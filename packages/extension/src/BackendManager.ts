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
    private stderrBuffer: string = '';
    private stdoutBuffer: string = '';

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

        await this.ensureBackendDependencies(pythonPath);

        this.stderrBuffer = '';
        this.stdoutBuffer = '';

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
            const text = data.toString();
            this.stdoutBuffer += text;
            this.outputChannel.appendLine(`[Backend] ${text.trim()}`);
        });

        this.process.stderr?.on('data', (data: Buffer) => {
            const text = data.toString();
            this.stderrBuffer += text;
            this.outputChannel.appendLine(`[Backend:err] ${text.trim()}`);
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

    private async ensureBackendDependencies(pythonPath: string): Promise<void> {
        const requiredModules: Array<{ module: string; packageName: string }> = [
            { module: 'fastapi', packageName: 'fastapi>=0.109.0' },
            { module: 'uvicorn', packageName: 'uvicorn[standard]>=0.27.0' },
            { module: 'pydantic', packageName: 'pydantic>=2.5.0' },
            { module: 'aiosqlite', packageName: 'aiosqlite>=0.19.0' },
            { module: 'detect_secrets', packageName: 'detect-secrets>=1.5.0' },
            { module: 'openpyxl', packageName: 'openpyxl>=3.1.5' },
            { module: 'pdfplumber', packageName: 'pdfplumber>=0.11.4' },
            { module: 'yaml', packageName: 'pyyaml>=6.0' },
            { module: 'PIL', packageName: 'pillow>=11.0.0' },
            { module: 'docx', packageName: 'python-docx>=1.1.2' },
            { module: 'pptx', packageName: 'python-pptx>=1.0.2' },
        ];

        const moduleScript = [
            'import importlib.util, json',
            `required = ${JSON.stringify(requiredModules.map((item) => item.module))}`,
            'missing = [name for name in required if importlib.util.find_spec(name) is None]',
            'print(json.dumps(missing))',
        ].join(';');
        const check = await this.runPythonCommand(pythonPath, ['-c', moduleScript]);
        if (check.exitCode !== 0) {
            throw new Error(`Failed to verify backend dependencies: ${check.stderr || check.stdout}`);
        }

        const missingModules = this.parseMissingModules(check.stdout);
        if (missingModules.length === 0) {
            return;
        }

        const packagesToInstall = requiredModules
            .filter((item) => missingModules.includes(item.module))
            .map((item) => item.packageName);
        if (packagesToInstall.length === 0) {
            return;
        }

        this.outputChannel.appendLine(
            `[MemoPilot] Installing backend dependencies: ${packagesToInstall.join(', ')}`
        );
        await this.ensurePipAvailable(pythonPath);
        const install = await this.runPythonCommand(
            pythonPath,
            ['-m', 'pip', 'install', '--disable-pip-version-check', ...packagesToInstall],
        );
        if (install.exitCode !== 0) {
            throw new Error(
                `Failed to install backend dependencies: ${install.stderr || install.stdout}`
            );
        }
    }

    private async ensurePipAvailable(pythonPath: string): Promise<void> {
        const pipVersion = await this.runPythonCommand(pythonPath, ['-m', 'pip', '--version']);
        if (pipVersion.exitCode === 0) {
            return;
        }
        if (!this.hasNoModuleNamedPip(pipVersion.stderr + pipVersion.stdout)) {
            throw new Error(`Failed to check pip: ${pipVersion.stderr || pipVersion.stdout}`);
        }

        this.outputChannel.appendLine('[MemoPilot] pip not found; bootstrapping with ensurepip.');
        const ensure = await this.runPythonCommand(pythonPath, ['-m', 'ensurepip', '--upgrade']);
        if (ensure.exitCode !== 0) {
            throw new Error(
                `Failed to bootstrap pip with ensurepip: ${ensure.stderr || ensure.stdout}`
            );
        }
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

    private parseMissingModules(stdout: string): string[] {
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
            // Fall through and treat as no missing modules.
        }
        return [];
    }

    private hasNoModuleNamedPip(output: string): boolean {
        return /No module named pip/i.test(output);
    }

    private runPythonCommand(
        pythonPath: string,
        args: string[],
    ): Promise<{ exitCode: number; stdout: string; stderr: string }> {
        return new Promise((resolve, reject) => {
            const child = spawn(pythonPath, args, {
                cwd: this.workspacePath,
                env: process.env,
                stdio: ['ignore', 'pipe', 'pipe'],
            });
            let stdout = '';
            let stderr = '';
            child.stdout?.on('data', (data: Buffer) => {
                stdout += data.toString();
            });
            child.stderr?.on('data', (data: Buffer) => {
                stderr += data.toString();
            });
            child.on('error', reject);
            child.on('close', (code) => {
                resolve({ exitCode: code ?? 1, stdout, stderr });
            });
        });
    }

    private async waitForPort(timeoutMs: number): Promise<number> {
        const start = Date.now();
        const pollInterval = 200;

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

            const portFromLogs = this.extractPortFromLogs();
            if (portFromLogs && await this.isBackendHealthy(portFromLogs)) {
                return portFromLogs;
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

    private extractPortFromLogs(): number | undefined {
        const combined = `${this.stdoutBuffer}\n${this.stderrBuffer}`;
        const listeningMatch = combined.match(/Backend listening on 127\.0\.0\.1:(\d+)/i);
        if (listeningMatch) {
            return Number(listeningMatch[1]);
        }
        const startedMatch = combined.match(/MemoPilot backend started on port (\d+)/i);
        if (startedMatch) {
            return Number(startedMatch[1]);
        }
        return undefined;
    }

    private async isBackendHealthy(port: number): Promise<boolean> {
        try {
            const response = await fetch(`http://127.0.0.1:${port}/v1/health`, {
                method: 'GET',
                headers: {
                    'X-Agent-Token': this.token,
                },
            });
            return response.ok;
        } catch {
            return false;
        }
    }

    private sleep(ms: number): Promise<void> {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}
