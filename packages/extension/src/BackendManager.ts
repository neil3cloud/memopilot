import * as vscode from 'vscode';
import * as crypto from 'crypto';
import * as path from 'path';
import * as fs from 'fs';
import { ChildProcess, spawn } from 'child_process';

export class BackendManager {
    private process: ChildProcess | undefined;
    private token: string;
    private port: number | undefined;
    private workspacePath: string;
    private outputChannel: vscode.OutputChannel;
    private lockFilePath: string;

    constructor(workspacePath: string, outputChannel: vscode.OutputChannel) {
        this.workspacePath = workspacePath;
        this.outputChannel = outputChannel;
        this.token = crypto.randomBytes(32).toString('hex');
        this.lockFilePath = path.join(workspacePath, '.memopilot', 'agent.lock');
    }

    get baseUrl(): string {
        if (!this.port) {
            throw new Error('Backend not started');
        }
        return `http://127.0.0.1:${this.port}`;
    }

    get authToken(): string {
        return this.token;
    }

    async start(): Promise<void> {
        const pythonPath = await this.resolvePython();
        const agentDir = this.resolveAgentDir();
        const agentParent = path.resolve(agentDir, '..');
        const mainScript = path.join(agentDir, 'main.py');

        if (!fs.existsSync(mainScript)) {
            throw new Error(`Backend script not found: ${mainScript}`);
        }

        // Ensure .memopilot directory exists for lockfile
        const memopilotDir = path.join(this.workspacePath, '.memopilot');
        if (!fs.existsSync(memopilotDir)) {
            fs.mkdirSync(memopilotDir, { recursive: true });
        }

        // Clean stale lockfile
        if (fs.existsSync(this.lockFilePath)) {
            fs.unlinkSync(this.lockFilePath);
        }

        this.outputChannel.appendLine(`[MemoPilot] Starting backend: ${pythonPath} -m agent.main`);
        this.outputChannel.appendLine(`[MemoPilot] Agent dir: ${agentDir}`);
        this.outputChannel.appendLine(`[MemoPilot] Agent parent (cwd): ${agentParent}`);
        this.outputChannel.appendLine(`[MemoPilot] Workspace: ${this.workspacePath}`);

        this.process = spawn(pythonPath, ['-m', 'agent.main'], {
            cwd: agentParent,
            env: {
                ...process.env,
                MEMOPILOT_TOKEN: this.token,
                MEMOPILOT_WORKSPACE: this.workspacePath,
                PYTHONPATH: agentParent,
            },
            stdio: ['ignore', 'pipe', 'pipe'],
        });

        this.process.stdout?.on('data', (data: Buffer) => {
            this.outputChannel.appendLine(`[Backend] ${data.toString().trim()}`);
        });

        this.process.stderr?.on('data', (data: Buffer) => {
            this.outputChannel.appendLine(`[Backend:err] ${data.toString().trim()}`);
        });

        this.process.on('exit', (code) => {
            this.outputChannel.appendLine(`[MemoPilot] Backend exited with code ${code}`);
            this.port = undefined;
        });

        // Wait for lockfile to appear with port
        this.port = await this.waitForPort(10000);
        this.outputChannel.appendLine(`[MemoPilot] Backend started on port ${this.port}`);
    }

    async stop(): Promise<void> {
        if (this.process) {
            this.process.kill('SIGTERM');
            this.process = undefined;
        }
        this.port = undefined;

        // Clean lockfile
        if (fs.existsSync(this.lockFilePath)) {
            try {
                fs.unlinkSync(this.lockFilePath);
            } catch {
                // Ignore cleanup errors
            }
        }
    }

    async request(method: string, urlPath: string, body?: unknown): Promise<unknown> {
        if (!this.port) {
            throw new Error('Backend not started');
        }

        const url = `${this.baseUrl}${urlPath}`;
        const maxRetries = 3;
        const retryDelay = 500;

        for (let attempt = 0; attempt < maxRetries; attempt++) {
            try {
                const response = await fetch(url, {
                    method,
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Agent-Token': this.token,
                    },
                    body: body ? JSON.stringify(body) : undefined,
                });

                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(`HTTP ${response.status}: ${text}`);
                }

                return await response.json();
            } catch (err) {
                if (attempt < maxRetries - 1) {
                    await this.sleep(retryDelay);
                } else {
                    throw err;
                }
            }
        }
    }

    private async resolvePython(): Promise<string> {
        // 1. Check memopilot.pythonPath setting
        const config = vscode.workspace.getConfiguration('memopilot');
        const configuredPath = config.get<string>('pythonPath');
        if (configuredPath && fs.existsSync(configuredPath)) {
            return configuredPath;
        }

        // 2. Check workspace .venv
        const isWindows = process.platform === 'win32';
        const venvPython = path.join(
            this.workspacePath,
            '.venv',
            isWindows ? 'Scripts' : 'bin',
            isWindows ? 'python.exe' : 'python',
        );
        if (fs.existsSync(venvPython)) {
            return venvPython;
        }

        // 3. Check python.defaultInterpreterPath
        const pythonConfig = vscode.workspace.getConfiguration('python');
        const defaultInterpreter = pythonConfig.get<string>('defaultInterpreterPath');
        if (defaultInterpreter && defaultInterpreter !== 'python' && fs.existsSync(defaultInterpreter)) {
            return defaultInterpreter;
        }

        // 4. Fallback to PATH
        return isWindows ? 'python' : 'python3';
    }

    private resolveAgentDir(): string {
        // Look for agent dir relative to extension
        // In dev: the agent is at packages/agent/agent/
        // We need to find it relative to the extension's location
        const extensionRoot = path.resolve(__dirname, '..');
        const monoRepoAgent = path.resolve(extensionRoot, '..', 'agent', 'agent');
        if (fs.existsSync(monoRepoAgent)) {
            return monoRepoAgent;
        }

        // Fallback: agent bundled alongside extension
        const bundledAgent = path.resolve(extensionRoot, 'agent');
        if (fs.existsSync(bundledAgent)) {
            return bundledAgent;
        }

        throw new Error(
            `Cannot find MemoPilot agent directory. Looked at:\n` +
            `  ${monoRepoAgent}\n  ${bundledAgent}`
        );
    }

    private stderrBuffer: string = '';

    private async waitForPort(timeoutMs: number): Promise<number> {
        const start = Date.now();
        const pollInterval = 200;

        // Capture stderr for error reporting
        this.stderrBuffer = '';
        this.process?.stderr?.on('data', (data: Buffer) => {
            this.stderrBuffer += data.toString();
        });

        while (Date.now() - start < timeoutMs) {
            // Check if process already exited
            if (this.process?.exitCode !== null && this.process?.exitCode !== undefined) {
                throw new Error(
                    `Backend process exited with code ${this.process.exitCode}.\n${this.stderrBuffer.slice(-500)}`
                );
            }
            if (fs.existsSync(this.lockFilePath)) {
                try {
                    const content = fs.readFileSync(this.lockFilePath, 'utf8').trim();
                    const data = JSON.parse(content);
                    if (data.port && typeof data.port === 'number') {
                        return data.port;
                    }
                } catch {
                    // Lockfile not ready yet, keep polling
                }
            }
            await this.sleep(pollInterval);
        }

        // Timeout — kill process
        this.process?.kill('SIGTERM');
        const hint = this.stderrBuffer
            ? `\nLast stderr:\n${this.stderrBuffer.slice(-500)}`
            : '';
        throw new Error(`Backend failed to start within 10 seconds (no lockfile)${hint}`);
    }

    private sleep(ms: number): Promise<void> {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}
