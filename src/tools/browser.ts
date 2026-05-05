import fs from 'node:fs';
import path from 'node:path';
import { chromium, Browser, BrowserContext, Page } from 'playwright';

export class BrowserResult {
  constructor(
    readonly action: string,
    readonly success: boolean,
    readonly data = '',
    readonly error = '',
    readonly screenshotPath = '',
    readonly url = '',
  ) {}

  get output(): string {
    if (!this.success) return `Browser error: ${this.error}`;
    const parts = [];
    if (this.url) parts.push(`URL: ${this.url}`);
    if (this.data) parts.push(this.data);
    if (this.screenshotPath) parts.push(`Screenshot saved: ${this.screenshotPath}`);
    return parts.join('\n') || 'OK';
  }
}

export const browserCommands = ['open', 'html', 'text', 'links', 'click', 'type', 'screenshot', 'wait', 'close'] as const;

export function isBrowserCommand(command: string): boolean {
  return /^browser\.\w+\(/.test(command.trim());
}

export function browserCommandHasShellOperators(command: string): boolean {
  return /[;&|`$<>]/.test(command.replace(/^browser\.\w+\((.*)\)\s*$/s, '$1'));
}

export function parseBrowserCommand(command: string): { method: string; args: string[] } | null {
  const match = command.trim().match(/^browser\.(\w+)\((.*)\)$/s);
  if (!match?.[1]) return null;
  const rawArgs = match[2] ?? '';
  const args = splitArgs(rawArgs).map(unquote);
  return { method: match[1], args };
}

export function validateBrowserCommand(command: string): [boolean, string] {
  const parsed = parseBrowserCommand(command);
  if (!parsed) return [false, 'Invalid browser command syntax'];
  if (!browserCommands.includes(parsed.method as (typeof browserCommands)[number])) return [false, `Unknown browser command: ${parsed.method}`];
  if (browserCommandHasShellOperators(command)) return [false, 'Browser command arguments must not contain shell operators'];
  return [true, ''];
}

export class BrowserManager {
  private browser: Browser | undefined;
  private context: BrowserContext | undefined;
  private page: Page | undefined;
  private screenshotCounter = 0;
  readonly screenshotsDir: string;

  constructor(private readonly options: { headless?: boolean; workDir?: string } = {}) {
    const workDir = options.workDir ?? '/tmp/cyrax';
    this.screenshotsDir = path.join(workDir, 'screenshots');
    fs.mkdirSync(this.screenshotsDir, { recursive: true });
  }

  async ensureStarted(): Promise<void> {
    if (this.page) return;
    this.browser = await chromium.launch({ headless: this.options.headless ?? true, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
    this.context = await this.browser.newContext({ viewport: { width: 1920, height: 1080 }, ignoreHTTPSErrors: true });
    this.page = await this.context.newPage();
    this.page.setDefaultTimeout(30_000);
  }

  async run(command: string): Promise<BrowserResult> {
    const valid = validateBrowserCommand(command);
    if (!valid[0]) return new BrowserResult(command, false, '', valid[1]);
    const parsed = parseBrowserCommand(command);
    if (!parsed) return new BrowserResult(command, false, '', 'Invalid browser command');
    try {
      await this.ensureStarted();
      const page = this.requirePage();
      if (parsed.method === 'open') {
        await page.goto(parsed.args[0] ?? 'about:blank', { waitUntil: 'domcontentloaded' });
        return new BrowserResult(command, true, 'Opened page', '', '', page.url());
      }
      if (parsed.method === 'html') return new BrowserResult(command, true, await page.content(), '', '', page.url());
      if (parsed.method === 'text') return new BrowserResult(command, true, await page.locator('body').innerText(), '', '', page.url());
      if (parsed.method === 'links') {
        const links = await page.locator('a').evaluateAll((anchors) => anchors.map((a) => {
          const anchor = a as unknown as { innerText: string; href: string };
          return `${anchor.innerText.trim()} -> ${anchor.href}`;
        }).filter(Boolean));
        return new BrowserResult(command, true, links.join('\n'), '', '', page.url());
      }
      if (parsed.method === 'click') {
        await page.locator(parsed.args[0] ?? '').first().click();
        return new BrowserResult(command, true, 'Clicked', '', '', page.url());
      }
      if (parsed.method === 'type') {
        await page.locator(parsed.args[0] ?? '').first().fill(parsed.args[1] ?? '');
        return new BrowserResult(command, true, 'Typed text', '', '', page.url());
      }
      if (parsed.method === 'screenshot') {
        const out = path.join(this.screenshotsDir, `screenshot-${++this.screenshotCounter}.png`);
        await page.screenshot({ path: out, fullPage: true });
        return new BrowserResult(command, true, '', '', out, page.url());
      }
      if (parsed.method === 'wait') {
        await page.waitForTimeout(Number(parsed.args[0] ?? '1000'));
        return new BrowserResult(command, true, 'Waited', '', '', page.url());
      }
      if (parsed.method === 'close') {
        await this.close();
        return new BrowserResult(command, true, 'Closed browser');
      }
      return new BrowserResult(command, false, '', 'Unsupported command');
    } catch (error) {
      return new BrowserResult(command, false, '', error instanceof Error ? error.message : String(error));
    }
  }

  async close(): Promise<void> {
    await this.context?.close().catch(() => undefined);
    await this.browser?.close().catch(() => undefined);
    this.page = undefined;
    this.context = undefined;
    this.browser = undefined;
  }

  private requirePage(): Page {
    if (!this.page) throw new Error('Browser not started');
    return this.page;
  }
}

function splitArgs(raw: string): string[] {
  const args: string[] = [];
  let current = '';
  let quote: string | null = null;
  for (let i = 0; i < raw.length; i += 1) {
    const char = raw[i];
    if (quote) {
      current += char;
      if (char === quote && raw[i - 1] !== '\\') quote = null;
    } else if (char === '"' || char === "'") {
      quote = char;
      current += char;
    } else if (char === ',') {
      args.push(current.trim());
      current = '';
    } else {
      current += char;
    }
  }
  if (current.trim()) args.push(current.trim());
  return args;
}

function unquote(value: string): string {
  const trimmed = value.trim();
  if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) return trimmed.slice(1, -1);
  return trimmed;
}
