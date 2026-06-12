import * as vscode from 'vscode';
import { BackendClient } from './BackendClient';
import { BackendManager } from './BackendManager';
import { StatusTreeProvider } from './views/StatusTreeProvider';
import { PlaceholderTreeProvider } from './views/PlaceholderTreeProvider';
import { WorkspaceProfileTreeProvider } from './views/WorkspaceProfileTreeProvider';
import { MemoryManagerTreeProvider, MEMORY_FILTERS, MemoryFilter } from './views/MemoryManagerTreeProvider';
import { PrivacyDashboardTreeProvider } from './views/PrivacyDashboardTreeProvider';
import { EvidenceBoardTreeProvider } from './views/EvidenceBoardTreeProvider';
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
    const profileProvider = new WorkspaceProfileTreeProvider();
    const memoryProvider = new MemoryManagerTreeProvider();
    const rulesProvider = new PlaceholderTreeProvider('Rules & Skills will appear here after indexing.');
    const contextProvider = new PlaceholderTreeProvider('Context Pack preview will appear here.');
    const costProvider = new PlaceholderTreeProvider('Cost Guard data will appear here.');
    const privacyProvider = new PrivacyDashboardTreeProvider();
    const evidenceProvider = new EvidenceBoardTreeProvider();

    context.subscriptions.push(
        vscode.window.registerTreeDataProvider('memopilot-status', statusProvider),
        vscode.window.registerTreeDataProvider('memopilot-profile', profileProvider),
        vscode.window.registerTreeDataProvider('memopilot-memory', memoryProvider),
        vscode.window.registerTreeDataProvider('memopilot-rules', rulesProvider),
        vscode.window.registerTreeDataProvider('memopilot-context', contextProvider),
        vscode.window.registerTreeDataProvider('memopilot-cost', costProvider),
        vscode.window.registerTreeDataProvider('memopilot-privacy', privacyProvider),
        vscode.window.registerTreeDataProvider('memopilot-evidence', evidenceProvider),
    );

    // Commands
    const notImplemented = (name: string) => () => {
        vscode.window.showInformationMessage(`MemoPilot: "${name}" is not yet implemented.`);
    };

    const rebuildMemory = async () => {
        if (!backendClient) {
            vscode.window.showWarningMessage('MemoPilot backend is not connected.');
            return;
        }

        try {
            const result = await backendClient.rebuildMemory();
            vscode.window.showInformationMessage(
                `MemoPilot memory rebuilt: ${result.indexed_files} files, ${result.symbols_extracted} symbols.`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            vscode.window.showErrorMessage(`MemoPilot memory rebuild failed: ${msg}`);
        }
    };

    const ensureBackendClient = (): BackendClient | undefined => {
        if (!backendClient) {
            void vscode.window.showWarningMessage('MemoPilot backend is not connected.');
            return undefined;
        }
        return backendClient;
    };

    const refreshGovernanceViews = async (): Promise<void> => {
        profileProvider.setClient(backendClient);
        memoryProvider.setClient(backendClient);
        privacyProvider.setClient(backendClient);
        evidenceProvider.setClient(backendClient);
        await Promise.all([
            profileProvider.refresh(),
            memoryProvider.refresh(),
            privacyProvider.refresh(),
            evidenceProvider.refresh(),
        ]);
    };

    const openWorkspaceProfile = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const profile = await client.getWorkspaceProfile();
            const document = await vscode.workspace.openTextDocument({
                language: 'yaml',
                content: profile.profile_yaml,
            });
            await vscode.window.showTextDocument(document, { preview: false });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot failed to open workspace profile: ${msg}`);
        }
    };

    const rebuildWorkspaceProfile = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            await client.rebuildWorkspaceProfile();
            await profileProvider.refresh();
            void vscode.window.showInformationMessage('MemoPilot workspace profile rebuilt.');
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot failed to rebuild workspace profile: ${msg}`);
        }
    };

    const validateWorkspaceProfile = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const validation = await client.validateWorkspaceProfile();
            if (validation.valid) {
                void vscode.window.showInformationMessage('MemoPilot workspace profile is valid.');
                return;
            }
            void vscode.window.showWarningMessage(
                `MemoPilot workspace profile issues: ${validation.issues.join(', ')}`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot profile validation failed: ${msg}`);
        }
    };

    const exportWorkspaceProfile = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const exported = await client.exportWorkspaceProfile();
            void vscode.window.showInformationMessage(
                `MemoPilot workspace profile exported to ${exported.exported_path}`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot profile export failed: ${msg}`);
        }
    };

    const reviewMemory = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const selectedFilter = await vscode.window.showQuickPick(
                MEMORY_FILTERS.map((filter) => ({
                    label: filter,
                    description: filter === memoryProvider.getFilter() ? 'current filter' : undefined,
                })),
                { title: 'Select Memory Manager filter' },
            );
            if (!selectedFilter) { return; }

            const filter = selectedFilter.label as MemoryFilter;
            memoryProvider.setFilter(filter);
            await memoryProvider.refresh();

            const refreshedItems = memoryProvider.getCurrentItems();
            if (refreshedItems.length === 0) {
                void vscode.window.showInformationMessage(`No memory items for filter "${filter}".`);
                return;
            }

            const selectedItem = await vscode.window.showQuickPick(
                refreshedItems.map((item) => ({
                    label: item.title,
                    description: `${item.type} • trust ${item.trust_level}`,
                    detail: item.id,
                    item,
                })),
                { title: `Memory items (${filter})` },
            );
            if (!selectedItem) { return; }

            const action = await vscode.window.showQuickPick(
                ['Approve', 'Reject', 'Edit', 'Delete', 'Rebuild'],
                { title: `Action for "${selectedItem.item.title}"` },
            );
            if (!action) { return; }

            if (action === 'Approve') {
                await client.approveMemoryItem(selectedItem.item.id);
            } else if (action === 'Reject') {
                await client.rejectMemoryItem(selectedItem.item.id);
            } else if (action === 'Delete') {
                await client.deleteMemoryItem(selectedItem.item.id);
            } else if (action === 'Rebuild') {
                await client.rebuildMemoryItem(selectedItem.item.id);
            } else if (action === 'Edit') {
                const newTitle = await vscode.window.showInputBox({
                    title: 'Edit memory title',
                    value: selectedItem.item.title,
                });
                if (!newTitle) { return; }
                const newBody = await vscode.window.showInputBox({
                    title: 'Edit memory body',
                    value: selectedItem.item.body,
                });
                if (!newBody) { return; }
                await client.editMemoryItem(selectedItem.item.id, newTitle, newBody);
            }

            await memoryProvider.refresh();
            void vscode.window.showInformationMessage(`Memory action "${action}" completed.`);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot memory review failed: ${msg}`);
        }
    };

    const showPrivacyDashboard = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const dashboard = await client.getPrivacyDashboard();
            await privacyProvider.refresh();
            void vscode.window.showInformationMessage(
                `Privacy summary: ${dashboard.pre_call_approval_summary}; MCP status ${dashboard.mcp_data_status}`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot privacy dashboard failed: ${msg}`);
        }
    };

    const attachEvidence = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        const picks = await vscode.window.showOpenDialog({
            canSelectMany: true,
            openLabel: 'Attach Evidence',
            canSelectFiles: true,
            canSelectFolders: false,
        });
        if (!picks || picks.length === 0) { return; }
        try {
            for (const pick of picks) {
                await client.attachEvidence(pick.fsPath);
            }
            await evidenceProvider.refresh();
            void vscode.window.showInformationMessage(`Attached ${picks.length} evidence file(s).`);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot failed to attach evidence: ${msg}`);
        }
    };

    const runInvestigation = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        const title = await vscode.window.showInputBox({ title: 'Investigation title' });
        if (!title) { return; }
        const description = await vscode.window.showInputBox({ title: 'Work item description' }) ?? '';
        const criteriaInput = await vscode.window.showInputBox({
            title: 'Acceptance criteria (separate with ;)',
        });
        const acceptanceCriteria = (criteriaInput ?? '')
            .split(';')
            .map((item) => item.trim())
            .filter((item) => item.length > 0);
        try {
            const result = await client.runInvestigation(title, description, acceptanceCriteria);
            await evidenceProvider.refresh();
            const document = await vscode.workspace.openTextDocument({
                language: 'markdown',
                content: result.context_pack,
            });
            await vscode.window.showTextDocument(document, { preview: false });
            void vscode.window.showInformationMessage(
                `Investigation pack generated (${result.evidence_count} evidence items).`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot investigation failed: ${msg}`);
        }
    };

    context.subscriptions.push(
        vscode.commands.registerCommand('memopilot.indexWorkspace', notImplemented('Index Workspace Memory')),
        vscode.commands.registerCommand('memopilot.analyzeTask', notImplemented('Analyze Current Task')),
        vscode.commands.registerCommand('memopilot.generateContextPack', notImplemented('Generate Context Pack')),
        vscode.commands.registerCommand('memopilot.showCostReport', notImplemented('Show Cost Report')),
        vscode.commands.registerCommand('memopilot.openRules', notImplemented('Open Rules')),
        vscode.commands.registerCommand('memopilot.rebuildMemory', rebuildMemory),
        vscode.commands.registerCommand('memopilot.openWorkspaceProfile', openWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.rebuildWorkspaceProfile', rebuildWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.validateWorkspaceProfile', validateWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.exportWorkspaceProfile', exportWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.reviewMemory', reviewMemory),
        vscode.commands.registerCommand('memopilot.showPrivacyDashboard', showPrivacyDashboard),
        vscode.commands.registerCommand('memopilot.attachEvidence', attachEvidence),
        vscode.commands.registerCommand('memopilot.runInvestigation', runInvestigation),
        vscode.commands.registerCommand('memopilot.showPanel', () => {
            MemoPilotPanel.createOrShow(context.extensionUri, backendClient);
        }),
        vscode.commands.registerCommand('memopilot.restartBackend', async () => {
            await stopBackend();
            await startBackend(
                context,
                outputChannel,
                statusProvider,
                refreshGovernanceViews,
            );
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

    await startBackend(
        context,
        outputChannel,
        statusProvider,
        refreshGovernanceViews,
    );
}

async function startBackend(
    context: vscode.ExtensionContext,
    outputChannel: vscode.OutputChannel,
    statusProvider: StatusTreeProvider,
    onConnectedRefresh: () => Promise<void>,
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
            await onConnectedRefresh();
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
