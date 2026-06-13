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
            return [new vscode.TreeItem(`$(error) ${this.error}`)];
        }

        if (!this.data) {
            return [new vscode.TreeItem('No active context pack. Enter a task to build one.')];
        }

        // Top-level categories
        if (!element) {
            const items: vscode.TreeItem[] = [];

            const filesItem = new vscode.TreeItem(
                `$(file-code) Files (${this.data.files.length})`,
                vscode.TreeItemCollapsibleState.Expanded,
            );
            filesItem.contextValue = 'context-files';
            items.push(filesItem);

            const rulesItem = new vscode.TreeItem(`$(law) Rules: ${this.data.rules_count}`);
            items.push(rulesItem);

            const skillsItem = new vscode.TreeItem(`$(tools) Skills: ${this.data.skills_count}`);
            items.push(skillsItem);

            const sep = new vscode.TreeItem('─────────────────────');
            items.push(sep);

            const tokensItem = new vscode.TreeItem(`$(symbol-number) Tokens: ${this.data.total_tokens.toLocaleString()}`);
            items.push(tokensItem);

            const costItem = new vscode.TreeItem(`$(credit-card) Est. Cost: $${this.data.estimated_cost_usd.toFixed(4)}`);
            items.push(costItem);

            return items;
        }

        // Children of "Files" node
        if (element.contextValue === 'context-files') {
            return this.data.files.map(f => {
                const item = new vscode.TreeItem(`$(file) ${f.path}`);
                item.description = `${f.tokens} tokens`;
                item.tooltip = `${f.path} — ${f.tokens} tokens`;
                return item;
            });
        }

        return [];
    }
}
