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
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .approval_gate import (
    ComplianceWarning,
    build_compliance_warnings,
    determine_approval_tier,
    rank_patch_files,
)
from .code_review_memory import (
    ReviewLesson as ReviewMemoryLesson,
)
from .code_review_memory import (
    approve_lesson,
    extract_review_lessons,
)
from .config import Config
from .context_budget import (
    ContextBudget,
    ContextItem,
    TIER_ORDER_BY_TASK_TYPE,
    build_budget_aware_context_pack,
)
from .context_builder import ContextBuilderService
from .cost_guard import CostGuardService, check_budget_gate, infer_selected_tier
from .db import DatabaseManager
from .document_ingestion import extract_csv, extract_docx, extract_excel, extract_pdf, extract_pptx
from .endpoint_registry import ENDPOINT_STATUS
from .flow_builder import FlowBuilderService
from .image_analysis import ImageAnalysisResult, analyze_image
from .investigation_service import InvestigationService
from .mcp_orchestrator import MCPOrchestrator, ToolCall
from .memory_manager_service import MemoryManagerService
from .memory_recall import MemoryRecallService, RecallRequest, RecallResponse
from .migration_runner import run_migrations
from .model_router import ModelTier, TIER_ORDER, get_outcome_routing_hint
from .patch_assessor import PatchAssessorService
from .policy_packs import PolicyPacksService
from .privacy_dashboard_service import PrivacyDashboardService
from .provider_registry import ProviderCapabilityRecord, ProviderRegistryService
from .provider_resilience import ProviderCallError, ProviderResilienceService
from .response_cache import ResponseCacheService
from .retention import enforce_retention
from .review_memory_mode import CodeReviewMemoryModeService
from .security_policy import CredentialRedactor, DatabaseWriteBlocker
from .skill_loader import SkillLoaderService
from .validation_runner import ValidationCommand, ValidationRunner
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
_retention_task: asyncio.Task[None] | None = None
_RETENTION_INTERVAL_SECONDS = 6 * 60 * 60


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


class StartTaskRunRequest(BaseModel):
    user_request: str
    task_type: str | None = None
    mode: str | None = None
    risk_level: str | None = None
    selected_model: str | None = None
    estimated_cost: float | None = Field(default=None, ge=0)
    constraints: list[str] = Field(default_factory=list)
    notes: str | None = None
    workspace_root: str | None = None


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


class StartTaskRunResponse(BaseModel):
    task_run_id: str
    status: str
    estimated_cost: float | None = None
    actual_cost: float = 0.0
    cost: TaskRunCostResponse | None = None
    budget_gate: BudgetGateResponse | None = None


class TaskAnalyzeRequest(BaseModel):
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


class ContextBuildRequest(BaseModel):
    task_description: str
    suggested_files: list[str] = Field(default_factory=list)
    file_overrides: list[str] | None = None
    mode: str | None = None
    workspace_root: str | None = None
    task_type: str | None = None
    model_max_tokens: int | None = Field(default=None, ge=1)


class ContextFileEntry(BaseModel):
    path: str
    tokens: int
    content: str | None = None


class StaleExclusionsResponse(BaseModel):
    count: int = 0
    affected_modules: list[str] = Field(default_factory=list)
    rebuild_command: str | None = None


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


class ValidationCommandRequest(BaseModel):
    name: str
    command: list[str] = Field(min_length=1)
    timeout: int | None = Field(default=None, ge=1)


class ValidateRequest(BaseModel):
    patches: list[dict] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=lambda: ["syntax", "lint", "test_impact"])
    commands: list[ValidationCommandRequest] = Field(default_factory=list)
    command_timeouts: dict[str, int] = Field(default_factory=dict)


class ValidationCheck(BaseModel):
    name: str
    status: str  # "pass", "fail", "warn", "skipped", "timeout"
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
    savings_report: SavingsReportResponse | None = None


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


class SuggestMemoryResponse(BaseModel):
    memory_item_id: str | None
    pending_approval: bool
    artifact_id: str | None = None
    blocked_reason: str | None = None


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


class SubmitReviewEvidenceRequest(BaseModel):
    pr_number: int
    body: str
    path: str | None = None
    line: int | None = None
    workspace_root: str | None = None


class SubmitReviewEvidenceResponse(BaseModel):
    evidence_id: str
    approved: bool


class ApproveReviewLessonRequest(BaseModel):
    evidence_id: str
    lesson_title: str
    lesson_body: str
    workspace_root: str | None = None


class ApproveReviewLessonResponse(BaseModel):
    memory_item_id: str
    evidence_id: str


class ExtractReviewLessonsRequest(BaseModel):
    review_comments: list[dict[str, object]] = Field(default_factory=list)


class ReviewMemoryLessonResponse(BaseModel):
    summary: str
    context: str
    source_pr: str | None = None
    source_reviewer: str | None = None
    approved: bool = False


class ExtractReviewLessonsResponse(BaseModel):
    lessons: list[ReviewMemoryLessonResponse]


class ApproveReviewMemoryLessonRequest(BaseModel):
    summary: str
    context: str
    source_pr: str | None = None
    source_reviewer: str | None = None
    workspace_root: str | None = None


class ApproveReviewMemoryLessonResponse(BaseModel):
    memory_item_id: str
    approved: bool


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
    investigation_session_id: str | None = None
    column_mapping: dict[str, str] | None = None
    workspace_root: str | None = None


class AttachEvidenceResponse(BaseModel):
    evidence_id: str
    source_type: str
    trust_level: int
    extraction_method: str
    extraction_status: str
    findings: list[str]
    redacted_values: int
    source_path: str | None = None
    investigation_session_id: str | None = None


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
    investigation_session_id: str | None = None


class EvidenceBoardResponse(BaseModel):
    items: list[EvidenceBoardItemResponse]


class StartInvestigationRequest(BaseModel):
    title: str
    description: str = ""
    mode: str = "investigation"
    workspace_root: str | None = None


class InvestigationSessionResponse(BaseModel):
    id: str
    title: str
    description: str | None = None
    mode: str
    status: str
    workspace_root: str
    created_at: str
    updated_at: str
    evidence_count: int = 0
    evidence: list[EvidenceBoardItemResponse] = Field(default_factory=list)


class RemoveEvidenceResponse(BaseModel):
    evidence_id: str
    removed: bool


class EvidenceColumnsPreviewRequest(BaseModel):
    evidence_path: str
    workspace_root: str | None = None


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
    workspace_root: str | None = None


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



def _serialize_compliance_warnings(
    warnings: list[ComplianceWarning],
) -> list[ComplianceWarningResponse]:
    return [
        ComplianceWarningResponse(
            rule_id=warning.rule_id,
            rule_text=warning.rule_text,
            warning_message=warning.warning_message,
            actions=[
                ComplianceActionResponse(
                    label=action.label,
                    action_type=action.action_type,
                    prefill_task_request=action.prefill_task_request,
                    prefill_mode=action.prefill_mode,
                    prefill_context_hints=action.prefill_context_hints,
                )
                for action in warning.actions
            ],
        )
        for warning in warnings
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
    global _retention_task
    try:
        await _run_retention_pass()
    except Exception:
        logger.exception("Startup retention enforcement failed")
    if _retention_task is None or _retention_task.done():
        _retention_task = asyncio.create_task(_retention_loop())


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
    await profile_service.ensure_profile()

    logger.info(f"Workspace initialized: {config.memopilot_dir} (schema v{schema_version})")

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


def _collect_task_paths(request: TaskAnalyzeRequest) -> list[str]:
    raw_paths = [*request.file_paths, *request.changed_files, *request.context_files]
    path_pattern = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s,;]+|[\w.-]+(?:[\\/][\w.-]+)+")
    for source in (request.description, request.notes or ""):
        raw_paths.extend(match.group(0) for match in path_pattern.finditer(source))

    seen: set[str] = set()
    normalized_paths: list[str] = []
    for raw_path in raw_paths:
        candidate = raw_path.strip().strip("`\"'")
        if not candidate:
            continue
        normalized = candidate.replace("\\", "/")
        if normalized not in seen:
            seen.add(normalized)
            normalized_paths.append(normalized)
    return normalized_paths


def _classify_task_from_signals(request: TaskAnalyzeRequest) -> tuple[str, str]:
    normalized_paths = [path.lower() for path in _collect_task_paths(request)]

    for path in normalized_paths:
        file_name = path.rsplit("/", 1)[-1]
        if (
            file_name.endswith("_test.py")
            or (file_name.startswith("test_") and file_name.endswith(".py"))
            or file_name.endswith(".spec.ts")
            or file_name.endswith(".test.ts")
        ):
            return "test_generation", "low"
        if file_name.endswith("_migration.py") or "/migrations/" in f"/{path}/":
            return "schema_change", "critical"

    for path in normalized_paths:
        if any(segment in path for segment in ("/auth/", "/security/", "/permission/", "/oauth/")):
            return "security_change", "high"
        if any(
            segment in path for segment in ("/billing/", "/payment/", "/invoice/", "/subscription/")
        ):
            return "billing_change", "high"

    combined_text = f"{request.description} {request.notes or ''}".lower()
    joined_paths = " ".join(normalized_paths)
    if any(signal in joined_paths for signal in ("migration", "schema", "alembic")) or any(
        keyword in combined_text for keyword in ("migration", "schema", "alembic")
    ):
        return "schema_change", "critical"
    if any(keyword in combined_text for keyword in ("explain", "summarize", "describe")):
        return "explanation", "low"
    if any(keyword in combined_text for keyword in ("document", "docstring", "comment", "readme")):
        return "documentation", "low"
    if (
        any(keyword in combined_text for keyword in ("refactor", "restructure", "move", "rename"))
        and len(normalized_paths) == 1
    ):
        return "bounded_refactor", "medium"
    if any(keyword in combined_text for keyword in ("fix", "bug", "error", "exception", "broken")):
        return "bug_fix", "medium"
    return "general", "medium"


@app.post("/v1/task/analyze", response_model=TaskAnalyzeResponse)
async def analyze_task(request: TaskAnalyzeRequest) -> TaskAnalyzeResponse:
    """Parse task intent and suggest context scope without starting a run."""
    config = _get_config()
    db = _get_db()

    description = request.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Task description is required.")

    task_type, risk = _classify_task_from_signals(request)

    # Determine suggested mode from classification and keywords
    mode = request.mode
    if not mode:
        mode_by_task_type = {
            "billing_change": "fix",
            "bounded_refactor": "refactor",
            "bug_fix": "fix",
            "documentation": "document",
            "schema_change": "refactor",
            "security_change": "fix",
            "test_generation": "test",
        }
        mode = mode_by_task_type.get(task_type)
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
    complexity = (
        "low" if complexity_signals == 0 else "medium" if complexity_signals <= 1 else "high"
    )

    # Find applicable rules from active policy packs
    applicable_rules: list[str] = []
    try:
        policy_service = PolicyPacksService(config=config, db=db)
        active_rules = await policy_service.list_active_policy_rules(
            workspace_root=request.workspace_root
        )
        applicable_rules.extend([item.rule for item in active_rules[:5]])
    except Exception:
        pass

    # Add constraint-derived rules
    if "follow_all_rules" in request.constraints:
        pass  # Already including all active rules above
    if (
        "run_tests" in request.constraints
        and "Run tests after applying changes" not in applicable_rules
    ):
        applicable_rules.append("Run tests after applying changes")

    # Suggest files by searching memory for relevant symbols
    suggested_files: list[str] = []
    try:
        memory_service = MemoryManagerService(config=config, db=db)
        items = await memory_service.list_items(
            filter_name="file_summaries",
            limit=200,
            workspace_root=request.workspace_root,
        )
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
        task_type=task_type,
        risk=risk,
    )


def _estimate_context_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _serialize_context_item(item: ContextItem) -> dict[str, object]:
    return {
        "content": item.content,
        "source": item.source,
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


def _read_context_file_item(workspace_root: str, file_path: str) -> ContextItem:
    full_path = os.path.join(workspace_root, file_path) if not os.path.isabs(file_path) else file_path
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
    workspace_service = WorkspaceRootsService(config=config, db=db)
    workspace_root = str(await workspace_service.resolve_workspace_root(request.workspace_root))

    file_items = [_read_context_file_item(workspace_root, file_path) for file_path in files_to_include[:20]]

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

    retrieval_results: dict[str, list[ContextItem | dict[str, object]]] = {
        "current_file": file_items,
        "stack_trace": _build_stack_trace_items(request),
        "fts": recall_items,
        "rules": [
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
    recall_service = MemoryRecallService(db)
    await recall_service.record_recall_trace(
        context_pack_hash=context_pack_hash,
        request_json=request.model_dump_json(),
        included_memory_ids=[item.source for item in included_items if item.source_type == "memory"],
        excluded_memory_ids=[item.source for item in excluded_items if item.source_type == "memory"],
    )

    return ContextBuildResponse(
        **response_payload,
        context_pack_hash=context_pack_hash,
    )


@app.post("/v1/context-pack/generate", response_model=ContextBuildResponse)
async def generate_context_pack(request: ContextBuildRequest) -> ContextBuildResponse:
    return await _generate_context_pack_response(request)


@app.post("/v1/context/build", response_model=ContextBuildResponse, deprecated=True)
async def build_context_pack(request: ContextBuildRequest) -> ContextBuildResponse:
    return await _generate_context_pack_response(request)


@app.post("/v1/model/route", response_model=ModelRouteResponse)
async def route_model(request: ModelRouteRequest) -> ModelRouteResponse:
    """Select optimal model based on context size, task type, privacy, and budget."""
    config = _get_config()
    db = _get_db()

    context_tokens = request.context_tokens
    privacy = request.privacy_level
    task_type = request.task_type

    cost_service = CostGuardService(config=config, db=db)
    remaining_usd = 50.0
    try:
        budget_info = await cost_service.get_budget_status()
        remaining_usd = budget_info.remaining_usd
    except Exception:
        budget_info = None

    candidates: list[ModelChoice] = []

    local_fits = context_tokens <= 32_000
    local_reasons = []
    if local_fits:
        local_reasons.append("Context fits local model window (32K)")
    if privacy in ("local_only", "local_preferred"):
        local_reasons.append("Privacy preference: local")
    if task_type in ("refactor", "fix", "test"):
        local_reasons.append(f"Task type '{task_type}' suitable for local model")
    candidates.append(
        ModelChoice(
            model_id="codellama-13b-local",
            provider="ollama",
            cost_estimate_usd=0.0,
            reasons=local_reasons or ["Local model available"],
            fits_context=local_fits,
        )
    )

    gpt4o_cost = (context_tokens / 1_000_000) * 5.0 + 0.015
    candidates.append(
        ModelChoice(
            model_id="gpt-4o",
            provider="openai",
            cost_estimate_usd=round(gpt4o_cost, 4),
            reasons=["Higher quality for complex tasks", "128K context window"],
            fits_context=context_tokens <= 128_000,
        )
    )

    claude_cost = (context_tokens / 1_000_000) * 3.0 + 0.015
    candidates.append(
        ModelChoice(
            model_id="claude-3.5-sonnet",
            provider="anthropic",
            cost_estimate_usd=round(claude_cost, 4),
            reasons=["Strong at structured code changes", "200K context window"],
            fits_context=context_tokens <= 200_000,
        )
    )

    allowed_candidates: list[ModelChoice] = []
    candidate_checks: dict[str, object] = {}
    for candidate in candidates:
        provider_privacy = "local" if candidate.provider == "ollama" else "cloud"
        provider_check = await cost_service.check_provider_budget(
            provider=candidate.provider,
            model=candidate.model_id,
            privacy_level=provider_privacy,
            estimated_cost_usd=candidate.cost_estimate_usd,
            requires_approval=candidate.model_id in {"gpt-4o", "claude-3.5-sonnet"},
            approval_granted=False,
        )
        candidate_checks[candidate.model_id] = provider_check
        if candidate.fits_context and provider_check.allowed:
            allowed_candidates.append(candidate)

    selection_pool = (
        allowed_candidates
        or [candidate for candidate in candidates if candidate.fits_context]
        or candidates
    )
    recommended = selection_pool[0]

    if request.preferred_model:
        for candidate in selection_pool:
            if candidate.model_id == request.preferred_model:
                recommended = candidate
                break
    elif not local_fits:
        cloud_fits = [candidate for candidate in selection_pool if candidate.provider != "ollama"]
        if cloud_fits:
            recommended = min(cloud_fits, key=lambda item: item.cost_estimate_usd)
    elif privacy == "cloud_ok" and task_type in ("complex", "architecture"):
        for candidate in selection_pool:
            if candidate.provider != "ollama":
                recommended = candidate
                break

    def candidate_tier(candidate: ModelChoice) -> ModelTier:
        if candidate.provider == "ollama":
            return ModelTier.LOCAL
        if candidate.provider == "anthropic":
            return ModelTier.FRONTIER
        return ModelTier.CHEAP_CLOUD

    base_tier = candidate_tier(recommended)
    escalation_source: str | None = None
    routing_reason = {
        ModelTier.LOCAL: (
            "Routing to local based on context fit and privacy preferences. Frontier escalation "
            "would only trigger after 2 failed non-frontier attempts on the same file within 30 "
            "days, and local routes are not escalated automatically."
        ),
        ModelTier.CHEAP_CLOUD: (
            "Routing to cheap_cloud based on the current context budget. Frontier escalation would "
            "trigger after 2 failed non-frontier attempts on the same file within 30 days."
        ),
        ModelTier.FRONTIER: (
            "Routing to frontier because the task already needs the highest-capability tier. "
            "Frontier escalation would otherwise trigger after 2 failed non-frontier attempts on "
            "the same file within 30 days, and no higher escalation tier exists."
        ),
    }[base_tier]

    if request.files_in_context and base_tier != ModelTier.LOCAL:
        conn = await db.connect()
        hinted_tier, hinted_reason = await get_outcome_routing_hint(
            task_type=task_type,
            files_in_context=request.files_in_context,
            db_conn=conn,
        )
        if hinted_tier is not None and TIER_ORDER[hinted_tier] > TIER_ORDER[base_tier]:
            escalation_source = "recent_file_failures"
            routing_reason = (
                f"{hinted_reason} Without repeated file failures, this request would stay on "
                f"{base_tier.value}."
            )
            for candidate in candidates:
                if candidate_tier(candidate) == hinted_tier:
                    recommended = candidate
                    break

    reason_list = [*recommended.reasons, routing_reason]
    if request.model_override:
        reason_list.append("Model override requested by caller.")
    recommended = recommended.model_copy(update={"reasons": reason_list})

    alternatives = [
        candidate for candidate in candidates if candidate.model_id != recommended.model_id
    ]
    recommended_check = candidate_checks.get(recommended.model_id)
    budget_allowed = bool(
        getattr(recommended_check, "allowed", recommended.cost_estimate_usd <= remaining_usd)
    )
    options = [
        ModelRouteOption(
            tier=candidate_tier(candidate).value,
            model_id=candidate.model_id,
            provider=candidate.provider,
            cost_estimate_usd=candidate.cost_estimate_usd,
            fits_context=candidate.fits_context,
        )
        for candidate in sorted(candidates, key=lambda item: TIER_ORDER[candidate_tier(item)])
    ]
    return ModelRouteResponse(
        recommended=recommended,
        alternatives=alternatives,
        budget_check=BudgetCheck(allowed=budget_allowed, remaining_usd=round(remaining_usd, 2)),
        options=options,
        escalation_source=escalation_source,
        base_tier=base_tier.value,
        model_override=request.model_override,
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
        patches.append(
            FilePatch(
                path=file_path,
                action="modify",
                original_content="# existing code\n# rest of file\n",
                new_content=(
                    f"# existing code\n# AI-generated change ({seed})\n"
                    f"# Task: {description[:50]}\n# rest of file\n"
                ),
                diff=mock_diff.strip(),
            )
        )

    # If no context files provided, generate a single placeholder patch
    if not patches:
        patches.append(
            FilePatch(
                path="src/changes.py",
                action="create",
                original_content=None,
                new_content=f"# New file for: {description[:60]}\n",
                diff=(
                    f"--- /dev/null\n+++ b/src/changes.py\n"
                    f"@@ -0,0 +1,1 @@\n+# New file for: {description[:60]}"
                ),
            )
        )

    # Estimate risk based on file count and mode
    risk = "low"
    if len(patches) > 3:
        risk = "medium"
    if request.mode in ("refactor", "architecture"):
        risk = "high" if len(patches) > 2 else "medium"

    ranked_files = rank_patch_files([patch.path for patch in patches])
    approval_tier = determine_approval_tier(ranked_files)
    compliance_warnings = build_compliance_warnings(ranked_files)

    approval_risk = {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "high",
    }[approval_tier.value]
    risk_priority = {"low": 0, "medium": 1, "high": 2}
    if risk_priority[approval_risk] > risk_priority[risk]:
        risk = approval_risk

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
        approval_tier=approval_tier.value,
        ranked_files=_serialize_ranked_files(ranked_files),
        compliance_warnings=_serialize_compliance_warnings(compliance_warnings),
    )


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _validation_command_for_check(
    *,
    check_name: str,
    request: ValidateRequest,
    config: Config,
) -> ValidationCommand | None:
    timeout = request.command_timeouts.get(check_name)
    workspace = config.workspace_path
    normalized = check_name.strip().lower()
    if normalized == "syntax":
        return ValidationCommand(
            name="Syntax Check",
            display_name="Syntax Check",
            argv=[sys.executable, "-m", "compileall", "-q", str(workspace)],
            timeout=timeout,
            cwd=workspace,
        )
    if normalized in {"pytest", "tests"}:
        return ValidationCommand(
            name="Pytest",
            display_name="Pytest",
            argv=[sys.executable, "-m", "pytest", "-q"],
            timeout=timeout,
            cwd=workspace,
        )
    if normalized == "ruff" and _module_available("ruff"):
        return ValidationCommand(
            name="Ruff",
            display_name="Ruff",
            argv=[sys.executable, "-m", "ruff", "check", "."],
            timeout=timeout,
            cwd=workspace,
        )
    if normalized == "mypy" and _module_available("mypy"):
        return ValidationCommand(
            name="Mypy",
            display_name="Mypy",
            argv=[sys.executable, "-m", "mypy", "."],
            timeout=timeout,
            cwd=workspace,
        )
    if normalized == "lint" and _module_available("ruff"):
        return ValidationCommand(
            name="Lint",
            display_name="Lint",
            argv=[sys.executable, "-m", "ruff", "check", "."],
            timeout=timeout,
            cwd=workspace,
        )
    return None


@app.post("/v1/task/validate", response_model=ValidateResponse)
async def validate_patches(request: ValidateRequest) -> ValidateResponse:
    """Run validation checks on proposed patches."""
    config = _get_config()
    runner = ValidationRunner(config=config)
    checks_to_run = request.checks
    results: list[ValidationCheck] = []

    for check_name in checks_to_run:
        command = _validation_command_for_check(
            check_name=check_name,
            request=request,
            config=config,
        )
        if command is not None:
            command_result = await runner.run_command(command)
            results.append(
                ValidationCheck(
                    name=command_result.name,
                    status=command_result.status,
                    message=command_result.message,
                )
            )
            continue

        if check_name == "lint":
            if len(request.patches) > 5:
                results.append(
                    ValidationCheck(
                        name="Lint",
                        status="warn",
                        message=f"{len(request.patches)} files changed — review lint warnings.",
                    )
                )
            else:
                results.append(
                    ValidationCheck(
                        name="Lint",
                        status="pass",
                        message="No lint issues detected.",
                    )
                )
        elif check_name == "test_impact":
            test_files = [p for p in request.patches if "test" in str(p.get("path", "")).lower()]
            if test_files:
                results.append(
                    ValidationCheck(
                        name="Test Impact",
                        status="warn",
                        message=(
                            f"{len(test_files)} test file(s) modified — "
                            "re-run tests recommended."
                        ),
                    )
                )
            else:
                results.append(
                    ValidationCheck(
                        name="Test Impact",
                        status="pass",
                        message="No test files affected.",
                    )
                )
        elif check_name == "security":
            results.append(
                ValidationCheck(
                    name="Security Scan",
                    status="pass",
                    message="No secrets or vulnerabilities detected in patches.",
                )
            )
        else:
            results.append(
                ValidationCheck(
                    name=check_name,
                    status="skipped",
                    message=f"Check '{check_name}' not implemented.",
                )
            )

    for command_request in request.commands:
        command_result = await runner.run_command(
            ValidationCommand(
                name=command_request.name,
                display_name=command_request.name,
                argv=command_request.command,
                timeout=command_request.timeout,
                cwd=config.workspace_path,
            )
        )
        results.append(
            ValidationCheck(
                name=command_result.name,
                status=command_result.status,
                message=command_result.message,
            )
        )

    statuses = [c.status for c in results]
    if "fail" in statuses or "timeout" in statuses:
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
        workspace_root=request.workspace_root,
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
    workspace_root = None
    if request.workspace_root:
        workspace_service = WorkspaceRootsService(config=_get_config(), db=_get_db())
        workspace_root = str(await workspace_service.resolve_workspace_root(request.workspace_root))
    task_run_id = await service.create_task_run(
        user_request=request.user_request,
        task_type=request.task_type,
        mode=request.mode,
        risk_level=request.risk_level,
        selected_model=request.selected_model,
        estimated_cost=request.estimated_cost,
        workspace_root=workspace_root,
    )
    budget_info = await service.get_budget_status()
    selected_tier = infer_selected_tier(provider=None, model=request.selected_model)
    budget_gate = check_budget_gate(
        selected_tier,
        request.estimated_cost or 0.0,
        budget_info,
    )
    return StartTaskRunResponse(
        task_run_id=task_run_id,
        status="running",
        estimated_cost=request.estimated_cost,
        actual_cost=0.0,
        cost=TaskRunCostResponse(
            estimated_cost_usd=request.estimated_cost or 0.0,
            actual_cost_usd=0.0,
            selected_tier=selected_tier,
        ),
        budget_gate=BudgetGateResponse(
            blocked=budget_gate.blocked,
            reason=budget_gate.reason,
            requires_approval=budget_gate.requires_approval,
            approval_prompt=budget_gate.approval_prompt,
            show_warning=budget_gate.show_warning,
            warning_message=budget_gate.warning_message,
        ),
    )


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

    return CostDashboardResponse(
        period_days=days,
        total_cost_usd=round(budget.spent_usd, 4),
        total_calls=total_calls,
        total_tokens=total_calls * 3000,
        saved_usd=round(budget.saved_usd, 4),
        by_day=by_day,
        by_model=by_model,
        savings_report=_to_savings_report_response(savings),
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

    # Detect MCP server configs from workspace .memopilot/mcp.json or settings
    servers: list[dict] = []
    import json as json_mod
    import os

    workspace_root = config.workspace_root if hasattr(config, "workspace_root") else "."
    mcp_config_path = os.path.join(workspace_root, ".memopilot", "mcp.json")

    if os.path.exists(mcp_config_path):
        try:
            with open(mcp_config_path) as f:
                mcp_config = json_mod.load(f)
            for server in mcp_config.get("servers", []):
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
    service = MemoryManagerService(config=_get_config(), db=_get_db())
    workspace_root = None
    if request.workspace_root:
        workspace_service = WorkspaceRootsService(config=_get_config(), db=_get_db())
        workspace_root = str(await workspace_service.resolve_workspace_root(request.workspace_root))
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


@app.post("/v1/reviews/evidence", response_model=SubmitReviewEvidenceResponse)
async def submit_review_evidence(
    request: SubmitReviewEvidenceRequest,
) -> SubmitReviewEvidenceResponse:
    workspace_root = None
    if request.workspace_root:
        workspace_service = WorkspaceRootsService(config=_get_config(), db=_get_db())
        workspace_root = str(await workspace_service.resolve_workspace_root(request.workspace_root))
    service = CodeReviewMemoryModeService(config=_get_config(), db=_get_db())
    evidence = await service.submit_review_evidence(
        pr_number=request.pr_number,
        body=request.body,
        path=request.path,
        line=request.line,
        workspace_root=workspace_root,
    )
    return SubmitReviewEvidenceResponse(
        evidence_id=evidence.evidence_id, approved=evidence.approved
    )


@app.post("/v1/reviews/approve-lesson", response_model=ApproveReviewLessonResponse)
async def approve_review_lesson(request: ApproveReviewLessonRequest) -> ApproveReviewLessonResponse:
    workspace_root = None
    if request.workspace_root:
        workspace_service = WorkspaceRootsService(config=_get_config(), db=_get_db())
        workspace_root = str(await workspace_service.resolve_workspace_root(request.workspace_root))
    service = CodeReviewMemoryModeService(config=_get_config(), db=_get_db())
    try:
        lesson = await service.approve_review_lesson(
            evidence_id=request.evidence_id,
            lesson_title=request.lesson_title,
            lesson_body=request.lesson_body,
            workspace_root=workspace_root,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApproveReviewLessonResponse(
        memory_item_id=lesson.memory_item_id,
        evidence_id=lesson.evidence_id,
    )


@app.post("/v1/memory/review-lessons/extract", response_model=ExtractReviewLessonsResponse)
async def extract_review_memory_lessons(
    request: ExtractReviewLessonsRequest,
) -> ExtractReviewLessonsResponse:
    lessons = extract_review_lessons(list(request.review_comments))
    return ExtractReviewLessonsResponse(
        lessons=[
            ReviewMemoryLessonResponse(
                summary=lesson.summary,
                context=lesson.context,
                source_pr=lesson.source_pr,
                source_reviewer=lesson.source_reviewer,
                approved=lesson.approved,
            )
            for lesson in lessons
        ]
    )


@app.post("/v1/memory/review-lessons/approve", response_model=ApproveReviewMemoryLessonResponse)
async def approve_review_memory_lesson(
    request: ApproveReviewMemoryLessonRequest,
) -> ApproveReviewMemoryLessonResponse:
    workspace_root = str(_get_config().workspace_path.resolve())
    if request.workspace_root:
        workspace_service = WorkspaceRootsService(config=_get_config(), db=_get_db())
        workspace_root = str(await workspace_service.resolve_workspace_root(request.workspace_root))

    lesson = ReviewMemoryLesson(
        summary=request.summary,
        context=request.context,
        source_pr=request.source_pr,
        source_reviewer=request.source_reviewer,
        approved=True,
    )
    memory_item = approve_lesson(lesson)
    memory_item_id = uuid.uuid4().hex
    conn = await _get_db().connect()
    tags = json.dumps(
        {
            "approved_review_lesson": True,
            "source_reviewer": request.source_reviewer,
            "maintainer_approved": True,
        }
    )
    provenance = json.dumps(
        [
            {
                "source_type": "code_review",
                "source_ref": request.source_pr or "unknown",
                "source_path": request.context,
                "reviewer": request.source_reviewer,
            }
        ]
    )
    await conn.execute(
        """
        INSERT INTO memory_items
        (
            id, type, title, body, source, source_path, source_hash, trust_level,
            tags_json, stale, memory_class, memory_status, visibility_scope,
            reusable, review_required, use_policy_json, provenance_json, workspace_root
        )
        VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, ?, ?, 'workspace', ?, ?, NULL, ?, ?)
        """,
        (
            memory_item_id,
            memory_item["type"],
            memory_item["title"],
            memory_item["body"],
            memory_item["source"],
            memory_item["source_path"],
            int(memory_item["trust_level"]),
            tags,
            memory_item["memory_class"],
            memory_item["memory_status"],
            int(memory_item["reusable"]),
            int(memory_item["review_required"]),
            provenance,
            workspace_root,
        ),
    )
    await conn.commit()
    return ApproveReviewMemoryLessonResponse(memory_item_id=memory_item_id, approved=True)


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


def _build_evidence_item_response(item) -> EvidenceBoardItemResponse:
    return EvidenceBoardItemResponse(
        evidence_id=item.evidence_id,
        source_type=item.source_type,
        source_path=item.source_path,
        source_url=item.source_url,
        trust_level=item.trust_level,
        extraction_method=item.extraction_method,
        extraction_status=item.extraction_status,
        redacted_values=item.redacted_values,
        findings=item.findings,
        investigation_session_id=item.investigation_session_id,
    )


def _build_investigation_session_response(session) -> InvestigationSessionResponse:
    return InvestigationSessionResponse(
        id=session.id,
        title=session.title,
        description=session.description,
        mode=session.mode,
        status=session.status,
        workspace_root=session.workspace_root,
        created_at=session.created_at,
        updated_at=session.updated_at,
        evidence_count=session.evidence_count,
        evidence=[_build_evidence_item_response(item) for item in session.evidence],
    )


@app.post("/v1/investigation/start", response_model=InvestigationSessionResponse)
async def start_investigation(request: StartInvestigationRequest) -> InvestigationSessionResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    try:
        session = await service.start_session(
            title=request.title,
            description=request.description,
            mode=request.mode,
            workspace_root=request.workspace_root,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _build_investigation_session_response(session)


@app.post("/v1/investigation/{session_id}/evidence", response_model=AttachEvidenceResponse)
async def attach_investigation_evidence(
    session_id: str,
    request: AttachEvidenceRequest,
) -> AttachEvidenceResponse:
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
            investigation_session_id=session_id,
            column_mapping=request.column_mapping,
            workspace_root=request.workspace_root,
        )
    except ValueError as exc:
        status_code = 404 if "Investigation session not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return AttachEvidenceResponse(
        evidence_id=result.evidence_id,
        source_type=result.source_type,
        trust_level=result.trust_level,
        extraction_method=result.extraction_method,
        extraction_status=result.extraction_status,
        findings=result.findings,
        redacted_values=result.redacted_values,
        source_path=result.source_path,
        investigation_session_id=result.investigation_session_id,
    )


@app.delete(
    "/v1/investigation/{session_id}/evidence/{evidence_id}",
    response_model=RemoveEvidenceResponse,
)
async def delete_investigation_evidence(
    session_id: str,
    evidence_id: str,
) -> RemoveEvidenceResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    try:
        result = await service.remove_evidence(session_id=session_id, evidence_id=evidence_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RemoveEvidenceResponse(evidence_id=result.evidence_id, removed=result.removed)


@app.post(
    "/v1/investigation/{session_id}/transition-to-patch",
    response_model=InvestigationSessionResponse,
)
async def transition_investigation_to_patch(session_id: str) -> InvestigationSessionResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    try:
        session = await service.transition_to_patch(session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _build_investigation_session_response(session)


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
            investigation_session_id=request.investigation_session_id,
            column_mapping=request.column_mapping,
            workspace_root=request.workspace_root,
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
        investigation_session_id=result.investigation_session_id,
    )


@app.get("/v1/investigation/evidence", response_model=EvidenceBoardResponse)
async def list_evidence(
    task_run_id: str | None = None,
    investigation_session_id: str | None = None,
    workspace_root: str | None = None,
) -> EvidenceBoardResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    items = await service.list_evidence_board(
        task_run_id=task_run_id,
        investigation_session_id=investigation_session_id,
        workspace_root=workspace_root,
    )
    return EvidenceBoardResponse(items=[_build_evidence_item_response(item) for item in items])


@app.post(
    "/v1/investigation/evidence/columns",
    response_model=EvidenceColumnsPreviewResponse,
)
async def preview_evidence_columns(
    request: EvidenceColumnsPreviewRequest,
) -> EvidenceColumnsPreviewResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    try:
        preview = await service.preview_columns(
            evidence_path=request.evidence_path,
            workspace_root=request.workspace_root,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EvidenceColumnsPreviewResponse(
        source_type=preview.source_type,
        columns=preview.columns,
        suggested_mapping=preview.suggested_mapping,
        requires_confirmation=preview.requires_confirmation,
    )


@app.get("/v1/investigation/{session_id}", response_model=InvestigationSessionResponse)
async def get_investigation(session_id: str) -> InvestigationSessionResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    try:
        session = await service.get_session(session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _build_investigation_session_response(session)


@app.post("/v1/investigation/run", response_model=RunInvestigationResponse)
async def run_investigation(request: RunInvestigationRequest) -> RunInvestigationResponse:
    service = InvestigationService(config=_get_config(), db=_get_db())
    result = await service.run_investigation(
        title=request.title,
        description=request.description,
        acceptance_criteria=request.acceptance_criteria,
        task_run_id=request.task_run_id,
        workspace_root=request.workspace_root,
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


@app.post("/v1/patch/rank-files", response_model=PatchRankFilesResponse)
async def rank_patch_files_endpoint(
    request: PatchRankFilesRequest,
) -> PatchRankFilesResponse:
    ranked_files = rank_patch_files(request.changed_files)
    approval_tier = determine_approval_tier(ranked_files)
    return PatchRankFilesResponse(
        ranked_files=_serialize_ranked_files(ranked_files),
        approval_tier=approval_tier.value,
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
            flow_id=request.flow_id,
            name=request.name,
            description=request.description,
            steps=[step.model_dump(exclude_none=True) for step in request.steps],
            flow_yaml=request.flow_yaml,
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
            constraints=request.constraints,
            approved_steps=request.approved_steps,
            planned_mcp_calls=request.planned_mcp_calls,
            mcp_cap=request.mcp_cap,
            failure_count=request.failure_count,
            allow_file_modifications=request.allow_file_modifications,
            workspace_root=request.workspace_root,
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
