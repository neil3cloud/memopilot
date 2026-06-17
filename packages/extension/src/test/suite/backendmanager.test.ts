import * as assert from 'assert';

/**
 * Test BackendManager helper and parsing methods
 * These are pure functions that don't require actual process management
 */
suite('BackendManager Parsing Helpers', () => {
    // Extract these helpers from BackendManager for testing
    // In practice, we'd export these as static methods or a utility module
    
    const parseMissingModules = (stdout: string): string[] => {
        const lines = stdout
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter((line) => line.length > 0);
        const jsonLine = lines.length > 0 ? lines[lines.length - 1] : '[]';
        try {
            const parsed = JSON.parse(jsonLine);
            if (Array.isArray(parsed)) {
                return parsed.filter((item): item is string => typeof item === 'string');
            }
        } catch {
            // Fall through
        }
        return [];
    };

    const extractPortFromLogs = (stdout: string, stderr: string): number | undefined => {
        const combined = `${stdout}\n${stderr}`;
        const listeningMatch = combined.match(/Backend listening on 127\.0\.0\.1:(\d+)/i);
        if (listeningMatch) {
            return Number(listeningMatch[1]);
        }
        const startedMatch = combined.match(/MemoPilot backend started on port (\d+)/i);
        if (startedMatch) {
            return Number(startedMatch[1]);
        }
        const uvicornMatch = combined.match(/Uvicorn running on http:\/\/127\.0\.0\.1:(\d+)/i);
        if (uvicornMatch) {
            return Number(uvicornMatch[1]);
        }
        return undefined;
    };

    test('parseMissingModules extracts module names from JSON output', () => {
        const stdout = 'some log line\n["fastapi", "uvicorn"]\n';
        const missing = parseMissingModules(stdout);
        assert.deepStrictEqual(missing, ['fastapi', 'uvicorn']);
    });

    test('parseMissingModules returns empty array for valid JSON with no missing modules', () => {
        const stdout = 'some log\n[]\n';
        const missing = parseMissingModules(stdout);
        assert.deepStrictEqual(missing, []);
    });

    test('parseMissingModules handles invalid JSON gracefully', () => {
        const stdout = 'some log\nnot json at all\n';
        const missing = parseMissingModules(stdout);
        assert.deepStrictEqual(missing, []);
    });

    test('parseMissingModules filters non-string values', () => {
        const stdout = '["fastapi", 123, "uvicorn", null, true]\n';
        const missing = parseMissingModules(stdout);
        assert.deepStrictEqual(missing, ['fastapi', 'uvicorn']);
    });

    test('extractPortFromLogs finds port from backend listening message', () => {
        const stdout = 'Backend listening on 127.0.0.1:8765\n';
        const port = extractPortFromLogs(stdout, '');
        assert.strictEqual(port, 8765);
    });

    test('extractPortFromLogs finds port from MemoPilot message', () => {
        const stdout = 'MemoPilot backend started on port 9999\n';
        const port = extractPortFromLogs(stdout, '');
        assert.strictEqual(port, 9999);
    });

    test('extractPortFromLogs finds port from Uvicorn message', () => {
        const stderr = 'Uvicorn running on http://127.0.0.1:5432\n';
        const port = extractPortFromLogs('', stderr);
        assert.strictEqual(port, 5432);
    });

    test('extractPortFromLogs prefers first matching pattern', () => {
        const stdout = 'Backend listening on 127.0.0.1:1111\nMemoPilot backend started on port 2222\n';
        const port = extractPortFromLogs(stdout, '');
        assert.strictEqual(port, 1111, 'Should use first match');
    });

    test('extractPortFromLogs returns undefined when no port found', () => {
        const stdout = 'Some log without port\n';
        const port = extractPortFromLogs(stdout, '');
        assert.strictEqual(port, undefined);
    });

    test('extractPortFromLogs searches both stdout and stderr', () => {
        const stdout = 'log line';
        const stderr = 'Uvicorn running on http://127.0.0.1:3456';
        const port = extractPortFromLogs(stdout, stderr);
        assert.strictEqual(port, 3456);
    });

    test('extractPortFromLogs extracts port with edge cases', () => {
        // Port at start of line
        let port = extractPortFromLogs('Backend listening on 127.0.0.1:7777', '');
        assert.strictEqual(port, 7777);

        // Port in middle of sentence
        port = extractPortFromLogs('The backend is listening on 127.0.0.1:8888 now', '');
        assert.strictEqual(port, 8888);

        // Very large port number
        port = extractPortFromLogs('Backend listening on 127.0.0.1:65535', '');
        assert.strictEqual(port, 65535);
    });

    test('parseMissingModules preserves order', () => {
        const stdout = '["z", "a", "m", "b"]\n';
        const missing = parseMissingModules(stdout);
        assert.deepStrictEqual(missing, ['z', 'a', 'm', 'b']);
    });
});
