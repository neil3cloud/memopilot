import * as http from 'http';
import * as vscode from 'vscode';
import { BackendManager } from './BackendManager';

interface LLMRequestEvent {
    type: 'LLM_REQUEST';
    task_run_id: string;
    system: string;
    user: string;
    ctx_tokens: number;
}

interface SSEEvent {
    type: string;
    [key: string]: unknown;
}

/**
 * Listens on the backend SSE stream for a given task run.
 * On LLM_REQUEST events, calls the VS Code Language Model API (Copilot)
 * and streams tokens back to the backend via POST /v1/llm/host-response.
 *
 * In Cursor or VS Code without Copilot, no models are available —
 * the client posts a no_host_models error so generate_patch() can fall through.
 */
export class HostModelClient {
    private manager: BackendManager;
    private activeListeners = new Map<string, () => void>();

    constructor(manager: BackendManager) {
        this.manager = manager;
    }

    /**
     * Start listening for LLM_REQUEST events on the given task run's SSE stream.
     * The returned disposable stops the listener.
     */
    listenForTask(taskRunId: string): vscode.Disposable {
        const cancel = this.startSSE(taskRunId);
        return { dispose: cancel };
    }

    private startSSE(taskRunId: string): () => void {
        const url = new URL(`/v1/task/${encodeURIComponent(taskRunId)}/stream`, this.manager.baseUrl);
        let req: http.ClientRequest | undefined;
        let cancelled = false;

        const connect = () => {
            if (cancelled) { return; }
            req = http.request(
                {
                    hostname: '127.0.0.1',
                    port: parseInt(url.port || '80', 10),
                    path: url.pathname + url.search,
                    headers: {
                        'X-Agent-Token': this.manager.authToken,
                        'Accept': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                    },
                },
                (res) => {
                    let buffer = '';
                    res.on('data', (chunk: Buffer) => {
                        buffer += chunk.toString('utf8');
                        const lines = buffer.split('\n');
                        buffer = lines.pop() ?? '';
                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                try {
                                    const event: SSEEvent = JSON.parse(line.slice(6));
                                    void this.onEvent(taskRunId, event);
                                } catch {
                                    // malformed JSON — skip
                                }
                            }
                        }
                    });
                    res.on('end', () => {
                        this.activeListeners.delete(taskRunId);
                    });
                },
            );
            req.on('error', () => {
                // Backend not ready or task ended — no retry needed
                this.activeListeners.delete(taskRunId);
            });
            req.end();
        };

        connect();
        this.activeListeners.set(taskRunId, () => {
            cancelled = true;
            req?.destroy();
        });

        return () => {
            cancelled = true;
            req?.destroy();
            this.activeListeners.delete(taskRunId);
        };
    }

    private async onEvent(taskRunId: string, event: SSEEvent): Promise<void> {
        if (event.type !== 'LLM_REQUEST') { return; }
        const llmReq = event as unknown as LLMRequestEvent;
        await this.handleLLMRequest(taskRunId, llmReq);
    }

    private async handleLLMRequest(taskRunId: string, req: LLMRequestEvent): Promise<void> {
        // Guard: vscode.lm may not be available in all VS Code versions / Cursor
        const lm = (vscode as unknown as Record<string, unknown>).lm as
            | { selectChatModels: (selector: object) => Thenable<unknown[]> }
            | undefined;

        if (!lm || typeof lm.selectChatModels !== 'function') {
            await this.postHostResponse(taskRunId, '', false, 'vscode.lm API not available');
            return;
        }

        const models = await lm.selectChatModels({ vendor: 'copilot' });
        if (!models || models.length === 0) {
            await this.postHostResponse(taskRunId, '', false, 'no_host_models: no Copilot models available');
            return;
        }

        // Pick model with largest context window that fits
        const needed = req.ctx_tokens;
        type ModelLike = { maxInputTokens?: number; sendRequest?: Function; id?: string };
        const sorted = (models as ModelLike[]).sort(
            (a, b) => (b.maxInputTokens ?? 0) - (a.maxInputTokens ?? 0),
        );
        const model = sorted.find(m => (m.maxInputTokens ?? Infinity) >= needed) ?? sorted[0];

        try {
            const cts = new vscode.CancellationTokenSource();
            const chatModel = model as ModelLike & { sendRequest: Function };

            // Build messages using VS Code LM API message format
            const messages = [
                { role: 1 /* User */, content: req.system + '\n\n' + req.user },
            ];

            const response = await chatModel.sendRequest(messages, {}, cts.token);

            // Stream tokens back
            const stream = response as { stream?: AsyncIterable<{ value?: string }> };
            if (stream.stream) {
                for await (const fragment of stream.stream) {
                    const token = fragment.value ?? '';
                    if (token) {
                        await this.postHostResponse(taskRunId, token, false, undefined);
                    }
                }
            }
            await this.postHostResponse(taskRunId, '', true, undefined);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            await this.postHostResponse(taskRunId, '', false, msg);
        }
    }

    private async postHostResponse(
        taskRunId: string,
        token: string,
        isFinal: boolean,
        error: string | undefined,
    ): Promise<void> {
        try {
            await this.manager.request('POST', '/v1/llm/host-response', {
                task_run_id: taskRunId,
                token,
                is_final: isFinal,
                error: error ?? null,
            });
        } catch {
            // Best-effort — if backend is gone, nothing to do
        }
    }

    dispose(): void {
        for (const cancel of this.activeListeners.values()) {
            cancel();
        }
        this.activeListeners.clear();
    }
}
