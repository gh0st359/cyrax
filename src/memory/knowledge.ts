import fs from 'node:fs';
import path from 'node:path';
import { Finding } from '../types/common.js';

interface KnowledgeFile {
  knowledge: Record<string, Record<string, unknown>>;
  findings: Finding[];
}

export class KnowledgeBase {
  private readonly filePath: string;
  private data: KnowledgeFile;

  constructor(dbPath = 'data/cyrax.db') {
    this.filePath = dbPath.endsWith('.json') ? dbPath : `${dbPath}.json`;
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    this.data = this.load();
  }

  store(category: string, key: string, value: Record<string, unknown>): void {
    this.data.knowledge[category] ??= {};
    this.data.knowledge[category][key] = { ...value, stored_at: new Date().toISOString() };
    this.save();
  }

  retrieve(category: string, key: string): Record<string, unknown> | undefined {
    return this.data.knowledge[category]?.[key] as Record<string, unknown> | undefined;
  }

  listCategory(category: string): Record<string, unknown>[] {
    return Object.values(this.data.knowledge[category] ?? {}) as Record<string, unknown>[];
  }

  addFinding(finding: Finding): void {
    this.data.findings.push({ ...finding, createdAt: finding.createdAt ?? new Date().toISOString() });
    this.save();
  }

  listFindings(): Finding[] {
    return [...this.data.findings];
  }

  getSummary(): string {
    const categories = Object.keys(this.data.knowledge);
    const findings = this.data.findings.slice(-5).map((f) => `- [${f.severity}] ${f.title}: ${f.description}`).join('\n');
    return `Knowledge categories: ${categories.join(', ') || 'none'}\nRecent findings:\n${findings || 'none'}`;
  }

  private load(): KnowledgeFile {
    if (!fs.existsSync(this.filePath)) return { knowledge: {}, findings: [] };
    const parsed = JSON.parse(fs.readFileSync(this.filePath, 'utf8')) as Partial<KnowledgeFile>;
    return { knowledge: parsed.knowledge ?? {}, findings: parsed.findings ?? [] };
  }

  private save(): void {
    fs.writeFileSync(this.filePath, JSON.stringify(this.data, null, 2), 'utf8');
  }
}
