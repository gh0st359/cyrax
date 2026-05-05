import os from 'node:os';
import path from 'node:path';

export const isWindows = process.platform === 'win32';

export function getDefaultWorkDir(): string {
  return path.join(os.tmpdir(), 'cyrax');
}

export function platformContext(): string {
  return `PLATFORM CONTEXT:\n- OS: ${os.platform()} ${os.release()}\n- Architecture: ${os.arch()}\n- Shell: ${process.env.SHELL ?? process.env.ComSpec ?? 'unknown'}\n- Working directory: ${process.cwd()}`;
}
