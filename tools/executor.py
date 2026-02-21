"""
CYRAX Tool Executor
Executes shell commands and tools with proper sandboxing, timeout, and output capture.
"""

import os
import sys
import re
import subprocess
import shlex
import signal
import tempfile
import textwrap
from pathlib import Path
from typing import Optional

from utils.logging import get_logger
from utils.platform_info import IS_WINDOWS, get_default_work_dir, get_shell_name
from utils.safety import ScopeEnforcer


def strip_markdown_fences(command: str) -> str:
    """
    Strip markdown code fences from commands and dedent consistently-indented
    code blocks. LLMs often wrap commands in ```bash ... ``` or ```python ... ```
    and indent the code inside, which causes IndentationError when written to files.
    """
    command = command.strip()
    match = re.match(r'^```\w*\s*\n(.*?)```\s*$', command, re.DOTALL)
    if match:
        return textwrap.dedent(match.group(1)).strip()
    # Also handle single-line: ```bash command```
    match = re.match(r'^```\w*\s+(.*?)```\s*$', command, re.DOTALL)
    if match:
        return textwrap.dedent(match.group(1)).strip()
    return textwrap.dedent(command).strip()


def _normalize_python_cmd(cmd: str) -> str:
    """Normalize python3 -> python on Windows where python3 doesn't exist."""
    if IS_WINDOWS:
        # Replace python3 with python at the start of commands
        if cmd.startswith("python3 ") or cmd == "python3":
            return "python" + cmd[7:]
        if cmd.startswith("python3."):
            return cmd  # Leave python3.11 etc. alone
    return cmd


def _adapt_windows_unix_filters(command: str) -> str:
    """
    Adapt common Unix text-filter usage for Windows cmd.exe.

    The model frequently emits pipelines like `... | grep ...` which fail on
    stock Windows installs. Convert simple grep cases to findstr so commands
    still run without requiring GNU tools.
    """
    if not IS_WINDOWS:
        return command

    # Convert `| grep ...` into `| findstr ...` for common cases.
    # Supports optional -i and quoted or unquoted patterns.
    grep_pipe_pattern = re.compile(
        r"\|\s*grep\s+(?P<flags>(?:-[A-Za-z]+\s+)*)?(?P<pattern>'[^']*'|\"[^\"]*\"|\S+)",
        flags=re.IGNORECASE,
    )

    def _replace_grep(match: re.Match) -> str:
        flags = (match.group("flags") or "").lower()
        pattern = (match.group("pattern") or "").strip()
        if (pattern.startswith("'") and pattern.endswith("'")) or (
            pattern.startswith('"') and pattern.endswith('"')
        ):
            pattern = pattern[1:-1]

        findstr_flags = ["/R", f'/C:"{pattern}"']
        if "i" in flags:
            findstr_flags.insert(0, "/I")

        return "| findstr " + " ".join(findstr_flags)

    return grep_pipe_pattern.sub(_replace_grep, command)


def _extract_python_c_code(command: str) -> Optional[str]:
    """
    Extract Python code from a 'python -c "..."' or "python3 -c '...'" command.
    Returns the code string if matched, None otherwise.
    """
    # Match: python[3] -c "code" or python[3] -c 'code'
    match = re.match(
        r'''python[3]?\s+-c\s+(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')''',
        command.strip(),
        re.DOTALL,
    )
    if match:
        return (match.group(1) or match.group(2) or "").strip()

    # Also match unquoted (the LLM sometimes generates python3 -c "\n...\n" with
    # the newlines as literal chars in the command string)
    match = re.match(
        r'python[3]?\s+-c\s+"(.*)"',
        command.strip(),
        re.DOTALL,
    )
    if match:
        return match.group(1).strip()

    return None


# Python compound statements that can't be semicolon-joined on one line.
# When these appear in python -c "..." code, we must route to a temp file.
_PYTHON_COMPOUND_KEYWORDS = re.compile(
    r'(?:^|;\s*)'           # Start of string or after semicolon
    r'(?:for |while |try:|except |with |if .+:.+(?:else|elif)|def |class |async )'
)


def _needs_temp_file(python_code: str) -> bool:
    """
    Check if Python code contains compound statements that can't run
    in a single-line python -c context. These require proper indentation
    and must be written to a temp file.
    """
    if "\n" in python_code:
        return True
    return bool(_PYTHON_COMPOUND_KEYWORDS.search(python_code))


def _unflatten_python(code: str) -> str:
    """
    Convert semicolon-joined Python with compound statements into properly
    indented multi-line Python for temp file execution.

    Handles common LLM patterns like:
      import httpx; for port in range(100): try: r=httpx.get(url); print(r) except: pass
    """
    if "\n" in code:
        return code  # Already multi-line

    # Step 1: Split on semicolons (respecting strings/parens)
    raw_parts = _split_on_semicolons(code)

    # Step 2: Further split parts that contain compound keywords mid-statement
    # e.g. "print(x) except Exception as e" → ["print(x)", "except Exception as e"]
    parts = []
    for part in raw_parts:
        parts.extend(_split_on_keywords(part.strip()))

    # Step 3: Build indented output
    lines = []
    indent = 0
    _BLOCK_OPENERS = ("for ", "while ", "if ", "elif ", "else:", "try:",
                       "except ", "except:", "with ", "def ", "class ",
                       "finally:", "async ")
    _DEDENT_KEYWORDS = ("except ", "except:", "elif ", "else:", "finally:")

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Dedent before except/elif/else/finally
        if any(part.startswith(kw) for kw in _DEDENT_KEYWORDS):
            indent = max(0, indent - 1)

        # Check if this part is "header: body" (e.g. "for x in y: print(x)")
        if any(part.startswith(kw) for kw in _BLOCK_OPENERS):
            colon_pos = _find_block_colon(part)
            if colon_pos is not None and colon_pos < len(part) - 1:
                header = part[:colon_pos + 1]
                body = part[colon_pos + 1:].strip()
                lines.append("    " * indent + header)
                indent += 1
                if body:
                    # Body might itself contain keywords: recursively split
                    for sub in _split_on_keywords(body):
                        sub = sub.strip()
                        if sub:
                            if any(sub.startswith(kw) for kw in _DEDENT_KEYWORDS):
                                indent = max(0, indent - 1)
                            if any(sub.startswith(kw) for kw in _BLOCK_OPENERS):
                                sub_colon = _find_block_colon(sub)
                                if sub_colon is not None and sub_colon < len(sub) - 1:
                                    lines.append("    " * indent + sub[:sub_colon + 1])
                                    indent += 1
                                    lines.append("    " * indent + sub[sub_colon + 1:].strip())
                                else:
                                    lines.append("    " * indent + sub)
                                    indent += 1
                            else:
                                lines.append("    " * indent + sub)
            else:
                lines.append("    " * indent + part)
                indent += 1
        else:
            lines.append("    " * indent + part)

    return "\n".join(lines)


def _split_on_semicolons(code: str) -> list[str]:
    """Split Python code on semicolons, respecting strings and parentheses."""
    parts = []
    current = []
    depth = 0
    in_string = None

    for char in code:
        if in_string:
            current.append(char)
            if char == in_string:
                in_string = None
        elif char in ('"', "'"):
            current.append(char)
            in_string = char
        elif char in ("(", "[", "{"):
            current.append(char)
            depth += 1
        elif char in (")", "]", "}"):
            current.append(char)
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)

    if current:
        parts.append("".join(current))
    return parts


def _split_on_keywords(stmt: str) -> list[str]:
    """
    Split a statement before Python block keywords that appear mid-statement.
    e.g. "print(x) except Exception as e: pass" → ["print(x)", "except Exception as e: pass"]
    """
    _KW_PATTERN = re.compile(
        r'(?<=\S)\s+'  # Preceded by non-space
        r'(?=(?:except |except:|elif |else:|finally:))'  # Followed by keyword
    )
    return _KW_PATTERN.split(stmt)


def _find_block_colon(stmt: str) -> Optional[int]:
    """Find the colon that opens a block (for/if/while/etc.), ignoring colons in strings/slices."""
    depth = 0
    in_string = None
    for i, char in enumerate(stmt):
        if in_string:
            if char == in_string:
                in_string = None
        elif char in ('"', "'"):
            in_string = char
        elif char in ("(", "[", "{"):
            depth += 1
        elif char in (")", "]", "}"):
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0 and i > 0:
            prefix = stmt[:i].strip()
            if any(prefix.startswith(kw) for kw in
                   ["for ", "while ", "if ", "elif ", "else", "try",
                    "except ", "except", "with ", "def ", "class ",
                    "finally", "async "]):
                return i
    return None


def _is_powershell_syntax(command: str) -> bool:
    """
    Detect if a command uses PowerShell syntax that won't work in cmd.exe.
    PowerShell-specific patterns: for ($i=...), foreach, $variable, cmdlets, etc.
    """
    ps_patterns = [
        r'for\s*\(\s*\$',           # for ($i = 0; ...)
        r'foreach\s*\(',             # foreach ($item in ...)
        r'\$\w+\s*=',               # $variable = value
        r'\$\w+\.\w+',             # $obj.Property
        r'\|\s*Where-Object',       # | Where-Object
        r'\|\s*Select-Object',      # | Select-Object
        r'\|\s*ForEach-Object',     # | ForEach-Object
        r'Get-\w+',                 # Get-ChildItem, Get-Process, etc.
        r'Set-\w+',                 # Set-Item, etc.
        r'New-\w+',                 # New-Object, etc.
        r'Invoke-\w+',             # Invoke-WebRequest, etc.
        r'Test-\w+',               # Test-Connection, etc.
        r'Write-\w+',              # Write-Host, Write-Output
        r'-eq\b|-ne\b|-gt\b|-lt\b|-ge\b|-le\b',  # PowerShell comparison operators
        r'\[System\.\w+',          # [System.Net.Sockets...]
    ]
    for pattern in ps_patterns:
        if re.search(pattern, command):
            return True
    return False


def _fix_python_c_quotes(command: str) -> Optional[str]:
    """
    Fix nested double-quote issues in python -c commands.
    E.g.: python -c "print(r.headers.get("X-Frame-Options", "Not set"))"
    The inner double quotes break cmd.exe. Detect and route to temp file.
    Returns the Python code if it has nested quote issues, None otherwise.
    """
    python_code = _extract_python_c_code(command)
    if not python_code:
        return None

    # Check for unbalanced quotes that suggest nested quote problems
    # If the extracted code itself contains unescaped quotes, it needs temp file
    if '"' in python_code or "'" in python_code:
        # Check if it's a simple case that works fine
        single_count = python_code.count("'") - python_code.count("\\'")
        double_count = python_code.count('"') - python_code.count('\\"')
        # If both single and double quotes are present, likely needs temp file
        if single_count > 0 and double_count > 0:
            return python_code

    return None


def _is_prose_line(line: str) -> bool:
    """Check if a line is markdown prose rather than a command."""
    # Markdown headings (but not shebangs)
    if line.startswith('#') and not line.startswith('#!'):
        return True
    # Numbered lists: 1. ..., 2. ...
    if re.match(r'^\d+\.\s+\w', line):
        return True
    # Bullet points
    if line.startswith(('- ', '* ', '+ ')):
        return True
    # Bold text markers
    if line.startswith('**') or line.startswith('__'):
        return True
    # Lines that look like section headers
    if line.startswith(('###', 'Step ', 'Example ', 'Updated ', 'Actions', 'Next ')):
        return True
    # Very long lines with spaces are likely prose (commands rarely exceed 200 chars)
    if len(line) > 200 and ' ' in line and not line.startswith(('python', 'curl', 'browser.', 'wget', 'nmap')):
        return True
    return False


def _clean_command_line(line: str) -> str:
    """Strip trailing comments from a command, being careful not to strip # inside strings."""
    if not line:
        return line
    in_single = False
    in_double = False
    for i, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == '#' and not in_single and not in_double:
            # Found a comment outside of strings
            stripped = line[:i].rstrip()
            if stripped:
                return stripped
    return line.strip()


def sanitize_command(raw: str) -> Optional[str]:
    """
    Clean up a command extracted from an [EXECUTE] block.

    The model often pollutes EXECUTE blocks with:
    - Trailing comments: browser.wait(10000) # Wait for 10 seconds
    - Markdown prose: ### Step 2: ..., **bold text**, numbered lists
    - Nested [EXECUTE] tags: [EXECUTE] browser.forms() [/EXECUTE]
    - Multi-line plans mixed with commands

    Returns the cleaned command, or None if no valid command found.
    """
    # Remove nested [EXECUTE] and [/EXECUTE] tags
    cleaned = re.sub(r'\[/?EXECUTE\]', '', raw)

    # Split into lines
    lines = cleaned.strip().split('\n')

    # If single line, just clean it
    if len(lines) == 1:
        return _clean_command_line(lines[0].strip()) or None

    # Multi-line: find the first line that looks like a command
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_prose_line(stripped):
            continue
        cmd = _clean_command_line(stripped)
        if cmd:
            return cmd

    # Fallback: first non-empty line, cleaned
    for line in lines:
        stripped = line.strip()
        if stripped:
            return _clean_command_line(stripped)

    return None


def split_compound_commands(command: str) -> list[str]:
    """
    Split a compound command block that contains multiple independent commands.
    E.g., multiple python -c commands or multiple curl commands concatenated.
    Returns a list of individual commands to execute separately.
    """
    # If it's a single line, return as-is
    if "\n" not in command:
        return [command]

    lines = command.strip().split("\n")

    # Check if this looks like multiple independent commands (not a script)
    # Each line starts with a command (python, curl, nmap, etc.)
    command_starters = (
        "python", "python3", "curl", "wget", "nmap", "dig", "whois",
        "nslookup", "ping", "traceroute", "nikto", "sqlmap", "gobuster",
        "ffuf", "nuclei", "httpx", "subfinder", "amass", "masscan",
        "echo", "cat", "grep", "find", "ls", "dir",
    )

    independent_lines = []
    script_block = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # If the line starts with a known command, it's likely independent
        first_word = stripped.split()[0] if stripped.split() else ""
        if first_word in command_starters or first_word.startswith("python"):
            independent_lines.append(stripped)
        else:
            # Looks like a continuation or script — treat entire block as one command
            script_block = True
            break

    if script_block or len(independent_lines) <= 1:
        return [command]

    return independent_lines


class CommandResult:
    """Result of a command execution."""

    def __init__(self, command: str, stdout: str, stderr: str, exit_code: int):
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.success = exit_code == 0

    @property
    def output(self) -> str:
        """Combined stdout and stderr."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)

    def __str__(self) -> str:
        status = "OK" if self.success else f"FAIL(exit={self.exit_code})"
        return f"[{status}] {self.command}\n{self.output}"


# Commands that could cause serious system damage
DANGEROUS_COMMANDS = [
    "rm -rf /",
    "mkfs",
    "dd if=/dev/zero",
    ":(){:|:&};:",
    "chmod -R 777 /",
    "mv / ",
    "shutdown",
    "reboot",
    "halt",
    "init 0",
    "init 6",
]


class ToolExecutor:
    """
    Executes shell commands and pentesting tools.
    Handles timeouts, output capture, working directory, and safety checks.
    """

    def __init__(
        self,
        work_dir: str = "",
        timeout: int = 300,
        allow_dangerous: bool = False,
        scope_enforcer: Optional[ScopeEnforcer] = None,
    ):
        self.work_dir = Path(work_dir) if work_dir else Path(get_default_work_dir())
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.allow_dangerous = allow_dangerous
        self.scope_enforcer = scope_enforcer
        self.env = os.environ.copy()
        self.env["TERM"] = "dumb"  # Prevent color codes in tool output
        self._active_process: Optional[subprocess.Popen] = None

    def _is_dangerous(self, command: str) -> bool:
        """Check if a command is potentially dangerous."""
        if self.allow_dangerous:
            return False
        cmd_lower = command.lower().strip()
        return any(dangerous in cmd_lower for dangerous in DANGEROUS_COMMANDS)

    def _resolve_user_path(self, user_path: str) -> Path:
        """Resolve a user-supplied path against the executor working directory."""
        return (self.work_dir / user_path).resolve()

    def _validate_user_path(self, action: str, user_path: str) -> tuple[Optional[Path], Optional[CommandResult]]:
        """Ensure resolved paths stay within the configured work directory."""
        logger = get_logger()
        work_dir_resolved = self.work_dir.resolve()
        resolved_path = self._resolve_user_path(user_path)

        if resolved_path != work_dir_resolved and work_dir_resolved not in resolved_path.parents:
            msg = (
                f"Rejected path outside work directory: requested='{user_path}', "
                f"resolved='{resolved_path}', work_dir='{work_dir_resolved}'"
            )
            logger.log_error("executor", msg)
            return None, CommandResult(f"{action}({user_path})", "", msg, 1)

        return resolved_path, None

    def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
    ) -> CommandResult:
        """
        Execute a shell command.

        Args:
            command: The command to execute.
            timeout: Override default timeout (seconds).
            cwd: Working directory override.
            env: Additional environment variables.

        Returns:
            CommandResult with stdout, stderr, and exit code.
        """
        logger = get_logger()

        # Strip markdown code fences that LLMs often wrap commands in
        command = strip_markdown_fences(command)

        # Normalize python3 -> python on Windows
        command = _normalize_python_cmd(command)

        # Windows compatibility shim for common Unix filters.
        command = _adapt_windows_unix_filters(command)

        # Handle python -c code that can't run inline:
        # - Multi-line code (Windows CMD can't handle newlines in -c args)
        # - Compound statements (for/try/while/with can't be semicolon-joined)
        # - Nested quote issues (double quotes inside double quotes)
        python_code = _extract_python_c_code(command)
        if python_code and _needs_temp_file(python_code):
            interpreter = "python" if IS_WINDOWS else "python3"
            script = _unflatten_python(python_code)
            logger.debug(f"Python -c code needs temp file ({interpreter}): compound statements or multi-line")
            return self.execute_script(script, interpreter=interpreter, timeout=timeout)

        # Check for nested quote issues in python -c on Windows
        if IS_WINDOWS and python_code:
            fixed_code = _fix_python_c_quotes(command)
            if fixed_code:
                interpreter = "python"
                logger.debug("Python -c has nested quote issues, routing to temp file")
                return self.execute_script(fixed_code, interpreter=interpreter, timeout=timeout)

        # Other multi-line commands (raw shell scripts): route through temp file
        if "\n" in command and command.strip().count("\n") >= 1:
            if IS_WINDOWS:
                # Detect PowerShell syntax and route through powershell instead of cmd
                if _is_powershell_syntax(command):
                    interpreter = "powershell -ExecutionPolicy Bypass -File"
                    logger.debug("PowerShell syntax detected in multi-line command, using powershell")
                else:
                    interpreter = "cmd /c"
            else:
                interpreter = "bash"
            logger.debug(f"Multi-line command detected, using {interpreter} via temp file")
            return self.execute_script(command, interpreter=interpreter, timeout=timeout)

        # Single-line PowerShell syntax on Windows: route through powershell -Command
        # Guard: don't re-wrap if already a powershell invocation (prevents infinite recursion)
        if IS_WINDOWS and _is_powershell_syntax(command) and not command.lstrip().lower().startswith("powershell"):
            logger.debug("PowerShell syntax detected in single-line command, using powershell -Command")
            ps_cmd = f'powershell -ExecutionPolicy Bypass -Command "{command}"'
            return self.execute(ps_cmd, timeout=timeout, cwd=cwd, env=env)

        if self._is_dangerous(command):
            msg = f"Blocked dangerous command: {command}"
            logger.log_error("executor", msg)
            return CommandResult(command, "", msg, -1)

        exec_timeout = timeout or self.timeout
        exec_cwd = cwd or str(self.work_dir)
        exec_env = {**self.env, **(env or {})}

        logger.debug(f"Executing: {command}")

        try:
            # Platform-specific process creation flags
            popen_kwargs = {
                "shell": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": exec_cwd,
                "env": exec_env,
            }
            if IS_WINDOWS:
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["preexec_fn"] = os.setsid

            process = subprocess.Popen(command, **popen_kwargs)
            self._active_process = process

            try:
                stdout, stderr = process.communicate(timeout=exec_timeout)
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                # Kill the process tree
                if IS_WINDOWS:
                    process.kill()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait()
                stdout, stderr = b"", b"Command timed out"
                exit_code = -1
            finally:
                self._active_process = None

            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()

            result = CommandResult(command, stdout_str, stderr_str, exit_code)

            logger.log_command(
                agent_id="executor",
                command=command,
                output=result.output,
                exit_code=exit_code,
            )

            return result
        except FileNotFoundError:
            msg = f"Command not found: {command.split()[0] if command else command}"
            logger.log_error("executor", msg)
            return CommandResult(command, "", msg, 127)
        except PermissionError:
            msg = f"Permission denied executing: {command}"
            logger.log_error("executor", msg)
            return CommandResult(command, "", msg, 126)
        except Exception as e:
            msg = f"Execution error: {e}"
            logger.log_error("executor", msg)
            return CommandResult(command, "", msg, -1)

    def execute_script(
        self,
        script_content: str,
        interpreter: str = "",
        timeout: Optional[int] = None,
    ) -> CommandResult:
        """
        Execute a multi-line script by writing it to a temp file.

        Args:
            script_content: The script content.
            interpreter: Script interpreter (bash, python3, cmd, etc.).
                         Defaults to python on Windows, bash on Unix.
            timeout: Timeout in seconds.

        Returns:
            CommandResult with output.
        """
        if not interpreter:
            interpreter = "python" if IS_WINDOWS else "bash"

        if self.scope_enforcer and self.scope_enforcer.enabled:
            scope_ok, scope_reason = self.scope_enforcer.check_command(script_content)
            if not scope_ok:
                return CommandResult(
                    command=f"{interpreter} <script>",
                    stdout="",
                    stderr=scope_reason,
                    exit_code=-1,
                )

        # Determine file suffix
        if "python" in interpreter:
            suffix = ".py"
        elif "powershell" in interpreter.lower():
            suffix = ".ps1"
        elif IS_WINDOWS and interpreter.startswith("cmd"):
            suffix = ".bat"
        else:
            suffix = ".sh"

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=suffix,
            dir=str(self.work_dir),
            delete=False,
        ) as f:
            f.write(script_content)
            script_path = f.name

        try:
            if not IS_WINDOWS:
                os.chmod(script_path, 0o700)

            # On Windows with cmd, execute .bat files directly
            if IS_WINDOWS and suffix == ".bat":
                run_cmd = f'"{script_path}"'
            else:
                run_cmd = f"{interpreter} \"{script_path}\""

            return self.execute(
                run_cmd,
                timeout=timeout,
            )
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

    def check_tool_available(self, tool_name: str) -> bool:
        """Check if a tool/command is available on the system."""
        from utils.platform_info import quote_arg
        check_cmd = "where" if IS_WINDOWS else "which"
        result = self.execute(f"{check_cmd} {quote_arg(tool_name)}", timeout=5)
        return result.success

    def interrupt_current(self):
        """Best-effort interrupt of the currently running subprocess."""
        process = self._active_process
        if not process:
            return
        try:
            if IS_WINDOWS:
                process.kill()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            pass

    def write_file(self, path: str, content: str) -> CommandResult:
        """Write content to a file in the work directory."""
        full_path, error_result = self._validate_user_path("write_file", path)
        if error_result:
            return error_result

        assert full_path is not None
        full_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            full_path.write_text(content)
            return CommandResult(
                f"write_file({path})",
                f"Written to {full_path}",
                "",
                0,
            )
        except Exception as e:
            return CommandResult(
                f"write_file({path})",
                "",
                str(e),
                1,
            )

    def read_file(self, path: str) -> CommandResult:
        """Read a file from the work directory."""
        full_path, error_result = self._validate_user_path("read_file", path)
        if error_result:
            return error_result

        assert full_path is not None
        try:
            content = full_path.read_text()
            return CommandResult(f"read_file({path})", content, "", 0)
        except Exception as e:
            return CommandResult(f"read_file({path})", "", str(e), 1)
