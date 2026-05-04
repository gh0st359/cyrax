"""
CYRAX Tmux Dashboard
Manages tmux sessions and panes for visual agent monitoring.
Gracefully degrades if tmux is not available.
"""

import subprocess
import shutil
import time
import threading
from typing import Optional


class TmuxDashboard:
    """
    Manages a tmux session with panes for monitoring agent subprocesses.
    Each agent gets its own pane showing tail -f of its log file.
    Gracefully no-ops if tmux is not installed.
    """

    def __init__(self, session_name: str):
        self.session_name = session_name
        self._available = shutil.which("tmux") is not None
        self._panes: dict[str, str] = {}  # agent_id -> pane_id
        self._session_exists = False

    @property
    def available(self) -> bool:
        return self._available

    def create_session(self) -> bool:
        """Create the tmux session if it doesn't exist. Returns True if created."""
        if not self._available:
            return False

        # Check if session already exists
        result = subprocess.run(
            ["tmux", "has-session", "-t", self.session_name],
            capture_output=True,
        )
        if result.returncode == 0:
            self._session_exists = True
            return True

        # Create a detached session
        result = subprocess.run(
            [
                "tmux", "new-session", "-d",
                "-s", self.session_name,
                "-x", "200", "-y", "50",
            ],
            capture_output=True,
        )
        if result.returncode == 0:
            self._session_exists = True
            # Set pane border status
            subprocess.run(
                ["tmux", "set-option", "-t", self.session_name,
                 "pane-border-status", "top"],
                capture_output=True,
            )
            subprocess.run(
                ["tmux", "set-option", "-t", self.session_name,
                 "pane-border-format", "#{pane_title}"],
                capture_output=True,
            )
            return True
        return False

    def add_agent_pane(self, agent_id: str, log_file: str) -> Optional[str]:
        """
        Add a pane for an agent showing tail -f of its log file.
        Returns the pane ID or None if tmux unavailable.
        """
        if not self._available or not self._session_exists:
            return None

        # Split the window horizontally and run tail -f
        result = subprocess.run(
            [
                "tmux", "split-window", "-t", self.session_name,
                "-h", "-l", "50%",
                f"echo '[{agent_id}] Starting...' && tail -f {log_file} 2>/dev/null || echo '[{agent_id}] No log yet' && sleep 3600",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Try vertical split if horizontal fails
            result = subprocess.run(
                [
                    "tmux", "split-window", "-t", self.session_name,
                    "-v", "-l", "30%",
                    f"echo '[{agent_id}] Starting...' && tail -f {log_file} 2>/dev/null || echo '[{agent_id}] No log yet' && sleep 3600",
                ],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return None

        # Get the pane ID of the newly created pane
        result = subprocess.run(
            [
                "tmux", "list-panes", "-t", self.session_name,
                "-F", "#{pane_id}",
            ],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            panes = result.stdout.strip().split("\n")
            if panes:
                pane_id = panes[-1]  # Most recently created pane
                self._panes[agent_id] = pane_id

                # Set pane title
                self.update_pane_title(agent_id, f"{agent_id}: starting")

                # Rebalance layout
                self.rebalance_layout()
                return pane_id

        return None

    def remove_agent_pane(self, agent_id: str, delay: float = 3.0):
        """Close the pane for a completed/killed agent after a short delay."""
        if not self._available:
            return

        pane_id = self._panes.pop(agent_id, None)
        if not pane_id:
            return

        def _delayed_remove():
            time.sleep(delay)
            subprocess.run(
                ["tmux", "kill-pane", "-t", pane_id],
                capture_output=True,
            )
            self.rebalance_layout()

        threading.Thread(target=_delayed_remove, daemon=True).start()

    def update_pane_title(self, agent_id: str, title: str):
        """Update the border title of an agent's pane."""
        if not self._available:
            return

        pane_id = self._panes.get(agent_id)
        if pane_id:
            subprocess.run(
                ["tmux", "select-pane", "-t", pane_id, "-T", title],
                capture_output=True,
            )

    def send_to_pane(self, agent_id: str, text: str):
        """Send text to a specific pane."""
        if not self._available:
            return

        pane_id = self._panes.get(agent_id)
        if pane_id:
            subprocess.run(
                ["tmux", "send-keys", "-t", pane_id, text, "Enter"],
                capture_output=True,
            )

    def rebalance_layout(self):
        """Rebalance all panes to tiled layout."""
        if not self._available or not self._session_exists:
            return

        subprocess.run(
            ["tmux", "select-layout", "-t", self.session_name, "tiled"],
            capture_output=True,
        )

    def get_attach_command(self) -> str:
        """Return the command to attach to this session."""
        return f"tmux attach -t {self.session_name}"

    def kill_session(self):
        """Kill the entire tmux session."""
        if not self._available:
            return

        subprocess.run(
            ["tmux", "kill-session", "-t", self.session_name],
            capture_output=True,
        )
        self._session_exists = False
        self._panes.clear()

    def get_pane_count(self) -> int:
        """Get the number of active agent panes."""
        return len(self._panes)
