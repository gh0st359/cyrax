import { BaseAgent, ReconAgent, ExploitAgent, PostExploitAgent, WebAgent, CloudAgent, ActiveDirectoryAgent, OSINTAgent } from './base.js';
import { ModelManager } from '../models/client.js';
import { ToolRegistry } from '../tools/registry.js';

export type AgentType = 'recon' | 'exploit' | 'post' | 'web' | 'cloud' | 'ad' | 'osint';

export interface AgentStatus {
  id: string;
  type: AgentType;
  task: string;
  status: string;
  iteration: number;
}

export class AgentPool {
  private readonly agents = new Map<string, BaseAgent>();
  private counter = 0;

  constructor(private readonly model: ModelManager, private readonly tools: ToolRegistry) {}

  spawn(type: AgentType, task: string): string {
    const id = `${type}-${++this.counter}`;
    const agent = this.createAgent(id, type, task);
    this.agents.set(id, agent);
    void agent.run().catch(() => { agent.status = 'failed'; });
    return id;
  }

  kill(agentId: string): boolean {
    const agent = this.agents.get(agentId);
    if (!agent) return false;
    agent.status = 'killed';
    return true;
  }

  getStatus(): Record<string, AgentStatus> {
    const status: Record<string, AgentStatus> = {};
    for (const [id, agent] of this.agents) {
      const type = id.split('-')[0] as AgentType;
      status[id] = { id, type, task: agent.task, status: agent.status, iteration: agent.iteration };
    }
    return status;
  }

  private createAgent(id: string, type: AgentType, task: string): BaseAgent {
    if (type === 'recon') return new ReconAgent(id, task, this.model, this.tools);
    if (type === 'exploit') return new ExploitAgent(id, task, this.model, this.tools);
    if (type === 'post') return new PostExploitAgent(id, task, this.model, this.tools);
    if (type === 'web') return new WebAgent(id, task, this.model, this.tools);
    if (type === 'cloud') return new CloudAgent(id, task, this.model, this.tools);
    if (type === 'ad') return new ActiveDirectoryAgent(id, task, this.model, this.tools);
    return new OSINTAgent(id, task, this.model, this.tools);
  }
}
