import * as vscode from 'vscode';
import * as path from 'path';
import { BackendClient, IndexStatusResponse } from './BackendClient';
import { BackendManager } from './BackendManager';
import { registerLanguageModelTools } from './tools/LanguageModelToolsRegistrar';
import { StatusTreeProvider } from './views/StatusTreeProvider';
import { PlaceholderTreeProvider } from './views/PlaceholderTreeProvider';
import { WorkspaceProfileTreeProvider } from './views/WorkspaceProfileTreeProvider';
import { MemoryManagerTreeProvider, MEMORY_FILTERS, MemoryFilter } from './views/MemoryManagerTreeProvider';
import { PrivacyDashboardTreeProvider } from './views/PrivacyDashboardTreeProvider';
import { EvidenceBoardTreeProvider } from './views/EvidenceBoardTreeProvider';
import { RulesSkillsTreeProvider } from './views/RulesSkillsTreeProvider';
import { CostGuardTreeProvider } from './views/CostGuardTreeProvider';
import { ContextPackTreeProvider } from './views/ContextPackTreeProvider';
import { TaskHistoryTreeProvider } from './views/TaskHistoryTreeProvider';
import { McpToolsTreeProvider } from './views/McpToolsTreeProvider';
import { MemoPilotPanel } from './panels/MemoPilotPanel';
import { TaskEntryPanel } from './panels/TaskEntryPanel';
import { PatchPreviewPanel } from './panels/PatchPreviewPanel';
import { CostDashboardPanel } from './panels/CostDashboardPanel';
import { ProviderMatrixPanel } from './panels/ProviderMatrixPanel';
import { TaskFlowController } from './controllers/TaskFlowController';

let backendManager: BackendManager | undefined;
let backendClient: BackendClient | undefined;
let taskFlowController: TaskFlowController | undefined;
let statusBarItem: vscode.StatusBarItem;
let workspaceIndexingInFlight = false;

async function refreshIndexStatus(
    client: BackendClient,
    statusProvider: StatusTreeProvider,
    outputChannel: vscode.OutputChannel,
): Promise<IndexStatusResponse | undefined> {
    try {
        const indexStatus = await client.getIndexStatus();
        statusProvider.updateIndexStatus(indexStatus);
        return indexStatus;
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        outputChannel.appendLine(`[MemoPilot] Failed to fetch index status: ${msg}`);
        statusProvider.updateIndexStatus(undefined);
        return undefined;
    }
}
let healthCheckInterval: ReturnType<typeof setInterval> | undefined;
let unexpectedExitRetryCount = 0;
const MAX_RESTART_RETRIES = 3;

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
    const rulesProvider = new RulesSkillsTreeProvider();
    const contextProvider = new ContextPackTreeProvider();
    const costProvider = new CostGuardTreeProvider();
    const privacyProvider = new PrivacyDashboardTreeProvider();
    const evidenceProvider = new EvidenceBoardTreeProvider();
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
        vscode.window.registerTreeDataProvider('memopilot-evidence', evidenceProvider),
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
                    await refreshIndexStatus(backendClient!, statusProvider, outputChannel);
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
        rulesProvider.setClient(backendClient);
        costProvider.setClient(backendClient);
        historyProvider.setClient(backendClient);
        mcpProvider.setClient(backendClient);
        // Update the main panel if it's open
        if (MemoPilotPanel.currentPanel) {
            MemoPilotPanel.currentPanel.setClient(backendClient);
        }
        // Recreate task flow controller with new client
        if (backendClient) {
            taskFlowController = new TaskFlowController(backendClient, backendManager);
        }
        await Promise.all([
            profileProvider.refresh(),
            memoryProvider.refresh(),
            privacyProvider.refresh(),
            evidenceProvider.refresh(),
            rulesProvider.refresh(),
            costProvider.refresh(),
            historyProvider.refresh(),
            mcpProvider.refresh(),
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
                const extensionName = path.extname(pick.fsPath).toLowerCase();
                let columnMapping: Record<string, string> | undefined;
                if (extensionName === '.xlsx' || extensionName === '.xlsm' || extensionName === '.xltx') {
                    const preview = await client.previewEvidenceColumns(pick.fsPath);
                    if (preview.requires_confirmation && preview.columns.length > 0) {
                        columnMapping = await confirmExcelColumnMapping(preview.columns, preview.suggested_mapping);
                    }
                }
                await client.attachEvidence(pick.fsPath, columnMapping);
            }
            await evidenceProvider.refresh();
            void vscode.window.showInformationMessage(`Attached ${picks.length} evidence file(s).`);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`MemoPilot failed to attach evidence: ${msg}`);
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

    const showProviderCapabilities = async () => {
        ProviderMatrixPanel.createOrShow(context.extensionUri, backendClient);
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

    const selectBudgetProfile = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const current = await client.getBudgetProfiles();
            const selected = await vscode.window.showQuickPick(
                Object.keys(current.profiles).map((profile) => ({
                    label: profile,
                    description: profile === current.active_profile ? 'active' : undefined,
                })),
                { title: 'Select budget profile' },
            );
            if (!selected) { return; }
            const updated = await client.setBudgetProfile(selected.label);
            void vscode.window.showInformationMessage(
                `Budget profile set to ${updated.active_profile} (effective $${updated.effective_budget_usd.toFixed(2)}).`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Budget profile update failed: ${msg}`);
        }
    };

    const classifyEvidenceSource = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        const picks = await vscode.window.showOpenDialog({
            canSelectMany: false,
            canSelectFiles: true,
            canSelectFolders: false,
            openLabel: 'Classify Evidence',
        });
        if (!picks || picks.length === 0) { return; }
        try {
            const result = await client.classifyEvidenceSource(picks[0].fsPath);
            void vscode.window.showInformationMessage(
                `Evidence classified as ${result.source_type} (trust ${result.trust_level}, ${result.extraction_method}).`,
            );
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Evidence classification failed: ${msg}`);
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

    const runLocalAgentFlow = async () => {
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const action = await vscode.window.showQuickPick(
                ['Run existing flow', 'Create default flow and run'],
                { title: 'Local Agent Flow Builder' },
            );
            if (!action) { return; }

            let flowId: string | undefined;
            if (action === 'Create default flow and run') {
                const created = await client.saveLocalFlow(
                    'default-governed-flow',
                    'Policy check + optimization + approval gate',
                    [
                        { id: 'policy-1', title: 'Policy gate', action: 'policy_check', stage: 'model_call' },
                        {
                            id: 'opt-1',
                            title: 'Tool optimizer',
                            action: 'tool_recommend',
                            available_tools: ['Ask', 'Plan', 'Patch', 'Test', 'Review', 'Investigate'],
                        },
                        { id: 'approval-1', title: 'Approval gate', action: 'approval_gate' },
                    ],
                );
                flowId = created.flow_id;
            } else {
                const flows = await client.listLocalFlows();
                if (flows.items.length === 0) {
                    void vscode.window.showInformationMessage('No local flows available.');
                    return;
                }
                const pickedFlow = await vscode.window.showQuickPick(
                    flows.items.map((item) => ({
                        label: item.name,
                        description: item.description || undefined,
                        detail: item.flow_id,
                    })),
                    { title: 'Select local flow to run' },
                );
                if (!pickedFlow) { return; }
                flowId = pickedFlow.detail;
            }

            if (!flowId) { return; }
            const taskText = await vscode.window.showInputBox({
                title: 'Flow task text',
                value: 'Investigate failing tests and propose a patch plan',
            });
            if (!taskText) { return; }
            const selectedModel = await vscode.window.showInputBox({
                title: 'Selected model (optional)',
                value: 'gpt-4o-mini',
            });

            const run = await client.runLocalFlow(flowId, taskText, [], selectedModel || undefined);
            const doc = await vscode.workspace.openTextDocument({
                language: 'markdown',
                content: [
                    `# Local Flow Run: ${run.flow_name}`,
                    '',
                    `- Status: ${run.status}`,
                    `- Run ID: ${run.run_id}`,
                    `- Blocked reason: ${run.blocked_reason ?? 'none'}`,
                    '',
                    '## Steps',
                    ...run.steps.map((step) => `- ${String(step.title ?? step.action)} => ${String(step.status ?? 'n/a')}`),
                ].join('\n'),
            });
            await vscode.window.showTextDocument(doc, { preview: false });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Local flow run failed: ${msg}`);
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

    const confirmExcelColumnMapping = async (
        columns: string[],
        suggestedMapping: Record<string, string>,
    ): Promise<Record<string, string>> => {
        const roles: Array<{ role: string; title: string }> = [
            { role: 'title', title: 'Select Excel column for test title' },
            { role: 'steps', title: 'Select Excel column for test steps (optional)' },
            { role: 'expected', title: 'Select Excel column for expected result (optional)' },
        ];

        const selectedMapping: Record<string, string> = {};
        for (const role of roles) {
            const options = [
                { label: '(Skip)', column: '' },
                ...columns.map((column) => ({ label: column, column })),
            ];
            const preselected = suggestedMapping[role.role];
            const picked = await vscode.window.showQuickPick(options, {
                title: role.title,
                placeHolder: preselected ? `Suggested: ${preselected}` : undefined,
            });
            if (!picked) {
                continue;
            }
            if (picked.column) {
                selectedMapping[role.role] = picked.column;
            }
        }
        return selectedMapping;
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
        vscode.commands.registerCommand('memopilot.indexWorkspace', indexWorkspace),
        vscode.commands.registerCommand('memopilot.analyzeTask', () => {
            TaskEntryPanel.createOrShow(context.extensionUri, backendClient, taskFlowController);
        }),
        vscode.commands.registerCommand('memopilot.approvePatch', async () => {
            if (taskFlowController) {
                await taskFlowController.approve();
                const state = taskFlowController.getState();
                if (state.stage === 'applying') {
                    vscode.window.showInformationMessage('MemoPilot: Patch approved. Applying changes...');
                    taskFlowController.complete();
                }
            }
        }),
        vscode.commands.registerCommand('memopilot.rejectPatch', () => {
            if (taskFlowController) {
                taskFlowController.reject();
                vscode.window.showInformationMessage('MemoPilot: Patch rejected.');
            }
        }),
        vscode.commands.registerCommand('memopilot.generateContextPack', manageContextTemplates),
        vscode.commands.registerCommand('memopilot.showCostReport', () => {
            CostDashboardPanel.createOrShow(context.extensionUri, backendClient);
        }),
        vscode.commands.registerCommand('memopilot.openRules', async () => {
            rulesProvider.setClient(backendClient);
            await rulesProvider.refresh();
        }),
        vscode.commands.registerCommand('memopilot.rebuildMemory', rebuildMemory),
        vscode.commands.registerCommand('memopilot.openWorkspaceProfile', openWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.rebuildWorkspaceProfile', rebuildWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.validateWorkspaceProfile', validateWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.exportWorkspaceProfile', exportWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.reviewMemory', reviewMemory),
        vscode.commands.registerCommand('memopilot.reviewAppliedPatch', async () => {
            const client = ensureBackendClient();
            if (!client) { return; }

            const workspaceRoot = getWorkspaceRoot();
            const gitDiff = await getGitDiffForReview(workspaceRoot);

            if (!gitDiff || !gitDiff.trim()) {
                vscode.window.showInformationMessage('No uncommitted changes to review. Apply a patch first.');
                return;
            }

            try {
                const review = await client.post<{ rendered_report?: string }>('/v1/task/review-applied-patch', {
                    git_diff: gitDiff,
                    workspace_root: workspaceRoot,
                    caller: 'memopilot_ui',
                });
                const patchReviewOutputChannel = vscode.window.createOutputChannel('MemoPilot Patch Review');
                patchReviewOutputChannel.clear();
                patchReviewOutputChannel.appendLine(review.rendered_report ?? 'No report available.');
                patchReviewOutputChannel.show(true);
            } catch (err: unknown) {
                const msg = err instanceof Error ? err.message : String(err);
                vscode.window.showErrorMessage(`MemoPilot patch review failed: ${msg}`);
            }
        }),
        vscode.commands.registerCommand('memopilot.refreshMemoryReviewQueue', async () => {
            await memoryProvider.refresh();
        }),
        vscode.commands.registerCommand('memopilot.showPrivacyDashboard', showPrivacyDashboard),
        vscode.commands.registerCommand('memopilot.showProviderCapabilities', showProviderCapabilities),
        vscode.commands.registerCommand('memopilot.replayAICall', replayAICall),
        vscode.commands.registerCommand('memopilot.manageSkillStore', manageSkillStore),
        vscode.commands.registerCommand('memopilot.backupMemory', backupMemory),
        vscode.commands.registerCommand('memopilot.restoreMemory', restoreMemory),
        vscode.commands.registerCommand('memopilot.optimizeToolsAndSkills', optimizeToolsAndSkills),
        vscode.commands.registerCommand('memopilot.selectBudgetProfile', selectBudgetProfile),
        vscode.commands.registerCommand('memopilot.classifyEvidenceSource', classifyEvidenceSource),
        vscode.commands.registerCommand('memopilot.managePolicyPacks', managePolicyPacks),
        vscode.commands.registerCommand('memopilot.runLocalAgentFlow', runLocalAgentFlow),
        vscode.commands.registerCommand('memopilot.manageWorkspaces', manageWorkspaces),
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

    // Register Language Model Tools (VS Code 1.99+ Copilot Chat integration)
    const toolDisposables = registerLanguageModelTools(context, () => backendClient);
    context.subscriptions.push(...toolDisposables);

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
): Promise<void> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0];
    if (!workspaceFolder) { return; }

    try {
        // Clear any existing health check interval
        if (healthCheckInterval) {
            clearInterval(healthCheckInterval);
            healthCheckInterval = undefined;
        }
        unexpectedExitRetryCount = 0;

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
                    await startBackend(context, outputChannel, statusProvider, onConnectedRefresh);
                }
                return;
            }

            unexpectedExitRetryCount++;
            const backoffMs = Math.pow(2, unexpectedExitRetryCount) * 1000; // 2s, 4s, 8s
            outputChannel.appendLine(`[MemoPilot] Retrying backend start in ${backoffMs}ms...`);
            
            await new Promise(resolve => setTimeout(resolve, backoffMs));
            await stopBackend();
            await startBackend(context, outputChannel, statusProvider, onConnectedRefresh);
        };

        backendManager = new BackendManager(workspaceFolder.uri.fsPath, outputChannel, onUnexpectedExit);
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
            
            // Set up periodic health checks (every 30 seconds)
            healthCheckInterval = setInterval(async () => {
                try {
                    await backendClient?.health();
                } catch (err) {
                    outputChannel.appendLine(`[MemoPilot] Health check failed: ${err instanceof Error ? err.message : String(err)}`);
                }
            }, 30000);
            
            context.subscriptions.push(new (class {
                dispose() {
                    if (healthCheckInterval) {
                        clearInterval(healthCheckInterval);
                        healthCheckInterval = undefined;
                    }
                }
            })());
            
            await onConnectedRefresh();

            const indexStatus = await refreshIndexStatus(backendClient, statusProvider, outputChannel);
            if (indexStatus?.never_indexed && !workspaceIndexingInFlight) {
                workspaceIndexingInFlight = true;
                outputChannel.appendLine('[MemoPilot] Auto-indexing workspace in background...');
                void (async () => {
                    try {
                        await backendClient!.indexWorkspace();
                    } catch (err: unknown) {
                        const msg = err instanceof Error ? err.message : String(err);
                        outputChannel.appendLine(`[MemoPilot] Background index failed: ${msg}`);
                    } finally {
                        workspaceIndexingInFlight = false;
                        if (backendClient) {
                            await refreshIndexStatus(backendClient, statusProvider, outputChannel);
                        }
                    }
                })();
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

async function getGitDiffForReview(workspaceRoot: string): Promise<string> {
    if (!workspaceRoot) {
        return '';
    }

    const { exec } = require('child_process') as typeof import('child_process');
    return new Promise<string>((resolve) => {
        exec('git diff HEAD', { cwd: workspaceRoot, maxBuffer: 1024 * 1024 }, (error: Error | null, stdout: string) => {
            resolve(error ? '' : stdout);
        });
    });
}

async function triggerAutoIndex(
    client: BackendClient | undefined,
    outputChannel: vscode.OutputChannel,
): Promise<void> {
    if (!client) { return; }

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

        const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
        bar.text = '$(sync~spin) MemoPilot indexing...';
        bar.show();

        await client.indexWorkspace(true);
        bar.text = '$(check) MemoPilot ready';
        setTimeout(() => bar.dispose(), 3000);

        outputChannel.appendLine('[MemoPilot] Auto-index complete.');
    } catch {
        // Silent failure — MemoPilot remains usable without index
    }
}

export async function deactivate(): Promise<void> {
    await stopBackend();
}
