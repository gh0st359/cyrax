"""
CYRAX Platform Detection
Provides cross-platform context for the executor and agent prompts.
"""

import os
import sys
import shutil
import tempfile
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def get_default_work_dir() -> str:
    """Get a cross-platform temporary working directory."""
    base = Path(tempfile.gettempdir()) / "cyrax"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def get_shell_name() -> str:
    """Return the active shell name."""
    if IS_WINDOWS:
        # Check if running under PowerShell or CMD
        if os.environ.get("PSModulePath"):
            return "powershell"
        return "cmd"
    return os.environ.get("SHELL", "/bin/bash").rsplit("/", 1)[-1]


def get_available_system_tools() -> list[str]:
    """Quick check for common tools available on the system."""
    tools_to_check = [
        "python", "python3", "pip", "curl", "git", "nmap", "whois",
        "dig", "nslookup", "grep", "findstr", "powershell", "bash",
        "amass", "subfinder", "httpx", "nuclei", "sqlmap", "nikto",
        "gobuster", "ffuf", "wfuzz", "masscan", "searchsploit",
    ]
    available = []
    for tool in tools_to_check:
        if shutil.which(tool):
            available.append(tool)
    return available


def get_platform_context() -> str:
    """
    Build a platform context string for inclusion in system/agent prompts.
    This tells the LLM what OS it's on, what shell to use, and what's available.
    """
    shell = get_shell_name()
    available_tools = get_available_system_tools()
    python_cmd = "python" if shutil.which("python") else "python3"

    lines = [
        f"PLATFORM: {'Windows' if IS_WINDOWS else 'Linux/macOS'} ({sys.platform})",
        f"SHELL: {shell}",
        f"PYTHON: {python_cmd} ({sys.version.split()[0]})",
        f"AVAILABLE TOOLS: {', '.join(available_tools) if available_tools else 'none detected'}",
    ]

    if IS_WINDOWS:
        lines.append("")
        lines.append("WINDOWS — THINGS THAT DO NOT WORK (NEVER USE THESE):")
        lines.append("- cat, heredoc (<<), ls, grep, head, tail, awk, sed — NONE of these exist")
        lines.append("- bash syntax: &&, ||, $(), backticks, {1..100}, do/done — ALL FAIL")
        lines.append("- mkdir /tmp/anything — /tmp does not exist on Windows")
        lines.append("- Single quotes for arguments — Windows CMD only supports double quotes")
        lines.append("- python3 — does not exist on Windows, use 'python' instead")
        lines.append("- python -c with complex code — breaks on quotes, loops, try/except")
        lines.append("- echo \"code\" > file.py — produces wrong output on Windows CMD")
        lines.append("- PowerShell scriptblocks for Python — makes no sense")
        lines.append("")
        lines.append("WINDOWS — WHAT TO USE INSTEAD:")
        lines.append("- Use [WRITE_FILE path=\"script.py\"] to create script files")
        lines.append(f"- Use '{python_cmd}' to run Python scripts")
        lines.append("- Use 'curl' for HTTP requests (it works on Windows)")
        lines.append("- Use 'nslookup' for DNS lookups")
        lines.append("- Use 'findstr' instead of grep, 'dir' instead of ls")
        lines.append("- Use browser.goto() for web application testing")
        lines.append(f"- Only use {python_cmd} -c for trivial one-liners with NO loops/try/quotes")
    else:
        lines.append("")
        lines.append("UNIX SHELL RULES:")
        lines.append("- Standard bash syntax is available")
        lines.append("- Pipes, redirects, and subshells work normally")
        lines.append("- Use [WRITE_FILE] for complex scripts to keep things clean")

    return "\n".join(lines)


def quote_arg(arg: str) -> str:
    """
    Cross-platform safe argument quoting.
    shlex.quote uses single quotes which break on Windows CMD.
    """
    if IS_WINDOWS:
        # CMD uses double quotes; escape internal double quotes
        if " " in arg or '"' in arg or any(c in arg for c in "&|<>^%"):
            escaped = arg.replace('"', '\\"')
            return f'"{escaped}"'
        return arg
    else:
        import shlex
        return shlex.quote(arg)
