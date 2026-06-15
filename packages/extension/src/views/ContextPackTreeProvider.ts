import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';

interface ContextFile {
    path: string;
    tokens: number;
}

interface ContextQualityScore {
    total: number;
    verdict: 'good' | 'acceptable' | 'poor' | 'rebuild';
    missing_signals: string[];
    dedup_savings_pct: number;
    graph_expansion_files: number;
}

interface ContextPackSummary {
    files: ContextFile[];
    rules_count: number;
    skills_count: number;
    total_tokens: number;
    estimated_cost_usd: number;
    quality_score?: ContextQualityScore;
    callers_not_in_context?: string[];
}

const VERDICT_ICON: Record<string, string> = {
    good: 'pass',
    acceptable: 'warning',
    poor: 'error',
    rebuild: 'error',
};

const VERDICT_LABEL: Record<string, string> = {
    good: '✅ Good',
    acceptable: '⚠️ Acceptable',
    poor: '🔴 Poor',
    rebuild: '🔴 Rebuild needed',
};

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

            // ── Quality indicator ────────────────────────────────────────────
            if (this.data.quality_score) {
                const q = this.data.quality_score;
                const scorePct = Math.round(q.total * 100);
                const label = VERDICT_LABEL[q.verdict] ?? q.verdict;
                const qualityItem = new vscode.TreeItem(
                    `Quality: ${label} (${scorePct}/100)`,
                    q.missing_signals.length > 0
                        ? vscode.TreeItemCollapsibleState.Collapsed
                        : vscode.TreeItemCollapsibleState.None,
                );
                qualityItem.contextValue = 'context-quality';
                qualityItem.iconPath = new vscode.ThemeIcon(VERDICT_ICON[q.verdict] ?? 'info');
                qualityItem.tooltip = q.missing_signals.length > 0
                    ? `Missing signals:\n${q.missing_signals.join('\n')}`
                    : 'All context quality signals are present';
                items.push(qualityItem);
            }

            const filesItem = new vscode.TreeItem(
                `Files (${this.data.files.length})`,
                vscode.TreeItemCollapsibleState.Expanded,
            );
            filesItem.contextValue = 'context-files';
            filesItem.iconPath = new vscode.ThemeIcon('file-code');
            items.push(filesItem);

            if (this.data.callers_not_in_context && this.data.callers_not_in_context.length > 0) {
                const callersItem = new vscode.TreeItem(
                    `Callers not in context (${this.data.callers_not_in_context.length})`,
                    vscode.TreeItemCollapsibleState.Collapsed,
                );
                callersItem.contextValue = 'context-missing-callers';
                callersItem.iconPath = new vscode.ThemeIcon('references');
                callersItem.tooltip = 'These files call functions in your context but are not included';
                items.push(callersItem);
            }

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

        // Children of "Quality" node — missing signals
        if (element.contextValue === 'context-quality' && this.data.quality_score) {
            return this.data.quality_score.missing_signals.map(signal => {
                const item = new vscode.TreeItem(signal);
                item.iconPath = new vscode.ThemeIcon('circle-slash');
                return item;
            });
        }

        // Children of "Callers not in context" node
        if (element.contextValue === 'context-missing-callers' && this.data.callers_not_in_context) {
            return this.data.callers_not_in_context.map(fp => {
                const item = new vscode.TreeItem(fp);
                item.iconPath = new vscode.ThemeIcon('file');
                item.tooltip = `${fp} calls functions in your context but is not included`;
                return item;
            });
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
