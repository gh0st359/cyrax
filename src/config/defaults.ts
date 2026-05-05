import os from 'node:os';
import path from 'node:path';
import { z } from 'zod';

export const modelProviders = ['anthropic', 'openai', 'google', 'xai', 'ollama', 'lmstudio', 'vllm', 'custom'] as const;
export type ModelProvider = (typeof modelProviders)[number];

export const providerEnvVars: Record<string, string> = {
  anthropic: 'ANTHROPIC_API_KEY',
  openai: 'OPENAI_API_KEY',
  google: 'GOOGLE_API_KEY',
  xai: 'XAI_API_KEY',
  custom: 'CYRAX_API_KEY',
  vllm: 'VLLM_API_KEY',
};

export const providerDefaultModels: Record<string, string> = {
  anthropic: 'claude-sonnet-4-20250514',
  openai: 'gpt-4o',
  google: 'gemini-1.5-pro',
  xai: 'grok-2',
  ollama: 'llama3.1:70b',
  lmstudio: 'local-model',
  vllm: 'local-model',
  custom: 'custom-model',
};

export const apiKeyPlaceholders = new Set(['', 'YOUR_API_KEY_HERE', 'your-key-here', 'sk-xxxxx', 'xxxxx']);

export const CyraxConfigSchema = z.object({
  model: z.object({
    provider: z.enum(modelProviders).default('anthropic'),
    api_key: z.string().default(''),
    api_url: z.string().optional(),
    model_name: z.string().default('claude-sonnet-4-20250514'),
    temperature: z.number().default(0.7),
    max_tokens: z.number().int().positive().default(4096),
  }).default({}),
  tools: z.object({
    timeout: z.number().int().positive().default(300),
    allow_dangerous: z.boolean().default(false),
    work_dir: z.string().default(''),
  }).default({}),
  memory: z.object({
    db_path: z.string().default('data/cyrax.db'),
    max_history: z.number().int().positive().default(50),
  }).default({}),
  logging: z.object({
    log_dir: z.string().default('logs'),
    level: z.string().default('INFO'),
    engagement_logging: z.boolean().default(true),
  }).default({}),
  display: z.object({
    show_reasoning: z.boolean().default(true),
    streaming: z.boolean().default(true),
    show_raw_output: z.boolean().default(false),
    theme: z.string().default('dark'),
  }).default({}),
  safety: z.object({
    auto_approve: z.boolean().default(false),
  }).default({}),
  campaign: z.object({
    data_dir: z.string().default('data/campaigns'),
    status_interval: z.number().int().positive().default(5),
  }).default({}),
});

export type CyraxConfig = z.infer<typeof CyraxConfigSchema>;

export function defaultWorkDir(): string {
  return path.join(os.tmpdir(), 'cyrax');
}

export function defaultConfig(): CyraxConfig {
  return CyraxConfigSchema.parse({
    model: {
      provider: 'anthropic',
      api_key: process.env.ANTHROPIC_API_KEY ?? '',
      model_name: 'claude-sonnet-4-20250514',
      temperature: 0.7,
      max_tokens: 4096,
    },
    tools: { timeout: 300, allow_dangerous: false, work_dir: defaultWorkDir() },
    memory: { db_path: 'data/cyrax.db', max_history: 50 },
    logging: { log_dir: 'logs', level: 'INFO', engagement_logging: true },
    display: { show_reasoning: true, streaming: true, show_raw_output: false, theme: 'dark' },
    safety: { auto_approve: false },
    campaign: { data_dir: 'data/campaigns', status_interval: 5 },
  });
}

export function isMissingApiKey(value: string | undefined): boolean {
  return apiKeyPlaceholders.has((value ?? '').trim());
}

export function deepMergeConfig(base: unknown, override: unknown): unknown {
  if (!isPlainObject(base) || !isPlainObject(override)) return override ?? base;
  const result: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(override)) {
    result[key] = key in result ? deepMergeConfig(result[key], value) : value;
  }
  return result;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
