# CYRAX Architecture Overhaul Plan

## The Core Problem

CYRAX currently feels like a **hardcoded automation loop**, not an intelligent agent. The symptoms:

1. The AI talks to itself via system-injected "user" messages that confuse it
2. The user cannot type, interrupt, or interact while the AI is running
3. Sub-agents run synchronously and block everything
4. No permission system - the AI can attack `target.com` from a prompt example without asking
5. Hardcoded nudges ("you MUST use subagents now") override the AI's judgment
6. The AI hallucinates browser methods, retries endlessly, and produces identical responses
7. No visible reasoning - the user can't see what the AI is thinking
8. The system prompt buries critical information under walls of text

The fix is not more patches on the current loop. It's a **structural overhaul** of how the human-AI-tool interaction works.

---

## Design Philosophy

**Stop fighting the model. Start enabling it.**

The current approach treats the LLM like a unreliable script executor that needs constant prodding. Every failure triggers another injected "[SYSTEM]" message, another hardcoded nudge, another circuit breaker. The model sees a conversation that's 80% system warnings and 20% actual work.

The new approach:
- **Clean context**: The model sees a clear conversation with real results, not injected warnings
- **User in control**: The user can always type, always interrupt, always redirect
- **Smart constraints**: Instead of telling the model what NOT to do (don't plan, don't repeat), give it clear tools and let it work
- **Visible reasoning**: Show the user what the AI is thinking so they can course-correct early
- **Safe by default**: Dangerous actions require confirmation. Scope violations are blocked before execution, not after.

---

## Architecture Overview: Before and After

### Current Architecture
```
User Input → chat() → _stream_response() → _process_response() loop:
  ├── _execute_actions() → browser/shell → results
  ├── Inject results as "user" message
  ├── _stream_response() again
  └── Repeat up to 8 times
  User cannot interact during this entire cycle.
  Auto-continue constructs fake "user" messages.
```

### New Architecture
```
┌─────────────────────────────────────────────────┐
│                  TUI Layer (Textual)             │
│  ┌──────────────────────────────────────────┐    │
│  │  Output Pane (scrollable)                │    │
│  │  - AI reasoning (streamed live)          │    │
│  │  - Action panels (EXECUTE, FINDING...)   │    │
│  │  - Sub-agent status cards                │    │
│  │  - Tool output (collapsible)             │    │
│  ├──────────────────────────────────────────┤    │
│  │  Input Bar (always active)               │    │
│  │  > User can type at any time             │    │
│  │  > Slash commands always available        │    │
│  │  > Ctrl+C pauses current operation       │    │
│  └──────────────────────────────────────────┘    │
└──────────┬──────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────┐
│              Orchestrator (async)                 │
│  - Manages conversation state                    │
│  - Routes actions to executors                   │
│  - Enforces scope/permission gates               │
│  - Coordinates sub-agents via asyncio            │
│  - Processes user interrupts                     │
└──────────┬──────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────┐
│           Execution Layer                        │
│  ┌─────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ Browser  │ │  Shell   │ │  Sub-Agents      │  │
│  │ (shared) │ │ executor │ │  (async, pooled) │  │
│  └─────────┘ └──────────┘ └──────────────────┘  │
└─────────────────────────────────────────────────┘
```

---

## Phase 1: Safety First — Scope Enforcement & Permission Gates

**Priority: CRITICAL**
**Files: `cyrax.py`, `agents/base_agent.py`, new `utils/safety.py`**

This is non-negotiable. The `target.com` incident proves the system can attack arbitrary domains. This must be fixed before anything else.

### 1a. Create `utils/safety.py` — Scope & Permission Engine

```python
class ScopeEnforcer:
    """Ensures all operations stay within the authorized target scope."""

    def __init__(self, targets: list[str]):
        # targets: ["192.168.1.0/24", "example.com", "*.example.com"]
        self.allowed_domains: set[str] = set()
        self.allowed_ip_ranges: list[ipaddress.IPv4Network] = []
        self.allowed_url_patterns: list[re.Pattern] = []
        self._parse_targets(targets)

    def is_in_scope(self, url_or_ip: str) -> bool:
        """Returns True if the target is within authorized scope."""
        # Parse URL to extract domain/IP
        # Check against allowed_domains (including wildcard subdomain matching)
        # Check against allowed_ip_ranges
        # Check against localhost/internal ranges only if explicitly allowed
        ...

    def check_command(self, command: str) -> tuple[bool, str]:
        """Check if a shell command targets in-scope resources.
        Returns (allowed, reason)."""
        # Extract URLs, IPs, domains from the command string
        # Check each against scope
        # Return (False, "target.com is not in scope") if any target is out-of-scope
        ...

    def check_browser_navigation(self, url: str) -> tuple[bool, str]:
        """Check if a browser navigation stays in scope."""
        ...

class PermissionGate:
    """Asks user for confirmation before dangerous actions."""

    # Action categories and their default permission level
    ACTIONS = {
        "attack_payload":  "ask",     # SQL injection, XSS, command injection payloads
        "credential_use":  "ask",     # Using found credentials
        "file_write":      "allow",   # Writing scripts to workdir
        "file_write_outside": "deny", # Writing outside workdir
        "network_scan":    "ask",     # Port scans, enumeration
        "exploit_launch":  "ask",     # Running exploits (Metasploit, custom)
        "privilege_esc":   "ask",     # Privilege escalation attempts
        "lateral_move":    "ask",     # Moving to another host
        "data_exfil":      "deny",    # Extracting sensitive data
        "agent_spawn":     "allow",   # Spawning sub-agents
        "browser_navigate": "allow",  # Basic browsing (in scope)
    }

    def __init__(self, display, auto_approve: bool = False):
        self.display = display
        self.auto_approve = auto_approve  # --auto flag for fully autonomous mode
        self.session_approvals: dict[str, str] = {}  # "remember" decisions

    async def check(self, action_type: str, description: str) -> bool:
        """Returns True if the action is permitted."""
        if self.auto_approve:
            return True
        level = self.session_approvals.get(action_type, self.ACTIONS.get(action_type, "ask"))
        if level == "allow":
            return True
        if level == "deny":
            self.display.show_blocked(action_type, description)
            return False
        # level == "ask"
        return await self.display.prompt_permission(action_type, description)
```

### 1b. Integrate Scope Enforcement

**In `cyrax.py` `_execute_actions()`:**
- Before every `browser.goto()` call: `self.scope.check_browser_navigation(url)`
- Before every shell command: `self.scope.check_command(command)`
- If out of scope: return error to model: `"[Scope Violation] target.com is not in your authorized scope. Your targets are: {scope_list}"`

**In `agents/base_agent.py` `execute()` loop:**
- Same scope checks. Sub-agents receive the `ScopeEnforcer` instance from the parent.

**In `_build_system_prompt()`:**
- Replace ALL `target.com` examples with `{{TARGET}}` and substitute the actual campaign target at runtime
- Add: `"Your authorized scope is: {targets}. ALL commands and navigation MUST target only these hosts. The system will block any out-of-scope requests."`

### 1c. Permission Gate Integration

**In `_execute_actions()`:**
- Classify each action (attack_payload detection: check for SQL injection patterns, XSS payloads, etc.)
- Call `self.permission_gate.check(action_type, description)` before execution
- If denied: return to model: `"[Permission Denied] The user declined this action: {description}. Try a different approach or ask for guidance."`

**Display for permission prompts:**
```
┌─ Permission Required ─────────────────────────────────┐
│ CYRAX wants to: Test SQL injection on login form       │
│ Command: browser.fill("input[name='user']", "' OR 1=1 │
│                                                        │
│ [Y] Allow  [N] Deny  [A] Allow all of this type       │
└────────────────────────────────────────────────────────┘
```

### 1d. Fix the `target.com` Prompt Examples

In `_build_system_prompt()`, replace every hardcoded `target.com` and `target` with the actual campaign target:

```python
# At the top of _build_system_prompt():
target_example = self.campaign.target or "TARGET_IP_OR_DOMAIN"

# Then in all examples:
f'[EXECUTE] curl -s -I https://{target_example} [/EXECUTE]'
f'browser.goto("https://{target_example}")'
# etc.
```

This eliminates the entire class of "prompt-example-becomes-real-target" bugs.

---

## Phase 2: Interactive TUI — Always-Available User Input

**Priority: HIGH**
**Files: new `ui/app.py`, new `ui/widgets.py`, refactor `utils/display.py`, refactor `cyrax.py` run()**

The current architecture makes it impossible for the user to interact during AI execution. `prompt_user()` is a blocking `console.input()` call. Streaming writes directly to `console.file`. There is no mechanism for concurrent input and output.

### 2a. Adopt Textual as the TUI Framework

[Textual](https://github.com/Textualize/textual) is built on top of Rich (which CYRAX already uses) and provides:
- True concurrent input/output via an async event loop
- A persistent input bar that's always active
- Scrollable output pane
- Key bindings and command palette
- CSS-based styling (uses the same Rich renderables)

**Why Textual and not raw Rich:**
- Rich has no built-in concurrent I/O — it's a rendering library, not a TUI framework
- Textual is by the same author (Will McGuinness), uses Rich internally
- Migration cost is low because all Rich renderables (Panel, Table, Markdown, Syntax) work inside Textual widgets

### 2b. TUI Layout

```python
# ui/app.py
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, Input
from textual.containers import Vertical

class CyraxApp(App):
    CSS = """
    #output { height: 1fr; overflow-y: scroll; }
    #input-bar { dock: bottom; height: 3; }
    """

    BINDINGS = [
        ("ctrl+c", "pause", "Pause AI"),
        ("ctrl+d", "quit", "Exit"),
        ("escape", "focus_input", "Focus input"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="output", highlight=True, markup=True, wrap=True)
        yield Input(id="input-bar", placeholder="Type a command or message...")
        yield Footer()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """User pressed Enter in the input bar."""
        user_text = event.value
        event.input.value = ""  # Clear input

        if user_text.startswith("/"):
            await self.handle_slash_command(user_text)
        else:
            await self.send_to_orchestrator(user_text)

    def action_pause(self) -> None:
        """Ctrl+C handler — pause the current operation."""
        self.orchestrator.request_pause()
        self.query_one("#output").write("[bold yellow]Paused. Type to continue or /exit to quit.[/]")
```

**Key properties:**
- `RichLog` widget accepts any Rich renderable via `.write()` — panels, tables, markdown all work
- `Input` widget is always focused and active — user can type while output streams
- The event loop is `asyncio`-based — no blocking calls
- `RichLog` auto-scrolls but the user can scroll up to read history

### 2c. Streaming Tokens into the TUI

Replace the current `display.stream_token()` (which writes directly to `console.file`) with:

```python
# In ui/app.py
class CyraxApp(App):
    def stream_ai_token(self, token: str):
        """Called by the orchestrator for each streaming token."""
        output = self.query_one("#output", RichLog)
        # RichLog supports incremental text appending
        output.write(token, expand=True, scroll_end=True)
```

The `RichLog` widget handles scrolling, wrapping, and rendering. The input bar remains active because Textual's event loop processes input events between output writes.

### 2d. Migrate Display Functions

Create a `TUIDisplay` class that wraps the Textual app and provides the same interface as the current `display` module:

```python
class TUIDisplay:
    def __init__(self, app: CyraxApp):
        self.app = app
        self.output = app.query_one("#output", RichLog)

    def show_execution(self, agent_id, command):
        panel = Panel(Syntax(command, "bash", theme="monokai"),
                     title=f"{agent_id} executing", border_style="cyan")
        self.output.write(panel)

    def show_tool_output(self, agent_id, output, truncate=2000):
        text = output[:truncate] + "..." if len(output) > truncate else output
        panel = Panel(text, title=f"{agent_id} output", border_style="green")
        self.output.write(panel)

    def show_finding(self, severity, title, details):
        color = {"critical": "red", "high": "orange", "medium": "yellow",
                "low": "blue", "info": "white"}[severity.lower()]
        panel = Panel(details, title=f"[{severity.upper()}] {title}", border_style=color)
        self.output.write(panel)

    async def prompt_permission(self, action_type, description) -> bool:
        """Non-blocking permission prompt using Textual's built-in modal."""
        # Use Textual's screen push for a modal dialog
        result = await self.app.push_screen_wait(
            PermissionScreen(action_type, description)
        )
        return result

    # ... all other display methods adapted to write to RichLog
```

### 2e. Fallback: Keep Rich-Only Mode

Not everyone wants a full TUI. Keep the current Rich console mode as a fallback:

```python
# cyrax.py
def main():
    args = parse_args()
    if args.simple or not sys.stdin.isatty():
        # Pipe mode or --simple flag: use current Rich-based display
        orchestrator = CyraxOrchestrator(config, display=RichDisplay())
        orchestrator.run()
    else:
        # Interactive mode: use Textual TUI
        app = CyraxApp(config)
        app.run()
```

This means the entire current `display.py` is preserved as `RichDisplay` and works as-is for non-interactive/pipe use cases.

---

## Phase 3: Async Sub-Agents — True Parallelism

**Priority: HIGH**
**Files: `cyrax.py`, `agents/base_agent.py`, new `agents/agent_pool.py`**

Currently all sub-agents run synchronously via `agent.execute()` which blocks the orchestrator. The user sees nothing until the agent completes. No parallelism is possible.

### 3a. Create `agents/agent_pool.py` — Async Agent Manager

```python
import asyncio
from typing import Optional

class AgentPool:
    """Manages concurrent sub-agent execution."""

    def __init__(self, max_concurrent: int = 3, display=None):
        self.max_concurrent = max_concurrent
        self.display = display
        self._running: dict[str, asyncio.Task] = {}
        self._results: dict[str, dict] = {}
        self._status_queue = asyncio.Queue()  # For streaming status updates to TUI

    async def spawn(self, agent: BaseAgent) -> str:
        """Launch an agent asynchronously. Returns agent_id immediately."""
        if len(self._running) >= self.max_concurrent:
            # Wait for a slot to open
            done, _ = await asyncio.wait(
                self._running.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                self._collect_result(task)

        task = asyncio.create_task(self._run_agent(agent))
        self._running[agent.agent_id] = task
        return agent.agent_id

    async def _run_agent(self, agent: BaseAgent) -> dict:
        """Run agent in a thread (since model.generate() is blocking)."""
        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(None, agent.execute)
        await self._status_queue.put({
            "type": "agent_complete",
            "agent_id": agent.agent_id,
            "report": report
        })
        return report

    async def wait_all(self) -> list[dict]:
        """Wait for all running agents to complete."""
        if self._running:
            done, _ = await asyncio.wait(self._running.values())
            for task in done:
                self._collect_result(task)
        return list(self._results.values())

    async def get_status_updates(self):
        """Async generator yielding status updates for the TUI."""
        while True:
            update = await self._status_queue.get()
            yield update
            if update.get("type") == "pool_shutdown":
                break
```

### 3b. Make Sub-Agent Updates Stream to the TUI

Each sub-agent calls `self.parent.receive_agent_update()` during execution. In the new architecture, this pushes to the status queue:

```python
# In base_agent.py
def _send_update(self, message: str):
    """Send an interim update to the parent/display."""
    if self.parent and hasattr(self.parent, '_agent_pool'):
        # Queue the update for async processing
        import asyncio
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(
            self.parent._agent_pool._status_queue.put_nowait,
            {"type": "agent_update", "agent_id": self.agent_id, "message": message}
        )
```

The TUI displays these as live status cards:

```
┌─ RECON-00 (active) ──────────────────────────────────┐
│ Task: Enumerate subdomains for 192.168.1.100          │
│ Iteration: 3/20 | Findings: 2 | Last: Found phpinfo  │
└───────────────────────────────────────────────────────┘
┌─ WEB-01 (active) ────────────────────────────────────┐
│ Task: Test SQL injection on /login                    │
│ Iteration: 5/20 | Findings: 1 | Last: SQLi confirmed │
└───────────────────────────────────────────────────────┘
```

### 3c. Orchestrator Integration

```python
# In cyrax.py
class CyraxOrchestrator:
    def __init__(self, ...):
        self._agent_pool = AgentPool(max_concurrent=3, display=self.display)

    async def _spawn_and_run_agent(self, agent_type, task):
        """Non-blocking agent spawn."""
        agent = self._create_agent(agent_type, task)
        agent_id = await self._agent_pool.spawn(agent)
        return {"status": "spawned", "agent_id": agent_id,
                "message": f"Agent {agent_id} launched. It will report findings when done."}
```

The model gets an immediate response ("Agent RECON-00 launched") and can continue working. When the agent completes, the results are injected into the next turn's context.

### 3d. Browser Isolation for Parallel Agents

Currently all agents share one `BrowserManager` instance. Parallel agents would collide.

**Solution:** Give each agent that needs browser access its own browser context (not a whole new browser process):

```python
# In browser.py
class BrowserManager:
    def create_isolated_context(self) -> 'BrowserContext':
        """Create a new browser context with isolated cookies/storage."""
        self._ensure_started()
        context = self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
        )
        return BrowserContext(context)

class BrowserContext:
    """Lightweight wrapper around a Playwright BrowserContext.
    Shares the browser process but has isolated cookies, storage, and page state."""
    ...
```

The orchestrator's main browser context handles authentication. Sub-agents get their own contexts but can be passed cookies from the main context if needed.

---

## Phase 4: Remove Hardcoded Behavior — Let the Model Think

**Priority: HIGH**
**Files: `cyrax.py`**

The current system injects fake "user" messages to prod the model. This confuses the model because it sees what looks like user instructions that don't make sense in context. It also removes the model's agency.

### 4a. Remove System-Injected User Messages

**Remove or replace these injections:**

| Current Injection | Line | Replace With |
|---|---|---|
| `"[SYSTEM] Your response contained NO action blocks..."` | 673-679 | Internal handling (see 4b) |
| `"[SYSTEM] Your response is identical..."` | 635-638 | Internal handling (see 4c) |
| `"[SYSTEM WARNING] N consecutive commands have failed..."` | 655-664 | Use `system` role or `tool` result format |
| `"[SYSTEM] You have reached the maximum number..."` | 710-714 | Natural conversation end |
| Auto-continue: `"Continue the campaign..."` with warnings | 1130-1151 | Cleaner continuation (see 4d) |
| Sub-agent forcing: `"IMPORTANT: You have a multi-agent system..."` | 1144-1149 | Remove entirely |

### 4b. No-Action Response Handling — Without Fake Messages

When the model produces no action blocks, instead of injecting a fake user message:

1. **First occurrence:** The orchestrator recognizes this is a planning/thinking response. Display it to the user as reasoning. Then, instead of injecting a system message, append the model's planning text to the conversation and let the auto-continue naturally send the next turn. The model will see its own plan and (usually) start executing.

2. **Second consecutive no-action:** Show the user: "The AI is planning but not executing. Would you like to nudge it or provide direction?" Let the USER decide what to do.

3. **Third consecutive no-action:** Pause the campaign. This is the model not understanding the task, and no amount of system messages will fix it.

### 4c. Deduplication — Without Fake Messages

When the model produces an identical response:

1. Log it internally and don't feed the duplicate back as context
2. Adjust the temperature slightly upward for the next generation (e.g., +0.1, capped at 1.0)
3. If it happens 3 times: inform the user "The AI appears stuck. It may need guidance." and pause for user input

### 4d. Clean Auto-Continue

Replace the current auto-continue (which constructs a fake user message with warnings and nudges) with a minimal, clean continuation:

```python
# New auto-continue
user_input = "Continue."
```

That's it. The model already has:
- The system prompt explaining its role
- The full conversation history with all results
- The campaign state with targets, findings, and phase

It doesn't need a paragraph of instructions every turn. If the model needs more context, it will ask or the user will provide it. The over-prompting in auto-continue is what makes the model feel "hardcoded" — it's being told exactly what to think and do every single turn.

If the model produces no actions after "Continue.":
- The no-action handling from 4b kicks in
- The user is involved naturally

### 4e. Remove the Sub-Agent Forcing

Delete the entire block at lines 1144-1149 that says "IMPORTANT: You have a multi-agent system — USE IT." The model should decide when to spawn agents based on the task, not because a hardcoded counter says it hasn't spawned any yet.

The system prompt already describes sub-agents and when to use them. If the model doesn't use them, it's either because the task doesn't need them or because the model needs better guidance in the system prompt — not a mid-conversation injection.

---

## Phase 5: System Prompt Overhaul — Clarity Over Volume

**Priority: HIGH**
**Files: `cyrax.py` `_build_system_prompt()`**

The current system prompt is ~500 lines. This is far too long. Models pay most attention to the beginning and end. The middle gets compressed. Critical rules get lost.

### 5a. Restructure to Front-Load What Matters

**New prompt structure (in order):**

1. **Identity (2 lines):** Who you are, what you're doing
2. **Action Format (15 lines):** The ONLY thing the model needs to execute: syntax for EXECUTE, WRITE_FILE, SPAWN, FINDING, TASK_COMPLETE
3. **Scope (3 lines):** Your authorized targets. You will be blocked from targeting anything else.
4. **Current State (dynamic):** Campaign state, findings, workspace, browser session
5. **Tool Reference (compact):** Available shell tools, browser methods (as a dense list, not verbose descriptions)
6. **Rules (10 lines max):** Only the rules that ACTUALLY affect behavior. Cut everything the model already knows.
7. **Final directive (1 line):** "Begin." or "Continue."

### 5b. Cut These Sections (They're Not Helping)

- **"EXECUTE FIRST, REPORT AFTER"** — Redundant with the action format section
- **"ANTI-HALLUCINATION"** — Too vague. Replace with specific tool validation (Phase 1)
- **"AUTONOMOUS OPERATION"** — The model knows it's autonomous; telling it again wastes tokens
- **The 10-step RECONNAISSANCE METHODOLOGY** — This is tactical advice that should be in the agent's own memory or a knowledge base, not the system prompt. It biases the model toward a specific playbook instead of thinking
- **WORDLISTS section** — Move to a knowledge base snippet or tool description
- **Multiple redundant "do NOT plan" warnings** — Say it once, clearly, at the top. Repeating it 4 times in different sections is what makes it feel hardcoded

### 5c. Dynamic Target Substitution

Every example in the prompt uses the actual campaign target, not `target.com`:

```python
target = self.campaign.target or "TARGET"
prompt = f"""You are CYRAX, an autonomous AI red team operator.

ACTION FORMAT — Every response must include at least one action block:

[EXECUTE] command_here [/EXECUTE]
[EXECUTE] browser.goto("https://{target}") [/EXECUTE]
[WRITE_FILE path="script.py"] code_here [/WRITE_FILE]
[SPAWN type="recon"] task description [/SPAWN]
[FINDING severity="high" title="Title"] details [/FINDING]

AUTHORIZED SCOPE: {', '.join(self.campaign.targets)}
All commands and navigation are restricted to these targets. Out-of-scope requests will be blocked.
..."""
```

### 5d. End the Prompt with a Situational Summary

Instead of a generic "Begin.", the last line should be contextual:

```python
if not self._turn_action_counts:
    closing = f"This is your first turn. Start with reconnaissance of {target}."
elif self._actions_executed_this_turn == 0:
    closing = "Your last response had no actions. Execute commands to make progress."
else:
    closing = "Continue."
```

---

## Phase 6: Visible Reasoning — Show the AI's Thinking

**Priority: MEDIUM**
**Files: `utils/display.py` or `ui/app.py`, `cyrax.py`**

The user wants to see the AI thinking, not just see action blocks execute. Currently the `StreamBuffer` hides everything between `[EXECUTE]` and `[/EXECUTE]`, showing only the conversational text. The model's reasoning IS the conversational text — but the current system prompt discourages it ("don't plan, just execute").

### 6a. Encourage Brief Reasoning Before Actions

Update the system prompt to request reasoning:

```
Every response should follow this pattern:
1. One sentence explaining what you're doing and why
2. The action block(s) to do it

Example:
  The login form uses POST to /login.php. Let me test for SQL injection in the username field.
  [EXECUTE] browser.fill("input[name='username']", "admin' OR '1'='1' --") [/EXECUTE]
  [EXECUTE] browser.click("input[type='submit']") [/EXECUTE]
```

This gives the user visibility without encouraging the model to write long plans.

### 6b. Stream Reasoning Text in Real-Time

The `StreamBuffer` already passes through non-block text. In the TUI, this text streams live into the output pane. The user sees:

```
CYRAX: The login form uses POST to /login.php. Let me test for SQL injection...
┌─ Executing ──────────────────────────────────────────┐
│ browser.fill("input[name='username']", "admin' OR...  │
└──────────────────────────────────────────────────────┘
```

### 6c. Action Block Display During Streaming

Currently `_render_block()` is a no-op — action blocks are silently consumed during streaming and only rendered after the full response. Change this:

```python
# In StreamBuffer._render_block():
def _render_block(self):
    """Render the action block in real-time during streaming."""
    if self._open_tag.startswith("[EXECUTE]"):
        command = self._block_buffer.rstrip("[/EXECUTE]")
        # Emit a panel to the TUI immediately
        self._display.show_execution("CYRAX", command.strip())
    elif self._open_tag.startswith("[FINDING"):
        # Emit finding panel immediately
        ...
```

This means the user sees actions rendered as panels the moment they're fully streamed, not after the entire response completes.

---

## Phase 7: Slash Commands During Execution

**Priority: MEDIUM**
**Files: `ui/app.py`, `cyrax.py`**

With the Textual TUI (Phase 2), the input bar is always active. Slash commands work at any time because the event loop processes input events concurrently with output streaming.

### 7a. Slash Command Registry

```python
SLASH_COMMANDS = {
    "/help":     "Show available commands",
    "/status":   "Show campaign status, agent pool, findings count",
    "/findings": "List all findings with severity",
    "/agents":   "Show running/completed sub-agents",
    "/pause":    "Pause the current AI operation",
    "/resume":   "Resume a paused operation",
    "/target":   "Show/change the target scope",
    "/approve":  "Pre-approve a category of actions (e.g., /approve sqli)",
    "/export":   "Export findings to a report file",
    "/exit":     "Save state and exit",
    "/clear":    "Clear the output pane",
    "/history":  "Show conversation history",
    "/model":    "Show/switch model info",
}
```

### 7b. Interrupt Handling

When the user types while the AI is streaming:

1. The text goes into the input bar (Textual handles this natively)
2. On Enter:
   - If it's a slash command: execute immediately (doesn't interrupt the AI)
   - If it's free text: **queue it** as the next user message. When the current AI turn completes, this message is sent instead of auto-continue.
   - If it's Ctrl+C: request a pause on the orchestrator

```python
# In CyraxApp
async def on_input_submitted(self, event):
    text = event.value.strip()
    event.input.value = ""

    if text.startswith("/"):
        await self.handle_slash_command(text)
    elif self.orchestrator.is_running:
        # Queue this as the next user message (replaces auto-continue)
        self.orchestrator.queue_user_message(text)
        self.write_output(f"[dim]Queued: {text} (will send after current turn)[/dim]")
    else:
        await self.send_to_orchestrator(text)
```

---

## Phase 8: Model-Level Anti-Hallucination

**Priority: MEDIUM**
**Files: `tools/browser.py`, `cyrax.py`, `agents/base_agent.py`**

The model invents browser methods that don't exist (`browser.test_sql_injection()`, `browser.uploadFile()`). The current fix (returning an error after parsing fails) works but is reactive.

### 8a. Strict Method Validation in `parse_browser_command()`

Already partially implemented via `is_browser_command()`. Strengthen:

```python
def parse_browser_command(command_str):
    match = re.match(r"browser\.(\w+)\((.*)\)$", command_str.strip(), re.DOTALL)
    if not match:
        return None
    method_name = match.group(1)
    if method_name not in BROWSER_COMMANDS:
        # Return a special sentinel instead of None
        return ("__invalid__", method_name, [], {})
    ...
```

The orchestrator handles `__invalid__` by returning a clear error with the full list of valid methods.

### 8b. Fix the `browser.type` Dispatcher Bug

In `BROWSER_COMMANDS` (browser.py line 839), the key `"type"` maps to `type_text()` but `getattr(browser_instance, "type")` returns Python's built-in `type`. Fix:

```python
# In BROWSER_COMMANDS dict, change:
"type": {"method": "type_text", "args": ["selector", "text"], ...}

# In the dispatcher (cyrax.py and base_agent.py):
method_map = BROWSER_COMMANDS.get(method_name)
actual_method_name = method_map.get("method", method_name) if isinstance(method_map, dict) else method_name
method = getattr(self.browser, actual_method_name, None)
```

This requires changing `BROWSER_COMMANDS` from a simple string-keyed dict to include a `method` field for aliased commands.

### 8c. Tool Result Feedback Quality

When a browser command succeeds, return rich context so the model doesn't have to guess:

```python
# After browser.goto():
result = f"Navigated to {url}\nHTTP {status}\nTitle: {title}\n\nPage content (first 3000 chars):\n{snapshot}"

# After browser.forms():
result = f"Found {n} forms:\n{forms_json}\n\nThis page has {n_inputs} input fields. Use browser.fill() to populate them."

# After browser.click():
result = f"Clicked {selector}. Page navigated to {new_url}.\nNew page title: {title}\n\nPage content:\n{snapshot}"
```

The richer the feedback, the less the model needs to hallucinate about what happened.

---

## Phase 9: Smarter Conversation Memory

**Priority: MEDIUM**
**Files: `memory/conversation.py`, `cyrax.py`**

Currently `ConversationMemory` is a fixed-size sliding window (50 messages for orchestrator, 30 for sub-agents). This means:
- Early reconnaissance findings get evicted as the conversation grows
- Tool output from 10 turns ago is gone
- The model "forgets" what it already tried

### 9a. Summarization-Based Memory

Instead of dropping old messages, summarize them:

```python
class ConversationMemory:
    def __init__(self, max_messages=50, summary_threshold=40):
        self.messages = []
        self.summary = ""  # Running summary of evicted messages
        self.summary_threshold = summary_threshold

    def add_message(self, role, content):
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > self.summary_threshold:
            self._summarize_oldest()

    def _summarize_oldest(self):
        """Summarize the oldest 10 messages and prepend to summary."""
        oldest = self.messages[:10]
        # Extract key facts: commands run, results, findings, errors
        facts = self._extract_facts(oldest)
        self.summary += f"\n{facts}"
        self.messages = self.messages[10:]

    def get_messages(self):
        """Return messages with summary prepended as a system-context message."""
        if self.summary:
            context_msg = {"role": "user", "content": f"[Previous session context]\n{self.summary}"}
            return [context_msg] + self.messages
        return self.messages
```

### 9b. Fact Extraction (Lightweight)

The `_extract_facts()` method doesn't need another LLM call. It extracts structured data:

```python
def _extract_facts(self, messages):
    facts = []
    for msg in messages:
        content = msg["content"]
        # Extract commands that were run
        for match in re.finditer(r'\[EXECUTE\](.*?)\[/EXECUTE\]', content, re.DOTALL):
            facts.append(f"- Ran: {match.group(1).strip()[:100]}")
        # Extract findings
        for match in re.finditer(r'\[FINDING.*?title="(.*?)"', content):
            facts.append(f"- Found: {match.group(1)}")
        # Extract errors
        if "failed" in content.lower() or "error" in content.lower():
            facts.append(f"- Error encountered in command")
    return "\n".join(facts) if facts else "No notable events."
```

---

## Phase 10: Error Recovery — Graceful, Not Forceful

**Priority: MEDIUM**
**Files: `cyrax.py`, `agents/base_agent.py`**

### 10a. Replace Circuit Breaker with Contextual Error Guidance

The current circuit breaker injects a generic "5 commands have failed" warning. Replace with specific guidance based on the type of failure:

```python
def _get_failure_guidance(self, command: str, error: str) -> str:
    """Return specific guidance based on what went wrong."""
    if "timeout" in error.lower():
        return "The element may not be visible. Try browser.html() to see the actual page structure, or use browser.wait() before interacting."
    if "not found" in error.lower() or "no such file" in error.lower():
        return "The file or command doesn't exist. Use [WRITE_FILE] to create files you need, or check available tools with which/where."
    if "permission denied" in error.lower():
        return "Permission denied. Try a different approach or check if you need elevated privileges."
    if "connection refused" in error.lower():
        return "The service may not be running on this port. Try scanning for open ports first."
    # Generic
    return "Analyze the error and try a different approach."
```

This is appended to the tool result, not injected as a fake user message.

### 10b. Progressive Backoff

Instead of blocking after 2 failures of the same pattern:

- **1st failure:** Return error + specific guidance
- **2nd failure of same pattern:** Return error + "This approach has failed before. Previous error: {previous_error}. Try something different."
- **3rd failure of same pattern:** Return error + "This approach has failed 3 times. It will not work. The system will skip further attempts with this pattern."
- **4th+ failure:** Skip execution entirely, return the block message

This gives the model progressively stronger signals without injecting fake conversation messages.

---

## Phase 11: Sub-Agent Quality — Streaming & Isolation

**Priority: LOW**
**Files: `agents/base_agent.py`, `models/model_manager.py`**

### 11a. Streaming for Sub-Agents

Sub-agents currently use `model.generate()` (blocking, no streaming). Switch to `model.generate_stream()` so the TUI can show sub-agent thinking in real-time:

```python
# In base_agent.py execute() loop:
async def _generate_response(self, system, messages):
    """Stream response and display reasoning live."""
    full_content = []
    for chunk in self.model.generate_stream(system=system, messages=messages):
        if chunk.get("done"):
            break
        delta = chunk.get("delta", "")
        full_content.append(delta)
        # Stream reasoning text to the TUI (only non-block text)
        self._stream_to_parent(delta)
    return "".join(full_content)
```

### 11b. Sub-Agent Conversation Isolation

Each sub-agent already has its own `ConversationMemory`. But they share the `ModelManager` and accumulate tokens globally. Add per-agent tracking:

```python
# In base_agent.py
def execute(self):
    tokens_start = self.model.get_usage()["total_tokens"]
    ... # run loop
    tokens_end = self.model.get_usage()["total_tokens"]
    self.tokens_used = tokens_end - tokens_start
```

This feeds into the agent status display and helps the user understand resource usage.

---

## Implementation Order

This is the order that maximizes safety and user value at each step:

| Order | Phase | Priority | Risk | Effort | Depends On |
|-------|-------|----------|------|--------|-----------|
| 1 | Phase 1a-1d: Safety/Scope | CRITICAL | Low | Medium | Nothing |
| 2 | Phase 5a-5d: System Prompt | HIGH | Medium | Low | Phase 1 (target substitution) |
| 3 | Phase 4a-4e: Remove Hardcoded | HIGH | Low | Low | Phase 5 (prompt must be good first) |
| 4 | Phase 8a-8c: Anti-Hallucination | MEDIUM | Low | Low | Nothing |
| 5 | Phase 6a-6c: Visible Reasoning | MEDIUM | Low | Low | Nothing |
| 6 | Phase 10a-10b: Error Recovery | MEDIUM | Low | Low | Nothing |
| 7 | Phase 2a-2e: TUI (Textual) | HIGH | Medium | High | Nothing (but benefits from all above) |
| 8 | Phase 7a-7b: Slash Commands | MEDIUM | Low | Low | Phase 2 (needs TUI) |
| 9 | Phase 3a-3d: Async Sub-Agents | HIGH | Medium | High | Phase 2 (needs async loop) |
| 10 | Phase 9a-9b: Smart Memory | MEDIUM | Low | Medium | Nothing |
| 11 | Phase 11a-11b: Sub-Agent Streaming | LOW | Low | Medium | Phase 2 + 3 |

**Phases 1-6 can be implemented WITHOUT the TUI migration.** They work with the current Rich-based display. The TUI (Phase 2) is a larger refactor that unlocks Phases 7-11.

**Recommended: Implement Phases 1-6 first, validate, then tackle the TUI migration.**

---

## What This Does NOT Change

- **Model providers** — All providers (Anthropic, OpenAI, Ollama, etc.) continue to work as-is
- **Campaign persistence** — SQLite knowledge base, campaign state, conversation JSON all unchanged
- **Tool registry** — No changes to how tools are registered or discovered
- **Browser automation** — Playwright core is untouched; only adding validation and context
- **CLI interface** — All flags continue to work; Textual TUI is additive, not replacing
- **Agent types** — All 7 agent types (recon, web, exploit, post, AD, cloud, OSINT) unchanged

---

## Verification Checklist

After each phase, verify:

### Phase 1 (Safety)
- [ ] `browser.goto("https://target.com")` is blocked when target.com is not in scope
- [ ] `curl https://evil.com` is blocked when evil.com is not in scope
- [ ] Sub-agents inherit scope enforcement from parent
- [ ] No `target.com` literals remain in the system prompt
- [ ] Permission prompt appears before attack payloads (when not in --auto mode)

### Phase 2 (TUI)
- [ ] User can type while AI is streaming output
- [ ] Slash commands work during AI execution
- [ ] Ctrl+C pauses the operation, doesn't crash
- [ ] Output pane is scrollable
- [ ] `--simple` flag falls back to current Rich-based display

### Phase 3 (Async Agents)
- [ ] Two agents can run concurrently
- [ ] Agent status updates appear in the TUI in real-time
- [ ] Agent completion results are injected into next turn's context
- [ ] Browser contexts are isolated between agents

### Phase 4 (Remove Hardcoded)
- [ ] No "[SYSTEM]" prefixed messages in conversation history
- [ ] Auto-continue sends only "Continue."
- [ ] No sub-agent forcing messages
- [ ] Model produces its own reasoning without being prodded

### Phase 5 (System Prompt)
- [ ] Prompt is under 200 lines
- [ ] Target-specific examples use actual target, not `target.com`
- [ ] Action format is in the first 20 lines
- [ ] No redundant "don't plan" warnings

### Phase 6 (Visible Reasoning)
- [ ] Model outputs one sentence of reasoning before each action
- [ ] Reasoning streams to the TUI in real-time
- [ ] Action blocks render as panels during streaming (not after)

### Phase 8 (Anti-Hallucination)
- [ ] `browser.test_sql_injection()` returns "invalid method" error with valid method list
- [ ] `browser.type(selector, text)` correctly routes to `type_text()`
- [ ] Tool results include page content after navigation/interaction

### Phase 10 (Error Recovery)
- [ ] Timeout errors include "check page structure" guidance
- [ ] 3rd failure of same pattern includes block message
- [ ] No fake user messages for error recovery

---

## Risk Assessment

| Phase | Risk | Mitigation |
|-------|------|-----------|
| Phase 1 (Safety) | Low — additive, doesn't break existing flows | Scope enforcement can be disabled with `--no-scope` flag |
| Phase 2 (TUI) | Medium — largest refactor, changes the main loop | Fallback to Rich-only mode; incremental migration |
| Phase 3 (Async) | Medium — concurrency is hard | Thread pool with `run_in_executor`, not raw threading; max 3 concurrent |
| Phase 4 (Hardcoded removal) | Low — removing code is safer than adding | The model may need a better prompt (Phase 5) to compensate |
| Phase 5 (Prompt) | Medium — prompt changes affect all model behavior | Test with multiple models (Qwen, Mistral, etc.) |
| Phase 6-11 | Low — additive improvements | Each phase is independent and can be reverted |

---

## The End State

After this overhaul, a CYRAX session looks like this:

```
┌─ CYRAX v2 ──────────────────────────────────────── 14:32:01 ┐
│                                                              │
│ CYRAX: Starting reconnaissance of 192.168.1.100.            │
│ Let me check what services are running.                      │
│                                                              │
│ ┌─ Executing ─────────────────────────────────────────────┐  │
│ │ nmap -sV -sC 192.168.1.100 -p-                         │  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
│ ┌─ Output ────────────────────────────────────────────────┐  │
│ │ PORT     STATE SERVICE  VERSION                         │  │
│ │ 22/tcp   open  ssh      OpenSSH 7.6p1                  │  │
│ │ 80/tcp   open  http     Apache httpd 2.4.29            │  │
│ │ 3306/tcp open  mysql    MySQL 5.7.33                   │  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
│ CYRAX: Three services found. The web server is the most      │
│ promising attack surface. Let me check the web application   │
│ while a recon agent enumerates further.                      │
│                                                              │
│ ┌─ Spawning Agent ────────────────────────────────────────┐  │
│ │ RECON-00: Deep enumeration of 192.168.1.100             │  │
│ │ (robots.txt, directories, tech stack, JS analysis)      │  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
│ ┌─ Executing ─────────────────────────────────────────────┐  │
│ │ browser.goto("http://192.168.1.100")                    │  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
│ CYRAX: This is a DVWA instance. I need to log in first.     │
│                                                              │
│ ┌─ Permission Required ──────────────────────────────────┐   │
│ │ CYRAX wants to: Use default credentials (admin/password)│   │
│ │ [Y] Allow  [N] Deny  [A] Allow all credential tests    │   │
│ └─────────────────────────────────────────────────────────┘   │
│                                                              │
│ ┌─ RECON-00 (running) ────────────────────────────────────┐  │
│ │ Iter 3/20 | Found: robots.txt (3 entries), /config/     │  │
│ └─────────────────────────────────────────────────────────┘  │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ > /findings                                                  │
└──────────────────────────────────────────────────────────────┘
```

The user typed `/findings` while the AI was working. The AI continued. The user can type more. The AI asked permission before using credentials. Sub-agents run in the background. Everything is visible. Nothing is hardcoded.

**This is what an intelligent, agentic penetration testing system looks like.**
