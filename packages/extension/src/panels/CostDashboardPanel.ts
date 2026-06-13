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
            { enableScripts: true, retainContextWhenHidden: true },
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
        this.panel.webview.html = this.renderHtml(this.getContent());
    }

    protected getContent(): string {
        if (this.loading) {
            return '<p>Loading cost data...</p>';
        }
        if (this.error) {
            return `<p class="error">$(error) ${this.escapeHtml(this.error)}</p>`;
        }
        if (!this.data) {
            return '<p>No cost data available.</p>';
        }

        const { total_cost_usd, total_calls, total_tokens, saved_usd, period_days, by_model } = this.data;

        const modelsHtml = by_model.map(m => `
            <tr>
                <td>${this.escapeHtml(m.model)}</td>
                <td>${this.escapeHtml(m.provider)}</td>
                <td>${m.calls}</td>
                <td>${m.tokens.toLocaleString()}</td>
                <td>$${m.cost_usd.toFixed(4)}</td>
            </tr>
        `).join('');

        // Simple ASCII bar chart for daily costs
        const maxDailyCost = Math.max(...this.data.by_day.map(d => d.cost_usd), 0.001);
        const chartHtml = this.data.by_day.slice(0, 14).reverse().map(d => {
            const barWidth = Math.max(1, Math.round((d.cost_usd / maxDailyCost) * 200));
            const dateShort = d.date.slice(5); // MM-DD
            return `<div class="chart-row">
                <span class="chart-label">${dateShort}</span>
                <span class="chart-bar" style="width:${barWidth}px"></span>
                <span class="chart-value">$${d.cost_usd.toFixed(4)}</span>
            </div>`;
        }).join('');

        return `
            <style>
                .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
                .metric-card { padding: 16px; background: var(--vscode-editor-background); border: 1px solid var(--vscode-panel-border); border-radius: 6px; text-align: center; }
                .metric-value { font-size: 24px; font-weight: 700; margin-bottom: 4px; }
                .metric-label { font-size: 11px; opacity: 0.7; text-transform: uppercase; }
                .savings { color: #4caf50; }
                table { width: 100%; border-collapse: collapse; margin-top: 12px; }
                th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--vscode-panel-border); }
                th { font-size: 11px; text-transform: uppercase; opacity: 0.7; }
                .chart-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
                .chart-label { width: 50px; font-size: 11px; text-align: right; }
                .chart-bar { height: 16px; background: var(--vscode-button-background); border-radius: 2px; }
                .chart-value { font-size: 11px; opacity: 0.7; }
                h3 { margin-top: 24px; }
            </style>

            <h2>Cost Dashboard</h2>
            <p>Last ${period_days} days</p>

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
            <div class="chart">${chartHtml}</div>

            <h3>Cost by Model</h3>
            <table>
                <thead><tr><th>Model</th><th>Provider</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>
                <tbody>${modelsHtml}</tbody>
            </table>

            <script nonce="REPLACED_BY_BASE">
                const vscode = acquireVsCodeApi();
            </script>
        `;
    }

    protected handleMessage(_message: { type: string }): void {
        // Future: handle refresh, date range changes
    }
}
