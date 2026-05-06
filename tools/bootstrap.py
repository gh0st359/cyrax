"""
CYRAX Tool Bootstrap

Self-healing tool installer and fallback advisor. When CYRAX hits a missing
binary or module, this module can install safe package dependencies or return
actionable recovery guidance instead of stopping.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class BootstrapResult:
    tool: str
    attempted: bool
    success: bool
    command: str = ""
    output: str = ""
    guidance: str = ""


class ToolBootstrapper:
    """Detect and install missing tools with conservative package mappings."""

    APT_PACKAGES = {
        "nmap": "nmap",
        "whois": "whois",
        "dig": "dnsutils",
        "curl": "curl",
        "wget": "wget",
        "jq": "jq",
        "openssl": "openssl",
        "grep": "grep",
        "awk": "gawk",
        "nc": "netcat-openbsd",
        "socat": "socat",
        "gobuster": "gobuster",
        "ffuf": "ffuf",
        "nikto": "nikto",
        "sqlmap": "sqlmap",
        "hydra": "hydra",
        "john": "john",
        "hashcat": "hashcat",
        "smbclient": "smbclient",
        "ldapsearch": "ldap-utils",
        "proxychains4": "proxychains4",
        "masscan": "masscan",
        "wafw00f": "wafw00f",
        "searchsploit": "exploitdb",
        "aws": "awscli",
        "python3": "python3",
        "pip3": "python3-pip",
    }

    PIP_PACKAGES = {
        "subfinder": "subfinder",
        "theHarvester": "theHarvester",
        "bloodhound-python": "bloodhound",
        "impacket-secretsdump": "impacket",
        "impacket-psexec": "impacket",
        "impacket-wmiexec": "impacket",
        "impacket-smbexec": "impacket",
        "impacket-GetNPUsers": "impacket",
        "impacket-GetUserSPNs": "impacket",
    }

    BREW_PACKAGES = {
        "nmap": "nmap",
        "whois": "whois",
        "curl": "curl",
        "wget": "wget",
        "jq": "jq",
        "openssl": "openssl",
        "nc": "netcat",
        "socat": "socat",
        "ffuf": "ffuf",
        "sqlmap": "sqlmap",
        "hydra": "hydra",
        "john": "john",
        "masscan": "masscan",
        "aws": "awscli",
    }

    GO_PACKAGES = {
        "subfinder": (
            "github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest"
        ),
        "dnsx": "github.com/projectdiscovery/dnsx/cmd/dnsx@latest",
        "httpx": "github.com/projectdiscovery/httpx/cmd/httpx@latest",
        "nuclei": "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
        "dalfox": "github.com/hahwul/dalfox/v2@latest",
        "chisel": "github.com/jpillora/chisel@latest",
    }

    SAFE_AUTO_INSTALL = {
        "nmap", "whois", "dig", "curl", "wget", "jq", "openssl", "grep", "awk",
        "nc", "socat", "gobuster", "ffuf", "nikto", "sqlmap", "wafw00f",
        "searchsploit", "python3", "pip3", "subfinder", "dnsx", "httpx",
        "nuclei", "dalfox", "theHarvester",
    }

    def __init__(
        self,
        auto_install: bool = False,
        work_dir: Optional[str | Path] = None,
    ):
        self.auto_install = auto_install
        self.work_dir = Path(work_dir or os.getcwd()).expanduser().resolve()

    def is_available(self, tool: str) -> bool:
        return shutil.which(tool) is not None

    def infer_missing_tool(self, command_or_error: str) -> str:
        text = command_or_error.strip()
        if not text:
            return ""
        patterns = [
            r"Command not found:\s*([^\s]+)",
            r"([A-Za-z0-9_.-]+):\s*command not found",
            r"([A-Za-z0-9_.-]+)\s+is not recognized",
            r"No module named ['\"]?([A-Za-z0-9_.-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return self._normalize_tool(match.group(1))
        first = re.split(r"\s+", text)[0].strip("'\"")
        return self._normalize_tool(first)

    @staticmethod
    def _normalize_tool(tool: str) -> str:
        aliases = {
            "python": "python3",
            "pip": "pip3",
            "netcat": "nc",
            "host": "dig",
        }
        return aliases.get(tool, tool)

    def bootstrap(self, tool: str, reason: str = "") -> BootstrapResult:
        tool = self._normalize_tool(tool)
        if not tool:
            return BootstrapResult(
                tool="",
                attempted=False,
                success=False,
                guidance="No missing tool could be inferred.",
            )
        if self.is_available(tool):
            return BootstrapResult(
                tool=tool,
                attempted=False,
                success=True,
                guidance=f"{tool} is already installed.",
            )

        if not self.auto_install:
            return BootstrapResult(
                tool=tool,
                attempted=False,
                success=False,
                guidance=self.install_guidance(tool, reason),
            )

        if tool not in self.SAFE_AUTO_INSTALL:
            return BootstrapResult(
                tool=tool,
                attempted=False,
                success=False,
                guidance=(
                    f"Auto-install is enabled, but {tool} is not in "
                    "the safe installer allowlist. "
                    + self.install_guidance(tool, reason)
                ),
            )

        command = self._install_command(tool)
        if not command:
            return BootstrapResult(
                tool=tool,
                attempted=False,
                success=False,
                guidance=self.install_guidance(tool, reason),
            )

        result = self._run(command)
        success = result.returncode == 0 and self.is_available(tool)
        return BootstrapResult(
            tool=tool,
            attempted=True,
            success=success,
            command=command,
            output=(result.stdout + result.stderr).strip()[-4000:],
            guidance="" if success else self.install_guidance(tool, reason),
        )

    def _install_command(self, tool: str) -> str:
        if shutil.which("apt-get") and tool in self.APT_PACKAGES:
            package = self.APT_PACKAGES[tool]
            sudo = (
                "sudo "
                if os.geteuid() != 0 and shutil.which("sudo")
                else ""
            )
            return (
                f"{sudo}apt-get update -y && "
                f"{sudo}apt-get install -y {package}"
            )
        if shutil.which("brew") and tool in self.BREW_PACKAGES:
            return f"brew install {self.BREW_PACKAGES[tool]}"
        if shutil.which("go") and tool in self.GO_PACKAGES:
            return f"go install {self.GO_PACKAGES[tool]}"
        if shutil.which("pip3") and tool in self.PIP_PACKAGES:
            return (
                f"{sys.executable} -m pip install --user "
                f"{self.PIP_PACKAGES[tool]}"
            )
        return ""

    def _run(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            shell=True,
            cwd=str(self.work_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
        )

    def install_guidance(self, tool: str, reason: str = "") -> str:
        pieces = [f"Missing tool: {tool}."]
        if reason:
            pieces.append(f"Reason: {reason}")
        commands = []
        apt = self.APT_PACKAGES.get(tool)
        brew = self.BREW_PACKAGES.get(tool)
        go = self.GO_PACKAGES.get(tool)
        pip = self.PIP_PACKAGES.get(tool)
        if apt:
            commands.append(
                "Debian/Ubuntu: sudo apt-get update && "
                f"sudo apt-get install -y {apt}"
            )
        if brew:
            commands.append(f"macOS/Homebrew: brew install {brew}")
        if go:
            commands.append(f"Go: go install {go}")
        if pip:
            commands.append(
                f"Python: {sys.executable} -m pip install --user {pip}"
            )
        if commands:
            pieces.append(
                "Install options:\n"
                + "\n".join(f"- {cmd}" for cmd in commands)
            )
        else:
            pieces.append(
                "No known installer mapping. Search package docs or "
                "write a small Python fallback."
            )
        pieces.append(
            "CYRAX should either install it, use an equivalent installed "
            "tool, or script the capability directly."
        )
        return "\n".join(pieces)
