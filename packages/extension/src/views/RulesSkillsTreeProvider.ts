import * as vscode from 'vscode';
import { BackendClient, ActiveRuleItem, ActiveSkillItem, ActiveRulesResponse } from '../BackendClient';

type RulesTreeElement = RulesCategoryItem | RuleTreeItem | SkillTreeItem;

class RulesCategoryItem extends vscode.TreeItem {
    constructor(
        public readonly category: 'global' | 'project' | 'skills',
        label: string,
        count: number,
    ) {
        super(`${label} (${count})`, vscode.TreeItemCollapsibleState.Expanded);
        this.contextValue = `rules-category-${category}`;
        const iconMap = { global: 'globe', project: 'folder', skills: 'tools' };
        this.iconPath = new vscode.ThemeIcon(iconMap[category]);
    }
}

class RuleTreeItem extends vscode.TreeItem {
    constructor(public readonly rule: ActiveRuleItem) {
        super(rule.text, vscode.TreeItemCollapsibleState.None);
        this.contextValue = 'rule-item';
        this.description = rule.source_file;
        this.tooltip = `${rule.text}\n\nSource: ${rule.source_file}\nEnabled: ${rule.enabled}`;
        this.iconPath = new vscode.ThemeIcon(
            rule.enabled ? 'pass' : 'circle-slash',
            rule.enabled ? undefined : new vscode.ThemeColor('disabledForeground'),
        );

        // Click to open source file if it's a real path (not policy-pack:)
        if (!rule.source_file.startsWith('policy-pack:')) {
            this.command = {
                command: 'vscode.open',
                title: 'Open Rule Source',
                arguments: [vscode.Uri.file(rule.source_file)],
            };
        }
    }
}

class SkillTreeItem extends vscode.TreeItem {
    constructor(public readonly skill: ActiveSkillItem) {
        super(skill.name, vscode.TreeItemCollapsibleState.None);
        this.contextValue = 'skill-item';
        this.description = skill.framework ?? '';
        this.tooltip = `Skill: ${skill.name}${skill.framework ? ` (${skill.framework})` : ''}\nEnabled: ${skill.enabled}`;
        this.iconPath = new vscode.ThemeIcon(
            skill.enabled ? 'symbol-method' : 'circle-slash',
            skill.enabled ? undefined : new vscode.ThemeColor('disabledForeground'),
        );
    }
}

export class RulesSkillsTreeProvider implements vscode.TreeDataProvider<RulesTreeElement> {
    private _onDidChangeTreeData = new vscode.EventEmitter<RulesTreeElement | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private data: ActiveRulesResponse | undefined;
    private loading = false;
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

        this.loading = true;
        this._onDidChangeTreeData.fire(undefined);

        try {
            this.data = await this.client.getActiveRules();
            this.error = undefined;
        } catch (err: unknown) {
            this.error = err instanceof Error ? err.message : String(err);
            this.data = undefined;
        } finally {
            this.loading = false;
            this._onDidChangeTreeData.fire(undefined);
        }
    }

    getTreeItem(element: RulesTreeElement): vscode.TreeItem {
        return element;
    }

    getChildren(element?: RulesTreeElement): RulesTreeElement[] {
        if (this.loading) {
            const item = new vscode.TreeItem('Loading rules...');
            item.iconPath = new vscode.ThemeIcon('sync~spin');
            return [item as unknown as RulesTreeElement];
        }

        if (this.error) {
            const item = new vscode.TreeItem(this.error);
            item.iconPath = new vscode.ThemeIcon('error');
            return [item as unknown as RulesTreeElement];
        }

        if (!this.data) {
            const item = new vscode.TreeItem('Rules & Skills will appear after indexing.');
            item.iconPath = new vscode.ThemeIcon('info');
            return [item as unknown as RulesTreeElement];
        }

        // Top level: categories
        if (!element) {
            return [
                new RulesCategoryItem('global', 'Global Rules', this.data.global_rules.length),
                new RulesCategoryItem('project', 'Project Rules', this.data.project_rules.length),
                new RulesCategoryItem('skills', 'Detected Skills', this.data.detected_skills.length),
            ];
        }

        // Children of categories
        if (element instanceof RulesCategoryItem) {
            switch (element.category) {
                case 'global':
                    return this.data.global_rules.map(r => new RuleTreeItem(r));
                case 'project':
                    return this.data.project_rules.map(r => new RuleTreeItem(r));
                case 'skills':
                    return this.data.detected_skills.map(s => new SkillTreeItem(s));
            }
        }

        return [];
    }
}
