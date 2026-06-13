import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';

interface BudgetData {
    monthly_budget_usd: number;
    spent_usd: number;
    saved_usd: number;
    remaining_usd: number;
}

export class CostGuardTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private data: BudgetData | undefined;
    private error: string | undefined;

    setClient(client: BackendClient | undefined): void {
        this.client = client;
    }

    async refresh(): Promise<void> {
        if (!this.client) {
            this.data = undefined;
            this.error = 'Backend not connected';
            this._onDidChangeTreeData.fire(undefined);
            return;
        }

        try {
            const result = await this.client.getBudgetStatus();
            this.data = result;
            this.error = undefined;
        } catch (err: unknown) {
            this.error = err instanceof Error ? err.message : String(err);
            this.data = undefined;
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

        if (!this.data) {
            return [new vscode.TreeItem('Cost Guard will appear after backend connects.')];
        }

        const { monthly_budget_usd, spent_usd, saved_usd, remaining_usd } = this.data;
        const pct = monthly_budget_usd > 0 ? Math.round((spent_usd / monthly_budget_usd) * 100) : 0;
        const barLen = 20;
        const filled = Math.round((pct / 100) * barLen);
        const bar = '█'.repeat(filled) + '░'.repeat(barLen - filled);

        const statusIcon = pct >= 90 ? 'error' : pct >= 70 ? 'warning' : 'check';

        const items: vscode.TreeItem[] = [];

        const budgetItem = new vscode.TreeItem(`Budget: ${pct}% used`);
        budgetItem.description = `[${bar}]`;
        budgetItem.tooltip = `$${spent_usd.toFixed(2)} / $${monthly_budget_usd.toFixed(2)}`;
        budgetItem.iconPath = new vscode.ThemeIcon(statusIcon);
        items.push(budgetItem);

        const spentItem = new vscode.TreeItem(`Spent: $${spent_usd.toFixed(2)}`);
        spentItem.description = `of $${monthly_budget_usd.toFixed(2)}`;
        spentItem.iconPath = new vscode.ThemeIcon('credit-card');
        items.push(spentItem);

        const remainItem = new vscode.TreeItem(`Remaining: $${remaining_usd.toFixed(2)}`);
        remainItem.iconPath = new vscode.ThemeIcon('arrow-right');
        items.push(remainItem);

        const savedItem = new vscode.TreeItem(`Saved (vs Frontier): $${saved_usd.toFixed(2)}`);
        savedItem.tooltip = 'Amount saved by using local/cheaper models instead of frontier models.';
        savedItem.iconPath = new vscode.ThemeIcon('sparkle');
        items.push(savedItem);

        return items;
    }
}
