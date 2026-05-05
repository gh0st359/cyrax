import crypto from 'node:crypto';
import { CyraxConfig } from './config/defaults.js';
import { ConversationMemory } from './memory/conversation.js';
import { CampaignState } from './memory/campaign.js';
import { KnowledgeBase } from './memory/knowledge.js';
import { MissionMemory } from './memory/mission.js';
import { ModelManager } from './models/client.js';
import { ToolExecutor } from './tools/executor.js';
import { ToolRegistry } from './tools/registry.js';
import { BrowserManager, isBrowserCommand } from './tools/browser.js';
import { AgentPool, AgentType } from './agents/pool.js';
import { findAllActions, findToolIntentActions, findUnclosedTags } from './utils/actions.js';
import { PermissionGate, ScopeEnforcer } from './utils/safety.js';
import { platformContext } from './utils/platform.js';
import { EngagementLogger } from './utils/logger.js';
import * as display from './utils/display.js';

export class CyraxOrchestrator {
  readonly conversation: ConversationMemory;
  readonly model: ModelManager;
  readonly executor: ToolExecutor;
  readonly tools: ToolRegistry;
  readonly browser: BrowserManager;
  readonly knowledge: KnowledgeBase;
  readonly campaign = new CampaignState();
  readonly mission = new MissionMemory();
  readonly permissionGate: PermissionGate;
  readonly scope: ScopeEnforcer;
  readonly agentPool: AgentPool;
  readonly logger: EngagementLogger;
  private lastResponseHash = '';
  private pauseRequested = false;
  private actionsExecutedThisTurn = 0;
  private cmdsSucceededThisTurn = 0;
  private tokensThisTurn = 0;

  constructor(readonly config: CyraxConfig, options: { scopeTargets?: string[]; auto?: boolean; campaignName?: string; objective?: string } = {}) {
    this.scope = new ScopeEnforcer(options.scopeTargets ?? []);
    this.permissionGate = new PermissionGate(options.auto ?? config.safety.auto_approve);
    this.conversation = new ConversationMemory(config.memory.max_history);
    this.model = new ModelManager(config.model);
    this.executor = new ToolExecutor({ workDir: config.tools.work_dir, timeoutSeconds: config.tools.timeout, allowDangerous: config.tools.allow_dangerous, scope: this.scope });
    this.tools = new ToolRegistry(this.executor);
    this.browser = new BrowserManager({ headless: true, workDir: config.tools.work_dir });
    this.knowledge = new KnowledgeBase(config.memory.db_path);
    this.agentPool = new AgentPool(this.model, this.tools);
    this.logger = new EngagementLogger(config.logging.log_dir);
    if (options.campaignName) this.campaign.name = options.campaignName;
    if (options.objective) this.campaign.objective = options.objective;
  }

  async chat(userMessage: string): Promise<string> {
    if (userMessage.startsWith('/')) return this.handleCommand(userMessage);
    this.logger.conversation('user', userMessage);
    this.conversation.addMessage('user', userMessage);
    this.actionsExecutedThisTurn = 0;
    this.cmdsSucceededThisTurn = 0;
    this.tokensThisTurn = 0;
    if (!this.scope.enabled) this.tryExtractTarget(userMessage);
    let response: string;
    try {
      response = await this.streamResponse(this.buildSystemPrompt());
    } catch (error) {
      const message = `Model error: ${error instanceof Error ? error.message : String(error)}`;
      display.error(message);
      return message;
    }
    this.conversation.addMessage('assistant', response);
    this.logger.conversation('assistant', response);
    const final = await this.processResponse(response);
    display.turnSummary(this.actionsExecutedThisTurn, this.cmdsSucceededThisTurn, Object.keys(this.agentPool.getStatus()).length, this.tokensThisTurn);
    return final;
  }

  handleCommand(command: string): string {
    const [name, ...args] = command.trim().split(/\s+/);
    if (name === '/help') {
      console.log(`CYRAX Commands\n/config show resolved config\n/model <name> switch model name\n/mode <ask|auto|plan> set permission mode\n/scope show active scope\n/compact [n] compact conversation\n/clear clear conversation\n/pause stop current loop`);
      return '';
    }
    if (name === '/config') {
      console.log(JSON.stringify(this.config, null, 2));
      return '';
    }
    if (name === '/model') {
      const model = args.join(' ').trim();
      if (model) this.model.setModel(model);
      display.success(`Model: ${this.model.modelName}`);
      return '';
    }
    if (name === '/mode') {
      const mode = args[0];
      if (mode === 'auto' || mode === 'plan' || mode === 'interactive') this.permissionGate.setMode(mode);
      if (mode === 'ask') this.permissionGate.setMode('interactive');
      display.success(`Mode: ${this.currentModeLabel()}`);
      return '';
    }
    if (name === '/scope') {
      console.log(this.scope.getScopeDescription());
      return '';
    }
    if (name === '/compact') {
      this.conversation.compact(Number(args[0] ?? 12));
      display.success('Conversation compacted');
      return '';
    }
    if (name === '/clear') {
      this.conversation.clear();
      display.success('Conversation cleared');
      return '';
    }
    if (name === '/pause') {
      this.pauseRequested = true;
      display.warning('Pause requested');
      return '';
    }
    display.warning(`Unknown command: ${command}`);
    return '';
  }

  currentModeLabel(): string {
    if (this.permissionGate.policyMode === 'auto') return 'auto';
    if (this.permissionGate.policyMode === 'plan') return 'plan';
    return 'ask';
  }

  private buildSystemPrompt(): string {
    const target = this.campaign.target;
    if (!target) {
      return `You are CYRAX, an autonomous AI red team operator for authorized penetration testing.

If the user asks whether you can operate locally, answer yes. If the user gives a local path, repository path, URL, host, or IP to inspect, treat it as the active target and immediately use action blocks.

ACTION FORMAT:
You must use exact executable tags when work requires the machine: [EXECUTE]command[/EXECUTE]. Do not narrate tool calls as plain text. Never write phrases like "invoke tool bash" or "command is" unless they are inside [EXECUTE] tags.

Style: skilled operator, short, direct, no filler.

${platformContext()}`;
    }
    const activeAgents = Object.values(this.agentPool.getStatus()).filter((a) => ['active', 'starting'].includes(a.status)).map((a) => `${a.id}: ${a.status} - ${a.task}`).join('\n') || 'none';
    return `You are CYRAX, an autonomous AI red team operator for authorized penetration testing.

TARGET: ${target}
SCOPE: ${this.scope.getScopeDescription()}
MODE: ${this.currentModeLabel()}

MISSION MEMORY:
${this.mission.buildContextBlock()}

CAMPAIGN STATE:
${this.campaign.summary()}

KNOWLEDGE:
${this.knowledge.getSummary()}

ACTIVE AGENTS:
${activeAgents}

TOOLS:
${this.tools.getAvailableToolsSummary()}

ACTION FORMAT:
Use exact executable tags: [EXECUTE]command[/EXECUTE], [WRITE_FILE path="relative/path"]content[/WRITE_FILE], [SPAWN type="recon|exploit|post|web|cloud|ad|osint"]task[/SPAWN], [STORE category="x" key="y"]json[/STORE], [FINDING severity="low|medium|high|critical" title="..."]details[/FINDING], [KILL agent="id"].

If you say you are scanning, checking, testing, reading, listing, or analyzing a target, include the needed [EXECUTE] or browser.* action in the same response. Do not describe tool usage in natural language. Never output "invoke tool bash with command is ..."; output [EXECUTE]...[/EXECUTE].

Operate continuously until the objective is complete, paused, or blocked. Keep output concise and avoid markdown headings.

${platformContext()}`;
  }

  private async streamResponse(systemPrompt: string): Promise<string> {
    display.showAssistantStart();
    const renderer = new display.SmoothStreamRenderer({
      enabled: this.config.display.streaming,
      delayMs: this.config.display.stream_delay_ms,
      minBatchChars: this.config.display.stream_min_chunk_chars,
      maxBufferedChars: this.config.display.stream_max_buffered_chars,
    });
    for await (const event of this.model.generateStream(systemPrompt, this.conversation.getContext())) {
      if (event.delta) {
        renderer.addPart(event.delta);
      }
      if (event.done && event.tokensOut) this.tokensThisTurn += event.tokensOut;
      if (event.done && event.content) break;
    }
    const content = await renderer.done();
    display.showAssistantEnd();
    return content;
  }

  private async processResponse(response: string): Promise<string> {
    let accumulated = response;
    let current = response;
    let depth = 0;
    const seen = new Set<string>();
    while (!this.pauseRequested) {
      const hash = crypto.createHash('md5').update(current.slice(0, 500)).digest('hex');
      if (seen.has(hash) || hash === this.lastResponseHash) break;
      seen.add(hash);
      this.lastResponseHash = hash;
      const results = await this.executeActions(current);
      this.mission.extractFromResponse(current);
      if (results.length === 0) {
        if (this.campaign.target && depth === 0 && this.actionsExecutedThisTurn === 0 && this.promisedAction(current)) {
          this.conversation.addMessage('user', '[Action Feedback] You promised action but executed none. Reply now with only the immediate [EXECUTE]...[/EXECUTE] or browser.* action blocks needed to continue. Do not describe the tool call in prose.');
          current = await this.streamResponse(this.buildSystemPrompt());
          this.conversation.addMessage('assistant', current);
          accumulated += `\n\n${current}`;
          depth += 1;
          continue;
        }
        break;
      }
      this.conversation.addMessage('user', `[Action Results]\n${results.join('\n\n')}`);
      current = await this.streamResponse(this.buildSystemPrompt());
      this.conversation.addMessage('assistant', current);
      accumulated += `\n\n${current}`;
      depth += 1;
    }
    this.pauseRequested = false;
    return accumulated;
  }

  private async executeActions(response: string): Promise<string[]> {
    const unclosed = findUnclosedTags(response);
    if (unclosed.length) return [`[Action Feedback]\nMalformed action tags:\n${unclosed.join('\n')}`];
    const results: string[] = [];
    const actions = findAllActions(response);
    const executableActions = actions.length > 0 ? actions : findToolIntentActions(response);
    for (const action of executableActions) {
      let actionCounted = false;
      if (action.kind === 'execute') {
        const command = action.groups[0]?.trim() ?? '';
        if (!command) continue;
        const permission = this.permissionGate.check(command);
        if (!permission[0]) {
          results.push(`[Action Blocked]\n${permission[1]}`);
          continue;
        }
        display.showToolEvent('Executing', command);
        const result = isBrowserCommand(command) ? await this.browser.run(command) : await this.executor.execute(command);
        const output = 'output' in result ? result.output : `${result.stdout}${result.stderr ? `\n${result.stderr}` : ''}`.trim();
        if ('success' in result && result.success) this.cmdsSucceededThisTurn += 1;
        this.actionsExecutedThisTurn += 1;
        actionCounted = true;
        display.showToolEvent('Output', command, output.slice(0, 4000), 'green');
        results.push(output || 'OK');
      }
      if (action.kind === 'write_file') {
        const result = this.executor.writeFile(action.groups[0] ?? '', action.groups[1] ?? '');
        this.actionsExecutedThisTurn += 1;
        actionCounted = true;
        if (result.success) this.cmdsSucceededThisTurn += 1;
        results.push(result.success ? result.stdout : result.stderr);
      }
      if (action.kind === 'spawn') {
        const agentId = this.agentPool.spawn((action.groups[0] ?? 'recon') as AgentType, action.groups[1] ?? '');
        this.campaign.registerAgent(agentId, action.groups[0] ?? 'recon', action.groups[1] ?? '');
        results.push(`Spawned agent ${agentId}`);
      }
      if (action.kind === 'store') {
        try {
          const value = JSON.parse(action.groups[2] ?? '{}') as Record<string, unknown>;
          this.knowledge.store(action.groups[0] ?? 'general', action.groups[1] ?? `item-${Date.now()}`, value);
          results.push('Stored knowledge item');
        } catch {
          results.push('Failed to store knowledge: content was not valid JSON');
        }
      }
      if (action.kind === 'finding') {
        this.knowledge.addFinding({ severity: action.groups[0] ?? 'info', title: action.groups[1] ?? 'Finding', description: action.groups[2] ?? '', target: this.campaign.target });
        results.push(`Finding recorded: ${action.groups[1] ?? 'Finding'}`);
      }
      if (action.kind === 'kill') {
        results.push(this.agentPool.kill(action.groups[0] ?? '') ? `Killed ${action.groups[0]}` : `Agent not found: ${action.groups[0]}`);
      }
      if (!actionCounted && action.kind !== 'execute') this.actionsExecutedThisTurn += 1;
    }
    return results;
  }

  private tryExtractTarget(message: string): void {
    const url = message.match(/https?:\/\/[^\s'"`<>]+/)?.[0];
    const localPath = message.match(/(?:^|\s)((?:~|\/|[A-Za-z]:\\)[^\s'"`<>]+)/)?.[1];
    const domain = message.match(/\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b/)?.[0];
    const ip = message.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/)?.[0];
    const target = url ?? localPath ?? domain ?? ip;
    if (target) this.campaign.setTarget(target, this.campaign.objective);
  }

  private promisedAction(response: string): boolean {
    return /\b(scanning|scan|checking|check|testing|test|enumerating|enumerate|running|run|reading|read|listing|list|analyzing|analyse|analyze|inspect|looking at|try)\b/i.test(response)
      || findToolIntentActions(response).length > 0;
  }
}
