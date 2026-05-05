import { mkdtempSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadConfig, redactConfig } from '../../src/config/loader.js';
import { ConversationMemory } from '../../src/memory/conversation.js';
import { KnowledgeBase } from '../../src/memory/knowledge.js';

describe('config and memory', () => {
  it('loads yaml config and redacts API keys', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'cyrax-config-'));
    const configPath = path.join(dir, 'config.yaml');
    writeFileSync(configPath, 'model:\n  provider: openai\n  api_key: sk-abcdefghijkl\n  model_name: gpt-4o-mini\n');
    const config = loadConfig(configPath);
    expect(config.model.provider).toBe('openai');
    expect(redactConfig(config).model.api_key).toBe('sk-a...ijkl');
  });

  it('compacts conversation while keeping recent turns', () => {
    const memory = new ConversationMemory(50);
    for (let i = 0; i < 8; i += 1) memory.addMessage('user', `message ${i}`);
    memory.compact(3);
    expect(memory.messages).toHaveLength(4);
    expect(memory.messages[0]?.content).toContain('Compacted conversation summary');
  });

  it('stores knowledge and findings in JSON-backed storage', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'cyrax-kb-'));
    const kb = new KnowledgeBase(path.join(dir, 'cyrax.db'));
    kb.store('notes', 'a', { ok: true });
    kb.addFinding({ title: 'Reflected XSS', severity: 'high', description: 'payload reflected' });
    expect(kb.retrieve('notes', 'a')?.ok).toBe(true);
    expect(kb.listFindings()[0]?.title).toBe('Reflected XSS');
  });
});
