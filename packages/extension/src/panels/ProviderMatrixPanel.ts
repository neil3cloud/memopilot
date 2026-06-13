import * as vscode from 'vscode';
import { MemoPilotPanelBase } from './MemoPilotPanelBase';
import { BackendClient, ProviderCapabilityItemResponse } from '../BackendClient';

export class ProviderMatrixPanel extends MemoPilotPanelBase {
    public static readonly viewType = 'memopilot.providerMatrix';
    private static instance: ProviderMatrixPanel | undefined;

    private client: BackendClient | undefined;
    private providers: ProviderCapabilityItemResponse[] = [];
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
            const result = await this.client.listProviderCapabilities();
            this.providers = result.items;
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
            .pm-container { max-width: 900px; padding: 16px; }
            .pm-container h2 { margin-bottom: 4px; }
            .pm-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 12px; }
            .pm-table th, .pm-table td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--vscode-panel-border, rgba(255,255,255,0.1)); }
            .pm-table th { font-size: 11px; text-transform: uppercase; opacity: 0.7; white-space: nowrap; }
            .pm-table td { white-space: nowrap; }
            .pm-table tr:hover td { background: var(--vscode-list-hoverBackground); }
            .pm-summary { margin-bottom: 16px; opacity: 0.7; font-size: 13px; }
            .pm-check { color: #4caf50; }
            .pm-dash { opacity: 0.4; }
            .pm-approval-yes { color: #ff9800; font-weight: 500; }
        `;
    }

    protected getContent(): string {
        if (this.loading) return '<p>Loading providers...</p>';
        if (this.error) return `<p class="error">${this.escapeHtml(this.error)}</p>`;
        if (this.providers.length === 0) {
            return '<p>No provider capabilities configured. Add providers via <code>POST /v1/providers/capabilities</code>.</p>';
        }

        const rows = this.providers.map(p => {
            const privacyColor = p.privacy_level === 'local' ? '#4caf50' : p.privacy_level === 'private_cloud' ? '#ff9800' : '#f44336';
            const tools = p.supports_tool_calling ? '<span class="pm-check">✓</span>' : '<span class="pm-dash">—</span>';
            const json = p.supports_json_mode ? '<span class="pm-check">✓</span>' : '<span class="pm-dash">—</span>';
            const approval = p.requires_approval ? '<span class="pm-approval-yes">⚠ Yes</span>' : 'No';
            const ctx = p.max_context_tokens ? `${(p.max_context_tokens / 1000).toFixed(0)}K` : '<span class="pm-dash">—</span>';
            const costIn = `$${p.estimated_cost_per_1m_input.toFixed(2)}`;
            const costOut = `$${p.estimated_cost_per_1m_output.toFixed(2)}`;

            return `<tr>
                <td><strong>${this.escapeHtml(p.model_id)}</strong></td>
                <td>${this.escapeHtml(p.source)}</td>
                <td><span style="color:${privacyColor}">${this.escapeHtml(p.privacy_level)}</span></td>
                <td>${ctx}</td>
                <td>${tools}</td>
                <td>${json}</td>
                <td>${costIn}</td>
                <td>${costOut}</td>
                <td>${approval}</td>
            </tr>`;
        }).join('');

        return `
            <div class="pm-container">
            <h2>Provider Capability Matrix</h2>
            <p class="pm-summary">${this.providers.length} model(s) configured</p>

            <table class="pm-table">
                <thead>
                    <tr>
                        <th>Model</th>
                        <th>Source</th>
                        <th>Privacy</th>
                        <th>Context</th>
                        <th>Tools</th>
                        <th>JSON</th>
                        <th>Cost/1M In</th>
                        <th>Cost/1M Out</th>
                        <th>Approval</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            </div>
        `;
    }

    protected handleMessage(_message: { type: string }): void {
        // Future: refresh, filter by privacy level
    }
}
