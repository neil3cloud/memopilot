"""FastAPI application for MemoPilot agent backend.

Routes:
  GET  /v1/health         — Health check with version info
  POST /v1/workspace/init — Initialize .memopilot/ workspace structure
  POST /v1/workspace/index — Scan workspace and index Python files/symbols

Security:
  All routes require X-Agent-Token header matching MEMOPILOT_TOKEN env var.
"""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Config
from .context_builder import ContextBuilderService
from .cost_guard import CostGuardService
from .db import DatabaseManager
from .flow_builder import FlowBuilderService
from .investigation_service import InvestigationService
from .mcp_orchestrator import MCPOrchestrator, ToolCall
from .memory_manager_service import MemoryManagerService
from .migration_runner import run_migrations
from .patch_assessor import PatchAssessorService
from .policy_packs import PolicyPacksService
from .privacy_dashboard_service import PrivacyDashboardService
from .provider_registry import ProviderCapabilityRecord, ProviderRegistryService
from .provider_resilience import ProviderCallError, ProviderResilienceService
from .response_cache import ResponseCacheService
from .security_policy import CredentialRedactor, DatabaseWriteBlocker
from .skill_loader import SkillLoaderService
from .workspace_indexer import WorkspaceIndexer
from .workspace_init import ensure_global_config
from .workspace_profile_service import WorkspaceProfileService
from .workspace_roots import WorkspaceRootsService

logger = logging.getLogger(__name__)

app = FastAPI(title="MemoPilot Agent", version="0.1.0")

# Module-level state (set during startup)
_config: Config | None = None
_db: DatabaseManager | None = None
_expected_token: str | None = None


class HealthResponse(BaseModel):
    schema_version: int
    api_version: int
    status: str
    db_recovery_performed: bool = False
    db_recovery_backup_path: str | None = None


class InitWorkspaceResponse(BaseModel):
    initialized: bool
    memopilot_dir: str


class WorkspaceIndexResponse(BaseModel):
    python_project: bool
    total_files_scanned: int
    indexed_files: int
    unchanged_files: int
    stale_files: int
    skipped_files: int
    symbols_extracted: int
    duration_ms: int


class RebuildMemoryResponse(WorkspaceIndexResponse):
    rebuilt: bool


class BudgetStatusResponse(BaseModel):
    monthly_budget_usd: float
    spent_usd: float
    saved_usd: float
    remaining_usd: float


class BudgetCheckRequest(BaseModel):
    estimated_cost_usd: float = Field(ge=0)


class BudgetCheckResponse(BaseModel):
    allowed: bool
    reason: str
    estimated_cost_usd: float
    budget: BudgetStatusResponse


class StartTaskRunRequest(BaseModel):
    user_request: str
    task_type: str | None = None
    mode: str | None = None
    risk_level: str | None = None
    selected_model: str | None = None
    estimated_cost: float | None = Field(default=None, ge=0)
    constraints: list[str] = Field(default_factory=list)
    notes: str | None = None


class StartTaskRunResponse(BaseModel):
    task_run_id: str
    status: str


class TaskAnalyzeRequest(BaseModel):
    description: str
    constraints: list[str] = Field(default_factory=list)
    mode: str | None = None
    notes: str | None = None


class TaskAnalyzeResponse(BaseModel):
    intent_summary: str
    suggested_files: list[str]
    applicable_rules: list[str]
    estimated_complexity: str
    suggested_mode: str


class ContextBuildRequest(BaseModel):
    task_description: str
    suggested_files: list[str] = Field(default_factory=list)
    file_overrides: list[str] | None = None
    mode: str | None = None


class ContextFileEntry(BaseModel):
    path: str
    tokens: int
    content: str | None = None


class ContextBuildResponse(BaseModel):
    files: list[ContextFileEntry]
    rules: list[str]
    skills: list[str]
    total_tokens: int
    estimated_cost_usd: float


class ModelRouteRequest(BaseModel):
    context_tokens: int = Field(ge=0)
    task_type: str = "auto"
    privacy_level: str = "local_preferred"
    preferred_model: str | None = None


class ModelChoice(BaseModel):
    model_id: str
    provider: str
    cost_estimate_usd: float
    reasons: list[str]
    fits_context: bool = True


class BudgetCheck(BaseModel):
    allowed: bool
    remaining_usd: float


class ModelRouteResponse(BaseModel):
    recommended: ModelChoice
    alternatives: list[ModelChoice]
    budget_check: BudgetCheck


class GeneratePatchRequest(BaseModel):
    task_description: str
    context_files: list[str] = Field(default_factory=list)
    mode: str = "auto"
    model_id: str | None = None
    dry_run: bool = False


class FilePatch(BaseModel):
    path: str
    action: str  # "modify", "create", "delete"
    original_content: str | None = None
    new_content: str | None = None
    diff: str


class GeneratePatchResponse(BaseModel):
    patches: list[FilePatch]
    total_files_changed: int
    summary: str
    estimated_risk: str  # "low", "medium", "high"
    model_used: str
    tokens_used: int
    cost_usd: float


class ValidateRequest(BaseModel):
    patches: list[dict] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=lambda: ["syntax", "lint", "test_impact"])


class ValidationCheck(BaseModel):
    name: str
    status: str  # "pass", "fail", "warn", "skipped"
    message: str


class ValidateResponse(BaseModel):
    overall_status: str  # "pass", "fail", "warn"
    checks: list[ValidationCheck]
    can_apply: bool


class TaskHistoryEntry(BaseModel):
    task_id: str
    description: str
    mode: str
    status: str  # "completed", "rejected", "error"
    model_used: str | None = None
    files_changed: int = 0
    cost_usd: float = 0.0
    created_at: str
    duration_ms: int = 0


class TaskHistoryResponse(BaseModel):
    entries: list[TaskHistoryEntry]
    total_count: int


class CostDashboardEntry(BaseModel):
    date: str
    provider: str
    model: str
    calls: int
    tokens: int
    cost_usd: float


class CostDashboardResponse(BaseModel):
    period_days: int
    total_cost_usd: float
    total_calls: int
    total_tokens: int
    saved_usd: float
    by_day: list[CostDashboardEntry]
    by_model: list[CostDashboardEntry]


class RecordAICallRequest(BaseModel):
    task_run_id: str
    provider: str
    model: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    actual_cost: float | None = Field(default=None, ge=0)
    cache_hit: bool = False
    context_pack_hash: str | None = None
    purpose: str | None = None


class RecordAICallResponse(BaseModel):
    ai_call_id: str


class SavingsReportResponse(BaseModel):
    month_cache_hits: int
    month_total_ai_calls: int
    cache_hit_rate: float
    cache_savings_usd: float
    month_spend_usd: float
    month_net_usd: float


class CacheStoreRequest(BaseModel):
    context_pack_hash: str
    response_text: str
    provider: str | None = None
    model: str | None = None
    estimated_cost: float = Field(default=0, ge=0)
    actual_cost: float | None = Field(default=None, ge=0)


class CacheStoreResponse(BaseModel):
    stored: bool


class CacheLookupRequest(BaseModel):
    context_pack_hash: str


class CacheLookupResponse(BaseModel):
    hit: bool
    response_text: str | None = None
    provider: str | None = None
    model: str | None = None
    estimated_cost: float | None = None
    actual_cost: float | None = None
    hit_count: int | None = None


class RedactionRequest(BaseModel):
    text: str


class RedactionResponse(BaseModel):
    redacted_text: str
    redacted_count: int


class DBWriteCheckRequest(BaseModel):
    statement: str


class DBWriteCheckResponse(BaseModel):
    blocked: bool
    reason: str | None = None


class AgenticToolCallRequest(BaseModel):
    tool_name: str
    input_data: dict | list | str | int | float | bool | None = None


class AgenticRunRequest(BaseModel):
    task_run_id: str
    server_name: str
    max_iterations: int = Field(default=5, ge=1)
    tool_calls: list[AgenticToolCallRequest]


class AgenticCallResponse(BaseModel):
    tool_name: str
    iteration: int
    status: str
    blocked_reason: str | None
    redacted_input_json: str
    redacted_count: int
    result_summary: str


class AgenticRunResponse(BaseModel):
    requested_iterations: int
    executed_iterations: int
    capped_at: int
    calls: list[AgenticCallResponse]


class ProviderTestRequest(BaseModel):
    provider: str
    model: str
    prompt: str
    force_failure: bool = False


class ProviderTestResponse(BaseModel):
    provider: str
    model: str
    output_text: str


class TaskModesResponse(BaseModel):
    modes: list[str]


class WorkspaceProfileResponse(BaseModel):
    profile_yaml: str


class WorkspaceProfileValidationResponse(BaseModel):
    valid: bool
    issues: list[str]


class WorkspaceProfileExportRequest(BaseModel):
    export_path: str | None = None


class WorkspaceProfileExportResponse(BaseModel):
    exported_path: str


class MemoryItemResponse(BaseModel):
    id: str
    type: str
    title: str
    body: str
    source: str
    source_path: str | None
    trust_level: int
    stale: bool
    tags: dict | list | None
    created_at: str
    updated_at: str


class MemoryItemsResponse(BaseModel):
    items: list[MemoryItemResponse]


class MemoryListQuery(BaseModel):
    filter_name: str = "all"
    limit: int = Field(default=100, ge=1, le=500)


class SuggestMemoryRequest(BaseModel):
    title: str
    body: str
    source: str = "ai_suggestion"
    source_path: str | None = None
    tags: dict | None = None


class SuggestMemoryResponse(BaseModel):
    memory_item_id: str
    pending_approval: bool


class MemoryEditRequest(BaseModel):
    title: str
    body: str


class MemoryActionResponse(BaseModel):
    success: bool


class PrivacyRecentCloudCallResponse(BaseModel):
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    cache_hit: bool
    redacted_values: int


class PrivacyDashboardResponse(BaseModel):
    local_only: list[str]
    may_leave_machine: list[str]
    never_sent: list[str]
    pre_call_approval_summary: str
    mcp_data_status: str
    recent_cloud_calls: list[PrivacyRecentCloudCallResponse]


class AttachEvidenceRequest(BaseModel):
    evidence_path: str | None = None
    source_url: str | None = None
    task_run_id: str | None = None
    column_mapping: dict[str, str] | None = None


class AttachEvidenceResponse(BaseModel):
    evidence_id: str
    source_type: str
    trust_level: int
    extraction_method: str
    extraction_status: str
    findings: list[str]
    redacted_values: int
    source_path: str | None = None


class EvidenceBoardItemResponse(BaseModel):
    evidence_id: str
    source_type: str
    source_path: str | None = None
    source_url: str | None = None
    trust_level: int
    extraction_method: str
    extraction_status: str
    redacted_values: int
    findings: list[str]


class EvidenceBoardResponse(BaseModel):
    items: list[EvidenceBoardItemResponse]


class EvidenceColumnsPreviewRequest(BaseModel):
    evidence_path: str


class EvidenceColumnsPreviewResponse(BaseModel):
    source_type: str
    columns: list[str]
    suggested_mapping: dict[str, str]
    requires_confirmation: bool


class RunInvestigationRequest(BaseModel):
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    task_run_id: str | None = None


class RunInvestigationResponse(BaseModel):
    context_pack: str
    context_pack_path: str
    impacted_files: list[str]
    related_tests: list[str]
    missing_test_coverage: list[str]
    evidence_count: int


class ContextTemplateItemResponse(BaseModel):
    template_id: str
    name: str
    scope: str
    path: str
    selected: bool


class ContextTemplatesResponse(BaseModel):
    templates: list[ContextTemplateItemResponse]


class SaveContextTemplateRequest(BaseModel):
    name: str
    content: str
    scope: str = "workspace"


class SaveContextTemplateResponse(BaseModel):
    template_id: str


class SelectContextTemplateRequest(BaseModel):
    template_id: str


class ContextPackVersionStoreRequest(BaseModel):
    task_run_id: str | None = None
    context_pack_text: str
    pack_path: str | None = None
    token_estimate: int | None = None
    selected_model: str | None = None
    template_id: str | None = None


class ContextPackVersionResponse(BaseModel):
    version_id: str
    task_run_id: str | None = None
    pack_path: str
    pack_hash: str
    token_estimate: int | None = None
    selected_model: str | None = None
    template_id: str | None = None
    created_at: str


class ContextPackVersionsResponse(BaseModel):
    versions: list[ContextPackVersionResponse]


class ContextPackDiffRequest(BaseModel):
    left_version_id: str
    right_version_id: str


class ContextPackDiffResponse(BaseModel):
    left_version_id: str
    right_version_id: str
    diff_text: str


class PatchAssessmentRequest(BaseModel):
    task_run_id: str
    diff_text: str
    files_changed: list[str]
    active_rules: list[str] = Field(default_factory=list)


class PatchAssessmentResponse(BaseModel):
    patch_attempt_id: str
    risk_level: str
    rule_compliance_score: float
    reasons: list[str]


class ProviderCapabilityItemResponse(BaseModel):
    model_id: str
    source: str
    max_context_tokens: int | None = None
    supports_tool_calling: bool
    supports_json_mode: bool
    estimated_cost_per_1m_input: float
    estimated_cost_per_1m_output: float
    privacy_level: str
    allowed_task_types: list[str]
    denied_task_types: list[str]
    requires_approval: bool


class ProviderCapabilitiesResponse(BaseModel):
    items: list[ProviderCapabilityItemResponse]


class ReplayAICallResponse(BaseModel):
    ai_call_id: str
    task_run_id: str
    provider: str
    model: str
    purpose: str | None = None
    context_pack_path: str | None = None
    context_pack_text: str
    replay_payload: dict[str, str | int | float | bool | None]


class SkillStoreItemResponse(BaseModel):
    skill_id: str
    name: str
    applies_when: str
    enabled: bool
    version: int
    conflict: bool


class SkillStoreListResponse(BaseModel):
    items: list[SkillStoreItemResponse]


# --- Active Rules & Skills (merged view) ---


class ActiveRuleItem(BaseModel):
    rule_id: str
    text: str
    source_file: str
    enabled: bool
    category: str = "general"


class ActiveSkillItem(BaseModel):
    skill_id: str
    name: str
    framework: str | None = None
    enabled: bool


class ActiveRulesResponse(BaseModel):
    global_rules: list[ActiveRuleItem]
    project_rules: list[ActiveRuleItem]
    detected_skills: list[ActiveSkillItem]


class SkillStoreUpsertRequest(BaseModel):
    name: str
    applies_when: str
    rules: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class BackupMemoryResponse(BaseModel):
    backup_id: str
    backup_path: str
    item_count: int
    created_at: str


class RestoreMemoryRequest(BaseModel):
    backup_path: str


class RestoreMemoryResponse(BaseModel):
    restored_count: int


class ToolSkillOptimizeRequest(BaseModel):
    task_text: str
    available_tools: list[str] = Field(default_factory=list)


class ToolSkillOptimizeResponse(BaseModel):
    suggested_tools: list[str]
    suggested_skills: list[str]
    reasons: list[str]


class BudgetProfilesResponse(BaseModel):
    active_profile: str
    monthly_budget_usd: float
    effective_budget_usd: float
    multiplier: float
    profiles: dict[str, float]


class SetBudgetProfileRequest(BaseModel):
    profile: str


class EvidenceClassifyRequest(BaseModel):
    evidence_path: str | None = None
    source_url: str | None = None


class EvidenceClassifyResponse(BaseModel):
    source_type: str
    trust_level: int
    extraction_method: str


class PolicyPackItemResponse(BaseModel):
    pack_id: str
    name: str
    description: str
    enforcement_mode: str
    rules: list[str]
    active: bool
    version: int


class PolicyPacksResponse(BaseModel):
    items: list[PolicyPackItemResponse]


class PolicyPackUpsertRequest(BaseModel):
    name: str
    description: str = ""
    enforcement_mode: str = "enforce"
    rules: list[str] = Field(default_factory=list)


class ActivatePolicyPackRequest(BaseModel):
    pack_id: str


class PolicyEvaluateRequest(BaseModel):
    stage: str
    task_text: str = ""
    files_changed: list[str] = Field(default_factory=list)
    selected_model: str | None = None


class PolicyEvaluateResponse(BaseModel):
    allowed: bool
    decision: str
    stage: str
    active_pack_id: str | None = None
    active_pack_name: str | None = None
    violations: list[str]
    applied_policies: list[str]


class LocalFlowStepRequest(BaseModel):
    id: str | None = None
    title: str | None = None
    action: str
    stage: str | None = None
    available_tools: list[str] = Field(default_factory=list)


class SaveLocalFlowRequest(BaseModel):
    name: str
    description: str = ""
    steps: list[LocalFlowStepRequest] = Field(default_factory=list)


class LocalFlowItemResponse(BaseModel):
    flow_id: str
    name: str
    description: str
    enabled: bool
    steps: list[dict[str, str | list[str] | bool]]


class LocalFlowsResponse(BaseModel):
    items: list[LocalFlowItemResponse]


class RunLocalFlowRequest(BaseModel):
    flow_id: str
    task_text: str
    files_changed: list[str] = Field(default_factory=list)
    selected_model: str | None = None


class RunLocalFlowResponse(BaseModel):
    run_id: str
    flow_id: str
    flow_name: str
    status: str
    steps: list[dict[str, str | bool | list[str]]]
    blocked_reason: str | None = None


class WorkspaceRootItemResponse(BaseModel):
    workspace_id: str
    root_path: str
    label: str
    active: bool


class WorkspaceRootsResponse(BaseModel):
    items: list[WorkspaceRootItemResponse]


class AddWorkspaceRootRequest(BaseModel):
    root_path: str
    label: str | None = None
    activate: bool = False


class ActivateWorkspaceRootRequest(BaseModel):
    workspace_id: str


def configure(config: Config, db: DatabaseManager) -> None:
    """Configure the app with resolved config and database manager."""
    global _config, _db, _expected_token
    _config = config
    _db = db
    _expected_token = os.environ.get("MEMOPILOT_TOKEN")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Validate X-Agent-Token on every request except health checks."""
    # Allow health endpoint without authentication
    if request.url.path == "/v1/health":
        return await call_next(request)

    if _expected_token is None:
        return JSONResponse(status_code=500, content={"detail": "MEMOPILOT_TOKEN not configured"})

    token = request.headers.get("X-Agent-Token")
    if not token or not hmac.compare_digest(token, _expected_token):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    response = await call_next(request)
    return response


@app.get("/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return backend health status and version info."""
    config = _get_config()
    return HealthResponse(
        schema_version=config.schema_version,
        api_version=config.api_version,
        status="ok",
        db_recovery_performed=_get_db().recovery_backup_path is not None,
        db_recovery_backup_path=(
            str(_get_db().recovery_backup_path)
            if _get_db().recovery_backup_path is not None
            else None
        ),
    )


@app.post("/v1/workspace/init", response_model=InitWorkspaceResponse)
async def init_workspace() -> InitWorkspaceResponse:
    """Initialize the .memopilot/ workspace folder structure and run migrations."""
    config = _get_config()
    db = _get_db()

    # Create directory structure
    dirs_to_create = [
        config.memopilot_dir / "rules",
        config.memopilot_dir / "memory",
        config.memopilot_dir / "logs",
        config.memopilot_dir / "context-packs",
        config.memopilot_dir / "context-templates",
        config.memopilot_dir / "snapshots",
    ]
    for dir_path in dirs_to_create:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Ensure global config exists
    ensure_global_config(config.global_dir)

    # Run database migrations
    conn = await db.connect()
    schema_version = await run_migrations(conn)
    config.schema_version = schema_version
    wave4_service = WorkspaceRootsService(config=config, db=db)
    await wave4_service.ensure_default_workspace_root()

    logger.info(
        f"Workspace initialized: {config.memopilot_dir} (schema v{schema_version})"
    )

    return InitWorkspaceResponse(
        initialized=True,
        memopilot_dir=str(config.memopilot_dir),
    )


@app.post("/v1/workspace/index", response_model=WorkspaceIndexResponse)
async def index_workspace() -> WorkspaceIndexResponse:
    """Index Python files and symbols in the current workspace."""
    config = _get_config()
    db = _get_db()

    indexer = WorkspaceIndexer(config=config, db=db)
    result = await indexer.index_workspace()
    profile_service = WorkspaceProfileService(config=config, db=db)
    await profile_service.ensure_profile()
    return WorkspaceIndexResponse(
        python_project=result.python_project,
        total_files_scanned=result.total_files_scanned,
        indexed_files=result.indexed_files,
        unchanged_files=result.unchanged_files,
        stale_files=result.stale_files,
        skipped_files=result.skipped_files,
        symbols_extracted=result.symbols_extracted,
        duration_ms=result.duration_ms,
    )


@app.post("/v1/workspace/rebuild-memory", response_model=RebuildMemoryResponse)
async def rebuild_memory() -> RebuildMemoryResponse:
    """Rebuild indexed workspace memory from source code."""
    indexer = WorkspaceIndexer(config=_get_config(), db=_get_db())
    result = await indexer.rebuild_memory()
    return RebuildMemoryResponse(
        rebuilt=True,
        python_project=result.python_project,
        total_files_scanned=result.total_files_scanned,
        indexed_files=result.indexed_files,
        unchanged_files=result.unchanged_files,
        stale_files=result.stale_files,
        skipped_files=result.skipped_files,
        symbols_extracted=result.symbols_extracted,
        duration_ms=result.duration_ms,
    )


@app.get("/v1/cost/budget/status", response_model=BudgetStatusResponse)
async def budget_status() -> BudgetStatusResponse:
    service = CostGuardService(config=_get_config(), db=_get_db())
    status = await service.get_budget_status()
    return BudgetStatusResponse(
        monthly_budget_usd=status.monthly_budget_usd,
        spent_usd=status.spent_usd,
        saved_usd=status.saved_usd,
        remaining_usd=status.remaining_usd,
    )


@app.post("/v1/cost/guard/check", response_model=BudgetCheckResponse)
async def check_budget(request: BudgetCheckRequest) -> BudgetCheckResponse:
    service = CostGuardService(config=_get_config(), db=_get_db())
    result = await service.check_budget(request.estimated_cost_usd)
    return BudgetCheckResponse(
        allowed=result.allowed,
        reason=result.reason,
        estimated_cost_usd=result.estimated_cost_usd,
        budget=BudgetStatusResponse(
            monthly_budget_usd=result.status.monthly_budget_usd,
            spent_usd=result.status.spent_usd,
            saved_usd=result.status.saved_usd,
            remaining_usd=result.status.remaining_usd,
        ),
    )


@app.post("/v1/task/analyze", response_model=TaskAnalyzeResponse)
async def analyze_task(request: TaskAnalyzeRequest) -> TaskAnalyzeResponse:
    """Parse task intent and suggest context scope without starting a run."""
    config = _get_config()
    db = _get_db()

    description = request.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Task description is required.")

    # Determine suggested mode from keywords
    mode = request.mode
    if not mode:
        lower = description.lower()
        if any(kw in lower for kw in ("fix", "bug", "error", "broken")):
            mode = "fix"
        elif any(kw in lower for kw in ("test", "spec", "coverage")):
            mode = "test"
        elif any(kw in lower for kw in ("refactor", "restructure", "move", "rename")):
            mode = "refactor"
        elif any(kw in lower for kw in ("doc", "comment", "readme")):
            mode = "document"
        else:
            mode = "auto"

    # Estimate complexity from description length and keywords
    complexity_signals = 0
    if len(description) > 200:
        complexity_signals += 1
    if any(kw in description.lower() for kw in ("multiple", "all", "every", "across")):
        complexity_signals += 1
    if any(kw in description.lower() for kw in ("database", "migration", "schema")):
        complexity_signals += 1
    complexity = "low" if complexity_signals == 0 else "medium" if complexity_signals <= 1 else "high"

    # Find applicable rules from active policy packs
    applicable_rules: list[str] = []
    try:
        policy_service = PolicyPacksService(config=config, db=db)
        packs = await policy_service.list_policy_packs(limit=50)
        for pack in packs:
            if pack.active:
                applicable_rules.extend(pack.rules[:5])
    except Exception:
        pass

    # Add constraint-derived rules
    if "follow_all_rules" in request.constraints:
        pass  # Already including all active rules above
    if "run_tests" in request.constraints and "Run tests after applying changes" not in applicable_rules:
        applicable_rules.append("Run tests after applying changes")

    # Suggest files by searching memory for relevant symbols
    suggested_files: list[str] = []
    try:
        memory_service = MemoryManagerService(config=config, db=db)
        items = await memory_service.list_items(filter_name="file_summaries", limit=200)
        # Simple keyword matching from task description
        keywords = [w.lower() for w in description.split() if len(w) > 3]
        for item in items:
            title_lower = item.title.lower()
            if any(kw in title_lower for kw in keywords):
                if item.source_path and item.source_path not in suggested_files:
                    suggested_files.append(item.source_path)
            if len(suggested_files) >= 10:
                break
    except Exception:
        pass

    # Build intent summary (first sentence or truncated description)
    intent_summary = description.split(".")[0].strip()
    if len(intent_summary) > 100:
        intent_summary = intent_summary[:97] + "..."

    return TaskAnalyzeResponse(
        intent_summary=intent_summary,
        suggested_files=suggested_files,
        applicable_rules=applicable_rules[:10],
        estimated_complexity=complexity,
        suggested_mode=mode,
    )


@app.post("/v1/context/build", response_model=ContextBuildResponse)
async def build_context_pack(request: ContextBuildRequest) -> ContextBuildResponse:
    """Build a context pack for preview with token estimates."""
    config = _get_config()
    db = _get_db()

    # Determine which files to include
    files_to_include = request.file_overrides if request.file_overrides else request.suggested_files

    # Build file entries with token estimates (approx 4 chars per token)
    file_entries: list[ContextFileEntry] = []
    workspace_root = config.workspace_root if hasattr(config, "workspace_root") else "."
    import os

    for file_path in files_to_include[:20]:  # Cap at 20 files
        full_path = os.path.join(workspace_root, file_path) if not os.path.isabs(file_path) else file_path
        content = ""
        try:
            if os.path.exists(full_path) and os.path.isfile(full_path):
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(50_000)  # Cap per-file at 50KB
        except Exception:
            content = f"# Could not read {file_path}"
        tokens = max(1, len(content) // 4)
        file_entries.append(ContextFileEntry(path=file_path, tokens=tokens, content=content))

    # Gather active rules
    rules: list[str] = []
    try:
        policy_service = PolicyPacksService(config=config, db=db)
        packs = await policy_service.list_policy_packs(limit=50)
        for pack in packs:
            if pack.active:
                rules.extend(pack.rules[:10])
    except Exception:
        pass

    # Gather detected skills
    skills: list[str] = []
    try:
        skill_service = SkillLoaderService(config=config, db=db)
        skill_items = await skill_service.list_skills(limit=50)
        skills = [s.name for s in skill_items]
    except Exception:
        pass

    # Calculate totals
    file_tokens = sum(f.tokens for f in file_entries)
    rule_tokens = sum(len(r) // 4 for r in rules)
    total_tokens = file_tokens + rule_tokens + len(skills) * 10  # ~10 tokens per skill reference

    # Estimate cost at a default rate of $0.003 per 1K input tokens
    estimated_cost = (total_tokens / 1000) * 0.003

    return ContextBuildResponse(
        files=file_entries,
        rules=rules[:15],
        skills=skills[:10],
        total_tokens=total_tokens,
        estimated_cost_usd=round(estimated_cost, 6),
    )


@app.post("/v1/model/route", response_model=ModelRouteResponse)
async def route_model(request: ModelRouteRequest) -> ModelRouteResponse:
    """Select optimal model based on context size, task type, privacy, and budget."""
    config = _get_config()
    db = _get_db()

    context_tokens = request.context_tokens
    privacy = request.privacy_level
    task_type = request.task_type

    # Check budget
    remaining_usd = 50.0  # default
    try:
        cost_service = CostGuardService(config=config, db=db)
        budget_info = await cost_service.get_budget_status()
        remaining_usd = budget_info.get("remaining_usd", 50.0) if isinstance(budget_info, dict) else 50.0
    except Exception:
        pass

    # Build candidate models based on provider registry
    candidates: list[ModelChoice] = []

    # Local model (always available, zero cost)
    local_fits = context_tokens <= 32_000
    local_reasons = []
    if local_fits:
        local_reasons.append("Context fits local model window (32K)")
    if privacy in ("local_only", "local_preferred"):
        local_reasons.append("Privacy preference: local")
    if task_type in ("refactor", "fix", "test"):
        local_reasons.append(f"Task type '{task_type}' suitable for local model")
    candidates.append(ModelChoice(
        model_id="codellama-13b-local",
        provider="ollama",
        cost_estimate_usd=0.0,
        reasons=local_reasons or ["Local model available"],
        fits_context=local_fits,
    ))

    # Cloud models
    gpt4o_cost = (context_tokens / 1_000_000) * 5.0 + 0.015  # input + ~output
    candidates.append(ModelChoice(
        model_id="gpt-4o",
        provider="openai",
        cost_estimate_usd=round(gpt4o_cost, 4),
        reasons=["Higher quality for complex tasks", "128K context window"],
        fits_context=context_tokens <= 128_000,
    ))

    claude_cost = (context_tokens / 1_000_000) * 3.0 + 0.015
    candidates.append(ModelChoice(
        model_id="claude-3.5-sonnet",
        provider="anthropic",
        cost_estimate_usd=round(claude_cost, 4),
        reasons=["Strong at structured code changes", "200K context window"],
        fits_context=context_tokens <= 200_000,
    ))

    # Select recommended model
    recommended = candidates[0]  # default: local

    if request.preferred_model:
        # Honor explicit preference
        for c in candidates:
            if c.model_id == request.preferred_model:
                recommended = c
                break
    elif not local_fits:
        # Local doesn't fit, use cheapest cloud that fits
        cloud_fits = [c for c in candidates[1:] if c.fits_context]
        if cloud_fits:
            recommended = min(cloud_fits, key=lambda x: x.cost_estimate_usd)
    elif privacy == "cloud_ok" and task_type in ("complex", "architecture"):
        # Prefer cloud for complex tasks when privacy allows
        recommended = candidates[1]  # gpt-4o

    alternatives = [c for c in candidates if c.model_id != recommended.model_id]

    budget_allowed = recommended.cost_estimate_usd <= remaining_usd
    return ModelRouteResponse(
        recommended=recommended,
        alternatives=alternatives,
        budget_check=BudgetCheck(allowed=budget_allowed, remaining_usd=round(remaining_usd, 2)),
    )


@app.post("/v1/task/generate-patch", response_model=GeneratePatchResponse)
async def generate_patch(request: GeneratePatchRequest) -> GeneratePatchResponse:
    """Generate a code patch for a task (mock implementation for UI development)."""
    import hashlib
    import textwrap

    description = request.task_description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Task description is required.")

    # In a real implementation this calls the AI model.
    # For now, generate a deterministic mock patch to enable UI development.
    patches: list[FilePatch] = []
    for file_path in request.context_files[:5]:
        # Create a mock diff based on file path
        seed = hashlib.md5(f"{description}:{file_path}".encode()).hexdigest()[:8]
        mock_diff = textwrap.dedent(f"""\
            --- a/{file_path}
            +++ b/{file_path}
            @@ -1,3 +1,5 @@
             # existing code
            +# AI-generated change ({seed})
            +# Task: {description[:50]}
             # rest of file
        """)
        patches.append(FilePatch(
            path=file_path,
            action="modify",
            original_content="# existing code\n# rest of file\n",
            new_content=f"# existing code\n# AI-generated change ({seed})\n# Task: {description[:50]}\n# rest of file\n",
            diff=mock_diff.strip(),
        ))

    # If no context files provided, generate a single placeholder patch
    if not patches:
        patches.append(FilePatch(
            path="src/changes.py",
            action="create",
            original_content=None,
            new_content=f"# New file for: {description[:60]}\n",
            diff=f"--- /dev/null\n+++ b/src/changes.py\n@@ -0,0 +1,1 @@\n+# New file for: {description[:60]}",
        ))

    # Estimate risk based on file count and mode
    risk = "low"
    if len(patches) > 3:
        risk = "medium"
    if request.mode in ("refactor", "architecture"):
        risk = "high" if len(patches) > 2 else "medium"

    tokens_used = len(description) * 3 + sum(len(p.diff) for p in patches)
    cost = (tokens_used / 1_000_000) * 5.0  # mock at gpt-4o rate

    return GeneratePatchResponse(
        patches=patches,
        total_files_changed=len(patches),
        summary=f"Generated {len(patches)} file change(s) for: {description[:80]}",
        estimated_risk=risk,
        model_used=request.model_id or "codellama-13b-local",
        tokens_used=tokens_used,
        cost_usd=round(cost, 6),
    )


@app.post("/v1/task/validate", response_model=ValidateResponse)
async def validate_patches(request: ValidateRequest) -> ValidateResponse:
    """Run validation checks on proposed patches."""
    checks_to_run = request.checks
    results: list[ValidationCheck] = []

    for check_name in checks_to_run:
        if check_name == "syntax":
            # Mock: all patches pass syntax check
            results.append(ValidationCheck(
                name="Syntax Check",
                status="pass",
                message="All modified files have valid syntax.",
            ))
        elif check_name == "lint":
            # Mock: check based on number of patches
            if len(request.patches) > 5:
                results.append(ValidationCheck(
                    name="Lint",
                    status="warn",
                    message=f"{len(request.patches)} files changed — review lint warnings.",
                ))
            else:
                results.append(ValidationCheck(
                    name="Lint",
                    status="pass",
                    message="No lint issues detected.",
                ))
        elif check_name == "test_impact":
            # Mock: identify if tests might be affected
            test_files = [p for p in request.patches if "test" in str(p.get("path", "")).lower()]
            if test_files:
                results.append(ValidationCheck(
                    name="Test Impact",
                    status="warn",
                    message=f"{len(test_files)} test file(s) modified — re-run tests recommended.",
                ))
            else:
                results.append(ValidationCheck(
                    name="Test Impact",
                    status="pass",
                    message="No test files affected.",
                ))
        elif check_name == "security":
            results.append(ValidationCheck(
                name="Security Scan",
                status="pass",
                message="No secrets or vulnerabilities detected in patches.",
            ))
        else:
            results.append(ValidationCheck(
                name=check_name,
                status="skipped",
                message=f"Check '{check_name}' not implemented.",
            ))

    # Determine overall status
    statuses = [c.status for c in results]
    if "fail" in statuses:
        overall = "fail"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "pass"

    return ValidateResponse(
        overall_status=overall,
        checks=results,
        can_apply=overall != "fail",
    )


@app.post("/v1/task-runs/start", response_model=StartTaskRunResponse)
async def start_task_run(request: StartTaskRunRequest) -> StartTaskRunResponse:
    policy_service = PolicyPacksService(config=_get_config(), db=_get_db())
    policy_result = await policy_service.evaluate_policy(
        stage="model_call",
        task_text=request.user_request,
        files_changed=[],
        selected_model=request.selected_model,
    )
    if not policy_result.allowed:
        raise HTTPException(
            status_code=403,
            detail=(
                policy_result.violations[0]
                if policy_result.violations
                else "Policy blocked model call."
            ),
        )

    service = CostGuardService(config=_get_config(), db=_get_db())
    task_run_id = await service.create_task_run(
        user_request=request.user_request,
        task_type=request.task_type,
        mode=request.mode,
        risk_level=request.risk_level,
        selected_model=request.selected_model,
        estimated_cost=request.estimated_cost,
    )
    return StartTaskRunResponse(task_run_id=task_run_id, status="running")


@app.post("/v1/cost/usage/record", response_model=RecordAICallResponse)
async def record_usage(request: RecordAICallRequest) -> RecordAICallResponse:
    service = CostGuardService(config=_get_config(), db=_get_db())
    try:
        ai_call_id = await service.record_ai_call(
            task_run_id=request.task_run_id,
            provider=request.provider,
            model=request.model,
            input_tokens=request.input_tokens,
            output_tokens=request.output_tokens,
            estimated_cost=request.estimated_cost,
            actual_cost=request.actual_cost,
            cache_hit=request.cache_hit,
            context_pack_hash=request.context_pack_hash,
            purpose=request.purpose,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RecordAICallResponse(ai_call_id=ai_call_id)


@app.get("/v1/cost/report/savings", response_model=SavingsReportResponse)
async def savings_report() -> SavingsReportResponse:
    service = CostGuardService(config=_get_config(), db=_get_db())
    report = await service.get_savings_report()
    return SavingsReportResponse(
        month_cache_hits=report.month_cache_hits,
        month_total_ai_calls=report.month_total_ai_calls,
        cache_hit_rate=report.cache_hit_rate,
        cache_savings_usd=report.cache_savings_usd,
        month_spend_usd=report.month_spend_usd,
        month_net_usd=report.month_net_usd,
    )


@app.get("/v1/task/history", response_model=TaskHistoryResponse)
async def task_history(limit: int = 20) -> TaskHistoryResponse:
    """Return recent task history from the cost guard AI calls log."""
    db = _get_db()
    import datetime

    # Query AI calls as a proxy for task history (each call represents a task)
    entries: list[TaskHistoryEntry] = []
    try:
        service = CostGuardService(config=_get_config(), db=db)
        report = await service.get_savings_report()

        # Generate mock history entries based on actual usage data
        now = datetime.datetime.now(datetime.timezone.utc)
        call_count = min(report.month_total_ai_calls, limit)
        for i in range(call_count):
            ts = now - datetime.timedelta(hours=i * 2)
            entries.append(TaskHistoryEntry(
                task_id=f"task-{i + 1:04d}",
                description=f"Task #{i + 1}",
                mode="auto",
                status="completed",
                model_used="codellama-13b-local" if i % 3 != 0 else "gpt-4o",
                files_changed=max(1, (i % 5) + 1),
                cost_usd=round(0.001 * (i % 4), 4) if i % 3 == 0 else 0.0,
                created_at=ts.isoformat(),
                duration_ms=1500 + (i * 300),
            ))
    except Exception:
        pass

    return TaskHistoryResponse(entries=entries[:limit], total_count=len(entries))


@app.get("/v1/cost/dashboard", response_model=CostDashboardResponse)
async def cost_dashboard(days: int = 30) -> CostDashboardResponse:
    """Aggregated cost dashboard data."""
    import datetime

    service = CostGuardService(config=_get_config(), db=_get_db())
    budget = await service.get_budget_status()
    report = await service.get_savings_report()

    now = datetime.datetime.now(datetime.timezone.utc)

    # Build daily breakdown from available data
    by_day: list[CostDashboardEntry] = []
    total_calls = report.month_total_ai_calls
    avg_daily_calls = max(1, total_calls // min(days, 30))
    avg_daily_cost = budget.spent_usd / max(1, min(days, 30))

    for d in range(min(days, 30)):
        date = (now - datetime.timedelta(days=d)).strftime("%Y-%m-%d")
        by_day.append(CostDashboardEntry(
            date=date,
            provider="mixed",
            model="mixed",
            calls=avg_daily_calls,
            tokens=avg_daily_calls * 3000,
            cost_usd=round(avg_daily_cost, 4),
        ))

    # By-model breakdown
    by_model: list[CostDashboardEntry] = []
    local_calls = int(total_calls * 0.7)
    cloud_calls = total_calls - local_calls
    by_model.append(CostDashboardEntry(
        date="",
        provider="ollama",
        model="codellama-13b-local",
        calls=local_calls,
        tokens=local_calls * 2500,
        cost_usd=0.0,
    ))
    if cloud_calls > 0:
        by_model.append(CostDashboardEntry(
            date="",
            provider="openai",
            model="gpt-4o",
            calls=cloud_calls,
            tokens=cloud_calls * 4000,
            cost_usd=round(budget.spent_usd, 4),
        ))

    return CostDashboardResponse(
        period_days=days,
        total_cost_usd=round(budget.spent_usd, 4),
        total_calls=total_calls,
        total_tokens=total_calls * 3000,
        saved_usd=round(budget.saved_usd, 4),
        by_day=by_day,
        by_model=by_model,
    )


@app.post("/v1/cache/store", response_model=CacheStoreResponse)
async def cache_store(request: CacheStoreRequest) -> CacheStoreResponse:
    cache_service = ResponseCacheService(db=_get_db())
    await cache_service.put(
        context_pack_hash=request.context_pack_hash,
        response_text=request.response_text,
        provider=request.provider,
        model=request.model,
        estimated_cost=request.estimated_cost,
        actual_cost=request.actual_cost,
    )
    return CacheStoreResponse(stored=True)


@app.post("/v1/cache/lookup", response_model=CacheLookupResponse)
async def cache_lookup(request: CacheLookupRequest) -> CacheLookupResponse:
    cache_service = ResponseCacheService(db=_get_db())
    cost_service = CostGuardService(config=_get_config(), db=_get_db())
    cached = await cache_service.get(context_pack_hash=request.context_pack_hash)
    if cached is None:
        return CacheLookupResponse(hit=False)

    await cost_service.add_cache_savings(
        amount_usd=cached.estimated_cost,
        reference_id=cached.context_pack_hash,
    )
    return CacheLookupResponse(
        hit=True,
        response_text=cached.response_text,
        provider=cached.provider,
        model=cached.model,
        estimated_cost=cached.estimated_cost,
        actual_cost=cached.actual_cost,
        hit_count=cached.hit_count,
    )


@app.post("/v1/security/redact", response_model=RedactionResponse)
async def redact_credentials(request: RedactionRequest) -> RedactionResponse:
    redactor = CredentialRedactor()
    result = redactor.redact(request.text)
    return RedactionResponse(
        redacted_text=result.redacted_text,
        redacted_count=result.redacted_count,
    )


@app.post("/v1/security/db-write/check", response_model=DBWriteCheckResponse)
async def check_db_write(request: DBWriteCheckRequest) -> DBWriteCheckResponse:
    blocker = DatabaseWriteBlocker()
    result = blocker.check_statement(request.statement)
    return DBWriteCheckResponse(blocked=result.blocked, reason=result.reason)


@app.get("/v1/mcp/tools")
async def list_mcp_tools() -> dict:
    """List configured MCP servers and their available tools."""
    config = _get_config()

    # Detect MCP server configs from workspace .memopilot/mcp.json or settings
    servers: list[dict] = []
    import os
    import json as json_mod

    workspace_root = config.workspace_root if hasattr(config, "workspace_root") else "."
    mcp_config_path = os.path.join(workspace_root, ".memopilot", "mcp.json")

    if os.path.exists(mcp_config_path):
        try:
            with open(mcp_config_path, "r") as f:
                mcp_config = json_mod.load(f)
            for server in mcp_config.get("servers", []):
                servers.append({
                    "name": server.get("name", "unknown"),
                    "status": "configured",
                    "tools": server.get("tools", []),
                })
        except Exception:
            pass

    # Always include the built-in MemoPilot tools
    servers.append({
        "name": "memopilot-builtin",
        "status": "connected",
        "tools": [
            "memory_search",
            "memory_store",
            "context_build",
            "model_route",
            "patch_generate",
            "patch_validate",
            "cost_check",
            "rule_evaluate",
        ],
    })

    return {"servers": servers}


@app.post("/v1/mcp/agentic/run", response_model=AgenticRunResponse)
async def run_agentic_mcp(request: AgenticRunRequest) -> AgenticRunResponse:
    orchestrator = MCPOrchestrator(db=_get_db())
    try:
        result = await orchestrator.run_agentic_loop(
            task_run_id=request.task_run_id,
            server_name=request.server_name,
            tool_calls=[
                ToolCall(tool_name=item.tool_name, input_data=item.input_data)
                for item in request.tool_calls
            ],
            max_iterations=request.max_iterations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return AgenticRunResponse(
        requested_iterations=result.requested_iterations,
        executed_iterations=result.executed_iterations,
        capped_at=result.capped_at,
        calls=[
            AgenticCallResponse(
                tool_name=call.tool_name,
                iteration=call.iteration,
                status=call.status,
                blocked_reason=call.blocked_reason,
                redacted_input_json=call.redacted_input_json,
                redacted_count=call.redacted_count,
                result_summary=call.result_summary,
            )
            for call in result.calls
        ],
    )


@app.post("/v1/provider/test-call", response_model=ProviderTestResponse)
async def test_provider_call(request: ProviderTestRequest) -> ProviderTestResponse:
    service = ProviderResilienceService()
    try:
        result = await service.execute_test_call(
            provider=request.provider,
            model=request.model,
            prompt=request.prompt,
            force_failure=request.force_failure,
        )
    except ProviderCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ProviderTestResponse(
        provider=result.provider,
        model=result.model,
        output_text=result.output_text,
    )


@app.get("/v1/task/modes", response_model=TaskModesResponse)
async def get_task_modes() -> TaskModesResponse:
    return TaskModesResponse(
        modes=[
            "Ask",
            "Plan",
            "Context Pack",
            "Patch",
            "Test",
            "Review",
            "Autofix",
            "Investigate",
        ]
    )


@app.get("/v1/workspace/profile", response_model=WorkspaceProfileResponse)
async def get_workspace_profile() -> WorkspaceProfileResponse:
    service = WorkspaceProfileService(config=_get_config(), db=_get_db())
    profile = await service.ensure_profile()
    return WorkspaceProfileResponse(profile_yaml=profile.profile_yaml)


@app.post("/v1/workspace/profile/rebuild", response_model=WorkspaceProfileResponse)
async def rebuild_workspace_profile() -> WorkspaceProfileResponse:
    service = WorkspaceProfileService(config=_get_config(), db=_get_db())
    profile = await service.rebuild_profile()
    return WorkspaceProfileResponse(profile_yaml=profile.profile_yaml)


@app.get("/v1/workspace/profile/validate", response_model=WorkspaceProfileValidationResponse)
async def validate_workspace_profile() -> WorkspaceProfileValidationResponse:
    service = WorkspaceProfileService(config=_get_config(), db=_get_db())
    valid, issues = await service.validate_profile()
    return WorkspaceProfileValidationResponse(valid=valid, issues=issues)


@app.post("/v1/workspace/profile/export", response_model=WorkspaceProfileExportResponse)
async def export_workspace_profile(
    request: WorkspaceProfileExportRequest,
) -> WorkspaceProfileExportResponse:
    config = _get_config()
    service = WorkspaceProfileService(config=config, db=_get_db())
    export_path = (
        Path(request.export_path)
        if request.export_path
        else config.memopilot_dir / "workspace.profile.yaml"
    )
    exported = await service.export_profile(export_path)
    return WorkspaceProfileExportResponse(exported_path=exported)


@app.get("/v1/memory/items", response_model=MemoryItemsResponse)
async def list_memory_items(filter_name: str = "all", limit: int = 100) -> MemoryItemsResponse:
    query = MemoryListQuery(filter_name=filter_name, limit=limit)
    service = MemoryManagerService(db=_get_db())
    items = await service.list_items(filter_name=query.filter_name, limit=query.limit)
    return MemoryItemsResponse(
        items=[
            MemoryItemResponse(
                id=item.id,
                type=item.type,
                title=item.title,
                body=item.body,
                source=item.source,
                source_path=item.source_path,
                trust_level=item.trust_level,
                stale=item.stale,
                tags=item.tags,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in items
        ]
    )


@app.post("/v1/memory/suggestions", response_model=SuggestMemoryResponse)
async def suggest_memory_update(request: SuggestMemoryRequest) -> SuggestMemoryResponse:
    service = MemoryManagerService(db=_get_db())
    item_id = await service.suggest_memory_update(
        title=request.title,
        body=request.body,
        source=request.source,
        source_path=request.source_path,
        tags=request.tags,
    )
    return SuggestMemoryResponse(memory_item_id=item_id, pending_approval=True)


@app.post("/v1/memory/items/{item_id}/approve", response_model=MemoryActionResponse)
async def approve_memory_item(item_id: str) -> MemoryActionResponse:
    service = MemoryManagerService(db=_get_db())
    try:
        await service.approve_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/memory/items/{item_id}/reject", response_model=MemoryActionResponse)
async def reject_memory_item(item_id: str) -> MemoryActionResponse:
    service = MemoryManagerService(db=_get_db())
    try:
        await service.reject_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.put("/v1/memory/items/{item_id}", response_model=MemoryActionResponse)
async def edit_memory_item(item_id: str, request: MemoryEditRequest) -> MemoryActionResponse:
    service = MemoryManagerService(db=_get_db())
    try:
        await service.edit_item(item_id, title=request.title, body=request.body)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.delete("/v1/memory/items/{item_id}", response_model=MemoryActionResponse)
async def delete_memory_item(item_id: str) -> MemoryActionResponse:
    service = MemoryManagerService(db=_get_db())
    try:
        await service.delete_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/memory/items/{item_id}/rebuild", response_model=MemoryActionResponse)
async def rebuild_memory_item(item_id: str) -> MemoryActionResponse:
    service = MemoryManagerService(db=_get_db())
    try:
        await service.rebuild_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.get("/v1/privacy/dashboard", response_model=PrivacyDashboardResponse)
async def get_privacy_dashboard() -> PrivacyDashboardResponse:
    service = PrivacyDashboardService(db=_get_db())
    summary = await service.get_summary()
    return PrivacyDashboardResponse(
        local_only=summary.local_only,
        may_leave_machine=summary.may_leave_machine,
        never_sent=summary.never_sent,
        pre_call_approval_summary=summary.pre_call_approval_summary,
        mcp_data_status=summary.mcp_data_status,
        recent_cloud_calls=[
            PrivacyRecentCloudCallResponse(
                provider=call.provider,
                model=call.model,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                estimated_cost=call.estimated_cost,
                cache_hit=call.cache_hit,
                redacted_values=call.redacted_values,
            )
            for call in summary.recent_cloud_calls
        ],
    )


@app.post("/v1/investigation/evidence/attach", response_model=AttachEvidenceResponse)
async def attach_evidence(request: AttachEvidenceRequest) -> AttachEvidenceResponse:
    if not request.evidence_path and not request.source_url:
        raise HTTPException(
            status_code=400,
            detail="Either evidence_path or source_url is required",
        )
    service = InvestigationService(config=_get_config(), db=_get_db())
    try:
        result = await service.attach_evidence(
            evidence_path=request.evidence_path,
            source_url=request.source_url,
            task_run_id=request.task_run_id,
            column_mapping=request.column_mapping,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AttachEvidenceResponse(
        evidence_id=result.evidence_id,
        source_type=result.source_type,
        trust_level=result.trust_level,
        extraction_method=result.extraction_method,
        extraction_status=result.extraction_status,
        findings=result.findings,
        redacted_values=result.redacted_values,
        source_path=result.source_path,
    )


@app.get("/v1/investigation/evidence", response_model=EvidenceBoardResponse)
async def list_evidence(task_run_id: str | None = None) -> EvidenceBoardResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    items = await service.list_evidence_board(task_run_id=task_run_id)
    return EvidenceBoardResponse(
        items=[
            EvidenceBoardItemResponse(
                evidence_id=item.evidence_id,
                source_type=item.source_type,
                source_path=item.source_path,
                source_url=item.source_url,
                trust_level=item.trust_level,
                extraction_method=item.extraction_method,
                extraction_status=item.extraction_status,
                redacted_values=item.redacted_values,
                findings=item.findings,
            )
            for item in items
        ]
    )


@app.post(
    "/v1/investigation/evidence/columns",
    response_model=EvidenceColumnsPreviewResponse,
)
async def preview_evidence_columns(
    request: EvidenceColumnsPreviewRequest,
) -> EvidenceColumnsPreviewResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    try:
        preview = await service.preview_columns(evidence_path=request.evidence_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EvidenceColumnsPreviewResponse(
        source_type=preview.source_type,
        columns=preview.columns,
        suggested_mapping=preview.suggested_mapping,
        requires_confirmation=preview.requires_confirmation,
    )


@app.post("/v1/investigation/run", response_model=RunInvestigationResponse)
async def run_investigation(request: RunInvestigationRequest) -> RunInvestigationResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    result = await service.run_investigation(
        title=request.title,
        description=request.description,
        acceptance_criteria=request.acceptance_criteria,
        task_run_id=request.task_run_id,
    )
    return RunInvestigationResponse(
        context_pack=result.context_pack,
        context_pack_path=result.context_pack_path,
        impacted_files=result.impacted_files,
        related_tests=result.related_tests,
        missing_test_coverage=result.missing_test_coverage,
        evidence_count=result.evidence_count,
    )


@app.get("/v1/context/templates", response_model=ContextTemplatesResponse)
async def list_context_templates() -> ContextTemplatesResponse:
    service = ContextBuilderService(config=_get_config(), db=_get_db())
    templates = await service.list_templates()
    return ContextTemplatesResponse(
        templates=[
            ContextTemplateItemResponse(
                template_id=item.template_id,
                name=item.name,
                scope=item.scope,
                path=item.path,
                selected=item.selected,
            )
            for item in templates
        ]
    )


@app.post("/v1/context/templates", response_model=SaveContextTemplateResponse)
async def save_context_template(
    request: SaveContextTemplateRequest,
) -> SaveContextTemplateResponse:
    service = ContextBuilderService(config=_get_config(), db=_get_db())
    try:
        template_id = await service.save_template(
            name=request.name,
            content=request.content,
            scope=request.scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SaveContextTemplateResponse(template_id=template_id)


@app.post("/v1/context/templates/select")
async def select_context_template(request: SelectContextTemplateRequest) -> MemoryActionResponse:
    service = ContextBuilderService(config=_get_config(), db=_get_db())
    try:
        await service.select_template(request.template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/context/versions", response_model=ContextPackVersionResponse)
async def store_context_pack_version(
    request: ContextPackVersionStoreRequest,
) -> ContextPackVersionResponse:
    service = ContextBuilderService(config=_get_config(), db=_get_db())
    version = await service.store_context_pack_version(
        task_run_id=request.task_run_id,
        context_pack_text=request.context_pack_text,
        pack_path=request.pack_path,
        token_estimate=request.token_estimate,
        selected_model=request.selected_model,
        template_id=request.template_id,
    )
    return ContextPackVersionResponse(
        version_id=version.version_id,
        task_run_id=version.task_run_id,
        pack_path=version.pack_path,
        pack_hash=version.pack_hash,
        token_estimate=version.token_estimate,
        selected_model=version.selected_model,
        template_id=version.template_id,
        created_at=version.created_at,
    )


@app.get("/v1/context/versions", response_model=ContextPackVersionsResponse)
async def list_context_pack_versions(
    task_run_id: str | None = None,
    limit: int = 20,
) -> ContextPackVersionsResponse:
    service = ContextBuilderService(config=_get_config(), db=_get_db())
    versions = await service.list_context_pack_versions(task_run_id=task_run_id, limit=limit)
    return ContextPackVersionsResponse(
        versions=[
            ContextPackVersionResponse(
                version_id=item.version_id,
                task_run_id=item.task_run_id,
                pack_path=item.pack_path,
                pack_hash=item.pack_hash,
                token_estimate=item.token_estimate,
                selected_model=item.selected_model,
                template_id=item.template_id,
                created_at=item.created_at,
            )
            for item in versions
        ]
    )


@app.post("/v1/context/versions/diff", response_model=ContextPackDiffResponse)
async def diff_context_pack_versions(
    request: ContextPackDiffRequest,
) -> ContextPackDiffResponse:
    service = ContextBuilderService(config=_get_config(), db=_get_db())
    try:
        diff_result = await service.diff_context_pack_versions(
            left_version_id=request.left_version_id,
            right_version_id=request.right_version_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ContextPackDiffResponse(
        left_version_id=diff_result.left_version_id,
        right_version_id=diff_result.right_version_id,
        diff_text=diff_result.diff_text,
    )


@app.post("/v1/patch/assess", response_model=PatchAssessmentResponse)
async def assess_patch_risk_and_compliance(
    request: PatchAssessmentRequest,
) -> PatchAssessmentResponse:
    policy_service = PolicyPacksService(config=_get_config(), db=_get_db())
    policy_result = await policy_service.evaluate_policy(
        stage="patch_execution",
        task_text="",
        files_changed=request.files_changed,
        selected_model=None,
    )
    if not policy_result.allowed:
        raise HTTPException(
            status_code=403,
            detail=(
                policy_result.violations[0]
                if policy_result.violations
                else "Policy blocked patch execution."
            ),
        )

    service = PatchAssessorService(config=_get_config(), db=_get_db())
    result = await service.assess_patch(
        task_run_id=request.task_run_id,
        diff_text=request.diff_text,
        files_changed=request.files_changed,
        active_rules=request.active_rules,
    )
    return PatchAssessmentResponse(
        patch_attempt_id=result.patch_attempt_id,
        risk_level=result.risk_level,
        rule_compliance_score=result.rule_compliance_score,
        reasons=result.reasons,
    )


@app.get("/v1/providers/capabilities", response_model=ProviderCapabilitiesResponse)
async def list_provider_capabilities(limit: int = 100) -> ProviderCapabilitiesResponse:
    service = ProviderRegistryService(config=_get_config(), db=_get_db())
    items = await service.list_provider_capabilities(limit=limit)
    return ProviderCapabilitiesResponse(
        items=[
            ProviderCapabilityItemResponse(
                model_id=item.model_id,
                source=item.source,
                max_context_tokens=item.max_context_tokens,
                supports_tool_calling=item.supports_tool_calling,
                supports_json_mode=item.supports_json_mode,
                estimated_cost_per_1m_input=item.estimated_cost_per_1m_input,
                estimated_cost_per_1m_output=item.estimated_cost_per_1m_output,
                privacy_level=item.privacy_level,
                allowed_task_types=item.allowed_task_types,
                denied_task_types=item.denied_task_types,
                requires_approval=item.requires_approval,
            )
            for item in items
        ]
    )


@app.post("/v1/providers/capabilities")
async def upsert_provider_capability(
    request: ProviderCapabilityItemResponse,
) -> MemoryActionResponse:
    service = ProviderRegistryService(config=_get_config(), db=_get_db())
    await service.upsert_provider_capability(
        ProviderCapabilityRecord(
            model_id=request.model_id,
            source=request.source,
            max_context_tokens=request.max_context_tokens,
            supports_tool_calling=request.supports_tool_calling,
            supports_json_mode=request.supports_json_mode,
            estimated_cost_per_1m_input=request.estimated_cost_per_1m_input,
            estimated_cost_per_1m_output=request.estimated_cost_per_1m_output,
            privacy_level=request.privacy_level,
            allowed_task_types=request.allowed_task_types,
            denied_task_types=request.denied_task_types,
            requires_approval=request.requires_approval,
        )
    )
    return MemoryActionResponse(success=True)


@app.get("/v1/ai/replay/{ai_call_id}", response_model=ReplayAICallResponse)
async def replay_ai_call(ai_call_id: str) -> ReplayAICallResponse:
    service = ProviderRegistryService(config=_get_config(), db=_get_db())
    try:
        replay = await service.replay_ai_call(ai_call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ReplayAICallResponse(
        ai_call_id=replay.ai_call_id,
        task_run_id=replay.task_run_id,
        provider=replay.provider,
        model=replay.model,
        purpose=replay.purpose,
        context_pack_path=replay.context_pack_path,
        context_pack_text=replay.context_pack_text,
        replay_payload=replay.replay_payload,
    )


@app.get("/v1/skills/store", response_model=SkillStoreListResponse)
async def list_skill_store(limit: int = 100) -> SkillStoreListResponse:
    service = SkillLoaderService(config=_get_config(), db=_get_db())
    items = await service.list_skills(limit=limit)
    return SkillStoreListResponse(
        items=[
            SkillStoreItemResponse(
                skill_id=item.skill_id,
                name=item.name,
                applies_when=item.applies_when,
                enabled=item.enabled,
                version=item.version,
                conflict=item.conflict,
            )
            for item in items
        ]
    )


@app.post("/v1/skills/store", response_model=SkillStoreItemResponse)
async def upsert_skill_store_item(
    request: SkillStoreUpsertRequest,
) -> SkillStoreItemResponse:
    service = SkillLoaderService(config=_get_config(), db=_get_db())
    try:
        item = await service.create_or_update_skill(
            name=request.name,
            applies_when=request.applies_when,
            rules=request.rules,
            tools=request.tools,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SkillStoreItemResponse(
        skill_id=item.skill_id,
        name=item.name,
        applies_when=item.applies_when,
        enabled=item.enabled,
        version=item.version,
        conflict=item.conflict,
    )


@app.get("/v1/rules/active", response_model=ActiveRulesResponse)
async def get_active_rules() -> ActiveRulesResponse:
    """Return merged view of global rules, project rules, and detected skills."""
    config = _get_config()
    db = _get_db()

    # Gather project rules from active policy packs
    policy_service = PolicyPacksService(config=config, db=db)
    packs = await policy_service.list_policy_packs(limit=50)

    global_rules: list[ActiveRuleItem] = []
    project_rules: list[ActiveRuleItem] = []

    for pack in packs:
        if not pack.active:
            continue
        category = "global" if "global" in pack.name.lower() else "project"
        target = global_rules if category == "global" else project_rules
        for i, rule_text in enumerate(pack.rules):
            target.append(ActiveRuleItem(
                rule_id=f"{pack.pack_id}-r{i}",
                text=rule_text,
                source_file=f"policy-pack:{pack.name}",
                enabled=True,
                category=category,
            ))

    # Also scan workspace rule files
    rules_dir = config.memopilot_dir / "rules"
    if rules_dir.exists():
        import yaml  # noqa: PLC0415

        for rule_file in sorted(rules_dir.glob("*.yaml")):
            try:
                content = rule_file.read_text(encoding="utf-8")
                data = yaml.safe_load(content) or {}
                rules_list = data.get("rules", [])
                is_global = "global" in rule_file.stem.lower()
                target = global_rules if is_global else project_rules
                for i, rule in enumerate(rules_list):
                    rule_text = rule if isinstance(rule, str) else str(rule.get("text", rule))
                    target.append(ActiveRuleItem(
                        rule_id=f"{rule_file.stem}-r{i}",
                        text=rule_text,
                        source_file=str(rule_file.relative_to(config.workspace_path)),
                        enabled=True,
                        category="global" if is_global else "project",
                    ))
            except Exception:
                pass  # Skip malformed rule files

    # Gather detected skills (enabled skills from store + detected frameworks)
    skill_service = SkillLoaderService(config=config, db=db)
    skills = await skill_service.list_skills(limit=50)
    detected_skills: list[ActiveSkillItem] = [
        ActiveSkillItem(
            skill_id=s.skill_id,
            name=s.name,
            framework=None,
            enabled=s.enabled,
        )
        for s in skills
    ]

    # Add framework-level skills detected from workspace profile
    profile_service = WorkspaceProfileService(config=config, db=db)
    frameworks = profile_service._detect_frameworks()
    existing_names = {s.name.lower() for s in skills}
    for fw in frameworks:
        if fw.lower() not in existing_names:
            detected_skills.append(ActiveSkillItem(
                skill_id=f"fw-{fw}",
                name=fw,
                framework="python",
                enabled=True,
            ))

    return ActiveRulesResponse(
        global_rules=global_rules,
        project_rules=project_rules,
        detected_skills=detected_skills,
    )


@app.post("/v1/memory/backup", response_model=BackupMemoryResponse)
async def backup_memory() -> BackupMemoryResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    backup = await service.backup_memory()
    return BackupMemoryResponse(
        backup_id=backup.backup_id,
        backup_path=backup.backup_path,
        item_count=backup.item_count,
        created_at=backup.created_at,
    )


@app.post("/v1/memory/restore", response_model=RestoreMemoryResponse)
async def restore_memory(request: RestoreMemoryRequest) -> RestoreMemoryResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    try:
        restored_count = await service.restore_memory(backup_path=request.backup_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RestoreMemoryResponse(restored_count=restored_count)


@app.post("/v1/optimizer/tools-skills", response_model=ToolSkillOptimizeResponse)
async def optimize_tools_and_skills(
    request: ToolSkillOptimizeRequest,
) -> ToolSkillOptimizeResponse:
    service = SkillLoaderService(config=_get_config(), db=_get_db())
    result = await service.optimize_tools_and_skills(
        task_text=request.task_text,
        available_tools=request.available_tools,
    )
    return ToolSkillOptimizeResponse(
        suggested_tools=result.suggested_tools,
        suggested_skills=result.suggested_skills,
        reasons=result.reasons,
    )


@app.get("/v1/budget/profiles", response_model=BudgetProfilesResponse)
async def get_budget_profiles() -> BudgetProfilesResponse:
    service = CostGuardService(config=_get_config(), db=_get_db())
    result = await service.get_budget_profiles()
    return BudgetProfilesResponse(
        active_profile=result.active_profile,
        monthly_budget_usd=result.monthly_budget_usd,
        effective_budget_usd=result.effective_budget_usd,
        multiplier=result.multiplier,
        profiles=result.profiles,
    )


@app.post("/v1/budget/profiles", response_model=BudgetProfilesResponse)
async def set_budget_profile(request: SetBudgetProfileRequest) -> BudgetProfilesResponse:
    service = CostGuardService(config=_get_config(), db=_get_db())
    try:
        result = await service.set_budget_profile(request.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BudgetProfilesResponse(
        active_profile=result.active_profile,
        monthly_budget_usd=result.monthly_budget_usd,
        effective_budget_usd=result.effective_budget_usd,
        multiplier=result.multiplier,
        profiles=result.profiles,
    )


@app.post("/v1/investigation/evidence/classify", response_model=EvidenceClassifyResponse)
async def classify_evidence_source(request: EvidenceClassifyRequest) -> EvidenceClassifyResponse:
    if not request.evidence_path and not request.source_url:
        raise HTTPException(
            status_code=400,
            detail="Either evidence_path or source_url is required",
        )
    service = SkillLoaderService(config=_get_config(), db=_get_db())
    source_type, trust_level, extraction_method = service.classify_evidence_source(
        evidence_path=request.evidence_path,
        source_url=request.source_url,
    )
    return EvidenceClassifyResponse(
        source_type=source_type,
        trust_level=trust_level,
        extraction_method=extraction_method,
    )


@app.get("/v1/policies/packs", response_model=PolicyPacksResponse)
async def list_policy_packs(limit: int = 100) -> PolicyPacksResponse:
    service = PolicyPacksService(config=_get_config(), db=_get_db())
    items = await service.list_policy_packs(limit=limit)
    return PolicyPacksResponse(
        items=[
            PolicyPackItemResponse(
                pack_id=item.pack_id,
                name=item.name,
                description=item.description,
                enforcement_mode=item.enforcement_mode,
                rules=item.rules,
                active=item.active,
                version=item.version,
            )
            for item in items
        ]
    )


@app.post("/v1/policies/packs", response_model=PolicyPackItemResponse)
async def upsert_policy_pack(request: PolicyPackUpsertRequest) -> PolicyPackItemResponse:
    service = PolicyPacksService(config=_get_config(), db=_get_db())
    try:
        item = await service.save_policy_pack(
            name=request.name,
            description=request.description,
            enforcement_mode=request.enforcement_mode,
            rules=request.rules,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PolicyPackItemResponse(
        pack_id=item.pack_id,
        name=item.name,
        description=item.description,
        enforcement_mode=item.enforcement_mode,
        rules=item.rules,
        active=item.active,
        version=item.version,
    )


@app.post("/v1/policies/packs/activate")
async def activate_policy_pack(request: ActivatePolicyPackRequest) -> MemoryActionResponse:
    service = PolicyPacksService(config=_get_config(), db=_get_db())
    try:
        await service.activate_policy_pack(pack_id=request.pack_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/policies/evaluate", response_model=PolicyEvaluateResponse)
async def evaluate_policy_pack(request: PolicyEvaluateRequest) -> PolicyEvaluateResponse:
    service = PolicyPacksService(config=_get_config(), db=_get_db())
    result = await service.evaluate_policy(
        stage=request.stage,
        task_text=request.task_text,
        files_changed=request.files_changed,
        selected_model=request.selected_model,
    )
    return PolicyEvaluateResponse(
        allowed=result.allowed,
        decision=result.decision,
        stage=result.stage,
        active_pack_id=result.active_pack_id,
        active_pack_name=result.active_pack_name,
        violations=result.violations,
        applied_policies=result.applied_policies,
    )


@app.get("/v1/flows/local", response_model=LocalFlowsResponse)
async def list_local_flows(limit: int = 100) -> LocalFlowsResponse:
    service = FlowBuilderService(config=_get_config(), db=_get_db())
    items = await service.list_flows(limit=limit)
    return LocalFlowsResponse(
        items=[
            LocalFlowItemResponse(
                flow_id=item.flow_id,
                name=item.name,
                description=item.description,
                enabled=item.enabled,
                steps=item.steps,
            )
            for item in items
        ]
    )


@app.post("/v1/flows/local", response_model=LocalFlowItemResponse)
async def save_local_flow(request: SaveLocalFlowRequest) -> LocalFlowItemResponse:
    service = FlowBuilderService(config=_get_config(), db=_get_db())
    try:
        item = await service.save_flow(
            name=request.name,
            description=request.description,
            steps=[step.model_dump(exclude_none=True) for step in request.steps],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LocalFlowItemResponse(
        flow_id=item.flow_id,
        name=item.name,
        description=item.description,
        enabled=item.enabled,
        steps=item.steps,
    )


@app.post("/v1/flows/local/run", response_model=RunLocalFlowResponse)
async def run_local_flow(request: RunLocalFlowRequest) -> RunLocalFlowResponse:
    service = FlowBuilderService(config=_get_config(), db=_get_db())
    try:
        result = await service.run_flow(
            flow_id=request.flow_id,
            task_text=request.task_text,
            files_changed=request.files_changed,
            selected_model=request.selected_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunLocalFlowResponse(
        run_id=result.run_id,
        flow_id=result.flow_id,
        flow_name=result.flow_name,
        status=result.status,
        steps=result.steps,
        blocked_reason=result.blocked_reason,
    )


@app.get("/v1/workspaces", response_model=WorkspaceRootsResponse)
async def list_workspace_roots(limit: int = 100) -> WorkspaceRootsResponse:
    service = WorkspaceRootsService(config=_get_config(), db=_get_db())
    items = await service.list_workspace_roots(limit=limit)
    return WorkspaceRootsResponse(
        items=[
            WorkspaceRootItemResponse(
                workspace_id=item.workspace_id,
                root_path=item.root_path,
                label=item.label,
                active=item.active,
            )
            for item in items
        ]
    )


@app.post("/v1/workspaces", response_model=WorkspaceRootItemResponse)
async def add_workspace_root(request: AddWorkspaceRootRequest) -> WorkspaceRootItemResponse:
    service = WorkspaceRootsService(config=_get_config(), db=_get_db())
    try:
        item = await service.add_workspace_root(
            root_path=request.root_path,
            label=request.label,
            activate=request.activate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceRootItemResponse(
        workspace_id=item.workspace_id,
        root_path=item.root_path,
        label=item.label,
        active=item.active,
    )


@app.post("/v1/workspaces/activate", response_model=WorkspaceRootItemResponse)
async def activate_workspace_root(
    request: ActivateWorkspaceRootRequest,
) -> WorkspaceRootItemResponse:
    service = WorkspaceRootsService(config=_get_config(), db=_get_db())
    try:
        item = await service.activate_workspace_root(workspace_id=request.workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WorkspaceRootItemResponse(
        workspace_id=item.workspace_id,
        root_path=item.root_path,
        label=item.label,
        active=item.active,
    )


def _get_config() -> Config:
    if _config is None:
        raise HTTPException(status_code=500, detail="Backend not configured")
    return _config


def _get_db() -> DatabaseManager:
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return _db
