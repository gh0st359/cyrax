import { mkdtempSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { splitCompoundCommands, stripMarkdownFences, ToolExecutor } from '../../src/tools/executor.js';

describe('tool executor', () => {
  it('strips markdown fences and splits compound commands', () => {
    expect(stripMarkdownFences('```bash\necho hi\n```')).toBe('echo hi');
    expect(splitCompoundCommands('echo one && echo two; echo three')).toEqual(['echo one', 'echo two', 'echo three']);
  });

  it('writes and reads allowed relative paths', () => {
    const executor = new ToolExecutor({ workDir: mkdtempSync(path.join(os.tmpdir(), 'cyrax-test-')) });
    expect(executor.writeFile('notes/a.txt', 'hello').success).toBe(true);
    expect(executor.readFile('notes/a.txt').stdout).toBe('hello');
  });

  it('rejects traversal and absolute paths', () => {
    const executor = new ToolExecutor({ workDir: mkdtempSync(path.join(os.tmpdir(), 'cyrax-test-')) });
    expect(executor.writeFile('../secret', 'x').success).toBe(false);
    expect(executor.readFile('/etc/passwd').success).toBe(false);
  });

  it('executes commands in the work directory', async () => {
    const executor = new ToolExecutor({ workDir: mkdtempSync(path.join(os.tmpdir(), 'cyrax-test-')), timeoutSeconds: 5 });
    const result = await executor.execute('printf ok');
    expect(result.success).toBe(true);
    expect(result.stdout).toBe('ok');
  });
});
