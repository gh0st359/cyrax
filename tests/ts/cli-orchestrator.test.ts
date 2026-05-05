import { describe, expect, it } from 'vitest';
import { CyraxOrchestrator } from '../../src/orchestrator.js';
import { defaultConfig } from '../../src/config/defaults.js';

describe('orchestrator slash commands', () => {
  it('updates model, mode, and conversation state', () => {
    const config = defaultConfig();
    config.model.api_key = 'test';
    const orchestrator = new CyraxOrchestrator(config);
    expect(orchestrator.handleCommand('/model new-model')).toBe('');
    expect(orchestrator.model.modelName).toBe('new-model');
    expect(orchestrator.handleCommand('/mode auto')).toBe('');
    expect(orchestrator.currentModeLabel()).toBe('auto');
    orchestrator.conversation.addMessage('user', 'hello');
    orchestrator.handleCommand('/clear');
    expect(orchestrator.conversation.messages).toHaveLength(0);
  });
});
