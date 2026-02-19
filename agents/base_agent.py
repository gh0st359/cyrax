"""
CYRAX Base Agent
Foundation class for all specialized sub-agents.
"""

import os
import re
import json
from typing import Optional, TYPE_CHECKING

from memory.conversation import ConversationMemory
from tools.tool_registry import ToolRegistry
from tools.browser import BrowserManager, parse_browser_command, is_browser_command, BROWSER_COMMANDS
from tools.executor import sanitize_command
from utils.logging import get_logger
from utils import display
from utils.platform_info import get_platform_context

if TYPE_CHECKING:
    from models.model_manager import ModelManager


def _find_agent_actions(response: str) -> list[tuple[int, str, re.Match]]:
    """Find all action blocks in an agent response, sorted by position."""
    actions = []
    for m in re.finditer(r'\[EXECUTE\]\s*(.*?)\s*\[/EXECUTE\]', response, re.DOTALL):
        actions.append((m.start(), 'execute', m))
    for m in re.finditer(r'\[WRITE_FILE\s+path="([^"]+)"\](.*?)\[/WRITE_FILE\]', response, re.DOTALL):
        actions.append((m.start(), 'write_file', m))
    for m in re.finditer(r'\[FINDING\s+severity="(\w+)"\s+title="([^"]+)"\](.*?)\[/FINDING\]', response, re.DOTALL):
        actions.append((m.start(), 'finding', m))
    for m in re.finditer(r'\[REPORT\](.*?)\[/REPORT\]', response, re.DOTALL):
        actions.append((m.start(), 'report', m))
    actions.sort(key=lambda x: x[0])
    return actions


def _get_failure_guidance(command: str, error: str) -> str:
    """Return contextual error guidance based on the failure type."""
    err_lower = error.lower()
    if "timeout" in err_lower:
        return (
            " Try browser.html() to see the actual page structure, "
            "or use browser.wait() before interacting."
        )
    if "not found" in err_lower or "no such file" in err_lower:
        return " Use [WRITE_FILE] to create needed files."
    if "permission denied" in err_lower:
        return " Try a different approach or check privileges."
    if "connection refused" in err_lower:
        return " The service may not be running. Try scanning ports."
    return " Analyze the error and try a different approach."


class BaseAgent:
    """
    Base class for all CYRAX specialized sub-agents.
    Each agent has its own conversation memory, access to tools,
    and a specialized system prompt.
    """

    def __init__(
        self,
        agent_id: str,
        task: str,
        model: "ModelManager",
        tools: ToolRegistry,
        parent: Optional[object] = None,
        max_iterations: int = 20,
        browser: Optional[BrowserManager] = None,
    ):
        self.agent_id = agent_id
        self.task = task
        self.model = model
        self.tools = tools
        self.parent = parent
        self.max_iterations = max_iterations
        self.browser = browser

        self.memory = ConversationMemory(max_history=30)
        self.status = "initialized"
        self.findings: list[dict] = []
        self.iteration = 0

        # Circuit breaker
        self._failed_commands: list[str] = []
        self._consecutive_failures = 0
        self._max_consecutive_failures = 3
        self._failed_pattern_counts: dict[str, int] = {}
        self._consecutive_empty_iters: int = 0

        # Per-iteration tracking
        self._recent_cmds: list[str] = []
        self._cmds_succeeded: int = 0

        # Scope enforcement (set by parent orchestrator)
        self.scope = None

        # Permission gate (set by parent orchestrator)
        self.permission_gate = None

        # Mission briefing (injected by orchestrator at spawn time)
        self.mission_briefing: str = ""

        # Graceful shutdown flag (set by signal handler in subprocess mode)
        self._shutdown_requested: bool = False

        # IPC client for subprocess mode (set by process_runner)
        self.ipc_client = None

    def _build_agent_prompt(self) -> str:
        """Build the system prompt for this agent. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement _build_agent_prompt")

    @staticmethod
    def _get_cmd_pattern(command: str) -> str:
        """Extract a pattern signature from a command for failure dedup.

        The pattern should be specific enough to avoid blocking unrelated
        commands — e.g., 'curl http://host/path1' failing should NOT block
        'curl http://host/path2'. Include the URL/target in the pattern.
        """
        cmd = command.strip()
        # Browser commands: use full method call as pattern
        m = re.match(r"(browser\.\w+\([^)]*\))", cmd)
        if m:
            return m.group(1)
        m2 = re.match(r"(browser\.\w+)\(", cmd)
        if m2:
            return m2.group(1)
        # Shell commands: use tool + URL/target (not just first flag)
        parts = cmd.split()
        if not parts:
            return cmd[:40]
        tool = parts[0]
        # Find URLs in the command for a specific pattern
        url_match = re.search(r'(https?://\S+)', cmd)
        if url_match:
            return f"{tool}:{url_match.group(1)[:60]}"
        # Find IPs
        ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', cmd)
        if ip_match:
            return f"{tool}:{ip_match.group(1)}"
        # Fallback: tool + significant argument (skip flags starting with -)
        for part in parts[1:]:
            if not part.startswith("-") and len(part) > 2:
                return f"{tool}:{part[:30]}"
        return tool

    def _record_failure(self, command: str):
        """Record a command failure."""
        self._failed_commands.append(command)
        self._consecutive_failures += 1
        pattern = self._get_cmd_pattern(command)
        self._failed_pattern_counts[pattern] = self._failed_pattern_counts.get(pattern, 0) + 1

    def _check_repeated_failure(self, command: str) -> Optional[str]:
        """Progressive backoff for repeated failures."""
        pattern = self._get_cmd_pattern(command)
        count = self._failed_pattern_counts.get(pattern, 0)
        if count == 2:
            return (
                f"[Action Feedback] The pattern '{pattern}' has failed {count} times. "
                f"Try something fundamentally different."
            )
        if count >= 3:
            return (
                f"[Action Feedback] The pattern '{pattern}' has failed {count} times. "
                f"Execution blocked. Use a completely different tool or technique."
            )
        return None

    def _get_tool_instructions(self) -> str:
        """Get tool usage instructions for the system prompt."""
        platform_context = get_platform_context()

        # Use actual target from parent scope if available
        target = "TARGET"
        if self.scope and self.scope._raw_targets:
            target = self.scope._raw_targets[0]
        elif self.parent and hasattr(self.parent, 'campaign') and self.parent.campaign.target:
            target = self.parent.campaign.target

        scope_line = ""
        if self.scope and self.scope.enabled:
            scope_line = f"\nAUTHORIZED SCOPE: {self.scope.get_scope_description()}\nAll actions MUST target only in-scope hosts.\n"

        briefing_block = ""
        if self.mission_briefing:
            briefing_block = f"\n{self.mission_briefing}\n"

        return f"""
{platform_context}
{scope_line}
{briefing_block}
ACTIONS — How to do things:

Execute a command (ONE per block):
[EXECUTE] command_here [/EXECUTE]

Write a script file:
[WRITE_FILE path="script.py"]
import httpx
# your code here
[/WRITE_FILE]
Then run it: [EXECUTE] python script.py [/EXECUTE]

Browser interaction:
[EXECUTE] browser.goto("http://{target}") [/EXECUTE]
[EXECUTE] browser.content() [/EXECUTE]
[EXECUTE] browser.forms() [/EXECUTE]

Browser commands: browser.goto(url), browser.back(), browser.refresh(),
  browser.content(), browser.html(), browser.title(), browser.url(),
  browser.click(selector), browser.fill(selector, value), browser.type(selector, text),
  browser.press(key), browser.select(selector, value), browser.submit(),
  browser.screenshot(), browser.query(selector), browser.links(), browser.forms(),
  browser.evaluate("js"), browser.cookies(), browser.set_cookie(name, value, domain),
  browser.crawl(url, max_pages=30), browser.test_xss(url, param), browser.new_tab(url)
These are the ONLY browser commands. Do NOT invent methods.

Report a finding (with evidence):
[FINDING severity="critical|high|medium|low|info" title="Title"]
Evidence from command output here.
[/FINDING]

Report to orchestrator: [REPORT] summary [/REPORT]
Mark task complete: [TASK_COMPLETE] summary [/TASK_COMPLETE]

RULES:
- ALWAYS write 1-2 sentences of reasoning BEFORE your action blocks explaining what you're doing and why.
- ONE command per [EXECUTE] block. No comments, no markdown, no extra text inside.
- For Python scripts, use [WRITE_FILE] then [EXECUTE] python script.py.
- NEVER fabricate findings. Cite exact command + output as evidence.
- NEVER tell the user to do things manually. YOU are the operator.
- ALWAYS call browser.links() or browser.forms() BEFORE constructing URLs or interacting with forms.
- NEVER guess URL paths from page display text — use browser.links() to get actual hrefs.
- After navigating to a new page, check what's on it BEFORE trying to fill forms.
- If a command fails, analyze the error and try a different approach. NEVER retry the same command.
- Do NOT write plans, step lists, or "Next Steps" sections. ACT immediately.
- On Windows: use 'findstr' not 'grep', 'type' not 'cat'. PowerShell syntax does NOT work in cmd.exe.
- For complex logic, write a Python script — it works the same on all platforms.

WRONG — guessing URLs from page text (causes 404 errors):
  [EXECUTE] browser.goto("http://target/vulnerabilities/SQL Injection/") [/EXECUTE]
CORRECT — use browser.links() to discover actual paths:
  [EXECUTE] browser.links() [/EXECUTE]
  Then use the real href from the output.
"""

    def execute(self) -> dict:
        """Execute the assigned task autonomously."""
        logger = get_logger()
        self.status = "active"
        logger.log_event("agent_started", self.agent_id, {"task": self.task})

        system_prompt = self._build_agent_prompt()

        self.memory.add_message(
            "user",
            f"Execute the following task:\n{self.task}",
        )

        while self.iteration < self.max_iterations:
            # Check for graceful shutdown request
            if self._shutdown_requested:
                self.status = "killed"
                return self._build_report(
                    "Agent shutdown requested. Returning current findings."
                )

            self.iteration += 1

            try:
                response = self.model.generate(
                    system=system_prompt,
                    messages=self.memory.get_messages(),
                )
            except Exception as e:
                logger.log_error(self.agent_id, f"Model generation failed: {e}")
                self.status = "failed"
                return self._build_report(f"Model error: {e}")

            self._display_response(response)
            self.memory.add_message("assistant", response)

            # Check if task is complete
            if "[TASK_COMPLETE]" in response:
                self.status = "completed"
                complete_match = re.search(
                    r"\[TASK_COMPLETE\](.*?)\[/TASK_COMPLETE\]",
                    response,
                    re.DOTALL,
                )
                summary = complete_match.group(1).strip() if complete_match else response
                return self._build_report(summary)

            # Process actions
            from tools.executor import strip_markdown_fences, split_compound_commands
            actions = _find_agent_actions(response)
            had_actions = False

            for pos, action_type, match in actions:
                if action_type == "write_file":
                    had_actions = True
                    file_path = match.group(1).strip()
                    content = strip_markdown_fences(match.group(2).strip())
                    result = self.tools.executor.write_file(file_path, content)
                    display.show_tool_output(self.agent_id, result.output)
                    self.memory.add_message(
                        "user",
                        f"[File Write Result]\nPath: {file_path}\n"
                        f"Success: {result.success}\nOutput: {result.output}",
                    )

                elif action_type == "execute":
                    raw_cmd = match.group(1).strip()
                    if not raw_cmd:
                        continue
                    raw_cmd = strip_markdown_fences(raw_cmd)
                    # Sanitize: strip comments, nested tags, markdown prose
                    sanitized_cmd = sanitize_command(raw_cmd)
                    if not sanitized_cmd:
                        logger.info(f"[{self.agent_id}] EXECUTE block had no valid command: {raw_cmd[:80]}")
                        continue
                    cmds = split_compound_commands(sanitized_cmd)
                    for command in cmds:
                        command = command.strip()
                        if not command:
                            continue

                        # Duplicate detection
                        if command in self._recent_cmds:
                            dup_msg = (
                                f"[Action Feedback] Duplicate command blocked: '{command[:60]}' "
                                f"was already executed. Try a different command."
                            )
                            self.memory.add_message("user", f"[Tool Result]\n{dup_msg}")
                            continue
                        self._recent_cmds.append(command)
                        had_actions = True

                        display.show_execution(self.agent_id, command)

                        # Check repeated failure (progressive backoff)
                        blocked_msg = self._check_repeated_failure(command)
                        if blocked_msg:
                            display.show_tool_output(self.agent_id, blocked_msg)
                            result_msg = blocked_msg
                            self.memory.add_message("user", f"[Tool Result]\n{result_msg}")
                            continue

                        # Permission gate check (inherited from orchestrator)
                        if self.permission_gate:
                            perm_ok, perm_reason = self.permission_gate.check(command)
                            if not perm_ok:
                                result_msg = f"[Permission Denied] {perm_reason}"
                                display.show_tool_output(self.agent_id, result_msg)
                                self.memory.add_message("user", f"[Tool Result]\n{result_msg}")
                                continue

                        if (browser_parsed := parse_browser_command(command)):
                            # Scope check for browser.goto()
                            method_name = browser_parsed[0]
                            args = browser_parsed[1]
                            if method_name == "goto" and args and self.scope:
                                allowed, reason = self.scope.check_browser_navigation(args[0])
                                if not allowed:
                                    result_msg = f"[Scope Violation] {reason}"
                                    display.show_tool_output(self.agent_id, result_msg)
                                    self.memory.add_message("user", f"[Tool Result]\n{result_msg}")
                                    continue

                            with display.get_spinner("Executing..."):
                                result_msg = self._execute_browser_command(
                                    command, browser_parsed, logger
                                )
                        elif is_browser_command(command):
                            method_match = re.match(r"browser\.(\w+)\(", command)
                            bad_method = method_match.group(1) if method_match else "unknown"
                            valid_methods = ", ".join(sorted(BROWSER_COMMANDS.keys()))
                            result_msg = (
                                f"Error: 'browser.{bad_method}()' does not exist.\n"
                                f"Valid methods: {valid_methods}\n"
                                f"Do NOT invent browser methods."
                            )
                            display.show_tool_output(self.agent_id, result_msg)
                            self._record_failure(command)
                        else:
                            # Shell command — scope check
                            if self.scope:
                                scope_ok, scope_reason = self.scope.check_command(command)
                                if not scope_ok:
                                    result_msg = f"[Scope Violation] {scope_reason}"
                                    display.show_tool_output(self.agent_id, result_msg)
                                    self.memory.add_message("user", f"[Tool Result]\n{result_msg}")
                                    continue

                            with display.get_spinner("Executing..."):
                                result = self.tools.execute_raw(command)
                            display.show_tool_output(self.agent_id, result.output)
                            logger.log_command(
                                self.agent_id, command, result.output, result.exit_code
                            )
                            if result.success:
                                result_msg = (
                                    f"Command: {command}\n"
                                    f"Exit code: {result.exit_code}\n"
                                    f"Output:\n{result.output}"
                                )
                                self._consecutive_failures = 0
                                self._cmds_succeeded += 1
                            else:
                                guidance = _get_failure_guidance(command, result.output)
                                result_msg = (
                                    f"Command: {command}\n"
                                    f"Exit code: {result.exit_code}\n"
                                    f"Output:\n{result.output}\n"
                                    f"{guidance}"
                                )
                                self._record_failure(command)

                        self.memory.add_message("user", f"[Tool Result]\n{result_msg}")

                elif action_type == "finding":
                    had_actions = True
                    title = match.group(2)
                    details = match.group(3).strip()

                    # Fabricated finding prevention
                    if self._cmds_succeeded == 0 and self.iteration > 1:
                        self.memory.add_message(
                            "user",
                            f"[Action Feedback] WARNING: Your finding '{title}' appears fabricated. "
                            f"No commands have succeeded to back it up. "
                            f"Run commands, verify results, THEN report findings.",
                        )
                        continue

                    finding = {
                        "severity": match.group(1),
                        "title": title,
                        "details": details,
                        "agent_id": self.agent_id,
                    }
                    self.findings.append(finding)
                    # Forward finding via IPC in subprocess mode
                    if self.ipc_client:
                        from agents.ipc import IPCMessage
                        self.ipc_client.send(IPCMessage(
                            "finding", self.agent_id, finding,
                        ))
                    display.show_finding(
                        finding["severity"], finding["title"], finding["details"]
                    )
                    logger.log_finding(
                        self.agent_id,
                        finding["severity"],
                        finding["title"],
                        finding["details"],
                    )

                elif action_type == "report":
                    had_actions = True
                    update_text = match.group(1).strip()
                    if self.parent and hasattr(self.parent, "receive_agent_update"):
                        self.parent.receive_agent_update(
                            self.agent_id, update_text
                        )
                    elif self.ipc_client:
                        from agents.ipc import IPCMessage
                        self.ipc_client.send(IPCMessage(
                            "report", self.agent_id, {"update": update_text},
                        ))

            # Circuit breaker with contextual guidance
            if self._consecutive_failures >= self._max_consecutive_failures:
                failed_summary = "\n".join(
                    f"  - {cmd}" for cmd in self._failed_commands[-5:]
                )
                self.memory.add_message(
                    "user",
                    f"[Action Feedback] {self._consecutive_failures} consecutive commands have failed.\n"
                    f"Recent failures:\n{failed_summary}\n\n"
                    "Try a completely different approach — write a Python script, "
                    "use alternative tools, or if blocked, use [TASK_COMPLETE] to report what you found.",
                )
                # Don't reset to 0 — allow warnings to escalate if failures continue
                self._consecutive_failures = max(0, self._consecutive_failures - 3)

            # No-action detection
            if not had_actions and "[REPORT]" not in response:
                self._consecutive_empty_iters += 1
                if self._consecutive_empty_iters >= 3:
                    self.status = "completed"
                    logger.warning(
                        f"Agent {self.agent_id} stalled: 3 iterations with no actions"
                    )
                    return self._build_report(
                        "Agent stalled (no commands executed for 3 iterations). "
                        "Returning partial findings."
                    )
                self.memory.add_message(
                    "user",
                    "[Action Feedback] Your response contained no action blocks. "
                    "Include [EXECUTE] or [WRITE_FILE] blocks to make progress. "
                    "If done, use [TASK_COMPLETE] to report findings.",
                )
            else:
                self._consecutive_empty_iters = 0

        # Max iterations reached
        self.status = "completed"
        logger.warning(f"Agent {self.agent_id} reached max iterations ({self.max_iterations})")
        return self._build_report("Max iterations reached. Returning current findings.")

    def _display_response(self, response: str):
        """Display the agent's response with formatting."""
        reasoning_matches = re.findall(
            r"\[Reasoning\](.*?)\[/Reasoning\]", response, re.DOTALL
        )
        for reasoning in reasoning_matches:
            display.show_reasoning(self.agent_id, reasoning.strip())

        cleaned = response
        for pattern in [
            r"\[Reasoning\].*?\[/Reasoning\]",
            r"\[EXECUTE\].*?\[/EXECUTE\]",
            r"\[WRITE_FILE.*?\[/WRITE_FILE\]",
            r"\[FINDING.*?\[/FINDING\]",
            r"\[REPORT\].*?\[/REPORT\]",
            r"\[TASK_COMPLETE\].*?\[/TASK_COMPLETE\]",
        ]:
            cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL)
        cleaned = cleaned.strip()
        if cleaned:
            display.show_agent_message(self.agent_id, cleaned)

    def _build_report(self, summary: str) -> dict:
        """Build the final agent report."""
        return {
            "agent_id": self.agent_id,
            "task": self.task,
            "status": self.status,
            "iterations": self.iteration,
            "summary": summary,
            "findings": self.findings,
            "pid": os.getpid(),
        }

    def _execute_browser_command(
        self, raw_command: str, parsed: tuple, logger
    ) -> str:
        """Execute a parsed browser command and return the result message."""
        method_name, args, kwargs = parsed

        if not self.browser:
            work_dir = self.tools.executor.work_dir if hasattr(self.tools, "executor") else "/tmp/cyrax"
            self.browser = BrowserManager(work_dir=str(work_dir))
            if self.parent and hasattr(self.parent, "browser") and self.parent.browser is None:
                self.parent.browser = self.browser

        # Fix browser.type -> type_text mapping
        actual_method_name = method_name
        if method_name == "type":
            actual_method_name = "type_text"

        method = getattr(self.browser, actual_method_name, None)
        if not method:
            valid_methods = ", ".join(sorted(BROWSER_COMMANDS.keys()))
            output = (
                f"Unknown browser command: {method_name}\n"
                f"Valid methods: {valid_methods}"
            )
            display.show_tool_output(self.agent_id, output)
            self._record_failure(raw_command)
            return f"Command: {raw_command}\nError: {output}"

        try:
            result = method(*args, **kwargs)
            display.show_tool_output(self.agent_id, result.output)
            logger.log_command(
                self.agent_id, raw_command, result.output, 0 if result.success else 1
            )
            if result.success:
                self._consecutive_failures = 0
                self._cmds_succeeded += 1
                return (
                    f"Command: {raw_command}\n"
                    f"Success: {result.success}\n"
                    f"Output:\n{result.output}"
                )
            else:
                guidance = _get_failure_guidance(raw_command, result.error or "")
                self._record_failure(raw_command)
                return (
                    f"Command: {raw_command}\n"
                    f"Success: False\n"
                    f"Output:\n{result.output}\n"
                    f"{guidance}"
                )
        except Exception as e:
            error_msg = f"Browser error: {e}"
            display.show_tool_output(self.agent_id, error_msg)
            logger.log_error(self.agent_id, error_msg)
            self._record_failure(raw_command)
            guidance = _get_failure_guidance(raw_command, str(e))
            return f"Command: {raw_command}\nError: {error_msg}\n{guidance}"

    def receive_instruction(self, instruction: str):
        """Receive an instruction from the orchestrator mid-execution."""
        self.memory.add_message(
            "user", f"[Orchestrator Instruction]: {instruction}"
        )

    def request_shutdown(self):
        """Request graceful shutdown (called from signal handler or IPC)."""
        self._shutdown_requested = True
