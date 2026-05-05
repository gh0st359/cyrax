# CYRAX - Autonomous AI Red Team Operator

An AI-powered autonomous red team operator that thinks, reasons, and operates like an elite pentester through natural conversation.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/gh0st359/cyrax-private.git
cd cyrax-private

# Install dependencies (use python -m pip to avoid path mismatches)
python -m pip install -r requirements.txt

# Install browser automation (optional, for web testing features)
python -m pip install playwright && playwright install chromium

# First time? Run setup to configure your model provider
python cyrax.py init

# After that, just type:
python cyrax.py
```

### Installable Package (Optional)

If you prefer a system-wide `cyrax` command:

```bash
pip install .

# Then just run:
cyrax init      # first time
cyrax            # after that
```

### Environment Variable Shortcut

```bash
# Set your API key and skip setup entirely
export ANTHROPIC_API_KEY="your-key-here"
python cyrax.py
```

## CLI

CYRAX now uses an assistant-ui-inspired command layout for setup, discovery,
and updates while preserving the original `cyrax` chat entry point.

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
cyrax.py                    # Main orchestrator & entry point
agents/
  base_agent.py             # Base agent with autonomous execution loop
  recon_agent.py            # Reconnaissance specialist
  exploit_agent.py          # Exploitation specialist
  post_exploit_agent.py     # Post-exploitation specialist
  ad_agent.py               # Active Directory specialist
  web_agent.py              # Web application specialist
  cloud_agent.py            # Cloud infrastructure specialist
  osint_agent.py            # OSINT specialist
models/
  model_manager.py          # Unified model interface
  api_providers.py          # OpenAI, Anthropic, Google, xAI clients
  local_providers.py        # Ollama, LM Studio, vLLM clients
tools/
  executor.py               # Command execution engine
  tool_registry.py          # Tool catalog with 60+ tools
  browser.py                # Playwright browser automation (33 commands)
memory/
  conversation.py           # Conversation history management
  knowledge_base.py         # Persistent findings/credentials store
  campaign_state.py         # Engagement state tracking
utils/
  display.py                # Rich terminal UI
  logging.py                # Engagement logging
config/
  config.example.yaml       # Example configuration
  prompts/                  # System prompt templates
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

Copy `config/config.example.yaml` to `config/config.yaml` and configure:

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

## Pre-Release Checklist

Before cutting a release, run this checklist:

- [ ] Run full test suite: `pytest`
- [ ] Run lint checks (project-standard linter/formatters)
- [ ] Execute a smoke campaign against an authorized test target
- [ ] Export and archive the campaign report/artifacts for review
