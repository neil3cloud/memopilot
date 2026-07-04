import * as vscode from 'vscode';
import * as crypto from 'crypto';
import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs';
import { ChildProcess, spawn } from 'child_process';

const EXTENSION_VERSION = '1.0.1-build-20260630';

interface BackendLockfile {
    port: number;
    pid: number;
    started_at?: string;
    schema_version?: number;
    api_version?: number;
}

export class BackendManager {
    private process: ChildProcess | undefined;
    private token: string;
    private port: number | undefined;
    private workspacePath: string;
    private outputChannel: vscode.OutputChannel;
    private lockFilePath: string;
    private stderrBuffer: string = '';
    private stdoutBuffer: string = '';
    private _stopping: boolean = false;
    private onUnexpectedExit?: () => void;

    constructor(
        workspacePath: string,
        outputChannel: vscode.OutputChannel,
        onUnexpectedExit?: () => void
    ) {
        this.workspacePath = workspacePath;
        this.outputChannel = outputChannel;
        this.token = crypto.randomBytes(32).toString('hex');
        this.lockFilePath = path.join(workspacePath, '.memopilot', 'agent.lock');
        this.onUnexpectedExit = onUnexpectedExit;
    }

    get baseUrl(): string {
        if (!this.port) {
            throw new Error('Backend not started');
        }
        // Security invariant: HMAC token is transmitted over plaintext HTTP, which is
        // acceptable ONLY because the backend binds exclusively to 127.0.0.1.
        // If binding changes to 0.0.0.0 or a remote host, HTTPS MUST be enforced.
        return `http://127.0.0.1:${this.port}`;
    }

    get authToken(): string {
        return this.token;
    }

    async start(context?: vscode.ExtensionContext): Promise<void> {
        this._stopping = false;
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

        this.outputChannel.appendLine(`[MemoPilot v${EXTENSION_VERSION}] Starting backend: ${pythonPath} -m agent.main`);
        this.outputChannel.appendLine(`[MemoPilot] Agent dir: ${agentDir}`);
        this.outputChannel.appendLine(`[MemoPilot] Agent parent (cwd): ${agentParent}`);
        this.outputChannel.appendLine(`[MemoPilot] Workspace: ${this.workspacePath}`);

        await this.ensureBackendDependencies(pythonPath, agentDir);

        this.stderrBuffer = '';
        this.stdoutBuffer = '';

        this.process = spawn(pythonPath, ['-m', 'agent.main'], {
            cwd: agentParent,
            env: {
                ...process.env,
                MEMOPILOT_TOKEN: this.token,
                MEMOPILOT_WORKSPACE: this.workspacePath,
                PYTHONPATH: agentParent,
                ...(context
                    ? {
                        OPENAI_API_KEY: await context.secrets.get('memopilot.openaiApiKey') ?? '',
                        ANTHROPIC_API_KEY: await context.secrets.get('memopilot.anthropicApiKey') ?? '',
                        MEMOPILOT_OLLAMA_URL: vscode.workspace.getConfiguration('memopilot').get<string>('ollamaUrl', 'http://localhost:11434'),
                    }
                    : {}),
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

            // Detect unexpected exit: if we didn't call stop(), the backend crashed
            if (!this._stopping && this.onUnexpectedExit) {
                this.outputChannel.appendLine(`[MemoPilot] Backend exited unexpectedly (code ${code})`);
                // Schedule callback after brief delay to allow any final logs to appear
                setTimeout(() => this.onUnexpectedExit?.(), 2000);
            }
        });

        // Wait for lockfile to appear with port
        this.port = await this.waitForPort(60000);
        this.outputChannel.appendLine(`[MemoPilot v${EXTENSION_VERSION}] Backend started on port ${this.port}`);

        // Write env files for MCP server (Cursor + CLI integration)
        this.writeCursorMcpEnv(memopilotDir);
        this.writeMcpEnv(memopilotDir);

        // Auto-register as global MCP server for Claude Code & Gemini CLI
        try {
            this.registerGlobalMcpServers(pythonPath, agentParent);
        } catch {
            // Non-critical: CLI tools can still be configured manually
        }
    }

    async stop(): Promise<void> {
        this._stopping = true;
        if (this.process) {
            const pid = this.process.pid;
            try {
                // On Windows, SIGTERM is ignored. Use taskkill /T to kill the
                // entire process tree (backend + uvicorn worker).
                if (process.platform === 'win32' && pid) {
                    spawn('taskkill', ['/pid', String(pid), '/T', '/F'], {
                        stdio: 'ignore',
                    });
                } else {
                    this.process.kill('SIGTERM');
                }
            } catch {
                // Process may have already exited
            }

            // Wait briefly for the process to actually exit
            await new Promise<void>((resolve) => {
                const timeout = setTimeout(() => resolve(), 3000);
                this.process?.on('exit', () => {
                    clearTimeout(timeout);
                    resolve();
                });
            });

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

        // Clean stale SQLite journal files that can cause "database is locked"
        this.cleanStaleDatabaseJournals();

        // Clean .cursor-mcp-env
        this.deleteCursorMcpEnv();
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

    private async ensureBackendDependencies(pythonPath: string, agentDir: string): Promise<void> {
        // All modules the agent imports at startup — single source of truth is requirements.txt.
        // This list is only for the "anything missing?" probe; the actual install uses the file.
        const requiredModules = [
            'fastapi', 'uvicorn', 'pydantic', 'aiosqlite', 'httpx',
            'detect_secrets', 'PIL', 'openpyxl', 'pdfplumber', 'yaml',
            'docx', 'pptx', 'pytesseract', 'sqlite_vec', 'jedi',
            'tree_sitter', 'tree_sitter_typescript', 'tree_sitter_c_sharp',
        ];

        const moduleScript = [
            'import importlib.util, json',
            `required = ${JSON.stringify(requiredModules)}`,
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

        this.outputChannel.appendLine(`[MemoPilot] Missing modules: ${missingModules.join(', ')}`);

        // requirements.txt is bundled at the parent of the agent dir.
        const requirementsPath = path.resolve(agentDir, '..', 'requirements.txt');
        if (!fs.existsSync(requirementsPath)) {
            throw new Error(
                `requirements.txt not found at ${requirementsPath}. ` +
                `Please reinstall the MemoPilot extension.`
            );
        }

        this.outputChannel.appendLine(`[MemoPilot] Installing backend dependencies (this runs once)...`);
        await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: 'MemoPilot: Installing backend dependencies (first-time setup)...' },
            async () => {
                await this.ensurePipAvailable(pythonPath);
                const install = await this.runPythonCommand(pythonPath, [
                    '-m', 'pip', 'install',
                    '--disable-pip-version-check', '--quiet',
                    '-r', requirementsPath,
                ]);
                if (install.exitCode !== 0) {
                    throw new Error(
                        `Failed to install backend dependencies:\n${install.stderr || install.stdout}`
                    );
                }
            }
        );
        this.outputChannel.appendLine('[MemoPilot] Backend dependencies installed successfully.');
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
            const lockfile = this.readLockfile();
            if (lockfile) {
                return lockfile.port;
            }

            const portFromLogs = this.extractPortFromLogs();
            if (portFromLogs && await this.isBackendHealthy(portFromLogs)) {
                return portFromLogs;
            }
            await this.sleep(pollInterval);
        }

        // Timeout — kill process tree
        if (this.process?.pid && process.platform === 'win32') {
            spawn('taskkill', ['/pid', String(this.process.pid), '/T', '/F'], {
                stdio: 'ignore',
            });
        } else {
            this.process?.kill('SIGTERM');
        }
        const hint = this.stderrBuffer
            ? `\nLast stderr:\n${this.stderrBuffer.slice(-500)}`
            : '';
        throw new Error(`Backend failed to start within 60 seconds (no lockfile)${hint}`);
    }

    private readLockfile(): BackendLockfile | undefined {
        if (!fs.existsSync(this.lockFilePath)) {
            return undefined;
        }
        try {
            const content = fs.readFileSync(this.lockFilePath, 'utf8').trim();
            const data: unknown = JSON.parse(content);
            if (!data || typeof data !== 'object') {
                return undefined;
            }

            const lockfile = data as Partial<BackendLockfile>;
            const { port, pid, started_at, schema_version, api_version } = lockfile;
            if (!Number.isInteger(port) || !Number.isInteger(pid)) {
                return undefined;
            }
            if (started_at !== undefined && typeof started_at !== 'string') {
                return undefined;
            }
            if (
                schema_version !== undefined &&
                !Number.isInteger(schema_version)
            ) {
                return undefined;
            }
            if (api_version !== undefined && !Number.isInteger(api_version)) {
                return undefined;
            }

            return {
                port: port as number,
                pid: pid as number,
                started_at,
                schema_version,
                api_version,
            };
        } catch {
            return undefined;
        }
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
        const uvicornMatch = combined.match(/Uvicorn running on http:\/\/127\.0\.0\.1:(\d+)/i);
        if (uvicornMatch) {
            return Number(uvicornMatch[1]);
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

    private writeCursorMcpEnv(memopilotDir: string): void {
        try {
            const envPath = path.join(memopilotDir, '.cursor-mcp-env');
            const content = [
                `MEMOPILOT_TOKEN=${this.token}`,
                `MEMOPILOT_PORT=${this.port ?? ''}`,
                `MEMOPILOT_WORKSPACE=${this.workspacePath}`,
                '',
            ].join('\n');
            fs.writeFileSync(envPath, content, { mode: 0o600 });

            // Auto-add to .gitignore if not already present
            this.ensureGitignoreEntry(memopilotDir, '.cursor-mcp-env');
        } catch {
            // Non-critical: Cursor integration will just not work
        }
    }

    private deleteCursorMcpEnv(): void {
        const memopilotDir = path.join(this.workspacePath, '.memopilot');
        for (const name of ['.cursor-mcp-env', '.mcp-env']) {
            try {
                const envPath = path.join(memopilotDir, name);
                if (fs.existsSync(envPath)) {
                    fs.unlinkSync(envPath);
                }
            } catch {
                // Ignore cleanup errors
            }
        }
    }

    private writeMcpEnv(memopilotDir: string): void {
        try {
            const envPath = path.join(memopilotDir, '.mcp-env');
            const content = [
                `MEMOPILOT_TOKEN=${this.token}`,
                `MEMOPILOT_PORT=${this.port ?? ''}`,
                `MEMOPILOT_WORKSPACE=${this.workspacePath}`,
                '',
            ].join('\n');
            fs.writeFileSync(envPath, content, { mode: 0o600 });
            this.ensureGitignoreEntry(memopilotDir, '.mcp-env');
        } catch {
            // Non-critical
        }
    }

    private cleanStaleDatabaseJournals(): void {
        const dbDir = path.join(this.workspacePath, '.memopilot', 'memory');
        for (const ext of ['-shm', '-wal', '-journal']) {
            const journalPath = path.join(dbDir, `memopilot.db${ext}`);
            try {
                if (fs.existsSync(journalPath)) {
                    fs.unlinkSync(journalPath);
                }
            } catch {
                // May still be held briefly; next start will retry
            }
        }
    }

    private ensureGitignoreEntry(memopilotDir: string, entry: string): void {
        const gitignorePath = path.join(memopilotDir, '.gitignore');
        try {
            let content = '';
            if (fs.existsSync(gitignorePath)) {
                content = fs.readFileSync(gitignorePath, 'utf-8');
            }
            if (!content.includes(entry)) {
                const newLine = content.endsWith('\n') || content === '' ? '' : '\n';
                fs.writeFileSync(gitignorePath, `${content}${newLine}${entry}\n`);
            }
        } catch {
            // Non-critical
        }
    }

    // ------------------------------------------------------------------
    // Auto-register MCP server for all supported clients
    // ------------------------------------------------------------------

    private registerGlobalMcpServers(pythonPath: string, agentCwd: string): void {
        // The pythonPath from resolvePython() may be the workspace's Python (which
        // lacks agent dependencies). For MCP config we need the Python that can
        // actually run the agent — prefer the venv in the agent's parent tree.
        const mcpPython = this.resolveMcpPython(pythonPath, agentCwd);
        const entry = {
            command: mcpPython,
            args: ['-m', 'agent.mcp_server'],
            cwd: agentCwd,
            env: { PYTHONPATH: agentCwd },
        };

        const entryWithWorkspace = {
            ...entry,
            env: { ...entry.env, MEMOPILOT_WORKSPACE: this.workspacePath },
        };

        // Project-level: Claude Code extension, Cursor
        this.writeMcpJsonFile(
            path.join(this.workspacePath, '.mcp.json'),
            entryWithWorkspace,
            'project .mcp.json (Claude Code extension)',
        );
        this.writeMcpJsonFile(
            path.join(this.workspacePath, '.cursor', 'mcp.json'),
            entryWithWorkspace,
            '.cursor/mcp.json (Cursor)',
        );

        // User-level: Claude Code CLI, Gemini CLI
        this.writeMcpJsonFile(
            path.join(os.homedir(), '.claude', '.mcp.json'),
            entry,
            '~/.claude/.mcp.json (Claude Code CLI)',
        );
        this.writeMcpJsonFile(
            path.join(os.homedir(), '.gemini', 'settings.json'),
            entry,
            '~/.gemini/settings.json (Gemini CLI)',
        );
    }

    private resolveMcpPython(fallback: string, agentCwd: string): string {
        const isWindows = process.platform === 'win32';
        const bin = isWindows ? 'Scripts' : 'bin';
        const exe = isWindows ? 'python.exe' : 'python';

        // Walk up from agentCwd to find a .venv with the right dependencies
        let dir = agentCwd;
        for (let i = 0; i < 5; i++) {
            const candidate = path.join(dir, '.venv', bin, exe);
            if (fs.existsSync(candidate)) {
                return candidate;
            }
            const parent = path.dirname(dir);
            if (parent === dir) break;
            dir = parent;
        }

        return fallback;
    }

    private writeMcpJsonFile(
        filePath: string,
        entry: Record<string, unknown>,
        label: string,
    ): void {
        try {
            const dir = path.dirname(filePath);
            if (!fs.existsSync(dir)) {
                fs.mkdirSync(dir, { recursive: true });
            }

            let config: Record<string, unknown> = {};
            if (fs.existsSync(filePath)) {
                config = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
            }
            if (!config.mcpServers || typeof config.mcpServers !== 'object') {
                config.mcpServers = {};
            }
            (config.mcpServers as Record<string, unknown>).memopilot = entry;
            fs.writeFileSync(filePath, JSON.stringify(config, null, 2) + '\n', 'utf-8');
            this.outputChannel.appendLine(`[MemoPilot] Registered MCP server in ${label}`);
        } catch (err) {
            this.outputChannel.appendLine(`[MemoPilot] Failed to write ${label}: ${err}`);
        }
    }
}
