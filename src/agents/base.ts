import { ConversationMemory } from '../memory/conversation.js';
import { ModelManager } from '../models/client.js';
import { ToolRegistry } from '../tools/registry.js';
import { BrowserManager, isBrowserCommand } from '../tools/browser.js';
import { findAllActions } from '../utils/actions.js';
import { Finding } from '../types/common.js';

export abstract class BaseAgent {
  readonly memory = new ConversationMemory(30);
  status = 'initialized';
  findings: Finding[] = [];
  iteration = 0;

  constructor(
    readonly agentId: string,
    readonly task: string,
    readonly model: ModelManager,
    readonly tools: ToolRegistry,
    readonly browser = new BrowserManager({ headless: true }),
    readonly maxIterations = 20,
  ) {}

  protected abstract buildAgentPrompt(): string;

  async run(): Promise<{ agentId: string; status: string; findings: Finding[]; report: string }> {
    this.status = 'active';
    this.memory.addMessage('user', this.task);
    let report = '';
    for (this.iteration = 0; this.iteration < this.maxIterations; this.iteration += 1) {
      const response = await this.model.generate(this.buildAgentPrompt(), this.memory.getContext());
      this.memory.addMessage('assistant', response.content);
      report += `${response.content}\n`;
      const actions = findAllActions(response.content);
      if (actions.length === 0) break;
      const results: string[] = [];
      for (const action of actions) {
        if (action.kind === 'execute') {
          const command = action.groups[0]?.trim() ?? '';
          const result = isBrowserCommand(command) ? await this.browser.run(command) : await this.tools.executor.execute(command);
          results.push('output' in result ? result.output : `${result.stdout}\n${result.stderr}`.trim());
        }
        if (action.kind === 'write_file') {
          const result = this.tools.executor.writeFile(action.groups[0] ?? '', action.groups[1] ?? '');
          results.push(result.success ? result.stdout : result.stderr);
        }
        if (action.kind === 'finding') {
          const finding = { severity: action.groups[0] ?? 'info', title: action.groups[1] ?? 'Finding', description: action.groups[2] ?? '', agentId: this.agentId };
          this.findings.push(finding);
          results.push(`Finding recorded: ${finding.title}`);
        }
      }
      this.memory.addMessage('user', `[Action Results]\n${results.join('\n\n')}`);
    }
    this.status = 'completed';
    return { agentId: this.agentId, status: this.status, findings: this.findings, report };
  }
}

export class ReconAgent extends BaseAgent { protected buildAgentPrompt(): string { return `You are CYRAX recon agent ${this.agentId}. Enumerate exposed assets, services, DNS, and web surface. Use authorized, in-scope actions only.`; } }
export class ExploitAgent extends BaseAgent { protected buildAgentPrompt(): string { return `You are CYRAX exploit agent ${this.agentId}. Validate vulnerabilities safely, avoid destructive impact, and report evidence.`; } }
export class PostExploitAgent extends BaseAgent { protected buildAgentPrompt(): string { return `You are CYRAX post-exploitation agent ${this.agentId}. Map access, privilege, and evidence without exfiltrating sensitive data.`; } }
export class WebAgent extends BaseAgent { protected buildAgentPrompt(): string { return `You are CYRAX web agent ${this.agentId}. Test web flows, auth, browser state, injection points, and client-side behavior.`; } }
export class CloudAgent extends BaseAgent { protected buildAgentPrompt(): string { return `You are CYRAX cloud agent ${this.agentId}. Inspect cloud posture and exposed service configuration within scope.`; } }
export class ActiveDirectoryAgent extends BaseAgent { protected buildAgentPrompt(): string { return `You are CYRAX Active Directory agent ${this.agentId}. Enumerate Windows domains and privilege paths safely.`; } }
export class OSINTAgent extends BaseAgent { protected buildAgentPrompt(): string { return `You are CYRAX OSINT agent ${this.agentId}. Gather passive intelligence and public exposure within scope.`; } }
