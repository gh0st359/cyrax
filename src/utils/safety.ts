import net from 'node:net';
import { URL } from 'node:url';

function ipToBigInt(ip: string): bigint | null {
  if (net.isIP(ip) === 4) {
    return ip.split('.').reduce((acc, octet) => (acc << 8n) + BigInt(Number(octet)), 0n);
  }
  return null;
}

function cidrContains(cidr: string, ip: string): boolean {
  const [base, prefixRaw] = cidr.split('/');
  if (!base || !prefixRaw || net.isIP(base) !== 4 || net.isIP(ip) !== 4) return false;
  const prefix = Number(prefixRaw);
  const baseNum = ipToBigInt(base);
  const ipNum = ipToBigInt(ip);
  if (baseNum === null || ipNum === null || prefix < 0 || prefix > 32) return false;
  const mask = prefix === 0 ? 0n : ((1n << BigInt(prefix)) - 1n) << BigInt(32 - prefix);
  return (baseNum & mask) === (ipNum & mask);
}

export class ScopeEnforcer {
  readonly allowedDomains = new Set<string>();
  readonly allowedWildcardDomains: string[] = [];
  readonly allowedIpRanges: string[] = [];
  readonly allowedIps = new Set<string>();
  readonly passiveIntelHosts = new Set(['crt.sh', 'www.crt.sh']);
  enabled = false;
  private readonly rawTargets: string[] = [];

  constructor(targets: string[] = []) {
    if (targets.length > 0) {
      this.parseTargets(targets);
      this.enabled = true;
    }
  }

  private parseTargets(targets: string[]): void {
    this.rawTargets.push(...targets);
    for (const raw of targets) {
      const target = raw.trim();
      if (!target) continue;
      if (target.includes('/') && !target.startsWith('http') && target.split('/').length === 2) {
        this.allowedIpRanges.push(target);
        continue;
      }
      if (net.isIP(target)) {
        this.allowedIps.add(target);
        continue;
      }
      if (target.includes('://')) {
        try {
          this.addDomain(new URL(target).hostname);
          continue;
        } catch {
          continue;
        }
      }
      if (target.startsWith('*.')) {
        const base = target.slice(2).toLowerCase();
        this.allowedWildcardDomains.push(base);
        this.allowedDomains.add(base);
        continue;
      }
      this.addDomain(target);
    }
  }

  private addDomain(domain: string): void {
    const normalized = domain.toLowerCase().trim();
    if (net.isIP(normalized)) this.allowedIps.add(normalized);
    else this.allowedDomains.add(normalized);
  }

  isInScope(urlOrHost: string): boolean {
    if (!this.enabled) return true;
    const host = this.extractHost(urlOrHost);
    if (!host) return true;
    if (['localhost', '127.0.0.1', '::1', '0.0.0.0'].includes(host)) return true;
    if (net.isIP(host)) return this.allowedIps.has(host) || this.allowedIpRanges.some((range) => cidrContains(range, host));
    return this.isDomainAllowed(host);
  }

  checkCommand(command: string): [boolean, string] {
    if (!this.enabled) return [true, ''];
    const targets = this.extractTargets(command);
    for (const target of targets) {
      if (this.passiveIntelHosts.has(this.extractHost(target)) && this.mentionsAllowedTarget(command)) continue;
      if (!this.isInScope(target)) return [false, `Target '${target}' is NOT in your authorized scope. Allowed scope: ${this.getScopeDescription()}`];
    }
    return [true, ''];
  }

  getScopeDescription(): string {
    if (!this.enabled) return 'unrestricted (no explicit scope configured)';
    return [...this.allowedDomains, ...this.allowedWildcardDomains.map((d) => `*.${d}`), ...this.allowedIps, ...this.allowedIpRanges].join(', ');
  }

  private isDomainAllowed(domain: string): boolean {
    const normalized = domain.toLowerCase();
    if (this.allowedDomains.has(normalized)) return true;
    if (normalized.startsWith('www.') && this.allowedDomains.has(normalized.slice(4))) return true;
    return this.allowedWildcardDomains.some((base) => normalized === base || normalized.endsWith(`.${base}`));
  }

  private extractHost(value: string): string {
    const trimmed = value.trim().replace(/[),.;'"`]+$/g, '');
    try {
      if (trimmed.includes('://')) return new URL(trimmed).hostname.toLowerCase();
    } catch {
      return '';
    }
    return trimmed.split(':')[0]?.toLowerCase() ?? '';
  }

  private extractTargets(text: string): string[] {
    const urls = text.match(/https?:\/\/[^\s'"`<>]+/g) ?? [];
    const ips = text.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g) ?? [];
    const domains = text.match(/\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b/g) ?? [];
    return [...new Set([...urls, ...ips, ...domains])];
  }

  private mentionsAllowedTarget(command: string): boolean {
    const lower = command.toLowerCase();
    return [...this.allowedDomains].some((domain) => lower.includes(domain));
  }
}

export type PermissionMode = 'interactive' | 'auto' | 'plan' | 'deny';

export class PermissionGate {
  autoApprove: boolean;
  policyMode: PermissionMode = 'interactive';
  private readonly approvedTypes = new Set<string>();
  private interrupted = false;

  constructor(autoApprove = false) {
    this.autoApprove = autoApprove;
    if (autoApprove) this.policyMode = 'auto';
  }

  classifyAction(command: string): string {
    const lower = command.toLowerCase();
    if (/\b(scp|rsync|curl\s+-t|aws\s+s3\s+cp)\b/.test(lower)) return 'data_exfil';
    if (/\b(sqlmap|hydra|nmap|nuclei|ffuf|gobuster|metasploit|msfconsole)\b/.test(lower)) return 'attack_payload';
    return 'shell_command';
  }

  check(command: string): [boolean, string] {
    if (this.interrupted) return [false, 'Session interrupted'];
    const actionType = this.classifyAction(command);
    if (actionType === 'data_exfil') return [false, 'Action denied by policy: data exfiltration requires explicit manual handling'];
    if (this.autoApprove || this.policyMode === 'auto') return [true, ''];
    if (this.policyMode === 'plan') return [false, 'Plan mode is active; command execution is disabled'];
    if (this.approvedTypes.has(actionType)) return [true, ''];
    if (actionType === 'shell_command') {
      this.approvedTypes.add(actionType);
      return [true, ''];
    }
    return [false, `Permission required for ${actionType}: ${command}`];
  }

  autoApproveAll(): void {
    this.autoApprove = true;
    this.policyMode = 'auto';
  }

  setMode(mode: PermissionMode): void {
    this.policyMode = mode;
    this.autoApprove = mode === 'auto';
  }

  setInterrupt(): void { this.interrupted = true; }
  clearInterrupt(): void { this.interrupted = false; }
}
