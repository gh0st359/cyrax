import chalk from 'chalk';

let streamingCursor = true;

export function configureStreaming(options: { cursor?: boolean }): void {
  if (typeof options.cursor === 'boolean') streamingCursor = options.cursor;
}

export function banner(): void {
  console.log(chalk.red.bold('CYRAX') + chalk.dim(' autonomous red team operator'));
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

export function showToolEvent(eventType: string, target: string, output = '', style: 'cyan' | 'green' | 'yellow' | 'red' = 'cyan'): void {
  const color = chalk[style];
  console.log(color(`\nCYRAX ${eventType}`));
  console.log(chalk.dim(`  ${target}`));
  if (output) console.log(output);
}

export function streamChunk(delta: string): void {
  process.stdout.write(delta);
}

export function streamEnd(): void {
  if (streamingCursor) process.stdout.write('');
  process.stdout.write('\n');
}

export function promptLabel(mode: string, model: string): string {
  return chalk.red('cyrax') + chalk.dim(` ${mode} · ${model}`) + chalk.red(' > ');
}
