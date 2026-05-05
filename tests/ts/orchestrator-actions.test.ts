import { mkdtempSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { CyraxOrchestrator } from '../../src/orchestrator.js';
import { defaultConfig } from '../../src/config/defaults.js';
import { findToolIntentActions } from '../../src/utils/actions.js';

function testOrchestrator(): CyraxOrchestrator {
  const config = defaultConfig();
  config.model.api_key = 'test';
  config.display.streaming = false;
  config.tools.work_dir = mkdtempSync(path.join(os.tmpdir(), 'cyrax-actions-'));
  config.memory.db_path = path.join(config.tools.work_dir, 'cyrax.db');
  return new CyraxOrchestrator(config, { auto: true });
}

describe('agentic action execution', () => {
  it('recovers plain-language bash tool intent into executable actions', () => {
    const actions = findToolIntentActions('invoke tool bash with command is ls -la /tmp/cyrax');
    expect(actions).toHaveLength(1);
    expect(actions[0]?.kind).toBe('execute');
    expect(actions[0]?.groups[0]).toBe('ls -la /tmp/cyrax');
  });

  it('executes recovered tool intent instead of reporting zero actions', async () => {
    const orchestrator = testOrchestrator();
    const executeActions = orchestrator['executeActions'].bind(orchestrator) as (response: string) => Promise<string[]>;
    const results = await executeActions('invoke tool bash with command is printf recovered');
    expect(results.join('\n')).toContain('recovered');
  });

  it('sets local filesystem paths as active targets', () => {
    const orchestrator = testOrchestrator();
    const extractTarget = orchestrator['tryExtractTarget'].bind(orchestrator) as (message: string) => void;
    extractTarget('Look at /Users/henry/Downloads/cyrax for issues');
    expect(orchestrator.campaign.target).toBe('/Users/henry/Downloads/cyrax');
  });
});
