import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';
import { MemoPilotPanelBase } from './MemoPilotPanelBase';
import { NAVIGATION_ITEMS } from './navigationItems';
import type { WebviewOutboundMessage, WorkspaceStatusDTO, NavigationItemDTO } from './types';

// Cloud provider key fields that must never be persisted in plaintext to config.yaml —
// keys live only in SecretStorage. Kept here so we can scrub stale values left by an
// older MemoPilot build (or a hand-edited config.yaml) whenever we touch the file.
const PROVIDER_KEY_FIELDS: Record<string, string> = {
    anthropic: 'anthropic_api_key',
    openai: 'openai_api_key',
    google: 'google_api_key',
    openrouter: 'openrouter_api_key',
};

/**
 * Main MemoPilot panel — shell with navigation sidebar and content area.
 * Handles view switching, backend status display, and message routing.
 */
export class MemoPilotPanel extends MemoPilotPanelBase {
    public static currentPanel: MemoPilotPanel | undefined;
    private static readonly viewType = 'memopilotPanel';

    private client: BackendClient | undefined;
    private activeViewId: string = 'workspace-status';
    private extensionContext: vscode.ExtensionContext;

    public static createOrShow(
        extensionUri: vscode.Uri,
        client: BackendClient | undefined,
        extensionContext: vscode.ExtensionContext,
    ): void {
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

        MemoPilotPanel.currentPanel = new MemoPilotPanel(panel, extensionUri, client, extensionContext);
    }

    private constructor(
        panel: vscode.WebviewPanel,
        extensionUri: vscode.Uri,
        client: BackendClient | undefined,
        extensionContext: vscode.ExtensionContext,
    ) {
        super(panel, extensionUri);
        this.client = client;
        this.extensionContext = extensionContext;

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
                this.handleNavigation(message.payload.viewId);
                break;
            case 'request-workspace-status':
                this.sendWorkspaceStatus();
                break;
            case 'restart-backend':
                void vscode.commands.executeCommand('memopilot.restartBackend');
                break;
            case 'enter-api-key':
                void this.handleEnterApiKey();
                break;
            default:
                break;
        }
    }

    private handleNavigation(viewId: string): void {
        // Views that open their own panels/commands instead of inline content
        const externalViews: Record<string, string> = {
            'cost-dashboard': 'memopilot.showCostReport',
            'configure-providers': 'memopilot.configureProviders',
            'rules-skills': 'memopilot.openRules',
        };

        if (externalViews[viewId]) {
            void vscode.commands.executeCommand(externalViews[viewId]);
            return;
        }

        // Views that focus sidebar tree views
        const treeViews: Record<string, string> = {
            'local-memory': 'memopilot-memory',
            'context-pack': 'memopilot-context',
            'model-routing': 'memopilot-cost',
            'task-history': 'memopilot-history',
            'memory-manager': 'memopilot-memory',
            'workspace-profile': 'memopilot-profile',
            'privacy-boundary': 'memopilot-privacy',
            'mcp-tools': 'memopilot-mcp',
        };

        if (treeViews[viewId]) {
            void vscode.commands.executeCommand(`${treeViews[viewId]}.focus`);
        }

        // Update inline content for all views
        this.activeViewId = viewId;
        this.sendActiveView();
        this.sendViewContent(viewId);
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
            needsSetup: false,
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

        if (base.connected) {
            try {
                // Check if VS Code Copilot (host) models are available
                const lm = (vscode as unknown as Record<string, unknown>).lm as
                    | { selectChatModels: (selector: object) => Thenable<unknown[]> }
                    | undefined;
                const copilotModels = lm && typeof lm.selectChatModels === 'function'
                    ? await lm.selectChatModels({ vendor: 'copilot' })
                    : [];
                const hasCopilot = Array.isArray(copilotModels) && copilotModels.length > 0;

                const local = await this.client.discoverLocalProviders(base.workspaceRoot || undefined);
                const hasLocal = local.models.length > 0;
                // Needs setup only if neither Copilot nor local models are available
                if (!hasLocal && !hasCopilot) {
                    const status = await this.client.getIndexStatus(base.workspaceRoot || '');
                    base.needsSetup = status.memory_item_count === 0;
                }
            } catch {
                // If checks fail, don't show setup card
            }
        }

        return base;
    }

    private async handleEnterApiKey(): Promise<void> {
        const provider = await vscode.window.showQuickPick(
            ['anthropic', 'openai', 'google', 'openrouter'],
            { placeHolder: 'Select provider' },
        );
        if (!provider) { return; }

        const providerLabels: Record<string, string> = {
            anthropic: 'Anthropic',
            openai: 'OpenAI',
            google: 'Google AI Studio',
            openrouter: 'OpenRouter',
        };
        const providerPlaceholders: Record<string, string> = {
            anthropic: 'sk-ant-...',
            openai: 'sk-...',
            google: 'AIza...',
            openrouter: 'sk-or-v1-...',
        };
        const key = await vscode.window.showInputBox({
            prompt: `Enter your ${providerLabels[provider]} API key`,
            password: true,
            placeHolder: providerPlaceholders[provider],
        });
        if (!key) { return; }

        const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
        if (!workspaceFolder) {
            vscode.window.showErrorMessage('No workspace folder open.');
            return;
        }

        const secretKeyNames: Record<string, string> = {
            anthropic: 'memopilot.anthropicApiKey',
            openai: 'memopilot.openaiApiKey',
            google: 'memopilot.googleApiKey',
            openrouter: 'memopilot.openrouterApiKey',
        };

        const configDir = vscode.Uri.joinPath(workspaceFolder.uri, '.memopilot');
        const configFile = vscode.Uri.joinPath(configDir, 'config.yaml');

        try {
            // The key itself is stored in SecretStorage (never written to disk) —
            // only the provider selection goes into the workspace config.yaml.
            await this.extensionContext.secrets.store(secretKeyNames[provider], key);

            let existing = '';
            try {
                existing = Buffer.from(await vscode.workspace.fs.readFile(configFile)).toString('utf8');
            } catch { /* file doesn't exist yet */ }

            const update = (yaml: string, field: string, value: string): string => {
                const re = new RegExp(`^(\\s*#\\s*)?${field}:.*$`, 'm');
                const line = `${field}: ${value}`;
                return re.test(yaml) ? yaml.replace(re, line) : yaml + `\n${line}`;
            };

            // Scrub any plaintext key ever written by an older MemoPilot build (or a
            // hand-edited config.yaml) so a stale on-disk value can't silently keep
            // being used if SecretStorage is later cleared or fails to load.
            const scrubLegacyPlaintextKeys = (yaml: string): string => {
                let result = yaml;
                for (const field of Object.values(PROVIDER_KEY_FIELDS)) {
                    result = result.replace(new RegExp(`^${field}:.*\\n?`, 'm'), '');
                }
                return result;
            };

            let yaml = existing || '# MemoPilot provider config — do not commit\n';
            yaml = scrubLegacyPlaintextKeys(yaml);
            yaml = update(yaml, 'provider', provider);

            await vscode.workspace.fs.createDirectory(configDir);
            await vscode.workspace.fs.writeFile(configFile, Buffer.from(yaml, 'utf8'));
            const action = await vscode.window.showInformationMessage(
                `${providerLabels[provider]} API key saved. Restart MemoPilot backend to apply.`,
                'Restart Now',
            );
            if (action === 'Restart Now') {
                await vscode.commands.executeCommand('memopilot.restartBackend');
            }
            this.sendWorkspaceStatus();
        } catch (err: unknown) {
            vscode.window.showErrorMessage(`Failed to save API key: ${err instanceof Error ? err.message : String(err)}`);
        }
    }

    private getViewContentHtml(viewId: string): string {
        const item = NAVIGATION_ITEMS.find(n => n.id === viewId);
        if (!item) { return ''; }

        switch (viewId) {
            case 'workspace-status':
                return this.getWorkspaceStatusHtml();
            case 'local-memory':
                return this.getInfoHtml('Local App Memory', 'The Memory Manager sidebar shows indexed symbols, file summaries, and learned patterns. Click items to approve, edit, or filter.', 'memopilot-memory');
            case 'rules-skills':
                return this.getInfoHtml('Rules & Skills', 'Active global rules, project rules, and detected skills are shown in the sidebar tree. Refresh to reload from backend.', 'memopilot-rules');
            case 'context-pack':
                return this.getInfoHtml('Context Pack', 'The Context Pack sidebar shows files, tokens, and cost that will be sent to the AI model. It populates after task analysis.', 'memopilot-context');
            case 'model-routing':
                return this.getInfoHtml('Model Routing', 'The Cost Guard sidebar shows budget usage. Model routing selects the optimal model based on context size, task type, and privacy.', 'memopilot-cost');
            case 'validation':
                return this.getInfoHtml('Validation', 'After approval, syntax, lint, test impact, and security checks run automatically. Results appear inline in the Diff Preview.', undefined);
            case 'task-history':
                return this.getInfoHtml('Tasks & History', 'Recent tasks with status, model used, cost, and duration are shown in the Task History sidebar tree.', 'memopilot-history');
            case 'cost-dashboard':
                return this.getInfoHtml('Cost Dashboard', 'Opening the Cost Dashboard panel with metrics, daily trends, and per-model breakdown.', undefined);
            case 'memory-manager':
                return this.getInfoHtml('Memory Manager', 'Use the Memory Manager sidebar for full CRUD operations: approve, reject, edit, filter, backup.', 'memopilot-memory');
            case 'workspace-profile':
                return this.getInfoHtml('Workspace Profile', 'The Workspace Profile sidebar shows detected frameworks, dependencies, and configuration.', 'memopilot-profile');
            case 'privacy-boundary':
                return this.getInfoHtml('Privacy Dashboard', 'The Privacy Dashboard sidebar shows data classification: what stays local vs. what may leave.', 'memopilot-privacy');
            case 'mcp-tools':
                return this.getInfoHtml('MCP & Tools', 'Connected MCP servers and available tools are shown in the MCP Tools sidebar tree.', 'memopilot-mcp');
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

    private getInfoHtml(title: string, message: string, treeViewId: string | undefined): string {
        const focusBtn = treeViewId
            ? `<button class="mp-btn mp-focus-btn" data-tree="${treeViewId}">Focus Sidebar View</button>`
            : '';
        return `
            <div class="mp-placeholder">
                <h3>${this.escapeHtml(title)}</h3>
                <p>${this.escapeHtml(message)}</p>
                ${focusBtn}
            </div>`;
    }

    private buildShellHtml(): string {
        const navItemsHtml = NAVIGATION_ITEMS.map((item) => {
            const classes = ['mp-nav-item'];
            if (item.id === this.activeViewId) { classes.push('active'); }
            if (!item.enabled) { classes.push('disabled'); }
            const badge = item.badge ? `<span class="badge">${this.escapeHtml(item.badge)}</span>` : '';
            // Convert $(icon-name) to codicon span
            const iconName = item.icon.replace('$(', '').replace(')', '');
            return `<div class="${classes.join(' ')}" data-view-id="${this.escapeHtml(item.id)}">
                <span class="icon codicon codicon-${this.escapeHtml(iconName)}"></span>
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
        // Delegated click handler for nav items (CSP blocks inline onclick)
        document.addEventListener('click', function(e) {
            var navItem = e.target.closest('.mp-nav-item');
            if (navItem && !navItem.classList.contains('disabled')) {
                var viewId = navItem.dataset.viewId;
                if (viewId) { navigate(viewId); }
                return;
            }
            var focusBtn = e.target.closest('.mp-focus-btn');
            if (focusBtn) {
                var tree = focusBtn.dataset.tree;
                if (tree) { navigate(tree); }
                return;
            }
            var restartBtn = e.target.closest('.mp-restart-btn');
            if (restartBtn) {
                postMsg('restart-backend');
                return;
            }
            if (e.target.closest('.mp-setup-key-btn')) {
                postMsg('enter-api-key');
                return;
            }
            if (e.target.closest('.mp-setup-ollama-btn')) {
                postMsg('navigate', { viewId: 'configure-providers' });
                return;
            }
            if (e.target.closest('.mp-setup-copilot-btn')) {
                postMsg('navigate', { viewId: 'configure-providers' });
                return;
            }
        });

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
                html += '<div style="margin-top:12px;"><button class="mp-restart-btn mp-btn">Restart Backend</button></div>';
            }
            if (status.needsSetup) {
                html += '<div style="margin-top:20px;padding:16px;border:1px solid var(--mp-border);border-radius:6px;background:var(--vscode-editor-background);">'
                    + '<strong style="font-size:14px;">First-time setup</strong>'
                    + '<p style="font-size:12px;margin:8px 0 12px;opacity:0.8;">No AI providers detected. Choose how to enable AI patch generation:</p>'
                    + '<div style="display:flex;flex-direction:column;gap:8px;">'
                    + '<button class="mp-setup-ollama-btn" style="background:var(--mp-button-bg);color:var(--mp-button-fg);border:none;padding:8px 12px;border-radius:4px;cursor:pointer;text-align:left;">🦙 Install Ollama (free, local) — ollama.com</button>'
                    + '<button class="mp-setup-key-btn" style="background:var(--mp-button-bg);color:var(--mp-button-fg);border:none;padding:8px 12px;border-radius:4px;cursor:pointer;text-align:left;">🔑 Enter API key (Anthropic, OpenAI, Google AI Studio, or OpenRouter)</button>'
                    + '<button class="mp-setup-copilot-btn" style="background:var(--mp-button-bg);color:var(--mp-button-fg);border:none;padding:8px 12px;border-radius:4px;cursor:pointer;text-align:left;">🤖 Use Copilot subscription (VS Code only)</button>'
                    + '</div></div>';
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

