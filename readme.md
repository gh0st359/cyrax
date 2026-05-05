# CYRAX - Autonomous AI Red Team Operator

An AI-powered autonomous red team operator that thinks, reasons, and operates like an elite pentester through natural conversation.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/gh0st359/cyrax-private.git
cd cyrax-private

# Install locally
pip3 install -e .

# Start CYRAX
cyrax
```

### Installable Package (Optional)

If you prefer the TypeScript backend while developing:

```bash
npm install
npm run build
npm link

# Then run the same single command:
cyrax
```

### Environment Variable Shortcut

```bash
# Set your API key and skip setup entirely
export ANTHROPIC_API_KEY="your-key-here"
cyrax

# xAI/Grok also works out of the box:
export GROK_API_KEY="your-key-here"
export GROK_BASE_URL="https://api.x.ai/v1"
export GROK_PRIMARY_MODEL="grok-4.3"
cyrax
```

## CLI

CYRAX starts like Claude Code: type `cyrax`. That opens the premium interactive operator by default. Subcommands exist only as secondary utilities.

```bash
cyrax                                           # Start interactive operator
cyrax "scan example.com" --auto                 # One-shot with auto permissions
cyrax --scope 10.0.0.0/24                       # Pre-set target scope
cyrax --add-dir /path/to/local/project          # Add local directory to workspace
cyrax --permission-mode plan                    # Start in plan mode
cyrax status --show-config                      # Show resolved config
cyrax tools --available                         # List installed tools
cyrax preflight                                 # Check environment readiness
```

Top-level flags such as `--setup`, `--campaign`, `--scope`, `--auto`, and one-shot prompt execution continue to work for backwards compatibility. The old Textual TUI is optional; the default `cyrax` experience is the maintained premium terminal operator.

## How It Works

Talk to CYRAX like you're talking to a senior pentester:

```
cyrax › Ready.

╭─ user
╰─ I need you to test example-company.com

╭─ cyrax thinking
│ Let me start by mapping their attack surface...
╰─
● run nmap -sV example-company.com
  ⎿ 22/tcp open ssh

cyrax › Found 127 subdomains, 3 interesting targets:
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
cyrax.py                    # installable `cyrax` entry point and operator loop
agents/                     # Recon, exploit, post, AD, web, cloud, OSINT agents
models/                     # Anthropic, OpenAI/xAI/custom, Google, Ollama clients
tools/                      # Shell executor, tool registry, browser automation
memory/                     # Conversation, campaign, mission, knowledge stores
utils/                      # Safety/scope, action parsing, display, logging
config/                     # YAML config and orchestrator prompt
src/                        # TypeScript backend work-in-progress
```

## Interactive Commands

| Command          | Description                                    |
|-----------------|------------------------------------------------|
| `/status`       | Show campaign status                           |
| `/config`       | Show runtime config (redacted)                 |
| `/model [name]` | Show or switch model name                      |
| `/mode [mode]`  | Show or switch permission mode (auto/interactive/plan) |
| `/scope [target]`| Switch target scope (resets previous)          |
| `/add-dir <path>`| Add a directory to workspace scope             |
| `/plan`         | Enter plan mode — analyze before executing     |
| `/auto`         | Enable fully autonomous permissions            |
| `/approve <cat>`| Pre-approve an action category                 |
| `/compact [n]`  | Summarize older context (keep last n messages) |
| `/clear`        | Clear conversation context                     |
| `/agents`       | List active agents                             |
| `/findings`     | Show all security findings                     |
| `/creds`        | Show discovered credentials                    |
| `/hosts`        | Show discovered hosts                          |
| `/usage`        | Show model token usage                         |
| `/export`       | Export findings report                         |
| `/help`         | Show available commands                        |
| `/exit`         | Exit CYRAX                                     |

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
