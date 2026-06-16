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

export interface TaskAnalyzeRequest {
    description: string;
    constraints?: string[];
    mode?: string | null;
    notes?: string | null;
}

export interface TaskAnalyzeResponse {
    intent_summary: string;
    suggested_files: string[];
    applicable_rules: string[];
    estimated_complexity: string;
    suggested_mode: string;
    task_type: string;
    risk: string;
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

export interface ModelRouteRequest {
    context_tokens: number;
    task_type?: string;
    privacy_level?: string;
    preferred_model?: string;
}

export interface ModelChoice {
    model_id: string;
    provider: string;
    cost_estimate_usd: number;
    reasons: string[];
    fits_context: boolean;
}

export interface ModelRouteResponse {
    recommended: ModelChoice;
    alternatives: ModelChoice[];
    budget_check: { allowed: boolean; remaining_usd: number };
}

export interface GeneratePatchRequest {
    task_description: string;
    context_files?: string[];
    mode?: string;
    model_id?: string;
    dry_run?: boolean;
    task_run_id?: string;
    context_pack_hash?: string;
    workspace_root?: string;
}

export interface FilePatch {
    path: string;
    action: 'modify' | 'create' | 'delete';
    original_content: string | null;
    new_content: string | null;
    diff: string;
}

export interface GeneratePatchResponse {
    patches: FilePatch[];
    total_files_changed: number;
    summary: string;
    estimated_risk: string;
    model_used: string;
    tokens_used: number;
    cost_usd: number;
}

export interface ValidateRequest {
    patches: Array<{ path: string; action: string; diff: string }>;
    checks?: string[];
}

export interface ValidationCheck {
    name: string;
    status: 'pass' | 'fail' | 'warn' | 'skipped';
    message: string;
}

export interface ValidateResponse {
    overall_status: 'pass' | 'fail' | 'warn';
    checks: ValidationCheck[];
    can_apply: boolean;
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

export interface AttachEvidenceResponse {
    evidence_id: string;
    source_type: string;
    trust_level: number;
    extraction_method: string;
    extraction_status: string;
    findings: string[];
    redacted_values: number;
    source_path: string | null;
}

export interface EvidenceColumnsPreviewResponse {
    source_type: string;
    columns: string[];
    suggested_mapping: Record<string, string>;
    requires_confirmation: boolean;
}

export interface EvidenceBoardItemResponse {
    evidence_id: string;
    source_type: string;
    source_path: string | null;
    source_url: string | null;
    trust_level: number;
    extraction_method: string;
    extraction_status: string;
    redacted_values: number;
    findings: string[];
}

export interface EvidenceBoardResponse {
    items: EvidenceBoardItemResponse[];
}

export interface RunInvestigationResponse {
    context_pack: string;
    context_pack_path: string;
    impacted_files: string[];
    related_tests: string[];
    missing_test_coverage: string[];
    evidence_count: number;
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

export interface EvidenceClassifyResponse {
    source_type: string;
    trust_level: number;
    extraction_method: string;
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

export interface LocalFlowStep {
    id?: string;
    title?: string;
    action: string;
    stage?: string;
    available_tools?: string[];
}

export interface LocalFlowItemResponse {
    flow_id: string;
    name: string;
    description: string;
    enabled: boolean;
    steps: Record<string, string | string[] | boolean>[];
}

export interface LocalFlowsResponse {
    items: LocalFlowItemResponse[];
}

export interface RunLocalFlowResponse {
    run_id: string;
    flow_id: string;
    flow_name: string;
    status: string;
    steps: Record<string, string | boolean | string[]>[];
    blocked_reason: string | null;
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

    async indexWorkspace(): Promise<WorkspaceIndexResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/index');
        return result as WorkspaceIndexResponse;
    }

    async rebuildMemory(): Promise<RebuildMemoryResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/rebuild-memory');
        return result as RebuildMemoryResponse;
    }

    async indexWorkspace(seedMemory: boolean = true): Promise<WorkspaceIndexResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/index', { seed_memory: seedMemory });
        return result as WorkspaceIndexResponse;
    }

    async getIndexStatus(workspaceRoot: string): Promise<WorkspaceIndexStatusResponse> {
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

    async analyzeTask(request: TaskAnalyzeRequest): Promise<TaskAnalyzeResponse> {
        const result = await this.manager.request('POST', '/v1/task/analyze', request);
        return result as TaskAnalyzeResponse;
    }

    async buildContextPack(request: ContextBuildRequest): Promise<ContextBuildResponse> {
        const result = await this.manager.request('POST', '/v1/context/build', request);
        return result as ContextBuildResponse;
    }

    async routeModel(request: ModelRouteRequest): Promise<ModelRouteResponse> {
        const result = await this.manager.request('POST', '/v1/model/route', request);
        return result as ModelRouteResponse;
    }

    async generatePatch(request: GeneratePatchRequest): Promise<GeneratePatchResponse> {
        const result = await this.manager.request('POST', '/v1/task/generate-patch', request);
        return result as GeneratePatchResponse;
    }

    /**
     * Open an SSE stream for a task run and call onToken for each TOKEN event.
     * Returns a dispose function that cancels the stream.
     */
    openTokenStream(taskRunId: string, onToken: (token: string) => void): () => void {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const http = require('http') as typeof import('http');
        const url = new URL(`/v1/task/${encodeURIComponent(taskRunId)}/stream`, this.manager.baseUrl);
        let req: import('http').ClientRequest | undefined;
        let cancelled = false;

        const connect = () => {
            if (cancelled) { return; }
            req = http.request(
                {
                    hostname: '127.0.0.1',
                    port: parseInt(url.port || '80', 10),
                    path: url.pathname + url.search,
                    headers: {
                        'X-Agent-Token': this.manager.authToken,
                        'Accept': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                    },
                },
                (res) => {
                    let buffer = '';
                    res.on('data', (chunk: Buffer) => {
                        buffer += chunk.toString('utf8');
                        const lines = buffer.split('\n');
                        buffer = lines.pop() ?? '';
                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                try {
                                    const event = JSON.parse(line.slice(6)) as { type: string; token?: string };
                                    if (event.type === 'TOKEN' && event.token) {
                                        onToken(event.token);
                                    }
                                } catch { /* skip malformed */ }
                            }
                        }
                    });
                },
            );
            req.on('error', () => { /* backend ended */ });
            req.end();
        };

        connect();

        return () => {
            cancelled = true;
            req?.destroy();
        };
    }

    async validatePatches(request: ValidateRequest): Promise<ValidateResponse> {
        const result = await this.manager.request('POST', '/v1/task/validate', request);
        return result as ValidateResponse;
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

    async previewEvidenceColumns(evidencePath: string): Promise<EvidenceColumnsPreviewResponse> {
        const result = await this.manager.request('POST', '/v1/investigation/evidence/columns', {
            evidence_path: evidencePath,
        });
        return result as EvidenceColumnsPreviewResponse;
    }

    async attachEvidence(
        evidencePath: string,
        columnMapping?: Record<string, string>,
    ): Promise<AttachEvidenceResponse> {
        const result = await this.manager.request('POST', '/v1/investigation/evidence/attach', {
            evidence_path: evidencePath,
            column_mapping: columnMapping,
        });
        return result as AttachEvidenceResponse;
    }

    async getEvidenceBoard(taskRunId?: string): Promise<EvidenceBoardResponse> {
        const suffix = taskRunId ? `?task_run_id=${encodeURIComponent(taskRunId)}` : '';
        const result = await this.manager.request('GET', `/v1/investigation/evidence${suffix}`);
        return result as EvidenceBoardResponse;
    }

    async runInvestigation(
        title: string,
        description: string,
        acceptanceCriteria: string[],
        taskRunId?: string,
    ): Promise<RunInvestigationResponse> {
        const result = await this.manager.request('POST', '/v1/investigation/run', {
            title,
            description,
            acceptance_criteria: acceptanceCriteria,
            task_run_id: taskRunId,
        });
        return result as RunInvestigationResponse;
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

    async assessPatchRiskAndCompliance(
        taskRunId: string,
        diffText: string,
        filesChanged: string[],
        activeRules: string[],
    ): Promise<PatchAssessmentResponse> {
        const result = await this.manager.request('POST', '/v1/patch/assess', {
            task_run_id: taskRunId,
            diff_text: diffText,
            files_changed: filesChanged,
            active_rules: activeRules,
        });
        return result as PatchAssessmentResponse;
    }

    async listProviderCapabilities(): Promise<ProviderCapabilitiesResponse> {
        const result = await this.manager.request('GET', '/v1/providers/capabilities');
        return result as ProviderCapabilitiesResponse;
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

    async classifyEvidenceSource(
        evidencePath?: string,
        sourceUrl?: string,
    ): Promise<EvidenceClassifyResponse> {
        const result = await this.manager.request('POST', '/v1/investigation/evidence/classify', {
            evidence_path: evidencePath,
            source_url: sourceUrl,
        });
        return result as EvidenceClassifyResponse;
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

    async listLocalFlows(): Promise<LocalFlowsResponse> {
        const result = await this.manager.request('GET', '/v1/flows/local');
        return result as LocalFlowsResponse;
    }

    async saveLocalFlow(
        name: string,
        description: string,
        steps: LocalFlowStep[],
    ): Promise<LocalFlowItemResponse> {
        const result = await this.manager.request('POST', '/v1/flows/local', {
            name,
            description,
            steps,
        });
        return result as LocalFlowItemResponse;
    }

    async runLocalFlow(
        flowId: string,
        taskText: string,
        filesChanged: string[],
        selectedModel?: string,
    ): Promise<RunLocalFlowResponse> {
        const result = await this.manager.request('POST', '/v1/flows/local/run', {
            flow_id: flowId,
            task_text: taskText,
            files_changed: filesChanged,
            selected_model: selectedModel,
        });
        return result as RunLocalFlowResponse;
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
