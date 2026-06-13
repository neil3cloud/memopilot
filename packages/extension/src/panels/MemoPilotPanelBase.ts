import * as vscode from 'vscode';
import * as crypto from 'crypto';
import type { WebviewOutboundMessage, WebviewInboundMessage } from './types';

/**
 * Abstract base class for all MemoPilot webview panels.
 * Provides: nonce generation, strict CSP, VS Code theme variables, message bridge.
 */
export abstract class MemoPilotPanelBase implements vscode.Disposable {
    protected readonly panel: vscode.WebviewPanel;
    protected readonly extensionUri: vscode.Uri;
    protected disposables: vscode.Disposable[] = [];

    constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
        this.panel = panel;
        this.extensionUri = extensionUri;

        this.panel.onDidDispose(() => this.dispose(), null, this.disposables);

        this.panel.webview.onDidReceiveMessage(
            (msg: WebviewOutboundMessage) => this.handleMessage(msg),
            null,
            this.disposables,
        );

        this.panel.onDidChangeViewState(() => {
            if (this.panel.visible) {
                this.onDidBecomeVisible();
            }
        }, null, this.disposables);
    }

    /** Send a typed message from extension to webview */
    protected postMessage(message: WebviewInboundMessage): void {
        void this.panel.webview.postMessage(message);
    }

    /** Subclass implements to handle messages from webview */
    protected abstract handleMessage(message: WebviewOutboundMessage): void;

    /** Called when panel becomes visible again (e.g., user switches back) */
    protected onDidBecomeVisible(): void {
        // Subclasses can override to refresh data
    }

    /** Generate the full HTML for the webview */
    protected renderHtml(bodyContent: string, extraScript: string = ''): string {
        const nonce = crypto.randomBytes(16).toString('hex');
        const webview = this.panel.webview;
        const cspSource = webview.cspSource;
        // Replace placeholder nonce in extra scripts
        const resolvedExtraScript = extraScript.replace(/nonce="REPLACED_BY_BASE"/g, `nonce="${nonce}"`);

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${cspSource} 'nonce-${nonce}'; script-src 'nonce-${nonce}'; font-src ${cspSource};">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MemoPilot</title>
    <style nonce="${nonce}">
        :root {
            --mp-bg: var(--vscode-editor-background);
            --mp-fg: var(--vscode-editor-foreground);
            --mp-border: var(--vscode-widget-border);
            --mp-accent: var(--vscode-focusBorder);
            --mp-success: var(--vscode-testing-iconPassed);
            --mp-error: var(--vscode-testing-iconFailed);
            --mp-warning: var(--vscode-editorWarning-foreground);
            --mp-muted: var(--vscode-descriptionForeground);
            --mp-sidebar-bg: var(--vscode-sideBar-background);
            --mp-sidebar-fg: var(--vscode-sideBar-foreground);
            --mp-input-bg: var(--vscode-input-background);
            --mp-input-border: var(--vscode-input-border);
            --mp-button-bg: var(--vscode-button-background);
            --mp-button-fg: var(--vscode-button-foreground);
            --mp-button-hover: var(--vscode-button-hoverBackground);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--vscode-font-size);
            color: var(--mp-fg);
            background: var(--mp-bg);
            height: 100vh;
            overflow: hidden;
        }
        .mp-shell {
            display: flex;
            height: 100vh;
        }
        .mp-nav {
            width: 200px;
            min-width: 200px;
            background: var(--mp-sidebar-bg);
            border-right: 1px solid var(--mp-border);
            overflow-y: auto;
            padding: 8px 0;
        }
        .mp-nav-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 12px;
            cursor: pointer;
            color: var(--mp-sidebar-fg);
            font-size: 12px;
            border-left: 3px solid transparent;
            transition: background 0.1s;
        }
        .mp-nav-item:hover {
            background: var(--vscode-list-hoverBackground);
        }
        .mp-nav-item.active {
            background: var(--vscode-list-activeSelectionBackground);
            color: var(--vscode-list-activeSelectionForeground);
            border-left-color: var(--mp-accent);
        }
        .mp-nav-item.disabled {
            opacity: 0.5;
            cursor: default;
        }
        .mp-nav-item .icon {
            width: 16px;
            text-align: center;
            flex-shrink: 0;
        }
        .mp-nav-item .badge {
            margin-left: auto;
            background: var(--mp-accent);
            color: var(--mp-button-fg);
            border-radius: 8px;
            padding: 0 6px;
            font-size: 10px;
        }
        .mp-content {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
        }
        .mp-header {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-bottom: 1px solid var(--mp-border);
            background: var(--mp-sidebar-bg);
        }
        .mp-header .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }
        .mp-header .status-dot.connected { background: var(--mp-success); }
        .mp-header .status-dot.disconnected { background: var(--mp-error); }
        .mp-header .status-text {
            font-size: 11px;
            color: var(--mp-muted);
        }
        .mp-placeholder {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            text-align: center;
            color: var(--mp-muted);
        }
        .mp-placeholder h3 {
            margin-bottom: 8px;
            font-size: 14px;
            color: var(--mp-fg);
        }
        .mp-placeholder p {
            max-width: 400px;
            line-height: 1.5;
        }
        .mp-status-panel { padding: 12px 0; }
        .mp-status-panel .info-row {
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid var(--mp-border);
        }
        .mp-status-panel .info-label { color: var(--mp-muted); }
        .mp-status-panel .info-value { font-weight: 500; }
        .mp-progress {
            margin: 12px 0;
            height: 4px;
            background: var(--mp-input-bg);
            border-radius: 2px;
            overflow: hidden;
        }
        .mp-progress-bar {
            height: 100%;
            background: var(--mp-accent);
            transition: width 0.3s;
        }
    </style>
    ${resolvedExtraScript}
</head>
<body>
    ${bodyContent}
    <script nonce="${nonce}">
        const vscode = acquireVsCodeApi();
        function postMsg(type, payload) {
            vscode.postMessage(payload !== undefined ? { type, payload } : { type });
        }
        function navigate(viewId) {
            postMsg('navigate', { viewId });
        }
        window.addEventListener('message', function(event) {
            const msg = event.data;
            if (window.handleMessage) {
                window.handleMessage(msg);
            }
        });
        // Signal ready
        postMsg('ready');
    </script>
</body>
</html>`;
    }

    /** Escape HTML to prevent XSS in dynamic content */
    protected escapeHtml(text: string): string {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    dispose(): void {
        for (const d of this.disposables) {
            d.dispose();
        }
        this.disposables = [];
    }
}
