"""
CYRAX Recovery Engine

Turns failures into next actions. Instead of stopping on unfamiliar
errors, CYRAX classifies failures and injects concrete alternate
strategies into the agent loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class RecoveryStrategy:
    name: str
    guidance: str
    example_actions: list[str]


class RecoveryEngine:
    """Classify failures and produce alternate execution paths."""

    def __init__(self):
        self.failures: list[str] = []
        self.strategies = self._default_strategies()

    def record_failure(self, text: str) -> None:
        if text:
            self.failures.append(text[-2000:])
            self.failures = self.failures[-20:]

    def guidance_for(self, text: str) -> str:
        """Return a recovery prompt section for a failure blob."""
        self.record_failure(text)
        lowered = text.lower()
        matched = []
        for pattern, strategy in self.strategies:
            if re.search(pattern, lowered):
                matched.append(strategy)
        if not matched:
            matched = [self._generic_strategy()]
        lines = ["AUTONOMOUS RECOVERY REQUIRED:"]
        lines.append(
            "Do not stop. Diagnose the failure and try "
            "a different path."
        )
        for strategy in matched[:3]:
            lines.append(
                f"- {strategy.name}: {strategy.guidance}"
            )
            for action in strategy.example_actions[:2]:
                lines.append(f"  Example: {action}")
        return "\n".join(lines)

    def _default_strategies(
        self,
    ) -> list[tuple[str, RecoveryStrategy]]:
        return [
            (
                r"command not found|missing tool"
                r"|not installed"
                r"|no such file or directory",
                RecoveryStrategy(
                    "Bootstrap missing capability",
                    "Install the missing tool if safe, "
                    "use an installed equivalent, or "
                    "script the capability directly.",
                    [
                        "[EXECUTE] python3 - <<'PY'\n"
                        "# implement check\nPY [/EXECUTE]",
                        "[EXECUTE] which nmap || "
                        "sudo apt-get install -y nmap "
                        "[/EXECUTE]",
                    ],
                ),
            ),
            (
                r"permission denied"
                r"|operation not permitted|eacces",
                RecoveryStrategy(
                    "Permission workaround",
                    "Try a user-writable location, "
                    "inspect permissions, or choose a "
                    "non-privileged approach.",
                    [
                        "[EXECUTE] ls -la . && id "
                        "[/EXECUTE]",
                        "[EXECUTE] python3 - <<'PY'\n"
                        "from pathlib import Path\n"
                        "print(Path.cwd())\nPY [/EXECUTE]",
                    ],
                ),
            ),
            (
                r"timeout|timed out|hang",
                RecoveryStrategy(
                    "Timeout decomposition",
                    "Split into smaller probes, reduce "
                    "concurrency, add timeouts, or "
                    "inspect intermediate state.",
                    [
                        "[EXECUTE] timeout 20 curl -v "
                        "https://target/ [/EXECUTE]",
                        "[EXECUTE] python3 - <<'PY'\n"
                        "# add per-request timeout\n"
                        "PY [/EXECUTE]",
                    ],
                ),
            ),
            (
                r"401|403|unauthorized|forbidden|auth",
                RecoveryStrategy(
                    "Authorized access path",
                    "Enumerate allowed public surface, "
                    "inspect local source/config, or "
                    "request test credentials.",
                    [
                        "[EXECUTE] curl -i "
                        "https://target/robots.txt "
                        "[/EXECUTE]",
                        '[READ_FILE path="src/auth"] '
                        "[/READ_FILE]",
                    ],
                ),
            ),
            (
                r"dns|could not resolve"
                r"|name or service not known|nxdomain",
                RecoveryStrategy(
                    "Resolution fallback",
                    "Try alternate resolvers, direct "
                    "IPs, or browser fetch to "
                    "distinguish DNS from app failure.",
                    [
                        "[EXECUTE] dig target @1.1.1.1 "
                        "|| nslookup target 8.8.8.8 "
                        "[/EXECUTE]",
                        "[EXECUTE] curl -vk "
                        "--connect-timeout 10 "
                        "https://target/ [/EXECUTE]",
                    ],
                ),
            ),
        ]

    def _generic_strategy(self) -> RecoveryStrategy:
        return RecoveryStrategy(
            "Unknown failure recovery",
            "Gather facts, inspect environment, "
            "reduce assumptions, then try a simpler "
            "alternate path.",
            [
                "[EXECUTE] pwd && ls -la && uname -a "
                "[/EXECUTE]",
                "[EXECUTE] python3 - <<'PY'\n"
                "print('diagnostic probe')\n"
                "PY [/EXECUTE]",
            ],
        )
