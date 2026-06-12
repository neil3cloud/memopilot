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

export interface EvidenceClassifyResponse {
    source_type: string;
    trust_level: number;
    extraction_method: string;
}

export class BackendClient {
    private manager: BackendManager;

    constructor(manager: BackendManager) {
        this.manager = manager;
    }

    async health(): Promise<HealthResponse> {
        const result = await this.manager.request('GET', '/v1/health');
        return result as HealthResponse;
    }

    async initWorkspace(): Promise<InitWorkspaceResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/init');
        return result as InitWorkspaceResponse;
    }

    async rebuildMemory(): Promise<RebuildMemoryResponse> {
        const result = await this.manager.request('POST', '/v1/workspace/rebuild-memory');
        return result as RebuildMemoryResponse;
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

    async getTaskModes(): Promise<TaskModesResponse> {
        const result = await this.manager.request('GET', '/v1/task/modes');
        return result as TaskModesResponse;
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
}
