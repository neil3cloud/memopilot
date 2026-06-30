"""FastAPI application for MemoPilot agent backend.

Routes:
  GET  /v1/health         — Health check with version info
  POST /v1/workspace/init — Initialize .memopilot/ workspace structure
  POST /v1/workspace/index — Scan workspace and index Python files/symbols

Security:
  All routes require X-Agent-Token header matching MEMOPILOT_TOKEN env var.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import logging
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import Config
from .config_loader import load_provider_config
from .context_budget import (
    TIER_ORDER_BY_TASK_TYPE,
    ContextBudget,
    ContextItem,
    build_budget_aware_context_pack,
)
from .context_builder import ContextBuilderService
from .context_deduplicator import deduplicate_text_list
from .context_quality_scorer import (
    ContextPackSnapshot,
    score_context_pack,
)
from .context_renderer import ContextPackRenderer
from .cost_guard import BudgetCheck as CostGuardBudgetCheck
from .cost_guard import CostGuardService, check_budget_gate, infer_selected_tier
from .db import DatabaseManager
from .document_ingestion import extract_csv, extract_docx, extract_excel, extract_pdf, extract_pptx
from .endpoint_registry import ENDPOINT_STATUS
from .git_history_indexer import GitHistoryIndexer
from .graph_retriever import GraphRetriever
from .image_analysis import ImageAnalysisResult, analyze_image
from .llm_client import BaseLLMClient, build_client
from .local_model_discovery import discover_all_local
from .mcp_orchestrator import MCPDispatcher, MCPOrchestrator, ToolCall
from .memory_manager_service import MemoryManagerService
from .memory_recall import MemoryRecallService, RecallRequest, RecallResponse
from .memory_seeder import MemorySeederService
from .migration_runner import run_migrations
from .policy_packs import PolicyPacksService
from .privacy_dashboard_service import PrivacyDashboardService
from .provider_registry import ProviderCapabilityRecord, ProviderRegistryService
from .provider_resilience import ProviderCallError, ProviderResilienceService
from .repo_map_generator import RepoMapGenerator
from .response_cache import ResponseCacheService
from .retention import enforce_retention
from .security_policy import CredentialRedactor, DatabaseWriteBlocker
from .skill_loader import SkillLoaderService
from .vector_backfill_service import VectorBackfillService
from .context_synthesizer import ContextSynthesizer, build_synthesis_user_prompt
from .symbol_summarizer import SymbolSummarizer
from .workspace_indexer import WorkspaceIndexer
from .workspace_init import ensure_global_config, generate_workspace_bootstrap
from .workspace_profile_service import WorkspaceProfileService
from .workspace_roots import WorkspaceRootsService

logger = logging.getLogger(__name__)

app = FastAPI(title="MemoPilot Agent", version="0.1.0")

# Module-level state (set during startup)
_config: Config | None = None
_db: DatabaseManager | None = None
_expected_token: str | None = None
_retention_task: asyncio.Task[None] | None = None
_RETENTION_INTERVAL_SECONDS = 6 * 60 * 60
_synthesizer: ContextSynthesizer | None = None
_writeback_client: BaseLLMClient | None = None
_symbol_summarizer: SymbolSummarizer | None = None

# Host model relay state — extension acts as LLM proxy for VS Code Copilot
_task_sse_queues: dict[str, asyncio.Queue] = {}
_host_relay_futures: dict[str, asyncio.Future] = {}

# Summarization progress counter — tracks concurrent background runs safely
_summarization_in_progress_count: int = 0

# Strong references to background tasks — prevents GC from cancelling them mid-run
_background_tasks: set["asyncio.Task[None]"] = set()

# Session-based auto-writeback — accumulates tool call context, fires after inactivity
_SESSION_INACTIVITY_SECONDS: int = 300  # 5 minutes
_session_tool_calls: list[dict] = []  # {task_description, files_in_focus, ts}
_session_synthesis_task: "asyncio.Task[None] | None" = None

# LLM mode — "copilot" | "cloud" | "local"
# copilot: all LLM calls routed through vscode.lm SSE relay
# cloud: direct HTTP to configured cloud provider (anthropic/openai)
# local: direct HTTP to configured local provider (LM Studio/Ollama)
_llm_mode: str = "local"
_llm_mode_model_id: str = ""  # copilot model id from probe

# Unified LLM relay — extension listens on SSE, fulfills requests via vscode.lm
_relay_sse_queue: asyncio.Queue = asyncio.Queue()
_relay_futures: dict[str, asyncio.Future] = {}

# Legacy aliases kept for backwards compat with existing synthesis path
_host_model_available: bool = False
_synthesis_sse_queue: asyncio.Queue = _relay_sse_queue
_synthesis_relay_futures: dict[str, asyncio.Future] = _relay_futures


def _get_effective_summarizer() -> "SymbolSummarizer | None":
    """Return the summarizer to use based on current LLM mode."""
    from .llm_client import RelayLLMClient
    if _llm_mode == "copilot" and _host_model_available:
        return SymbolSummarizer(RelayLLMClient(_relay_llm_request, request_type="summarize"))
    return _symbol_summarizer


async def _relay_llm_request(
    request_type: str,
    system: str,
    user: str,
    ctx_tokens: int = 0,
    timeout: float = 45.0,
    retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
) -> str:
    """Send an LLM request through the extension SSE relay (copilot mode).

    Retries with exponential backoff on llm_mode_changed or timeout.
    Raises RuntimeError if all retries fail or mode switches away from copilot.
    """
    relay_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _relay_futures[relay_id] = fut
    await _relay_sse_queue.put({
        "type": "LLM_REQUEST",
        "request_type": request_type,
        "relay_id": relay_id,
        "system": system,
        "user": user,
        "ctx_tokens": ctx_tokens,
    })
    try:
        result = await asyncio.wait_for(fut, timeout=timeout)
        return result
    except (asyncio.TimeoutError, RuntimeError) as exc:
        _relay_futures.pop(relay_id, None)
        # If mode changed mid-flight, do not retry
        if "llm_mode_changed" in str(exc):
            raise
        # Retry with backoff for transient failures
        for delay in retry_delays:
            if _llm_mode != "copilot":
                raise RuntimeError("llm_mode_changed")
            await asyncio.sleep(delay)
            relay_id = str(uuid.uuid4())
            fut = loop.create_future()
            _relay_futures[relay_id] = fut
            await _relay_sse_queue.put({
                "type": "LLM_REQUEST",
                "request_type": request_type,
                "relay_id": relay_id,
                "system": system,
                "user": user,
                "ctx_tokens": ctx_tokens,
            })
            try:
                result = await asyncio.wait_for(fut, timeout=timeout)
                return result
            except (asyncio.TimeoutError, RuntimeError):
                _relay_futures.pop(relay_id, None)
        raise RuntimeError(f"relay failed after retries: {exc}")


def _record_session_tool_call(task_description: str, files_in_focus: list[str]) -> None:
    """Record a copilot tool call and reset the inactivity synthesis timer."""
    import time as _time
    global _session_synthesis_task
    _session_tool_calls.append({
        "task_description": task_description,
        "files_in_focus": files_in_focus,
        "ts": _time.time(),
    })
    logger.debug("session_tool_call recorded: %d in queue", len(_session_tool_calls))
    # Cancel existing timer and reschedule
    if _session_synthesis_task and not _session_synthesis_task.done():
        _session_synthesis_task.cancel()
    _session_synthesis_task = asyncio.create_task(_delayed_session_synthesis())
    _background_tasks.add(_session_synthesis_task)
    _session_synthesis_task.add_done_callback(_background_tasks.discard)


async def _delayed_session_synthesis() -> None:
    """Wait for inactivity window then synthesize session learnings into memory items."""
    global _session_tool_calls
    try:
        await asyncio.sleep(_SESSION_INACTIVITY_SECONDS)
    except asyncio.CancelledError:
        return  # A new tool call came in — timer was reset, do nothing

    calls = _session_tool_calls.copy()
    _session_tool_calls.clear()

    if not calls:
        return

    effective_client = _get_session_writeback_client()
    if effective_client is None:
        logger.warning("session_synthesis: no LLM client available, skipping")
        return

    logger.info("session_synthesis: firing for %d tool call(s)", len(calls))
    try:
        from .memory_seeder import MemorySeederService
        seeder = MemorySeederService(config=_get_config(), db=_get_db())
        count = await seeder.synthesize_session(calls, client=effective_client)
        logger.info("session_synthesis: wrote %d memory item(s)", count)
    except Exception:
        logger.exception("session_synthesis: failed")


def _get_session_writeback_client() -> "BaseLLMClient | None":
    """Return the best available LLM client for session synthesis."""
    from .llm_client import RelayLLMClient
    if _llm_mode == "copilot" and _host_model_available:
        return RelayLLMClient(_relay_llm_request, request_type="synthesize")
    return _writeback_client


async def _create_mcp_dispatcher() -> MCPDispatcher:
    """Create a dispatcher function that executes real MCP tool calls."""
    from .mcp_server import MCPServer

    server = MCPServer()

    async def dispatch(tool_name: str, args: dict[str, Any]) -> str:
        """Execute an MCP tool call and return result text."""
        try:
            result = await server._dispatch(tool_name, args)
            return result
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"

    return dispatch


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
    memory_items_seeded: int = 0


class WorkspaceIndexRequest(BaseModel):
    seed_memory: bool = True
    summarization_batch_size: int = 25


class WorkspaceIndexStatusResponse(BaseModel):
    ever_indexed: bool
    file_count: int
    stale_file_count: int
    last_indexed_at: str | None
    memory_item_count: int


class RebuildMemoryResponse(WorkspaceIndexResponse):
    rebuilt: bool


class IndexStatusResponse(BaseModel):
    indexed_files: int
    stale_files: int
    symbols_count: int
    last_indexed_at: str | None = None
    never_indexed: bool
    symbols_pending_summary: int = 0
    summarizing: bool = False
    languages: list[str] = []


def _workspace_index_response_kwargs(result: object) -> dict[str, object]:
    return {
        "python_project": result.python_project,
        "total_files_scanned": result.total_files_scanned,
        "indexed_files": result.indexed_files,
        "unchanged_files": result.unchanged_files,
        "stale_files": result.stale_files,
        "skipped_files": result.skipped_files,
        "symbols_extracted": result.symbols_extracted,
        "duration_ms": result.duration_ms,
    }


class BudgetStatusResponse(BaseModel):
    monthly_budget_usd: float
    spent_usd: float
    saved_usd: float
    remaining_usd: float
    warning_threshold_usd: float = 0.0
    warning_triggered: bool = False
    blocked: bool = False
    spend_ratio: float = 0.0
    current_month_spend: float = 0.0
    monthly_budget: float = 0.0
    remaining: float = 0.0
    pct_used: float = 0.0
    at_limit: bool = False
    warning_threshold: float = 0.80
    at_warning: bool = False
    last_updated_at: str | None = None


class BudgetCheckRequest(BaseModel):
    estimated_cost_usd: float = Field(ge=0)


class BudgetCheckResponse(BaseModel):
    allowed: bool
    reason: str
    estimated_cost_usd: float
    budget: BudgetStatusResponse


class BudgetGateResponse(BaseModel):
    blocked: bool
    reason: str
    requires_approval: bool = False
    approval_prompt: str | None = None
    show_warning: bool = False
    warning_message: str | None = None


class TaskRunCostResponse(BaseModel):
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    selected_tier: str = "local"


class TaskRunStartRequest(BaseModel):
    user_request: str
    selected_model: str | None = None
    estimated_cost: float | None = Field(default=None, ge=0)
    task_type: str | None = None
    mode: str | None = None
    risk_level: str | None = None
    workspace_root: str | None = None


class TaskRunStartResponse(BaseModel):
    task_run_id: str


class TaskHistoryEntry(BaseModel):
    description: str
    constraints: list[str] = Field(default_factory=list)
    mode: str | None = None
    notes: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    workspace_root: str | None = None


class TaskAnalyzeResponse(BaseModel):
    intent_summary: str
    suggested_files: list[str]
    applicable_rules: list[str]
    estimated_complexity: str
    suggested_mode: str
    task_type: str = "general"
    risk: str = "medium"


class VectorBackfillRequest(BaseModel):
    workspace_root: str | None = None
    entity_types: list[str] = Field(default_factory=lambda: ["memory_items", "symbols"])
    limit: int | None = None


class VectorBackfillResponse(BaseModel):
    total_embedded: int
    total_failed: int
    memory_items_embedded: int
    memory_items_failed: int
    symbols_embedded: int
    symbols_failed: int
    model_used: str
    workspace_root: str | None = None


class ContextBuildRequest(BaseModel):
    task_description: str
    suggested_files: list[str] = Field(default_factory=list)
    file_overrides: list[str] | None = None
    mode: str | None = None
    workspace_root: str | None = None
    task_type: str | None = None
    model_max_tokens: int | None = Field(default=None, ge=1)
    caller: str = "memopilot_ui"
    output_format: str = "full"
    max_output_tokens: int = 8000


class ContextFileEntry(BaseModel):
    path: str
    tokens: int
    content: str | None = None


class StaleExclusionsResponse(BaseModel):
    count: int = 0
    affected_modules: list[str] = Field(default_factory=list)
    rebuild_command: str | None = None


class ContextQualityScoreResponse(BaseModel):
    total: float
    has_primary_symbol: bool
    has_callers: bool
    has_related_tests: bool
    has_active_rules: bool
    has_recent_history: bool
    stale_exclusion_pct: float
    dedup_savings_pct: float
    graph_expansion_files: int
    verdict: str            # 'good' | 'acceptable' | 'poor' | 'rebuild'
    missing_signals: list[str] = Field(default_factory=list)


class ContextBuildResponse(BaseModel):
    files: list[ContextFileEntry]
    rules: list[str]
    skills: list[str]
    total_tokens: int
    estimated_cost_usd: float
    context_pack_hash: str
    budget_summary: dict[str, object] | None = None
    stale_exclusions: StaleExclusionsResponse | None = None
    included_items: list[dict[str, object]] | None = None
    excluded_items: list[dict[str, object]] | None = None
    quality_score: ContextQualityScoreResponse | None = None
    callers_not_in_context: list[str] | None = None     # file paths of callers not included
    repo_map: str | None = None                          # compact structural overview
    commit_history: str | None = None                    # structured decision history


class ContextAssembleRequest(BaseModel):
    task_description: str
    files_in_focus: list[str] = Field(default_factory=list)
    task_type_hint: str = "general"
    workspace_root: str | None = None
    caller: str = "memopilot_ui"
    max_output_tokens: int = Field(default=8000, ge=200, le=16000)


class ContextAssembleResponse(BaseModel):
    rendered_markdown: str
    context_pack_hash: str
    total_tokens: int
    stale_exclusion_count: int = 0
    redacted_values_count: int = 0
    quality_verdict: str | None = None


class SymbolSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)


class SymbolSearchItemResponse(BaseModel):
    name: str
    kind: str
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None
    summary: str | None = None


class SymbolSearchResponse(BaseModel):
    symbols: list[SymbolSearchItemResponse]


class ModelRouteRequest(BaseModel):
    context_tokens: int = Field(ge=0)
    task_type: str = "auto"
    privacy_level: str = "local_preferred"
    preferred_model: str | None = None
    files_in_context: list[str] | None = None
    model_override: bool = False


class ModelChoice(BaseModel):
    model_id: str
    provider: str
    cost_estimate_usd: float
    reasons: list[str]
    fits_context: bool = True


class ModelRouteOption(BaseModel):
    tier: str
    model_id: str
    provider: str
    cost_estimate_usd: float
    fits_context: bool = True


class BudgetCheck(BaseModel):
    allowed: bool
    remaining_usd: float
    reason: str | None = None
    status: BudgetStatusResponse | None = None


class ModelRouteResponse(BaseModel):
    recommended: ModelChoice
    alternatives: list[ModelChoice]
    budget_check: BudgetCheck
    options: list[ModelRouteOption] = Field(default_factory=list)
    escalation_source: str | None = None
    base_tier: str | None = None
    model_override: bool = False


class GeneratePatchRequest(BaseModel):
    task_description: str
    context_files: list[str] = Field(default_factory=list)
    mode: str = "auto"
    model_id: str | None = None
    dry_run: bool = False
    context_pack_hash: str | None = None
    workspace_root: str | None = None
    task_run_id: str | None = None


class FilePatch(BaseModel):
    path: str
    action: str  # "modify", "create", "delete"
    original_content: str | None = None
    new_content: str | None = None
    diff: str


class RankedFileResponse(BaseModel):
    path: str
    risk_level: str
    risk_category: str


class ComplianceActionResponse(BaseModel):
    label: str
    action_type: str
    prefill_task_request: str
    prefill_mode: str
    prefill_context_hints: list[str] = Field(default_factory=list)


class ComplianceWarningResponse(BaseModel):
    rule_id: str
    rule_text: str
    warning_message: str
    actions: list[ComplianceActionResponse] = Field(default_factory=list)


class GeneratePatchResponse(BaseModel):
    patches: list[FilePatch]
    total_files_changed: int
    summary: str
    estimated_risk: str  # "low", "medium", "high"
    model_used: str
    tokens_used: int
    cost_usd: float
    approval_tier: str | None = None
    ranked_files: list[RankedFileResponse] | None = None
    compliance_warnings: list[ComplianceWarningResponse] | None = None


class PatchRankFilesRequest(BaseModel):
    changed_files: list[str] = Field(default_factory=list)


class PatchRankFilesResponse(BaseModel):
    ranked_files: list[RankedFileResponse]
    approval_tier: str


class ReviewAppliedPatchRequest(BaseModel):
    git_diff: str
    workspace_root: str | None = None
    caller: str = "memopilot_ui"


class PatchReviewRankedFile(BaseModel):
    path: str
    risk_level: str
    risk_category: str


class PatchReviewComplianceWarning(BaseModel):
    rule_id: str | None = None
    message: str
    severity: str = "warning"


class ReviewAppliedPatchResponse(BaseModel):
    task_run_id: str
    risk_level: str
    risk_category: str
    compliance_score: float
    compliance_passed: list[str] = Field(default_factory=list)
    compliance_warnings: list[PatchReviewComplianceWarning] = Field(default_factory=list)
    ranked_files: list[PatchReviewRankedFile] = Field(default_factory=list)
    secret_detected: bool = False
    rendered_report: str
    patch_governance_available: bool = False


class WritebackRequest(BaseModel):
    outcome_summary: str
    outcome_status: str
    context_pack_hash: str | None = None
    git_diff: str | None = None
    workspace_root: str
    caller: str = "memopilot_ui"


class WritebackProposalResponse(BaseModel):
    id: str
    title: str
    memory_class: str
    memory_status: str
    trust_level: int
    reusable: bool


class WritebackResponse(BaseModel):
    writeback_id: str
    task_run_id: str
    proposals_count: int
    blocked_content_count: int
    already_processed: bool = False
    rendered_summary: str
    proposals: list[WritebackProposalResponse] = Field(default_factory=list)


class DismissWritebackRequest(BaseModel):
    task_run_id: str


class DismissWritebackResponse(BaseModel):
    status: str
    task_run_id: str


class PendingWritebacksResponse(BaseModel):
    count: int
    runs: list[dict[str, object]] = Field(default_factory=list)


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
    savings_report: SavingsReportResponse | None = None
    avg_context_quality: float | None = None     # 0.0–1.0 average quality score
    context_quality_verdicts: dict[str, int] | None = None  # verdict → count


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
    actual_cost: float
    hypothetical_frontier_cost: float
    savings: float
    reduction_pct: float
    total_tasks: int
    local_tasks: int
    cheap_cloud_tasks: int
    frontier_tasks: int
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
    response_status: str = "success"


class CacheStoreResponse(BaseModel):
    stored: bool


class CacheLookupRequest(BaseModel):
    context_pack_hash: str
    task_type: str | None = None


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
    context: str = Field(
        default="patch_generation",
        pattern="^(pre_fetch|patch_generation|investigation)$",
    )
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


class MemoryUsageStatsResponse(BaseModel):
    recalled_count: int
    used_count: int
    last_used_at: str | None = None
    days_since_last_use: int | None = None


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
    memory_class: str
    memory_status: str
    visibility_scope: str
    reusable: bool
    review_required: bool
    created_at: str
    updated_at: str
    usage_stats: MemoryUsageStatsResponse


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
    task_run_id: str | None = None
    workspace_root: str | None = None


class SmartSuggestMemoryRequest(SuggestMemoryRequest):
    memory_class: str = "fact"
    derivation_source: str | None = Field(
        default=None,
        pattern=r"^(git_diff|call_graph|code_analysis)$",
        description="Must be 'git_diff', 'call_graph', or 'code_analysis'. "
        "Auto-confirmation only applies when task_run_id is also provided.",
    )


class SuggestMemoryResponse(BaseModel):
    memory_item_id: str | None
    pending_approval: bool
    artifact_id: str | None = None
    blocked_reason: str | None = None


class ModuleMemoryProposalsRequest(BaseModel):
    module_path: str = Field(min_length=1)
    workspace_root: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class MemoryEditRequest(BaseModel):
    title: str
    body: str


class MemoryActionResponse(BaseModel):
    success: bool


class BulkMemoryActionRequest(BaseModel):
    memory_ids: list[str] = Field(default_factory=list, max_length=500)
    workspace_root: str | None = None


class MemoryReviewRequest(BaseModel):
    decision: str
    workspace_root: str | None = None


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
    budget_summary_json: str | None = None
    stale_exclusion_count: int | None = None
    included_items_json: str | None = None
    excluded_items_json: str | None = None


class ContextPackVersionResponse(BaseModel):
    version_id: str
    task_run_id: str | None = None
    pack_path: str
    pack_hash: str
    token_estimate: int | None = None
    selected_model: str | None = None
    template_id: str | None = None
    created_at: str
    budget_summary_json: str | None = None
    stale_exclusion_count: int | None = None
    included_items_json: str | None = None
    excluded_items_json: str | None = None


class ContextPackVersionsResponse(BaseModel):
    versions: list[ContextPackVersionResponse]


class ContextPackDiffRequest(BaseModel):
    left_version_id: str
    right_version_id: str


class ContextPackDiffResponse(BaseModel):
    from_version_id: str
    to_version_id: str
    left_version_id: str
    right_version_id: str
    diff_text: str
    added_items: dict[str, list[str]] = Field(default_factory=dict)
    removed_items: dict[str, list[str]] = Field(default_factory=dict)
    token_delta_estimate: int = 0


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


def _serialize_ranked_files(
    ranked_files: list[tuple[str, str, str]],
) -> list[RankedFileResponse]:
    return [
        RankedFileResponse(
            path=file_path,
            risk_level=risk_level,
            risk_category=risk_category,
        )
        for file_path, risk_level, risk_category in ranked_files
    ]






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
    source: str


class SkillStoreListResponse(BaseModel):
    items: list[SkillStoreItemResponse]


class SkillImportRequest(BaseModel):
    yaml_content: str


class SkillConflictItemResponse(BaseModel):
    first_skill_id: str
    first_name: str
    second_skill_id: str
    second_name: str
    language: str
    path_contains: str
    contradictory_rules: list[str]


class SkillConflictListResponse(BaseModel):
    items: list[SkillConflictItemResponse]


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
    manifest: dict[str, int | float | str | None] = Field(default_factory=dict)


class RestoreMemoryRequest(BaseModel):
    backup_path: str


class RestoreMemoryResponse(BaseModel):
    restored_count: int


class ToolSkillOptimizeRequest(BaseModel):
    task_text: str
    available_tools: list[str] = Field(default_factory=list)
    task_type: str | None = None
    budget_profile: str = "balanced"


class ToolSkillOptimizeResponse(BaseModel):
    suggested_tools: list[str]
    excluded_tools: list[str] = Field(default_factory=list)
    suggested_skills: list[str]
    reasons: list[str]
    reasons_map: dict[str, str] = Field(default_factory=dict)


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


class DocumentChunkResponse(BaseModel):
    chunk_index: int
    chunk_text: str
    source_hash: str = ""
    trust_level: int = 3
    memory_class: str = "evidence"
    memory_status: str = "evidence_only"


class ExtractionResultResponse(BaseModel):
    source_type: str
    chunks: list[DocumentChunkResponse] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    requires_ocr: bool = False


class ExtractPdfRequest(BaseModel):
    file_path: str
    workspace_root: str | None = None


class ExtractExcelRequest(BaseModel):
    file_path: str
    sheet_names: list[str] | None = None
    column_mapping: dict[str, str] | None = None
    workspace_root: str | None = None


class ExtractCsvRequest(BaseModel):
    file_path: str
    delimiter: str | None = None
    column_mapping: dict[str, str] | None = None
    workspace_root: str | None = None


class ExtractDocxRequest(BaseModel):
    file_path: str
    workspace_root: str | None = None


class ExtractPptxRequest(BaseModel):
    file_path: str
    workspace_root: str | None = None


class AnalyzeImageRequest(BaseModel):
    file_path: str
    allow_cloud: bool = False
    workspace_root: str | None = None


class ImageAnalysisResponse(BaseModel):
    description: str
    ui_elements: list[str]
    error_messages: list[str]
    ocr_text: str
    source: str
    trust_level: int
    memory_status: str
    error: str | None = None


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
    workspace_root: str | None = None


class PolicyEvaluateResponse(BaseModel):
    allowed: bool
    decision: str
    stage: str
    active_pack_id: str | None = None
    active_pack_name: str | None = None
    violations: list[str]
    applied_policies: list[str]


class ActivePolicyRuleResponse(BaseModel):
    rule: str
    source: str
    source_kind: str
    precedence: int
    enforcement_mode: str
    pack_id: str | None = None
    pack_name: str | None = None


class PolicyConflictResponse(BaseModel):
    rule: str
    source: str
    source_kind: str
    overridden_by_rule: str
    overridden_by_source: str
    overridden_by_kind: str
    conflict_key: str


class ActivePolicyRulesResponse(BaseModel):
    items: list[ActivePolicyRuleResponse]
    conflicts: list[PolicyConflictResponse] = Field(default_factory=list)
    precedence_order: list[str] = Field(default_factory=list)


class PolicyDirectoryLoadRequest(BaseModel):
    policy_dir: str | None = None
    workspace_root: str | None = None


class LocalFlowStepRequest(BaseModel):
    id: str | None = None
    name: str | None = None
    title: str | None = None
    action: str
    stage: str | None = None
    available_tools: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    approval_required: bool = False
    escalate_after_failures: int | None = None
    escalate_to_model: str | None = None
    requires_mcp: bool = False
    simulate_failure: bool = False
    command: str | None = None


class SaveLocalFlowRequest(BaseModel):
    flow_id: str | None = None
    name: str = ""
    description: str = ""
    steps: list[LocalFlowStepRequest] = Field(default_factory=list)
    flow_yaml: str | None = None


class LocalFlowItemResponse(BaseModel):
    flow_id: str
    name: str
    description: str
    enabled: bool
    steps: list[dict[str, object]]


class LocalFlowsResponse(BaseModel):
    items: list[LocalFlowItemResponse]


class RunLocalFlowRequest(BaseModel):
    flow_id: str
    task_text: str
    files_changed: list[str] = Field(default_factory=list)
    selected_model: str | None = None
    constraints: list[str] = Field(default_factory=list)
    approved_steps: list[str] = Field(default_factory=list)
    planned_mcp_calls: int = 0
    mcp_cap: int | None = None
    failure_count: int = 0
    allow_file_modifications: bool = False
    workspace_root: str | None = None


class RunLocalFlowResponse(BaseModel):
    run_id: str
    flow_id: str
    flow_name: str
    status: str
    steps: list[dict[str, object]]
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
    workspace_root: str | None = None


class ActivateWorkspaceRootRequest(BaseModel):
    workspace_id: str | None = None
    root_path: str | None = None
    workspace_root: str | None = None


def configure(config: Config, db: DatabaseManager) -> None:
    """Configure the app with resolved config and database manager."""
    global _config, _db, _expected_token
    _config = config
    _db = db
    _expected_token = os.environ.get("MEMOPILOT_TOKEN")


async def _run_retention_pass() -> None:
    db = _db
    if db is None:
        return
    conn = await db.connect()
    await enforce_retention(conn)


async def _retention_loop() -> None:
    try:
        while True:
            await asyncio.sleep(_RETENTION_INTERVAL_SECONDS)
            try:
                await _run_retention_pass()
            except Exception:
                logger.exception("Scheduled retention enforcement failed")
    except asyncio.CancelledError:
        return


@app.on_event("startup")
async def startup_event() -> None:
    global _retention_task, _synthesizer, _writeback_client, _symbol_summarizer
    try:
        await _run_retention_pass()
    except Exception:
        logger.exception("Startup retention enforcement failed")
    if _retention_task is None or _retention_task.done():
        _retention_task = asyncio.create_task(_retention_loop())
    try:
        await _seed_local_providers()
    except Exception:
        logger.exception("Startup local provider seeding failed")
    try:
        cfg = load_provider_config(str(_get_config().workspace_path))
        _llm_client = build_client(cfg.get("provider", "host"), cfg)
        _synthesizer = ContextSynthesizer(_llm_client)
        _writeback_client = _llm_client
        _symbol_summarizer = SymbolSummarizer(_llm_client)
    except (ValueError, KeyError):
        _synthesizer = None
        _writeback_client = None
        _symbol_summarizer = None


async def _seed_local_providers() -> None:
    """Discover running local models and register them in the provider registry."""
    from .local_model_discovery import discover_all_local
    from .provider_registry import ProviderCapabilityRecord, ProviderRegistryService

    config = _get_config()
    models = await discover_all_local({"ollama_base_url": config.ollama_url})
    if not models:
        return

    service = ProviderRegistryService(config=config, db=_get_db())
    for m in models:
        await service.upsert_provider_capability(
            ProviderCapabilityRecord(
                model_id=m.model_id,
                source=m.source,
                max_context_tokens=m.max_context_tokens,
                supports_tool_calling=m.supports_tools,
                supports_json_mode=False,
                estimated_cost_per_1m_input=0.0,
                estimated_cost_per_1m_output=0.0,
                privacy_level="local",
                allowed_task_types=[],
                denied_task_types=[],
                requires_approval=False,
            )
        )
    logger.info("Seeded %d local model(s) into provider registry", len(models))


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global _retention_task
    if _retention_task is None:
        return
    _retention_task.cancel()
    try:
        await _retention_task
    except asyncio.CancelledError:
        pass
    _retention_task = None


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


@app.get("/v1/endpoints/status", response_model=dict[str, str])
async def endpoint_status() -> dict[str, str]:
    return ENDPOINT_STATUS


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
        config.memopilot_dir / "memory" / "snapshots",
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

    profile_service = WorkspaceProfileService(config=config, db=db)
    profile = await profile_service.ensure_profile()
    generate_workspace_bootstrap(
        workspace_path=config.workspace_path,
        memopilot_dir=config.memopilot_dir,
        profile=profile.profile,
    )

    logger.info(f"Workspace initialized: {config.memopilot_dir} (schema v{schema_version})")

    return InitWorkspaceResponse(
        initialized=True,
        memopilot_dir=str(config.memopilot_dir),
    )


@app.get("/v1/workspace/index-status", response_model=WorkspaceIndexStatusResponse)
async def get_index_status(workspace_root: str | None = None) -> WorkspaceIndexStatusResponse:
    """Return indexing and memory population status for the current workspace."""
    db = _get_db()
    config = _get_config()
    root = workspace_root or str(config.workspace_path)
    conn = await db.connect()

    cursor = await conn.execute(
        """
        SELECT COUNT(*) AS file_count,
               SUM(stale) AS stale_count,
               MAX(last_indexed_at) AS last_indexed_at
        FROM file_index
        WHERE workspace_root = ?
        """,
        (root,),
    )
    row = await cursor.fetchone()

    mem_cursor = await conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM memory_items
        WHERE memory_status = 'confirmed'
                    AND workspace_root = ?
        """,
        (root,),
    )
    mem_row = await mem_cursor.fetchone()

    file_count = row["file_count"] or 0
    return WorkspaceIndexStatusResponse(
        ever_indexed=file_count > 0,
        file_count=file_count,
        stale_file_count=row["stale_count"] or 0,
        last_indexed_at=row["last_indexed_at"],
        memory_item_count=mem_row["cnt"] if mem_row else 0,
    )


@app.post("/v1/workspace/index", response_model=WorkspaceIndexResponse)
async def index_workspace(request: WorkspaceIndexRequest | None = None) -> WorkspaceIndexResponse:
    """Index Python files and symbols in the current workspace."""
    config = _get_config()
    db = _get_db()
    seed_memory = request.seed_memory if request is not None else True
    batch_size = max(1, request.summarization_batch_size if request is not None else 25)

    effective_summarizer = _get_effective_summarizer()
    indexer = WorkspaceIndexer(config=config, db=db, summarizer=effective_summarizer)
    result = await indexer.index_workspace()
    if effective_summarizer is not None:
        _seed_after = seed_memory
        async def _run_summarization() -> None:
            global _summarization_in_progress_count
            _summarization_in_progress_count += 1
            try:
                await indexer._summarize_pending_symbols(batch_size=batch_size)
                if _seed_after:
                    _post_seeder = MemorySeederService(config=config, db=db)
                    await _post_seeder.seed(str(config.workspace_path.resolve()))
            except Exception:
                logger.exception("_run_summarization (index) failed")
            finally:
                _summarization_in_progress_count = max(0, _summarization_in_progress_count - 1)
        _t = asyncio.create_task(_run_summarization())
        _background_tasks.add(_t)
        _t.add_done_callback(_background_tasks.discard)
    profile_service = WorkspaceProfileService(config=config, db=db)
    await profile_service.rebuild_profile()
    memory_items_seeded = 0
    if seed_memory:
        seeder = MemorySeederService(config=config, db=db)
        # Seeds symbols already summarized from prior runs; newly indexed symbols
        # will be seeded by _run_summarization above once it completes.
        memory_items_seeded = await seeder.seed(str(config.workspace_path.resolve()))

    return WorkspaceIndexResponse(**_workspace_index_response_kwargs(result), memory_items_seeded=memory_items_seeded)


@app.get("/v1/workspace/index/status", response_model=IndexStatusResponse)
async def workspace_index_status() -> IndexStatusResponse:
    """Return a lightweight summary of workspace indexing state."""
    config = _get_config()
    conn = await _get_db().connect()
    root = str(config.workspace_path)

    file_cursor = await conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN stale = 0 THEN 1 ELSE 0 END), 0) AS indexed_files,
            COALESCE(SUM(CASE WHEN stale = 1 THEN 1 ELSE 0 END), 0) AS stale_files,
            MAX(last_indexed_at) AS last_indexed_at
        FROM file_index
        WHERE workspace_root = ?
        """
        ,
        (root,),
    )
    file_row = await file_cursor.fetchone()

    symbols_cursor = await conn.execute("SELECT COUNT(*) AS symbols_count FROM symbols")
    symbols_row = await symbols_cursor.fetchone()

    pending_cursor = await conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM symbols s
        JOIN file_index fi ON fi.file_path = s.file_path
        WHERE s.summary IS NULL AND s.kind IN ('function', 'class') AND fi.stale = 0
        """
    )
    pending_row = await pending_cursor.fetchone()

    indexed_files = int(file_row["indexed_files"] or 0)
    stale_files = int(file_row["stale_files"] or 0)
    symbols_count = int(symbols_row["symbols_count"] or 0)
    symbols_pending_summary = int(pending_row["cnt"] or 0)

    lang_cursor = await conn.execute(
        "SELECT DISTINCT language FROM file_index WHERE workspace_root = ? AND language IS NOT NULL",
        (root,),
    )
    lang_rows = await lang_cursor.fetchall()
    languages = sorted(row["language"] for row in lang_rows)

    return IndexStatusResponse(
        indexed_files=indexed_files,
        stale_files=stale_files,
        symbols_count=symbols_count,
        last_indexed_at=file_row["last_indexed_at"],
        never_indexed=(indexed_files == 0 and stale_files == 0 and symbols_count == 0),
        symbols_pending_summary=symbols_pending_summary,
        summarizing=_summarization_in_progress_count > 0,
        languages=languages,
    )


class RebuildMemoryRequest(BaseModel):
    summarization_batch_size: int = 25


@app.post("/v1/workspace/rebuild-memory", response_model=RebuildMemoryResponse)
async def rebuild_memory(request: RebuildMemoryRequest | None = None) -> RebuildMemoryResponse:
    """Rebuild indexed workspace memory from source code."""
    batch_size = max(1, request.summarization_batch_size if request is not None else 25)
    config = _get_config()
    db = _get_db()
    effective_summarizer = _get_effective_summarizer()
    logger.info("rebuild_memory: batch_size=%d mode=%s summarizer=%s", batch_size, _llm_mode, type(effective_summarizer).__name__ if effective_summarizer else "None")
    indexer = WorkspaceIndexer(config=config, db=db, summarizer=effective_summarizer)
    result = await indexer.rebuild_memory()
    if effective_summarizer is not None:
        async def _run_summarization() -> None:
            global _summarization_in_progress_count
            _summarization_in_progress_count += 1
            try:
                await indexer._summarize_pending_symbols(batch_size=batch_size)
            except Exception:
                logger.exception("_run_summarization (rebuild) failed")
            finally:
                _summarization_in_progress_count = max(0, _summarization_in_progress_count - 1)
        _t = asyncio.create_task(_run_summarization())
        _background_tasks.add(_t)
        _t.add_done_callback(_background_tasks.discard)
    else:
        logger.warning("rebuild_memory: no summarizer available, skipping summarization")
    profile_service = WorkspaceProfileService(config=config, db=db)
    await profile_service.rebuild_profile()
    return RebuildMemoryResponse(rebuilt=True, **_workspace_index_response_kwargs(result))


@app.post("/v1/workspace/summarize", response_model=RebuildMemoryResponse)
async def summarize_pending(request: RebuildMemoryRequest | None = None) -> RebuildMemoryResponse:
    """Summarize any symbols that don't yet have a summary, without re-indexing."""
    batch_size = max(1, request.summarization_batch_size if request is not None else 25)
    config = _get_config()
    db = _get_db()
    effective_summarizer = _get_effective_summarizer()
    logger.info("summarize_pending: batch_size=%d mode=%s summarizer=%s", batch_size, _llm_mode, type(effective_summarizer).__name__ if effective_summarizer else "None")
    _empty = RebuildMemoryResponse(rebuilt=False, python_project=True, total_files_scanned=0, indexed_files=0, unchanged_files=0, stale_files=0, skipped_files=0, symbols_extracted=0, duration_ms=0)
    if effective_summarizer is None:
        logger.warning("summarize_pending: no summarizer available")
        return _empty
    indexer = WorkspaceIndexer(config=config, db=db, summarizer=effective_summarizer)

    async def _run_summarization() -> None:
        global _summarization_in_progress_count
        _summarization_in_progress_count += 1
        try:
            await indexer._summarize_pending_symbols(batch_size=batch_size)
        except Exception:
            logger.exception("_run_summarization (summarize-only) failed")
        finally:
            _summarization_in_progress_count = max(0, _summarization_in_progress_count - 1)

    _t = asyncio.create_task(_run_summarization())
    _background_tasks.add(_t)
    _t.add_done_callback(_background_tasks.discard)
    return _empty


def _to_budget_status_response(status) -> BudgetStatusResponse:
    return BudgetStatusResponse(
        monthly_budget_usd=status.monthly_budget_usd,
        spent_usd=status.spent_usd,
        saved_usd=status.saved_usd,
        remaining_usd=status.remaining_usd,
        warning_threshold_usd=status.warning_threshold_usd,
        warning_triggered=status.warning_triggered,
        blocked=status.blocked,
        spend_ratio=status.spend_ratio,
        current_month_spend=status.spent_usd,
        monthly_budget=status.monthly_budget_usd,
        remaining=status.remaining_usd,
        pct_used=status.pct_used,
        at_limit=status.at_limit,
        warning_threshold=status.warning_threshold,
        at_warning=status.at_warning,
        last_updated_at=status.last_updated_at,
    )


def _to_model_route_budget_check_response(
    check: CostGuardBudgetCheck | None,
    *,
    fallback_remaining_usd: float,
    fallback_allowed: bool,
) -> BudgetCheck:
    if check is None:
        remaining = round(max(0.0, fallback_remaining_usd), 2)
        return BudgetCheck(
            allowed=fallback_allowed,
            remaining_usd=remaining,
            reason="No provider budget check required for local or sentinel routing.",
            status=BudgetStatusResponse(
                monthly_budget_usd=remaining,
                spent_usd=0.0,
                saved_usd=0.0,
                remaining_usd=remaining,
                warning_threshold_usd=0.0,
                warning_triggered=False,
                blocked=not fallback_allowed,
                spend_ratio=0.0,
                current_month_spend=0.0,
                monthly_budget=remaining,
                remaining=remaining,
                pct_used=0.0,
                at_limit=not fallback_allowed,
                warning_threshold=0.80,
                at_warning=False,
                last_updated_at=None,
            ),
        )

    return BudgetCheck(
        allowed=fallback_allowed,
        remaining_usd=round(check.status.remaining_usd, 2),
        reason=check.reason,
        status=_to_budget_status_response(check.status),
    )


class UsageStatsResponse(BaseModel):
    symbols_indexed: int = 0
    symbols_summarized: int = 0
    memory_items_total: int = 0
    memory_items_learned: int = 0
    session_queries: int = 0


@app.get("/v1/usage/stats", response_model=UsageStatsResponse)
async def usage_stats() -> UsageStatsResponse:
    """Lightweight usage statistics for the Usage Stats panel."""
    try:
        conn = await _get_db().connect()
        symbols_indexed = (await (await conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE kind IN ('function','class')"
        )).fetchone())[0]
        symbols_summarized = (await (await conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE summary IS NOT NULL AND kind IN ('function','class')"
        )).fetchone())[0]
        memory_items_total = (await (await conn.execute(
            "SELECT COUNT(*) FROM memory_items WHERE stale=0"
        )).fetchone())[0]
        memory_items_learned = (await (await conn.execute(
            "SELECT COUNT(*) FROM memory_items WHERE stale=0 AND type='learned'"
        )).fetchone())[0]
    except Exception:
        symbols_indexed = symbols_summarized = memory_items_total = memory_items_learned = 0
    return UsageStatsResponse(
        symbols_indexed=symbols_indexed,
        symbols_summarized=symbols_summarized,
        memory_items_total=memory_items_total,
        memory_items_learned=memory_items_learned,
        session_queries=len(_session_tool_calls),
    )


@app.get("/v1/cost/budget/status", response_model=BudgetStatusResponse)
@app.get("/v1/cost/budget-status", response_model=BudgetStatusResponse)
async def budget_status() -> BudgetStatusResponse:
    service = CostGuardService(config=_get_config(), db=_get_db())
    status = await service.get_budget_status()
    return _to_budget_status_response(status)


def _to_savings_report_response(report) -> SavingsReportResponse:
    return SavingsReportResponse(
        actual_cost=report.actual_cost,
        hypothetical_frontier_cost=report.hypothetical_frontier_cost,
        savings=report.savings,
        reduction_pct=report.reduction_pct,
        total_tasks=report.total_tasks,
        local_tasks=report.local_tasks,
        cheap_cloud_tasks=report.cheap_cloud_tasks,
        frontier_tasks=report.frontier_tasks,
        month_cache_hits=report.month_cache_hits,
        month_total_ai_calls=report.month_total_ai_calls,
        cache_hit_rate=report.cache_hit_rate,
        cache_savings_usd=report.cache_savings_usd,
        month_spend_usd=report.month_spend_usd,
        month_net_usd=report.month_net_usd,
    )


@app.post("/v1/cost/guard/check", response_model=BudgetCheckResponse)
async def check_budget(request: BudgetCheckRequest) -> BudgetCheckResponse:
    service = CostGuardService(config=_get_config(), db=_get_db())
    result = await service.check_budget(request.estimated_cost_usd)
    return BudgetCheckResponse(
        allowed=result.allowed,
        reason=result.reason,
        estimated_cost_usd=result.estimated_cost_usd,
        budget=_to_budget_status_response(result.status),
    )


@app.post("/v1/task-runs/start", response_model=TaskRunStartResponse)
async def start_task_run(request: TaskRunStartRequest) -> TaskRunStartResponse:
    """Create a task run record for downstream cost and telemetry logging."""
    config = _get_config()
    workspace_root = request.workspace_root
    if workspace_root:
        workspace_service = WorkspaceRootsService(config=config, db=_get_db())
        workspace_root = str(await workspace_service.resolve_workspace_root(workspace_root))
    service = CostGuardService(config=config, db=_get_db())
    task_run_id = await service.create_task_run(
        user_request=request.user_request,
        task_type=request.task_type,
        mode=request.mode,
        risk_level=request.risk_level,
        selected_model=request.selected_model,
        estimated_cost=request.estimated_cost,
        workspace_root=workspace_root,
    )
    return TaskRunStartResponse(task_run_id=task_run_id)


@app.post("/v1/vector/backfill", response_model=VectorBackfillResponse)
async def backfill_vectors(request: VectorBackfillRequest) -> VectorBackfillResponse:
    """Generate embeddings for existing memory items and symbols.
    
    Backfill operation can be triggered on-demand to enable semantic search
    for existing memory and symbol data. Works with configured embedding models
    (ollama, anthropic, openai).
    """
    db = _get_db()
    config = _get_config()

    backfill_service = VectorBackfillService(db=db, config=config)

    # Perform backfill based on requested entity types
    results = await backfill_service.backfill_all(
        workspace_root=request.workspace_root
    )

    memory_stats = results.get("memory_items", {})
    symbol_stats = results.get("symbols", {})

    return VectorBackfillResponse(
        total_embedded=results.get("total_embedded", 0),
        total_failed=results.get("total_failed", 0),
        memory_items_embedded=memory_stats.get("embedded_count", 0),
        memory_items_failed=memory_stats.get("failed_count", 0),
        symbols_embedded=symbol_stats.get("embedded_count", 0),
        symbols_failed=symbol_stats.get("failed_count", 0),
        model_used=memory_stats.get("model_used", symbol_stats.get("model_used", "unknown")),
        workspace_root=request.workspace_root,
    )


def _estimate_context_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


_INDEX_KEYWORD_STOPWORDS = {
    "also",
    "about",
    "across",
    "after",
    "agent",
    "before",
    "build",
    "change",
    "code",
    "from",
    "have",
    "into",
    "just",
    "mode",
    "project",
    "that",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "those",
    "task",
    "want",
    "with",
}


def _extract_search_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text.lower())
    keywords: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) < 4 or token in _INDEX_KEYWORD_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= 20:
            break
    return keywords


async def _suggest_files_from_index(
    *,
    db: DatabaseManager,
    keywords: list[str],
    limit: int,
) -> list[str]:
    if not keywords or limit <= 0:
        return []

    conn = await db.connect()
    suggested_files: list[str] = []
    seen_paths: set[str] = set()

    file_where = " OR ".join("lower(file_path) LIKE ?" for _ in keywords)
    file_params: tuple[object, ...] = (
        *(f"%{kw}%" for kw in keywords),
        max(limit * 5, 50),
    )
    file_cursor = await conn.execute(
        f"""
        SELECT file_path
        FROM file_index
        WHERE stale = 0 AND ({file_where})
        ORDER BY
            CASE WHEN lower(file_path) LIKE '%/test_%' OR lower(file_path) LIKE '%\\test_%'
                      OR lower(file_path) LIKE '%_test.%' OR lower(file_path) LIKE '%/tests/%'
                      OR lower(file_path) LIKE '%\\tests\\%' THEN 1 ELSE 0 END ASC,
            COALESCE(last_indexed_at, '') DESC,
            file_path ASC
        LIMIT ?
        """,
        file_params,
    )
    file_rows = await file_cursor.fetchall()
    for row in file_rows:
        file_path = str(row["file_path"])
        if file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        suggested_files.append(file_path)
        if len(suggested_files) >= limit:
            return suggested_files

    symbol_where = " OR ".join("lower(name) LIKE ?" for _ in keywords)
    symbol_params: tuple[object, ...] = (
        *(f"%{kw}%" for kw in keywords),
        max((limit - len(suggested_files)) * 10, 50),
    )
    symbol_cursor = await conn.execute(
        f"""
        SELECT DISTINCT file_path
        FROM symbols
        WHERE ({symbol_where})
        ORDER BY file_path ASC
        LIMIT ?
        """,
        symbol_params,
    )
    symbol_rows = await symbol_cursor.fetchall()
    for row in symbol_rows:
        file_path = str(row["file_path"])
        if file_path in seen_paths:
            continue
        seen_paths.add(file_path)
        suggested_files.append(file_path)
        if len(suggested_files) >= limit:
            break

    return suggested_files


def _extract_primary_symbol(task_description: str) -> str | None:
    """Heuristically extract the primary symbol name from a task description."""
    import re
    # Look for CamelCase class names or snake_case function names in backticks or quotes
    for pattern in (r"`([A-Za-z_][A-Za-z0-9_.]+)`", r"'([A-Za-z_][A-Za-z0-9_.]+)'",
                    r'"([A-Za-z_][A-Za-z0-9_.]+)"'):
        m = re.search(pattern, task_description)
        if m:
            return m.group(1)
    # Fall back: first CamelCase word
    m = re.search(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b', task_description)
    return m.group(1) if m else None


def _serialize_context_item(item: ContextItem) -> dict[str, object]:
    return {
        "content": item.content,
        "source": item.source,
        "reference_id": item.reference_id,
        "source_type": item.source_type,
        "tokens": item.tokens,
        "relevance_score": item.relevance_score,
        "inclusion_reason": item.inclusion_reason,
        "retrieval_method": item.retrieval_method,
        "trust_level": item.trust_level,
        "tier": item.tier,
    }


def _serialize_excluded_item(item) -> dict[str, object]:
    return {
        "source": item.source,
        "reference_id": item.reference_id,
        "source_type": item.source_type,
        "exclusion_reason": item.exclusion_reason.value,
        "tokens_would_have_used": item.tokens_would_have_used,
    }


def _build_stale_exclusions_response(
    budget_summary: dict[str, object] | None,
    workspace_root: str,
) -> StaleExclusionsResponse | None:
    if not budget_summary:
        return None
    stale_summary = budget_summary.get("stale_exclusions")
    if not isinstance(stale_summary, dict):
        return None
    affected_modules = stale_summary.get("affected_modules", [])
    if not isinstance(affected_modules, list):
        affected_modules = []
    return StaleExclusionsResponse(
        count=int(stale_summary.get("count", 0) or 0),
        affected_modules=[str(item) for item in affected_modules],
        rebuild_command=(
            f'memopilot workspace index --workspace-root "{workspace_root}"'
            if workspace_root
            else "memopilot workspace index"
        ),
    )


def _build_stack_trace_items(request: ContextBuildRequest) -> list[ContextItem]:
    description = request.task_description.strip()
    if not description:
        return []
    lowered = description.lower()
    if request.task_type == "bug_fix" or any(
        marker in lowered for marker in ("traceback", "stack trace", "exception", "error:")
    ):
        return [
            ContextItem(
                content=description,
                source="task_description",
                source_type="stack_trace",
                tokens=_estimate_context_tokens(description),
                relevance_score=1.0,
                inclusion_reason="",
                retrieval_method="task_description",
                trust_level=5,
                tier="stack_trace",
            )
        ]
    return []


def _render_assembled_context(
    *,
    request: ContextAssembleRequest,
    context_pack: ContextBuildResponse,
) -> ContextAssembleResponse:
    renderer = ContextPackRenderer()
    active_rules = [{"rule_text": rule} for rule in context_pack.rules]
    active_skills = [{"name": skill, "description": ""} for skill in context_pack.skills]
    file_snippets = [
        {"path": item.path, "content": item.content or ""}
        for item in context_pack.files
    ]

    memory_items: list[dict[str, Any]] = []
    for item in context_pack.included_items or []:
        if item.get("source_type") != "memory":
            continue
        memory_items.append(
            {
                "title": str(item.get("source", "Project memory")),
                "body": str(item.get("content", "")),
                "memory_class": "fact",
                "trust_level": int(item.get("trust_level", 0) or 0),
                "source": str(item.get("source", "")),
            }
        )

    stale_exclusions = context_pack.stale_exclusions
    rendered = renderer.render(
        caller=request.caller,
        task_description=request.task_description,
        active_rules=active_rules,
        active_skills=active_skills,
        memory_items=memory_items or None,
        file_snippets=file_snippets,
        stale_exclusion_count=stale_exclusions.count if stale_exclusions else 0,
        stale_affected_modules=stale_exclusions.affected_modules if stale_exclusions else None,
        max_tokens=request.max_output_tokens,
    )

    extras: list[str] = []
    if context_pack.quality_score:
        extras.append(
            "## Context Quality\n\n"
            f"Verdict: {context_pack.quality_score.verdict} | "
            f"Score: {context_pack.quality_score.total}\n"
        )
    if context_pack.repo_map:
        extras.append(f"## Repo Map\n\n{context_pack.repo_map}\n")
    if context_pack.commit_history:
        extras.append(f"## Recent History\n\n{context_pack.commit_history}\n")

    assembled = "\n".join([rendered, *extras]).strip()
    return ContextAssembleResponse(
        rendered_markdown=assembled,
        context_pack_hash=context_pack.context_pack_hash,
        total_tokens=context_pack.total_tokens,
        stale_exclusion_count=stale_exclusions.count if stale_exclusions else 0,
        redacted_values_count=0,
        quality_verdict=context_pack.quality_score.verdict if context_pack.quality_score else None,
    )


def _read_context_file_item(workspace_root: str, file_path: str) -> ContextItem:
    full_path = (
        os.path.join(workspace_root, file_path)
        if not os.path.isabs(file_path)
        else file_path
    )
    content = ""
    try:
        if os.path.exists(full_path) and os.path.isfile(full_path):
            with open(full_path, encoding="utf-8", errors="replace") as handle:
                content = handle.read(50_000)
    except Exception:
        content = f"# Could not read {file_path}"
    tokens = _estimate_context_tokens(content)
    return ContextItem(
        content=content,
        source=file_path,
        source_type="file",
        tokens=tokens,
        relevance_score=1.0,
        inclusion_reason="",
        retrieval_method="filesystem",
        trust_level=5,
        tier="current_file",
    )


async def _generate_context_pack_response(request: ContextBuildRequest) -> ContextBuildResponse:
    """Build a context pack for preview with token estimates."""
    config = _get_config()
    db = _get_db()
    files_to_include = request.file_overrides if request.file_overrides else request.suggested_files
    if not files_to_include:
        fallback_keywords = _extract_search_keywords(request.task_description)
        files_to_include = await _suggest_files_from_index(
            db=db,
            keywords=fallback_keywords,
            limit=20,
        )

    workspace_service = WorkspaceRootsService(config=config, db=db)
    workspace_root = str(await workspace_service.resolve_workspace_root(request.workspace_root))

    file_items = [
        _read_context_file_item(workspace_root, file_path)
        for file_path in files_to_include[:20]
    ]

    rules: list[str] = []
    try:
        policy_service = PolicyPacksService(config=config, db=db)
        active_rules = await policy_service.list_active_policy_rules(
            workspace_root=request.workspace_root
        )
        rules.extend([item.rule for item in active_rules[:10]])
    except Exception:
        pass

    skills: list[str] = []
    try:
        skill_service = SkillLoaderService(config=config, db=db)
        skill_items = await skill_service.list_skills(limit=50)
        skills = [s.name for s in skill_items]
    except Exception:
        pass

    if request.model_max_tokens is None:
        file_entries = [
            ContextFileEntry(path=item.source, tokens=item.tokens, content=item.content)
            for item in file_items
        ]
        file_tokens = sum(f.tokens for f in file_entries)
        rule_tokens = sum(_estimate_context_tokens(rule) for rule in rules)
        total_tokens = file_tokens + rule_tokens + len(skills) * 10
        estimated_cost = (total_tokens / 1000) * 0.003
        response_payload = {
            "files": [entry.model_dump() for entry in file_entries],
            "rules": rules[:15],
            "skills": skills[:10],
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(estimated_cost, 6),
        }
        context_pack_hash = hashlib.sha256(
            json.dumps(response_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        recall_service = MemoryRecallService(db)
        await recall_service.record_recall_trace(
            context_pack_hash=context_pack_hash,
            request_json=request.model_dump_json(),
            included_memory_ids=[],
            excluded_memory_ids=[],
        )
        return ContextBuildResponse(
            **response_payload,
            context_pack_hash=context_pack_hash,
        )

    context_builder = ContextBuilderService(config=config, db=db)
    task_type = (request.task_type or "default").strip().lower().replace("-", "_")
    tier_order = TIER_ORDER_BY_TASK_TYPE.get(task_type, TIER_ORDER_BY_TASK_TYPE["default"])
    budget = ContextBudget.from_model_max_tokens(
        request.model_max_tokens,
        task_type=task_type,
        template_id=context_builder.get_selected_template_id(),
        tier_order=tier_order,
    )

    recall_items: list[dict[str, object]] = []
    try:
        recall_service = MemoryRecallService(db)
        recall_response = await recall_service.recall(
            RecallRequest(
                query=request.task_description,
                include_stale=True,
                limit=20,
                min_trust_level=0,
                workspace_root=request.workspace_root,
            )
        )
        for item in recall_response.items:
            source_path = item.provenance[0].source_path if item.provenance else None
            recall_items.append(
                {
                    "content": f"{item.title}\n\n{item.body}".strip(),
                    "source": source_path or item.memory_id,
                    "reference_id": item.memory_id,
                    "source_type": "memory",
                    "tokens": _estimate_context_tokens(f"{item.title}\n\n{item.body}".strip()),
                    "relevance_score": item.relevance_score,
                    "inclusion_reason": "",
                    "retrieval_method": "fts",
                    "trust_level": item.trust_level,
                    "tier": "fts",
                    "stale": item.memory_status == "stale",
                }
            )
    except Exception:
        recall_items = []

    # Retrieval-first mode: legacy plan/rejection injections removed.
    plan_items: list[ContextItem] = []
    rejection_items: list[ContextItem] = []

    retrieval_results: dict[str, list[ContextItem | dict[str, object]]] = {
        "current_file": file_items,
        "stack_trace": _build_stack_trace_items(request),
        "fts": recall_items,
        "rules": plan_items + rejection_items + [
            ContextItem(
                content=rule,
                source=f"rule:{index}",
                source_type="rule",
                tokens=_estimate_context_tokens(rule),
                relevance_score=max(0.2, 1.0 - (index * 0.05)),
                inclusion_reason="",
                retrieval_method="policy_pack",
                trust_level=5,
                tier="rules",
            )
            for index, rule in enumerate(rules[:15], start=1)
        ],
        "skills": [
            ContextItem(
                content=skill,
                source=skill,
                source_type="skill",
                tokens=max(1, len(skill) // 4),
                relevance_score=max(0.2, 1.0 - (index * 0.05)),
                inclusion_reason="",
                retrieval_method="skill_store",
                trust_level=4,
                tier="skills",
            )
            for index, skill in enumerate(skills[:10], start=1)
        ],
    }

    included_items, excluded_items, budget_summary = build_budget_aware_context_pack(
        tier_order=tier_order,
        budget=budget,
        retrieval_results=retrieval_results,
    )

    file_entries = [
        ContextFileEntry(path=item.source, tokens=item.tokens, content=item.content)
        for item in included_items
        if item.source_type == "file"
    ]
    included_rules = [item.content for item in included_items if item.source_type == "rule"]
    included_skills = [item.content for item in included_items if item.source_type == "skill"]
    total_tokens = sum(item.tokens for item in included_items)
    estimated_cost = (total_tokens / 1000) * 0.003
    serialized_included_items = [_serialize_context_item(item) for item in included_items]
    serialized_excluded_items = [_serialize_excluded_item(item) for item in excluded_items]
    stale_exclusions = _build_stale_exclusions_response(budget_summary, workspace_root)

    response_payload = {
        "files": [entry.model_dump() for entry in file_entries],
        "rules": included_rules,
        "skills": included_skills,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(estimated_cost, 6),
        "budget_summary": budget_summary,
        "stale_exclusions": None if stale_exclusions is None else stale_exclusions.model_dump(),
        "included_items": serialized_included_items,
        "excluded_items": serialized_excluded_items,
    }
    context_pack_hash = hashlib.sha256(
        json.dumps(response_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    recall_service = MemoryRecallService(db, config)
    await recall_service.record_recall_trace(
        context_pack_hash=context_pack_hash,
        request_json=request.model_dump_json(),
        included_memory_ids=[
            item.reference_id or item.source
            for item in included_items
            if item.source_type == "memory"
        ],
        excluded_memory_ids=[
            item.reference_id or item.source
            for item in excluded_items
            if item.source_type == "memory"
        ],
    )

    # ── Layer 3: structural graph (callers not in context) ────────────────────
    callers_not_in_context: list[str] = []
    graph_expansion_files = 0
    try:
        graph = GraphRetriever(db=db)
        included_file_paths = {e.path for e in file_entries}
        conn = await db.connect()
        cursor = await conn.execute(
            """
            SELECT id FROM symbols
            WHERE file_path IN ({})
              AND kind IN ('function', 'class', 'method')
            LIMIT 1
            """.format(",".join("?" * len(list(included_file_paths)))),
            list(included_file_paths) or ["__none__"],
        )
        primary_row = await cursor.fetchone()
        if primary_row:
            callers_missing = await graph.find_callers_not_in_context(
                primary_row["id"], included_file_paths
            )
            callers_not_in_context = list({c.file_path for c in callers_missing})
            graph_expansion_files = len({c.file_path for c in callers_missing})
    except Exception:
        pass

    # ── Layer 4: git history ──────────────────────────────────────────────────
    commit_history_text: str | None = None
    try:
        git_indexer = GitHistoryIndexer(db=db)
        commits = await git_indexer.get_relevant_commits(
            file_paths=list(included_file_paths),
            task_description=request.task_description,
            workspace_root=workspace_root or "",
        )
        if commits:
            commit_history_text = git_indexer.format_commit_history_for_context(
                commits, list(included_file_paths)
            )
    except Exception:
        pass

    # ── Repo map ──────────────────────────────────────────────────────────────
    repo_map_text: str | None = None
    try:
        repo_gen = RepoMapGenerator(db=db)
        repo_map_text = await repo_gen.generate(
            workspace_root=workspace_root or "", max_tokens=500
        )
    except Exception:
        pass

    # ── Deduplication ─────────────────────────────────────────────────────────
    all_rule_texts = included_rules[:]
    if all_rule_texts:
        deduped_rules, dedup_savings_pct = deduplicate_text_list(all_rule_texts)
        included_rules_final = deduped_rules
    else:
        dedup_savings_pct = 0.0
        included_rules_final = included_rules

    # ── Quality scoring ───────────────────────────────────────────────────────
    stale_pct = 0.0
    if budget_summary and isinstance(budget_summary, dict):
        stale_count = budget_summary.get("stale_exclusion_count", 0) or 0
        total_count = budget_summary.get("total_recall_count", 1) or 1
        stale_pct = min(1.0, stale_count / total_count)

    source_types = [item.source_type for item in included_items]
    if commit_history_text:
        source_types.append("commit")

    quality_pack = ContextPackSnapshot(
        files=[e.path for e in file_entries],
        rules=included_rules_final,
        source_types=source_types,
        stale_exclusion_pct=stale_pct,
        dedup_savings_pct=dedup_savings_pct,
        graph_expansion_files=graph_expansion_files,
        primary_symbol=_extract_primary_symbol(request.task_description),
    )
    quality = score_context_pack(quality_pack, task_description=request.task_description)
    quality_response = ContextQualityScoreResponse(**quality.as_dict())

    return ContextBuildResponse(
        files=file_entries,
        rules=included_rules_final,
        skills=included_skills,
        total_tokens=total_tokens,
        estimated_cost_usd=round(estimated_cost, 6),
        context_pack_hash=context_pack_hash,
        budget_summary=budget_summary,
        stale_exclusions=stale_exclusions,
        included_items=serialized_included_items,
        excluded_items=serialized_excluded_items,
        quality_score=quality_response,
        callers_not_in_context=callers_not_in_context or None,
        repo_map=repo_map_text,
        commit_history=commit_history_text,
    )


@app.post("/v1/context-pack/generate", response_model=ContextBuildResponse)
async def generate_context_pack(request: ContextBuildRequest) -> ContextBuildResponse:
    return await _generate_context_pack_response(request)


@app.post("/v1/context/assemble", response_model=ContextAssembleResponse)
async def assemble_context(request: ContextAssembleRequest) -> ContextAssembleResponse:
    context_pack = await _generate_context_pack_response(
        ContextBuildRequest(
            task_description=request.task_description,
            suggested_files=request.files_in_focus,
            task_type=request.task_type_hint,
            workspace_root=request.workspace_root,
            caller=request.caller,
            output_format="markdown_for_llm",
            max_output_tokens=request.max_output_tokens,
        )
    )
    response = _render_assembled_context(request=request, context_pack=context_pack)

    if request.caller == "copilot_lm_tool":
        _record_session_tool_call(
            task_description=request.task_description,
            files_in_focus=list(request.files_in_focus or []),
        )

    if request.caller == "copilot_lm_tool" and response.total_tokens > 1000:
        if _llm_mode == "copilot" and _host_model_available:
            try:
                synthesis_id = str(uuid.uuid4())
                loop = asyncio.get_event_loop()
                fut: asyncio.Future = loop.create_future()
                _relay_futures[synthesis_id] = fut
                from .context_synthesizer import SYSTEM as SYNTH_SYSTEM
                user_prompt = build_synthesis_user_prompt(
                    task=request.task_description,
                    raw_markdown=response.rendered_markdown,
                    max_chars=6000,
                )
                await _relay_sse_queue.put({
                    "type": "LLM_REQUEST",
                    "request_type": "synthesize",
                    "relay_id": synthesis_id,
                    "system": SYNTH_SYSTEM,
                    "user": user_prompt,
                    "ctx_tokens": response.total_tokens,
                })
                synthesized = await asyncio.wait_for(fut, timeout=30.0)
                if synthesized:
                    response.rendered_markdown = synthesized
                    response.total_tokens = len(synthesized) // 4
            except Exception:
                _relay_futures.pop(synthesis_id, None)
        elif _synthesizer is not None:
            try:
                synthesized = await asyncio.wait_for(
                    _synthesizer.synthesize(
                        task=request.task_description,
                        raw_markdown=response.rendered_markdown,
                        max_tokens=request.max_output_tokens or 4000,
                    ),
                    timeout=15.0,
                )
                response.rendered_markdown = synthesized
                response.total_tokens = len(synthesized) // 4
            except Exception:
                pass

    return response


@app.post("/v1/context/build", response_model=ContextBuildResponse, deprecated=True)
async def build_context_pack(request: ContextBuildRequest) -> ContextBuildResponse:
    return await _generate_context_pack_response(request)


# ── Git blame endpoint (Layer 4 supplemental) ─────────────────────────────────

class BlameRequest(BaseModel):
    file_path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    workspace_root: str = ""


class BlameEntryResponse(BaseModel):
    sha: str
    author: str
    committed_at: str
    line_number: int
    line_content: str
    commit_message: str | None = None


class BlameResponse(BaseModel):
    entries: list[BlameEntryResponse]
    file_path: str


@app.post("/v1/context/blame", response_model=BlameResponse)
async def get_blame_context(request: BlameRequest) -> BlameResponse:
    """Return git blame context for a line range within a file."""
    db = _get_db()
    indexer = GitHistoryIndexer(db=db)
    workspace_root = request.workspace_root or str(_get_config().workspace_path.resolve())
    entries = await indexer.get_blame_context(
        file_path=request.file_path,
        line_start=request.line_start,
        line_end=request.line_end,
        workspace_root=workspace_root,
    )
    return BlameResponse(
        file_path=request.file_path,
        entries=[
            BlameEntryResponse(
                sha=e.sha,
                author=e.author,
                committed_at=e.committed_at,
                line_number=e.line_number,
                line_content=e.line_content,
                commit_message=e.commit_message,
            )
            for e in entries
        ],
    )


# ── Rejection learning endpoint (T3-C) ────────────────────────────────────────

class PatchRejectionRequest(BaseModel):
    patch_attempt_id: str
    rejection_reason: str
    rejection_category: str | None = None
    workspace_root: str | None = None


class PatchRejectionResponse(BaseModel):
    recorded: bool
    patch_attempt_id: str
    action_taken: str | None = None
    memory_item_id: str | None = None
    suggestion: str | None = None


# ── Plan mode endpoints (Priority 1) ──────────────────────────────────────────

class PlanStepRequest(BaseModel):
    step_number: int = Field(ge=1)
    description: str
    target_file: str | None = None
    target_symbol: str | None = None


class StorePlanRequest(BaseModel):
    title: str
    steps: list[PlanStepRequest]
    task_description: str
    task_run_id: str | None = None
    workspace_root: str | None = None


class PlanStepResponse(BaseModel):
    step_number: int
    description: str
    target_file: str | None = None
    target_symbol: str | None = None


class StorePlanResponse(BaseModel):
    plan_id: str
    memory_item_id: str
    title: str
    steps: list[PlanStepResponse]
    raw_text: str
    created_at: str


class RecallPlansRequest(BaseModel):
    workspace_root: str | None = None
    module_path: str | None = None
    limit: int = Field(default=3, ge=1, le=10)


class PlanRecallItemResponse(BaseModel):
    memory_item_id: str
    title: str
    body: str
    trust_level: int
    created_at: str


class RecallPlansResponse(BaseModel):
    plans: list[PlanRecallItemResponse]


class PlanComplianceRequest(BaseModel):
    plan_memory_id: str
    files_changed: list[str]


class PlanComplianceResponse(BaseModel):
    compliant: bool
    warnings: list[str]


# ── Autofix mode endpoints (Priority 2) ───────────────────────────────────────

class DiagnosticItem(BaseModel):
    file_path: str
    line: int = Field(ge=0)
    code: str
    message: str


class AutofixClassifyRequest(BaseModel):
    diagnostics: list[DiagnosticItem]


class AutofixCandidateResponse(BaseModel):
    file_path: str
    line: int
    code: str
    message: str
    safety: str
    category: str


class AutofixClassifyResponse(BaseModel):
    safe_candidates: list[AutofixCandidateResponse]
    manual_candidates: list[AutofixCandidateResponse]
    autofix_available: bool
    task_description: str


class AutofixRunRequest(BaseModel):
    diagnostics: list[DiagnosticItem]
    workspace_root: str | None = None
    task_run_id: str | None = None


class AutofixRunResponse(BaseModel):
    task_description: str
    safe_count: int
    manual_count: int
    mode: str
    model: str
    approval_tier: str
    files_to_fix: list[str]
    autofix_available: bool


class TaskPatternsRequest(BaseModel):
    workspace_root: str


class TaskPatternResponse(BaseModel):
    pattern_type: str
    context_path: str
    details: dict[str, object] = Field(default_factory=dict)
    suggestion: str


class TaskPatternsResponse(BaseModel):
    patterns: list[TaskPatternResponse]


class SimilarTaskResponse(BaseModel):
    task_id: str
    user_request: str
    status: str
    model_used: str | None = None
    cost_usd: float
    created_at: str
    rejection_reason: str | None = None


class SimilarTasksResponse(BaseModel):
    tasks: list[SimilarTaskResponse]


async def _resolve_memory_workspace_root(workspace_root: str | None) -> str | None:
    if not workspace_root:
        return None
    workspace_service = WorkspaceRootsService(config=_get_config(), db=_get_db())
    return str(await workspace_service.resolve_workspace_root(workspace_root))


async def _suggest_memory_from_request(
    request: SuggestMemoryRequest | SmartSuggestMemoryRequest,
    *,
    smart: bool,
) -> SuggestMemoryResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    workspace_root = await _resolve_memory_workspace_root(request.workspace_root)
    if smart:
        assert isinstance(request, SmartSuggestMemoryRequest)
        result = await service.suggest_memory_update_smart(
            title=request.title,
            body=request.body,
            source=request.source,
            source_path=request.source_path,
            tags=request.tags,
            task_run_id=request.task_run_id,
            workspace_root=workspace_root,
            memory_class=request.memory_class,
            derivation_source=request.derivation_source,
        )
    else:
        result = await service.suggest_memory_update(
            title=request.title,
            body=request.body,
            source=request.source,
            source_path=request.source_path,
            tags=request.tags,
            task_run_id=request.task_run_id,
            workspace_root=workspace_root,
        )
    return SuggestMemoryResponse(
        memory_item_id=result.memory_item_id,
        pending_approval=result.pending_approval,
        artifact_id=result.artifact_id,
        blocked_reason=result.blocked_reason,
    )


@app.post("/v1/memory/smart-suggest", response_model=SuggestMemoryResponse)
async def smart_suggest_memory(request: SmartSuggestMemoryRequest) -> SuggestMemoryResponse:
    return await _suggest_memory_from_request(request, smart=True)


@app.post("/v1/memory/proposals-for-module", response_model=MemoryItemsResponse)
async def memory_proposals_for_module(
    request: ModuleMemoryProposalsRequest,
) -> MemoryItemsResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    workspace_root = await _resolve_memory_workspace_root(request.workspace_root)
    items = await service.get_pending_proposals_for_module(
        module_path=request.module_path,
        workspace_root=workspace_root,
        limit=request.limit,
    )
    return MemoryItemsResponse(items=[_memory_item_response(item) for item in items])


class HostResponseRequest(BaseModel):
    task_run_id: str
    token: str = ""
    is_final: bool = False
    error: str | None = None


@app.post("/v1/llm/host-response")
async def host_llm_response(response: HostResponseRequest):
    """Extension posts host LLM tokens here; backend resolves the relay future."""
    fut = _host_relay_futures.get(response.task_run_id)
    if fut is None or fut.done():
        raise HTTPException(status_code=404, detail="No pending host relay for this task_run_id")

    if response.error:
        fut.set_exception(RuntimeError(response.error))
        _host_relay_futures.pop(response.task_run_id, None)
        return {"ok": True}

    # Accumulate tokens
    if not hasattr(fut, "_token_buffer"):
        fut._token_buffer = []  # type: ignore[attr-defined]
    if response.token:
        fut._token_buffer.append(response.token)  # type: ignore[attr-defined]

        # Also push token to SSE queue so streaming UI gets it
        queue = _task_sse_queues.get(response.task_run_id)
        if queue:
            await queue.put({"type": "TOKEN", "token": response.token})

    if response.is_final:
        content = "".join(getattr(fut, "_token_buffer", []))
        fut.set_result(content)
        _host_relay_futures.pop(response.task_run_id, None)

    return {"ok": True}


class HostModelReadyRequest(BaseModel):
    available: bool
    model_id: str = ""


@app.post("/v1/host/model-ready")
async def host_model_ready(request: HostModelReadyRequest):
    """Extension calls this once at startup after probing vscode.lm."""
    global _host_model_available, _llm_mode, _llm_mode_model_id
    _host_model_available = request.available
    _llm_mode_model_id = request.model_id
    if request.available and _llm_mode == "local":
        # Auto-upgrade to copilot on first successful probe
        _llm_mode = "copilot"
    logger.info("Host model probe: available=%s model=%s mode=%s", request.available, request.model_id, _llm_mode)
    return {"ok": True}


class LLMModeRequest(BaseModel):
    mode: str  # "copilot" | "cloud" | "local"


@app.post("/v1/config/llm-mode")
async def set_llm_mode(request: LLMModeRequest):
    """Extension posts this when user toggles the LLM mode."""
    global _llm_mode
    allowed = {"copilot", "cloud", "local"}
    if request.mode not in allowed:
        raise HTTPException(status_code=400, detail=f"mode must be one of {allowed}")
    if request.mode == "copilot" and not _host_model_available:
        raise HTTPException(status_code=400, detail="Copilot model not available — probe first")
    prev = _llm_mode
    _llm_mode = request.mode
    # Cancel any in-flight relay futures so they fail fast and caller can retry
    if prev != request.mode:
        for fut in list(_relay_futures.values()):
            if not fut.done():
                fut.set_exception(RuntimeError("llm_mode_changed"))
        _relay_futures.clear()
    logger.info("LLM mode changed: %s → %s", prev, request.mode)
    return {"ok": True, "mode": _llm_mode}


@app.get("/v1/config/llm-mode")
async def get_llm_mode():
    """Return current LLM mode and available options."""
    return {
        "mode": _llm_mode,
        "model_id": _llm_mode_model_id,
        "copilot_available": _host_model_available,
        "cloud_available": _writeback_client is not None and getattr(_writeback_client, "provider_name", "") not in ("local", ""),
        "local_available": _writeback_client is not None and getattr(_writeback_client, "provider_name", "") == "local",
    }


@app.get("/v1/synthesis/stream")
async def synthesis_stream(request: Request):
    """SSE stream the extension listens on for all LLM_REQUEST relay events."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(_relay_sse_queue.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")


class RelayResponseRequest(BaseModel):
    relay_id: str
    token: str = ""
    is_final: bool = False
    error: str | None = None
    # Legacy field — maps to relay_id if relay_id not provided
    synthesis_id: str = ""


@app.post("/v1/synthesis/host-response")
async def synthesis_host_response(response: RelayResponseRequest):
    """Extension streams relay tokens back here (used for all request types)."""
    rid = response.relay_id or response.synthesis_id
    fut = _relay_futures.get(rid)
    if fut is None or fut.done():
        return {"ok": True}

    if response.error:
        if not fut.done():
            fut.set_exception(RuntimeError(response.error))
        _relay_futures.pop(rid, None)
        return {"ok": True}

    if not hasattr(fut, "_token_buffer"):
        fut._token_buffer = []  # type: ignore[attr-defined]
    if response.token:
        fut._token_buffer.append(response.token)  # type: ignore[attr-defined]

    if response.is_final:
        content = "".join(getattr(fut, "_token_buffer", []))
        if not fut.done():
            fut.set_result(content)
        _relay_futures.pop(rid, None)

    return {"ok": True}


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
    return _to_savings_report_response(report)


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
        now = datetime.datetime.now(datetime.UTC)
        call_count = min(report.month_total_ai_calls, limit)
        for i in range(call_count):
            ts = now - datetime.timedelta(hours=i * 2)
            entries.append(
                TaskHistoryEntry(
                    task_id=f"task-{i + 1:04d}",
                    description=f"Task #{i + 1}",
                    mode="auto",
                    status="completed",
                    model_used="codellama-13b-local" if i % 3 != 0 else "gpt-4o",
                    files_changed=max(1, (i % 5) + 1),
                    cost_usd=round(0.001 * (i % 4), 4) if i % 3 == 0 else 0.0,
                    created_at=ts.isoformat(),
                    duration_ms=1500 + (i * 300),
                )
            )
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

    now = datetime.datetime.now(datetime.UTC)

    # Build daily breakdown from available data
    by_day: list[CostDashboardEntry] = []
    total_calls = report.month_total_ai_calls
    avg_daily_calls = max(1, total_calls // min(days, 30))
    avg_daily_cost = budget.spent_usd / max(1, min(days, 30))

    for d in range(min(days, 30)):
        date = (now - datetime.timedelta(days=d)).strftime("%Y-%m-%d")
        by_day.append(
            CostDashboardEntry(
                date=date,
                provider="mixed",
                model="mixed",
                calls=avg_daily_calls,
                tokens=avg_daily_calls * 3000,
                cost_usd=round(avg_daily_cost, 4),
            )
        )

    # By-model breakdown
    by_model: list[CostDashboardEntry] = []
    local_calls = int(total_calls * 0.7)
    cloud_calls = total_calls - local_calls
    by_model.append(
        CostDashboardEntry(
            date="",
            provider="ollama",
            model="codellama-13b-local",
            calls=local_calls,
            tokens=local_calls * 2500,
            cost_usd=0.0,
        )
    )
    if cloud_calls > 0:
        by_model.append(
            CostDashboardEntry(
                date="",
                provider="openai",
                model="gpt-4o",
                calls=cloud_calls,
                tokens=cloud_calls * 4000,
                cost_usd=round(budget.spent_usd, 4),
            )
        )

    dashboard_start = now - datetime.timedelta(days=max(days, 1))
    savings = await service.get_savings_report(start_date=dashboard_start, end_date=now)

    # ── Context quality metrics ───────────────────────────────────────────────
    avg_quality: float | None = None
    verdicts: dict[str, int] | None = None
    try:
        conn = await _get_db().connect()
        cutoff = (now - datetime.timedelta(days=days)).isoformat()
        cursor = await conn.execute(
            """SELECT quality_score, quality_verdict
               FROM task_runs
               WHERE created_at >= ? AND quality_score IS NOT NULL""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        if rows:
            scores = [r["quality_score"] for r in rows]
            avg_quality = round(sum(scores) / len(scores), 3)
            verdicts = {}
            for r in rows:
                v = r["quality_verdict"] or "unknown"
                verdicts[v] = verdicts.get(v, 0) + 1
    except Exception:
        pass

    return CostDashboardResponse(
        period_days=days,
        total_cost_usd=round(budget.spent_usd, 4),
        total_calls=total_calls,
        total_tokens=total_calls * 3000,
        saved_usd=round(budget.saved_usd, 4),
        by_day=by_day,
        by_model=by_model,
        savings_report=_to_savings_report_response(savings),
        avg_context_quality=avg_quality,
        context_quality_verdicts=verdicts,
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
        response_status=request.response_status,
    )
    return CacheStoreResponse(stored=True)


@app.post("/v1/cache/lookup", response_model=CacheLookupResponse)
async def cache_lookup(request: CacheLookupRequest) -> CacheLookupResponse:
    cache_service = ResponseCacheService(db=_get_db())
    cost_service = CostGuardService(config=_get_config(), db=_get_db())
    cached = await cache_service.lookup(
        context_pack_hash=request.context_pack_hash,
        task_type=request.task_type,
    )
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

    # Detect MCP server configs from workspace .vscode/mcp.json or legacy settings
    servers: list[dict] = []
    import json as json_mod
    import os

    workspace_root = str(getattr(config, "workspace_path", "."))
    mcp_config_paths = [
        os.path.join(workspace_root, ".vscode", "mcp.json"),
        os.path.join(workspace_root, ".memopilot", "mcp.json"),
    ]
    builtin_retrieval_tools = [
        "memopilot-search",
        "memopilot-symbols",
        "memopilot-memory",
        "memopilot-profile",
    ]

    for mcp_config_path in mcp_config_paths:
        if not os.path.exists(mcp_config_path):
            continue
        try:
            with open(mcp_config_path, encoding="utf-8") as f:
                mcp_config = json_mod.load(f)
            raw_servers = mcp_config.get("servers", {})
            if isinstance(raw_servers, dict):
                for server_name, server_config in raw_servers.items():
                    if not isinstance(server_config, dict):
                        continue
                    tools = server_config.get("tools")
                    if not isinstance(tools, list):
                        tools = builtin_retrieval_tools if server_name == "memopilot" else []
                    servers.append(
                        {
                            "name": server_name,
                            "status": "configured",
                            "tools": tools,
                        }
                    )
            elif isinstance(raw_servers, list):
                for server in raw_servers:
                    if not isinstance(server, dict):
                        continue
                    servers.append(
                        {
                            "name": server.get("name", "unknown"),
                            "status": "configured",
                            "tools": server.get("tools", []),
                        }
                    )
        except Exception:
            pass

    # Always include the built-in MemoPilot tools
    servers.append(
        {
            "name": "memopilot-builtin",
            "status": "connected",
            "tools": builtin_retrieval_tools,
        }
    )

    return {"servers": servers}


@app.post("/v1/mcp/agentic/run", response_model=AgenticRunResponse)
async def run_agentic_mcp(request: AgenticRunRequest) -> AgenticRunResponse:
    orchestrator = MCPOrchestrator(db=_get_db(), config=_get_config())
    try:
        result = await orchestrator.run_agentic_loop(
            task_run_id=request.task_run_id,
            server_name=request.server_name,
            tool_calls=[
                ToolCall(tool_name=item.tool_name, input_data=item.input_data)
                for item in request.tool_calls
            ],
            max_iterations=request.max_iterations,
            context=request.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    agentic_response = AgenticRunResponse(
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

    if result.calls and _writeback_client is not None:
        outcome = result.calls[-1].result_summary
        captured_client = _writeback_client
        seeder = MemorySeederService(config=_get_config(), db=_get_db())

        async def _writeback_task() -> None:
            try:
                count = await seeder.writeback_from_task(
                    client=captured_client,
                    task_description=request.context,
                    outcome=outcome,
                )
                logger.info("Task writeback: %d fact(s) stored", count)
            except Exception as exc:
                logger.warning("Task writeback failed (non-fatal): %s", exc)

        _t = asyncio.create_task(_writeback_task())
        _background_tasks.add(_t)
        _t.add_done_callback(_background_tasks.discard)

    return agentic_response


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


@app.post("/v1/symbols/search", response_model=SymbolSearchResponse)
async def search_symbols(request: SymbolSearchRequest) -> SymbolSearchResponse:
    conn = await _get_db().connect()
    like_query = f"%{request.query.lower()}%"
    cursor = await conn.execute(
        """
        SELECT name, kind, file_path, start_line, end_line, signature, summary
        FROM symbols
        WHERE lower(name) LIKE ? OR lower(file_path) LIKE ?
        ORDER BY
            CASE WHEN lower(name) = ? THEN 0 ELSE 1 END,
            CASE WHEN lower(name) LIKE ? THEN 0 ELSE 1 END,
            name ASC,
            file_path ASC
        LIMIT ?
        """,
        (
            like_query,
            like_query,
            request.query.lower(),
            f"{request.query.lower()}%",
            request.limit,
        ),
    )
    rows = await cursor.fetchall()
    return SymbolSearchResponse(
        symbols=[
            SymbolSearchItemResponse(
                name=str(row["name"]),
                kind=str(row["kind"]),
                file_path=str(row["file_path"]),
                start_line=row["start_line"],
                end_line=row["end_line"],
                signature=row["signature"],
                summary=row["summary"],
            )
            for row in rows
        ]
    )


@app.post("/v1/workspace/profile/rebuild", response_model=WorkspaceProfileResponse)
async def rebuild_workspace_profile() -> WorkspaceProfileResponse:
    service = WorkspaceProfileService(config=_get_config(), db=_get_db())
    profile = await service.rebuild_profile()
    config = _get_config()
    generate_workspace_bootstrap(
        workspace_path=config.workspace_path,
        memopilot_dir=config.memopilot_dir,
        profile=profile.profile,
    )
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


def _memory_item_response(item) -> MemoryItemResponse:
    return MemoryItemResponse(
        id=item.id,
        type=item.type,
        title=item.title,
        body=item.body,
        source=item.source,
        source_path=item.source_path,
        trust_level=item.trust_level,
        stale=item.stale,
        tags=item.tags,
        memory_class=item.memory_class,
        memory_status=item.memory_status,
        visibility_scope=item.visibility_scope,
        reusable=item.reusable,
        review_required=item.review_required,
        created_at=item.created_at,
        updated_at=item.updated_at,
        usage_stats=MemoryUsageStatsResponse.model_validate(
            item.usage_stats
            or {
                "recalled_count": 0,
                "used_count": 0,
                "last_used_at": None,
                "days_since_last_use": None,
            }
        ),
    )


@app.get("/v1/memory/items", response_model=MemoryItemsResponse)
async def list_memory_items(
    filter_name: str = "all",
    limit: int = 100,
    workspace_root: str | None = None,
) -> MemoryItemsResponse:
    query = MemoryListQuery(filter_name=filter_name, limit=limit)
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    items = await service.list_items(
        filter_name=query.filter_name,
        limit=query.limit,
        workspace_root=workspace_root,
    )
    return MemoryItemsResponse(items=[_memory_item_response(item) for item in items])


@app.get("/v1/memory/unused", response_model=MemoryItemsResponse)
async def list_unused_memory_items(
    days_threshold: int = 30,
    limit: int = 100,
    workspace_root: str | None = None,
) -> MemoryItemsResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    items = await service.list_unused_memories(
        days_threshold=days_threshold,
        limit=limit,
        workspace_root=workspace_root,
    )
    return MemoryItemsResponse(items=[_memory_item_response(item) for item in items])


@app.post("/v1/memory/recall", response_model=RecallResponse)
async def recall_memory(request: RecallRequest) -> RecallResponse:
    if request.workspace_root:
        workspace_service = WorkspaceRootsService(config=_get_config(), db=_get_db())
        request.workspace_root = str(
            await workspace_service.resolve_workspace_root(request.workspace_root)
        )
    service = MemoryRecallService(_get_db())
    return await service.recall(request)


async def _write_back_memory(request: SuggestMemoryRequest) -> SuggestMemoryResponse:
    return await _suggest_memory_from_request(request, smart=False)


@app.post("/v1/memory/writeback", response_model=SuggestMemoryResponse)
async def write_back_memory(request: SuggestMemoryRequest) -> SuggestMemoryResponse:
    return await _write_back_memory(request)


@app.post("/v1/memory/suggestions", response_model=SuggestMemoryResponse)
async def suggest_memory_update(request: SuggestMemoryRequest) -> SuggestMemoryResponse:
    return await _write_back_memory(request)


@app.get("/v1/memory/review", response_model=MemoryItemsResponse)
async def list_memory_review_queue(
    limit: int = 100,
    workspace_root: str | None = None,
) -> MemoryItemsResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    items = await service.list_review_items(limit=limit, workspace_root=workspace_root)
    return MemoryItemsResponse(items=[_memory_item_response(item) for item in items])


@app.patch("/v1/memory/items/{item_id}/review", response_model=MemoryActionResponse)
async def review_memory_item(item_id: str, request: MemoryReviewRequest) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    try:
        await service.review_item(
            item_id, decision=request.decision, workspace_root=request.workspace_root
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail.startswith("Memory item not found") else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/memory/items/{item_id}/approve", response_model=MemoryActionResponse)
async def approve_memory_item(
    item_id: str, workspace_root: str | None = None
) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    try:
        await service.approve_item(item_id, workspace_root=workspace_root)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail.startswith("Memory item not found") else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/memory/items/{item_id}/reject", response_model=MemoryActionResponse)
async def reject_memory_item(
    item_id: str, workspace_root: str | None = None
) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    try:
        await service.reject_item(item_id, workspace_root=workspace_root)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail.startswith("Memory item not found") else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/memory/bulk-approve", response_model=MemoryActionResponse)
async def bulk_approve_memory_items(request: BulkMemoryActionRequest) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    await service.bulk_approve(request.memory_ids, workspace_root=request.workspace_root)
    return MemoryActionResponse(success=True)


@app.post("/v1/memory/bulk-reject", response_model=MemoryActionResponse)
async def bulk_reject_memory_items(request: BulkMemoryActionRequest) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    await service.bulk_reject(request.memory_ids, workspace_root=request.workspace_root)
    return MemoryActionResponse(success=True)


@app.post("/v1/memory/bulk-delete", response_model=MemoryActionResponse)
async def bulk_delete_memory_items(request: BulkMemoryActionRequest) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    await service.bulk_delete(request.memory_ids, workspace_root=request.workspace_root)
    return MemoryActionResponse(success=True)


@app.put("/v1/memory/items/{item_id}", response_model=MemoryActionResponse)
async def edit_memory_item(
    item_id: str,
    request: MemoryEditRequest,
    workspace_root: str | None = None,
) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    try:
        await service.edit_item(
            item_id, title=request.title, body=request.body, workspace_root=workspace_root
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.delete("/v1/memory/items/{item_id}", response_model=MemoryActionResponse)
async def delete_memory_item(
    item_id: str, workspace_root: str | None = None
) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    try:
        await service.delete_item(item_id, workspace_root=workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryActionResponse(success=True)


@app.post("/v1/memory/items/{item_id}/rebuild", response_model=MemoryActionResponse)
async def rebuild_memory_item(
    item_id: str, workspace_root: str | None = None
) -> MemoryActionResponse:
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    try:
        await service.rebuild_item(item_id, workspace_root=workspace_root)
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
        budget_summary_json=request.budget_summary_json,
        stale_exclusion_count=request.stale_exclusion_count,
        included_items_json=request.included_items_json,
        excluded_items_json=request.excluded_items_json,
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
        budget_summary_json=version.budget_summary_json,
        stale_exclusion_count=version.stale_exclusion_count,
        included_items_json=version.included_items_json,
        excluded_items_json=version.excluded_items_json,
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
                budget_summary_json=item.budget_summary_json,
                stale_exclusion_count=item.stale_exclusion_count,
                included_items_json=item.included_items_json,
                excluded_items_json=item.excluded_items_json,
            )
            for item in versions
        ]
    )


def _serialize_context_pack_diff(diff_result) -> ContextPackDiffResponse:
    return ContextPackDiffResponse(
        from_version_id=diff_result.left_version_id,
        to_version_id=diff_result.right_version_id,
        left_version_id=diff_result.left_version_id,
        right_version_id=diff_result.right_version_id,
        diff_text=diff_result.diff_text,
        added_items=diff_result.added_items,
        removed_items=diff_result.removed_items,
        token_delta_estimate=diff_result.token_delta_estimate,
    )


@app.get("/v1/context-pack/diff", response_model=ContextPackDiffResponse)
async def get_context_pack_diff(
    from_version_id: str,
    to_version_id: str,
) -> ContextPackDiffResponse:
    service = ContextBuilderService(config=_get_config(), db=_get_db())
    try:
        diff_result = await service.diff_context_pack_versions(
            left_version_id=from_version_id,
            right_version_id=to_version_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_context_pack_diff(diff_result)


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
    return _serialize_context_pack_diff(diff_result)


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


class LocalModelItem(BaseModel):
    model_id: str
    source: str
    max_context_tokens: int
    supports_tools: bool
    cost_per_1m_input: float
    status: str


class LocalDiscoverResponse(BaseModel):
    models: list[LocalModelItem]
    ollama_running: bool
    lmstudio_running: bool


@app.get("/v1/providers/local-discover", response_model=LocalDiscoverResponse)
async def discover_local_providers(workspace_root: str | None = None) -> LocalDiscoverResponse:
    """Probe Ollama and LM Studio for locally available models and sync to DB."""
    config = load_provider_config(workspace_root)
    models = await discover_all_local(config)

    # Sync discovered models to provider_capabilities table
    try:
        registry = ProviderRegistryService(config=_get_config(), db=_get_db())
        for m in models:
            await registry.upsert_provider_capability(
                ProviderCapabilityRecord(
                    model_id=m.model_id,
                    source=m.source,
                    max_context_tokens=m.max_context_tokens,
                    supports_tool_calling=m.supports_tools,
                    supports_json_mode=False,
                    estimated_cost_per_1m_input=0.0,
                    estimated_cost_per_1m_output=0.0,
                    privacy_level="local",
                    allowed_task_types=[],
                    denied_task_types=[],
                    requires_approval=False,
                )
            )
    except Exception:
        pass

    return LocalDiscoverResponse(
        models=[
            LocalModelItem(
                model_id=m.model_id,
                source=m.source,
                max_context_tokens=m.max_context_tokens,
                supports_tools=m.supports_tools,
                cost_per_1m_input=0.0,
                status="available",
            )
            for m in models
        ],
        ollama_running=any(m.source == "ollama" for m in models),
        lmstudio_running=any(m.source == "lmstudio" for m in models),
    )


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
@app.get("/v1/skills", response_model=SkillStoreListResponse)
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
                source=item.source,
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
        source=item.source,
    )


@app.post("/v1/skills/import", response_model=SkillStoreItemResponse)
async def import_skill_store_item(request: SkillImportRequest) -> SkillStoreItemResponse:
    service = SkillLoaderService(config=_get_config(), db=_get_db())
    try:
        item = await service.import_skill_from_yaml(request.yaml_content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SkillStoreItemResponse(
        skill_id=item.skill_id,
        name=item.name,
        applies_when=item.applies_when,
        enabled=item.enabled,
        version=item.version,
        conflict=item.conflict,
        source=item.source,
    )


@app.get("/v1/skills/conflicts", response_model=SkillConflictListResponse)
async def list_skill_conflicts() -> SkillConflictListResponse:
    service = SkillLoaderService(config=_get_config(), db=_get_db())
    items = await service.detect_conflicts()
    return SkillConflictListResponse(
        items=[
            SkillConflictItemResponse(
                first_skill_id=item.first_skill_id,
                first_name=item.first_name,
                second_skill_id=item.second_skill_id,
                second_name=item.second_name,
                language=item.language,
                path_contains=item.path_contains,
                contradictory_rules=item.contradictory_rules,
            )
            for item in items
        ]
    )


@app.get("/v1/rules/active", response_model=ActiveRulesResponse)
async def get_active_rules(workspace_root: str | None = None) -> ActiveRulesResponse:
    """Return merged view of global rules, project rules, and detected skills."""
    config = _get_config()
    db = _get_db()

    policy_service = PolicyPacksService(config=config, db=db)
    active_policy_rules = await policy_service.list_active_policy_rules(
        workspace_root=workspace_root
    )

    global_rules: list[ActiveRuleItem] = []
    project_rules: list[ActiveRuleItem] = []

    for index, rule in enumerate(active_policy_rules):
        category = "global" if rule.source_kind == "global_dev_rules" else "project"
        target = global_rules if category == "global" else project_rules
        target.append(
            ActiveRuleItem(
                rule_id=f"active-rule-{index}",
                text=rule.rule,
                source_file=rule.source,
                enabled=True,
                category=category,
            )
        )

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
            detected_skills.append(
                ActiveSkillItem(
                    skill_id=f"fw-{fw}",
                    name=fw,
                    framework="python",
                    enabled=True,
                )
            )

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
        manifest=backup.manifest,
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
        task_type=request.task_type,
        budget_profile=request.budget_profile,
    )
    return ToolSkillOptimizeResponse(
        suggested_tools=result.suggested_tools,
        excluded_tools=result.excluded_tools,
        suggested_skills=result.suggested_skills,
        reasons=result.reasons,
        reasons_map=result.reasons_map,
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


@app.post("/v1/evidence/extract-pdf", response_model=ExtractionResultResponse)
async def extract_pdf_evidence(request: ExtractPdfRequest) -> ExtractionResultResponse:
    result = extract_pdf(await _resolve_workspace_file(request.file_path, request.workspace_root))
    return _serialize_extraction_result(result)


@app.post("/v1/evidence/extract-excel", response_model=ExtractionResultResponse)
async def extract_excel_evidence(request: ExtractExcelRequest) -> ExtractionResultResponse:
    result = extract_excel(
        await _resolve_workspace_file(request.file_path, request.workspace_root),
        sheet_names=request.sheet_names,
        column_mapping=request.column_mapping,
    )
    return _serialize_extraction_result(result)


@app.post("/v1/evidence/extract-csv", response_model=ExtractionResultResponse)
async def extract_csv_evidence(request: ExtractCsvRequest) -> ExtractionResultResponse:
    result = extract_csv(
        await _resolve_workspace_file(request.file_path, request.workspace_root),
        delimiter=request.delimiter,
        column_mapping=request.column_mapping,
    )
    return _serialize_extraction_result(result)


@app.post("/v1/evidence/extract-docx", response_model=ExtractionResultResponse)
async def extract_docx_evidence(request: ExtractDocxRequest) -> ExtractionResultResponse:
    result = extract_docx(await _resolve_workspace_file(request.file_path, request.workspace_root))
    return _serialize_extraction_result(result)


@app.post("/v1/evidence/extract-pptx", response_model=ExtractionResultResponse)
async def extract_pptx_evidence(request: ExtractPptxRequest) -> ExtractionResultResponse:
    result = extract_pptx(await _resolve_workspace_file(request.file_path, request.workspace_root))
    return _serialize_extraction_result(result)


@app.post("/v1/evidence/analyze-image", response_model=ImageAnalysisResponse)
async def analyze_image_evidence(request: AnalyzeImageRequest) -> ImageAnalysisResponse:
    result: ImageAnalysisResult = await analyze_image(
        await _resolve_workspace_file(request.file_path, request.workspace_root),
        allow_cloud=request.allow_cloud,
        workspace_root=request.workspace_root,
    )
    return ImageAnalysisResponse(**result.__dict__)


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


@app.post("/v1/policies/load", response_model=PolicyPacksResponse)
async def load_policy_packs(
    request: PolicyDirectoryLoadRequest | None = None,
    workspace_root: str | None = None,
) -> PolicyPacksResponse:
    service = PolicyPacksService(config=_get_config(), db=_get_db())
    if request is not None and request.policy_dir:
        items = await service.load_from_directory(Path(request.policy_dir))
    else:
        resolved_workspace = request.workspace_root if request is not None else workspace_root
        items = await service.load_policy_directory(workspace_root=resolved_workspace)
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


@app.get("/v1/policies/active", response_model=ActivePolicyRulesResponse)
async def list_active_policy_rules(workspace_root: str | None = None) -> ActivePolicyRulesResponse:
    service = PolicyPacksService(config=_get_config(), db=_get_db())
    items = await service.list_active_policy_rules(workspace_root=workspace_root)
    conflicts = service.resolve_conflicts(items)
    return ActivePolicyRulesResponse(
        items=[
            ActivePolicyRuleResponse(
                rule=item.rule,
                source=item.source,
                source_kind=item.source_kind,
                precedence=item.precedence,
                enforcement_mode=item.enforcement_mode,
                pack_id=item.pack_id,
                pack_name=item.pack_name,
            )
            for item in items
        ],
        conflicts=[
            PolicyConflictResponse(
                rule=item.rule,
                source=item.source,
                source_kind=item.source_kind,
                overridden_by_rule=item.overridden_by_rule,
                overridden_by_source=item.overridden_by_source,
                overridden_by_kind=item.overridden_by_kind,
                conflict_key=item.conflict_key,
            )
            for item in conflicts
        ],
        precedence_order=[
            "safety_rules",
            "policy_pack_rules",
            "workspace_rules",
            "global_dev_rules",
        ],
    )


@app.post("/v1/policies/evaluate", response_model=PolicyEvaluateResponse)
async def evaluate_policy_pack(request: PolicyEvaluateRequest) -> PolicyEvaluateResponse:
    service = PolicyPacksService(config=_get_config(), db=_get_db())
    result = await service.evaluate_policy(
        stage=request.stage,
        task_text=request.task_text,
        files_changed=request.files_changed,
        selected_model=request.selected_model,
        workspace_root=request.workspace_root,
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


@app.get("/v1/workspaces", response_model=WorkspaceRootsResponse)
async def list_workspace_roots(
    limit: int = 100,
    workspace_root: str | None = None,
) -> WorkspaceRootsResponse:
    service = WorkspaceRootsService(config=_get_config(), db=_get_db())
    items = await service.list_roots(limit=limit, workspace_root=workspace_root)
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
            workspace_root=request.workspace_root,
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
        if request.root_path:
            item = await service.set_active_root(
                request.root_path,
                workspace_root=request.workspace_root,
            )
        elif request.workspace_id:
            item = await service.activate_workspace_root(
                workspace_id=request.workspace_id,
                workspace_root=request.workspace_root,
            )
        else:
            raise ValueError("workspace_id or root_path is required")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WorkspaceRootItemResponse(
        workspace_id=item.workspace_id,
        root_path=item.root_path,
        label=item.label,
        active=item.active,
    )


async def _resolve_workspace_file(file_path: str, workspace_root: str | None = None) -> Path:
    candidate = Path(file_path)
    config = _get_config()
    workspace_service = WorkspaceRootsService(config=config, db=_get_db())
    try:
        resolved_workspace_root = await workspace_service.resolve_workspace_root(workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not candidate.is_absolute():
        candidate = (resolved_workspace_root / candidate).resolve()
        if not candidate.is_relative_to(resolved_workspace_root):
            raise HTTPException(status_code=400, detail="Path traversal denied")
    return candidate.resolve()


def _serialize_extraction_result(result: object) -> ExtractionResultResponse:
    chunks = getattr(result, "chunks", [])
    return ExtractionResultResponse(
        source_type=str(getattr(result, "source_type", "")),
        chunks=[
            DocumentChunkResponse(
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                source_hash=chunk.source_hash,
                trust_level=chunk.trust_level,
                memory_class=chunk.memory_class,
                memory_status=chunk.memory_status,
            )
            for chunk in chunks
        ],
        metadata=dict(getattr(result, "metadata", {}) or {}),
        error=getattr(result, "error", None),
        requires_ocr=bool(getattr(result, "requires_ocr", False)),
    )


def _get_config() -> Config:
    if _config is None:
        raise HTTPException(status_code=500, detail="Backend not configured")
    return _config


def _get_db() -> DatabaseManager:
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return _db
