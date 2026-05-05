import { execFileSync } from 'node:child_process';
import { ToolExecutor } from './executor.js';

export interface ToolDefinition {
  name: string;
  description: string;
  category: string;
  commandTemplate: string;
  available: boolean;
  examples: string[];
}

export class ToolRegistry {
  readonly executor: ToolExecutor;
  readonly tools = new Map<string, ToolDefinition>();

  constructor(executor = new ToolExecutor()) {
    this.executor = executor;
    this.registerDefaults();
    this.checkAvailability();
  }

  list(category?: string): ToolDefinition[] {
    return [...this.tools.values()].filter((tool) => !category || tool.category === category);
  }

  getAvailableToolsSummary(): string {
    return this.list().map((tool) => `- ${tool.name}: ${tool.description} (${tool.available ? 'available' : 'not installed'})`).join('\n');
  }

  private register(name: string, description: string, category: string, commandTemplate: string, examples: string[] = []): void {
    this.tools.set(name, { name, description, category, commandTemplate, examples, available: false });
  }

  private registerDefaults(): void {
    this.register('nmap', 'Network scanner for port scanning and service detection', 'recon', 'nmap {args}', ['nmap -sV -sC target.com']);
    this.register('subfinder', 'Fast passive subdomain discovery tool', 'recon', 'subfinder {args}');
    this.register('amass', 'Attack surface mapping and asset discovery', 'recon', 'amass {args}');
    this.register('whois', 'WHOIS domain lookup', 'recon', 'whois {args}');
    this.register('dig', 'DNS lookup utility', 'recon', 'dig {args}');
    this.register('nikto', 'Web server scanner for vulnerabilities', 'web', 'nikto {args}');
    this.register('gobuster', 'Directory/file and DNS brute forcing', 'web', 'gobuster {args}');
    this.register('ffuf', 'Fast web fuzzer', 'web', 'ffuf {args}');
    this.register('sqlmap', 'Automatic SQL injection testing', 'web', 'sqlmap {args}');
    this.register('nuclei', 'Fast vulnerability scanner using templates', 'web', 'nuclei {args}');
    this.register('dalfox', 'XSS scanning and parameter analysis', 'web', 'dalfox {args}');
    this.register('hydra', 'Network login brute forcing', 'exploit', 'hydra {args}');
    this.register('msfconsole', 'Metasploit Framework console', 'exploit', 'msfconsole {args}');
    this.register('curl', 'HTTP client', 'utility', 'curl {args}');
    this.register('python', 'Python interpreter for scripts', 'utility', 'python {args}');
    this.register('node', 'Node.js runtime for scripts', 'utility', 'node {args}');
  }

  private checkAvailability(): void {
    for (const tool of this.tools.values()) {
      tool.available = commandExists(tool.name);
    }
  }
}

function commandExists(command: string): boolean {
  try {
    const checker = process.platform === 'win32' ? 'where' : 'which';
    execFileSync(checker, [command], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}
