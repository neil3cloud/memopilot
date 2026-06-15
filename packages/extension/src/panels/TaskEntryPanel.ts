import * as vscode from 'vscode';
import { MemoPilotPanelBase } from './MemoPilotPanelBase';
import { BackendClient, TaskAnalyzeResponse } from '../BackendClient';
import { TaskFlowController } from '../controllers/TaskFlowController';
import type { WebviewOutboundMessage } from './types';

/** Messages specific to the Task Entry panel */
type TaskEntryMessage = WebviewOutboundMessage
    | { type: 'submit-task'; payload: { description: string; constraints: string[]; mode: string; notes: string } }
    | { type: 'cancel-task' }
    | { type: 'generate-context' }
    | { type: 'generate-patch' };

/**
 * Task Entry panel — modern card-based workflow screen.
 * Guides the developer through: Task → Analyze → Context → Route → Patch → Approval → Validate.
 */
export class TaskEntryPanel extends MemoPilotPanelBase {
    public static currentPanel: TaskEntryPanel | undefined;
    private static readonly viewType = 'memopilotTaskEntry';
    private static outputChannel: vscode.OutputChannel | undefined;

    private client: BackendClient | undefined;
    private flowController: TaskFlowController | undefined;
    private lastAnalysis: TaskAnalyzeResponse | undefined;
    private lastTaskDescription = '';
    private lastConstraints: string[] = [];
    private lastMode = 'auto';

    private log(msg: string): void {
        if (!TaskEntryPanel.outputChannel) {
            TaskEntryPanel.outputChannel = vscode.window.createOutputChannel('MemoPilot Task');
        }
        const ts = new Date().toISOString().slice(11, 23);
        TaskEntryPanel.outputChannel.appendLine(`[${ts}] ${msg}`);
    }

    public static createOrShow(extensionUri: vscode.Uri, client: BackendClient | undefined, flowController?: TaskFlowController): void {
        const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.Beside;

        if (TaskEntryPanel.currentPanel) {
            TaskEntryPanel.currentPanel.client = client;
            TaskEntryPanel.currentPanel.flowController = flowController;
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

        TaskEntryPanel.currentPanel = new TaskEntryPanel(panel, extensionUri, client, flowController);
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri, client: BackendClient | undefined, flowController?: TaskFlowController) {
        super(panel, extensionUri);
        this.client = client;
        this.flowController = flowController;

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
            case 'generate-context':
                if (this.lastAnalysis) {
                    this.postMessage({ type: 'view-content', payload: { viewId: 'btn-loading', html: 'context' } });
                    this.runContextBuild();
                }
                break;
            case 'generate-patch':
                if (this.lastAnalysis) {
                    this.postMessage({ type: 'view-content', payload: { viewId: 'btn-loading', html: 'patch' } });
                    this.runPatchGeneration();
                }
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

        this.log(`analyzeTask: "${description}" mode=${mode || 'auto'}`);

        this.postMessage({
            type: 'view-content',
            payload: { viewId: 'task-loading', html: '' },
        });

        try {
            const result = await this.client.analyzeTask({
                description,
                constraints,
                mode: mode || null,
                notes: notes || null,
            });
            this.lastAnalysis = result;
            this.lastTaskDescription = description;
            this.lastConstraints = constraints;
            this.lastMode = mode || 'auto';
            this.log(`analyzeTask: SUCCESS — intent="${result.intent_summary}" mode=${result.suggested_mode} files=[${result.suggested_files.join(', ')}]`);
            // Seed flow controller so buildContext() has the analysis
            if (this.flowController) {
                this.flowController.setAnalysis(description, constraints, this.lastMode, result);
            }
            this.renderAnalysisResult(result, description);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            this.postMessage({ type: 'error', payload: { message: `Analysis failed: ${msg}` } });
        }
    }

    private renderAnalysisResult(result: TaskAnalyzeResponse, taskDescription: string): void {
        // Infer suggested file operations from intent and task description
        const targetFiles = this.inferTargetFiles(result, taskDescription);
        const isDocsOnly = this.isDocumentationOnly(result, targetFiles);

        const viewModel = {
            intent: result.intent_summary,
            mode: result.suggested_mode,
            complexity: result.estimated_complexity,
            risk: result.risk || 'medium',
            taskType: result.task_type || 'general',
            targetFiles,
            applicableRules: result.applicable_rules,
            isDocsOnly,
            aiUsage: { status: 'none', message: 'No AI call yet — local analysis only' },
        };

        this.postMessage({
            type: 'view-content',
            payload: { viewId: 'task-analysis', html: JSON.stringify(viewModel) },
        });
    }

    private inferTargetFiles(result: TaskAnalyzeResponse, taskDescription: string): Array<{ path: string; operation: string; reason: string }> {
        const files: Array<{ path: string; operation: string; reason: string }> = [];

        // Include backend-suggested files
        for (const f of result.suggested_files) {
            files.push({ path: f, operation: 'modify', reason: 'Identified by analyzer' });
        }

        // Infer file creation from task description patterns
        if (files.length === 0) {
            const createPatterns = [
                /(?:add|create|new)\s+(?:a\s+)?(?:file\s+)?(?:named?\s+|called\s+)?["']?([^\s"',]+\.\w+)/i,
                /(?:add|create|new)\s+([^\s"',]+\.\w+)/i,
            ];
            for (const pattern of createPatterns) {
                const match = taskDescription.match(pattern);
                if (match) {
                    files.push({ path: match[1], operation: 'create', reason: 'Inferred from task description' });
                    break;
                }
            }
        }

        return files;
    }

    private isDocumentationOnly(_result: TaskAnalyzeResponse, targetFiles: Array<{ path: string; operation: string }>): boolean {
        if (targetFiles.length === 0) { return false; }
        const docExtensions = ['.md', '.txt', '.rst', '.adoc', '.mdx'];
        return targetFiles.every(f => docExtensions.some(ext => f.path.toLowerCase().endsWith(ext)));
    }

    private async runContextBuild(): Promise<void> {
        if (!this.client || !this.lastAnalysis) {
            this.log('runContextBuild: aborted — no client or analysis');
            return;
        }

        this.log('runContextBuild: starting...');
        TaskEntryPanel.outputChannel?.show(true);

        try {
            vscode.window.showInformationMessage('MemoPilot: Building context pack...');
            if (this.flowController) {
                // Ensure analysis is seeded (safety check in case controller was reset)
                const currentState = this.flowController.getState();
                if (!currentState.analysis && this.lastAnalysis) {
                    this.log('runContextBuild: re-seeding flow controller with analysis');
                    this.flowController.setAnalysis(this.lastTaskDescription, this.lastConstraints, this.lastMode, this.lastAnalysis);
                }
                this.log('runContextBuild: using TaskFlowController.buildContext()');
                this.log(`  → task: "${this.lastAnalysis.intent_summary}"`);
                this.log(`  → suggested_files: [${this.lastAnalysis.suggested_files.join(', ')}]`);
                this.log(`  → mode: ${this.lastAnalysis.suggested_mode}`);
                await this.flowController.buildContext();
                const state = this.flowController.getState();
                this.log(`runContextBuild: flow state = ${state.stage}`);
                if (state.error) {
                    this.log(`runContextBuild: ERROR from flow — ${state.error}`);
                    this.postMessage({ type: 'error', payload: { message: state.error } });
                    return;
                }
                if (state.contextPack) {
                    const tokens = state.contextPack.total_tokens.toLocaleString();
                    const cost = state.contextPack.estimated_cost_usd.toFixed(4);
                    this.log(`runContextBuild: SUCCESS — ${tokens} tokens, $${cost}, ${state.contextPack.files.length} files`);
                    this.postMessage({ type: 'view-content', payload: { viewId: 'context-done', html: JSON.stringify({ tokens, cost, files: state.contextPack.files.length }) } });
                    vscode.window.showInformationMessage(`MemoPilot: Context pack ready — ${tokens} tokens, $${cost}`);
                } else {
                    this.log('runContextBuild: no contextPack in state after buildContext()');
                }
            } else {
                this.log('runContextBuild: no flowController — calling client.buildContextPack() directly');
                const pack = await this.client.buildContextPack({
                    task_description: this.lastAnalysis.intent_summary,
                    suggested_files: this.lastAnalysis.suggested_files,
                    mode: this.lastAnalysis.suggested_mode,
                });
                const tokens = pack.total_tokens.toLocaleString();
                const cost = pack.estimated_cost_usd.toFixed(4);
                this.log(`runContextBuild: direct SUCCESS — ${tokens} tokens, $${cost}, ${pack.files.length} files`);
                this.postMessage({ type: 'view-content', payload: { viewId: 'context-done', html: JSON.stringify({ tokens, cost, files: pack.files.length }) } });
                vscode.window.showInformationMessage(`MemoPilot: Context pack ready — ${tokens} tokens, $${cost}`);
            }
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            this.log(`runContextBuild: EXCEPTION — ${msg}`);
            this.postMessage({ type: 'error', payload: { message: `Context build failed: ${msg}` } });
        } finally {
            // Always reset button state
            this.postMessage({ type: 'view-content', payload: { viewId: 'btn-reset', html: 'context' } });
        }
    }

    private async runPatchGeneration(): Promise<void> {
        if (!this.client || !this.lastAnalysis) {
            this.log('runPatchGeneration: aborted — no client or analysis');
            return;
        }

        this.log('runPatchGeneration: starting full pipeline...');
        TaskEntryPanel.outputChannel?.show(true);

        try {
            vscode.window.showInformationMessage('MemoPilot: Generating patch (context → route → patch)...');
            if (this.flowController) {
                const state = this.flowController.getState();
                this.log(`runPatchGeneration: current flow state = ${state.stage}`);
                if (!state.contextPack) {
                    this.log('runPatchGeneration: no contextPack → calling buildContext()');
                    await this.flowController.buildContext();
                } else if (!state.modelDecision) {
                    this.log('runPatchGeneration: no modelDecision → calling routeModel()');
                    await this.flowController.routeModel();
                } else {
                    this.log('runPatchGeneration: context+route ready → calling generatePatch()');
                    await this.flowController.generatePatch();
                }
                const finalState = this.flowController.getState();
                this.log(`runPatchGeneration: final state = ${finalState.stage}`);
                if (finalState.patch) {
                    const fileCount = finalState.patch.patches.length;
                    this.log(`runPatchGeneration: SUCCESS — ${fileCount} file(s)`);
                    this.postMessage({ type: 'view-content', payload: { viewId: 'patch-done', html: JSON.stringify({ fileCount }) } });
                    vscode.window.showInformationMessage(`MemoPilot: Patch ready — ${fileCount} file(s) changed. Review in diff editor.`);
                } else if (finalState.error) {
                    this.log(`runPatchGeneration: ERROR — ${finalState.error}`);
                    this.postMessage({ type: 'error', payload: { message: finalState.error } });
                }
            } else {
                this.log('runPatchGeneration: no flowController');
                vscode.window.showWarningMessage('MemoPilot: No flow controller available. Use the command palette to generate patches.');
            }
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            this.log(`runPatchGeneration: EXCEPTION — ${msg}`);
            this.postMessage({ type: 'error', payload: { message: `Patch generation failed: ${msg}` } });
        } finally {
            // Always reset button state
            this.postMessage({ type: 'view-content', payload: { viewId: 'btn-reset', html: 'patch' } });
        }
    }

    private render(): void {
        this.panel.webview.html = this.renderHtml(this.buildFormHtml(), this.buildScript(), this.getStyles());
    }

    private getStyles(): string {
        return `
            /* Layout */
            body { overflow-y: auto !important; height: auto !important; }
            .task-panel { max-width: 900px; margin: 0 auto; padding: 24px 20px 48px; }

            /* Header */
            .task-header { margin-bottom: 24px; }
            .task-header h2 { font-size: 20px; font-weight: 600; margin-bottom: 6px; }
            .task-header .subtitle { color: var(--mp-muted); font-size: 12px; line-height: 1.6; max-width: 520px; }

            /* Workflow Stepper */
            .workflow-stepper {
                display: flex;
                align-items: center;
                gap: 0;
                margin-bottom: 24px;
                padding: 10px 12px;
                background: var(--vscode-sideBar-background);
                border: 1px solid var(--mp-border);
                border-radius: 6px;
                overflow-x: auto;
            }
            .step {
                display: flex;
                align-items: center;
                gap: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 500;
                border-radius: 12px;
                white-space: nowrap;
                color: var(--mp-muted);
            }
            .step.active {
                background: var(--vscode-button-background);
                color: var(--vscode-button-foreground);
            }
            .step.completed {
                color: var(--mp-success);
            }
            .step-connector {
                width: 16px;
                height: 1px;
                background: var(--mp-border);
                flex-shrink: 0;
            }
            .step-number {
                width: 18px;
                height: 18px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 10px;
                font-weight: 600;
                border: 1.5px solid currentColor;
                flex-shrink: 0;
            }
            .step.active .step-number { border-color: var(--vscode-button-foreground); background: color-mix(in srgb, var(--vscode-button-foreground) 15%, transparent); }
            .step.completed .step-number { border-color: var(--mp-success); background: color-mix(in srgb, var(--mp-success) 12%, transparent); }

            /* Cards */
            .card {
                background: var(--vscode-sideBar-background);
                border: 1px solid var(--mp-border);
                border-radius: 8px;
                padding: 16px;
                margin-bottom: 16px;
            }
            .card-title {
                font-size: 13px;
                font-weight: 600;
                margin-bottom: 12px;
                display: flex;
                align-items: center;
                gap: 6px;
            }
            .card-title .codicon { font-size: 14px; color: var(--mp-accent); }

            /* Form elements */
            .form-group { margin-bottom: 14px; }
            .form-group label {
                display: block;
                font-size: 11px;
                font-weight: 500;
                margin-bottom: 5px;
                color: var(--mp-fg);
                text-transform: uppercase;
                letter-spacing: 0.3px;
            }
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
                box-shadow: 0 0 0 1px var(--vscode-focusBorder);
            }
            .mode-hint {
                font-size: 11px;
                color: var(--mp-muted);
                margin-top: 4px;
                font-style: italic;
            }

            /* Guardrails */
            .guardrails { display: flex; flex-wrap: wrap; gap: 8px; }
            .guardrail-chip {
                display: inline-flex;
                align-items: center;
                gap: 5px;
                padding: 4px 10px;
                background: color-mix(in srgb, var(--mp-success) 8%, transparent);
                border: 1px solid color-mix(in srgb, var(--mp-success) 25%, transparent);
                border-radius: 12px;
                font-size: 11px;
                color: var(--mp-success);
            }
            .guardrail-chip .codicon { font-size: 12px; }

            /* Badges */
            .badge {
                display: inline-block;
                padding: 2px 8px;
                border-radius: 10px;
                font-size: 10px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.4px;
            }
            .badge-low { background: color-mix(in srgb, var(--mp-success) 12%, transparent); color: var(--mp-success); border: 1px solid color-mix(in srgb, var(--mp-success) 25%, transparent); }
            .badge-medium { background: color-mix(in srgb, var(--mp-warning) 12%, transparent); color: var(--mp-warning); border: 1px solid color-mix(in srgb, var(--mp-warning) 25%, transparent); }
            .badge-high { background: color-mix(in srgb, var(--mp-error) 12%, transparent); color: var(--mp-error); border: 1px solid color-mix(in srgb, var(--mp-error) 25%, transparent); }
            .badge-critical { background: color-mix(in srgb, var(--mp-error) 18%, transparent); color: var(--mp-error); border: 1px solid color-mix(in srgb, var(--mp-error) 35%, transparent); }

            /* Analysis fields */
            .analysis-grid {
                display: grid;
                grid-template-columns: 110px 1fr;
                gap: 8px 12px;
                align-items: center;
            }
            .analysis-label {
                font-size: 11px;
                color: var(--mp-muted);
                text-transform: uppercase;
                letter-spacing: 0.3px;
            }
            .analysis-value {
                font-size: 13px;
                font-weight: 500;
            }

            /* Suggested files */
            .file-list { list-style: none; padding: 0; margin: 0; }
            .file-item {
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 6px 10px;
                border-radius: 4px;
                font-size: 12px;
                font-family: var(--vscode-editor-font-family, monospace);
            }
            .file-item:nth-child(odd) { background: color-mix(in srgb, var(--mp-fg) 5%, transparent); }
            .file-op {
                font-size: 10px;
                font-weight: 700;
                padding: 1px 6px;
                border-radius: 3px;
                text-transform: uppercase;
            }
            .file-op-create { background: color-mix(in srgb, var(--mp-success) 15%, transparent); color: var(--mp-success); }
            .file-op-modify { background: color-mix(in srgb, var(--mp-accent) 15%, transparent); color: var(--mp-accent); }
            .file-op-delete { background: color-mix(in srgb, var(--mp-error) 15%, transparent); color: var(--mp-error); }
            .file-reason { font-size: 10px; color: var(--mp-muted); margin-left: auto; font-family: var(--vscode-font-family); }
            .empty-state { text-align: center; padding: 16px; color: var(--mp-muted); font-size: 12px; }

            /* Cost boundary */
            .ai-status {
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 10px 12px;
                border-radius: 4px;
                background: color-mix(in srgb, var(--mp-fg) 6%, transparent);
                font-size: 12px;
            }
            .ai-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: var(--mp-muted);
                flex-shrink: 0;
            }
            .ai-dot.active { background: var(--mp-success); animation: pulse-dot 2s infinite; }
            @keyframes pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

            /* Actions bar */
            .actions-bar {
                display: flex;
                gap: 8px;
                margin-top: 20px;
                padding-top: 16px;
                border-top: 1px solid var(--mp-border);
            }
            .actions-bar .mp-btn { flex: 0 0 auto; }

            /* Validation note */
            .validation-note {
                margin-top: 8px;
                padding: 8px 12px;
                background: color-mix(in srgb, var(--mp-fg) 6%, transparent);
                border-left: 3px solid var(--mp-accent);
                border-radius: 0 4px 4px 0;
                font-size: 11px;
                color: var(--mp-muted);
            }

            /* Loading */
            .loading-state {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
                padding: 24px;
                color: var(--mp-muted);
                font-size: 13px;
            }
            .spinner {
                width: 16px; height: 16px;
                border: 2px solid var(--mp-border);
                border-top-color: var(--vscode-button-background);
                border-radius: 50%;
                animation: spin 0.8s linear infinite;
            }
            @keyframes spin { to { transform: rotate(360deg); } }

            /* Hidden sections */
            .hidden { display: none !important; }

            #error-area { margin-top: 8px; color: var(--mp-error); font-size: 12px; min-height: 18px; }
        `;
    }

    private buildFormHtml(): string {
        return `
        <div class="task-panel">
            <!-- Header -->
            <div class="task-header">
                <h2>New Task</h2>
                <p class="subtitle">Describe the change, bug, or user story. MemoPilot will analyze local rules, memory, and project context before using AI.</p>
            </div>

            <!-- Workflow Stepper -->
            <div class="workflow-stepper" role="navigation" aria-label="Workflow steps">
                <div class="step active" data-step="task"><span class="step-number">1</span>Task</div>
                <div class="step-connector"></div>
                <div class="step" data-step="analyze"><span class="step-number">2</span>Analyze</div>
                <div class="step-connector"></div>
                <div class="step" data-step="context"><span class="step-number">3</span>Context</div>
                <div class="step-connector"></div>
                <div class="step" data-step="route"><span class="step-number">4</span>Route</div>
                <div class="step-connector"></div>
                <div class="step" data-step="patch"><span class="step-number">5</span>Patch</div>
                <div class="step-connector"></div>
                <div class="step" data-step="approval"><span class="step-number">6</span>Approval</div>
                <div class="step-connector"></div>
                <div class="step" data-step="validate"><span class="step-number">7</span>Validate</div>
            </div>

            <!-- Task Input Card -->
            <div class="card" id="task-input-card">
                <div class="card-title"><span class="codicon codicon-edit"></span> Task Details</div>
                <div class="form-group">
                    <label for="task-desc">Description</label>
                    <textarea id="task-desc" rows="4" placeholder="Add validation so expired items cannot be sold..." aria-label="Task description"></textarea>
                </div>
                <div class="form-group">
                    <label for="task-notes">Notes / Evidence (optional)</label>
                    <textarea id="task-notes" rows="2" placeholder="Business rule: items past expiry_date must be blocked at checkout..." aria-label="Additional notes"></textarea>
                </div>
                <div class="form-group">
                    <label for="task-mode">Mode</label>
                    <select id="task-mode" aria-label="Task mode">
                        <option value="">Auto-detect (recommended)</option>
                        <option value="patch">Patch — Generate code changes</option>
                        <option value="refactor">Refactor — Restructure existing code</option>
                        <option value="fix">Fix Bug — Diagnose and repair</option>
                        <option value="test">Write Tests — Add test coverage</option>
                        <option value="document">Document — Docs and comments only</option>
                        <option value="investigate">Investigate — Root-cause analysis</option>
                    </select>
                    <div class="mode-hint" id="mode-hint">MemoPilot will recommend the best mode based on your description.</div>
                </div>
            </div>

            <!-- Guardrails Card -->
            <div class="card" id="guardrails-card">
                <div class="card-title"><span class="codicon codicon-shield"></span> Active Guardrails</div>
                <div class="guardrails">
                    <span class="guardrail-chip"><span class="codicon codicon-check"></span> Project rules enforced</span>
                    <span class="guardrail-chip"><span class="codicon codicon-check"></span> Patch approval required</span>
                    <span class="guardrail-chip"><span class="codicon codicon-check"></span> Secret redaction</span>
                    <span class="guardrail-chip" id="guardrail-validation"><span class="codicon codicon-check"></span> Validation after changes</span>
                </div>
            </div>

            <!-- Analyze Button -->
            <button id="submit-btn" class="mp-btn" style="width:100%; padding:10px;">Analyze Task</button>

            <!-- Loading State -->
            <div id="loading-state" class="loading-state hidden">
                <div class="spinner"></div>
                <span>Running local analysis — rules, memory, project context...</span>
            </div>

            <!-- Analysis Result (hidden initially) -->
            <div id="analysis-section" class="hidden">
                <!-- Analysis Summary Card -->
                <div class="card">
                    <div class="card-title"><span class="codicon codicon-pulse"></span> Analysis Summary</div>
                    <div class="analysis-grid">
                        <span class="analysis-label">Intent</span>
                        <span class="analysis-value" id="a-intent"></span>

                        <span class="analysis-label">Mode</span>
                        <span class="analysis-value" id="a-mode"></span>

                        <span class="analysis-label">Complexity</span>
                        <span class="analysis-value" id="a-complexity"></span>

                        <span class="analysis-label">Risk</span>
                        <span class="analysis-value" id="a-risk"></span>

                        <span class="analysis-label">AI Usage</span>
                        <span class="analysis-value" id="a-ai-usage"></span>
                    </div>
                    <div id="a-rules-section" style="margin-top: 12px;">
                        <div style="font-size: 11px; color: var(--mp-muted); text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 6px;">Applicable Rules</div>
                        <div id="a-rules" style="font-size: 12px;"></div>
                    </div>
                </div>

                <!-- Suggested Files Card -->
                <div class="card">
                    <div class="card-title"><span class="codicon codicon-file-code"></span> Suggested Files</div>
                    <ul class="file-list" id="a-files"></ul>
                </div>

                <!-- Cost / AI Boundary Card -->
                <div class="card">
                    <div class="card-title"><span class="codicon codicon-server"></span> AI / Cost Boundary</div>
                    <div class="ai-status" id="a-cost-status">
                        <div class="ai-dot"></div>
                        <span>No AI call yet — local analysis only</span>
                    </div>
                </div>

                <!-- Validation Note -->
                <div class="validation-note" id="a-validation-note"></div>

                <!-- Next Actions Bar -->
                <div class="actions-bar">
                    <button id="context-btn" class="mp-btn">Generate Context Pack</button>
                    <button id="patch-btn" class="mp-btn">Generate Patch</button>
                    <button id="edit-btn" class="mp-btn-secondary">← Edit Task</button>
                </div>
            </div>

            <div id="error-area"></div>
        </div>`;
    }

    private buildScript(): string {
        return `<script nonce="REPLACED_BY_BASE">
        document.addEventListener('DOMContentLoaded', function() {
            var submitBtn = document.getElementById('submit-btn');
            var editBtn = document.getElementById('edit-btn');
            var contextBtn = document.getElementById('context-btn');
            var patchBtn = document.getElementById('patch-btn');
            var modeSelect = document.getElementById('task-mode');

            if (submitBtn) submitBtn.addEventListener('click', submitTask);

            document.addEventListener('click', function(e) {
                var target = e.target;
                if (!(target instanceof Element)) return;
                if (target.closest('#edit-btn')) { postMsg('cancel-task'); }
                if (target.closest('#context-btn')) {
                    showBtnLoading('context-btn', 'Generating Context Pack...');
                    postMsg('generate-context');
                }
                if (target.closest('#patch-btn')) {
                    showBtnLoading('patch-btn', 'Generating Patch...');
                    postMsg('generate-patch');
                }
            });

            if (modeSelect) {
                modeSelect.addEventListener('change', updateModeHint);
            }
        });

        var modeHints = {
            '': 'MemoPilot will recommend the best mode based on your description.',
            'patch': 'Generate code changes directly from your task description.',
            'refactor': 'Restructure existing code while preserving behavior.',
            'fix': 'Diagnose and repair a specific bug or issue.',
            'test': 'Add test coverage for existing functionality.',
            'document': 'Generate documentation and code comments. No code changes.',
            'investigate': 'Deep-dive root-cause analysis before making changes.'
        };

        function showBtnLoading(btnId, text) {
            var btn = document.getElementById(btnId);
            if (btn) {
                btn.disabled = true;
                btn.dataset.originalText = btn.textContent;
                btn.innerHTML = '<span style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:6px;border:2px solid rgba(255,255,255,0.3);border-top-color:var(--vscode-button-foreground);border-radius:50%;animation:spin 0.8s linear infinite;"></span>' + text;
                btn.style.opacity = '0.8';
            }
        }

        function updateModeHint() {
            var mode = document.getElementById('task-mode').value;
            var hint = document.getElementById('mode-hint');
            if (hint) hint.textContent = modeHints[mode] || modeHints[''];
        }

        function submitTask() {
            var desc = document.getElementById('task-desc').value;
            var notes = document.getElementById('task-notes').value;
            var mode = document.getElementById('task-mode').value;
            var constraints = ['follow_all_rules'];

            var validationChip = document.getElementById('guardrail-validation');
            if (validationChip) constraints.push('run_tests');

            if (!desc.trim()) {
                document.getElementById('error-area').textContent = 'Please enter a task description.';
                return;
            }

            document.getElementById('error-area').textContent = '';
            document.getElementById('submit-btn').classList.add('hidden');
            document.getElementById('loading-state').classList.remove('hidden');
            updateStepper('analyze');

            postMsg('submit-task', { description: desc, constraints: constraints, mode: mode, notes: notes });
        }

        function updateStepper(activeStep) {
            var steps = document.querySelectorAll('.step');
            var stepNames = ['task', 'analyze', 'context', 'route', 'patch', 'approval', 'validate'];
            var activeIdx = stepNames.indexOf(activeStep);
            steps.forEach(function(step, idx) {
                step.classList.remove('active', 'completed');
                if (idx < activeIdx) step.classList.add('completed');
                else if (idx === activeIdx) step.classList.add('active');
            });
        }

        function getBadgeClass(level) {
            if (level === 'low') return 'badge-low';
            if (level === 'medium') return 'badge-medium';
            if (level === 'high' || level === 'critical') return 'badge-high';
            return 'badge-medium';
        }

        function escapeHtml(text) {
            var div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function renderAnalysis(viewModel) {
            document.getElementById('loading-state').classList.add('hidden');
            document.getElementById('analysis-section').classList.remove('hidden');
            document.getElementById('task-input-card').style.opacity = '0.6';
            document.getElementById('task-input-card').style.pointerEvents = 'none';
            updateStepper('analyze');

            // Intent
            document.getElementById('a-intent').textContent = viewModel.intent;

            // Mode
            document.getElementById('a-mode').innerHTML = '<span class="badge badge-low">' + escapeHtml(viewModel.mode) + '</span>';

            // Complexity
            var compClass = getBadgeClass(viewModel.complexity);
            document.getElementById('a-complexity').innerHTML = '<span class="badge ' + compClass + '">' + escapeHtml(viewModel.complexity) + '</span>';

            // Risk
            var riskClass = getBadgeClass(viewModel.risk);
            document.getElementById('a-risk').innerHTML = '<span class="badge ' + riskClass + '">' + escapeHtml(viewModel.risk) + '</span>';

            // AI usage
            document.getElementById('a-ai-usage').innerHTML = '<span style="color:var(--mp-muted);font-size:12px;">' + escapeHtml(viewModel.aiUsage.message) + '</span>';

            // Rules
            var rulesEl = document.getElementById('a-rules');
            if (viewModel.applicableRules && viewModel.applicableRules.length > 0) {
                rulesEl.innerHTML = viewModel.applicableRules.map(function(r) {
                    return '<div style="padding:3px 0;font-size:12px;">• ' + escapeHtml(r) + '</div>';
                }).join('');
            } else {
                rulesEl.innerHTML = '<div style="color:var(--mp-muted);font-size:12px;">No specific project rules apply to this task.</div>';
            }

            // Files
            var filesEl = document.getElementById('a-files');
            if (viewModel.targetFiles && viewModel.targetFiles.length > 0) {
                filesEl.innerHTML = viewModel.targetFiles.map(function(f) {
                    var opClass = 'file-op-' + f.operation;
                    var opLabel = f.operation === 'create' ? '+' : f.operation === 'delete' ? '−' : '~';
                    return '<li class="file-item">' +
                        '<span class="file-op ' + opClass + '">' + opLabel + '</span>' +
                        '<span>' + escapeHtml(f.path) + '</span>' +
                        (f.reason ? '<span class="file-reason">' + escapeHtml(f.reason) + '</span>' : '') +
                        '</li>';
                }).join('');
            } else {
                filesEl.innerHTML = '<li class="empty-state">No specific files identified yet. Context pack generation will discover relevant files.</li>';
            }

            // Validation note
            var validNote = document.getElementById('a-validation-note');
            if (viewModel.isDocsOnly) {
                validNote.textContent = 'Docs-only change detected — tests not required unless explicitly requested.';
                validNote.classList.remove('hidden');
            } else {
                validNote.textContent = 'Validation will run after patch is applied to catch regressions.';
                validNote.classList.remove('hidden');
            }
        }

        function resetToInput() {
            document.getElementById('submit-btn').classList.remove('hidden');
            document.getElementById('loading-state').classList.add('hidden');
            document.getElementById('analysis-section').classList.add('hidden');
            document.getElementById('task-input-card').style.opacity = '1';
            document.getElementById('task-input-card').style.pointerEvents = 'auto';
            updateStepper('task');
        }

        window.handleMessage = function(msg) {
            switch (msg.type) {
                case 'view-content':
                    if (msg.payload.viewId === 'task-loading') {
                        // Already handled via loading state
                    } else if (msg.payload.viewId === 'task-analysis') {
                        try {
                            var viewModel = JSON.parse(msg.payload.html);
                            renderAnalysis(viewModel);
                        } catch(e) {
                            document.getElementById('error-area').textContent = 'Failed to render analysis.';
                            resetToInput();
                        }
                    } else if (msg.payload.viewId === 'btn-loading') {
                        var which = msg.payload.html;
                        if (which === 'context') { updateStepper('context'); }
                        else if (which === 'patch') { updateStepper('route'); }
                    } else if (msg.payload.viewId === 'btn-reset') {
                        var resetWhich = msg.payload.html;
                        if (resetWhich === 'context') { restoreBtn('context-btn'); }
                        else if (resetWhich === 'patch') { restoreBtn('patch-btn'); }
                    } else if (msg.payload.viewId === 'context-done') {
                        updateStepper('context');
                        restoreBtn('context-btn');
                        try {
                            var cdata = JSON.parse(msg.payload.html);
                            var aiEl = document.getElementById('a-cost-status');
                            if (aiEl) {
                                aiEl.innerHTML = '<div class="ai-dot active"></div><span>Context ready — ' + cdata.tokens + ' tokens, $' + cdata.cost + ' (' + cdata.files + ' files)</span>';
                            }
                        } catch(e) {}
                    } else if (msg.payload.viewId === 'patch-done') {
                        updateStepper('approval');
                        restoreBtn('patch-btn');
                        try {
                            var pdata = JSON.parse(msg.payload.html);
                            var aiEl2 = document.getElementById('a-cost-status');
                            if (aiEl2) {
                                aiEl2.innerHTML = '<div class="ai-dot active"></div><span>Patch generated — ' + pdata.fileCount + ' file(s). Review required.</span>';
                            }
                        } catch(e) {}
                    }
                    break;
                case 'error':
                    document.getElementById('error-area').textContent = msg.payload.message;
                    restoreBtn('context-btn');
                    restoreBtn('patch-btn');
                    break;
            }
        };

        function restoreBtn(btnId) {
            var btn = document.getElementById(btnId);
            if (btn && btn.dataset.originalText) {
                btn.textContent = btn.dataset.originalText;
                btn.disabled = false;
                btn.style.opacity = '1';
            }
        }
        </script>`;
    }
}

