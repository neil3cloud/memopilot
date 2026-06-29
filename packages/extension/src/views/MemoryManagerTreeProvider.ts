import * as vscode from 'vscode';

import { BackendClient, MemoryItemResponse, IndexStatusResponse } from '../BackendClient';

export const MEMORY_FILTERS = [
    'all',
    'rules',
    'symbols',
    'file_summaries',
    'stale',
    'pending_approval',
] as const;

export type MemoryFilter = (typeof MEMORY_FILTERS)[number];

export class MemoryManagerTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private filter: MemoryFilter = 'all';
    private items: MemoryItemResponse[] = [];
    private treeItems: vscode.TreeItem[] = [new vscode.TreeItem('Memory Manager not loaded yet.')];
    private _pollTimer: ReturnType<typeof setInterval> | undefined;
    private _reindexing = false;

    setClient(client: BackendClient | undefined): void {
        this.client = client;
    }

    setReindexing(flag: boolean): void {
        this._reindexing = flag;
        if (flag) {
            this.items = [];
            this.treeItems = this._reindexingTreeItems();
            this._onDidChangeTreeData.fire(undefined);
            this._startPolling();
        } else {
            this._stopPolling();
        }
    }

    private _reindexingTreeItems(): vscode.TreeItem[] {
        const spinner = new vscode.TreeItem('Re-indexing workspace...');
        spinner.iconPath = new vscode.ThemeIcon('sync~spin');
        spinner.description = 'Memory items will appear after summarization.';
        return [spinner];
    }

    setFilter(filter: MemoryFilter): void {
        this.filter = filter;
    }

    getFilter(): MemoryFilter {
        return this.filter;
    }

    getCurrentItems(): MemoryItemResponse[] {
        return this.items;
    }

    getPendingItems(): MemoryItemResponse[] {
        return this.items.filter((item) => this.isPending(item));
    }

    async refresh(): Promise<void> {
        if (!this.client) {
            this.items = [];
            this.treeItems = [new vscode.TreeItem('Backend not connected.')];
            this._onDidChangeTreeData.fire(undefined);
            return;
        }

        // While a full reindex is in flight, keep showing the reindexing spinner
        // rather than fetching stale data from the backend mid-operation.
        if (this._reindexing) {
            this.treeItems = this._reindexingTreeItems();
            this._onDidChangeTreeData.fire(undefined);
            return;
        }

        try {
            const [memResponse, statusResponse] = await Promise.all([
                this.client.listMemoryItems(this.filter),
                this.client.getIndexStatus().catch(() => null as IndexStatusResponse | null),
            ]);
            this.items = memResponse.items;
            this.treeItems = this.itemsToTreeItems(memResponse.items, statusResponse ?? undefined);

            if (statusResponse?.summarizing) {
                this._startPolling();
            } else {
                this._stopPolling();
            }
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : String(err);
            this.items = [];
            this.treeItems = [new vscode.TreeItem(`Memory load failed: ${message}`)];
            this._stopPolling();
        }

        this._onDidChangeTreeData.fire(undefined);
    }

    private _startPolling(): void {
        if (this._pollTimer) { return; }
        this._pollTimer = setInterval(() => { void this.refresh(); }, 10_000);
    }

    private _stopPolling(): void {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = undefined;
        }
    }

    dispose(): void {
        this._stopPolling();
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        return this.treeItems;
    }

    private itemsToTreeItems(items: MemoryItemResponse[], status?: IndexStatusResponse): vscode.TreeItem[] {
        const header = new vscode.TreeItem(`Filter: ${this.filter} (${items.length} items)`);
        header.description = 'Use "Review Memory" command to change filter and act on items.';

        if (items.length === 0) {
            if (status?.summarizing) {
                const pending = status.symbols_pending_summary ?? 0;
                const spinner = new vscode.TreeItem(`Summarizing symbols... (${pending} remaining)`);
                spinner.iconPath = new vscode.ThemeIcon('sync~spin');
                spinner.description = 'Memory items will appear when complete.';
                return [header, spinner];
            }
            if ((status?.symbols_pending_summary ?? 0) > 0) {
                const pending = status!.symbols_pending_summary!;
                const nudge = new vscode.TreeItem(`${pending} symbols not yet summarized`);
                nudge.iconPath = new vscode.ThemeIcon('warning');
                nudge.description = 'Run Summarization to continue.';
                return [header, nudge];
            }
            return [header, new vscode.TreeItem('No memory items found for current filter.')];
        }

        return [
            header,
            ...items.slice(0, 50).map((item) => {
                const trustEmoji = this.trustEmoji(item.trust_level);
                const staleLabel = item.stale ? 'stale' : 'fresh';
                const isPending = this.isPending(item);
                const pendingLabel = isPending ? 'pending' : 'active';
                const treeItem = new vscode.TreeItem(`${trustEmoji} ${item.title}`);
                treeItem.description = `${item.type} • trust ${item.trust_level} • ${pendingLabel} • ${staleLabel}`;
                treeItem.tooltip = `${item.body}\n\nid=${item.id}`;
                // contextValue drives inline approve/reject buttons via package.json menus
                treeItem.contextValue = isPending ? 'pending' : 'confirmed';
                // Store item id so command handlers can retrieve it
                (treeItem as vscode.TreeItem & { memopilotItemId?: string }).memopilotItemId = item.id;
                return treeItem;
            }),
        ];
    }

    private trustEmoji(trustLevel: number): string {
        if (trustLevel <= 2) { return '🟢'; }
        if (trustLevel === 3) { return '🟡'; }
        return '🟠';
    }

    private isPending(item: MemoryItemResponse): boolean {
        if (!item.tags || Array.isArray(item.tags)) {
            return false;
        }
        return item.tags.pending_approval === true;
    }
}
