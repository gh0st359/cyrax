"""
CYRAX Textual TUI Application
Provides a full interactive terminal with always-available input,
concurrent AI output streaming, and slash commands during execution.
"""

import asyncio
from typing import Optional, TYPE_CHECKING

try:
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, RichLog, Input, Static
    from textual.containers import Vertical
    from textual.binding import Binding
    from textual import work
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.markdown import Markdown
    from rich import box
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False

if TYPE_CHECKING:
    pass


def rich_escape(text: str) -> str:
    """Escape Rich markup characters."""
    return text.replace("[", "\\[").replace("]", "\\]")


if HAS_TEXTUAL:

    class CyraxApp(App):
        """
        CYRAX Interactive TUI.

        Features:
        - Always-available input bar at the bottom
        - Scrollable output pane for AI reasoning, commands, and results
        - Slash commands work during AI execution
        - Ctrl+C pauses the current operation
        - Real-time streaming of AI output
        """

        CSS = """
        #output {
            height: 1fr;
            overflow-y: scroll;
            border: solid green;
            padding: 0 1;
        }
        #status-bar {
            height: 1;
            background: $panel;
            color: $text-muted;
            padding: 0 1;
        }
        #input-bar {
            dock: bottom;
            height: 3;
        }
        """

        BINDINGS = [
            Binding("ctrl+c", "pause", "Pause AI", show=True),
            Binding("ctrl+d", "quit", "Exit", show=True),
            Binding("escape", "focus_input", "Focus Input", show=False),
        ]

        TITLE = "CYRAX"
        SUB_TITLE = "Autonomous AI Red Team Operator"

        def __init__(self, orchestrator, **kwargs):
            super().__init__(**kwargs)
            self.orchestrator = orchestrator
            self._ai_running = False
            self._turn_count = 0

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield RichLog(id="output", highlight=True, markup=True, wrap=True)
            yield Static("Ready", id="status-bar")
            yield Input(
                id="input-bar",
                placeholder="Type a command or message... (/help for commands)",
            )
            yield Footer()

        def on_mount(self) -> None:
            """Called when the app is mounted."""
            output = self.query_one("#output", RichLog)
            # Show banner
            output.write(
                Panel(
                    "[bold red]CYRAX[/bold red] - Autonomous AI Red Team Operator\n"
                    "Type a target to begin. Use /help for available commands.",
                    border_style="red",
                    box=box.DOUBLE,
                )
            )

            # Show scope info if configured
            if self.orchestrator.scope.enabled:
                output.write(
                    f"[green]Scope:[/green] {self.orchestrator.scope.get_scope_description()}"
                )

            # Show campaign info if active
            if self.orchestrator._campaign_mode:
                output.write(
                    f"[yellow]Campaign:[/yellow] {self.orchestrator._campaign_name} - "
                    f"{self.orchestrator.campaign.objective or 'No objective set'}"
                )

            # Focus input
            self.query_one("#input-bar", Input).focus()

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            """Handle user input from the input bar."""
            text = event.value.strip()
            event.input.value = ""

            if not text:
                return

            output = self.query_one("#output", RichLog)

            # Show user input
            output.write(f"\n[bold white]> {rich_escape(text)}[/bold white]")

            if text.startswith("/"):
                await self._handle_slash_command(text)
            elif self._ai_running:
                # Queue message for next turn
                self.orchestrator.queue_user_message(text)
                output.write(
                    f"[dim]Queued for next turn: {rich_escape(text)}[/dim]"
                )
            else:
                # Send to AI
                self._run_ai_turn(text)

        async def _handle_slash_command(self, command: str) -> None:
            """Handle slash commands (work during AI execution)."""
            output = self.query_one("#output", RichLog)
            result = self.orchestrator.handle_command(command)

            if result == "EXIT":
                if self.orchestrator._campaign_mode:
                    self.orchestrator._save_campaign_state()
                    output.write("[yellow]Campaign state saved. Exiting...[/yellow]")
                self.exit()
            elif result is None:
                output.write(f"[red]Unknown command: {rich_escape(command)}[/red]")
                output.write("[dim]Use /help for available commands[/dim]")

        @work(thread=True)
        def _run_ai_turn(self, user_input: str) -> None:
            """Run an AI turn in a background thread."""
            self._run_ai_turn_loop(user_input)

        def _run_ai_turn_loop(self, user_input: str) -> None:
            """Run one or more AI turns iteratively for campaign auto-continue."""
            output = self.query_one("#output", RichLog)
            status = self.query_one("#status-bar", Static)
            pending_input = user_input

            while pending_input is not None:
                current_input = pending_input
                pending_input = None
                self._ai_running = True
                self.call_from_thread(status.update, "AI thinking...")

                try:
                    self.orchestrator.chat(current_input)
                    self._turn_count += 1

                    # Update tracking
                    self.orchestrator._turn_action_counts.append(
                        self.orchestrator._actions_executed_this_turn
                    )
                    if self.orchestrator._actions_executed_this_turn == 0:
                        self.orchestrator._consecutive_empty_turns += 1
                    else:
                        self.orchestrator._consecutive_empty_turns = 0

                    if self.orchestrator._campaign_mode:
                        self.orchestrator._save_campaign_state()

                    # Auto-continue in campaign mode
                    if (
                        self.orchestrator._campaign_mode
                        and self.orchestrator.campaign.status == "active"
                        and self.orchestrator._consecutive_empty_turns < 3
                    ):
                        # Queued user input should override automatic continuation.
                        if self.orchestrator._queued_user_message:
                            pending_input = self.orchestrator._queued_user_message
                            self.orchestrator._queued_user_message = None
                        else:
                            pending_input = "Continue."

                        self.call_from_thread(
                            output.write,
                            f"\n[dim][Turn {self._turn_count + 1}][/dim]"
                        )

                except KeyboardInterrupt:
                    self.call_from_thread(
                        output.write,
                        "[yellow]Interrupted. Type to continue or /exit to quit.[/yellow]"
                    )
                    break
                except Exception as e:
                    self.call_from_thread(
                        output.write,
                        f"[red]Error: {rich_escape(str(e))}[/red]"
                    )
                    break
                finally:
                    self._ai_running = False
                    self.call_from_thread(status.update, "Ready")

        def action_pause(self) -> None:
            """Ctrl+C handler — pause the current operation."""
            if self._ai_running:
                self.orchestrator.request_pause()
                output = self.query_one("#output", RichLog)
                output.write(
                    "[bold yellow]Pausing... The current action will finish, "
                    "then CYRAX will stop.[/bold yellow]"
                )
            else:
                output = self.query_one("#output", RichLog)
                output.write("[dim]Not running. Type /exit to quit.[/dim]")

        def action_focus_input(self) -> None:
            """Focus the input bar."""
            self.query_one("#input-bar", Input).focus()

else:
    # Fallback stub if textual is not installed
    class CyraxApp:
        def __init__(self, orchestrator, **kwargs):
            raise ImportError(
                "Textual is not installed. Install with: pip install textual\n"
                "Or use --simple mode for Rich-based console."
            )
