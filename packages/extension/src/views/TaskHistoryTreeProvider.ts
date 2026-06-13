import * as vscode from 'vscode';
import { BackendClient, TaskHistoryEntry } from '../BackendClient';

export class TaskHistoryTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private entries: TaskHistoryEntry[] = [];
    private error: string | undefined;

    setClient(client: BackendClient | undefined): void {
        this.client = client;
    }

    async refresh(): Promise<void> {
        if (!this.client) {
            this.entries = [];
            this.error = 'Backend not connected';
            this._onDidChangeTreeData.fire(undefined);
            return;
        }

        try {
            const result = await this.client.getTaskHistory(15);
            this.entries = result.entries;
            this.error = undefined;
        } catch (err: unknown) {
            this.error = err instanceof Error ? err.message : String(err);
            this.entries = [];
        }
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        if (this.error) {
            const item = new vscode.TreeItem(this.error);
            item.iconPath = new vscode.ThemeIcon('error');
            return [item];
        }

        if (this.entries.length === 0) {
            return [new vscode.TreeItem('No task history yet. Complete a task to see history.')];
        }

        return this.entries.map(entry => {
            const statusIcon = entry.status === 'completed' ? 'check'
                : entry.status === 'rejected' ? 'close' : 'error';

            const item = new vscode.TreeItem(entry.description);

            const time = this.formatRelativeTime(entry.created_at);
            const costStr = entry.cost_usd > 0 ? ` · $${entry.cost_usd.toFixed(4)}` : ' · free';
            item.description = `${time} · ${entry.files_changed} files${costStr}`;
            item.iconPath = new vscode.ThemeIcon(statusIcon);

            const model = entry.model_used || 'unknown';
            const duration = entry.duration_ms > 0 ? `${(entry.duration_ms / 1000).toFixed(1)}s` : 'N/A';
            item.tooltip = `Mode: ${entry.mode}\nModel: ${model}\nDuration: ${duration}\nStatus: ${entry.status}`;

            return item;
        });
    }

    private formatRelativeTime(isoString: string): string {
        try {
            const date = new Date(isoString);
            const now = new Date();
            const diffMs = now.getTime() - date.getTime();
            const diffMin = Math.floor(diffMs / 60000);

            if (diffMin < 1) return 'just now';
            if (diffMin < 60) return `${diffMin}m ago`;
            const diffHrs = Math.floor(diffMin / 60);
            if (diffHrs < 24) return `${diffHrs}h ago`;
            const diffDays = Math.floor(diffHrs / 24);
            return `${diffDays}d ago`;
        } catch {
            return isoString;
        }
    }
}
