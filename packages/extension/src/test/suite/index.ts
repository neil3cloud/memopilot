import * as path from 'path';

import Mocha from 'mocha';

export function run(): Promise<void> {
    const mocha = new Mocha({
        ui: 'tdd',
        color: true,
    });

    mocha.addFile(path.resolve(__dirname, './extension.test.js'));
    mocha.addFile(path.resolve(__dirname, './panel.test.js'));
    mocha.addFile(path.resolve(__dirname, './taskflow.test.js'));
    mocha.addFile(path.resolve(__dirname, './backendclient.test.js'));
    mocha.addFile(path.resolve(__dirname, './backendmanager.test.js'));

    return new Promise((resolve, reject) => {
        mocha.run((failures) => {
            if (failures > 0) {
                reject(new Error(`${failures} extension test(s) failed.`));
            } else {
                resolve();
            }
        });
    });
}
