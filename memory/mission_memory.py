"""
CYRAX Mission Memory
Persistent, tiered memory system that maintains mission context across
conversation turns and sub-agent boundaries.

Inspired by memU's hierarchical architecture:
- Core Memory (never evicted): target, scope, engagement parameters
- Working Memory (session-scoped): credentials, vulns, browser state, progress
- Episodic Memory (summarized): facts extracted from conversation history

This prevents context drift that causes agents to "forget" their mission
(e.g., a post-exploitation agent running whoami on the operator's machine
instead of the remote target).
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path


def _lock_file(f, exclusive: bool = True):
    """Cross-platform file locking."""
    if os.name == "nt":
        import msvcrt
        # On Windows, lock the first byte (msvcrt requires length)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK if exclusive else msvcrt.LK_NBRLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)


def _unlock_file(f):
    """Cross-platform file unlocking."""
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class MissionMemory:
    """
    Three-tier memory system for persistent mission awareness.

    Tier 1 - Core Memory (immutable per engagement):
        Target, scope, objective, engagement type, operator platform.
        Set once and never evicted.

    Tier 2 - Working Memory (updated continuously):
        Active credentials, confirmed vulns, compromised hosts,
        current browser state, attack path progress.

    Tier 3 - Episodic Memory (accumulated from conversations):
        Key decisions, extracted facts, failed approaches.
        Grows over time but stays bounded.
    """

    def __init__(self):
        # Tier 1: Core (immutable for the engagement)
        self.core = {
            "target": "",
            "target_type": "",  # "web_app", "network", "host", "cloud", "ad"
            "scope": "",
            "objective": "",
            "engagement_rules": [],
            "operator_platform": "",  # The platform CYRAX is running on
        }

        # Tier 2: Working (updated continuously)
        self.working = {
            "credentials": [],       # [{"user", "password", "target", "source"}]
            "confirmed_vulns": [],   # [{"type", "url", "evidence", "timestamp"}]
            "compromised_hosts": [],  # ["host1", "host2"]
            "browser_state": {},     # {"url", "cookies", "authenticated"}
            "attack_progress": [],   # ["Logged in with admin:password", ...]
            "active_agents": {},     # {agent_id: {"type", "task", "status"}}
            "files_created": [],     # ["/tmp/cyrax/exploit.py", ...]
            "key_discoveries": [],   # Free-form important facts
        }

        # Tier 3: Episodic (accumulated summaries)
        self.episodic = {
            "session_facts": [],     # Extracted from conversation turns
            "decisions_made": [],    # Key strategic decisions
            "failed_approaches": [],  # What didn't work (avoid repetition)
        }

    # === Core Memory ===

    def set_core(self, target: str, target_type: str = "", scope: str = "",
                 objective: str = "", operator_platform: str = ""):
        """Set core mission parameters (called once at engagement start)."""
        self.core["target"] = target
        self.core["target_type"] = target_type or self.detect_target_type(target)
        self.core["scope"] = scope
        self.core["objective"] = objective
        self.core["operator_platform"] = operator_platform

    @staticmethod
    def detect_target_type(target: str) -> str:
        """Infer engagement type from the target string."""
        t = target.lower()
        if any(x in t for x in (
            "http://", "https://", "/dvwa", "/wp-",
            ".php", ".asp", ".aspx", ".jsp"
        )):
            return "web_app"
        # CIDR notation
        if "/" in t:
            parts = t.split("/")
            if len(parts) == 2 and parts[1].isdigit():
                return "network"
        # Pure IP
        parts = t.replace(".", "").replace(":", "")
        if parts.isdigit():
            return "host"
        # Domain with common web indicators
        if "." in t:
            return "web_app"
        return "host"

    # === Working Memory ===

    def add_credential(self, user: str, password: str = "",
                       target: str = "", source: str = ""):
        """Record a discovered credential (deduplicates)."""
        for existing in self.working["credentials"]:
            if existing["user"] == user and existing["target"] == target:
                existing["password"] = password or existing["password"]
                existing["source"] = source or existing["source"]
                return
        self.working["credentials"].append({
            "user": user, "password": password,
            "target": target, "source": source,
        })

    def add_vuln(self, vuln_type: str, url: str = "", evidence: str = ""):
        """Record a confirmed vulnerability."""
        self.working["confirmed_vulns"].append({
            "type": vuln_type, "url": url,
            "evidence": evidence[:200],
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        })

    def add_compromised_host(self, host: str):
        """Record a compromised host."""
        if host not in self.working["compromised_hosts"]:
            self.working["compromised_hosts"].append(host)

    def update_browser_state(self, url: str = "", cookies: list = None,
                             authenticated: bool = False):
        """Update current browser state snapshot."""
        self.working["browser_state"] = {
            "url": url,
            "cookies": cookies or [],
            "authenticated": authenticated,
        }

    def add_discovery(self, fact: str):
        """Add a key discovery to working memory."""
        if fact not in self.working["key_discoveries"]:
            self.working["key_discoveries"].append(fact)
            if len(self.working["key_discoveries"]) > 30:
                self.working["key_discoveries"] = self.working["key_discoveries"][-30:]

    def update_attack_progress(self, step: str):
        """Add an attack progress step."""
        self.working["attack_progress"].append(step)
        if len(self.working["attack_progress"]) > 30:
            self.working["attack_progress"] = self.working["attack_progress"][-30:]

    def add_file(self, path: str):
        """Record a file created during the engagement."""
        if path not in self.working["files_created"]:
            self.working["files_created"].append(path)

    def register_agent(self, agent_id: str, agent_type: str, task: str):
        """Register an active sub-agent."""
        self.working["active_agents"][agent_id] = {
            "type": agent_type, "task": task, "status": "active",
        }

    def update_agent(self, agent_id: str, status: str):
        """Update agent status."""
        if agent_id in self.working["active_agents"]:
            self.working["active_agents"][agent_id]["status"] = status

    # === Episodic Memory ===

    def add_failed_approach(self, approach: str):
        """Record a failed approach to prevent repetition."""
        if approach not in self.episodic["failed_approaches"]:
            self.episodic["failed_approaches"].append(approach)
            if len(self.episodic["failed_approaches"]) > 20:
                self.episodic["failed_approaches"] = self.episodic["failed_approaches"][-20:]

    def add_session_fact(self, fact: str):
        """Add a fact extracted from conversation."""
        if fact not in self.episodic["session_facts"]:
            self.episodic["session_facts"].append(fact)
            if len(self.episodic["session_facts"]) > 50:
                self.episodic["session_facts"] = self.episodic["session_facts"][-50:]

    def add_decision(self, decision: str):
        """Record a key strategic decision."""
        self.episodic["decisions_made"].append({
            "decision": decision,
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        })
        if len(self.episodic["decisions_made"]) > 20:
            self.episodic["decisions_made"] = self.episodic["decisions_made"][-20:]

    # === Prompt Builders ===

    def build_context_block(self) -> str:
        """Build the persistent memory block for the orchestrator's system prompt."""
        lines = ["=== MISSION MEMORY (persistent — read this every turn) ==="]

        # Core
        lines.append(f"TARGET: {self.core['target'] or 'Not set'}")
        lines.append(f"TYPE: {self.core['target_type'] or 'Unknown'}")
        lines.append(f"SCOPE: {self.core['scope'] or 'Not configured'}")
        if self.core["objective"]:
            lines.append(f"OBJECTIVE: {self.core['objective']}")

        # Platform awareness with safety warning
        if self.core["operator_platform"]:
            lines.append(f"OPERATOR PLATFORM: {self.core['operator_platform']}")
        if self.core["target_type"] == "web_app":
            lines.append(
                "IMPORTANT: Shell commands (whoami, ipconfig, systeminfo, etc.) "
                "execute on the OPERATOR machine, NOT on the target web app. "
                "Use browser commands and HTTP requests to interact with the target. "
                "Only use shell commands for running tools (nmap, sqlmap, python) "
                "that connect TO the target."
            )

        # Working memory
        if self.working["credentials"]:
            lines.append(f"\nCREDENTIALS ({len(self.working['credentials'])}):")
            for c in self.working["credentials"]:
                lines.append(
                    f"  {c['user']}:{c['password'] or '***'} "
                    f"@ {c['target'] or 'N/A'} (src: {c['source'] or '?'})"
                )

        if self.working["confirmed_vulns"]:
            lines.append(f"\nCONFIRMED VULNS ({len(self.working['confirmed_vulns'])}):")
            for v in self.working["confirmed_vulns"][-10:]:
                lines.append(f"  [{v['type']}] {v['url']} ({v['timestamp']})")

        if self.working["compromised_hosts"]:
            lines.append(f"\nCOMPROMISED: {', '.join(self.working['compromised_hosts'])}")

        bs = self.working["browser_state"]
        if bs.get("url"):
            lines.append(f"\nBROWSER: {bs['url']}")
            if bs.get("authenticated"):
                lines.append("  Authenticated: Yes")
            if bs.get("cookies"):
                cookie_str = "; ".join(
                    f"{c['name']}={c['value']}" for c in bs["cookies"][:8]
                )
                lines.append(f"  Cookies: {cookie_str}")

        if self.working["attack_progress"]:
            lines.append("\nPROGRESS:")
            for step in self.working["attack_progress"][-10:]:
                lines.append(f"  - {step}")

        if self.working["key_discoveries"]:
            lines.append("\nDISCOVERIES:")
            for d in self.working["key_discoveries"][-10:]:
                lines.append(f"  - {d}")

        # Episodic
        if self.episodic["failed_approaches"]:
            lines.append("\nFAILED APPROACHES (do NOT repeat):")
            for f in self.episodic["failed_approaches"][-10:]:
                lines.append(f"  - {f}")

        lines.append("=== END MISSION MEMORY ===")
        return "\n".join(lines)

    def build_agent_briefing(self, agent_type: str, task: str) -> str:
        """
        Build a context briefing for a sub-agent.
        This gives the agent everything it needs to understand the mission
        without having to re-discover it all from scratch.
        """
        lines = ["=== MISSION BRIEFING FROM ORCHESTRATOR ==="]

        # Core context
        lines.append(f"Target: {self.core['target']}")
        lines.append(f"Target type: {self.core['target_type']}")
        lines.append(f"Scope: {self.core['scope']}")
        if self.core["objective"]:
            lines.append(f"Objective: {self.core['objective']}")

        # Platform-specific warnings
        if self.core["operator_platform"]:
            lines.append(f"Operator platform: {self.core['operator_platform']}")

        if self.core["target_type"] == "web_app":
            if agent_type == "post":
                lines.append(
                    "\nCRITICAL: This is a REMOTE web application test. "
                    "Shell commands (whoami, ipconfig, systeminfo, tasklist, hostname, "
                    "ifconfig, id, uname, cat /etc/passwd, net user) execute on the "
                    "OPERATOR's local machine, NOT on the target server. "
                    "Running these commands would leak the operator's own system info. "
                    "For web app post-exploitation:\n"
                    "  - Use SQL injection to extract database contents\n"
                    "  - Use file inclusion/upload to read server files\n"
                    "  - Use browser commands to explore authenticated areas\n"
                    "  - Use HTTP requests to probe internal APIs\n"
                    "  - Only run shell commands for tools that connect TO the target"
                )
            elif agent_type == "exploit":
                lines.append(
                    "\nCRITICAL: This is a REMOTE web application. "
                    "The browser is already authenticated and navigated. "
                    "Use browser commands (browser.goto, browser.fill, etc.) and "
                    "HTTP requests (httpx/curl with cookies) to interact with the target. "
                    "Shell commands run locally — use them only for tools like sqlmap, "
                    "python scripts, etc."
                )
            else:
                lines.append(
                    "\nNOTE: This is a remote web application test. Shell commands "
                    "execute locally on the operator's machine, not on the target."
                )

        # Known credentials
        if self.working["credentials"]:
            lines.append("\nKnown credentials:")
            for c in self.working["credentials"]:
                lines.append(
                    f"  {c['user']}:{c['password'] or '***'} @ {c['target'] or 'N/A'}"
                )

        # Confirmed vulns
        if self.working["confirmed_vulns"]:
            lines.append("\nConfirmed vulnerabilities:")
            for v in self.working["confirmed_vulns"][-5:]:
                lines.append(f"  [{v['type']}] {v['url']}")

        # Browser state — critical for web-testing agents
        bs = self.working["browser_state"]
        if bs.get("url"):
            lines.append("\nBrowser state:")
            lines.append(f"  Current URL: {bs['url']}")
            if bs.get("authenticated"):
                lines.append("  Authenticated: Yes")
            if bs.get("cookies"):
                cookie_str = "; ".join(
                    f"{c['name']}={c['value']}" for c in bs["cookies"][:10]
                )
                lines.append(f"  Cookies: {cookie_str}")
                lines.append(
                    "  Use these cookies in HTTP requests for authenticated access."
                )

        # Attack progress
        if self.working["attack_progress"]:
            lines.append("\nCompleted so far:")
            for step in self.working["attack_progress"][-5:]:
                lines.append(f"  - {step}")

        # Key discoveries
        if self.working["key_discoveries"]:
            lines.append("\nKey discoveries:")
            for d in self.working["key_discoveries"][-5:]:
                lines.append(f"  - {d}")

        # Failed approaches
        if self.episodic["failed_approaches"]:
            lines.append("\nApproaches already tried and failed (avoid):")
            for f in self.episodic["failed_approaches"][-5:]:
                lines.append(f"  - {f}")

        # Files available
        if self.working["files_created"]:
            lines.append("\nFiles in workspace:")
            for fp in self.working["files_created"][-10:]:
                lines.append(f"  - {fp}")

        lines.append("=== END BRIEFING ===")
        return "\n".join(lines)

    # === Auto-extraction from conversation ===

    def extract_from_response(self, response: str):
        """
        Auto-extract mission-relevant facts from an AI response or tool output.
        Updates working memory with any credentials, vulns, or progress found.
        """
        import re
        resp_lower = response.lower()

        # Extract credentials from FINDING blocks or tool output
        cred_patterns = [
            r"(?:username|user|login)[:\s]+['\"]?(\w+)['\"]?\s*(?:password|pass|pwd)[:\s]+['\"]?(\S+)['\"]?",
            r"default\s+credentials?\s*[:\-]?\s*(\w+)[:/](\S+)",
        ]
        for pattern in cred_patterns:
            for m in re.finditer(pattern, response, re.IGNORECASE):
                self.add_credential(m.group(1), m.group(2),
                                    target=self.core["target"])

        # Track attack progress from FINDING blocks
        for m in re.finditer(
            r'\[FINDING\s+severity="(\w+)"\s+title="([^"]+)"', response
        ):
            self.add_vuln(m.group(2))

        # Track files written
        for m in re.finditer(r'\[WRITE_FILE\s+path="([^"]+)"', response):
            self.add_file(m.group(1))

        # Track login success
        if "login" in resp_lower and ("success" in resp_lower or "welcome" in resp_lower):
            if "admin" in resp_lower:
                self.add_discovery("Logged in successfully (likely admin)")
                self.update_attack_progress("Authenticated to target application")

    def extract_from_browser(self, browser):
        """Update browser state from a live browser instance."""
        if not browser or not browser._page:
            return
        try:
            url = browser._page.url
            cookies = browser._page.context.cookies()
            # Simplify cookies for storage
            simple_cookies = [
                {"name": c["name"], "value": c["value"], "domain": c.get("domain", "")}
                for c in cookies[:15]
            ]
            authenticated = any(
                c["name"].lower() in (
                    "phpsessid", "session", "sessionid",
                    "auth", "token", "jwt", "sid"
                )
                for c in cookies
            )
            self.update_browser_state(url, simple_cookies, authenticated)
        except Exception:
            pass

    # === Serialization ===

    def to_dict(self) -> dict:
        return {
            "core": self.core,
            "working": self.working,
            "episodic": self.episodic,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "MissionMemory":
        mem = cls()
        mem.core = {**mem.core, **data.get("core", {})}
        mem.working = {**mem.working, **data.get("working", {})}
        mem.episodic = {**mem.episodic, **data.get("episodic", {})}
        return mem

    @classmethod
    def from_json(cls, json_str: str) -> "MissionMemory":
        return cls.from_dict(json.loads(json_str))

    def save_to_dir(self, dir_path: Path):
        """Persist mission memory to directory with atomic write and file locking."""
        dir_path.mkdir(parents=True, exist_ok=True)
        mem_file = dir_path / "mission_memory.json"
        tmp_file = mem_file.with_suffix(".tmp")
        data = self.to_json()
        with open(tmp_file, "w") as f:
            _lock_file(f, exclusive=True)
            try:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            finally:
                _unlock_file(f)
        # Atomic rename (on Windows, need to remove dest first)
        if os.name == "nt" and mem_file.exists():
            try:
                mem_file.unlink()
            except OSError:
                pass
        tmp_file.rename(mem_file)

    @classmethod
    def load_from_dir(cls, dir_path: Path) -> Optional["MissionMemory"]:
        """Load mission memory from directory with shared file locking."""
        mem_file = dir_path / "mission_memory.json"
        if not mem_file.exists():
            return None
        with open(mem_file, "r") as f:
            _lock_file(f, exclusive=False)
            try:
                data = f.read()
            finally:
                _unlock_file(f)
        return cls.from_json(data)
