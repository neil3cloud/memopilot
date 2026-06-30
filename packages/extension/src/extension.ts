import * as vscode from 'vscode';
import { BackendClient, IndexStatusResponse } from './BackendClient';
import { BackendManager } from './BackendManager';
import { registerLanguageModelTools } from './tools/LanguageModelToolsRegistrar';
import { StatusTreeProvider } from './views/StatusTreeProvider';
import { PlaceholderTreeProvider } from './views/PlaceholderTreeProvider';
import { WorkspaceProfileTreeProvider } from './views/WorkspaceProfileTreeProvider';
import { MemoryManagerTreeProvider, MEMORY_FILTERS, MemoryFilter } from './views/MemoryManagerTreeProvider';
import { PrivacyDashboardTreeProvider } from './views/PrivacyDashboardTreeProvider';
import { RulesSkillsTreeProvider } from './views/RulesSkillsTreeProvider';
import { UsageStatsTreeProvider } from './views/UsageStatsTreeProvider';
import { ContextPackTreeProvider } from './views/ContextPackTreeProvider';
import { TaskHistoryTreeProvider } from './views/TaskHistoryTreeProvider';
import { McpToolsTreeProvider } from './views/McpToolsTreeProvider';
import { MemoPilotPanel } from './panels/MemoPilotPanel';
import { SynthesisHostClient } from './SynthesisHostClient';

let backendManager: BackendManager | undefined;
let backendClient: BackendClient | undefined;
let synthesisHostClient: SynthesisHostClient | undefined;
let statusBarItem: vscode.StatusBarItem;
let workspaceIndexingInFlight = false;
let extensionContext: vscode.ExtensionContext | undefined;

let pendingChangesBar: vscode.StatusBarItem | undefined;
let fileWatcher: vscode.FileSystemWatcher | undefined;
const pendingNew = new Set<string>();
const pendingModified = new Set<string>();
const pendingDeleted = new Set<string>();

function updatePendingChangesBar(): void {
    const total = pendingNew.size + pendingModified.size + pendingDeleted.size;
    if (total === 0) {
        pendingChangesBar?.hide();
        return;
    }
    if (!pendingChangesBar) { return; }
    const parts: string[] = [];
    if (pendingNew.size > 0) { parts.push(`${pendingNew.size} new`); }
    if (pendingModified.size > 0) { parts.push(`${pendingModified.size} modified`); }
    if (pendingDeleted.size > 0) { parts.push(`${pendingDeleted.size} deleted`); }
    pendingChangesBar.text = `$(sync) MemoPilot: ${parts.join(', ')} — click to update`;
    pendingChangesBar.tooltip = 'Click to reindex changed files in MemoPilot';
    pendingChangesBar.show();
}

function setupFileWatcher(context: vscode.ExtensionContext): void {
    fileWatcher?.dispose();
    const pattern = new vscode.RelativePattern(
        vscode.workspace.workspaceFolders![0],
        '**/*.{cs,ts,tsx,py,js,jsx}',
    );
    fileWatcher = vscode.workspace.createFileSystemWatcher(pattern);

    fileWatcher.onDidCreate((uri) => {
        pendingNew.add(uri.fsPath);
        pendingDeleted.delete(uri.fsPath);
        updatePendingChangesBar();
    });
    fileWatcher.onDidChange((uri) => {
        if (!pendingNew.has(uri.fsPath)) {
            pendingModified.add(uri.fsPath);
        }
        updatePendingChangesBar();
    });
    fileWatcher.onDidDelete((uri) => {
        pendingNew.delete(uri.fsPath);
        pendingModified.delete(uri.fsPath);
        pendingDeleted.add(uri.fsPath);
        updatePendingChangesBar();
    });

    context.subscriptions.push(fileWatcher);
}

async function refreshIndexStatus(
    client: BackendClient,
    statusProvider: StatusTreeProvider,
    outputChannel: vscode.OutputChannel,
    memoryProvider?: MemoryManagerTreeProvider,
    profileProvider?: WorkspaceProfileTreeProvider,
): Promise<IndexStatusResponse | undefined> {
    try {
        const indexStatus = await client.getIndexStatus();
        statusProvider.updateIndexStatus(indexStatus);
        if (indexStatus.languages && indexStatus.languages.length > 0) {
            memoryProvider?.setIndexedLanguages(indexStatus.languages);
            profileProvider?.setDetectedLanguages(indexStatus.languages);
        }
        return indexStatus;
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        outputChannel.appendLine(`[MemoPilot] Failed to fetch index status: ${msg}`);
        statusProvider.updateIndexStatus(undefined);
        return undefined;
    }
}
let healthCheckInterval: ReturnType<typeof setInterval> | undefined;
let healthCheckDisposable: vscode.Disposable | undefined;
let unexpectedExitRetryCount = 0;
const MAX_RESTART_RETRIES = 3;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    extensionContext = context;
    const outputChannel = vscode.window.createOutputChannel('MemoPilot');
    context.subscriptions.push(outputChannel);

    // Status bar
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'memopilot.analyzeTask';
    statusBarItem.text = '$(sync~spin) MemoPilot';
    statusBarItem.tooltip = 'MemoPilot — Starting backend...';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    pendingChangesBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
    pendingChangesBar.command = 'memopilot.indexPendingChanges';
    context.subscriptions.push(pendingChangesBar);

    // Tree views
    const statusProvider = new StatusTreeProvider();
    const profileProvider = new WorkspaceProfileTreeProvider();
    const memoryProvider = new MemoryManagerTreeProvider();
    const rulesProvider = new RulesSkillsTreeProvider();
    const contextProvider = new ContextPackTreeProvider();
    const costProvider = new UsageStatsTreeProvider();
    const privacyProvider = new PrivacyDashboardTreeProvider();
    const historyProvider = new TaskHistoryTreeProvider();
    const mcpProvider = new McpToolsTreeProvider();

    context.subscriptions.push(
        vscode.window.registerTreeDataProvider('memopilot-status', statusProvider),
        vscode.window.registerTreeDataProvider('memopilot-profile', profileProvider),
        vscode.window.registerTreeDataProvider('memopilot-memory', memoryProvider),
        vscode.window.registerTreeDataProvider('memopilot-rules', rulesProvider),
        vscode.window.registerTreeDataProvider('memopilot-context', contextProvider),
        vscode.window.registerTreeDataProvider('memopilot-cost', costProvider),
        vscode.window.registerTreeDataProvider('memopilot-privacy', privacyProvider),
        vscode.window.registerTreeDataProvider('memopilot-history', historyProvider),
        vscode.window.registerTreeDataProvider('memopilot-mcp', mcpProvider),
    );

    // Commands
    const indexWorkspace = async () => {
        if (!backendClient) {
            vscode.window.showWarningMessage('MemoPilot backend is not connected.');
            return;
        }
        if (workspaceIndexingInFlight) {
            vscode.window.showInformationMessage('MemoPilot workspace indexing is already in progress.');
            return;
        }

        try {
            workspaceIndexingInFlight = true;
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'MemoPilot: Indexing workspace...' },
                async () => {
                    const result = await backendClient!.indexWorkspace();
                    const parts: string[] = [];
                    parts.push(`${result.total_files_scanned} files scanned`);
                    if (result.indexed_files > 0) {
                        parts.push(`${result.indexed_files} new/changed`);
                    }
                    if (result.unchanged_files > 0) {
                        parts.push(`${result.unchanged_files} unchanged`);
                    }
                    parts.push(`${result.symbols_extracted} symbols`);
                    vscode.window.showInformationMessage(
                        `MemoPilot: ${parts.join(', ')} (${result.duration_ms}ms).`,
                    );
                    await refreshIndexStatus(backendClient!, statusProvider, outputChannel, memoryProvider, profileProvider);
                    await costProvider.refresh();
                },
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            vscode.window.showErrorMessage(`MemoPilot workspace indexing failed: ${msg}`);
        } finally {
            workspaceIndexingInFlight = false;
        }
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
            await refreshIndexStatus(backendClient, statusProvider, outputChannel, memoryProvider, profileProvider);
            await costProvider.refresh();
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            vscode.window.showErrorMessage(`MemoPilot memory rebuild failed: ${msg}`);
        }
    };

    const switchLLMMode = async () => {
        if (!backendClient) {
            void vscode.window.showWarningMessage('MemoPilot backend is not connected.');
            return;
        }
        try {
            const modeInfo = await backendClient.getLLMMode();
            const options: vscode.QuickPickItem[] = [];

            if (modeInfo.copilot_available) {
                options.push({
                    label: '$(copilot) Copilot',
                    description: modeInfo.model_id || 'vscode.lm non-frontier',
                    detail: modeInfo.mode === 'copilot' ? '✓ Active' : 'Use GitHub Copilot via vscode.lm API',
                });
            }
            if (modeInfo.cloud_available) {
                options.push({
                    label: '$(cloud) Cloud',
                    description: 'Configured cloud provider (Anthropic / OpenAI)',
                    detail: modeInfo.mode === 'cloud' ? '✓ Active' : 'Use cloud API keys from config',
                });
            }
            if (modeInfo.local_available) {
                options.push({
                    label: '$(server) Local',
                    description: 'LM Studio / Ollama',
                    detail: modeInfo.mode === 'local' ? '✓ Active' : 'Use local inference server',
                });
            }

            if (options.length === 0) {
                void vscode.window.showWarningMessage('No LLM providers available. Configure a provider first.');
                return;
            }

            const picked = await vscode.window.showQuickPick(options, {
                title: 'MemoPilot: Select LLM Mode',
                placeHolder: `Current: ${modeInfo.mode}`,
            });
            if (!picked) { return; }

            const modeMap: Record<string, string> = {
                '$(copilot) Copilot': 'copilot',
                '$(cloud) Cloud': 'cloud',
                '$(server) Local': 'local',
            };
            const newMode = modeMap[picked.label];
            if (!newMode || newMode === modeInfo.mode) { return; }

            await backendClient.setLLMMode(newMode);
            context.globalState.update('memopilot.llmMode', newMode);
            statusProvider.updateLLMMode(newMode, modeInfo.model_id, modeInfo.copilot_available);
            void vscode.window.showInformationMessage(`MemoPilot: LLM mode switched to ${newMode}.`);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot: Failed to switch LLM mode: ${msg}`);
        }
    };

    const reindexAndSummarize = async () => {
        if (!backendClient) {
            void vscode.window.showWarningMessage('MemoPilot backend is not connected.');
            return;
        }
        if (workspaceIndexingInFlight) {
            void vscode.window.showInformationMessage('MemoPilot indexing already in progress.');
            return;
        }

        const batchSize: number = vscode.workspace
            .getConfiguration('memopilot')
            .get('summarizationBatchSize', 25);

        try {
            workspaceIndexingInFlight = true;
            memoryProvider.setReindexing(true);
            await vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: `MemoPilot: Re-indexing and summarizing (${batchSize} symbols/request)...`,
                    cancellable: false,
                },
                async () => {
                    const result = await backendClient!.rebuildMemory(batchSize);
                    vscode.window.showInformationMessage(
                        `MemoPilot: ${result.symbols_extracted} symbols extracted. Summarization running in background.`,
                    );
                },
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot reindex failed: ${msg}`);
        } finally {
            workspaceIndexingInFlight = false;
            memoryProvider.setReindexing(false);
            await memoryProvider.refresh();
            await refreshIndexStatus(backendClient!, statusProvider, outputChannel, memoryProvider, profileProvider);
            await costProvider.refresh();
        }
    };

    const runSummarization = async () => {
        if (!backendClient) {
            void vscode.window.showWarningMessage('MemoPilot backend is not connected.');
            return;
        }
        if (workspaceIndexingInFlight) {
            void vscode.window.showInformationMessage('MemoPilot indexing already in progress.');
            return;
        }

        const batchSize: number = vscode.workspace
            .getConfiguration('memopilot')
            .get('summarizationBatchSize', 25);

        try {
            workspaceIndexingInFlight = true;
            await vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: `MemoPilot: Summarizing pending symbols (${batchSize} symbols/request)...`,
                    cancellable: false,
                },
                async () => {
                    await backendClient!.summarizePending(batchSize);
                    vscode.window.showInformationMessage(
                        'MemoPilot: Summarization running in background.',
                    );
                    await memoryProvider.refresh();
                    await costProvider.refresh();
                },
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot summarization failed: ${msg}`);
        } finally {
            workspaceIndexingInFlight = false;
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
        rulesProvider.setClient(backendClient);
        costProvider.setClient(backendClient);
        historyProvider.setClient(backendClient);
        mcpProvider.setClient(backendClient);

        // Load indexed languages from configuration
        const indexedLanguages = vscode.workspace.getConfiguration('memopilot').get<string[]>('indexedLanguages', ['python']);
        memoryProvider.setIndexedLanguages(indexedLanguages);

        // Update the main panel if it's open
        if (MemoPilotPanel.currentPanel) {
            MemoPilotPanel.currentPanel.setClient(backendClient);
        }
        await Promise.all([
            profileProvider.refresh(),
            memoryProvider.refresh(),
            privacyProvider.refresh(),
            rulesProvider.refresh(),
            costProvider.refresh(),
            historyProvider.refresh(),
            mcpProvider.refresh(),
        ]);

        await refreshProviderStatus();

        // Probe vscode.lm once at startup — backend caches the result for every synthesis call
        if (backendManager) {
            synthesisHostClient?.dispose();
            synthesisHostClient = new SynthesisHostClient(backendManager, (available, _modelId) => {
                if (available) {
                    void vscode.commands.executeCommand('setContext', 'memopilot.llmReady', true);
                }
                // Probe result changes the backend's llm_mode — refresh STATUS panel to reflect it
                void refreshProviderStatus();
            });
            void synthesisHostClient.probe();
        }
    };

    const refreshProviderStatus = async (): Promise<void> => {
        if (!backendClient) {
            statusProvider.updateProviderStatus([]);
            return;
        }

        try {
            const [capabilities, modeInfo] = await Promise.all([
                backendClient.listProviderCapabilities(),
                backendClient.getLLMMode().catch(() => null),
            ]);
            statusProvider.updateProviderStatus(capabilities.items ?? []);
            const hasProvider = (capabilities.items ?? []).some(c => c.healthy);
            if (hasProvider || modeInfo?.copilot_available) {
                void vscode.commands.executeCommand('setContext', 'memopilot.llmReady', true);
            }
            if (modeInfo) {
                statusProvider.updateLLMMode(modeInfo.mode, modeInfo.model_id, modeInfo.copilot_available);
                context.globalState.update('memopilot.llmMode', modeInfo.mode);
                context.globalState.update('memopilot.llmModeModelId', modeInfo.model_id);
                context.globalState.update('memopilot.copilotAvailable', modeInfo.copilot_available);
            }
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            outputChannel.appendLine(`[MemoPilot] Failed to load provider capabilities: ${msg}`);
            statusProvider.updateProviderStatus([]);
        }
    };

    const restartBackendNow = async (): Promise<void> => {
        await stopBackend();
        await startBackend(context, outputChannel, statusProvider, refreshGovernanceViews, memoryProvider, profileProvider);
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

    const openContextPreview = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }

        const taskDescription = await vscode.window.showInputBox({
            title: 'Search Project Context',
            prompt: 'Describe the code path, symbol, or behavior you want context for',
            placeHolder: 'Example: explain billing validation flow',
        });
        if (!taskDescription) { return; }

        try {
            const assembled = await client.assembleContext({
                task_description: taskDescription,
                workspace_root: getWorkspaceRoot(),
                caller: 'memopilot_ui',
                max_output_tokens: 8000,
            });
            const document = await vscode.workspace.openTextDocument({
                language: 'markdown',
                content: assembled.rendered_markdown,
            });
            await vscode.window.showTextDocument(document, { preview: false });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot context assembly failed: ${msg}`);
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

    const approveMemoryItem = async (treeItem: vscode.TreeItem & { memopilotItemId?: string }) => {
        const client = ensureBackendClient();
        if (!client) { return; }
        const id = treeItem?.memopilotItemId;
        if (!id) { return; }
        try {
            await client.approveMemoryItem(id);
            await memoryProvider.refresh();
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot approve failed: ${msg}`);
        }
    };

    const rejectMemoryItem = async (treeItem: vscode.TreeItem & { memopilotItemId?: string }) => {
        const client = ensureBackendClient();
        if (!client) { return; }
        const id = treeItem?.memopilotItemId;
        if (!id) { return; }
        try {
            await client.rejectMemoryItem(id);
            await memoryProvider.refresh();
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot reject failed: ${msg}`);
        }
    };

    const bulkApproveMemory = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        await memoryProvider.refresh();
        const pending = memoryProvider.getPendingItems();
        if (pending.length === 0) {
            void vscode.window.showInformationMessage('MemoPilot: No pending memory items to approve.');
            return;
        }
        const confirm = await vscode.window.showWarningMessage(
            `Approve all ${pending.length} pending memory item${pending.length === 1 ? '' : 's'}?`,
            { modal: true },
            'Approve All',
        );
        if (confirm !== 'Approve All') { return; }
        try {
            await Promise.all(pending.map((item) => client.approveMemoryItem(item.id)));
            await memoryProvider.refresh();
            void vscode.window.showInformationMessage(
                `MemoPilot: Approved ${pending.length} memory item${pending.length === 1 ? '' : 's'}.`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot bulk approve failed: ${msg}`);
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

    const manageContextTemplates = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const templates = await client.listContextTemplates();
            const picked = await vscode.window.showQuickPick(
                [
                    { label: '$(add) Create template', action: 'create' as const },
                    ...templates.templates.map((item) => ({
                        label: `${item.name} (${item.scope})`,
                        description: item.selected ? 'active' : undefined,
                        detail: item.template_id,
                        action: 'select' as const,
                        templateId: item.template_id,
                    })),
                ],
                { title: 'Context Pack Templates' },
            );
            if (!picked) { return; }

            if (picked.action === 'create') {
                const name = await vscode.window.showInputBox({ title: 'Template name' });
                if (!name) { return; }
                const content = await vscode.window.showInputBox({
                    title: 'Template content',
                    value: '# Template\n\n## Summary\n- ...',
                });
                if (!content) { return; }
                const newId = await client.saveContextTemplate(name, content, 'workspace');
                await client.selectContextTemplate(newId);
                void vscode.window.showInformationMessage(`Template "${name}" created and selected.`);
                return;
            }

            if (picked.templateId) {
                await client.selectContextTemplate(picked.templateId);
                void vscode.window.showInformationMessage(`Selected template: ${picked.label}`);
            }
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Template manager failed: ${msg}`);
        }
    };

    const configureProviders = async () => {
        if (!extensionContext) { return; }

        // These models are used only for MemoPilot's four LLM touch points:
        // symbol summarization, context synthesis, memory writeback, and profile inference.
        const touchPoint = await vscode.window.showQuickPick(
            [
                {
                    label: '$(cloud) Cloud small model',
                    description: 'Anthropic claude-haiku-4-5 or OpenAI gpt-4o-mini',
                    detail: 'Used for: symbol summarization, context synthesis, memory writeback, profile inference',
                    value: 'cloud' as const,
                },
                {
                    label: '$(server) Local model (OpenAI-compatible)',
                    description: 'Free, runs on your machine — Ollama, LM Studio, vLLM, OpenVINO, llama.cpp, etc.',
                    detail: 'Used for: symbol summarization, context synthesis, memory writeback',
                    value: 'local' as const,
                },
            ],
            { title: 'Configure MemoPilot LLM Touch Points' },
        );
        if (!touchPoint) { return; }

        try {
            if (touchPoint.value === 'local') {
                const localUrl = await vscode.window.showInputBox({
                    title: 'Local AI server URL',
                    value: 'http://localhost:1234',
                    prompt: 'Base URL of your local OpenAI-compatible server (Ollama, LM Studio, vLLM, etc.)',
                    placeHolder: 'http://localhost:1234',
                });
                if (!localUrl) { return; }

                // Discover models via OpenAI-compatible /v1/models endpoint
                let modelItems: vscode.QuickPickItem[] = [];
                try {
                    const http = await import('http');
                    const discovered = await new Promise<string[]>((resolve, reject) => {
                        const url = new URL('/v1/models', localUrl);
                        const port = url.port || (url.protocol === 'https:' ? '443' : '80');
                        const req = http.get({ hostname: url.hostname, port, path: url.pathname, timeout: 4000 }, (res) => {
                            let body = '';
                            res.on('data', (chunk: Buffer) => { body += chunk.toString(); });
                            res.on('end', () => {
                                try {
                                    const json = JSON.parse(body) as { data?: { id: string }[] };
                                    resolve((json.data ?? []).map(m => m.id));
                                } catch { resolve([]); }
                            });
                        });
                        req.on('error', reject);
                        req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
                    });
                    modelItems = discovered.map(id => ({ label: id, description: '' }));
                } catch {
                    // Server not reachable — fall through to manual entry
                }

                let localModel: string | undefined;
                if (modelItems.length > 0) {
                    const picked = await vscode.window.showQuickPick(
                        [...modelItems, { label: '$(edit) Enter model name manually', description: '' }],
                        { title: 'Select local model', placeHolder: 'Choose a model from your local server' },
                    );
                    if (!picked) { return; }
                    if (picked.label.startsWith('$(edit)')) {
                        localModel = await vscode.window.showInputBox({ title: 'Model name', placeHolder: 'e.g. qwen2.5-coder-7b-instruct' });
                    } else {
                        localModel = picked.label;
                    }
                } else {
                    localModel = await vscode.window.showInputBox({
                        title: 'Model name',
                        prompt: 'Could not reach server — enter model name manually',
                        placeHolder: 'e.g. qwen2.5-coder-7b-instruct',
                    });
                }
                if (!localModel) { return; }

                // Write provider + model + url into .memopilot/config.yaml
                const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
                if (!workspaceFolder) {
                    void vscode.window.showErrorMessage('No workspace folder open.');
                    return;
                }
                const configDir = vscode.Uri.joinPath(workspaceFolder.uri, '.memopilot');
                const configFile = vscode.Uri.joinPath(configDir, 'config.yaml');

                let existing = '';
                try {
                    existing = Buffer.from(await vscode.workspace.fs.readFile(configFile)).toString('utf8');
                } catch { /* file doesn't exist yet */ }

                const update = (yaml: string, key: string, value: string): string => {
                    const re = new RegExp(`^(\\s*#\\s*)?${key}:.*$`, 'm');
                    const line = `${key}: ${value}`;
                    return re.test(yaml) ? yaml.replace(re, line) : yaml + `\n${line}`;
                };

                let yaml = existing || '# MemoPilot provider config — do not commit\n';
                yaml = update(yaml, 'provider', 'local');
                yaml = update(yaml, 'local_url', localUrl);
                yaml = update(yaml, 'local_model', localModel);
                yaml = update(yaml, 'budget_profile', 'strict_local');

                await vscode.workspace.fs.createDirectory(configDir);
                await vscode.workspace.fs.writeFile(configFile, Buffer.from(yaml, 'utf8'));

                // Sync to provider registry immediately so sidebar updates without a restart
                if (backendClient) {
                    try {
                        await backendClient.discoverLocalProviders(workspaceFolder.uri.fsPath);
                        await refreshProviderStatus();
                    } catch { /* backend may be down; user can restart */ }
                }

                const action = await vscode.window.showInformationMessage(
                    `Local AI configured: ${localModel} at ${localUrl}. Restart backend to apply.`,
                    'Restart Now',
                );
                if (action === 'Restart Now') {
                    await restartBackendNow();
                }
                return;
            }

            const provider = await vscode.window.showQuickPick(
                [
                    { label: 'Anthropic', description: 'claude-haiku-4-5 (recommended)', value: 'anthropic' as const },
                    { label: 'OpenAI', description: 'gpt-4o-mini', value: 'openai' as const },
                ],
                { title: 'Cloud model provider for LLM touch points' },
            );
            if (!provider) { return; }

            const keyName = provider.value === 'openai' ? 'memopilot.openaiApiKey' : 'memopilot.anthropicApiKey';
            const placeholder = provider.value === 'openai' ? 'sk-...' : 'sk-ant-...';
            const apiKey = await vscode.window.showInputBox({
                title: `${provider.label} API key`,
                prompt: 'Stored securely in SecretStorage — used only for summarization, synthesis, writeback, and profile inference',
                placeHolder: placeholder,
                password: true,
                ignoreFocusOut: true,
            });
            if (!apiKey) { return; }

            await extensionContext.secrets.store(keyName, apiKey);
            const action = await vscode.window.showInformationMessage(
                `${provider.label} API key saved. Restart MemoPilot backend to apply.`,
                'Restart Now',
            );
            if (action === 'Restart Now') {
                await restartBackendNow();
            }
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`LLM touch point configuration failed: ${msg}`);
        }
    };

    const replayAICall = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        const aiCallId = await vscode.window.showInputBox({
            title: 'Replay AI call',
            prompt: 'Enter AI call id',
        });
        if (!aiCallId) { return; }
        try {
            const replay = await client.replayAICall(aiCallId);
            const contextText = replay.context_pack_text || '# No context pack captured';
            await client.storeContextPackVersion(
                contextText,
                replay.task_run_id,
                replay.model,
                undefined,
            );
            const document = await vscode.workspace.openTextDocument({
                language: 'markdown',
                content: [
                    `# Replay: ${replay.ai_call_id}`,
                    '',
                    `- Provider: ${replay.provider}`,
                    `- Model: ${replay.model}`,
                    `- Task Run: ${replay.task_run_id}`,
                    '',
                    '## Context Pack',
                    replay.context_pack_text || '_none_',
                ].join('\n'),
            });
            await vscode.window.showTextDocument(document, { preview: false });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Replay failed: ${msg}`);
        }
    };

    const manageSkillStore = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const action = await vscode.window.showQuickPick(
                ['List skills', 'Add or update skill'],
                { title: 'Skill Store' },
            );
            if (!action) { return; }
            if (action === 'List skills') {
                const listing = await client.listSkillStore();
                const document = await vscode.workspace.openTextDocument({
                    language: 'markdown',
                    content: [
                        '# Skill Store',
                        '',
                        '| Name | Version | Conflict | Applies When |',
                        '|---|---:|:---:|---|',
                        ...listing.items.map((item) => (
                            `| ${item.name} | ${item.version} | ${item.conflict ? 'Y' : 'N'} | ${item.applies_when} |`
                        )),
                    ].join('\n'),
                });
                await vscode.window.showTextDocument(document, { preview: false });
                return;
            }

            const name = await vscode.window.showInputBox({ title: 'Skill name' });
            if (!name) { return; }
            const appliesWhen = await vscode.window.showInputBox({
                title: 'Applies when',
                value: 'python tests',
            });
            if (!appliesWhen) { return; }
            const rulesInput = await vscode.window.showInputBox({
                title: 'Rules (semicolon separated)',
                value: 'must include tests',
            });
            const toolsInput = await vscode.window.showInputBox({
                title: 'Tools (semicolon separated)',
                value: 'Test;Review',
            });
            const rules = (rulesInput ?? '').split(';').map((item) => item.trim()).filter((item) => item);
            const tools = (toolsInput ?? '').split(';').map((item) => item.trim()).filter((item) => item);
            const saved = await client.upsertSkillStoreItem(name, appliesWhen, rules, tools);
            void vscode.window.showInformationMessage(
                `Skill "${saved.name}" saved (v${saved.version}, conflict=${saved.conflict}).`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Skill store failed: ${msg}`);
        }
    };

    const backupMemory = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const backup = await client.backupMemory();
            void vscode.window.showInformationMessage(
                `Memory backup created (${backup.item_count} items): ${backup.backup_path}`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Memory backup failed: ${msg}`);
        }
    };

    const restoreMemory = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        const pick = await vscode.window.showOpenDialog({
            canSelectMany: false,
            canSelectFiles: true,
            canSelectFolders: false,
            openLabel: 'Restore memory from backup',
            filters: { JSON: ['json'] },
        });
        if (!pick || pick.length === 0) { return; }
        try {
            const restored = await client.restoreMemory(pick[0].fsPath);
            await memoryProvider.refresh();
            void vscode.window.showInformationMessage(`Restored ${restored.restored_count} memory items.`);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Memory restore failed: ${msg}`);
        }
    };

    const optimizeToolsAndSkills = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        const taskText = await vscode.window.showInputBox({
            title: 'Optimizer input',
            prompt: 'Describe the task for tool/skill optimization',
        });
        if (!taskText) { return; }
        try {
            const result = await client.optimizeToolsAndSkills(
                taskText,
                ['Ask', 'Plan', 'Context Pack', 'Patch', 'Test', 'Review', 'Autofix', 'Investigate'],
            );
            const doc = await vscode.workspace.openTextDocument({
                language: 'markdown',
                content: [
                    '# Tool/Skill Optimization',
                    '',
                    `## Suggested Tools\n- ${result.suggested_tools.join('\n- ') || 'none'}`,
                    '',
                    `## Suggested Skills\n- ${result.suggested_skills.join('\n- ') || 'none'}`,
                    '',
                    `## Reasons\n- ${result.reasons.join('\n- ') || 'none'}`,
                ].join('\n'),
            });
            await vscode.window.showTextDocument(doc, { preview: false });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Optimizer failed: ${msg}`);
        }
    };


    const managePolicyPacks = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const action = await vscode.window.showQuickPick(
                ['List policy packs', 'Create or update policy pack', 'Activate policy pack'],
                { title: 'Policy Packs' },
            );
            if (!action) { return; }

            if (action === 'List policy packs') {
                const packs = await client.listPolicyPacks();
                const doc = await vscode.workspace.openTextDocument({
                    language: 'markdown',
                    content: [
                        '# Team Policy Packs',
                        '',
                        '| Name | Mode | Active | Version |',
                        '|---|---|:---:|---:|',
                        ...packs.items.map((item) => (
                            `| ${item.name} | ${item.enforcement_mode} | ${item.active ? 'Y' : 'N'} | ${item.version} |`
                        )),
                    ].join('\n'),
                });
                await vscode.window.showTextDocument(doc, { preview: false });
                return;
            }

            if (action === 'Create or update policy pack') {
                const name = await vscode.window.showInputBox({ title: 'Policy pack name' });
                if (!name) { return; }
                const description = await vscode.window.showInputBox({
                    title: 'Policy pack description',
                    value: 'Team governance policy',
                }) ?? '';
                const modePick = await vscode.window.showQuickPick(['enforce', 'advisory'], {
                    title: 'Enforcement mode',
                });
                if (!modePick) { return; }
                const rulesInput = await vscode.window.showInputBox({
                    title: 'Rules (semicolon separated)',
                    value: 'deny_model: gpt-4o, opus; require_test_file',
                });
                if (!rulesInput) { return; }
                const rules = rulesInput
                    .split(';')
                    .map((item) => item.trim())
                    .filter((item) => item.length > 0);
                const saved = await client.savePolicyPack(name, description, modePick as 'enforce' | 'advisory', rules);
                void vscode.window.showInformationMessage(`Policy pack "${saved.name}" saved (v${saved.version}).`);
                return;
            }

            const packs = await client.listPolicyPacks();
            if (packs.items.length === 0) {
                void vscode.window.showInformationMessage('No policy packs available.');
                return;
            }
            const picked = await vscode.window.showQuickPick(
                packs.items.map((item) => ({
                    label: item.name,
                    description: `${item.enforcement_mode}${item.active ? ' • active' : ''}`,
                    detail: item.pack_id,
                })),
                { title: 'Activate policy pack' },
            );
            if (!picked) { return; }
            await client.activatePolicyPack(picked.detail ?? '');
            void vscode.window.showInformationMessage(`Policy pack activated: ${picked.label}`);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Policy pack workflow failed: ${msg}`);
        }
    };

    const manageWorkspaces = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const action = await vscode.window.showQuickPick(
                ['List workspace roots', 'Add workspace root', 'Activate workspace root'],
                { title: 'Multi-workspace Manager' },
            );
            if (!action) { return; }

            if (action === 'List workspace roots') {
                const roots = await client.listWorkspaceRoots();
                const doc = await vscode.workspace.openTextDocument({
                    language: 'markdown',
                    content: [
                        '# Workspace Roots',
                        '',
                        '| Label | Path | Active |',
                        '|---|---|:---:|',
                        ...roots.items.map((item) => (
                            `| ${item.label} | ${item.root_path} | ${item.active ? 'Y' : 'N'} |`
                        )),
                    ].join('\n'),
                });
                await vscode.window.showTextDocument(doc, { preview: false });
                return;
            }

            if (action === 'Add workspace root') {
                const rootPath = await vscode.window.showInputBox({
                    title: 'Workspace root path',
                    value: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '',
                });
                if (!rootPath) { return; }
                const label = await vscode.window.showInputBox({
                    title: 'Workspace label (optional)',
                });
                const activateChoice = await vscode.window.showQuickPick(
                    ['Yes', 'No'],
                    { title: 'Activate this workspace now?' },
                );
                const added = await client.addWorkspaceRoot(rootPath, label, activateChoice === 'Yes');
                void vscode.window.showInformationMessage(
                    `Workspace added: ${added.label}${added.active ? ' (active)' : ''}`,
                );
                return;
            }

            const roots = await client.listWorkspaceRoots();
            if (roots.items.length === 0) {
                void vscode.window.showInformationMessage('No workspace roots configured.');
                return;
            }
            const picked = await vscode.window.showQuickPick(
                roots.items.map((item) => ({
                    label: item.label,
                    description: item.active ? 'active' : undefined,
                    detail: item.workspace_id,
                })),
                { title: 'Activate workspace root' },
            );
            if (!picked || !picked.detail) { return; }
            const activated = await client.activateWorkspaceRoot(picked.detail);
            void vscode.window.showInformationMessage(`Active workspace is now "${activated.label}".`);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Workspace management failed: ${msg}`);
        }
    };

    context.subscriptions.push(
        vscode.commands.registerCommand('memopilot.indexWorkspace', indexWorkspace),
        vscode.commands.registerCommand('memopilot.analyzeTask', () => {
            void openContextPreview();
        }),
        vscode.commands.registerCommand('memopilot.generateContextPack', manageContextTemplates),
        vscode.commands.registerCommand('memopilot.openRules', async () => {
            rulesProvider.setClient(backendClient);
            await rulesProvider.refresh();
        }),
        vscode.commands.registerCommand('memopilot.rebuildMemory', rebuildMemory),
        vscode.commands.registerCommand('memopilot.reindexAndSummarize', reindexAndSummarize),
        vscode.commands.registerCommand('memopilot.runSummarization', runSummarization),
        vscode.commands.registerCommand('memopilot.switchLLMMode', switchLLMMode),
        vscode.commands.registerCommand('memopilot.openWorkspaceProfile', openWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.rebuildWorkspaceProfile', rebuildWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.validateWorkspaceProfile', validateWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.exportWorkspaceProfile', exportWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.reviewMemory', reviewMemory),
        vscode.commands.registerCommand('memopilot.approveMemoryItem', approveMemoryItem),
        vscode.commands.registerCommand('memopilot.rejectMemoryItem', rejectMemoryItem),
        vscode.commands.registerCommand('memopilot.bulkApproveMemory', bulkApproveMemory),
        vscode.commands.registerCommand('memopilot.refreshMemoryReviewQueue', async () => {
            await memoryProvider.refresh();
        }),
        vscode.commands.registerCommand('memopilot.showPrivacyDashboard', showPrivacyDashboard),
        vscode.commands.registerCommand('memopilot.configureProviders', configureProviders),
        vscode.commands.registerCommand('memopilot.replayAICall', replayAICall),
        vscode.commands.registerCommand('memopilot.manageSkillStore', manageSkillStore),
        vscode.commands.registerCommand('memopilot.backupMemory', backupMemory),
        vscode.commands.registerCommand('memopilot.restoreMemory', restoreMemory),
        vscode.commands.registerCommand('memopilot.optimizeToolsAndSkills', optimizeToolsAndSkills),
        vscode.commands.registerCommand('memopilot.managePolicyPacks', managePolicyPacks),
        vscode.commands.registerCommand('memopilot.manageWorkspaces', manageWorkspaces),
        vscode.commands.registerCommand('memopilot.indexPendingChanges', async () => {
            if (!backendClient) { return; }
            pendingNew.clear();
            pendingModified.clear();
            pendingDeleted.clear();
            pendingChangesBar?.hide();
            await vscode.commands.executeCommand('memopilot.indexWorkspace');
        }),
        vscode.commands.registerCommand('memopilot.showPanel', () => {
            MemoPilotPanel.createOrShow(context.extensionUri, backendClient);
        }),
        vscode.commands.registerCommand('memopilot.restartBackend', async () => {
            await restartBackendNow();
        }),
    );

    // Register Language Model Tools (VS Code 1.99+ Copilot Chat integration)
    const toolDisposables = registerLanguageModelTools(context, () => backendClient);
    context.subscriptions.push(...toolDisposables);

    // Listen for configuration changes (e.g., memopilot.indexedLanguages)
    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((event) => {
            if (event.affectsConfiguration('memopilot.indexedLanguages') ||
                event.affectsConfiguration('memopilot.showLanguageBadges')) {
                const indexedLanguages = vscode.workspace.getConfiguration('memopilot').get<string[]>('indexedLanguages', ['python']);
                memoryProvider.setIndexedLanguages(indexedLanguages);
                void memoryProvider.refresh();
            }
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
        memoryProvider,
        profileProvider,
    );

    // Auto-index on activation — silent background, non-blocking
    void triggerAutoIndex(backendClient, outputChannel);

    context.subscriptions.push(
        vscode.workspace.onDidChangeWorkspaceFolders(() => {
            void triggerAutoIndex(backendClient, outputChannel);
        }),
    );
}

async function startBackend(
    context: vscode.ExtensionContext,
    outputChannel: vscode.OutputChannel,
    statusProvider: StatusTreeProvider,
    onConnectedRefresh: () => Promise<void>,
    memoryProvider?: MemoryManagerTreeProvider,
    profileProvider?: WorkspaceProfileTreeProvider,
): Promise<void> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) { return; }

    try {
        // Clear any existing health check interval
        if (healthCheckInterval) {
            clearInterval(healthCheckInterval);
            healthCheckInterval = undefined;
        }

        // Create handler for unexpected backend exits
        const onUnexpectedExit = async () => {
            outputChannel.appendLine(`[MemoPilot] Backend unexpected exit detected (attempt ${unexpectedExitRetryCount + 1}/${MAX_RESTART_RETRIES})`);
            
            if (unexpectedExitRetryCount >= MAX_RESTART_RETRIES) {
                statusBarItem.text = '$(error) MemoPilot';
                statusBarItem.tooltip = 'MemoPilot — Backend crashed and could not restart';
                statusProvider.setStatus('error', 'Backend crashed and could not restart');
                
                const action = await vscode.window.showErrorMessage(
                    'MemoPilot backend crashed and could not be automatically restarted.',
                    'Restart Backend',
                    'Dismiss'
                );
                if (action === 'Restart Backend') {
                    await stopBackend();
                    unexpectedExitRetryCount = 0;
                    await startBackend(context, outputChannel, statusProvider, onConnectedRefresh, memoryProvider, profileProvider);
                }
                return;
            }

            unexpectedExitRetryCount++;
            const backoffMs = Math.pow(2, unexpectedExitRetryCount) * 1000; // 2s, 4s, 8s
            outputChannel.appendLine(`[MemoPilot] Retrying backend start in ${backoffMs}ms...`);
            
            await new Promise(resolve => setTimeout(resolve, backoffMs));
            await stopBackend();
            await startBackend(context, outputChannel, statusProvider, onConnectedRefresh, memoryProvider, profileProvider);
        };

        backendManager = new BackendManager(workspaceFolder.uri.fsPath, outputChannel, onUnexpectedExit);
        await backendManager.start(extensionContext);

        backendClient = new BackendClient(backendManager);
        const health = await backendClient.health();

        if (health.status === 'ok') {
            unexpectedExitRetryCount = 0;
            statusBarItem.text = '$(check) MemoPilot';
            statusBarItem.tooltip = `MemoPilot — Connected (API v${health.api_version})`;
            statusProvider.setStatus('connected', `Backend connected — API v${health.api_version}`);

            // Initialize workspace .memopilot/ folder
            await backendClient.initWorkspace();
            outputChannel.appendLine('[MemoPilot] Workspace initialized.');
            
            // Clear any stale health check interval before creating a new one
            if (healthCheckInterval) {
                clearInterval(healthCheckInterval);
                healthCheckInterval = undefined;
            }
            
            // Set up periodic health checks (every 30 seconds)
            healthCheckInterval = setInterval(async () => {
                try {
                    await backendClient?.health();
                } catch (err) {
                    outputChannel.appendLine(`[MemoPilot] Health check failed: ${err instanceof Error ? err.message : String(err)}`);
                }
            }, 30000);
            
            // Register single managed disposable for health check cleanup
            if (!healthCheckDisposable) {
                healthCheckDisposable = new vscode.Disposable(() => {
                    if (healthCheckInterval) {
                        clearInterval(healthCheckInterval);
                        healthCheckInterval = undefined;
                    }
                });
                context.subscriptions.push(healthCheckDisposable);
            }
            
            await onConnectedRefresh();

            await refreshIndexStatus(backendClient, statusProvider, outputChannel, memoryProvider, profileProvider);

            if (vscode.workspace.workspaceFolders?.length) {
                setupFileWatcher(context);
            }
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
    if (healthCheckInterval) {
        clearInterval(healthCheckInterval);
        healthCheckInterval = undefined;
    }
    if (backendManager) {
        await backendManager.stop();
        backendManager = undefined;
        backendClient = undefined;
    }
}

function getWorkspaceRoot(): string {
    const folders = vscode.workspace.workspaceFolders;
    if (folders && folders.length > 0) {
        return folders[0].uri.fsPath;
    }
    return '';
}

async function triggerAutoIndex(
    client: BackendClient | undefined,
    outputChannel: vscode.OutputChannel,
): Promise<void> {
    if (!client) { return; }
    if (workspaceIndexingInFlight) { return; }

    workspaceIndexingInFlight = true;
    let bar: vscode.StatusBarItem | undefined;
    try {
        const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!workspaceRoot) { return; }

        const status = await client.getIndexStatus(workspaceRoot);
        const lastIndexed = status.last_indexed_at ? new Date(status.last_indexed_at).getTime() : 0;
        const hoursSince = (Date.now() - lastIndexed) / 3_600_000;

        const shouldIndex =
            !status.ever_indexed ||
            status.stale_file_count > 0 ||
            hoursSince > 24;

        if (!shouldIndex) { return; }

        bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
        bar.text = '$(sync~spin) MemoPilot indexing...';
        bar.show();

        await client.indexWorkspace(true);
        bar.text = '$(check) MemoPilot ready';
        setTimeout(() => bar?.dispose(), 3000);

        outputChannel.appendLine('[MemoPilot] Auto-index complete.');
    } catch (err: unknown) {
        // Silent failure — MemoPilot remains usable without index
        if (bar) {
            bar.dispose();
        }
    } finally {
        workspaceIndexingInFlight = false;
    }
}

export async function deactivate(): Promise<void> {
    synthesisHostClient?.dispose();
    await stopBackend();
}
