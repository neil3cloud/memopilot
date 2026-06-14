import * as vscode from 'vscode';
import { MemoPilotPanelBase } from './MemoPilotPanelBase';
import { BackendClient, TaskAnalyzeResponse } from '../BackendClient';
import type { WebviewOutboundMessage } from './types';

/** Messages specific to the Task Entry panel */
type TaskEntryMessage = WebviewOutboundMessage
    | { type: 'submit-task'; payload: { description: string; constraints: string[]; mode: string; notes: string } }
    | { type: 'cancel-task' };

/**
 * Task Entry panel — developer enters a natural language task with constraints and mode.
 * After submission, shows parsed intent, applicable rules, and suggested file scope.
 */
export class TaskEntryPanel extends MemoPilotPanelBase {
    public static currentPanel: TaskEntryPanel | undefined;
    private static readonly viewType = 'memopilotTaskEntry';

    private client: BackendClient | undefined;
    private lastAnalysis: TaskAnalyzeResponse | undefined;

    public static createOrShow(extensionUri: vscode.Uri, client: BackendClient | undefined): void {
        const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.Beside;

        if (TaskEntryPanel.currentPanel) {
            TaskEntryPanel.currentPanel.client = client;
            TaskEntryPanel.currentPanel.panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            TaskEntryPanel.viewType,
            'MemoPilot: New Task',
            column,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'resources')],
            },
        );

        TaskEntryPanel.currentPanel = new TaskEntryPanel(panel, extensionUri, client);
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri, client: BackendClient | undefined) {
        super(panel, extensionUri);
        this.client = client;

        this.panel.onDidDispose(() => {
            TaskEntryPanel.currentPanel = undefined;
        }, null, this.disposables);

        this.render();
    }

    protected handleMessage(message: TaskEntryMessage | WebviewOutboundMessage): void {
        switch (message.type) {
            case 'ready':
                break;
            case 'submit-task':
                this.handleSubmitTask(message as { type: 'submit-task'; payload: { description: string; constraints: string[]; mode: string; notes: string } });
                break;
            case 'cancel-task':
                this.lastAnalysis = undefined;
                this.render();
                break;
            default:
                break;
        }
    }

    private async handleSubmitTask(message: { type: 'submit-task'; payload: { description: string; constraints: string[]; mode: string; notes: string } }): Promise<void> {
        const { description, constraints, mode, notes } = message.payload;

        if (!description.trim()) {
            this.postMessage({ type: 'error', payload: { message: 'Task description is required.' } });
            return;
        }

        if (!this.client) {
            this.postMessage({ type: 'error', payload: { message: 'Backend not connected. Please restart the backend.' } });
            return;
        }

        // Show loading spinner in result area
        this.postMessage({
            type: 'view-content',
            payload: {
                viewId: 'task-loading',
                html: `<div style="display:flex;align-items:center;gap:8px;padding:12px 0;color:var(--mp-muted);">
                    <span class="codicon codicon-loading" style="animation:spin 1s linear infinite;"></span>
                    <span>Analyzing task...</span>
                </div>`,
            },
        });

        try {
            const result = await this.client.analyzeTask({
                description,
                constraints,
                mode: mode || null,
                notes: notes || null,
            });
            this.lastAnalysis = result;
            this.renderAnalysisResult(result);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            this.postMessage({ type: 'error', payload: { message: `Analysis failed: ${msg}` } });
        }
    }

    private renderAnalysisResult(result: TaskAnalyzeResponse): void {
        const rulesHtml = result.applicable_rules.length > 0
            ? result.applicable_rules.map(r => `<li>${this.escapeHtml(r)}</li>`).join('')
            : '<li style="color:var(--mp-muted);">No specific rules apply</li>';

        const filesHtml = result.suggested_files.length > 0
            ? result.suggested_files.map(f => `<li><code>${this.escapeHtml(f)}</code></li>`).join('')
            : '<li style="color:var(--mp-muted);">No files suggested yet</li>';

        const complexityColor = result.estimated_complexity === 'low' ? 'var(--mp-success)'
            : result.estimated_complexity === 'high' ? 'var(--mp-error)' : 'var(--mp-warning)';

        const html = `
            <div style="padding:12px 0;">
                <h3 style="margin-bottom:8px;">Task Analysis</h3>
                <div class="info-row"><span class="info-label">Intent</span><span class="info-value">${this.escapeHtml(result.intent_summary)}</span></div>
                <div class="info-row"><span class="info-label">Mode</span><span class="info-value">${this.escapeHtml(result.suggested_mode)}</span></div>
                <div class="info-row"><span class="info-label">Complexity</span><span class="info-value" style="color:${complexityColor}">${this.escapeHtml(result.estimated_complexity)}</span></div>

                <h4 style="margin:12px 0 4px;">Applicable Rules</h4>
                <ul style="margin:0;padding-left:16px;font-size:12px;">${rulesHtml}</ul>

                <h4 style="margin:12px 0 4px;">Suggested Files</h4>
                <ul style="margin:0;padding-left:16px;font-size:12px;">${filesHtml}</ul>

                <div style="margin-top:16px;display:flex;gap:8px;">
                    <button id="edit-task-btn" style="background:transparent;color:var(--mp-fg);border:1px solid var(--mp-border);padding:6px 12px;border-radius:4px;cursor:pointer;">← Edit Task</button>
                </div>
            </div>`;

        // Note: result area content is set via innerHTML so we add a delegated listener in the main script
        this.postMessage({ type: 'view-content', payload: { viewId: 'task-analysis', html } });
    }

    private render(): void {
        this.panel.webview.html = this.renderHtml(this.buildFormHtml(), this.buildScript(), this.getStyles());
    }

    private getStyles(): string {
        return `
            .task-container { max-width: 560px; margin: 0 auto; padding: 20px; }
            .task-container h2 { margin-bottom: 4px; font-size: 18px; }
            .task-container .subtitle { color: var(--mp-muted); margin-bottom: 20px; font-size: 12px; line-height: 1.5; }
            .form-group { margin-bottom: 16px; }
            .form-group label { display: block; font-size: 12px; margin-bottom: 6px; font-weight: 500; color: var(--mp-fg); }
            .form-group textarea,
            .form-group select {
                width: 100%;
                background: var(--vscode-input-background);
                color: var(--vscode-input-foreground, var(--mp-fg));
                border: 1px solid var(--vscode-input-border, var(--mp-border));
                border-radius: 4px;
                padding: 8px 10px;
                font-family: var(--vscode-font-family);
                font-size: 13px;
                resize: vertical;
            }
            .form-group textarea:focus,
            .form-group select:focus {
                outline: none;
                border-color: var(--vscode-focusBorder);
            }
            .constraints-group { margin-bottom: 20px; }
            .constraints-group label.group-label { display: block; font-size: 12px; margin-bottom: 8px; font-weight: 500; }
            .constraints-group .checkbox-item {
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 12px;
                margin-bottom: 6px;
                cursor: pointer;
            }
            .constraints-group .checkbox-item input[type="checkbox"] {
                accent-color: var(--vscode-button-background);
            }
            .btn-primary {
                background: var(--vscode-button-background);
                color: var(--vscode-button-foreground);
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 500;
                transition: background 0.15s;
            }
            .btn-primary:hover { background: var(--vscode-button-hoverBackground); }
            .btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
            #error-area { margin-top: 8px; color: var(--mp-error); font-size: 12px; }
            #result-area { margin-top: 16px; }
            @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
            .codicon-loading { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--mp-muted); border-top-color: var(--vscode-button-background); border-radius: 50%; }
        `;
    }

    private buildFormHtml(): string {
        return `
        <div class="task-container">
            <h2>New Task</h2>
            <p class="subtitle">Describe what you want MemoPilot to do. Be specific about files, behavior, and constraints.</p>

            <div class="form-group">
                <label for="task-desc">Task Description</label>
                <textarea id="task-desc" rows="4" placeholder="Add validation so expired items cannot be sold..."></textarea>
            </div>

            <div class="form-group">
                <label for="task-notes">Additional Notes (optional)</label>
                <textarea id="task-notes" rows="2" placeholder="Expiration date is in inventory/item.expiry_date..."></textarea>
            </div>

            <div class="form-group">
                <label for="task-mode">Mode</label>
                <select id="task-mode">
                    <option value="">Auto-detect</option>
                    <option value="refactor">Refactor</option>
                    <option value="fix">Fix Bug</option>
                    <option value="test">Write Tests</option>
                    <option value="document">Document</option>
                </select>
            </div>

            <div class="constraints-group">
                <label class="group-label">Constraints</label>
                <label class="checkbox-item">
                    <input type="checkbox" id="constraint-rules" checked> Follow all project rules
                </label>
                <label class="checkbox-item">
                    <input type="checkbox" id="constraint-tests" checked> Run tests after applying changes
                </label>
            </div>

            <button id="submit-btn" class="btn-primary">Analyze Task</button>

            <div id="result-area"></div>
            <div id="error-area"></div>
        </div>`;
    }

    private buildScript(): string {
        return `<script nonce="REPLACED_BY_BASE">
        document.addEventListener('DOMContentLoaded', function() {
            var submitBtn = document.getElementById('submit-btn');
            if (submitBtn) {
                submitBtn.addEventListener('click', submitTask);
            }

            // Delegated handler for dynamically-inserted buttons
            document.addEventListener('click', function(e) {
                var target = e.target;
                if (!(target instanceof Element)) { return; }
                var editBtn = target.closest('#edit-task-btn');
                if (editBtn) { postMsg('cancel-task'); }
            });
        });

        function submitTask() {
            var desc = document.getElementById('task-desc').value;
            var notes = document.getElementById('task-notes').value;
            var mode = document.getElementById('task-mode').value;
            var constraints = [];
            if (document.getElementById('constraint-rules').checked) constraints.push('follow_all_rules');
            if (document.getElementById('constraint-tests').checked) constraints.push('run_tests');

            document.getElementById('submit-btn').textContent = 'Analyzing...';
            document.getElementById('submit-btn').disabled = true;
            document.getElementById('error-area').textContent = '';

            postMsg('submit-task', { description: desc, constraints: constraints, mode: mode, notes: notes });
        }

        window.handleMessage = function(msg) {
            switch (msg.type) {
                case 'view-content':
                    document.getElementById('result-area').innerHTML = msg.payload.html;
                    if (msg.payload.viewId !== 'task-loading') {
                        document.getElementById('submit-btn').textContent = 'Analyze Task';
                        document.getElementById('submit-btn').disabled = false;
                    }
                    break;
                case 'error':
                    document.getElementById('error-area').textContent = msg.payload.message;
                    document.getElementById('result-area').innerHTML = '';
                    document.getElementById('submit-btn').textContent = 'Analyze Task';
                    document.getElementById('submit-btn').disabled = false;
                    break;
            }
        };
        </script>`;
    }
}
