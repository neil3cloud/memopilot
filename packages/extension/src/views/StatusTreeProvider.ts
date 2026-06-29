import * as vscode from 'vscode';
import { IndexStatusResponse, ProviderCapabilityItemResponse } from '../BackendClient';

type BackendStatus = 'connecting' | 'connected' | 'error' | 'no-workspace';

export class StatusTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private status: BackendStatus = 'connecting';
    private message = 'Starting backend...';
    private indexStatus: IndexStatusResponse | undefined;
    private providerCapabilities: ProviderCapabilityItemResponse[] = [];
    private llmMode: string = 'local';
    private llmModeModelId: string = '';
    private copilotAvailable: boolean = false;

    setStatus(status: BackendStatus, message: string): void {
        this.status = status;
        this.message = message;
        if (status !== 'connected') {
            this.indexStatus = undefined;
        }
        this._onDidChangeTreeData.fire(undefined);
    }

    updateIndexStatus(status: IndexStatusResponse | undefined): void {
        this.indexStatus = status;
        this._onDidChangeTreeData.fire(undefined);
    }

    updateProviderStatus(items: ProviderCapabilityItemResponse[]): void {
        this.providerCapabilities = items;
        this._onDidChangeTreeData.fire(undefined);
    }

    updateLLMMode(mode: string, modelId: string, copilotAvailable: boolean): void {
        this.llmMode = mode;
        this.llmModeModelId = modelId;
        this.copilotAvailable = copilotAvailable;
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        const iconMap: Record<BackendStatus, string> = {
            connecting: 'sync~spin',
            connected: 'pass',
            error: 'error',
            'no-workspace': 'info',
        };

        const backendItem = new vscode.TreeItem(this.message);
        backendItem.contextValue = this.status;
        backendItem.iconPath = new vscode.ThemeIcon(iconMap[this.status]);

        if (this.status !== 'connected') {
            return [backendItem];
        }

        const indexItem = new vscode.TreeItem(this.buildIndexLabel());
        indexItem.contextValue = this.indexStatus?.never_indexed ? 'index-not-indexed' : 'index-ready';
        indexItem.iconPath = new vscode.ThemeIcon(this.indexStatus?.never_indexed ? 'warning' : 'zap');

        const providerItem = new vscode.TreeItem(this.buildProviderLabel());
        providerItem.contextValue = 'provider-summary';
        providerItem.iconPath = new vscode.ThemeIcon('symbol-interface');
        providerItem.command = {
            command: 'memopilot.configureProviders',
            title: 'Configure Providers',
        };

        const modeItem = new vscode.TreeItem(this.buildLLMModeLabel());
        modeItem.contextValue = 'llm-mode';
        modeItem.iconPath = new vscode.ThemeIcon(this.llmModeIcon());
        modeItem.tooltip = 'Click to switch LLM mode (copilot / cloud / local)';
        modeItem.command = {
            command: 'memopilot.switchLLMMode',
            title: 'Switch LLM Mode',
        };

        return [backendItem, indexItem, providerItem, modeItem];
    }

    private buildIndexLabel(): string {
        if (!this.indexStatus) {
            return 'Index: Checking status...';
        }
        if (this.indexStatus.never_indexed) {
            return 'Index: Not indexed - run MemoPilot: Index Workspace';
        }

        const staleSuffix = this.indexStatus.stale_files > 0
            ? ` (${this.indexStatus.stale_files} stale)`
            : '';
        const lastRun = this.formatLastRun(this.indexStatus.last_indexed_at);
        return `Index: ${this.indexStatus.indexed_files} files, ${this.indexStatus.symbols_count} symbols${staleSuffix} (${lastRun})`;
    }

    private formatLastRun(lastIndexedAt: string | null): string {
        if (!lastIndexedAt) {
            return 'last run unknown';
        }

        const parsed = new Date(lastIndexedAt.replace(' ', 'T') + 'Z');
        if (Number.isNaN(parsed.getTime())) {
            return `last run ${lastIndexedAt}`;
        }

        const deltaMs = Date.now() - parsed.getTime();
        const deltaMinutes = Math.floor(deltaMs / 60000);
        if (deltaMinutes < 1) {
            return 'just now';
        }
        if (deltaMinutes < 60) {
            return `${deltaMinutes} min ago`;
        }
        const deltaHours = Math.floor(deltaMinutes / 60);
        if (deltaHours < 24) {
            return `${deltaHours} hr ago`;
        }
        const deltaDays = Math.floor(deltaHours / 24);
        return `${deltaDays} day${deltaDays === 1 ? '' : 's'} ago`;
    }

    private buildLLMModeLabel(): string {
        const labels: Record<string, string> = {
            copilot: `LLM Mode: Copilot (${this.llmModeModelId || 'probing...'})`,
            cloud: 'LLM Mode: Cloud provider',
            local: 'LLM Mode: Local (LM Studio / Ollama)',
        };
        return labels[this.llmMode] ?? `LLM Mode: ${this.llmMode}`;
    }

    private llmModeIcon(): string {
        const icons: Record<string, string> = {
            copilot: 'copilot',
            cloud: 'cloud',
            local: 'server',
        };
        return icons[this.llmMode] ?? 'symbol-misc';
    }

    private buildProviderLabel(): string {
        if (!this.providerCapabilities.length) {
            return 'LLM Touch Points: Not configured - run MemoPilot: Configure LLM Touch Points';
        }

        const bySource = new Map<string, string[]>();
        for (const item of this.providerCapabilities) {
            const bucket = bySource.get(item.source) ?? [];
            bucket.push(item.model_id);
            bySource.set(item.source, bucket);
        }

        const segments = Array.from(bySource.entries()).map(([source, models]) => {
            const uniqueModels = Array.from(new Set(models)).slice(0, 2);
            return `${source}: ${uniqueModels.join(', ')}`;
        });
        return `LLM Touch Points: ${segments.join(' · ')}`;
    }
}
