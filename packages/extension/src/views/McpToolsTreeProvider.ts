import * as vscode from 'vscode';
import { BackendClient } from '../BackendClient';

interface McpServer {
    name: string;
    status: string;
    tools: string[];
}

export class McpToolsTreeProvider implements vscode.TreeDataProvider<vscode.TreeItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<vscode.TreeItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient | undefined;
    private servers: McpServer[] = [];
    private error: string | undefined;

    setClient(client: BackendClient | undefined): void {
        this.client = client;
    }

    async refresh(): Promise<void> {
        if (!this.client) {
            this.servers = [];
            this.error = 'Backend not connected';
            this._onDidChangeTreeData.fire(undefined);
            return;
        }

        try {
            const result = await this.client.listMcpTools();
            this.servers = result.servers;
            this.error = undefined;
        } catch (err: unknown) {
            // Endpoint may not exist yet — show graceful fallback
            this.error = undefined;
            this.servers = [];
        }
        this._onDidChangeTreeData.fire(undefined);
    }

    getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
        return element;
    }

    getChildren(element?: vscode.TreeItem): vscode.TreeItem[] {
        if (this.error) {
            return [new vscode.TreeItem(`$(error) ${this.error}`)];
        }

        if (!element) {
            // Top level: servers
            if (this.servers.length === 0) {
                return [new vscode.TreeItem('No MCP servers configured.')];
            }
            return this.servers.map(server => {
                const statusIcon = server.status === 'connected' ? '$(plug)' : '$(debug-disconnect)';
                const item = new vscode.TreeItem(
                    `${statusIcon} ${server.name}`,
                    vscode.TreeItemCollapsibleState.Expanded,
                );
                item.description = `${server.tools.length} tools`;
                item.contextValue = `mcp-server:${server.name}`;
                return item;
            });
        }

        // Children: tools for a server
        const serverName = element.contextValue?.replace('mcp-server:', '');
        const server = this.servers.find(s => s.name === serverName);
        if (!server) return [];

        return server.tools.map(tool => {
            const item = new vscode.TreeItem(`$(symbol-method) ${tool}`);
            item.tooltip = `MCP tool: ${tool} (from ${server.name})`;
            return item;
        });
    }
}
