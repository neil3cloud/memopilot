import * as vscode from 'vscode';
import { MemoPilotPanelBase } from './MemoPilotPanelBase';
import { BackendClient, CostDashboardResponse } from '../BackendClient';

export class CostDashboardPanel extends MemoPilotPanelBase {
    public static readonly viewType = 'memopilot.costDashboard';
    private static instance: CostDashboardPanel | undefined;

    private client: BackendClient | undefined;
    private data: CostDashboardResponse | undefined;
    private loading = false;
    private error: string | undefined;

    public static createOrShow(extensionUri: vscode.Uri, client: BackendClient | undefined): void {
        const column = vscode.ViewColumn.One;
        if (CostDashboardPanel.instance) {
            CostDashboardPanel.instance.client = client;
            CostDashboardPanel.instance.panel.reveal(column);
            CostDashboardPanel.instance.loadData();
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            CostDashboardPanel.viewType,
            'MemoPilot: Cost Dashboard',
            column,
            { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'resources')] },
        );

        CostDashboardPanel.instance = new CostDashboardPanel(panel, extensionUri);
        CostDashboardPanel.instance.client = client;
        CostDashboardPanel.instance.loadData();
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
        super(panel, extensionUri);
        this.panel.onDidDispose(() => {
            CostDashboardPanel.instance = undefined;
        });
    }

    private async loadData(): Promise<void> {
        if (!this.client) {
            this.error = 'Backend not connected';
            this.update();
            return;
        }

        this.loading = true;
        this.error = undefined;
        this.update();

        try {
            this.data = await this.client.getCostDashboard(30);
            this.loading = false;
        } catch (err: unknown) {
            this.error = err instanceof Error ? err.message : String(err);
            this.loading = false;
        }
        this.update();
    }

    private update(): void {
        this.panel.webview.html = this.renderHtml(this.getContent(), '', this.getStyles());
    }

    private getStyles(): string {
        return `
            .cd-container { max-width: 800px; padding: 16px; }
            .cd-container h2 { margin-bottom: 4px; }
            .cd-container h3 { margin-top: 24px; margin-bottom: 12px; font-size: 14px; }
            .cd-period { font-size: 12px; opacity: 0.7; margin-bottom: 16px; }
            .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
            .metric-card { padding: 16px; background: var(--vscode-sideBar-background, rgba(255,255,255,0.05)); border: 1px solid var(--vscode-panel-border, rgba(255,255,255,0.1)); border-radius: 6px; text-align: center; }
            .metric-value { font-size: 22px; font-weight: 700; margin-bottom: 4px; color: var(--vscode-editor-foreground); }
            .metric-label { font-size: 11px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.5px; }
            .savings { color: #4caf50; }
            .cd-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
            .cd-table th, .cd-table td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--vscode-panel-border, rgba(255,255,255,0.1)); }
            .cd-table th { font-size: 11px; text-transform: uppercase; opacity: 0.7; }
            .cd-table tr:hover td { background: var(--vscode-list-hoverBackground); }
            .chart-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
            .chart-label { width: 45px; font-size: 11px; text-align: right; opacity: 0.7; }
            .chart-bar { height: 18px; background: var(--vscode-button-background); border-radius: 3px; min-width: 2px; }
            .chart-value { font-size: 11px; opacity: 0.7; }
        `;
    }

    protected getContent(): string {
        if (this.loading) {
            return '<div class="cd-container"><p style="opacity:0.7;">Loading cost data...</p></div>';
        }
        if (this.error) {
            return `<div class="cd-container"><p style="color:var(--mp-error);">${this.escapeHtml(this.error)}</p></div>`;
        }
        if (!this.data) {
            return '<div class="cd-container"><p style="opacity:0.7;">No cost data available.</p></div>';
        }

        const { total_cost_usd, total_calls, total_tokens, saved_usd, period_days, by_model } = this.data;

        const modelsHtml = by_model.map(m => `
            <tr>
                <td><strong>${this.escapeHtml(m.model)}</strong></td>
                <td>${this.escapeHtml(m.provider)}</td>
                <td>${m.calls}</td>
                <td>${m.tokens.toLocaleString()}</td>
                <td>$${m.cost_usd.toFixed(4)}</td>
            </tr>
        `).join('');

        // Simple bar chart for daily costs
        const maxDailyCost = Math.max(...this.data.by_day.map(d => d.cost_usd), 0.001);
        const chartHtml = this.data.by_day.slice(0, 14).reverse().map(d => {
            const barWidth = Math.max(2, Math.round((d.cost_usd / maxDailyCost) * 200));
            const dateShort = d.date.slice(5); // MM-DD
            return `<div class="chart-row">
                <span class="chart-label">${dateShort}</span>
                <span class="chart-bar" style="width:${barWidth}px"></span>
                <span class="chart-value">$${d.cost_usd.toFixed(4)}</span>
            </div>`;
        }).join('');

        return `
            <div class="cd-container">
                <h2>Cost Dashboard</h2>
                <p class="cd-period">Last ${period_days} days</p>

                <div class="metrics">
                    <div class="metric-card">
                        <div class="metric-value">$${total_cost_usd.toFixed(2)}</div>
                        <div class="metric-label">Total Spent</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value savings">$${saved_usd.toFixed(2)}</div>
                        <div class="metric-label">Saved (Local)</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">${total_calls}</div>
                        <div class="metric-label">AI Calls</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">${(total_tokens / 1000).toFixed(0)}K</div>
                        <div class="metric-label">Tokens</div>
                    </div>
                </div>

                <h3>Daily Cost (Last 14 Days)</h3>
                <div>${chartHtml}</div>

                <h3>Cost by Model</h3>
                <table class="cd-table">
                    <thead><tr><th>Model</th><th>Provider</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>
                    <tbody>${modelsHtml}</tbody>
                </table>
            </div>
        `;
    }

    protected handleMessage(_message: { type: string }): void {
        // Future: handle refresh, date range changes
    }
}
