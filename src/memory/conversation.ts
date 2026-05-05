import { ChatMessage } from '../types/common.js';

export class ConversationMemory {
  readonly maxHistory: number;
  messages: ChatMessage[] = [];

  constructor(maxHistory = 50) {
    this.maxHistory = maxHistory;
  }

  addMessage(role: ChatMessage['role'], content: string): void {
    this.messages.push({ role, content });
    this.trim();
  }

  getContext(): ChatMessage[] {
    return [...this.messages];
  }

  clear(): void {
    this.messages = [];
  }

  compact(keepRecent = 12): void {
    if (this.messages.length <= keepRecent) return;
    const older = this.messages.slice(0, -keepRecent);
    const recent = this.messages.slice(-keepRecent);
    const summary = older.map((msg) => `${msg.role}: ${msg.content}`).join('\n').slice(-6000);
    this.messages = [{ role: 'user', content: `[Compacted conversation summary]\n${summary}` }, ...recent];
  }

  private trim(): void {
    if (this.messages.length > this.maxHistory) {
      this.messages = this.messages.slice(-this.maxHistory);
    }
  }
}
