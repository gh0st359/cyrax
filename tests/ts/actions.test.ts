import { describe, expect, it } from 'vitest';
import { findAllActions, findUnclosedTags } from '../../src/utils/actions.js';

describe('action parsing', () => {
  it('extracts execute, write, spawn, store, finding, and kill in document order', () => {
    const response = `Plan
[WRITE_FILE path="scan.js"]console.log('ok')[/WRITE_FILE]
[EXECUTE]node scan.js[/EXECUTE]
[SPAWN type="recon"]Enumerate subdomains[/SPAWN]
[STORE category="notes" key="one"]{"ok":true}[/STORE]
[FINDING severity="high" title="XSS"]payload reflected[/FINDING]
[KILL agent="recon-1" reason="done"]`;
    const actions = findAllActions(response);
    expect(actions.map((action) => action.kind)).toEqual(['write_file', 'execute', 'spawn', 'store', 'finding', 'kill']);
    expect(actions[0]?.groups[0]).toBe('scan.js');
    expect(actions[1]?.groups[0]?.trim()).toBe('node scan.js');
    expect(actions[2]?.groups[0]).toBe('recon');
  });

  it('detects unclosed tags', () => {
    expect(findUnclosedTags('[EXECUTE]nmap example.com')).toContainEqual(expect.stringContaining('EXECUTE'));
  });
});
