import * as vscode from 'vscode';

export class PlaceholderTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private message: string;

    constructor(message: string) {
        this.message = message;
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        const item = new vscode.TreeItem(this.message);
        item.contextValue = 'placeholder';
        return [item];
    }
}
