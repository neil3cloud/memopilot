import * as vscode from 'vscode';
import { BackendManager } from './BackendManager';
import { BackendClient } from './BackendClient';
import { StatusTreeProvider } from './views/StatusTreeProvider';
import { PlaceholderTreeProvider } from './views/PlaceholderTreeProvider';
import { MemoPilotPanel } from './panels/MemoPilotPanel';

let backendManager: BackendManager | undefined;
let backendClient: BackendClient | undefined;
let statusBarItem: vscode.StatusBarItem;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    const outputChannel = vscode.window.createOutputChannel('MemoPilot');
    context.subscriptions.push(outputChannel);

    // Status bar
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'memopilot.showPanel';
    statusBarItem.text = '$(sync~spin) MemoPilot';
    statusBarItem.tooltip = 'MemoPilot — Starting backend...';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Tree views
    const statusProvider = new StatusTreeProvider();
    const rulesProvider = new PlaceholderTreeProvider('Rules & Skills will appear here after indexing.');
    const contextProvider = new PlaceholderTreeProvider('Context Pack preview will appear here.');
    const costProvider = new PlaceholderTreeProvider('Cost Guard data will appear here.');

    context.subscriptions.push(
        vscode.window.registerTreeDataProvider('memopilot-status', statusProvider),
        vscode.window.registerTreeDataProvider('memopilot-rules', rulesProvider),
        vscode.window.registerTreeDataProvider('memopilot-context', contextProvider),
        vscode.window.registerTreeDataProvider('memopilot-cost', costProvider),
    );

    // Commands
    const notImplemented = (name: string) => () => {
        vscode.window.showInformationMessage(`MemoPilot: "${name}" is not yet implemented.`);
    };

    context.subscriptions.push(
        vscode.commands.registerCommand('memopilot.indexWorkspace', notImplemented('Index Workspace Memory')),
        vscode.commands.registerCommand('memopilot.analyzeTask', notImplemented('Analyze Current Task')),
        vscode.commands.registerCommand('memopilot.generateContextPack', notImplemented('Generate Context Pack')),
        vscode.commands.registerCommand('memopilot.showCostReport', notImplemented('Show Cost Report')),
        vscode.commands.registerCommand('memopilot.openRules', notImplemented('Open Rules')),
        vscode.commands.registerCommand('memopilot.rebuildMemory', notImplemented('Rebuild Memory')),
        vscode.commands.registerCommand('memopilot.showPanel', () => {
            MemoPilotPanel.createOrShow(context.extensionUri, backendClient);
        }),
        vscode.commands.registerCommand('memopilot.restartBackend', async () => {
            await stopBackend();
            await startBackend(context, outputChannel, statusProvider);
        }),
    );

    // Start backend if workspace is open
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) {
        statusBarItem.text = '$(info) MemoPilot';
        statusBarItem.tooltip = 'MemoPilot — Open a folder to start';
        statusProvider.setStatus('no-workspace', 'Open a folder to use MemoPilot');
        return;
    }

    await startBackend(context, outputChannel, statusProvider);
}

async function startBackend(
    context: vscode.ExtensionContext,
    outputChannel: vscode.OutputChannel,
    statusProvider: StatusTreeProvider,
): Promise<void> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) { return; }

    try {
        backendManager = new BackendManager(workspaceFolder.uri.fsPath, outputChannel);
        await backendManager.start();

        backendClient = new BackendClient(backendManager);
        const health = await backendClient.health();

        if (health.status === 'ok') {
            statusBarItem.text = '$(check) MemoPilot';
            statusBarItem.tooltip = `MemoPilot — Connected (API v${health.api_version})`;
            statusProvider.setStatus('connected', `Backend connected — API v${health.api_version}`);

            // Initialize workspace .memopilot/ folder
            await backendClient.initWorkspace();
            outputChannel.appendLine('[MemoPilot] Workspace initialized.');
        }
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        statusBarItem.text = '$(error) MemoPilot';
        statusBarItem.tooltip = `MemoPilot — Backend unavailable: ${msg}`;
        statusProvider.setStatus('error', `Backend unavailable: ${msg}`);
        outputChannel.appendLine(`[MemoPilot] Backend start failed: ${msg}`);

        vscode.window.showErrorMessage(
            `MemoPilot backend failed to start: ${msg}`,
            'Restart Backend',
        ).then(action => {
            if (action === 'Restart Backend') {
                vscode.commands.executeCommand('memopilot.restartBackend');
            }
        });
    }
}

async function stopBackend(): Promise<void> {
    if (backendManager) {
        await backendManager.stop();
        backendManager = undefined;
        backendClient = undefined;
    }
}

export async function deactivate(): Promise<void> {
    await stopBackend();
}
