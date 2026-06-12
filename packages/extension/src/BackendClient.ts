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
}
