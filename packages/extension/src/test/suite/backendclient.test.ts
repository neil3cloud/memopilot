import * as assert from 'assert';
import * as http from 'http';

/**
 * Test SSE (Server-Sent Events) streaming functionality
 */
suite('BackendClient SSE Streaming', () => {
    let testServer: http.Server | undefined;
    let testServerPort: number;

    setup(async () => {
        // Start a simple local HTTP SSE server for testing
        return new Promise<void>((resolve) => {
            testServer = http.createServer((req, res) => {
                if (req.url === '/stream') {
                    res.writeHead(200, {
                        'Content-Type': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive',
                    });

                    // Send test events
                    let tokenCount = 0;
                    const interval = setInterval(() => {
                        if (tokenCount < 5) {
                            res.write(`data: {"type":"token","content":"test_${tokenCount}"}\n\n`);
                            tokenCount++;
                        } else {
                            res.write(`data: {"type":"done"}\n\n`);
                            clearInterval(interval);
                            res.end();
                        }
                    }, 50);
                } else {
                    res.writeHead(404);
                    res.end();
                }
            });

            testServer.listen(0, '127.0.0.1', () => {
                testServerPort = (testServer!.address() as any).port;
                resolve();
            });
        });
    });

    teardown(async () => {
        if (testServer) {
            return new Promise<void>((resolve) => {
                testServer!.close(() => resolve());
            });
        }
    });

    test('SSE stream connects and receives events', async () => {
        const tokens: string[] = [];
        
        return new Promise<void>((resolve, reject) => {
            const eventSource = new (require('eventsource') || require('events').EventEmitter)() as any;
            
            // Use fetch with ReadableStream for SSE testing
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 5000);

            fetch(`http://127.0.0.1:${testServerPort}/stream`, {
                signal: controller.signal,
            })
                .then(async (response) => {
                    const reader = response.body?.getReader();
                    if (!reader) {
                        reject(new Error('No response body'));
                        return;
                    }

                    const decoder = new TextDecoder();
                    let buffer = '';

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;

                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop() || '';

                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                try {
                                    const data = JSON.parse(line.slice(6));
                                    if (data.type === 'token') {
                                        tokens.push(data.content);
                                    } else if (data.type === 'done') {
                                        clearTimeout(timeoutId);
                                        assert.strictEqual(tokens.length, 5, 'Should receive 5 tokens');
                                        assert.ok(tokens[0].startsWith('test_'), 'Tokens should match pattern');
                                        resolve();
                                        return;
                                    }
                                } catch (e) {
                                    // Skip non-JSON lines
                                }
                            }
                        }
                    }
                    reject(new Error('Stream ended without done event'));
                })
                .catch(reject);
        });
    });

    test('SSE stream cancellation stops reading', async () => {
        const controller = new AbortController();
        
        const promise = fetch(`http://127.0.0.1:${testServerPort}/stream`, {
            signal: controller.signal,
        });

        // Cancel after short delay
        setTimeout(() => controller.abort(), 100);

        try {
            await promise;
            assert.fail('Should have thrown AbortError');
        } catch (err: any) {
            assert.ok(err.name === 'AbortError' || err.message.includes('aborted'), 'Should abort');
        }
    });

    test('SSE stream handles connection errors', async () => {
        const controller = new AbortController();
        
        try {
            await fetch(`http://127.0.0.1:99999/stream`, {
                signal: controller.signal,
            });
            assert.fail('Should have thrown connection error');
        } catch (err: any) {
            assert.ok(
                err.message.includes('ECONNREFUSED') || err.message.includes('Failed to fetch'),
                'Should fail with connection error'
            );
        }
    });
});
