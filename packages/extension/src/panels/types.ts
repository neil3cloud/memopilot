/**
 * Shared types for MemoPilot webview panels and message bridge.
 */

// --- Message Protocol ---

/** Messages sent FROM webview TO extension */
export type WebviewOutboundMessage =
    | { type: 'navigate'; payload: { viewId: string } }
    | { type: 'request-workspace-status' }
    | { type: 'request-rules' }
    | { type: 'request-context-pack' }
    | { type: 'request-cost-status' }
    | { type: 'restart-backend' }
    | { type: 'cancel-indexing' }
    | { type: 'enter-api-key' }
    | { type: 'ready' };

/** Messages sent FROM extension TO webview */
export type WebviewInboundMessage =
    | { type: 'workspace-status'; payload: WorkspaceStatusDTO }
    | { type: 'navigation-items'; payload: NavigationItemDTO[] }
    | { type: 'active-view'; payload: { viewId: string } }
    | { type: 'view-content'; payload: { viewId: string; html: string } }
    | { type: 'streaming-token'; payload: { content: string } }
    | { type: 'error'; payload: { message: string } };

export type WebviewMessage = WebviewOutboundMessage | WebviewInboundMessage;

// --- Data Transfer Objects ---

export interface WorkspaceStatusDTO {
    connected: boolean;
    apiVersion: number | null;
    schemaVersion: number | null;
    workspaceName: string;
    workspaceRoot: string;
    indexed: boolean;
    indexingPhase: 'idle' | 'scanning' | 'extracting' | 'complete';
    filesScanned: number;
    totalFiles: number;
    symbolsExtracted: number;
    needsSetup?: boolean;
}

export interface NavigationItemDTO {
    id: string;
    label: string;
    icon: string;
    enabled: boolean;
    badge?: string;
    description: string;
}

// --- Async State ---

export type AsyncState<T> =
    | { status: 'idle' }
    | { status: 'loading' }
    | { status: 'success'; data: T; fetchedAt: number }
    | { status: 'error'; error: string; lastData?: T }
    | { status: 'stale'; data: T; fetchedAt: number };
