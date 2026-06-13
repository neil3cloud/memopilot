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

    /** Generate the patch */
    async generatePatch(): Promise<void> {
        if (!this.state.modelDecision || !this.state.contextPack) {
            this.transition('error', { error: 'Missing model decision or context' });
            return;
        }

        try {
            const patch = await this.client.generatePatch({
                task_description: this.state.taskDescription,
                context_files: this.state.contextPack.files.map(f => f.path),
                mode: this.state.mode,
                model_id: this.state.modelDecision.recommended.model_id,
            });
            // Stop here — developer must approve
            this.transition('awaiting_approval', { patch });
        } catch (err: unknown) {
            this.transition('error', { error: this.errorMsg(err) });
        }
    }

    /** Developer approves the patch — proceed to validation */
    async approve(): Promise<void> {
        if (this.state.stage !== 'awaiting_approval' || !this.state.patch) {
            return;
        }

        this.transition('validating');

        try {
            const validation = await this.client.validatePatches({
                patches: this.state.patch.patches.map(p => ({
                    path: p.path,
                    action: p.action,
                    diff: p.diff,
                })),
                checks: ['syntax', 'lint', 'test_impact', 'security'],
            });
            this.transition(validation.can_apply ? 'applying' : 'awaiting_approval', { validation });
        } catch (err: unknown) {
            this.transition('error', { error: this.errorMsg(err) });
        }
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
