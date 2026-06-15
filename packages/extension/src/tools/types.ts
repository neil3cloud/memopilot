/**
 * Types for MemoPilot LM Tools integration.
 */

export interface MemoPilotContextInput {
    task_description: string;
    files_in_focus?: string[];
    task_type_hint?: string;
}

export interface MemoPilotMemorySearchInput {
    query: string;
    limit?: number;
}

export interface MemoPilotPatchReviewInput {
    git_diff?: string;
}

export interface MemoPilotWritebackInput {
    outcome_summary: string;
    outcome_status: 'success' | 'partial' | 'reverted';
    context_pack_hash?: string;
    git_diff?: string;
}

export interface ContextPackToolResponse {
    rendered_markdown: string;
    context_pack_hash?: string;
    total_tokens?: number;
    stale_exclusion_count?: number;
    redacted_values_count?: number;
}

export interface PatchReviewResponse {
    task_run_id: string;
    risk_level: string;
    risk_category: string;
    compliance_score: number;
    rendered_report: string;
    secret_detected: boolean;
    patch_governance_available: boolean;
}

export interface WritebackResponse {
    writeback_id: string;
    task_run_id: string;
    proposals_count: number;
    blocked_content_count: number;
    already_processed: boolean;
    rendered_summary: string;
}

export interface RulesResponse {
    rendered_markdown: string;
}

export interface WorkspaceProfileResponse {
    rendered_markdown: string;
}

export interface MemorySearchResponse {
    rendered_markdown: string;
    items: unknown[];
}
