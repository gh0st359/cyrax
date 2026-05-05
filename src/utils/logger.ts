import fs from 'node:fs';
import path from 'node:path';

export class EngagementLogger {
  readonly logDir: string;

  constructor(logDir = 'logs') {
    this.logDir = logDir;
    fs.mkdirSync(logDir, { recursive: true });
  }

  info(message: string): void {
    this.write('INFO', message);
  }

  error(scope: string, message: string): void {
    this.write('ERROR', `[${scope}] ${message}`);
  }

  modelCall(provider: string, model: string, tokensIn: number, tokensOut: number): void {
    this.write('MODEL', `${provider}/${model} tokens_in=${tokensIn} tokens_out=${tokensOut}`);
  }

  conversation(role: string, content: string): void {
    this.write('CHAT', `${role}: ${content}`);
  }

  private write(level: string, message: string): void {
    const line = `${new Date().toISOString()} ${level} ${message}\n`;
    fs.appendFileSync(path.join(this.logDir, 'cyrax.log'), line, 'utf8');
  }
}
