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

    async attachEvidence(evidencePath: string): Promise<AttachEvidenceResponse> {
        const result = await this.manager.request('POST', '/v1/investigation/evidence/attach', {
            evidence_path: evidencePath,
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
}
