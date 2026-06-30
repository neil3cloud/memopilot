import * as vscode from 'vscode';
import { BackendClient, IndexStatusResponse } from '../BackendClient';

const POLL_INTERVAL_MS = 5000;

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
    private indexStatus: IndexStatusResponse | undefined;
    private error: string | undefined;
    private _pollTimer: ReturnType<typeof setTimeout> | undefined;

    setClient(client: BackendClient | undefined): void {
        this.client = client;
    }

    async refresh(): Promise<void> {
        if (!this.client) {
            this.stats = undefined;
            this.indexStatus = undefined;
            this.error = undefined;
            this._stopPolling();
            this._onDidChangeTreeData.fire(undefined);
            return;
        }
        try {
            const [stats, indexStatus] = await Promise.all([
                this.client.getUsageStats(),
                this.client.getIndexStatus().catch(() => undefined),
            ]);
            this.stats = stats;
            this.indexStatus = indexStatus ?? undefined;
            this.error = undefined;
        } catch (err) {
            this.error = err instanceof Error ? err.message : String(err);
        }
        this._onDidChangeTreeData.fire(undefined);
        this._scheduleNextPoll();
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
        const activelySummarizing = this.indexStatus?.summarizing === true;

        // Symbols row
        const summarizedPct = s.symbols_indexed > 0
            ? Math.round((s.symbols_summarized / s.symbols_indexed) * 100)
            : 0;
        const symbolsItem = new vscode.TreeItem(`${s.symbols_indexed} symbols indexed`);
        symbolsItem.description = `${s.symbols_summarized} summarized (${summarizedPct}%)`;

        if (activelySummarizing) {
            symbolsItem.iconPath = new vscode.ThemeIcon('loading~spin');
        } else if (summarizedPct === 100) {
            symbolsItem.iconPath = new vscode.ThemeIcon('database');
        } else {
            // Summarization not running but incomplete — prompt user to run it
            symbolsItem.iconPath = new vscode.ThemeIcon('warning');
            symbolsItem.tooltip = 'Some symbols are not yet summarized. Run "Reindex & Summarize" to continue.';
        }

        // Memory items row
        const memoryItem = new vscode.TreeItem(`${s.memory_items_total} memory items`);
        if (s.memory_items_learned > 0) {
            memoryItem.description = `${s.memory_items_learned} learned from sessions`;
        } else if (s.memory_items_total > 0) {
            memoryItem.description = 'seeded from codebase';
        } else {
            memoryItem.description = 'none yet';
        }
        memoryItem.iconPath = new vscode.ThemeIcon('book');

        // Session row
        const sessionItem = new vscode.TreeItem(
            s.session_queries > 0
                ? `${s.session_queries} queries this session`
                : 'No queries this session',
        );
        sessionItem.description = s.session_queries > 0 ? 'synthesis pending on idle' : '';
        sessionItem.iconPath = new vscode.ThemeIcon('search');

        return [symbolsItem, memoryItem, sessionItem];
    }

    private _scheduleNextPoll(): void {
        this._stopPolling();
        // Only keep polling while summarization is actively running on the backend.
        if (this.indexStatus?.summarizing === true) {
            this._pollTimer = setTimeout(() => void this.refresh(), POLL_INTERVAL_MS);
        }
    }

    private _stopPolling(): void {
        if (this._pollTimer !== undefined) {
            clearTimeout(this._pollTimer);
            this._pollTimer = undefined;
        }
    }
}
