import * as vscode from 'vscode';
import { MemoPilotPanelBase } from './MemoPilotPanelBase';
import { BackendClient, LocalModelItem, ProviderCapabilityItemResponse } from '../BackendClient';

export class ProviderMatrixPanel extends MemoPilotPanelBase {
    public static readonly viewType = 'memopilot.providerMatrix';
    private static instance: ProviderMatrixPanel | undefined;

    private client: BackendClient | undefined;
    private providers: ProviderCapabilityItemResponse[] = [];
    private localModels: LocalModelItem[] = [];
    private ollamaRunning = false;
    private lmstudioRunning = false;
    private copilotModels: string[] = [];
    private loading = false;
    private error: string | undefined;

    public static createOrShow(extensionUri: vscode.Uri, client: BackendClient | undefined): void {
        const column = vscode.ViewColumn.One;
        if (ProviderMatrixPanel.instance) {
            ProviderMatrixPanel.instance.client = client;
            ProviderMatrixPanel.instance.panel.reveal(column);
            ProviderMatrixPanel.instance.loadData();
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            ProviderMatrixPanel.viewType,
            'MemoPilot: Provider Matrix',
            column,
            { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'resources')] },
        );

        ProviderMatrixPanel.instance = new ProviderMatrixPanel(panel, extensionUri);
        ProviderMatrixPanel.instance.client = client;
        ProviderMatrixPanel.instance.loadData();
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
        super(panel, extensionUri);
        this.panel.onDidDispose(() => {
            ProviderMatrixPanel.instance = undefined;
        });
        this.panel.webview.onDidReceiveMessage((msg: { type: string }) => {
            this.handleMessage(msg);
        });
    }

    private async loadData(): Promise<void> {
        if (!this.client) {
            this.error = 'Backend not connected';
            this.update();
            return;
        }

        this.loading = true;
        this.update();

        try {
            const [caps, local] = await Promise.all([
                this.client.listProviderCapabilities(),
                this.client.discoverLocalProviders(
                    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
                ).catch(() => ({ models: [], ollama_running: false, lmstudio_running: false })),
            ]);
            this.providers = caps.items;
            this.localModels = local.models;
            this.ollamaRunning = local.ollama_running;
            this.lmstudioRunning = local.lmstudio_running;

            // Detect authenticated Copilot models via VS Code LM API
            try {
                const lm = (vscode as unknown as Record<string, unknown>).lm as
                    | { selectChatModels: (selector: object) => Thenable<{ id: string; name?: string }[]> }
                    | undefined;
                const models = lm && typeof lm.selectChatModels === 'function'
                    ? await lm.selectChatModels({ vendor: 'copilot' })
                    : [];
                this.copilotModels = (models ?? []).map(m => m.id ?? m.name ?? 'copilot');
            } catch {
                this.copilotModels = [];
            }

            this.error = undefined;
        } catch (err: unknown) {
            this.error = err instanceof Error ? err.message : String(err);
        }
        this.loading = false;
        this.update();
    }

    private update(): void {
        this.panel.webview.html = this.renderHtml(this.getContent(), '', this.getStyles());
    }

    private getStyles(): string {
        return `
            .pm-container { max-width: 960px; padding: 16px; }
            .pm-container h2 { margin-bottom: 4px; }
            .pm-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 12px; }
            .pm-table th, .pm-table td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--vscode-panel-border, rgba(255,255,255,0.1)); }
            .pm-table th { font-size: 11px; text-transform: uppercase; opacity: 0.7; white-space: nowrap; }
            .pm-table td { white-space: nowrap; }
            .pm-table tr:hover td { background: var(--vscode-list-hoverBackground); }
            .pm-summary { margin-bottom: 8px; opacity: 0.7; font-size: 13px; }
            .pm-check { color: #4caf50; }
            .pm-dash { opacity: 0.4; }
            .pm-approval-yes { color: #ff9800; font-weight: 500; }
            .pm-local-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; background: #1b5e20; color: #a5d6a7; }
            .pm-status-row { display: flex; gap: 12px; margin-bottom: 12px; font-size: 12px; }
            .pm-status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 4px; vertical-align: middle; }
            .pm-status-on { background: #4caf50; }
            .pm-status-off { background: #555; }
            .pm-section-title { font-size: 12px; text-transform: uppercase; opacity: 0.6; margin: 20px 0 4px; letter-spacing: 0.05em; }
            .pm-refresh-btn { padding: 4px 12px; font-size: 12px; cursor: pointer; background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; border-radius: 3px; margin-bottom: 8px; }
            .pm-refresh-btn:hover { background: var(--vscode-button-hoverBackground); }
        `;
    }

    protected getContent(): string {
        if (this.loading) { return '<p>Loading providers...</p>'; }
        if (this.error) { return `<p class="error">${this.escapeHtml(this.error)}</p>`; }

        const ollamaStatus = this.ollamaRunning ? 'on' : 'off';
        const lmsStatus = this.lmstudioRunning ? 'on' : 'off';

        const copilotStatus = this.copilotModels.length > 0 ? 'on' : 'off';
        const copilotLabel = this.copilotModels.length > 0
            ? `authenticated (${this.copilotModels.length} model${this.copilotModels.length > 1 ? 's' : ''})`
            : 'not available';

        const statusRow = `
            <div class="pm-status-row">
                <span><span class="pm-status-dot pm-status-${copilotStatus}"></span> GitHub Copilot: ${copilotLabel}</span>
                <span><span class="pm-status-dot pm-status-${ollamaStatus}"></span> Ollama: ${this.ollamaRunning ? 'running' : 'not detected'}</span>
                <span><span class="pm-status-dot pm-status-${lmsStatus}"></span> LM Studio: ${this.lmstudioRunning ? 'running' : 'not detected'}</span>
            </div>
        `;

        const copilotRows = this.copilotModels.map(id => `
                    <tr>
                        <td><strong>${this.escapeHtml(id)}</strong> <span class="pm-local-badge" style="background:#0e3a5e;color:#90caf9">copilot</span></td>
                        <td>GitHub Copilot</td>
                        <td>varies</td>
                        <td><span class="pm-check">✓</span></td>
                        <td>$0.00</td>
                    </tr>`).join('');

        const localSection = (this.localModels.length > 0 || this.copilotModels.length > 0) ? `
            <div class="pm-section-title">Local / Host Models (free)</div>
            <table class="pm-table">
                <thead>
                    <tr><th>Model</th><th>Source</th><th>Context</th><th>Tools</th><th>Cost/1M</th></tr>
                </thead>
                <tbody>
                ${copilotRows}
                ${this.localModels.map(m => `
                    <tr>
                        <td><strong>${this.escapeHtml(m.model_id)}</strong> <span class="pm-local-badge">local</span></td>
                        <td>${this.escapeHtml(m.source)}</td>
                        <td>${(m.max_context_tokens / 1000).toFixed(0)}K</td>
                        <td>${m.supports_tools ? '<span class="pm-check">✓</span>' : '<span class="pm-dash">—</span>'}</td>
                        <td>$0.00</td>
                    </tr>`).join('')}
                </tbody>
            </table>
        ` : '';

        const cloudSection = this.providers.length > 0 ? `
            <div class="pm-section-title">Cloud / Configured Providers</div>
            <table class="pm-table">
                <thead>
                    <tr>
                        <th>Model</th><th>Source</th><th>Privacy</th><th>Context</th>
                        <th>Tools</th><th>JSON</th><th>Cost/1M In</th><th>Cost/1M Out</th><th>Approval</th>
                    </tr>
                </thead>
                <tbody>
                ${this.providers.map(p => {
                    const privacyColor = p.privacy_level === 'local' ? '#4caf50' : p.privacy_level === 'private_cloud' ? '#ff9800' : '#f44336';
                    const ctx = p.max_context_tokens ? `${(p.max_context_tokens / 1000).toFixed(0)}K` : '<span class="pm-dash">—</span>';
                    return `<tr>
                        <td><strong>${this.escapeHtml(p.model_id)}</strong></td>
                        <td>${this.escapeHtml(p.source)}</td>
                        <td><span style="color:${privacyColor}">${this.escapeHtml(p.privacy_level)}</span></td>
                        <td>${ctx}</td>
                        <td>${p.supports_tool_calling ? '<span class="pm-check">✓</span>' : '<span class="pm-dash">—</span>'}</td>
                        <td>${p.supports_json_mode ? '<span class="pm-check">✓</span>' : '<span class="pm-dash">—</span>'}</td>
                        <td>$${p.estimated_cost_per_1m_input.toFixed(2)}</td>
                        <td>$${p.estimated_cost_per_1m_output.toFixed(2)}</td>
                        <td>${p.requires_approval ? '<span class="pm-approval-yes">⚠ Yes</span>' : 'No'}</td>
                    </tr>`;
                }).join('')}
                </tbody>
            </table>
        ` : '';

        const noProviders = this.providers.length === 0 && this.localModels.length === 0 && this.copilotModels.length === 0
            ? '<p>No providers detected. Sign in to GitHub Copilot, start Ollama, or add API keys in <code>.memopilot/config.yaml</code>.</p>'
            : '';

        return `
            <div class="pm-container">
                <h2>Provider Capability Matrix</h2>
                <button class="pm-refresh-btn" onclick="acquireVsCodeApi().postMessage({type:'refresh'})">↻ Refresh local</button>
                ${statusRow}
                ${localSection}
                ${cloudSection}
                ${noProviders}
            </div>
        `;
    }

    protected handleMessage(message: { type: string }): void {
        if (message.type === 'refresh') {
            void this.loadData();
        }
    }
}
