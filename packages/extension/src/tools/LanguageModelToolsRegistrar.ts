/**
 * Registers MemoPilot tools with the VS Code Language Model Tools API.
 * Feature-gated: silently skips if the API is unavailable (VS Code < 1.99).
 */

import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';
import {
    ContextPackToolResponse,
    MemoPilotContextInput,
    MemoPilotMemorySearchInput,
    MemoPilotPatchReviewInput,
    MemoPilotWritebackInput,
    MemorySearchResponse,
    PatchReviewResponse,
    RulesResponse,
    WorkspaceProfileResponse,
    WritebackResponse,
} from './types';

/**
 * Register all MemoPilot LM tools if the API is available.
 * Returns disposables for cleanup.
 */
export function registerLanguageModelTools(
    context: vscode.ExtensionContext,
    getBackendClient: () => BackendClient | undefined,
): vscode.Disposable[] {
    void context;

    const lm = (vscode as any).lm as { registerTool?: (name: string, tool: unknown) => vscode.Disposable } | undefined;
    if (typeof lm?.registerTool !== 'function') {
        return [];
    }

    const disposables: vscode.Disposable[] = [];

    disposables.push(lm.registerTool('memopilot_context', {
        async invoke(
            options: { input: MemoPilotContextInput },
            token: vscode.CancellationToken,
        ) {
            if (token.isCancellationRequested) {
                return createToolResult('## MemoPilot Context Cancelled');
            }

            const client = getBackendClient();
            if (!client) {
                return createToolResult('## MemoPilot Context Unavailable\n\nThe MemoPilot backend is not running.');
            }

            const workspaceRoot = getWorkspaceRoot();
            const response = await client.post<ContextPackToolResponse>('/v1/context-pack/generate', {
                task_description: options.input.task_description,
                suggested_files: options.input.files_in_focus ?? [],
                task_type: options.input.task_type_hint ?? 'general',
                workspace_root: workspaceRoot,
                caller: 'copilot_lm_tool',
                output_format: 'markdown_for_llm',
                max_output_tokens: 8000,
            });

            return createToolResult(response.rendered_markdown ?? '## MemoPilot Context\n\nNo content available.');
        },
    }));

    disposables.push(lm.registerTool('memopilot_rules', {
        async invoke(
            _options: { input: Record<string, never> },
            token: vscode.CancellationToken,
        ) {
            if (token.isCancellationRequested) {
                return createToolResult('## MemoPilot Rules Cancelled');
            }

            const client = getBackendClient();
            if (!client) {
                return createToolResult('## MemoPilot Rules Unavailable\n\nBackend not running.');
            }

            const workspaceRoot = getWorkspaceRoot();
            const response = await client.get<RulesResponse>(
                `/v1/rules/active?workspace_root=${encodeURIComponent(workspaceRoot)}&caller=copilot_lm_tool`,
            );

            return createToolResult(response.rendered_markdown ?? 'No rules found.');
        },
    }));

    disposables.push(lm.registerTool('memopilot_memory_search', {
        async invoke(
            options: { input: MemoPilotMemorySearchInput },
            token: vscode.CancellationToken,
        ) {
            if (token.isCancellationRequested) {
                return createToolResult('## MemoPilot Memory Search Cancelled');
            }

            const client = getBackendClient();
            if (!client) {
                return createToolResult('## MemoPilot Memory Unavailable\n\nBackend not running.');
            }

            const workspaceRoot = getWorkspaceRoot();
            const response = await client.post<MemorySearchResponse>('/v1/memory/recall', {
                query: options.input.query,
                limit: options.input.limit ?? 10,
                workspace_root: workspaceRoot,
                caller: 'copilot_lm_tool',
                output_format: 'markdown_for_llm',
            });

            return createToolResult(response.rendered_markdown ?? 'No results found.');
        },
    }));

    disposables.push(lm.registerTool('memopilot_workspace_profile', {
        async invoke(
            _options: { input: Record<string, never> },
            token: vscode.CancellationToken,
        ) {
            if (token.isCancellationRequested) {
                return createToolResult('## MemoPilot Workspace Profile Cancelled');
            }

            const client = getBackendClient();
            if (!client) {
                return createToolResult('## Workspace Profile Unavailable\n\nBackend not running.');
            }

            const workspaceRoot = getWorkspaceRoot();
            const response = await client.get<WorkspaceProfileResponse>(
                `/v1/workspace/profile?workspace_root=${encodeURIComponent(workspaceRoot)}&caller=copilot_lm_tool`,
            );

            return createToolResult(response.rendered_markdown ?? 'Profile unavailable.');
        },
    }));

    disposables.push(lm.registerTool('memopilot_patch_review', {
        async invoke(
            options: { input: MemoPilotPatchReviewInput },
            token: vscode.CancellationToken,
        ) {
            if (token.isCancellationRequested) {
                return createToolResult('## MemoPilot Patch Review Cancelled');
            }

            const client = getBackendClient();
            if (!client) {
                return createToolResult('## Patch Review Unavailable\n\nBackend not running.');
            }

            const workspaceRoot = getWorkspaceRoot();
            let gitDiff = options.input.git_diff;
            if (!gitDiff) {
                gitDiff = await getGitDiff(workspaceRoot);
            }
            if (!gitDiff || !gitDiff.trim()) {
                return createToolResult('## MemoPilot Patch Review\n\nNo uncommitted changes detected. Apply a patch first.');
            }

            const response = await client.post<PatchReviewResponse>('/v1/task/review-applied-patch', {
                git_diff: gitDiff,
                workspace_root: workspaceRoot,
                caller: 'copilot_lm_tool',
            });

            return createToolResult(response.rendered_report ?? 'No report available.');
        },
    }));

    disposables.push(lm.registerTool('memopilot_writeback', {
        async invoke(
            options: { input: MemoPilotWritebackInput },
            token: vscode.CancellationToken,
        ) {
            if (token.isCancellationRequested) {
                return createToolResult('## MemoPilot Writeback Cancelled');
            }

            const client = getBackendClient();
            if (!client) {
                return createToolResult('## Writeback Unavailable\n\nBackend not running.');
            }

            const workspaceRoot = getWorkspaceRoot();
            const response = await client.post<WritebackResponse>('/v1/tool-mode/writeback', {
                outcome_summary: options.input.outcome_summary,
                outcome_status: options.input.outcome_status,
                context_pack_hash: options.input.context_pack_hash ?? null,
                git_diff: options.input.git_diff ?? null,
                workspace_root: workspaceRoot,
                caller: 'copilot_lm_tool',
            });

            void vscode.commands.executeCommand('memopilot.refreshMemoryReviewQueue');

            return createToolResult(response.rendered_summary ?? 'Writeback completed.');
        },
    }));

    return disposables;
}

function createToolResult(markdown: string): unknown {
    const LanguageModelToolResult = (vscode as any).LanguageModelToolResult;
    const LanguageModelTextPart = (vscode as any).LanguageModelTextPart;

    if (typeof LanguageModelToolResult !== 'function' || typeof LanguageModelTextPart !== 'function') {
        return markdown;
    }

    return new LanguageModelToolResult([
        new LanguageModelTextPart(markdown),
    ]);
}

function getWorkspaceRoot(): string {
    const folders = vscode.workspace.workspaceFolders;
    if (folders && folders.length > 0) {
        return folders[0].uri.fsPath;
    }
    return '';
}

async function getGitDiff(workspaceRoot: string): Promise<string> {
    if (!workspaceRoot) {
        return '';
    }

    try {
        const { exec } = require('child_process') as typeof import('child_process');
        return new Promise<string>((resolve) => {
            exec('git diff HEAD', { cwd: workspaceRoot, maxBuffer: 1024 * 1024 }, (error: Error | null, stdout: string) => {
                resolve(error ? '' : stdout);
            });
        });
    } catch {
        return '';
    }
}
