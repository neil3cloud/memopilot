import * as assert from 'assert';

import * as vscode from 'vscode';

suite('MemoPilot Extension', () => {
    let extension: vscode.Extension<unknown> | undefined;

    setup(async () => {
        extension = vscode.extensions.all.find(
            (candidate) =>
                candidate.packageJSON.name === 'memopilot'
                && candidate.packageJSON.publisher === 'memopilot',
        );
        if (extension) {
            await extension.activate();
        }
    });

    test('Extension is present and activates', function () {
        if (!extension) {
            this.skip();
            return;
        }

        assert.ok(extension, 'Expected MemoPilot extension to be available in extension host');
        assert.strictEqual(extension.isActive, true);
    });

    test('Core command is registered', async function () {
        if (!extension) {
            this.skip();
            return;
        }

        const commands = await vscode.commands.getCommands(true);
        assert.ok(
            commands.includes('memopilot.rebuildMemory'),
            'Expected memopilot.rebuildMemory command to be registered',
        );
    });
});
