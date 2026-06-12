import * as vscode from 'vscode';

import { BackendClient, MemoryItemResponse } from '../BackendClient';

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

    setClient(client: BackendClient | undefined): void {
        this.client = client;
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

    async refresh(): Promise<void> {
        if (!this.client) {
            this.items = [];
            this.treeItems = [new vscode.TreeItem('Backend not connected.')];
            this._onDidChangeTreeData.fire(undefined);
            return;
        }

        try {
            const response = await this.client.listMemoryItems(this.filter);
            this.items = response.items;
            this.treeItems = this.itemsToTreeItems(response.items);
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : String(err);
            this.items = [];
            this.treeItems = [new vscode.TreeItem(`Memory load failed: ${message}`)];
        }

        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        return this.treeItems;
    }

    private itemsToTreeItems(items: MemoryItemResponse[]): vscode.TreeItem[] {
        const header = new vscode.TreeItem(`Filter: ${this.filter} (${items.length} items)`);
        header.description = 'Use "Review Memory" command to change filter and act on items.';

        if (items.length === 0) {
            return [header, new vscode.TreeItem('No memory items found for current filter.')];
        }

        return [
            header,
            ...items.slice(0, 50).map((item) => {
                const trustEmoji = this.trustEmoji(item.trust_level);
                const staleLabel = item.stale ? 'stale' : 'fresh';
                const pending = this.isPending(item) ? 'pending' : 'active';
                const treeItem = new vscode.TreeItem(`${trustEmoji} ${item.title}`);
                treeItem.description = `${item.type} • trust ${item.trust_level} • ${pending} • ${staleLabel}`;
                treeItem.tooltip = `${item.body}\n\nid=${item.id}`;
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
