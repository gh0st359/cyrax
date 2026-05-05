import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';
import {
  CyraxConfig,
  CyraxConfigSchema,
  deepMergeConfig,
  defaultConfig,
  isMissingApiKey,
  providerDefaultModels,
  providerEnvVars,
} from './defaults.js';

export function loadConfig(configPath?: string): CyraxConfig {
  const defaults = defaultConfig();
  const candidate = configPath ?? path.join('config', 'config.yaml');
  let loaded: unknown = {};
  if (fs.existsSync(candidate)) {
    loaded = yaml.load(fs.readFileSync(candidate, 'utf8')) ?? {};
  }
  const merged = deepMergeConfig(defaults, loaded);
  return applyEnvModelDefaults(CyraxConfigSchema.parse(merged));
}

export function saveConfig(config: CyraxConfig, outputPath: string): void {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, yaml.dump(config, { lineWidth: 120 }), 'utf8');
}

export function applyEnvModelDefaults(config: CyraxConfig): CyraxConfig {
  const provider = config.model.provider || 'anthropic';
  config.model.provider = provider;
  const defaultModel = providerDefaultModels[provider] ?? '';
  if (!config.model.model_name || (Object.values(providerDefaultModels).includes(config.model.model_name) && config.model.model_name !== defaultModel)) {
    config.model.model_name = defaultModel;
  }
  const envVar = providerEnvVars[provider];
  if (envVar && isMissingApiKey(config.model.api_key)) {
    const envValue = process.env[envVar];
    if (envValue) config.model.api_key = envValue;
  }
  if (provider === 'ollama' && !config.model.api_url) config.model.api_url = 'http://localhost:11434';
  if (provider === 'lmstudio' && !config.model.api_url) config.model.api_url = 'http://localhost:1234/v1';
  return config;
}

export function redactConfig(config: CyraxConfig): CyraxConfig {
  const clone = CyraxConfigSchema.parse(JSON.parse(JSON.stringify(config)));
  const key = clone.model.api_key;
  if (key && !isMissingApiKey(key)) {
    clone.model.api_key = key.length > 8 ? `${key.slice(0, 4)}...${key.slice(-4)}` : '***';
  }
  return clone;
}
