export class MissionMemory {
  facts: string[] = [];

  extractFromResponse(response: string): void {
    for (const line of response.split(/\r?\n/)) {
      if (/found|identified|credential|vulnerab|token|endpoint|host/i.test(line) && line.trim().length > 12) {
        this.addFact(line.trim());
      }
    }
  }

  addFact(fact: string): void {
    if (!this.facts.includes(fact)) this.facts.push(fact);
    if (this.facts.length > 100) this.facts = this.facts.slice(-100);
  }

  buildContextBlock(): string {
    return this.facts.length ? this.facts.map((fact) => `- ${fact}`).join('\n') : 'No mission facts recorded yet.';
  }

  snapshot(): Record<string, unknown> {
    return { facts: [...this.facts] };
  }
}
