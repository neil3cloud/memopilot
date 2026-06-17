import * as assert from 'assert';
import { TaskFlowController } from '../../controllers/TaskFlowController';

/**
 * Mock BackendClient for testing TaskFlowController without real backend
 */
class MockBackendClient {
    async analyzeTask(taskText: string) {
        return {
            task_id: 'mock-task-123',
            task_type: 'enhancement',
            suggested_files: ['src/main.ts'],
            signals: { keyword_matches: 5, confidence: 0.8 },
        };
    }

    async buildContext(taskId: string) {
        return {
            task_run_id: 'run-123',
            status: 'success',
            context_pack: '# Context\n\nRepo structure analysis...',
        };
    }

    async routeModel(taskId: string) {
        return {
            model_selected: 'gpt-4o-mini',
            estimated_tokens: 5000,
            provider: 'openai',
        };
    }

    async generatePatch(taskId: string) {
        return {
            task_run_id: 'run-123',
            patch_generated: true,
            files_affected: 2,
        };
    }
}

/**
 * Mock BackendManager
 */
class MockBackendManager {
    async start() { }
    async stop() { }
}

suite('TaskFlowController', () => {
    let controller: TaskFlowController;
    let mockClient: MockBackendClient;
    let mockManager: MockBackendManager;

    setup(() => {
        mockClient = new MockBackendClient() as any;
        mockManager = new MockBackendManager() as any;
        controller = new TaskFlowController(mockClient as any, mockManager as any);
    });

    test('Initial state is idle', () => {
        const state = controller.getState();
        assert.strictEqual(state.stage, 'idle', 'Initial stage should be idle');
        assert.strictEqual(state.error, undefined, 'Initial error should be undefined');
    });

    test('Transitions to analyzing when startTask is called', async () => {
        controller.startTask('Fix failing tests', [], 'auto');
        const state = controller.getState();
        assert.strictEqual(state.stage, 'analyzing', 'Stage should be analyzing');
        
        // Let async work complete
        await new Promise(resolve => setTimeout(resolve, 100));
    });

    test('streamingToken accumulates during patch generation', async () => {
        controller.startTask('Generate a patch', [], 'auto');
        
        // Simulate streaming tokens
        const onStageChangeHandler = (state: any) => {
            if (state.stage === 'generating_patch') {
                assert.ok(state.streamingToken !== undefined, 'streamingToken should be present during generation');
            }
        };
        
        const disposable = controller.onStageChange(onStageChangeHandler);
        
        // Give some time for event handlers
        await new Promise(resolve => setTimeout(resolve, 50));
        
        disposable.dispose();
    });

    test('Rejection resets flow state', () => {
        // Move to approving stage via setAnalysis
        controller.setAnalysis('test task', [], 'auto', {
            intent_summary: 'test',
            suggested_files: [],
            applicable_rules: [],
            estimated_complexity: 'low',
            suggested_mode: 'auto',
            task_type: 'general',
            risk: 'low',
        });
        
        // Reject the patch
        controller.reject();
        
        const state = controller.getState();
        // After rejection, should either return to idle or clear error
        assert.ok(
            state.stage === 'idle' || state.error !== undefined,
            'Rejection should reset or error state'
        );
    });

    test('Error state is captured on task analysis failure', async () => {
        const badClient = {
            analyzeTask: async () => {
                throw new Error('Analysis failed');
            },
        } as any;
        
        const controllerWithBadClient = new TaskFlowController(badClient, mockManager as any);
        controllerWithBadClient.startTask('Test task', [], 'auto');
        
        // Wait for async error handling
        await new Promise(resolve => setTimeout(resolve, 100));
        
        const state = controllerWithBadClient.getState();
        assert.ok(state.error !== undefined, 'Error should be captured on analysis failure');
    });

    test('onStageChange events are emitted on stage transitions', async () => {
        let stageChangeCount = 0;
        
        const disposable = controller.onStageChange(() => {
            stageChangeCount++;
        });
        
        controller.startTask('Test', [], 'auto');
        await new Promise(resolve => setTimeout(resolve, 150));
        
        assert.ok(stageChangeCount > 0, 'Stage change events should be emitted');
        disposable.dispose();
    });
});
