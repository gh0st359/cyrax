"""
CYRAX Safety Module
Scope enforcement and permission gates to prevent out-of-scope attacks
and require user confirmation for dangerous actions.
"""

import re
import threading
import ipaddress
from typing import Optional
from urllib.parse import urlparse

from utils import display


class ScopeEnforcer:
    """
    Ensures all operations stay within the authorized target scope.
    Prevents the AI from attacking arbitrary domains (e.g., target.com from prompt examples).
    """

    def __init__(self, targets: Optional[list[str]] = None):
        self.allowed_domains: set[str] = set()
        self.allowed_wildcard_domains: list[str] = []
        self.allowed_ip_ranges: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self.allowed_ips: set[str] = set()
        self.enabled = False
        self._raw_targets: list[str] = []

        if targets:
            self._parse_targets(targets)
            self.enabled = True

    def _parse_targets(self, targets: list[str]):
        """Parse target specifications into domains, IPs, and CIDR ranges."""
        self._raw_targets = list(targets)
        for target in targets:
            target = target.strip()
            if not target:
                continue

            # CIDR notation (e.g., 192.168.1.0/24)
            if "/" in target and not target.startswith("http"):
                try:
                    network = ipaddress.ip_network(target, strict=False)
                    self.allowed_ip_ranges.append(network)
                    continue
                except ValueError:
                    pass

            # IP address
            try:
                ipaddress.ip_address(target)
                self.allowed_ips.add(target)
                continue
            except ValueError:
                pass

            # URL — extract domain
            if "://" in target:
                parsed = urlparse(target)
                host = parsed.hostname or ""
                if host:
                    self._add_domain(host)
                continue

            # Wildcard domain (e.g., *.example.com)
            if target.startswith("*."):
                self.allowed_wildcard_domains.append(target[2:].lower())
                # Also allow the base domain itself
                self.allowed_domains.add(target[2:].lower())
                continue

            # Plain domain or hostname
            self._add_domain(target)

    def _add_domain(self, domain: str):
        """Add a domain, also checking if it's an IP."""
        domain = domain.lower().strip()
        try:
            ipaddress.ip_address(domain)
            self.allowed_ips.add(domain)
        except ValueError:
            self.allowed_domains.add(domain)

    def _is_ip_allowed(self, ip_str: str) -> bool:
        """Check if an IP is in scope."""
        if ip_str in self.allowed_ips:
            return True
        try:
            addr = ipaddress.ip_address(ip_str)
            return any(addr in network for network in self.allowed_ip_ranges)
        except ValueError:
            return False

    def _is_domain_allowed(self, domain: str) -> bool:
        """Check if a domain is in scope (including wildcard matching)."""
        domain = domain.lower()
        if domain in self.allowed_domains:
            return True

        # Common redirect alias handling: if scope includes apex domain,
        # also allow the corresponding www host (example.com <-> www.example.com).
        if domain.startswith("www.") and domain[4:] in self.allowed_domains:
            return True

        # Check wildcard patterns (*.example.com matches sub.example.com)
        for wildcard_base in self.allowed_wildcard_domains:
            if domain.endswith("." + wildcard_base) or domain == wildcard_base:
                return True
        return False

    def is_in_scope(self, url_or_host: str) -> bool:
        """Check if a URL, domain, or IP is within authorized scope."""
        if not self.enabled:
            return True

        target = url_or_host.strip()

        # Parse URL
        if "://" in target:
            parsed = urlparse(target)
            host = parsed.hostname or ""
        else:
            # Could be domain:port, just domain, or IP
            host = target.split(":")[0].strip()

        if not host:
            return True  # Can't determine target, allow

        # Localhost/loopback is always allowed
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return True

        # Check IP
        try:
            ipaddress.ip_address(host)
            return self._is_ip_allowed(host)
        except ValueError:
            pass

        # Check domain
        return self._is_domain_allowed(host)

    def _check_text_for_scope(self, text: str) -> tuple[bool, str]:
        """Run scope extraction checks on arbitrary text."""
        urls = re.findall(r'https?://[^\s\'"]+', text)
        for url in urls:
            if not self.is_in_scope(url):
                parsed = urlparse(url)
                return False, (
                    f"'{parsed.hostname}' is NOT in your authorized scope. "
                    f"Your targets are: {', '.join(self._raw_targets)}"
                )

        ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text)
        for ip in ips:
            if ip in ("127.0.0.1", "0.0.0.0"):
                continue
            if not self._is_ip_allowed(ip):
                return False, (
                    f"IP '{ip}' is NOT in your authorized scope. "
                    f"Your targets are: {', '.join(self._raw_targets)}"
                )

        tool_patterns = [
            r'\b(?:nslookup|dig|host|ping|whois|nmap|curl|wget|nikto|gobuster|ffuf|dirb|wfuzz|sqlmap|hydra)\s+(?:-[^\s]+\s+)*([a-zA-Z0-9][-a-zA-Z0-9.]+\.[a-zA-Z]{2,})',
        ]
        for pattern in tool_patterns:
            for match in re.finditer(pattern, text):
                domain = match.group(1).lower()
                if domain.endswith(('.py', '.sh', '.txt', '.conf', '.log', '.xml', '.json', '.html')):
                    continue
                if not self.is_in_scope(domain):
                    return False, (
                        f"'{domain}' is NOT in your authorized scope. "
                        f"Your targets are: {', '.join(self._raw_targets)}"
                    )

        return True, ""

    def _extract_wrapped_payloads(self, command: str) -> list[str]:
        """Extract command payloads from wrappers like bash -c, python -c, pwsh -Command."""
        payloads: list[str] = []
        wrappers = {
            "bash": "-c",
            "sh": "-c",
            "zsh": "-c",
            "dash": "-c",
            "ksh": "-c",
            "python": "-c",
            "python3": "-c",
            "py": "-c",
            "powershell": "-command",
            "pwsh": "-command",
        }

        tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\S+', command)
        if not tokens:
            return payloads

        binary = tokens[0].strip('"\'').lower()
        if binary not in wrappers:
            return payloads

        marker = wrappers[binary]
        for i, token in enumerate(tokens[1:], start=1):
            normalized = token.lower()
            if normalized == marker or (marker == "-command" and normalized == "-c"):
                if i + 1 < len(tokens):
                    payloads.append(tokens[i + 1].strip('"\''))
                break
            if normalized.startswith(marker + "="):
                payloads.append(token.split("=", 1)[1].strip('"\''))
                break

        return payloads

    def check_command(self, command: str, _depth: int = 0) -> tuple[bool, str]:
        """
        Check if a shell command targets in-scope resources.
        Returns (allowed, reason).
        """
        if not self.enabled:
            return True, ""

        allowed, reason = self._check_text_for_scope(command)
        if not allowed:
            return False, reason

        if _depth >= 4:
            return True, ""

        for payload in self._extract_wrapped_payloads(command):
            allowed, reason = self._check_text_for_scope(payload)
            if not allowed:
                return False, reason
            nested_ok, nested_reason = self.check_command(payload, _depth=_depth + 1)
            if not nested_ok:
                return False, nested_reason

        return True, ""

    def check_browser_navigation(self, url: str) -> tuple[bool, str]:
        """Check if a browser navigation stays in scope."""
        if not self.enabled:
            return True, ""
        if not self.is_in_scope(url):
            parsed = urlparse(url)
            return False, (
                f"'{parsed.hostname or url}' is NOT in your authorized scope. "
                f"Your targets are: {', '.join(self._raw_targets)}"
            )
        return True, ""

    def get_scope_description(self) -> str:
        """Get a human-readable description of the authorized scope."""
        if not self.enabled or not self._raw_targets:
            return "No scope restrictions (be careful!)"
        return ", ".join(self._raw_targets)


# Module-level attack pattern constants (shared by classify_action and PermissionGate)
_ATTACK_PATTERNS = [
    r"'.*OR.*'.*=.*'",                     # SQL injection
    r"(?i)union\s+select",                  # SQL UNION
    r"(?i)sleep\s*\(\s*\d+\s*\)",           # Time-based SQLi
    r"<script",                             # XSS
    r"javascript:",                         # XSS via protocol
    r"onerror\s*=",                         # XSS event handlers
    r"onload\s*=",                          # XSS event handlers
    r";\s*(?:ls|cat|id|whoami|pwd)\b",      # Command injection
    r"\|\s*(?:ls|cat|id|whoami|pwd)\b",     # Command injection via pipe
    r"`[^`]+`",                             # Command injection via backticks
    r"\$\([^)]+\)",                         # Command injection via subshell
]

_EXPLOIT_PATTERNS = [
    r"\bmsfconsole\b.*\bexploit\b",
    r"\bmsfconsole\b.*\brun\b",
    r"\bsqlmap\b",
    r"\bhydra\b",
    r"\bmetasploit\b",
]


def classify_action(command: str) -> str:
    """
    Classify a command into an action category (module-level function).
    Usable from both PermissionGate and IPCPermissionGate in subprocess mode.
    """
    cmd = command.strip()

    # Check for attack payloads in browser fill/type commands
    if re.match(r"browser\.(?:fill|type)\(", cmd):
        for pattern in _ATTACK_PATTERNS:
            if re.search(pattern, cmd):
                return "attack_payload"

    # Check for attack payloads in curl/wget commands
    if re.match(r"\b(?:curl|wget)\b", cmd):
        for pattern in _ATTACK_PATTERNS:
            if re.search(pattern, cmd):
                return "attack_payload"

    # Check for attack payloads in python scripts
    if re.match(r"\b(?:python|python3)\b", cmd) and len(cmd) > 200:
        for pattern in _ATTACK_PATTERNS:
            if re.search(pattern, cmd):
                return "attack_payload"

    # Check for exploit tools
    for pattern in _EXPLOIT_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "exploit_launch"

    # Network scanning tools
    if re.match(r"\b(?:nmap|masscan|zmap)\b", cmd):
        return "network_scan"

    # Browser navigation
    if cmd.startswith("browser.goto("):
        return "browser_navigate"

    # Other browser commands
    if cmd.startswith("browser."):
        return "browser_navigate"

    # Everything else is a shell command
    return "shell_command"


class PermissionGate:
    """
    Asks user for confirmation before dangerous actions.
    Categories of actions have default permission levels.
    """

    # Action categories and their default permission level
    # "ask" = prompt every time, "allow" = auto-approve,
    # "ask_first" = prompt once then auto-approve for session, "deny" = block
    ACTIONS = {
        "attack_payload": "ask",       # SQLi, XSS, command injection payloads
        "credential_use": "ask",       # Using found credentials
        "exploit_launch": "ask",       # Running exploits
        "privilege_esc": "ask",        # Privilege escalation
        "lateral_move": "ask",         # Moving to another host
        "network_scan": "allow",       # Port scans, enumeration
        "file_write": "allow",         # Writing scripts to workdir
        "agent_spawn": "allow",        # Spawning sub-agents
        "browser_navigate": "allow",   # Basic browsing (in scope)
        "shell_command": "ask_first",  # Shell commands: prompt once, then allow
        "data_exfil": "deny",          # Extracting sensitive data off-target
    }

    # Reference module-level constants
    _ATTACK_PATTERNS = _ATTACK_PATTERNS
    _EXPLOIT_PATTERNS = _EXPLOIT_PATTERNS

    def __init__(self, auto_approve: bool = False):
        self.auto_approve = auto_approve
        self.session_approvals: dict[str, str] = {}  # Remembered decisions
        self.enabled = True
        self._prompt_lock = threading.Lock()

    def classify_action(self, command: str) -> str:
        """Classify a command into an action category. Delegates to module-level function."""
        return classify_action(command)

    def check(self, command: str, allow_prompt: bool = True) -> tuple[bool, str]:
        """
        Check if a command is permitted. Returns (allowed, reason).
        For 'ask' level, prompts the user interactively.
        """
        if not self.enabled or self.auto_approve:
            return True, ""

        action_type = self.classify_action(command)

        # Check session-level overrides first
        level = self.session_approvals.get(action_type, self.ACTIONS.get(action_type, "allow"))

        if level == "allow":
            return True, ""
        if level == "deny":
            return False, f"Action type '{action_type}' is denied by policy."

        if level == "ask_first":
            if not allow_prompt:
                return False, (
                    f"Action '{action_type}' requires confirmation. "
                    "Use /approve <category> or /auto to pre-approve."
                )
            # Prompt once per category, then auto-allow for the session
            allowed, reason = self._prompt_user(action_type, command)
            if allowed:
                self.session_approvals[action_type] = "allow"
            return allowed, reason

        # level == "ask" — prompt the user every time
        if not allow_prompt:
            return False, (
                f"Action '{action_type}' requires confirmation. "
                "Use /approve <category> or /auto to pre-approve."
            )
        return self._prompt_user(action_type, command)

    def _prompt_user(self, action_type: str, command: str) -> tuple[bool, str]:
        """Prompt the user for permission."""
        short_cmd = command[:120] + "..." if len(command) > 120 else command
        display.console.print()
        display.console.print(
            f"[bold yellow]Permission Required[/bold yellow] [{action_type}]"
        )
        display.console.print(f"  Command: [cyan]{short_cmd}[/cyan]")
        display.console.print(
            "  [Y] Allow  [N] Deny  [A] Allow all of this type  [D] Deny all of this type"
        )
        with self._prompt_lock:
            try:
                choice = display.console.input("[bold white]  > [/bold white]").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False, "User interrupted permission prompt."

        if choice in ("y", "yes"):
            return True, ""
        elif choice in ("a", "all", "always"):
            self.session_approvals[action_type] = "allow"
            return True, ""
        elif choice in ("d", "deny all"):
            self.session_approvals[action_type] = "deny"
            return False, f"User denied all '{action_type}' actions for this session."
        else:
            return False, "User denied this action."

    def is_prompt_active(self) -> bool:
        """Return True when a permission prompt is currently waiting for user input."""
        return self._prompt_lock.locked()

    def auto_approve_all(self):
        """Switch to fully autonomous mode (no prompts)."""
        self.auto_approve = True

    def approve_category(self, category: str):
        """Pre-approve a category of actions."""
        if category in self.ACTIONS:
            self.session_approvals[category] = "allow"
