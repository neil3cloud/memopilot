/**
 * LspContextProvider — enriches the context pack with LSP-derived information.
 *
 * Uses the VS Code Language Server Protocol client to retrieve:
 *   - Symbol definitions for the primary symbol under cursor
 *   - References (callers) across the workspace
 *   - Type hierarchy parents / children
 *
 * This data supplements the static call graph built at index time, providing
 * real-time accurate caller lists without waiting for a workspace re-index.
 */
import * as vscode from 'vscode';

export interface LspSymbolInfo {
    name: string;
    filePath: string;
    line: number;
    character: number;
    kind: vscode.SymbolKind;
}

export interface LspCallerInfo {
    callerName: string;
    filePath: string;
    line: number;
    character: number;
}

export interface LspContextResult {
    primarySymbol: LspSymbolInfo | null;
    callers: LspCallerInfo[];
    definitionFile: string | null;
}

/**
 * Provides LSP-enriched context for a given file position.
 * Designed to be called during the context build flow (TaskFlowController)
 * to augment the static graph with live reference data.
 */
export class LspContextProvider {
    /**
     * Retrieve LSP enrichment for the symbol at the given position.
     *
     * @param document  The active text document
     * @param position  Cursor position within the document
     * @returns LspContextResult — may have empty callers if LSP not available
     */
    async getContextForPosition(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): Promise<LspContextResult> {
        const [primarySymbol, callers, definitionFile] = await Promise.all([
            this._getPrimarySymbol(document, position),
            this._getCallers(document, position),
            this._getDefinitionFile(document, position),
        ]);
        return { primarySymbol, callers, definitionFile };
    }

    /**
     * Retrieve LSP enrichment for the primary symbol of a task by name.
     * Falls back to document-position lookup when no active editor matches.
     *
     * @param symbolName  Name of the symbol to look up (e.g. "UserService")
     * @param workspaceFolders  Workspace folder URIs to search within
     */
    async getContextForSymbol(
        symbolName: string,
        workspaceFolders: readonly vscode.WorkspaceFolder[],
    ): Promise<LspContextResult> {
        if (!symbolName || workspaceFolders.length === 0) {
            return { primarySymbol: null, callers: [], definitionFile: null };
        }

        // Use workspace symbol search to find the primary symbol
        let symbols: vscode.SymbolInformation[] = [];
        try {
            symbols = await vscode.commands.executeCommand<vscode.SymbolInformation[]>(
                'vscode.executeWorkspaceSymbolProvider',
                symbolName,
            ) ?? [];
        } catch {
            // LSP not available for this language — return empty result
            return { primarySymbol: null, callers: [], definitionFile: null };
        }

        const exact = symbols.find(s => s.name === symbolName);
        if (!exact) {
            return { primarySymbol: null, callers: [], definitionFile: null };
        }

        const uri = exact.location.uri;
        const startPos = exact.location.range.start;

        let document: vscode.TextDocument;
        try {
            document = await vscode.workspace.openTextDocument(uri);
        } catch {
            return {
                primarySymbol: {
                    name: exact.name,
                    filePath: uri.fsPath,
                    line: startPos.line,
                    character: startPos.character,
                    kind: exact.kind,
                },
                callers: [],
                definitionFile: uri.fsPath,
            };
        }

        return this.getContextForPosition(document, startPos);
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    private async _getPrimarySymbol(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): Promise<LspSymbolInfo | null> {
        try {
            const symbols = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
                'vscode.executeDocumentSymbolProvider',
                document.uri,
            ) ?? [];

            const symbol = this._findEnclosingSymbol(symbols, position);
            if (!symbol) {
                return null;
            }
            return {
                name: symbol.name,
                filePath: document.uri.fsPath,
                line: symbol.selectionRange.start.line,
                character: symbol.selectionRange.start.character,
                kind: symbol.kind,
            };
        } catch {
            return null;
        }
    }

    private async _getCallers(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): Promise<LspCallerInfo[]> {
        try {
            const locations = await vscode.commands.executeCommand<vscode.Location[]>(
                'vscode.executeReferenceProvider',
                document.uri,
                position,
            ) ?? [];

            const callers: LspCallerInfo[] = [];
            for (const loc of locations.slice(0, 20)) {
                // Skip the definition site itself
                if (
                    loc.uri.fsPath === document.uri.fsPath &&
                    loc.range.start.line === position.line
                ) {
                    continue;
                }
                callers.push({
                    callerName: loc.uri.fsPath,
                    filePath: loc.uri.fsPath,
                    line: loc.range.start.line,
                    character: loc.range.start.character,
                });
            }
            return callers;
        } catch {
            return [];
        }
    }

    private async _getDefinitionFile(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): Promise<string | null> {
        try {
            const locations = await vscode.commands.executeCommand<vscode.Location[]>(
                'vscode.executeDefinitionProvider',
                document.uri,
                position,
            ) ?? [];

            return locations[0]?.uri.fsPath ?? null;
        } catch {
            return null;
        }
    }

    private _findEnclosingSymbol(
        symbols: vscode.DocumentSymbol[],
        position: vscode.Position,
    ): vscode.DocumentSymbol | null {
        for (const symbol of symbols) {
            if (symbol.range.contains(position)) {
                const child = this._findEnclosingSymbol(symbol.children, position);
                return child ?? symbol;
            }
        }
        return null;
    }
}
