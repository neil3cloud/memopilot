import { BackendManager } from './BackendManager';

export interface HealthResponse {
    schema_version: number;
    api_version: number;
    status: string;
}

export interface InitWorkspaceResponse {
    initialized: boolean;
    memopilot_dir: string;
}

export interface WorkspaceIndexResponse {
    python_project: boolean;
    total_files_scanned: number;
    indexed_files: number;
    unchanged_files: number;
    stale_files: number;
    skipped_files: number;
    symbols_extracted: number;
    duration_ms: number;
    memory_items_seeded: number;
}

export interface WorkspaceIndexStatusResponse {
    ever_indexed: boolean;
    file_count: number;
    stale_file_count: number;
    last_indexed_at: string | null;
    memory_item_count: number;
}

export interface IndexStatusResponse {
    indexed_files: number;
    stale_files: number;
    symbols_count: number;
    last_indexed_at: string | null;
    never_indexed: boolean;
    symbols_pending_summary?: number;
    summarizing?: boolean;
}

export interface RebuildMemoryResponse {
    rebuilt: boolean;
    total_files_scanned: number;
    indexed_files: number;
    symbols_extracted: number;
}

export interface WorkspaceProfileResponse {
    profile_yaml: string;
}

export interface WorkspaceProfileValidationResponse {
    valid: boolean;
    issues: string[];
}

export interface WorkspaceProfileExportResponse {
    exported_path: string;
}

export interface MemoryItemResponse {
    id: string;
    type: string;
    title: string;
    body: string;
    source: string;
    source_path: string | null;
    trust_level: number;
    stale: boolean;
    tags: Record<string, unknown> | unknown[] | null;
    created_at: string;
    updated_at: string;
}

export interface MemoryItemsResponse {
    items: MemoryItemResponse[];
}

export interface SuggestMemoryResponse {
    memory_item_id: string;
    pending_approval: boolean;
}

export interface PrivacyRecentCloudCallResponse {
    provider: string;
    model: string;
    input_tokens: number;
    output_tokens: number;
    estimated_cost: number;
    cache_hit: boolean;
    redacted_values: number;
}

export interface PrivacyDashboardResponse {
    local_only: string[];
    may_leave_machine: string[];
    never_sent: string[];
    pre_call_approval_summary: string;
    mcp_data_status: string;
    recent_cloud_calls: PrivacyRecentCloudCallResponse[];
}

export interface TaskModesResponse {
    modes: string[];
}

export interface ContextBuildRequest {
    task_description: string;
    suggested_files?: string[];
    file_overrides?: string[];
    mode?: string;
}

export interface ContextFileEntry {
    path: string;
    tokens: number;
    content?: string;
}

export interface ContextBuildResponse {
    files: ContextFileEntry[];
    rules: string[];
    skills: string[];
    total_tokens: number;
    estimated_cost_usd: number;
    quality_score?: {
        total: number;
        verdict: 'good' | 'acceptable' | 'poor' | 'rebuild';
        missing_signals: string[];
        has_primary_symbol: boolean;
        has_callers: boolean;
        has_related_tests: boolean;
        has_active_rules: boolean;
        has_recent_history: boolean;
        dedup_savings_pct: number;
        graph_expansion_files: number;
    };
    callers_not_in_context?: string[];
    repo_map?: string;
    commit_history?: string;
}

export interface ContextAssembleRequest {
    task_description: string;
    files_in_focus?: string[];
    task_type_hint?: string;
    workspace_root?: string;
    caller?: string;
    max_output_tokens?: number;
}

export interface ContextAssembleResponse {
    rendered_markdown: string;
    context_pack_hash: string;
    total_tokens: number;
    stale_exclusion_count: number;
    redacted_values_count: number;
    quality_verdict?: string;
}

export interface TaskHistoryEntry {
    task_id: string;
    description: string;
    mode: string;
    status: string;
    model_used: string | null;
    files_changed: number;
    cost_usd: number;
    created_at: string;
    duration_ms: number;
}

export interface TaskHistoryResponse {
    entries: TaskHistoryEntry[];
    total_count: number;
}

export interface CostDashboardEntry {
    date: string;
    provider: string;
    model: string;
    calls: number;
    tokens: number;
    cost_usd: number;
}

export interface CostDashboardResponse {
    period_days: number;
    total_cost_usd: number;
    total_calls: number;
    total_tokens: number;
    saved_usd: number;
    by_day: CostDashboardEntry[];
    by_model: CostDashboardEntry[];
}

export interface McpServer {
    name: string;
    status: string;
    tools: string[];
}

export interface McpToolsResponse {
    servers: McpServer[];
}

export interface ContextTemplateItemResponse {
    template_id: string;
    name: string;
    scope: string;
    path: string;
    selected: boolean;
}

export interface ContextTemplatesResponse {
    templates: ContextTemplateItemResponse[];
}

export interface ContextPackVersionResponse {
    version_id: string;
    task_run_id: string | null;
    pack_path: string;
    pack_hash: string;
    token_estimate: number | null;
    selected_model: string | null;
    template_id: string | null;
    created_at: string;
}

export interface ContextPackVersionsResponse {
    versions: ContextPackVersionResponse[];
}

export interface ContextPackDiffResponse {
    left_version_id: string;
    right_version_id: string;
    diff_text: string;
}

export interface PatchAssessmentResponse {
    patch_attempt_id: string;
    risk_level: string;
    rule_compliance_score: number;
    reasons: string[];
}

export interface ProviderCapabilityItemResponse {
    model_id: string;
    source: string;
    max_context_tokens: number | null;
    supports_tool_calling: boolean;
    supports_json_mode: boolean;
    estimated_cost_per_1m_input: number;
    estimated_cost_per_1m_output: number;
    privacy_level: string;
    allowed_task_types: string[];
    denied_task_types: string[];
    requires_approval: boolean;
}

export interface ProviderCapabilitiesResponse {
    items: ProviderCapabilityItemResponse[];
}

export interface LocalModelItem {
    model_id: string;
    source: string;
    max_context_tokens: number;
    supports_tools: boolean;
    cost_per_1m_input: number;
    status: string;
}

export interface LocalDiscoverResponse {
    models: LocalModelItem[];
    ollama_running: boolean;
    lmstudio_running: boolean;
}

export interface ReplayAICallResponse {
    ai_call_id: string;
    task_run_id: string;
    provider: string;
    model: string;
    purpose: string | null;
    context_pack_path: string | null;
    context_pack_text: string;
    replay_payload: Record<string, string | number | boolean | null>;
}

export interface SkillStoreItemResponse {
    skill_id: string;
    name: string;
    applies_when: string;
    enabled: boolean;
    version: number;
    conflict: boolean;
}

export interface SkillStoreListResponse {
    items: SkillStoreItemResponse[];
}

// --- Active Rules & Skills (merged view) ---

export interface ActiveRuleItem {
    rule_id: string;
    text: string;
    source_file: string;
    enabled: boolean;
    category: string;
}

export interface ActiveSkillItem {
    skill_id: string;
    name: string;
    framework: string | null;
    enabled: boolean;
}

export interface ActiveRulesResponse {
    global_rules: ActiveRuleItem[];
    project_rules: ActiveRuleItem[];
    detected_skills: ActiveSkillItem[];
}

export interface BackupMemoryResponse {
    backup_id: string;
    backup_path: string;
    item_count: number;
    created_at: string;
}

export interface RestoreMemoryResponse {
    restored_count: number;
}

export interface ToolSkillOptimizeResponse {
    suggested_tools: string[];
    suggested_skills: string[];
    reasons: string[];
}

export interface BudgetProfilesResponse {
    active_profile: string;
    monthly_budget_usd: number;
    effective_budget_usd: number;
    multiplier: number;
    profiles: Record<string, number>;
}

export interface BudgetStatusResponse {
    monthly_budget_usd: number;
    spent_usd: number;
    saved_usd: number;
    remaining_usd: number;
}

export interface PolicyPackItemResponse {
    pack_id: string;
    name: string;
    description: string;
    enforcement_mode: string;
    rules: string[];
    active: boolean;
    version: number;
}

export interface PolicyPacksResponse {
    items: PolicyPackItemResponse[];
}

export interface PolicyEvaluateResponse {
    allowed: boolean;
    decision: string;
    stage: string;
    active_pack_id: string | null;
    active_pack_name: string | null;
    violations: string[];
    applied_policies: string[];
}

export interface WorkspaceRootItemResponse {
    workspace_id: string;
    root_path: string;
    label: string;
    active: boolean;
}

export interface WorkspaceRootsResponse {
    items: WorkspaceRootItemResponse[];
}

export class BackendClient {
    private manager: BackendManager;

    constructor(manager: BackendManager) {
        this.manager = manager;
    }

    async get<T = unknown>(urlPath: string): Promise<T> {
        const result = await this.manager.request('GET', urlPath);
        return result as T;
    }

    async post<T = unknown>(urlPath: string, body?: unknown): Promise<T> {
        const result = await this.manager.request('POST', urlPath, body);
        return result as T;
    }

    async health(): Promise<HealthResponse> {
        const result = await this.manager.request('GET', '/v1/health');
        return result as HealthResponse;
    }

    async initWorkspace(): Promise<InitWorkspaceResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/init');
        return result as InitWorkspaceResponse;
    }

    async indexWorkspace(seedMemory: boolean = true): Promise<WorkspaceIndexResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/index', { seed_memory: seedMemory });
        return result as WorkspaceIndexResponse;
    }

    async rebuildMemory(summarizationBatchSize = 25): Promise<RebuildMemoryResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/rebuild-memory', {
            summarization_batch_size: summarizationBatchSize,
        });
        return result as RebuildMemoryResponse;
    }

    async summarizePending(summarizationBatchSize = 25): Promise<RebuildMemoryResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/summarize', {
            summarization_batch_size: summarizationBatchSize,
        });
        return result as RebuildMemoryResponse;
    }

    async getIndexStatus(): Promise<IndexStatusResponse>;
    async getIndexStatus(workspaceRoot: string): Promise<WorkspaceIndexStatusResponse>;
    async getIndexStatus(workspaceRoot?: string): Promise<IndexStatusResponse | WorkspaceIndexStatusResponse> {
        if (workspaceRoot === undefined) {
            const result = await this.manager.request('GET', '/v1/workspace/index/status');
            return result as IndexStatusResponse;
        }
        const result = await this.manager.request('GET', `/v1/workspace/index-status?workspace_root=${encodeURIComponent(workspaceRoot)}`);
        return result as WorkspaceIndexStatusResponse;
    }

    async getWorkspaceProfile(): Promise<WorkspaceProfileResponse> {
        const result = await this.manager.request('GET', '/v1/workspace/profile');
        return result as WorkspaceProfileResponse;
    }

    async rebuildWorkspaceProfile(): Promise<WorkspaceProfileResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/profile/rebuild');
        return result as WorkspaceProfileResponse;
    }

    async validateWorkspaceProfile(): Promise<WorkspaceProfileValidationResponse> {
        const result = await this.manager.request('GET', '/v1/workspace/profile/validate');
        return result as WorkspaceProfileValidationResponse;
    }

    async exportWorkspaceProfile(exportPath?: string): Promise<WorkspaceProfileExportResponse> {
        const payload = exportPath ? { export_path: exportPath } : {};
        const result = await this.manager.request('POST', '/v1/workspace/profile/export', payload);
        return result as WorkspaceProfileExportResponse;
    }

    async listMemoryItems(filterName: string): Promise<MemoryItemsResponse> {
        const result = await this.manager.request(
            'GET',
            `/v1/memory/items?filter_name=${encodeURIComponent(filterName)}`,
        );
        return result as MemoryItemsResponse;
    }

    async approveMemoryItem(itemId: string): Promise<void> {
        await this.manager.request('POST', `/v1/memory/items/${encodeURIComponent(itemId)}/approve`);
    }

    async rejectMemoryItem(itemId: string): Promise<void> {
        await this.manager.request('POST', `/v1/memory/items/${encodeURIComponent(itemId)}/reject`);
    }

    async editMemoryItem(itemId: string, title: string, body: string): Promise<void> {
        await this.manager.request('PUT', `/v1/memory/items/${encodeURIComponent(itemId)}`, { title, body });
    }

    async deleteMemoryItem(itemId: string): Promise<void> {
        await this.manager.request('DELETE', `/v1/memory/items/${encodeURIComponent(itemId)}`);
    }

    async rebuildMemoryItem(itemId: string): Promise<void> {
        await this.manager.request('POST', `/v1/memory/items/${encodeURIComponent(itemId)}/rebuild`);
    }

    async suggestMemoryUpdate(title: string, body: string): Promise<SuggestMemoryResponse> {
        const result = await this.manager.request('POST', '/v1/memory/suggestions', {
            title,
            body,
            source: 'ai_suggestion',
        });
        return result as SuggestMemoryResponse;
    }

    async getPrivacyDashboard(): Promise<PrivacyDashboardResponse> {
        const result = await this.manager.request('GET', '/v1/privacy/dashboard');
        return result as PrivacyDashboardResponse;
    }

    async getActiveRules(): Promise<ActiveRulesResponse> {
        const result = await this.manager.request('GET', '/v1/rules/active');
        return result as ActiveRulesResponse;
    }

    async getTaskModes(): Promise<TaskModesResponse> {
        const result = await this.manager.request('GET', '/v1/task/modes');
        return result as TaskModesResponse;
    }

    async buildContextPack(request: ContextBuildRequest): Promise<ContextBuildResponse> {
        const result = await this.manager.request('POST', '/v1/context/build', request);
        return result as ContextBuildResponse;
    }

    async assembleContext(request: ContextAssembleRequest): Promise<ContextAssembleResponse> {
        const result = await this.manager.request('POST', '/v1/context/assemble', request);
        return result as ContextAssembleResponse;
    }

    async getTaskHistory(limit = 20): Promise<TaskHistoryResponse> {
        const result = await this.manager.request('GET', `/v1/task/history?limit=${limit}`);
        return result as TaskHistoryResponse;
    }

    async getCostDashboard(days = 30): Promise<CostDashboardResponse> {
        const result = await this.manager.request('GET', `/v1/cost/dashboard?days=${days}`);
        return result as CostDashboardResponse;
    }

    async listMcpTools(): Promise<McpToolsResponse> {
        const result = await this.manager.request('GET', '/v1/mcp/tools');
        return result as McpToolsResponse;
    }

    async listContextTemplates(): Promise<ContextTemplatesResponse> {
        const result = await this.manager.request('GET', '/v1/context/templates');
        return result as ContextTemplatesResponse;
    }

    async saveContextTemplate(name: string, content: string, scope = 'workspace'): Promise<string> {
        const result = await this.manager.request('POST', '/v1/context/templates', {
            name,
            content,
            scope,
        });
        return (result as { template_id: string }).template_id;
    }

    async selectContextTemplate(templateId: string): Promise<void> {
        await this.manager.request('POST', '/v1/context/templates/select', {
            template_id: templateId,
        });
    }

    async storeContextPackVersion(
        contextPackText: string,
        taskRunId?: string,
        selectedModel?: string,
        templateId?: string,
    ): Promise<ContextPackVersionResponse> {
        const result = await this.manager.request('POST', '/v1/context/versions', {
            task_run_id: taskRunId,
            context_pack_text: contextPackText,
            selected_model: selectedModel,
            template_id: templateId,
        });
        return result as ContextPackVersionResponse;
    }

    async listContextPackVersions(taskRunId?: string): Promise<ContextPackVersionsResponse> {
        const suffix = taskRunId ? `?task_run_id=${encodeURIComponent(taskRunId)}` : '';
        const result = await this.manager.request('GET', `/v1/context/versions${suffix}`);
        return result as ContextPackVersionsResponse;
    }

    async diffContextPackVersions(
        leftVersionId: string,
        rightVersionId: string,
    ): Promise<ContextPackDiffResponse> {
        const result = await this.manager.request('POST', '/v1/context/versions/diff', {
            left_version_id: leftVersionId,
            right_version_id: rightVersionId,
        });
        return result as ContextPackDiffResponse;
    }

    async listProviderCapabilities(): Promise<ProviderCapabilitiesResponse> {
        const result = await this.manager.request('GET', '/v1/providers/capabilities');
        return result as ProviderCapabilitiesResponse;
    }

    async getLLMMode(): Promise<{ mode: string; model_id: string; copilot_available: boolean; cloud_available: boolean; local_available: boolean }> {
        const result = await this.manager.request('GET', '/v1/config/llm-mode');
        return result as { mode: string; model_id: string; copilot_available: boolean; cloud_available: boolean; local_available: boolean };
    }

    async setLLMMode(mode: string): Promise<{ ok: boolean; mode: string }> {
        const result = await this.manager.request('POST', '/v1/config/llm-mode', { mode });
        return result as { ok: boolean; mode: string };
    }

    async discoverLocalProviders(workspaceRoot?: string): Promise<LocalDiscoverResponse> {
        const qs = workspaceRoot ? `?workspace_root=${encodeURIComponent(workspaceRoot)}` : '';
        const result = await this.manager.request('GET', `/v1/providers/local-discover${qs}`);
        return result as LocalDiscoverResponse;
    }

    async replayAICall(aiCallId: string): Promise<ReplayAICallResponse> {
        const result = await this.manager.request('GET', `/v1/ai/replay/${encodeURIComponent(aiCallId)}`);
        return result as ReplayAICallResponse;
    }

    async listSkillStore(): Promise<SkillStoreListResponse> {
        const result = await this.manager.request('GET', '/v1/skills/store');
        return result as SkillStoreListResponse;
    }

    async upsertSkillStoreItem(
        name: string,
        appliesWhen: string,
        rules: string[],
        tools: string[],
    ): Promise<SkillStoreItemResponse> {
        const result = await this.manager.request('POST', '/v1/skills/store', {
            name,
            applies_when: appliesWhen,
            rules,
            tools,
        });
        return result as SkillStoreItemResponse;
    }

    async backupMemory(): Promise<BackupMemoryResponse> {
        const result = await this.manager.request('POST', '/v1/memory/backup');
        return result as BackupMemoryResponse;
    }

    async restoreMemory(backupPath: string): Promise<RestoreMemoryResponse> {
        const result = await this.manager.request('POST', '/v1/memory/restore', {
            backup_path: backupPath,
        });
        return result as RestoreMemoryResponse;
    }

    async optimizeToolsAndSkills(
        taskText: string,
        availableTools: string[],
    ): Promise<ToolSkillOptimizeResponse> {
        const result = await this.manager.request('POST', '/v1/optimizer/tools-skills', {
            task_text: taskText,
            available_tools: availableTools,
        });
        return result as ToolSkillOptimizeResponse;
    }

    async getUsageStats(): Promise<{
        symbols_indexed: number;
        symbols_summarized: number;
        memory_items_total: number;
        memory_items_learned: number;
        session_queries: number;
    }> {
        const result = await this.manager.request('GET', '/v1/usage/stats');
        return result as {
            symbols_indexed: number;
            symbols_summarized: number;
            memory_items_total: number;
            memory_items_learned: number;
            session_queries: number;
        };
    }

    async getBudgetProfiles(): Promise<BudgetProfilesResponse> {
        const result = await this.manager.request('GET', '/v1/budget/profiles');
        return result as BudgetProfilesResponse;
    }

    async getBudgetStatus(): Promise<BudgetStatusResponse> {
        const result = await this.manager.request('GET', '/v1/cost/budget/status');
        return result as BudgetStatusResponse;
    }

    async setBudgetProfile(profile: string): Promise<BudgetProfilesResponse> {
        const result = await this.manager.request('POST', '/v1/budget/profiles', { profile });
        return result as BudgetProfilesResponse;
    }

    async listPolicyPacks(): Promise<PolicyPacksResponse> {
        const result = await this.manager.request('GET', '/v1/policies/packs');
        return result as PolicyPacksResponse;
    }

    async savePolicyPack(
        name: string,
        description: string,
        enforcementMode: 'enforce' | 'advisory',
        rules: string[],
    ): Promise<PolicyPackItemResponse> {
        const result = await this.manager.request('POST', '/v1/policies/packs', {
            name,
            description,
            enforcement_mode: enforcementMode,
            rules,
        });
        return result as PolicyPackItemResponse;
    }

    async activatePolicyPack(packId: string): Promise<void> {
        await this.manager.request('POST', '/v1/policies/packs/activate', { pack_id: packId });
    }

    async evaluatePolicy(
        stage: string,
        taskText: string,
        filesChanged: string[],
        selectedModel?: string,
    ): Promise<PolicyEvaluateResponse> {
        const result = await this.manager.request('POST', '/v1/policies/evaluate', {
            stage,
            task_text: taskText,
            files_changed: filesChanged,
            selected_model: selectedModel,
        });
        return result as PolicyEvaluateResponse;
    }

    async listWorkspaceRoots(): Promise<WorkspaceRootsResponse> {
        const result = await this.manager.request('GET', '/v1/workspaces');
        return result as WorkspaceRootsResponse;
    }

    async addWorkspaceRoot(
        rootPath: string,
        label?: string,
        activate = false,
    ): Promise<WorkspaceRootItemResponse> {
        const result = await this.manager.request('POST', '/v1/workspaces', {
            root_path: rootPath,
            label,
            activate,
        });
        return result as WorkspaceRootItemResponse;
    }

    async activateWorkspaceRoot(workspaceId: string): Promise<WorkspaceRootItemResponse> {
        const result = await this.manager.request('POST', '/v1/workspaces/activate', {
            workspace_id: workspaceId,
        });
        return result as WorkspaceRootItemResponse;
    }
}
