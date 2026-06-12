import * as vscode from 'vscode';
import { BackendClient, EvidenceBoardItemResponse } from '../BackendClient';

export class EvidenceBoardTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private readonly _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined | null | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private items: EvidenceBoardItemResponse[] = [];

    setClient(client: BackendClient | undefined): void {
        this.client = client;
        this.refresh();
    }

    async refresh(): Promise<void> {
        if (!this.client) {
            this.items = [];
            this._onDidChangeTreeData.fire();
            return;
        }
        try {
            const payload = await this.client.getEvidenceBoard();
            this.items = payload.items;
        } catch {
            this.items = [];
        }
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    async getChildren(): Promise<vscode.TreeItem[]> {
        if (!this.client) {
            return [new vscode.TreeItem('Backend disconnected')];
        }
        if (this.items.length === 0) {
            return [new vscode.TreeItem('No evidence attached yet')];
        }
        return this.items.map((item) => {
            const treeItem = new vscode.TreeItem(
                `${item.source_type} (trust ${item.trust_level})`,
                vscode.TreeItemCollapsibleState.None,
            );
            treeItem.description =
                `${item.extraction_status} • ${item.source_path ?? item.source_url ?? 'n/a'}`;
            treeItem.tooltip = item.findings.join('\n') || 'No findings';
            return treeItem;
        });
    }
}
