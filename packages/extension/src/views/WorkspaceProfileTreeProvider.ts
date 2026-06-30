import * as vscode from 'vscode';

import { BackendClient } from '../BackendClient';

export class WorkspaceProfileTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private items: vscode.TreeItem[] = [new vscode.TreeItem('Workspace profile not loaded yet.')];
    private _detectedLanguages: string[] = [];

    setDetectedLanguages(languages: string[]): void {
        this._detectedLanguages = languages;
        this._onDidChangeTreeData.fire(undefined);
    }

    setClient(client: BackendClient | undefined): void {
        this.client = client;
    }

    async refresh(): Promise<void> {
        if (!this.client) {
            this.items = [new vscode.TreeItem('Backend not connected.')];
            this._onDidChangeTreeData.fire(undefined);
            return;
        }

        try {
            const response = await this.client.getWorkspaceProfile();
            this.items = this.profileToItems(response.profile_yaml);
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : String(err);
            this.items = [new vscode.TreeItem(`Profile load failed: ${message}`)];
        }
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        return this.items;
    }

    private profileToItems(profileYaml: string): vscode.TreeItem[] {
        const pick = (field: string): string => {
            const regex = new RegExp(`^\\s*${field}:\\s*(.+)$`, 'm');
            const match = profileYaml.match(regex);
            return match?.[1]?.trim() ?? 'unknown';
        };

        const nameItem = new vscode.TreeItem(`Workspace: ${pick('name')}`);
        const langDisplay = this._detectedLanguages.length > 0
            ? this._detectedLanguages.join(', ')
            : pick('primary_language');
        const languageItem = new vscode.TreeItem(`Language: ${langDisplay}`);
        const budgetItem = new vscode.TreeItem(`Budget profile: ${pick('budget_profile')}`);
        const privacyItem = new vscode.TreeItem(`Redact secrets: ${pick('redact_secrets')}`);
        const modelItem = new vscode.TreeItem(`Frontier requires approval: ${pick('frontier_requires_approval')}`);
        return [nameItem, languageItem, budgetItem, privacyItem, modelItem];
    }
}
