import * as vscode from 'vscode';
import {
    BackendClient,
    TaskAnalyzeResponse,
    ContextBuildResponse,
    ModelRouteResponse,
    GeneratePatchResponse,
    ValidateResponse,
} from '../BackendClient';

export type TaskFlowStage =
    | 'idle'
    | 'analyzing'
    | 'context_building'
    | 'routing'
    | 'generating_patch'
    | 'awaiting_approval'
    | 'validating'
    | 'applying'
    | 'done'
    | 'error';

export interface TaskFlowState {
    stage: TaskFlowStage;
    taskDescription: string;
    constraints: string[];
    mode: string;
    analysis?: TaskAnalyzeResponse;
    contextPack?: ContextBuildResponse;
    modelDecision?: ModelRouteResponse;
    patch?: GeneratePatchResponse;
    validation?: ValidateResponse;
    error?: string;
    streamingToken?: string;
    taskRunId?: string;
}

type StageChangeListener = (state: TaskFlowState) => void;

/**
 * Orchestrates the full MemoPilot task flow:
 * analyze → context build → model route → generate patch → approval → validate → apply
 */
export class TaskFlowController {
    private state: TaskFlowState;
    private listeners: StageChangeListener[] = [];

    constructor(private client: BackendClient) {
        this.state = {
            stage: 'idle',
            taskDescription: '',
            constraints: [],
            mode: 'auto',
        };
    }

    getState(): Readonly<TaskFlowState> {
        return this.state;
    }

    /** Seed the controller with an analysis already obtained externally */
    setAnalysis(description: string, constraints: string[], mode: string, analysis: TaskAnalyzeResponse): void {
        this.state = {
            ...this.state,
            stage: 'context_building',
            taskDescription: description,
            constraints,
            mode: analysis.suggested_mode || mode,
            analysis,
            contextPack: undefined,
            modelDecision: undefined,
            patch: undefined,
            validation: undefined,
            error: undefined,
        };
    }

    onStageChange(listener: StageChangeListener): vscode.Disposable {
        this.listeners.push(listener);
        return new vscode.Disposable(() => {
            const idx = this.listeners.indexOf(listener);
            if (idx >= 0) this.listeners.splice(idx, 1);
        });
    }

    private emit(): void {
        for (const listener of this.listeners) {
            listener(this.state);
        }
    }

    private transition(stage: TaskFlowStage, updates: Partial<TaskFlowState> = {}): void {
        this.state = { ...this.state, stage, ...updates };
        this.emit();
    }

    /** Start a new task flow from analysis */
    async startTask(description: string, constraints: string[], mode: string): Promise<void> {
        this.transition('analyzing', {
            taskDescription: description,
            constraints,
            mode,
            analysis: undefined,
            contextPack: undefined,
            modelDecision: undefined,
            patch: undefined,
            validation: undefined,
            error: undefined,
        });

        try {
            const analysis = await this.client.analyzeTask({ description, constraints, mode });
            this.transition('context_building', { analysis, mode: analysis.suggested_mode || mode });
        } catch (err: unknown) {
            this.transition('error', { error: this.errorMsg(err) });
            return;
        }

        // Auto-proceed to context build
        await this.buildContext();
    }

    /** Build the context pack */
    async buildContext(): Promise<void> {
        if (!this.state.analysis) {
            this.transition('error', { error: 'No analysis available' });
            return;
        }

        try {
            const contextPack = await this.client.buildContextPack({
                task_description: this.state.taskDescription,
                suggested_files: this.state.analysis.suggested_files,
                mode: this.state.mode,
            });
            this.transition('routing', { contextPack });
        } catch (err: unknown) {
            this.transition('error', { error: this.errorMsg(err) });
            return;
        }

        // Auto-proceed to model routing
        await this.routeModel();
    }

    /** Select the model */
    async routeModel(): Promise<void> {
        if (!this.state.contextPack) {
            this.transition('error', { error: 'No context pack available' });
            return;
        }

        try {
            const modelDecision = await this.client.routeModel({
                context_tokens: this.state.contextPack.total_tokens,
                task_type: this.state.mode,
            });
            this.transition('generating_patch', { modelDecision });
        } catch (err: unknown) {
            this.transition('error', { error: this.errorMsg(err) });
            return;
        }

        // Auto-proceed to patch generation
        await this.generatePatch();
    }

    /** Generate the patch, streaming tokens to listeners as they arrive */
    async generatePatch(): Promise<void> {
        if (!this.state.modelDecision || !this.state.contextPack) {
            this.transition('error', { error: 'Missing model decision or context' });
            return;
        }

        // Generate a task run ID so we can open the SSE stream before posting
        const taskRunId = crypto.randomUUID();
        this.transition('generating_patch', { taskRunId, streamingToken: undefined });

        // Open SSE stream to receive tokens as the LLM generates them
        let streamingContent = '';
        const stopStream = this.client.openTokenStream(taskRunId, (token) => {
            streamingContent += token;
            this.transition('generating_patch', { streamingToken: streamingContent });
        });

        try {
            const patch = await this.client.generatePatch({
                task_description: this.state.taskDescription,
                context_files: this.state.contextPack.files.map(f => f.path),
                mode: this.state.mode,
                model_id: this.state.modelDecision.recommended.model_id,
                task_run_id: taskRunId,
            });
            // Stop here — developer must approve
            this.transition('awaiting_approval', { patch, streamingToken: undefined });
        } catch (err: unknown) {
            this.transition('error', { error: this.errorMsg(err), streamingToken: undefined });
        } finally {
            stopStream();
        }
    }

    /** Developer approves the patch — proceed to validation */
    async approve(): Promise<void> {
        if (this.state.stage !== 'awaiting_approval' || !this.state.patch) {
            return;
        }

        this.transition('validating');

        try {
            // For docs-only changes, skip heavy validation (syntax, test_impact)
            const docExtensions = ['.md', '.txt', '.rst', '.adoc', '.mdx'];
            const isDocsOnly = this.state.patch.patches.every(p =>
                docExtensions.some(ext => p.path.toLowerCase().endsWith(ext))
            );
            const checks = isDocsOnly ? ['security'] : ['syntax', 'lint', 'test_impact', 'security'];

            const validation = await this.client.validatePatches({
                patches: this.state.patch.patches.map(p => ({
                    path: p.path,
                    action: p.action,
                    diff: p.diff,
                })),
                checks,
            });
            this.transition(validation.can_apply ? 'applying' : 'awaiting_approval', { validation });

            // If validation passed, actually write files to disk
            if (validation.can_apply && this.state.patch) {
                await this.applyPatchesToDisk();
            }
        } catch (err: unknown) {
            this.transition('error', { error: this.errorMsg(err) });
        }
    }

    /** Write patch file changes to the workspace */
    private async applyPatchesToDisk(): Promise<void> {
        if (!this.state.patch) { return; }

        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (!workspaceFolders || workspaceFolders.length === 0) {
            this.transition('error', { error: 'No workspace folder open' });
            return;
        }
        const rootUri = workspaceFolders[0].uri;

        for (const filePatch of this.state.patch.patches) {
            const fileUri = vscode.Uri.joinPath(rootUri, filePatch.path);

            if (filePatch.action === 'create' && filePatch.new_content !== null) {
                await vscode.workspace.fs.writeFile(fileUri, Buffer.from(filePatch.new_content, 'utf-8'));
            } else if (filePatch.action === 'modify' && filePatch.new_content !== null) {
                await vscode.workspace.fs.writeFile(fileUri, Buffer.from(filePatch.new_content, 'utf-8'));
            } else if (filePatch.action === 'delete') {
                try {
                    await vscode.workspace.fs.delete(fileUri);
                } catch { /* file may not exist */ }
            }
        }

        this.transition('done');
    }

    /** Developer rejects the patch — reset to idle */
    reject(): void {
        this.transition('idle', {
            taskDescription: '',
            constraints: [],
            mode: 'auto',
            analysis: undefined,
            contextPack: undefined,
            modelDecision: undefined,
            patch: undefined,
            validation: undefined,
            error: undefined,
        });
    }

    /** Mark task as done after applying */
    complete(): void {
        this.transition('done');
    }

    /** Reset the controller */
    reset(): void {
        this.reject();
    }

    private errorMsg(err: unknown): string {
        return err instanceof Error ? err.message : String(err);
    }
}
