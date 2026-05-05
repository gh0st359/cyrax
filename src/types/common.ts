export type Role = 'system' | 'user' | 'assistant';

export interface ChatMessage {
  role: Exclude<Role, 'system'>;
  content: string;
}

export interface ModelResponse {
  content: string;
  tokensIn: number;
  tokensOut: number;
}

export interface StreamDelta {
  delta: string;
  done: boolean;
  content?: string;
  tokensIn?: number;
  tokensOut?: number;
}

export interface CommandResult {
  command: string;
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  duration: number;
}

export interface Finding {
  title: string;
  severity: string;
  description: string;
  target?: string;
  evidence?: string;
  commandActionId?: string;
  rawOutputRef?: string;
  agentId?: string;
  targetUrlHost?: string;
  createdAt?: string;
}

export interface ActionMatch {
  index: number;
  kind: 'execute' | 'write_file' | 'spawn' | 'store' | 'kill' | 'finding' | 'report';
  groups: string[];
  raw: string;
}
