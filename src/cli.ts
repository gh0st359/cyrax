#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { stdin as input, stdout as output } from 'node:process';
import { Command } from 'commander';
import yaml from 'js-yaml';
import { CyraxOrchestrator } from './orchestrator.js';
import { loadConfig, redactConfig, saveConfig } from './config/loader.js';
import { CyraxConfig, modelProviders, providerDefaultModels, providerEnvVars } from './config/defaults.js';
import { ToolRegistry } from './tools/registry.js';
import * as display from './utils/display.js';

interface ChatOptions {
  config?: string;
  cwd?: string;
  campaign?: string;
  objective?: string;
  auto?: boolean;
  scope?: string;
  print?: boolean;
  simple?: boolean;
  tui?: boolean;
}

const program = new Command();
program
  .name('cyrax')
  .description('CYRAX - Autonomous AI Red Team Operator')
  .option('-c, --config <path>', 'Path to configuration file')
  .option('--cwd <path>', 'Working directory for this command');

program.command('init')
  .description('run first-time interactive setup')
  .action(async () => {
    const config = loadConfig(program.opts<{ config?: string }>().config);
    await setupInteractive(config, program.opts<{ config?: string }>().config ?? path.join('config', 'config.yaml'));
  });

program.command('configure')
  .alias('config')
  .description('write provider settings non-interactively')
  .option('--provider <provider>', 'Model provider')
  .option('--model <model>', 'Model name')
  .option('--api-key <key>', 'API key for the provider')
  .option('--api-key-env <env>', 'Environment variable to read the API key from')
  .option('--api-url <url>', 'Provider API URL')
  .option('--output <path>', 'Config file to write')
  .action((options) => configureCli(options as Record<string, string | undefined>));

program.command('chat')
  .description('start the interactive operator session')
  .argument('[prompt]', 'Run one prompt non-interactively, then exit')
  .option('--campaign <name>', 'Start or resume a named campaign')
  .option('--objective <objective>', 'Campaign objective')
  .option('--auto', 'Fully autonomous mode — no permission prompts')
  .option('--scope <targets>', 'Comma-separated in-scope targets')
  .option('--tui', 'Launch with Textual-style interface placeholder')
  .option('--simple', 'Force simple console mode')
  .option('--print', 'Print full final response after one-shot prompt')
  .action((prompt: string | undefined, options: ChatOptions) => chatCli(prompt ?? '', mergedOptions(options)));

program.command('status')
  .description('show resolved runtime status')
  .option('--show-config', 'Print redacted resolved configuration')
  .action((options) => statusCli(Boolean((options as { showConfig?: boolean }).showConfig)));

program.command('tools')
  .description('list registered tools')
  .option('--category <category>', 'Filter by category')
  .option('--available', 'Only show installed tools')
  .action((options) => toolsCli(options as { category?: string; available?: boolean }));

program.command('preflight')
  .description('check interpreter, packages, and local toolchain')
  .action(() => preflightCli());

program.argument('[prompt]', 'Run one prompt non-interactively, then exit')
  .option('--campaign <name>', 'Start or resume a named campaign')
  .option('--objective <objective>', 'Campaign objective')
  .option('--auto', 'Fully autonomous mode — no permission prompts')
  .option('--scope <targets>', 'Comma-separated in-scope targets')
  .option('--print', 'Print full final response after one-shot prompt')
  .action((prompt?: string) => {
    const opts = program.opts<ChatOptions>();
    if (prompt) return chatCli(prompt, opts);
    if (!process.argv.slice(2).length) return chatCli('', opts);
    return undefined;
  });

function mergedOptions(options: ChatOptions): ChatOptions {
  return { ...program.opts<ChatOptions>(), ...options };
}

async function setupInteractive(config: CyraxConfig, outputPath: string): Promise<void> {
  const rl = readline.createInterface({ input, output });
  try {
    const provider = await rl.question(`Provider (${config.model.provider}): `) || config.model.provider;
    const model = await rl.question(`Model (${providerDefaultModels[provider] ?? config.model.model_name}): `) || providerDefaultModels[provider] || config.model.model_name;
    const envVar = providerEnvVars[provider] ?? 'CYRAX_API_KEY';
    const key = await rl.question(`API key env/value (${envVar}): `);
    config.model.provider = modelProviders.includes(provider as CyraxConfig['model']['provider']) ? provider as CyraxConfig['model']['provider'] : config.model.provider;
    config.model.model_name = model;
    if (key && process.env[key]) config.model.api_key = process.env[key] ?? '';
    else if (key) config.model.api_key = key;
    saveConfig(config, outputPath);
    display.success(`Config written to ${outputPath}`);
  } finally {
    rl.close();
  }
}

function configureCli(options: Record<string, string | undefined>): void {
  const global = program.opts<{ config?: string }>();
  const config = loadConfig(global.config);
  if (options.provider && modelProviders.includes(options.provider as CyraxConfig['model']['provider'])) config.model.provider = options.provider as CyraxConfig['model']['provider'];
  if (options.model) config.model.model_name = options.model;
  if (options.apiKey) config.model.api_key = options.apiKey;
  if (options.apiKeyEnv) config.model.api_key = process.env[options.apiKeyEnv] ?? '';
  if (options.apiUrl) config.model.api_url = options.apiUrl;
  const outputPath = options.output ?? global.config ?? path.join('config', 'config.yaml');
  saveConfig(config, outputPath);
  display.success(`Config written to ${outputPath}`);
}

async function chatCli(prompt: string, options: ChatOptions): Promise<void> {
  const config = loadConfig(options.config);
  if (options.cwd) process.chdir(options.cwd);
  display.banner();
  const orchestratorOptions: { scopeTargets?: string[]; auto?: boolean; campaignName?: string; objective?: string } = {
    scopeTargets: options.scope ? options.scope.split(',').map((target) => target.trim()).filter(Boolean) : [],
  };
  if (options.auto !== undefined) orchestratorOptions.auto = options.auto;
  if (options.campaign !== undefined) orchestratorOptions.campaignName = options.campaign;
  if (options.objective !== undefined) orchestratorOptions.objective = options.objective;
  const orchestrator = new CyraxOrchestrator(config, orchestratorOptions);
  if (prompt) {
    const response = await orchestrator.chat(prompt);
    if (options.print) console.log(response);
    return;
  }
  const rl = readline.createInterface({ input, output });
  try {
    while (true) {
      const line = await rl.question(display.promptLabel(orchestrator.currentModeLabel(), orchestrator.model.modelName));
      if (['/exit', '/quit'].includes(line.trim())) break;
      await orchestrator.chat(line);
    }
  } finally {
    rl.close();
    await orchestrator.browser.close();
  }
}

function statusCli(showConfig: boolean): void {
  const config = loadConfig(program.opts<{ config?: string }>().config);
  console.log(`CYRAX TypeScript backend\nProvider: ${config.model.provider}\nModel: ${config.model.model_name}\nWork dir: ${config.tools.work_dir}`);
  if (showConfig) console.log(yaml.dump(redactConfig(config)));
}

function toolsCli(options: { category?: string; available?: boolean }): void {
  const registry = new ToolRegistry();
  const tools = registry.list(options.category).filter((tool) => !options.available || tool.available);
  for (const tool of tools) console.log(`${tool.available ? '✓' : '·'} ${tool.name.padEnd(12)} ${tool.category.padEnd(8)} ${tool.description}`);
}

function preflightCli(): void {
  const checks = [
    ['node', process.version],
    ['platform', `${process.platform}/${process.arch}`],
    ['package.json', fs.existsSync('package.json') ? 'ok' : 'missing'],
    ['dist', fs.existsSync('dist/src/cli.js') ? 'built' : 'not built; run npm run build'],
  ];
  for (const [name, value] of checks) console.log(`${name}: ${value}`);
}

await program.parseAsync(process.argv);
