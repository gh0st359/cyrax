import { describe, expect, it } from 'vitest';
import { promptLabel, SmoothStreamRenderer } from '../../src/utils/display.js';

class MemoryOutput {
  readonly isTTY = false;
  chunks: string[] = [];

  write(chunk: string): boolean {
    this.chunks.push(chunk);
    return true;
  }
}

describe('display polish', () => {
  it('uses user as the prompt identity instead of cyrax', () => {
    const label = promptLabel('auto', 'claude-sonnet-4');
    expect(label).toContain('user');
    expect(label.toLowerCase()).not.toContain('cyrax ›');
    expect(label).toContain('claude-sonnet-4');
  });

  it('decouples network chunks from rendered smooth-stream output', async () => {
    const output = new MemoryOutput();
    const renderer = new SmoothStreamRenderer({ enabled: true, output: output as unknown as NodeJS.WriteStream });
    renderer.addPart('Hello ');
    renderer.addPart('world');
    await expect(renderer.done()).resolves.toBe('Hello world');
    expect(output.chunks.join('')).toBe('Hello world\n');
  });
});
