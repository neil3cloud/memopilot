import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';

interface ContextFile {
    path: string;
    tokens: number;
}

interface ContextPackSummary {
    files: ContextFile[];
    rules_count: number;
    skills_count: number;
    total_tokens: number;
    estimated_cost_usd: number;
}

export class ContextPackTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private data: ContextPackSummary | undefined;
    private error: string | undefined;

    setClient(client: BackendClient | undefined): void {
        this.client = client;
    }

    /** Update with a context pack summary (called after task analysis builds a pack) */
    setContextPack(pack: ContextPackSummary): void {
        this.data = pack;
        this.error = undefined;
        this._onDidChangeTreeData.fire(undefined);
    }

    clear(): void {
        this.data = undefined;
        this.error = undefined;
        this._onDidChangeTreeData.fire(undefined);
    }

    refresh(): void {
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: vscode.TreeItem): vscode.TreeItem[] {
        if (this.error) {
            const item = new vscode.TreeItem(this.error);
            item.iconPath = new vscode.ThemeIcon('error');
            return [item];
        }

        if (!this.data) {
            return [new vscode.TreeItem('No active context pack. Enter a task to build one.')];
        }

        // Top-level categories
        if (!element) {
            const items: vscode.TreeItem[] = [];

            const filesItem = new vscode.TreeItem(
                `Files (${this.data.files.length})`,
                vscode.TreeItemCollapsibleState.Expanded,
            );
            filesItem.contextValue = 'context-files';
            filesItem.iconPath = new vscode.ThemeIcon('file-code');
            items.push(filesItem);

            const rulesItem = new vscode.TreeItem(`Rules: ${this.data.rules_count}`);
            rulesItem.iconPath = new vscode.ThemeIcon('law');
            items.push(rulesItem);

            const skillsItem = new vscode.TreeItem(`Skills: ${this.data.skills_count}`);
            skillsItem.iconPath = new vscode.ThemeIcon('tools');
            items.push(skillsItem);

            const sep = new vscode.TreeItem('─────────────────────');
            items.push(sep);

            const tokensItem = new vscode.TreeItem(`Tokens: ${this.data.total_tokens.toLocaleString()}`);
            tokensItem.iconPath = new vscode.ThemeIcon('symbol-number');
            items.push(tokensItem);

            const costItem = new vscode.TreeItem(`Est. Cost: $${this.data.estimated_cost_usd.toFixed(4)}`);
            costItem.iconPath = new vscode.ThemeIcon('credit-card');
            items.push(costItem);

            return items;
        }

        // Children of "Files" node
        if (element.contextValue === 'context-files') {
            return this.data.files.map(f => {
                const item = new vscode.TreeItem(f.path);
                item.description = `${f.tokens} tokens`;
                item.tooltip = `${f.path} — ${f.tokens} tokens`;
                item.iconPath = new vscode.ThemeIcon('file');
                return item;
            });
        }

        return [];
    }
}
