import * as http from 'http';
import * as vscode from 'vscode';
import { BackendManager } from './BackendManager';

// Frontier models to exclude — pick fastest non-frontier available
const FRONTIER_FAMILIES = ['o1', 'o3', 'o4', 'gpt-4o', 'claude-opus', 'gemini-ultra', 'claude-3-5'];

/**
 * Probes vscode.lm once at startup, registers the best non-frontier model
 * with the backend, then listens on /v1/synthesis/stream for LLM_REQUEST
 * events and fulfills them using the cached model.
 *
 * In Cursor or VS Code without Copilot, vscode.lm is unavailable — the probe
 * posts available=false and synthesis falls back to local LLM or skips.
 */
export class SynthesisHostClient {
    private manager: BackendManager;
    private cachedModel: unknown = null;
    private listening = false;
    private disposed = false;
    private _onReadyCallback: ((available: boolean, modelId: string) => void) | undefined;

    constructor(manager: BackendManager, onReady?: (available: boolean, modelId: string) => void) {
        this.manager = manager;
        this._onReadyCallback = onReady;
    }

    async probe(): Promise<void> {
        const lm = this._getLmApi();
        if (!lm) {
            await this._notifyBackend(false, '');
            return;
        }

        try {
            const models = await lm.selectChatModels({ vendor: 'copilot' });
            if (!models || models.length === 0) {
                await this._notifyBackend(false, '');
                return;
            }

            // Pick best non-frontier model — smallest/fastest that can handle synthesis
            type ModelLike = { family?: string; id?: string; maxInputTokens?: number };
            const nonFrontier = (models as ModelLike[]).filter(
                m => !FRONTIER_FAMILIES.some(f => (m.family ?? m.id ?? '').toLowerCase().includes(f))
            );
            const candidates = nonFrontier.length > 0 ? nonFrontier : (models as ModelLike[]);
            // Among candidates, prefer largest context window (more headroom for synthesis input)
            const picked = candidates.sort((a, b) => (b.maxInputTokens ?? 0) - (a.maxInputTokens ?? 0))[0];

            this.cachedModel = picked;
            await this._notifyBackend(true, (picked as ModelLike).id ?? (picked as ModelLike).family ?? 'unknown');

            if (!this.listening) {
                this.listening = true;
                void this._listenForSynthesisRequests();
            }
        } catch {
            await this._notifyBackend(false, '');
        }
    }

    private async _notifyBackend(available: boolean, modelId: string): Promise<void> {
        try {
            await this.manager.request('POST', '/v1/host/model-ready', { available, model_id: modelId });
        } catch {
            // Backend may not be ready yet — non-fatal
        }
        this._onReadyCallback?.(available, modelId);
    }

    private _getLmApi(): { selectChatModels: (selector: object) => Thenable<unknown[]> } | undefined {
        const lm = (vscode as unknown as Record<string, unknown>).lm as
            | { selectChatModels: (selector: object) => Thenable<unknown[]> }
            | undefined;
        return lm && typeof lm.selectChatModels === 'function' ? lm : undefined;
    }

    private _listenForSynthesisRequests(): void {
        if (this.disposed) { return; }

        const url = new URL('/v1/synthesis/stream', this.manager.baseUrl);
        let req: http.ClientRequest | undefined;

        const connect = () => {
            if (this.disposed) { return; }
            req = http.request(
                {
                    hostname: '127.0.0.1',
                    port: parseInt(url.port || '80', 10),
                    path: url.pathname,
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
                                    const event = JSON.parse(line.slice(6));
                                    if (event.type === 'LLM_REQUEST') {
                                        void this._handleSynthesisRequest(event);
                                    }
                                } catch { /* malformed JSON */ }
                            }
                        }
                    });
                    res.on('end', () => {
                        // Reconnect after short delay if not disposed
                        if (!this.disposed) {
                            setTimeout(connect, 2000);
                        }
                    });
                },
            );
            req.on('error', () => {
                if (!this.disposed) {
                    setTimeout(connect, 5000);
                }
            });
            req.end();
        };

        connect();
    }

    private async _handleSynthesisRequest(event: {
        relay_id?: string;
        synthesis_id?: string;
        request_type?: string;
        system: string;
        user: string;
        ctx_tokens: number;
    }): Promise<void> {
        const relayId = event.relay_id ?? event.synthesis_id ?? '';
        const lm = this._getLmApi();
        if (!lm || !this.cachedModel) {
            await this._postResponse(relayId, '', true, 'no_host_model');
            return;
        }

        try {
            type ModelLike = { sendRequest?: Function; maxInputTokens?: number };
            const model = this.cachedModel as ModelLike;
            if (typeof model.sendRequest !== 'function') {
                await this._postResponse(relayId, '', true, 'model_missing_sendRequest');
                return;
            }

            const cts = new vscode.CancellationTokenSource();
            const messages = [
                { role: 1 /* User */, content: event.system + '\n\n' + event.user },
            ];

            const response = await model.sendRequest(messages, {}, cts.token);
            const stream = response as { stream?: AsyncIterable<{ value?: string }> };

            if (stream.stream) {
                for await (const fragment of stream.stream) {
                    const token = (fragment as { value?: string }).value ?? '';
                    if (token) {
                        await this._postResponse(relayId, token, false, undefined);
                    }
                }
            }
            await this._postResponse(relayId, '', true, undefined);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            await this._postResponse(relayId, '', true, msg);
        }
    }

    private async _postResponse(
        relayId: string,
        token: string,
        isFinal: boolean,
        error: string | undefined,
    ): Promise<void> {
        try {
            await this.manager.request('POST', '/v1/synthesis/host-response', {
                relay_id: relayId,
                synthesis_id: relayId, // legacy compat
                token,
                is_final: isFinal,
                error: error ?? null,
            });
        } catch { /* best-effort */ }
    }

    dispose(): void {
        this.disposed = true;
    }
}
