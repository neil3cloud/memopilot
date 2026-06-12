"""FastAPI application for MemoPilot agent backend.

Routes:
  GET  /v1/health         — Health check with version info
  POST /v1/workspace/init — Initialize .memopilot/ workspace structure
  POST /v1/workspace/index — Scan workspace and index Python files/symbols

Security:
  All routes require X-Agent-Token header matching MEMOPILOT_TOKEN env var.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Config
from .cost_guard import CostGuardService
from .db import DatabaseManager
from .investigation_service import InvestigationService
from .mcp_orchestrator import MCPOrchestrator, ToolCall
from .memory_manager_service import MemoryManagerService
from .migration_runner import run_migrations
from .privacy_dashboard_service import PrivacyDashboardService
from .provider_resilience import ProviderCallError, ProviderResilienceService
from .response_cache import ResponseCacheService
from .security_policy import CredentialRedactor, DatabaseWriteBlocker
from .waveb_service import (
    ProviderCapabilityRecord,
    WaveBService,
)
from .wavec_service import WaveCService
from .workspace_indexer import WorkspaceIndexer
from .workspace_init import ensure_global_config
from .workspace_profile_service import WorkspaceProfileService

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


class StartTaskRunResponse(BaseModel):
    task_run_id: str
    status: str


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
    if not token or token != _expected_token:
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


@app.post("/v1/task-runs/start", response_model=StartTaskRunResponse)
async def start_task_run(request: StartTaskRunRequest) -> StartTaskRunResponse:
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
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveBService(config=_get_config(), db=_get_db())
    try:
        await service.select_template(request.template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/context/versions", response_model=ContextPackVersionResponse)
async def store_context_pack_version(
    request: ContextPackVersionStoreRequest,
) -> ContextPackVersionResponse:
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveBService(config=_get_config(), db=_get_db())
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
    service = WaveCService(config=_get_config(), db=_get_db())
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
    service = WaveCService(config=_get_config(), db=_get_db())
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


@app.post("/v1/memory/backup", response_model=BackupMemoryResponse)
async def backup_memory() -> BackupMemoryResponse:
    service = WaveCService(config=_get_config(), db=_get_db())
    backup = await service.backup_memory()
    return BackupMemoryResponse(
        backup_id=backup.backup_id,
        backup_path=backup.backup_path,
        item_count=backup.item_count,
        created_at=backup.created_at,
    )


@app.post("/v1/memory/restore", response_model=RestoreMemoryResponse)
async def restore_memory(request: RestoreMemoryRequest) -> RestoreMemoryResponse:
    service = WaveCService(config=_get_config(), db=_get_db())
    try:
        restored_count = await service.restore_memory(backup_path=request.backup_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RestoreMemoryResponse(restored_count=restored_count)


@app.post("/v1/optimizer/tools-skills", response_model=ToolSkillOptimizeResponse)
async def optimize_tools_and_skills(
    request: ToolSkillOptimizeRequest,
) -> ToolSkillOptimizeResponse:
    service = WaveCService(config=_get_config(), db=_get_db())
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
    service = WaveCService(config=_get_config(), db=_get_db())
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
    service = WaveCService(config=_get_config(), db=_get_db())
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
    service = WaveCService(config=_get_config(), db=_get_db())
    source_type, trust_level, extraction_method = service.classify_evidence_source(
        evidence_path=request.evidence_path,
        source_url=request.source_url,
    )
    return EvidenceClassifyResponse(
        source_type=source_type,
        trust_level=trust_level,
        extraction_method=extraction_method,
    )


def _get_config() -> Config:
    if _config is None:
        raise HTTPException(status_code=500, detail="Backend not configured")
    return _config


def _get_db() -> DatabaseManager:
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return _db
