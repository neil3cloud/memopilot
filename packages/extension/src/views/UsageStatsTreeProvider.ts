import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';

interface UsageStats {
    symbols_indexed: number;
    symbols_summarized: number;
    memory_items_total: number;
    memory_items_learned: number;
    session_queries: number;
}

export class UsageStatsTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private stats: UsageStats | undefined;
    private error: string | undefined;

    setClient(client: BackendClient | undefined): void {
        this.client = client;
    }

    async refresh(): Promise<void> {
        if (!this.client) {
            this.stats = undefined;
            this.error = undefined;
            this._onDidChangeTreeData.fire(undefined);
            return;
        }
        try {
            this.stats = await this.client.getUsageStats();
            this.error = undefined;
        } catch (err) {
            this.error = err instanceof Error ? err.message : String(err);
        }
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        if (!this.client) {
            return [new vscode.TreeItem('Backend not connected.')];
        }
        if (this.error) {
            return [new vscode.TreeItem(`Error: ${this.error}`)];
        }
        if (!this.stats) {
            return [new vscode.TreeItem('Usage stats will appear after backend connects.')];
        }

        const s = this.stats;

        // Symbols row
        const summarizedPct = s.symbols_indexed > 0
            ? Math.round((s.symbols_summarized / s.symbols_indexed) * 100)
            : 0;
        const symbolsItem = new vscode.TreeItem(
            `${s.symbols_indexed} symbols indexed`,
        );
        symbolsItem.description = `${s.symbols_summarized} summarized (${summarizedPct}%)`;
        symbolsItem.iconPath = new vscode.ThemeIcon(
            summarizedPct === 100 ? 'database' : 'loading~spin',
        );

        // Memory items row
        const memoryItem = new vscode.TreeItem(`${s.memory_items_total} memory items`);
        memoryItem.description = s.memory_items_learned > 0
            ? `${s.memory_items_learned} learned from sessions`
            : 'seeded from codebase';
        memoryItem.iconPath = new vscode.ThemeIcon('book');

        // Session row
        const sessionItem = new vscode.TreeItem(
            s.session_queries > 0
                ? `${s.session_queries} queries this session`
                : 'No queries this session',
        );
        sessionItem.description = s.session_queries > 0 ? 'synthesis pending on idle' : '';
        sessionItem.iconPath = new vscode.ThemeIcon(
            s.session_queries > 0 ? 'search' : 'search',
        );

        return [symbolsItem, memoryItem, sessionItem];
    }
}
