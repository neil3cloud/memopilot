import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';
import { MemoPilotPanelBase } from './MemoPilotPanelBase';
import { NAVIGATION_ITEMS } from './navigationItems';
import type { WebviewOutboundMessage, WorkspaceStatusDTO, NavigationItemDTO } from './types';

/**
 * Main MemoPilot panel — shell with navigation sidebar and content area.
 * Handles view switching, backend status display, and message routing.
 */
export class MemoPilotPanel extends MemoPilotPanelBase {
    public static currentPanel: MemoPilotPanel | undefined;
    private static readonly viewType = 'memopilotPanel';

    private client: BackendClient | undefined;
    private activeViewId: string = 'workspace-status';

    public static createOrShow(extensionUri: vscode.Uri, client: BackendClient | undefined): void {
        const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;

        if (MemoPilotPanel.currentPanel) {
            MemoPilotPanel.currentPanel.client = client;
            MemoPilotPanel.currentPanel.panel.reveal(column);
            MemoPilotPanel.currentPanel.refresh();
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            MemoPilotPanel.viewType,
            'MemoPilot',
            column,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'resources')],
            },
        );

        MemoPilotPanel.currentPanel = new MemoPilotPanel(panel, extensionUri, client);
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri, client: BackendClient | undefined) {
        super(panel, extensionUri);
        this.client = client;

        this.panel.onDidDispose(() => {
            MemoPilotPanel.currentPanel = undefined;
        }, null, this.disposables);

        this.render();
    }

    /** Update client reference (e.g., after backend reconnect) */
    public setClient(client: BackendClient | undefined): void {
        this.client = client;
        this.refresh();
    }

    /** Re-render the full panel */
    public refresh(): void {
        this.render();
    }

    protected handleMessage(message: WebviewOutboundMessage): void {
        switch (message.type) {
            case 'ready':
                this.sendNavigationItems();
                this.sendActiveView();
                this.sendWorkspaceStatus();
                break;
            case 'navigate':
                this.activeViewId = message.payload.viewId;
                this.sendActiveView();
                this.sendViewContent(message.payload.viewId);
                break;
            case 'request-workspace-status':
                this.sendWorkspaceStatus();
                break;
            case 'restart-backend':
                void vscode.commands.executeCommand('memopilot.restartBackend');
                break;
            default:
                break;
        }
    }

    protected onDidBecomeVisible(): void {
        this.sendWorkspaceStatus();
    }

    private render(): void {
        this.panel.webview.html = this.renderHtml(this.buildShellHtml(), this.buildExtraScript());
    }

    private async sendWorkspaceStatus(): Promise<void> {
        const status = await this.getWorkspaceStatus();
        this.postMessage({ type: 'workspace-status', payload: status });
    }

    private sendNavigationItems(): void {
        this.postMessage({ type: 'navigation-items', payload: NAVIGATION_ITEMS });
    }

    private sendActiveView(): void {
        this.postMessage({ type: 'active-view', payload: { viewId: this.activeViewId } });
    }

    private sendViewContent(viewId: string): void {
        const html = this.getViewContentHtml(viewId);
        this.postMessage({ type: 'view-content', payload: { viewId, html } });
    }

    private async getWorkspaceStatus(): Promise<WorkspaceStatusDTO> {
        const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
        const base: WorkspaceStatusDTO = {
            connected: false,
            apiVersion: null,
            schemaVersion: null,
            workspaceName: workspaceFolder?.name ?? 'No workspace',
            workspaceRoot: workspaceFolder?.uri.fsPath ?? '',
            indexed: false,
            indexingPhase: 'idle',
            filesScanned: 0,
            totalFiles: 0,
            symbolsExtracted: 0,
        };

        if (!this.client) {
            return base;
        }

        try {
            const health = await this.client.health();
            base.connected = health.status === 'ok';
            base.apiVersion = health.api_version;
            base.schemaVersion = health.schema_version;
            base.indexed = true;
        } catch {
            base.connected = false;
        }

        return base;
    }

    private getViewContentHtml(viewId: string): string {
        const item = NAVIGATION_ITEMS.find(n => n.id === viewId);
        if (!item) { return ''; }

        if (!item.enabled) {
            return `
                <div class="mp-placeholder">
                    <h3>${this.escapeHtml(item.label)}</h3>
                    <p>Coming soon — ${this.escapeHtml(item.description)}</p>
                </div>`;
        }

        // Enabled views get content rendered based on viewId
        switch (viewId) {
            case 'workspace-status':
                return this.getWorkspaceStatusHtml();
            case 'local-memory':
                return this.getSimpleRedirectHtml('Memory Manager', 'Use the Memory Manager tree view in the sidebar, or run "MemoPilot: Review Memory" from the command palette.');
            case 'rules-skills':
                return this.getSimpleRedirectHtml('Rules & Skills', 'Use the Rules & Skills tree view in the sidebar, or run "MemoPilot: Open Rules" from the command palette.');
            case 'memory-manager':
                return this.getSimpleRedirectHtml('Memory Manager', 'Use the Memory Manager tree view in the sidebar for full CRUD operations.');
            case 'workspace-profile':
                return this.getSimpleRedirectHtml('Workspace Profile', 'Use the Workspace Profile tree view in the sidebar, or run "MemoPilot: Open Workspace Profile" from the command palette.');
            case 'privacy-boundary':
                return this.getSimpleRedirectHtml('Privacy Dashboard', 'Use the Privacy Dashboard tree view in the sidebar, or run "MemoPilot: Show Privacy Dashboard" from the command palette.');
            case 'evidence-board':
                return this.getSimpleRedirectHtml('Evidence Board', 'Use the Evidence Board tree view in the sidebar, or run "MemoPilot: Attach Evidence" from the command palette.');
            default:
                return `<div class="mp-placeholder"><p>View not implemented yet.</p></div>`;
        }
    }

    private getWorkspaceStatusHtml(): string {
        return `
            <div class="mp-status-panel" id="status-content">
                <p style="color: var(--mp-muted);">Loading workspace status...</p>
            </div>`;
    }

    private getSimpleRedirectHtml(title: string, message: string): string {
        return `
            <div class="mp-placeholder">
                <h3>${this.escapeHtml(title)}</h3>
                <p>${this.escapeHtml(message)}</p>
            </div>`;
    }

    private buildShellHtml(): string {
        const navItemsHtml = NAVIGATION_ITEMS.map((item) => {
            const classes = ['mp-nav-item'];
            if (item.id === this.activeViewId) { classes.push('active'); }
            if (!item.enabled) { classes.push('disabled'); }
            const badge = item.badge ? `<span class="badge">${this.escapeHtml(item.badge)}</span>` : '';
            return `<div class="${classes.join(' ')}" data-view-id="${this.escapeHtml(item.id)}" onclick="navigate('${this.escapeHtml(item.id)}')">
                <span class="icon">${this.escapeHtml(item.icon)}</span>
                <span>${this.escapeHtml(item.label)}</span>
                ${badge}
            </div>`;
        }).join('\n');

        return `
        <div class="mp-shell">
            <nav class="mp-nav">
                <div style="padding: 8px 12px; font-weight: bold; font-size: 13px; border-bottom: 1px solid var(--mp-border); margin-bottom: 4px;">
                    MemoPilot
                </div>
                ${navItemsHtml}
            </nav>
            <main class="mp-content" id="mp-content">
                ${this.getViewContentHtml(this.activeViewId)}
            </main>
        </div>`;
    }

    private buildExtraScript(): string {
        return `<script nonce="REPLACED_BY_BASE">
        window.handleMessage = function(msg) {
            switch (msg.type) {
                case 'workspace-status':
                    renderWorkspaceStatus(msg.payload);
                    break;
                case 'active-view':
                    setActiveNav(msg.payload.viewId);
                    break;
                case 'view-content':
                    document.getElementById('mp-content').innerHTML = msg.payload.html;
                    if (msg.payload.viewId === 'workspace-status') {
                        postMsg('request-workspace-status');
                    }
                    break;
                case 'navigation-items':
                    break;
            }
        };

        function setActiveNav(viewId) {
            document.querySelectorAll('.mp-nav-item').forEach(function(el) {
                el.classList.toggle('active', el.dataset.viewId === viewId);
            });
        }

        function renderWorkspaceStatus(status) {
            var el = document.getElementById('status-content');
            if (!el) return;
            var dot = status.connected ? 'connected' : 'disconnected';
            var statusText = status.connected
                ? 'Connected — API v' + status.apiVersion + ', Schema v' + status.schemaVersion
                : 'Backend unavailable';
            var html = '<div class="mp-header" style="border: none; padding: 0; margin-bottom: 12px;">'
                + '<span class="status-dot ' + dot + '"></span>'
                + '<span class="status-text">' + statusText + '</span>'
                + '</div>';
            html += '<div class="info-row"><span class="info-label">Workspace</span><span class="info-value">' + escHtml(status.workspaceName) + '</span></div>';
            html += '<div class="info-row"><span class="info-label">Root</span><span class="info-value" style="font-size:11px; word-break:break-all;">' + escHtml(status.workspaceRoot) + '</span></div>';
            if (status.connected) {
                html += '<div class="info-row"><span class="info-label">API Version</span><span class="info-value">' + status.apiVersion + '</span></div>';
                html += '<div class="info-row"><span class="info-label">Schema Version</span><span class="info-value">' + status.schemaVersion + '</span></div>';
                html += '<div class="info-row"><span class="info-label">Indexed</span><span class="info-value">' + (status.indexed ? 'Yes' : 'No') + '</span></div>';
            }
            if (status.indexingPhase === 'scanning' || status.indexingPhase === 'extracting') {
                var pct = status.totalFiles > 0 ? Math.round((status.filesScanned / status.totalFiles) * 100) : 0;
                html += '<div style="margin-top:12px;"><strong>Indexing: </strong>' + status.indexingPhase + ' (' + pct + '%)</div>';
                html += '<div class="mp-progress"><div class="mp-progress-bar" style="width:' + pct + '%;"></div></div>';
                html += '<div style="font-size:11px;color:var(--mp-muted);">' + status.filesScanned + ' / ' + status.totalFiles + ' files • ' + status.symbolsExtracted + ' symbols</div>';
            }
            if (!status.connected) {
                html += '<div style="margin-top:12px;"><button onclick="postMsg(\\'restart-backend\\')" style="background:var(--mp-button-bg);color:var(--mp-button-fg);border:none;padding:6px 12px;border-radius:4px;cursor:pointer;">Restart Backend</button></div>';
            }
            el.innerHTML = html;
        }

        function escHtml(t) {
            var d = document.createElement('div');
            d.textContent = t || '';
            return d.innerHTML;
        }
        </script>`;
    }
}

