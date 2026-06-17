import * as vscode from 'vscode';
import { IndexStatusResponse } from '../BackendClient';

type BackendStatus = 'connecting' | 'connected' | 'error' | 'no-workspace';

export class StatusTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private status: BackendStatus = 'connecting';
    private message = 'Starting backend...';
    private indexStatus: IndexStatusResponse | undefined;

    setStatus(status: BackendStatus, message: string): void {
        this.status = status;
        this.message = message;
        if (status !== 'connected') {
            this.indexStatus = undefined;
        }
        this._onDidChangeTreeData.fire(undefined);
    }

    updateIndexStatus(status: IndexStatusResponse | undefined): void {
        this.indexStatus = status;
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        const iconMap: Record<BackendStatus, string> = {
            connecting: 'sync~spin',
            connected: 'pass',
            error: 'error',
            'no-workspace': 'info',
        };

        const backendItem = new vscode.TreeItem(this.message);
        backendItem.contextValue = this.status;
        backendItem.iconPath = new vscode.ThemeIcon(iconMap[this.status]);

        if (this.status !== 'connected') {
            return [backendItem];
        }

        const indexItem = new vscode.TreeItem(this.buildIndexLabel());
        indexItem.contextValue = this.indexStatus?.never_indexed ? 'index-not-indexed' : 'index-ready';
        indexItem.iconPath = new vscode.ThemeIcon(this.indexStatus?.never_indexed ? 'warning' : 'zap');
        return [backendItem, indexItem];
    }

    private buildIndexLabel(): string {
        if (!this.indexStatus) {
            return 'Index: Checking status...';
        }
        if (this.indexStatus.never_indexed) {
            return 'Index: Not indexed - run MemoPilot: Index Workspace';
        }

        const staleSuffix = this.indexStatus.stale_files > 0
            ? ` (${this.indexStatus.stale_files} stale)`
            : '';
        const lastRun = this.formatLastRun(this.indexStatus.last_indexed_at);
        return `Index: ${this.indexStatus.indexed_files} files, ${this.indexStatus.symbols_count} symbols${staleSuffix} (${lastRun})`;
    }

    private formatLastRun(lastIndexedAt: string | null): string {
        if (!lastIndexedAt) {
            return 'last run unknown';
        }

        const parsed = new Date(lastIndexedAt.replace(' ', 'T') + 'Z');
        if (Number.isNaN(parsed.getTime())) {
            return `last run ${lastIndexedAt}`;
        }

        const deltaMs = Date.now() - parsed.getTime();
        const deltaMinutes = Math.floor(deltaMs / 60000);
        if (deltaMinutes < 1) {
            return 'just now';
        }
        if (deltaMinutes < 60) {
            return `${deltaMinutes} min ago`;
        }
        const deltaHours = Math.floor(deltaMinutes / 60);
        if (deltaHours < 24) {
            return `${deltaHours} hr ago`;
        }
        const deltaDays = Math.floor(deltaHours / 24);
        return `${deltaDays} day${deltaDays === 1 ? '' : 's'} ago`;
    }
}
