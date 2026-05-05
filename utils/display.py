"""
CYRAX Display Module
Rich terminal output for the CYRAX red team operator.
"""

import re
from rich import box
from rich.markup import escape as rich_escape
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner


console = Console()


BANNER = r"""
  ██████╗██╗   ██╗██████╗  █████╗ ██╗  ██╗
 ██╔════╝╚██╗ ██╔╝██╔══██╗██╔══██╗╚██╗██╔╝
██║      ╚████╔╝ ██████╔╝███████║ ╚███╔╝
██║       ╚██╔╝  ██╔══██╗██╔══██║ ██╔██╗
 ╚██████╗   ██║   ██║  ██║██║  ██║██╔╝ ██╗
  ╚═════╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝
"""


def show_banner():
    """Display the CYRAX startup banner."""
    console.print(
        Panel(
            Text(BANNER, style="bold red", justify="center"),
            title="[bold white]CYRAX[/bold white] [dim]autonomous red-team operator[/dim]",
            subtitle="[dim]natural language in, scoped operations out[/dim]",
            border_style="red",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )
    console.print()


def show_session_intro(
    provider: str,
    model: str,
    scope: str,
    permission_mode: str,
    streaming: bool = True,
):
    """Display a compact Claude-style session orientation panel."""
    status = Table.grid(padding=(0, 2))
    status.add_column(style="bold")
    status.add_column()
    status.add_row("model", f"{provider or 'unconfigured'}/{model or 'unconfigured'}")
    status.add_row("scope", scope or "not set")
    status.add_row("permissions", permission_mode)
    status.add_row("streaming", "on" if streaming else "off")

    tips = Text()
    tips.append("Type a target or task. ", style="white")
    tips.append("/help", style="bold cyan")
    tips.append(" commands · ")
    tips.append("/status", style="bold cyan")
    tips.append(" state · ")
    tips.append("/auto", style="bold cyan")
    tips.append(" autonomous · ")
    tips.append("/pause", style="bold cyan")
    tips.append(" save/stop · ")
    tips.append("Ctrl+C", style="bold cyan")
    tips.append(" interrupt")

    console.print(
        Panel(
            status,
            title="[bold red]session[/bold red]",
            border_style="red",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )
    console.print(tips)
    console.print()


def show_reasoning(agent_id: str, text: str):
    """Display an agent's reasoning block."""
    console.print(
        Panel(
            Markdown(text),
            title=f"[bold yellow]{agent_id} Reasoning[/bold yellow]",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def show_execution(agent_id: str, command: str):
    """Display a command being executed."""
    console.print(
        Panel(
            Syntax(command, "bash", theme="monokai", line_numbers=False),
            title=f"[bold cyan]{agent_id} Executing[/bold cyan]",
            border_style="cyan",
            box=box.SIMPLE,
            padding=(0, 1),
        )
    )


def show_tool_output(agent_id: str, output: str, truncate: int = 2000):
    """Display tool execution output."""
    if len(output) > truncate:
        output = output[:truncate] + f"\n... [truncated, {len(output)} total chars]"
    console.print(
        Panel(
            Text(output, style="dim"),
            title=f"[bold green]{agent_id} Output[/bold green]",
            border_style="green",
            box=box.SIMPLE,
            padding=(0, 1),
        )
    )


def show_tool_event(kind: str, title: str, detail: str = "", style: str = "cyan"):
    """Display a compact one-line tool/status event."""
    label = kind.upper()
    safe_title = rich_escape(title)
    if detail:
        console.print(f"[bold {style}]● {label}[/bold {style}] {safe_title} [dim]{rich_escape(detail)}[/dim]")
    else:
        console.print(f"[bold {style}]● {label}[/bold {style}] {safe_title}")


def show_agent_message(agent_id: str, message: str):
    """Display a message from an agent."""
    console.print(f"[bold magenta]{agent_id}[/bold magenta]: {rich_escape(message)}")


def show_cyrax_message(message: str):
    """Display a message from the main CYRAX orchestrator."""
    console.print(f"[bold red]CYRAX[/bold red]: {rich_escape(message)}")


def show_spawning_agent(agent_id: str, agent_type: str, task: str):
    """Display agent spawning notification."""
    console.print(
        Panel(
            f"[bold]Type:[/bold] {rich_escape(agent_type)}\n[bold]Task:[/bold] {rich_escape(task)}",
            title=f"[bold magenta]Spawning Agent: {rich_escape(agent_id)}[/bold magenta]",
            border_style="magenta",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def show_finding(severity: str, title: str, details: str):
    """Display a security finding."""
    severity_colors = {
        "critical": "bold red",
        "high": "red",
        "medium": "yellow",
        "low": "blue",
        "info": "cyan",
    }
    color = severity_colors.get(severity.lower(), "white")
    base_color = color.replace("bold ", "")
    console.print(
        Panel(
            Text(details),
            title=f"[{color}][{rich_escape(severity.upper())}] {rich_escape(title)}[/{color}]",
            border_style=base_color,
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def show_attack_path(steps: list[dict]):
    """Display an attack path summary."""
    table = Table(
        title="Attack Path",
        box=box.ROUNDED,
        border_style="red",
        show_header=True,
        header_style="bold red",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Stage", style="cyan")
    table.add_column("Target", style="yellow")
    table.add_column("Technique", style="green")
    table.add_column("Result", style="bold white")

    for i, step in enumerate(steps, 1):
        table.add_row(
            str(i),
            step.get("stage", ""),
            step.get("target", ""),
            step.get("technique", ""),
            step.get("result", ""),
        )

    console.print(table)


def show_campaign_status(state: dict):
    """Display current campaign status."""
    table = Table(
        title="Campaign Status",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Property", style="bold")
    table.add_column("Value")

    for key, value in state.items():
        table.add_row(str(key), str(value))

    console.print(table)


def show_error(message: str):
    """Display an error message."""
    console.print(f"[bold red]ERROR:[/bold red] {rich_escape(message)}")


def show_warning(message: str):
    """Display a warning message."""
    console.print(f"[bold yellow]WARNING:[/bold yellow] {rich_escape(message)}")


def show_info(message: str):
    """Display an info message."""
    console.print(f"[bold blue]INFO:[/bold blue] {rich_escape(message)}")


def show_success(message: str):
    """Display a success message."""
    console.print(f"[bold green]SUCCESS:[/bold green] {rich_escape(message)}")


def show_help(commands: list[tuple[str, str, str]]):
    """Display slash commands in a compact searchable table."""
    table = Table(
        title="CYRAX Commands",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold red",
    )
    table.add_column("Command", style="bold cyan", no_wrap=True)
    table.add_column("Args", style="yellow")
    table.add_column("Description")
    for command, args, description in commands:
        table.add_row(command, args, description)
    console.print(table)


def show_turn_summary(actions: int, succeeded: int, tokens: dict, agents: int = 0):
    """Show a compact end-of-turn activity summary."""
    token_total = tokens.get("total_tokens", 0)
    detail = f"{actions} action(s), {succeeded} succeeded, {agents} agent(s), {token_total} tokens"
    show_tool_event("turn", "complete", detail, style="green")


def show_compact_summary(removed: int, remaining: int):
    """Show context compaction result."""
    show_tool_event(
        "compact",
        f"summarized {removed} old message(s)",
        f"{remaining} recent message(s) kept",
        style="magenta",
    )


def get_spinner(text: str = "Working...") -> Live:
    """Get a live spinner context manager."""
    return Live(
        Spinner("dots", text=text, style="cyan"),
        console=console,
        transient=True,
    )


_streaming_buffer = []
_streaming_active = False
_streaming_enabled = True
_streaming_show_cursor = True
_streaming_cursor_visible = False
_streaming_terminal_cursor_hidden = False


def configure_streaming(
    enabled: bool = True,
    delay: float = 0.0,
    chunk_size: int = 0,
    show_cursor: bool = True,
):
    """Configure assistant response streaming behavior."""
    global _streaming_enabled
    global _streaming_show_cursor
    _streaming_enabled = bool(enabled)
    _streaming_show_cursor = bool(show_cursor)


def start_streaming(agent_id: str):
    """Begin streaming output and prepare for a smooth assistant response."""
    global _streaming_buffer, _streaming_active, _streaming_cursor_visible
    _streaming_buffer = []
    _streaming_active = True
    _streaming_cursor_visible = False
    console.print()
    console.print(f"[bold red]{agent_id}[/bold red] [dim]responding[/dim]")
    console.print("[bold red]│[/bold red] ", end="")
    _hide_terminal_cursor()


def stream_token(token: str):
    """Display streamed text as model chunks arrive."""
    if not _streaming_active:
        return
    _streaming_buffer.append(token)
    _erase_stream_cursor()
    console.file.write(token)
    _draw_stream_cursor()
    console.file.flush()


def end_streaming():
    """End the streaming display."""
    global _streaming_buffer, _streaming_active
    _erase_stream_cursor()
    _show_terminal_cursor()
    _streaming_active = False
    console.print()
    _streaming_buffer = []


def _draw_stream_cursor():
    if not (_streaming_enabled and _streaming_show_cursor and console.is_terminal):
        return
    console.file.write("\x1b[90m▌\x1b[0m")
    _set_cursor_visible(True)


def _set_cursor_visible(visible: bool):
    global _streaming_cursor_visible
    _streaming_cursor_visible = visible


def _erase_stream_cursor():
    if not _streaming_cursor_visible:
        return
    console.file.write("\b \b")
    _set_cursor_visible(False)


def _hide_terminal_cursor():
    global _streaming_terminal_cursor_hidden
    if not (_streaming_enabled and _streaming_show_cursor and console.is_terminal):
        return
    console.file.write("\x1b[?25l")
    _streaming_terminal_cursor_hidden = True


def _show_terminal_cursor():
    global _streaming_terminal_cursor_hidden
    if not _streaming_terminal_cursor_hidden:
        return
    console.file.write("\x1b[?25h")
    _streaming_terminal_cursor_hidden = False


class StreamBuffer:
    """
    Buffer that separates conversational text from structured action blocks
    during streaming. Conversational text passes through immediately (visible
    to the user as tokens arrive). Structured blocks like [EXECUTE],
    [WRITE_FILE], [FINDING] are buffered and rendered as Rich panels
    when complete.
    """

    # Known block-opening tag prefixes and their closing tags
    _BLOCK_TAGS = {
        "[EXECUTE]": "[/EXECUTE]",
        "[WRITE_FILE ": "[/WRITE_FILE]",
        "[FINDING ": "[/FINDING]",
        "[SPAWN ": "[/SPAWN]",
        "[STORE ": "[/STORE]",
    }

    _MAX_TAG_LEN = 150  # Max chars to buffer when detecting a tag

    def __init__(self):
        self._mode = "NORMAL"       # NORMAL | TAG_DETECT | INSIDE_BLOCK
        self._pending = ""          # Chars buffered during TAG_DETECT
        self._block_buffer = ""     # Content inside a block
        self._close_tag = ""        # Closing tag we're looking for
        self._open_tag = ""         # Opening tag that started this block

    def feed(self, token: str) -> str:
        """Feed a token from the stream. Returns text to display immediately."""
        output = []
        for char in token:
            result = self._process_char(char)
            if result:
                output.append(result)
        return "".join(output)

    def _process_char(self, char: str) -> str:
        if self._mode == "NORMAL":
            if char == "[":
                self._mode = "TAG_DETECT"
                self._pending = "["
                return ""
            return char

        elif self._mode == "TAG_DETECT":
            self._pending += char

            # Check if pending matches a complete opening tag
            if char == "]":
                for open_prefix, close_tag in self._BLOCK_TAGS.items():
                    if self._pending == open_prefix or (
                        self._pending.startswith(open_prefix) and self._pending.endswith("]")
                    ):
                        # Complete opening tag found
                        self._mode = "INSIDE_BLOCK"
                        self._open_tag = self._pending
                        self._close_tag = close_tag
                        self._block_buffer = ""
                        self._pending = ""
                        return ""

                # Closing ] but didn't match any known tag — flush as text
                flushed = self._pending
                self._pending = ""
                self._mode = "NORMAL"
                return flushed

            # Still accumulating — check if any tag could still match
            could_match = False
            for open_prefix in self._BLOCK_TAGS:
                if open_prefix.startswith(self._pending) or self._pending.startswith(open_prefix):
                    could_match = True
                    break

            if not could_match or len(self._pending) > self._MAX_TAG_LEN:
                # No possible match or too long — flush as normal text
                flushed = self._pending
                self._pending = ""
                self._mode = "NORMAL"
                return flushed

            return ""  # Keep accumulating

        elif self._mode == "INSIDE_BLOCK":
            self._block_buffer += char
            if self._block_buffer.endswith(self._close_tag):
                # Block complete — render as a Rich panel
                content = self._block_buffer[: -len(self._close_tag)]
                self._render_block(self._open_tag, content)
                self._mode = "NORMAL"
                self._block_buffer = ""
                self._open_tag = ""
                self._close_tag = ""
            return ""

        return char

    def _render_block(self, open_tag: str, content: str):
        """
        Silently consume completed action blocks during streaming.
        Actions are displayed as Rich panels when they actually execute
        (via show_execution and show_tool_output), providing a cleaner
        UX where reasoning text streams first, then actions appear
        in their panel format during execution.
        """
        pass

    def flush(self) -> str:
        """Flush any remaining buffered text (called at end of stream)."""
        result = self._pending + self._block_buffer
        self._pending = ""
        self._block_buffer = ""
        self._mode = "NORMAL"
        return result


def format_response(text: str) -> str:
    """
    Parse CYRAX response text and render special blocks.
    Returns the cleaned text after rendering blocks.
    """
    # Extract and display reasoning blocks
    reasoning_pattern = r"\[Reasoning\]\s*\n(.*?)(?=\n\[|\Z)"
    for match in re.finditer(reasoning_pattern, text, re.DOTALL):
        show_reasoning("CYRAX", match.group(1).strip())

    # Extract and display execution blocks
    exec_pattern = r"\[Executing\]\s*\n(.*?)(?=\n\[|\Z)"
    for match in re.finditer(exec_pattern, text, re.DOTALL):
        show_execution("CYRAX", match.group(1).strip())

    # Extract and display spawning blocks
    spawn_pattern = r"\[Spawning Agent: ([\w-]+)\]\s*\n?(.*?)(?=\n\[|\Z)"
    for match in re.finditer(spawn_pattern, text, re.DOTALL):
        agent_id = match.group(1)
        details = match.group(2).strip()
        show_spawning_agent(agent_id, agent_id.split("-")[0], details)

    # Clean the text of special blocks for plain display
    cleaned = re.sub(r"\[Reasoning\].*?(?=\n\[|\Z)", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"\[Executing\].*?(?=\n\[|\Z)", "", text, flags=re.DOTALL)
    cleaned = re.sub(
        r"\[Spawning Agent: [\w-]+\].*?(?=\n\[|\Z)", "", cleaned, flags=re.DOTALL
    )
    cleaned = cleaned.strip()

    return cleaned


def show_blocked(action_type: str, description: str):
    """Display a blocked action notification."""
    console.print(
        Panel(
            f"[bold]Type:[/bold] {rich_escape(action_type)}\n"
            f"[bold]Action:[/bold] {rich_escape(description)}",
            title="[bold red]Blocked by Policy[/bold red]",
            border_style="red",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def show_scope_violation(target: str, scope: str):
    """Display a scope violation."""
    console.print(
        Panel(
            f"[bold]Target:[/bold] {rich_escape(target)}\n"
            f"[bold]Authorized scope:[/bold] {rich_escape(scope)}",
            title="[bold red]Scope Violation[/bold red]",
            border_style="red",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def prompt_user() -> str:
    """Get input from the user with the CYRAX prompt."""
    try:
        return console.input("\n[bold red]cyrax[/bold red] [dim]›[/dim] ")
    except (EOFError, KeyboardInterrupt):
        return "exit"
