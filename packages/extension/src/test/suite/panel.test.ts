import * as assert from 'assert';
import { NAVIGATION_ITEMS } from '../../panels/navigationItems';
import type { NavigationItemDTO, WorkspaceStatusDTO, WebviewOutboundMessage } from '../../panels/types';

suite('MemoPilot Panel Shell', () => {
    test('Navigation items are well-formed', () => {
        assert.ok(NAVIGATION_ITEMS.length >= 17, 'Expected at least 17 navigation items');

        for (const item of NAVIGATION_ITEMS) {
            assert.ok(item.id, `Navigation item missing id`);
            assert.ok(item.label, `Navigation item ${item.id} missing label`);
            assert.ok(item.icon, `Navigation item ${item.id} missing icon`);
            assert.ok(item.description, `Navigation item ${item.id} missing description`);
            assert.strictEqual(typeof item.enabled, 'boolean', `${item.id}.enabled must be boolean`);
        }
    });

    test('Navigation item IDs are unique', () => {
        const ids = NAVIGATION_ITEMS.map(item => item.id);
        const unique = new Set(ids);
        assert.strictEqual(ids.length, unique.size, 'Navigation item IDs must be unique');
    });

    test('Workspace status view is enabled and first', () => {
        const first = NAVIGATION_ITEMS[0];
        assert.strictEqual(first.id, 'workspace-status');
        assert.strictEqual(first.enabled, true);
    });

    test('Required views are present', () => {
        const requiredIds = [
            'workspace-status',
            'local-memory',
            'rules-skills',
            'task-entry',
            'context-pack',
            'model-routing',
            'patch-preview',
            'approval-gate',
            'validation',
            'task-history',
            'cost-dashboard',
            'memory-manager',
            'workspace-profile',
            'privacy-boundary',
            'provider-matrix',
            'evidence-board',
            'mcp-tools',
        ];

        for (const id of requiredIds) {
            const found = NAVIGATION_ITEMS.find(item => item.id === id);
            assert.ok(found, `Expected navigation item with id "${id}"`);
        }
    });

    test('WebviewOutboundMessage types are valid', () => {
        // Type check — ensures the union type compiles correctly
        const messages: WebviewOutboundMessage[] = [
            { type: 'navigate', payload: { viewId: 'workspace-status' } },
            { type: 'request-workspace-status' },
            { type: 'restart-backend' },
            { type: 'ready' },
        ];

        assert.strictEqual(messages.length, 4);
    });

    test('WorkspaceStatusDTO has correct shape', () => {
        const dto: WorkspaceStatusDTO = {
            connected: true,
            apiVersion: 2,
            schemaVersion: 5,
            workspaceName: 'test-project',
            workspaceRoot: '/path/to/project',
            indexed: true,
            indexingPhase: 'complete',
            filesScanned: 100,
            totalFiles: 100,
            symbolsExtracted: 500,
        };

        assert.strictEqual(dto.connected, true);
        assert.strictEqual(dto.apiVersion, 2);
        assert.strictEqual(dto.indexingPhase, 'complete');
    });

    test('NavigationItemDTO optional badge field', () => {
        const withBadge: NavigationItemDTO = {
            id: 'test',
            label: 'Test',
            icon: '$(test)',
            enabled: true,
            badge: '3',
            description: 'Test item',
        };
        const withoutBadge: NavigationItemDTO = {
            id: 'test2',
            label: 'Test 2',
            icon: '$(test)',
            enabled: false,
            description: 'Test item 2',
        };

        assert.strictEqual(withBadge.badge, '3');
        assert.strictEqual(withoutBadge.badge, undefined);
    });
});
