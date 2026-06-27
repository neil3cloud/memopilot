/**
 * Registers MemoPilot tools with the VS Code Language Model Tools API.
 * Feature-gated: silently skips if the API is unavailable (VS Code < 1.99).
 */

import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';
import {
    MemoPilotContextInput,
    MemoPilotMemorySearchInput,
    MemoPilotSymbolsInput,
    MemorySearchResponse,
    SymbolSearchResponse,
    WorkspaceProfileResponse,
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

    disposables.push(lm.registerTool('memopilot-search', {
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
            const response = await client.assembleContext({
                task_description: options.input.task_description,
                files_in_focus: options.input.files_in_focus ?? [],
                task_type_hint: options.input.task_type_hint ?? 'general',
                workspace_root: workspaceRoot,
                caller: 'copilot_lm_tool',
                max_output_tokens: 8000,
            });

            return createToolResult(response.rendered_markdown ?? '## MemoPilot Context\n\nNo content available.');
        },
    }));

    disposables.push(lm.registerTool('memopilot-symbols', {
        async invoke(
            options: { input: MemoPilotSymbolsInput },
            token: vscode.CancellationToken,
        ) {
            if (token.isCancellationRequested) {
                return createToolResult('## MemoPilot Symbols Cancelled');
            }

            const client = getBackendClient();
            if (!client) {
                return createToolResult('## MemoPilot Symbols Unavailable\n\nBackend not running.');
            }

            const response = await client.post<SymbolSearchResponse>('/v1/symbols/search', {
                query: options.input.query,
                limit: options.input.limit ?? 10,
            });

            if (!response.symbols || response.symbols.length === 0) {
                return createToolResult(`## MemoPilot Symbols\n\nNo symbols found for: "${options.input.query}"`);
            }

            const lines: string[] = [
                `## MemoPilot Symbols — "${options.input.query}"`,
                `_${response.symbols.length} result(s)_`,
                '',
            ];
            for (const symbol of response.symbols) {
                lines.push(`### ${symbol.name} [${symbol.kind}]`);
                lines.push(`- Location: ${symbol.file_path}:${symbol.start_line ?? '?'}`);
                if (symbol.signature) {
                    lines.push(`- Signature: \`${symbol.signature}\``);
                }
                if (symbol.summary) {
                    lines.push(`- Summary: ${symbol.summary}`);
                }
                lines.push('');
            }

            return createToolResult(lines.join('\n').trim());
        },
    }));

    disposables.push(lm.registerTool('memopilot-memory', {
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

    disposables.push(lm.registerTool('memopilot-profile', {
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

