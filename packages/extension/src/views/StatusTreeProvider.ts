import * as vscode from 'vscode';

type BackendStatus = 'connecting' | 'connected' | 'error' | 'no-workspace';

export class StatusTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private status: BackendStatus = 'connecting';
    private message = 'Starting backend...';

    setStatus(status: BackendStatus, message: string): void {
        this.status = status;
        this.message = message;
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

        const item = new vscode.TreeItem(this.message);
        item.contextValue = this.status;
        item.iconPath = new vscode.ThemeIcon(iconMap[this.status]);
        return [item];
    }
}
