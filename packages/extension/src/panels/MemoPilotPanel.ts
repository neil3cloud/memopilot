import * as vscode from 'vscode';
import * as crypto from 'crypto';
import { BackendClient } from '../BackendClient';

export class MemoPilotPanel {
    public static currentPanel: MemoPilotPanel | undefined;
    private static readonly viewType = 'memopilotPanel';

    private readonly panel: vscode.WebviewPanel;
    private readonly extensionUri: vscode.Uri;
    private readonly client: BackendClient | undefined;
    private disposables: vscode.Disposable[] = [];

    public static createOrShow(extensionUri: vscode.Uri, client: BackendClient | undefined): void {
        const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;

        if (MemoPilotPanel.currentPanel) {
            MemoPilotPanel.currentPanel.panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            MemoPilotPanel.viewType,
            'MemoPilot',
            column,
            {
                enableScripts: true,
                localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'resources')],
            },
        );

        MemoPilotPanel.currentPanel = new MemoPilotPanel(panel, extensionUri, client);
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri, client: BackendClient | undefined) {
        this.panel = panel;
        this.extensionUri = extensionUri;
        this.client = client;

        this.update();

        this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
        this.panel.onDidChangeViewState(() => {
            if (this.panel.visible) {
                this.update();
            }
        }, null, this.disposables);
    }

    private async update(): Promise<void> {
        const webview = this.panel.webview;
        const nonce = crypto.randomBytes(16).toString('hex');

        let statusHtml = '<p class="status error">Backend not connected</p>';
        if (this.client) {
            try {
                const health = await this.client.health();
                statusHtml = `<p class="status ok">Backend connected — API v${health.api_version}, Schema v${health.schema_version}</p>`;
            } catch {
                statusHtml = '<p class="status error">Backend unavailable</p>';
            }
        }

        const workspaceName = vscode.workspace.workspaceFolders?.[0]?.name ?? 'No workspace';

        webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}';">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MemoPilot</title>
    <style nonce="${nonce}">
        body { font-family: var(--vscode-font-family); padding: 16px; color: var(--vscode-foreground); }
        h1 { font-size: 1.4em; margin-bottom: 0.5em; }
        .status { padding: 8px 12px; border-radius: 4px; margin: 8px 0; }
        .status.ok { background: var(--vscode-testing-iconPassed); color: #fff; }
        .status.error { background: var(--vscode-testing-iconFailed); color: #fff; }
        .section { margin: 16px 0; padding: 12px; border: 1px solid var(--vscode-widget-border); border-radius: 4px; }
        .section h2 { font-size: 1.1em; margin: 0 0 8px 0; }
        .placeholder { color: var(--vscode-descriptionForeground); font-style: italic; }
    </style>
</head>
<body>
    <h1>MemoPilot</h1>
    <p><strong>Workspace:</strong> ${this.escapeHtml(workspaceName)}</p>
    ${statusHtml}
    <div class="section">
        <h2>Context Pack</h2>
        <p class="placeholder">Context pack preview will appear here when a task is analyzed.</p>
    </div>
    <div class="section">
        <h2>Rules & Skills</h2>
        <p class="placeholder">Active rules and skills will be displayed after workspace indexing.</p>
    </div>
    <div class="section">
        <h2>Cost Guard</h2>
        <p class="placeholder">Cost estimation and model routing info will appear here.</p>
    </div>
    <div class="section">
        <h2>Approval Controls</h2>
        <p class="placeholder">Patch approval workflow will be available when patches are generated.</p>
    </div>
</body>
</html>`;
    }

    private escapeHtml(text: string): string {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    private dispose(): void {
        MemoPilotPanel.currentPanel = undefined;
        this.panel.dispose();
        for (const d of this.disposables) {
            d.dispose();
        }
        this.disposables = [];
    }
}
