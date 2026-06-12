import * as vscode from 'vscode';

import { BackendClient } from '../BackendClient';

export class PrivacyDashboardTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private items: vscode.TreeItem[] = [new vscode.TreeItem('Privacy dashboard not loaded yet.')];

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
            const dashboard = await this.client.getPrivacyDashboard();
            const local = new vscode.TreeItem(`Local only: ${dashboard.local_only.length} categories`);
            local.tooltip = dashboard.local_only.join(', ');

            const mayLeave = new vscode.TreeItem(`May leave machine: ${dashboard.may_leave_machine.length} categories`);
            mayLeave.tooltip = dashboard.may_leave_machine.join(', ');

            const neverSent = new vscode.TreeItem(`Never sent: ${dashboard.never_sent.length} categories`);
            neverSent.tooltip = dashboard.never_sent.join(', ');

            const preCall = new vscode.TreeItem(`Pre-call: ${dashboard.pre_call_approval_summary}`);
            const mcpStatus = new vscode.TreeItem(`MCP data status: ${dashboard.mcp_data_status}`);

            const recentCalls = dashboard.recent_cloud_calls.slice(0, 5).map((call) => {
                const item = new vscode.TreeItem(`Cloud: ${call.provider}/${call.model}`);
                item.description = `${call.input_tokens} in • ${call.output_tokens} out • $${call.estimated_cost.toFixed(4)}`;
                item.tooltip = `cache_hit=${call.cache_hit}; redacted=${call.redacted_values}`;
                return item;
            });

            this.items = [local, mayLeave, neverSent, preCall, mcpStatus, ...recentCalls];
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : String(err);
            this.items = [new vscode.TreeItem(`Privacy dashboard failed: ${message}`)];
        }

        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(): vscode.TreeItem[] {
        return this.items;
    }
}
