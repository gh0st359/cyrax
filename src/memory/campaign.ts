export interface AccessLevel {
  target: string;
  user: string;
  level: string;
  method: string;
  timestamp: string;
  active: boolean;
}

export interface AttackPathStep {
  stage: string;
  target: string;
  technique: string;
  result: string;
  agentId: string;
  timestamp: string;
}

export class CampaignState {
  name = '';
  target = '';
  objective = '';
  startTime = new Date().toISOString();
  status: 'initialized' | 'active' | 'paused' | 'completed' = 'initialized';
  accessLevels: AccessLevel[] = [];
  attackPath: AttackPathStep[] = [];
  compromisedHosts: string[] = [];
  discoveredNetworks: string[] = [];
  activeAgents: Record<string, Record<string, unknown>> = {};
  notes: string[] = [];

  setTarget(target: string, objective = ''): void {
    this.target = target;
    this.objective = objective;
    this.status = 'active';
  }

  addAccess(target: string, user: string, level: string, method: string): void {
    this.accessLevels.push({ target, user, level, method, timestamp: new Date().toISOString(), active: true });
    if (!this.compromisedHosts.includes(target)) this.compromisedHosts.push(target);
  }

  addAttackStep(stage: string, target: string, technique: string, result: string, agentId = ''): void {
    this.attackPath.push({ stage, target, technique, result, agentId, timestamp: new Date().toISOString() });
  }

  registerAgent(agentId: string, agentType: string, task: string, pid = 0): void {
    this.activeAgents[agentId] = { type: agentType, task, status: 'active', started: new Date().toISOString(), pid, lastHeartbeat: Date.now() / 1000 };
  }

  updateAgentStatus(agentId: string, status: string): void {
    if (this.activeAgents[agentId]) this.activeAgents[agentId].status = status;
  }

  summary(): string {
    return [
      `Target: ${this.target || 'not set'}`,
      `Objective: ${this.objective || 'not set'}`,
      `Status: ${this.status}`,
      `Compromised hosts: ${this.compromisedHosts.join(', ') || 'none'}`,
      `Attack path steps: ${this.attackPath.length}`,
      `Active agents: ${Object.keys(this.activeAgents).length}`,
    ].join('\n');
  }
}
