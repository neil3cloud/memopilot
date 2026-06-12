import * as vscode from 'vscode';
import * as path from 'path';
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
        const client = ensureBackendClient();
        if (!client) { return; }
        try {
            const result = await client.listProviderCapabilities();
            const lines = [
                '# Provider Capability Matrix',
                '',
                '| Model | Source | Max Context | Tools | JSON | Privacy | Approval |',
                '|---|---|---:|:---:|:---:|---|:---:|',
                ...result.items.map((item) => (
                    `| ${item.model_id} | ${item.source} | ${item.max_context_tokens ?? 0} | `
                    + `${item.supports_tool_calling ? 'Y' : 'N'} | ${item.supports_json_mode ? 'Y' : 'N'} | `
                    + `${item.privacy_level} | ${item.requires_approval ? 'Y' : 'N'} |`
                )),
            ];
            const document = await vscode.workspace.openTextDocument({
                language: 'markdown',
                content: lines.join('\n'),
            });
            await vscode.window.showTextDocument(document, { preview: false });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(`Provider matrix failed: ${msg}`);
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
        vscode.commands.registerCommand('memopilot.indexWorkspace', notImplemented('Index Workspace Memory')),
        vscode.commands.registerCommand('memopilot.analyzeTask', notImplemented('Analyze Current Task')),
        vscode.commands.registerCommand('memopilot.generateContextPack', manageContextTemplates),
        vscode.commands.registerCommand('memopilot.showCostReport', notImplemented('Show Cost Report')),
        vscode.commands.registerCommand('memopilot.openRules', notImplemented('Open Rules')),
        vscode.commands.registerCommand('memopilot.rebuildMemory', rebuildMemory),
        vscode.commands.registerCommand('memopilot.openWorkspaceProfile', openWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.rebuildWorkspaceProfile', rebuildWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.validateWorkspaceProfile', validateWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.exportWorkspaceProfile', exportWorkspaceProfile),
        vscode.commands.registerCommand('memopilot.reviewMemory', reviewMemory),
        vscode.commands.registerCommand('memopilot.showPrivacyDashboard', showPrivacyDashboard),
        vscode.commands.registerCommand('memopilot.showProviderCapabilities', showProviderCapabilities),
        vscode.commands.registerCommand('memopilot.replayAICall', replayAICall),
        vscode.commands.registerCommand('memopilot.manageSkillStore', manageSkillStore),
        vscode.commands.registerCommand('memopilot.backupMemory', backupMemory),
        vscode.commands.registerCommand('memopilot.restoreMemory', restoreMemory),
        vscode.commands.registerCommand('memopilot.optimizeToolsAndSkills', optimizeToolsAndSkills),
        vscode.commands.registerCommand('memopilot.selectBudgetProfile', selectBudgetProfile),
        vscode.commands.registerCommand('memopilot.classifyEvidenceSource', classifyEvidenceSource),
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
