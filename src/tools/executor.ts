import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { CommandResult } from '../types/common.js';
import { ScopeEnforcer } from '../utils/safety.js';
import { isWindows } from '../utils/platform.js';

export function stripMarkdownFences(command: string): string {
  const trimmed = command.trim();
  const block = trimmed.match(/^```\w*\s*\n([\s\S]*?)```\s*$/);
  if (block?.[1]) return dedent(block[1]).trim();
  const inline = trimmed.match(/^```\w*\s+([\s\S]*?)```\s*$/);
  if (inline?.[1]) return dedent(inline[1]).trim();
  return dedent(trimmed).trim();
}

function dedent(value: string): string {
  const lines = value.replace(/\t/g, '    ').split(/\r?\n/);
  const indents = lines.filter((line) => line.trim()).map((line) => line.match(/^ */)?.[0].length ?? 0);
  const min = Math.min(...indents, 0);
  return lines.map((line) => line.slice(min)).join('\n');
}

export function adaptWindowsCommands(command: string): string {
  if (!isWindows) return command;
  return command
    .replace(/\bcat\b\s+(?!\|)/gi, 'type ')
    .replace(/\bls\s+-la?\b/gi, 'dir')
    .replace(/\bls\b(?!\s*-)/gi, 'dir /B')
    .replace(/\brm\s+-rf?\b/gi, 'rd /s /q')
    .replace(/\bcp\b/gi, 'copy')
    .replace(/\bmv\b/gi, 'move')
    .replace(/\bclear\b/gi, 'cls');
}

export function splitCompoundCommands(command: string): string[] {
  const parts: string[] = [];
  let current = '';
  let quote: string | null = null;
  for (let i = 0; i < command.length; i += 1) {
    const char = command[i];
    const next = command[i + 1];
    if (quote) {
      current += char;
      if (char === quote && command[i - 1] !== '\\') quote = null;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      current += char;
      continue;
    }
    if ((char === '&' && next === '&') || char === ';') {
      if (current.trim()) parts.push(current.trim());
      current = '';
      if (char === '&') i += 1;
      continue;
    }
    current += char;
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

export function sanitizeCommand(command: string, allowDangerous = false): [boolean, string] {
  const cleaned = stripMarkdownFences(command);
  if (allowDangerous) return [true, cleaned];
  const dangerous = [/rm\s+-rf\s+\//i, /mkfs\b/i, /dd\s+if=.*of=\/dev\//i, /:\(\)\s*\{\s*:\|:/, /shutdown\b/i, /reboot\b/i];
  for (const pattern of dangerous) {
    if (pattern.test(cleaned)) return [false, `Dangerous command blocked: ${cleaned}`];
  }
  return [true, cleaned];
}

export class ToolExecutor {
  readonly workDir: string;
  readonly timeoutSeconds: number;
  readonly allowDangerous: boolean;
  scope: ScopeEnforcer | undefined;

  constructor(options: { workDir?: string; timeoutSeconds?: number; allowDangerous?: boolean; scope?: ScopeEnforcer } = {}) {
    this.workDir = options.workDir || path.join(os.tmpdir(), 'cyrax');
    this.timeoutSeconds = options.timeoutSeconds ?? 300;
    this.allowDangerous = options.allowDangerous ?? false;
    this.scope = options.scope;
    fs.mkdirSync(this.workDir, { recursive: true });
  }

  async execute(command: string): Promise<CommandResult> {
    const started = Date.now();
    const [safe, sanitizedOrReason] = sanitizeCommand(command, this.allowDangerous);
    if (!safe) return this.result(command, false, '', sanitizedOrReason, null, started);
    const scoped = this.scope?.checkCommand(sanitizedOrReason) ?? [true, ''];
    if (!scoped[0]) return this.result(command, false, '', scoped[1], null, started);
    const shell = isWindows ? 'cmd.exe' : '/bin/sh';
    const args = isWindows ? ['/d', '/s', '/c', adaptWindowsCommands(sanitizedOrReason)] : ['-c', sanitizedOrReason];
    return new Promise((resolve) => {
      const child = spawn(shell, args, { cwd: this.workDir, windowsHide: true });
      let stdout = '';
      let stderr = '';
      const timer = setTimeout(() => {
        child.kill('SIGTERM');
        resolve(this.result(command, false, stdout, `Command timed out after ${this.timeoutSeconds}s`, null, started));
      }, this.timeoutSeconds * 1000);
      child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString(); });
      child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });
      child.on('close', (code) => {
        clearTimeout(timer);
        resolve(this.result(command, code === 0, stdout, stderr, code, started));
      });
      child.on('error', (error) => {
        clearTimeout(timer);
        resolve(this.result(command, false, stdout, error.message, null, started));
      });
    });
  }

  writeFile(relativePath: string, content: string): CommandResult {
    const started = Date.now();
    const resolved = this.resolveSafePath(relativePath);
    if (!resolved.ok) return this.result(`write ${relativePath}`, false, '', resolved.error, null, started);
    fs.mkdirSync(path.dirname(resolved.path), { recursive: true });
    fs.writeFileSync(resolved.path, content, 'utf8');
    return this.result(`write ${relativePath}`, true, `Wrote ${relativePath}`, '', 0, started);
  }

  readFile(relativePath: string): CommandResult {
    const started = Date.now();
    const resolved = this.resolveSafePath(relativePath);
    if (!resolved.ok) return this.result(`read ${relativePath}`, false, '', resolved.error, null, started);
    if (!fs.existsSync(resolved.path)) return this.result(`read ${relativePath}`, false, '', 'File not found', 1, started);
    return this.result(`read ${relativePath}`, true, fs.readFileSync(resolved.path, 'utf8'), '', 0, started);
  }

  private resolveSafePath(inputPath: string): { ok: true; path: string } | { ok: false; error: string } {
    if (inputPath.includes('\0')) return { ok: false, error: 'Rejected path outside work directory' };
    const decoded = decodeURIComponent(inputPath);
    if (path.isAbsolute(decoded)) return { ok: false, error: 'Rejected path outside work directory' };
    const resolved = path.resolve(this.workDir, decoded);
    const root = path.resolve(this.workDir);
    if (resolved !== root && !resolved.startsWith(root + path.sep)) return { ok: false, error: 'Rejected path outside work directory' };
    return { ok: true, path: resolved };
  }

  private result(command: string, success: boolean, stdout: string, stderr: string, exitCode: number | null, started: number): CommandResult {
    return { command, success, stdout, stderr, exitCode, duration: (Date.now() - started) / 1000 };
  }
}
