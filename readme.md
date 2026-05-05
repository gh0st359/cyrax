# CYRAX - Autonomous AI Red Team Operator

An AI-powered autonomous red team operator that thinks, reasons, and operates like an elite pentester through natural conversation.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/gh0st359/cyrax-private.git
cd cyrax-private

# Install the TypeScript backend dependencies
npm install

# Build the CLI
npm run build

# Optional browser automation support
npx playwright install chromium

# First time? Run setup to configure your model provider
npm run dev -- init

# After that, start CYRAX
npm run dev -- chat
```

### Installable Package (Optional)

If you prefer a local `cyrax` command while developing:

```bash
npm link

# Then just run:
cyrax init      # first time
cyrax chat      # after that
```

### Environment Variable Shortcut

```bash
# Set your API key and skip setup entirely
export ANTHROPIC_API_KEY="your-key-here"
npm run dev -- chat
```

## CLI

CYRAX now runs on a TypeScript/Node backend with an assistant-ui-inspired command layout for setup, discovery, and responsive Claude/Codex-style chat while preserving the original `cyrax` operator entry point.

```bash
cyrax init
cyrax configure --provider anthropic --api-key-env ANTHROPIC_API_KEY
cyrax status --show-config
cyrax tools --available
cyrax preflight
cyrax chat --scope example-company.com --campaign example
cyrax chat "recon example-company.com" --print --auto
```

Top-level chat flags such as `--setup`, `--campaign`, `--scope`, `--auto`,
`--tui`, `--simple`, and one-shot prompt execution continue to work for
backwards compatibility.

## How It Works

Talk to CYRAX like you're talking to a senior pentester:

```
CYRAX: Ready. What's the target?

cyrax › I need you to test example-company.com

CYRAX: Let me start by mapping their attack surface...
● RUN nmap -sV example-company.com
● SHELL nmap -sV example-company.com 22/tcp open ssh

CYRAX: Found 127 subdomains, 3 interesting targets:
- jenkins.example-company.com (potentially unauthenticated)
- dev-api.example-company.com (Swagger UI exposed)
- vpn.example-company.com (outdated firmware)

Which should I pursue first?
```

## Supported Model Providers

### API Models
- **Anthropic**: Claude Opus 4, Claude Sonnet 4.5, Claude Haiku 3.5
- **OpenAI**: GPT-4o, o1, o3-mini
- **Google**: Gemini 2.0 Flash, Gemini 2.5 Pro
- **xAI**: Grok-2, Grok-3
- **Custom**: Any OpenAI-compatible endpoint

### Local Models
- **Ollama**: llama3.1, deepseek-coder, qwen, mixtral, etc.
- **LM Studio**: Any locally-hosted model
- **vLLM**: Self-hosted models

## Architecture

```
src/cli.ts                  # Commander-based CLI entry point
src/orchestrator.ts         # Main operator loop and action execution
src/agents/                 # Recon, exploit, post, AD, web, cloud, OSINT agents
src/models/                 # Anthropic, OpenAI/xAI/custom, Google, Ollama clients
src/tools/                  # Shell executor, tool registry, Playwright browser automation
src/memory/                 # Conversation, campaign, mission, knowledge stores
src/utils/                  # Safety/scope, action parsing, display, logging
src/config/                 # Typed config defaults and YAML loader
tests/ts/                   # Vitest coverage for the TypeScript backend
```

## Interactive Commands

| Command      | Description                    |
|-------------|--------------------------------|
| `/status`   | Show campaign status           |
| `/config`   | Show runtime config (redacted) |
| `/model`    | Show or switch model name      |
| `/mode`     | Show or switch permission mode |
| `/scope`    | Show or update target scope    |
| `/auto`     | Enable autonomous permissions  |
| `/compact`  | Summarize older context        |
| `/clear`    | Clear conversation context     |
| `/agents`   | List active agents             |
| `/findings` | Show all security findings     |
| `/creds`    | Show discovered credentials    |
| `/hosts`    | Show discovered hosts          |
| `/usage`    | Show model token usage         |
| `/help`     | Show available commands        |
| `/exit`     | Exit CYRAX                     |

## Configuration

Copy `config/config.example.yaml` to `config/config.yaml`, or run `cyrax configure`, and configure:

- **Model provider and credentials**
- **Tool execution settings** (timeouts, working directory)
- **Memory settings** (database path, history limits)
- **Logging settings** (log directory, verbosity)
- **Display settings** (reasoning visibility, themes)

## Multi-Agent System

CYRAX spawns specialized sub-agents for complex tasks:

- **RECON-XX**: Subdomain enumeration, port scanning, OSINT
- **EXPLOIT-XX**: Vulnerability discovery and exploitation
- **POST-XX**: Privilege escalation, credential harvesting
- **AD-XX**: Active Directory attacks, Kerberos abuse
- **WEB-XX**: Web application testing, OWASP Top 10
- **CLOUD-XX**: AWS/Azure/GCP enumeration and exploitation
- **OSINT-XX**: Employee discovery, breach data, social engineering

All agents are powered by the same model with role-specific prompts. The framework handles agent coordination, tool execution, and memory management.

## Legal Notice

This tool is intended for authorized security testing only. Always obtain proper written authorization before testing any system. Unauthorized access to computer systems is illegal.
## TypeScript Backend

The `upgrade` branch migrates the backend runtime to TypeScript. The Python implementation remains in-tree during the transition for compatibility and regression reference, while local validation covers both the existing Python tests and the new Vitest/TypeScript suite.

Useful developer commands:

```bash
npm run typecheck
npm test
npm run build
npm run dev -- status --show-config
npm run dev -- tools --available
```
