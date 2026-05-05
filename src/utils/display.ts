import chalk from 'chalk';

export type ToolEventStyle = 'cyan' | 'green' | 'yellow' | 'red';

export interface SmoothStreamOptions {
  enabled?: boolean;
  delayMs?: number;
  minBatchChars?: number;
  maxBufferedChars?: number;
  output?: NodeJS.WriteStream;
}

let streamingCursor = true;

export function configureStreaming(options: { cursor?: boolean }): void {
  if (typeof options.cursor === 'boolean') streamingCursor = options.cursor;
}

export function banner(): void {
  const title = chalk.hex('#ff4d4d').bold('CYRAX');
  const subtitle = chalk.dim('AI red team operator');
  const hint = chalk.dim('/help for commands · /mode auto for autonomy · /compact to compress context');
  console.log(chalk.dim('╭────────────────────────────────────────────────────────╮'));
  console.log(chalk.dim('│ ') + `${title} ${subtitle}` + chalk.dim(' │'));
  console.log(chalk.dim('│ ') + hint + chalk.dim(' │'));
  console.log(chalk.dim('╰────────────────────────────────────────────────────────╯'));
}

export function info(message: string): void {
  console.log(chalk.cyan(message));
}

export function warning(message: string): void {
  console.log(chalk.yellow(message));
}

export function error(message: string): void {
  console.error(chalk.red(message));
}

export function success(message: string): void {
  console.log(chalk.green(message));
}

export function showAssistantStart(): void {
  console.log(chalk.dim('\n╭─ ') + chalk.hex('#ff4d4d').bold('cyrax') + chalk.dim(' responding'));
  process.stdout.write(chalk.dim('│ '));
}

export function showAssistantEnd(): void {
  console.log(chalk.dim('╰─'));
}

export function showToolEvent(eventType: string, target: string, output = '', style: ToolEventStyle = 'cyan'): void {
  const color = chalk[style];
  const marker = eventType.toLowerCase().includes('output') ? '⎿' : '●';
  console.log(color(`\n${marker} ${eventType}`) + chalk.dim(` ${target}`));
  if (output) console.log(indentOutput(output));
}

export function turnSummary(actions: number, succeeded: number, agents: number, tokens = 0): void {
  console.log(chalk.dim(`● Turn complete · ${actions} action(s) · ${succeeded} succeeded · ${agents} agent(s) · ${tokens} tokens`));
}

export function promptLabel(mode: string, model: string): string {
  const modeLabel = mode === 'auto' ? chalk.green('auto') : mode === 'plan' ? chalk.yellow('plan') : chalk.cyan('ask');
  return `${chalk.dim('╭─')} ${chalk.bold('user')} ${chalk.dim('(')}${modeLabel}${chalk.dim(` · ${model})`)}\n${chalk.dim('╰─')} `;
}

export class SmoothStreamRenderer {
  private buffer = '';
  private fullText = '';
  private visibleIndex = 0;
  private animation: Promise<void> | undefined;
  private closed = false;
  private readonly output: NodeJS.WriteStream;
  private readonly enabled: boolean;
  private readonly delayMs: number;
  private readonly minBatchChars: number;
  private readonly maxBufferedChars: number;

  constructor(options: SmoothStreamOptions = {}) {
    this.enabled = options.enabled ?? true;
    this.delayMs = options.delayMs ?? 5;
    this.minBatchChars = options.minBatchChars ?? 1;
    this.maxBufferedChars = options.maxBufferedChars ?? 320;
    this.output = options.output ?? process.stdout;
  }

  addPart(part: string): void {
    if (!part) return;
    this.fullText += part;
    if (!this.enabled || !this.output.isTTY) {
      this.output.write(part);
      this.visibleIndex = this.fullText.length;
      return;
    }
    this.buffer += part;
    if (!this.animation) this.animation = this.animate();
  }

  async done(): Promise<string> {
    this.closed = true;
    if (this.animation) await this.animation;
    while (this.visibleIndex < this.fullText.length) this.flushNext(true);
    if (streamingCursor) this.output.write('');
    this.output.write('\n');
    return this.fullText;
  }

  private async animate(): Promise<void> {
    try {
      while (!this.closed || this.visibleIndex < this.fullText.length) {
        if (this.visibleIndex >= this.fullText.length) {
          await sleep(this.delayMs);
          continue;
        }
        this.flushNext(false);
        const last = this.fullText[this.visibleIndex - 1] ?? '';
        await sleep(last === '\n' ? this.delayMs * 2 : this.delayMs);
      }
    } finally {
      this.animation = undefined;
    }
  }

  private flushNext(force: boolean): void {
    const remaining = this.fullText.slice(this.visibleIndex);
    if (!remaining) return;
    const shouldBurst = remaining.length > this.maxBufferedChars;
    const count = force || shouldBurst ? Math.min(remaining.length, shouldBurst ? 24 : remaining.length) : this.nextChunkLength(remaining);
    if (count < this.minBatchChars && !force) return;
    const chunk = remaining.slice(0, count);
    this.output.write(chunk);
    this.visibleIndex += chunk.length;
  }

  private nextChunkLength(remaining: string): number {
    const newlineIndex = remaining.indexOf('\n');
    if (newlineIndex === 0) return 1;
    if (newlineIndex > 0 && newlineIndex <= 18) return newlineIndex + 1;
    const wordMatch = remaining.match(/^\S+\s*/);
    if (!wordMatch?.[0]) return 1;
    const word = wordMatch[0];
    if (word.length <= 3) return word.length;
    return Math.max(1, Math.min(3, word.length));
  }
}

function indentOutput(output: string): string {
  return output.split('\n').map((line) => chalk.dim('  │ ') + line).join('\n');
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
