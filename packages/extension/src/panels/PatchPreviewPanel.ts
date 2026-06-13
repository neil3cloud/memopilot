import * as vscode from 'vscode';
import { MemoPilotPanelBase } from './MemoPilotPanelBase';
import { BackendClient, GeneratePatchResponse, FilePatch, ValidateResponse } from '../BackendClient';

export class PatchPreviewPanel extends MemoPilotPanelBase {
    public static readonly viewType = 'memopilot.patchPreview';
    private static instance: PatchPreviewPanel | undefined;

    private patchData: GeneratePatchResponse | undefined;
    private validationResult: ValidateResponse | undefined;
    private client: BackendClient | undefined;

    public static createOrShow(
        extensionUri: vscode.Uri,
        client: BackendClient | undefined,
        patch: GeneratePatchResponse,
    ): PatchPreviewPanel {
        const column = vscode.ViewColumn.Two;
        if (PatchPreviewPanel.instance) {
            PatchPreviewPanel.instance.patchData = patch;
            PatchPreviewPanel.instance.validationResult = undefined;
            PatchPreviewPanel.instance.client = client;
            PatchPreviewPanel.instance.panel.reveal(column);
            PatchPreviewPanel.instance.update();
            return PatchPreviewPanel.instance;
        }

        const panel = vscode.window.createWebviewPanel(
            PatchPreviewPanel.viewType,
            'MemoPilot: Patch Preview',
            column,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        PatchPreviewPanel.instance = new PatchPreviewPanel(panel, extensionUri);
        PatchPreviewPanel.instance.patchData = patch;
        PatchPreviewPanel.instance.client = client;
        PatchPreviewPanel.instance.update();
        return PatchPreviewPanel.instance;
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
        super(panel, extensionUri);
        this.panel.onDidDispose(() => {
            PatchPreviewPanel.instance = undefined;
        });
    }

    public setValidation(result: ValidateResponse): void {
        this.validationResult = result;
        this.update();
    }

    private update(): void {
        this.panel.webview.html = this.renderHtml(this.getContent());
    }

    protected getContent(): string {
        if (!this.patchData) {
            return '<p>No patch data available.</p>';
        }

        const { patches, summary, estimated_risk, model_used, tokens_used, cost_usd } = this.patchData;

        const riskColor = estimated_risk === 'high' ? '#f44336' : estimated_risk === 'medium' ? '#ff9800' : '#4caf50';

        const patchesHtml = patches.map((p, i) => this.renderFilePatch(p, i)).join('');

        const validationHtml = this.validationResult
            ? this.renderValidation(this.validationResult)
            : '';

        return `
            <style>
                .summary-bar { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; padding: 12px; background: var(--vscode-editor-background); border: 1px solid var(--vscode-panel-border); border-radius: 4px; }
                .summary-item { display: flex; flex-direction: column; }
                .summary-label { font-size: 11px; opacity: 0.7; text-transform: uppercase; }
                .summary-value { font-size: 14px; font-weight: 600; }
                .risk-badge { padding: 2px 8px; border-radius: 3px; color: white; font-size: 12px; }
                .patch-file { margin-bottom: 16px; border: 1px solid var(--vscode-panel-border); border-radius: 4px; overflow: hidden; }
                .patch-header { padding: 8px 12px; background: var(--vscode-sideBar-background); font-weight: 600; display: flex; justify-content: space-between; }
                .patch-action { font-size: 11px; padding: 2px 6px; border-radius: 3px; text-transform: uppercase; }
                .patch-action-modify { background: #1976d2; color: white; }
                .patch-action-create { background: #388e3c; color: white; }
                .patch-action-delete { background: #d32f2f; color: white; }
                .diff-block { padding: 8px 12px; font-family: var(--vscode-editor-font-family); font-size: 12px; white-space: pre-wrap; overflow-x: auto; background: var(--vscode-editor-background); }
                .diff-add { color: #4caf50; }
                .diff-remove { color: #f44336; }
                .diff-header { color: #64b5f6; }
                .actions { margin-top: 20px; display: flex; gap: 12px; }
                .btn { padding: 8px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: 600; }
                .btn-approve { background: #388e3c; color: white; }
                .btn-reject { background: #d32f2f; color: white; }
                .btn-approve:hover { background: #2e7d32; }
                .btn-reject:hover { background: #c62828; }
                .validation-section { margin-top: 16px; padding: 12px; border: 1px solid var(--vscode-panel-border); border-radius: 4px; }
                .check-pass { color: #4caf50; }
                .check-fail { color: #f44336; }
                .check-warn { color: #ff9800; }
            </style>

            <h2>Patch Preview</h2>
            <p>${summary}</p>

            <div class="summary-bar">
                <div class="summary-item">
                    <span class="summary-label">Files</span>
                    <span class="summary-value">${patches.length}</span>
                </div>
                <div class="summary-item">
                    <span class="summary-label">Risk</span>
                    <span class="summary-value"><span class="risk-badge" style="background:${riskColor}">${estimated_risk}</span></span>
                </div>
                <div class="summary-item">
                    <span class="summary-label">Model</span>
                    <span class="summary-value">${model_used}</span>
                </div>
                <div class="summary-item">
                    <span class="summary-label">Tokens</span>
                    <span class="summary-value">${tokens_used.toLocaleString()}</span>
                </div>
                <div class="summary-item">
                    <span class="summary-label">Cost</span>
                    <span class="summary-value">$${cost_usd.toFixed(4)}</span>
                </div>
            </div>

            ${patchesHtml}
            ${validationHtml}

            <div class="actions">
                <button class="btn btn-approve" onclick="sendMessage('approve')">✓ Approve & Apply</button>
                <button class="btn btn-reject" onclick="sendMessage('reject')">✗ Reject</button>
            </div>

            <script nonce="REPLACED_BY_BASE">
                const vscode = acquireVsCodeApi();
                function sendMessage(action) {
                    vscode.postMessage({ type: action });
                }
            </script>
        `;
    }

    private renderFilePatch(patch: FilePatch, index: number): string {
        const actionClass = `patch-action-${patch.action}`;
        const diffHtml = this.colorDiff(patch.diff);
        return `
            <div class="patch-file">
                <div class="patch-header">
                    <span>${patch.path}</span>
                    <span class="patch-action ${actionClass}">${patch.action}</span>
                </div>
                <div class="diff-block">${diffHtml}</div>
            </div>
        `;
    }

    private colorDiff(diff: string): string {
        return diff
            .split('\n')
            .map(line => {
                if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) {
                    return `<span class="diff-header">${this.escapeHtml(line)}</span>`;
                } else if (line.startsWith('+')) {
                    return `<span class="diff-add">${this.escapeHtml(line)}</span>`;
                } else if (line.startsWith('-')) {
                    return `<span class="diff-remove">${this.escapeHtml(line)}</span>`;
                }
                return this.escapeHtml(line);
            })
            .join('\n');
    }

    private renderValidation(result: ValidateResponse): string {
        const statusIcon = result.overall_status === 'pass' ? '✓' : result.overall_status === 'warn' ? '⚠' : '✗';
        const statusClass = `check-${result.overall_status}`;
        const checksHtml = result.checks
            .map(c => `<div class="check-${c.status}">• ${c.name}: ${c.message}</div>`)
            .join('');

        return `
            <div class="validation-section">
                <h3 class="${statusClass}">${statusIcon} Validation: ${result.overall_status.toUpperCase()}</h3>
                ${checksHtml}
            </div>
        `;
    }

    protected handleMessage(message: { type: string }): void {
        if (message.type === 'approve') {
            vscode.commands.executeCommand('memopilot.approvePatch');
        } else if (message.type === 'reject') {
            vscode.commands.executeCommand('memopilot.rejectPatch');
        }
    }
}
