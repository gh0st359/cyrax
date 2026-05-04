"""
CYRAX Agent Process Runner
Entry point for agent subprocesses. Each agent runs in its own process
with isolated ModelManager, ToolExecutor, and BrowserManager.

Usage:
    python -m agents.process_runner /path/to/manifest.json
"""

import sys
import os
import json
import signal
import logging
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.ipc import IPCClient, IPCMessage  # noqa: E402
from agents.recon_agent import ReconAgent  # noqa: E402
from agents.exploit_agent import ExploitAgent  # noqa: E402
from agents.post_exploit_agent import PostExploitAgent  # noqa: E402
from agents.ad_agent import ActiveDirectoryAgent  # noqa: E402
from agents.web_agent import WebAgent  # noqa: E402
from agents.cloud_agent import CloudAgent  # noqa: E402
from agents.osint_agent import OSINTAgent  # noqa: E402
from models.model_manager import ModelManager  # noqa: E402
from tools.executor import ToolExecutor  # noqa: E402
from tools.tool_registry import ToolRegistry  # noqa: E402
from utils.safety import ScopeEnforcer, classify_action  # noqa: E402
from utils.logging import init_logger  # noqa: E402


AGENT_CLASSES = {
    "recon": ReconAgent,
    "exploit": ExploitAgent,
    "post": PostExploitAgent,
    "ad": ActiveDirectoryAgent,
    "web": WebAgent,
    "cloud": CloudAgent,
    "osint": OSINTAgent,
}


class IPCPermissionGate:
    """
    Permission gate that proxies checks through IPC to the orchestrator.
    Agent subprocess sends permission_request, blocks until response.
    """

    def __init__(self, ipc_client: IPCClient, config: dict):
        self.ipc = ipc_client
        self.auto_approve = config.get("auto_approve", False)
        self.session_approvals: dict[str, str] = config.get("session_approvals", {})
        self.enabled = True
        self._pending: dict[str, threading.Event] = {}
        self._responses: dict[str, tuple[bool, str]] = {}
        self._lock = threading.Lock()

    def check(self, command: str) -> tuple[bool, str]:
        """Classify action, check local approvals, or forward to orchestrator."""
        if not self.enabled or self.auto_approve:
            return True, ""

        action_type = classify_action(command)

        # Check session-level overrides
        level = self.session_approvals.get(action_type)
        if level == "allow":
            return True, ""
        if level == "deny":
            return False, f"Action type '{action_type}' is denied by policy."

        # Check default levels
        from utils.safety import PermissionGate
        default_level = PermissionGate.ACTIONS.get(action_type, "allow")
        if default_level == "allow":
            return True, ""
        if default_level == "deny":
            return False, f"Action type '{action_type}' is denied by policy."

        # For ask_first: check if already approved this session
        if default_level == "ask_first" and action_type in self.session_approvals:
            return True, ""

        # Need to ask orchestrator
        request_id = str(uuid.uuid4())[:8]
        event = threading.Event()

        with self._lock:
            self._pending[request_id] = event

        # Send permission request via IPC
        self.ipc.send(IPCMessage(
            "permission_request",
            self.ipc.agent_id,
            {
                "request_id": request_id,
                "action_type": action_type,
                "command": command[:200],
            },
        ))

        # Block waiting for response. Keep a long timeout so the operator has
        # time to review/decide without sub-agents spuriously continuing.
        if not event.wait(timeout=300.0):
            with self._lock:
                self._pending.pop(request_id, None)
            return False, "Permission request timed out (300s)."

        with self._lock:
            self._pending.pop(request_id, None)
            allowed, reason = self._responses.pop(request_id, (False, "No response"))

        # If allowed and ask_first, remember for session
        if allowed and default_level == "ask_first":
            self.session_approvals[action_type] = "allow"

        return allowed, reason

    def receive_response(self, request_id: str, allowed: bool, reason: str = ""):
        """Called by IPC listener when permission_response arrives."""
        with self._lock:
            self._responses[request_id] = (allowed, reason)
            event = self._pending.get(request_id)
        if event:
            event.set()

    def auto_approve_all(self):
        self.auto_approve = True

    def approve_category(self, category: str):
        self.session_approvals[category] = "allow"


class AgentProcessRunner:
    """
    Runs a single agent in an isolated subprocess.
    Loads a manifest, creates its own ModelManager/ToolExecutor,
    communicates with orchestrator via IPC.
    """

    def __init__(self, manifest_path: str):
        with open(manifest_path, "r") as f:
            self.manifest = json.load(f)

        self.agent_id = self.manifest["agent_id"]
        self.agent_type = self.manifest["agent_type"]
        self.task = self.manifest["task"]
        self.log_file = self.manifest.get("log_file", f"/tmp/cyrax-agent-{self.agent_id}.log")
        reconnect_meta = self.manifest.get("reconnect", {})
        self._reconnect_session_id = reconnect_meta.get("session_id", "")
        self._socket_generation = int(reconnect_meta.get("socket_generation", 1) or 1)
        self._last_heartbeat = float(reconnect_meta.get("last_heartbeat", time.time()) or time.time())
        self._reconnect_acked = False
        self._last_reconnect_attempt = 0.0

        # Set up file logging
        self._setup_logging()

        # IPC client
        self.ipc = IPCClient(
            self.manifest["ipc_socket_path"],
            self.agent_id,
        )

        # Permission gate (proxies through IPC)
        self.permission_gate = IPCPermissionGate(
            self.ipc,
            self.manifest.get("permission_config", {}),
        )

        # Initialize components
        self.model = self._init_model()
        self.tools = self._init_tools()
        self.agent = self._init_agent()

        self._shutdown_requested = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._reconnect_thread: Optional[threading.Thread] = None

    def _setup_logging(self):
        """Set up logging to the agent's log file."""
        log_dir = os.path.dirname(self.log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # File handler for agent log
        self._file_handler = logging.FileHandler(self.log_file)
        self._file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        # Also redirect stdout/stderr to log file
        self._log_stream = open(self.log_file, "a", buffering=1)

        # Init the CYRAX logger
        init_logger(log_dir=log_dir or "logs", level="INFO")

    def _setup_signal_handlers(self):
        """Register signal handlers for graceful shutdown (cross-platform)."""
        def handle_sigterm(signum, frame):
            self._shutdown_requested = True
            if self.agent:
                self.agent.request_shutdown()

        # SIGINT works on all platforms
        signal.signal(signal.SIGINT, handle_sigterm)
        # SIGTERM is available on Unix; on Windows it's limited but still works
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handle_sigterm)
        # On Windows, also handle CTRL_BREAK_EVENT via SIGBREAK
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, handle_sigterm)

    def _init_model(self) -> ModelManager:
        """Create a ModelManager from manifest config + environment API keys."""
        model_config = dict(self.manifest["model_config"])
        # API key comes from environment, not manifest
        # ModelManager reads it from env automatically
        return ModelManager(model_config)

    def _init_tools(self) -> ToolRegistry:
        """Create ToolExecutor and ToolRegistry from manifest config."""
        tool_config = self.manifest.get("tool_config", {})
        executor = ToolExecutor(
            work_dir=tool_config.get("work_dir", "/tmp/cyrax"),
            timeout=tool_config.get("timeout", 300),
            allow_dangerous=tool_config.get("allow_dangerous", False),
        )
        return ToolRegistry(executor=executor)

    def _init_agent(self):
        """Instantiate the correct agent subclass."""
        agent_class = AGENT_CLASSES.get(self.agent_type)
        if not agent_class:
            raise ValueError(f"Unknown agent type: {self.agent_type}")

        agent = agent_class(
            agent_id=self.agent_id,
            task=self.task,
            model=self.model,
            tools=self.tools,
            parent=None,        # No parent in subprocess mode
            browser=None,       # Lazy init on first browser command
            max_iterations=self.manifest.get("max_iterations", 20),
        )

        # Set up scope enforcement
        scope_config = self.manifest.get("scope", {})
        if scope_config.get("enabled"):
            agent.scope = ScopeEnforcer(scope_config.get("raw_targets", []))

        # Set permission gate (IPC-based)
        agent.permission_gate = self.permission_gate

        # Set mission briefing
        agent.mission_briefing = self.manifest.get("mission_briefing", "")

        # Set IPC client for finding/report forwarding
        agent.ipc_client = self.ipc

        return agent

    def _reconnect_manifest_path(self) -> Path:
        return Path(self.log_file).parent / f"{self.agent_id}_reconnect.json"

    def _attempt_reconnect(self) -> bool:
        reconnect_file = self._reconnect_manifest_path()
        if not reconnect_file.exists():
            return False
        try:
            reconnect_data = json.loads(reconnect_file.read_text())
        except Exception:
            return False

        socket_path = reconnect_data.get("ipc_socket_path", "")
        reconnect_meta = reconnect_data.get("reconnect", {})
        if not socket_path:
            return False

        self.ipc.update_socket_path(socket_path)
        try:
            self.ipc.connect(timeout=5.0)
        except Exception:
            return False

        self._reconnect_session_id = reconnect_meta.get("session_id", self._reconnect_session_id)
        self._socket_generation = int(reconnect_meta.get("socket_generation", self._socket_generation) or self._socket_generation)
        self._reconnect_acked = False
        self.ipc.send(IPCMessage(
            "agent_reconnect",
            self.agent_id,
            {
                "pid": os.getpid(),
                "session_id": self._reconnect_session_id,
                "socket_generation": self._socket_generation,
                "last_heartbeat": self._last_heartbeat,
            },
        ))

        ack_deadline = time.time() + 8.0
        while time.time() < ack_deadline and not self._shutdown_requested:
            if self._reconnect_acked:
                self._last_reconnect_attempt = time.time()
                return True
            ack = self.ipc.recv(timeout=0.5)
            if ack and ack.msg_type == "agent_reconnect_ack":
                if ack.payload.get("accepted", False):
                    self._reconnect_acked = True
                    self._last_reconnect_attempt = time.time()
                    return True
                return False

        return False

    def _reconnect_monitor_loop(self):
        """Detect orchestrator disconnect and periodically attempt reconnect."""
        while not self._shutdown_requested:
            if not self.ipc.connected:
                now = time.time()
                if (now - self._last_reconnect_attempt) >= 5.0:
                    self._last_reconnect_attempt = now
                    self._attempt_reconnect()
            time.sleep(1.0)

    def run(self):
        """
        Main execution:
        1. Connect to IPC socket
        2. Send 'agent_ready' with PID
        3. Start heartbeat + IPC listener threads
        4. Call agent.execute()
        5. Send 'agent_complete' with report
        6. Cleanup
        """
        self._setup_signal_handlers()

        try:
            # Connect to orchestrator
            self.ipc.connect(timeout=30.0)
            self.ipc.send(IPCMessage(
                "agent_ready",
                self.agent_id,
                {
                    "pid": os.getpid(),
                    "session_id": self._reconnect_session_id,
                    "socket_generation": self._socket_generation,
                },
            ))

            # Start background threads
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True, name=f"{self.agent_id}-heartbeat"
            )
            self._heartbeat_thread.start()

            self._listener_thread = threading.Thread(
                target=self._ipc_listener_loop, daemon=True, name=f"{self.agent_id}-listener"
            )
            self._listener_thread.start()

            self._reconnect_thread = threading.Thread(
                target=self._reconnect_monitor_loop, daemon=True, name=f"{self.agent_id}-reconnect"
            )
            self._reconnect_thread.start()

            # Execute the agent
            report = self.agent.execute()

            # Send completion report
            self.ipc.send(IPCMessage(
                "agent_complete",
                self.agent_id,
                {"report": report},
            ))

        except Exception as e:
            tb = traceback.format_exc()
            try:
                self.ipc.send(IPCMessage(
                    "agent_error",
                    self.agent_id,
                    {"error": str(e), "traceback": tb},
                ))
            except Exception:
                pass
            logging.error(f"Agent {self.agent_id} crashed: {e}\n{tb}")

        finally:
            self._cleanup()

    def _heartbeat_loop(self):
        """Send status_update every 10 seconds."""
        while not self._shutdown_requested:
            try:
                self.ipc.send(IPCMessage(
                    "status_update",
                    self.agent_id,
                    {
                        "iteration": self.agent.iteration,
                        "status": self.agent.status,
                        "last_cmd": (
                            self.agent._recent_cmds[-1][:80]
                            if self.agent._recent_cmds else ""
                        ),
                        "session_id": self._reconnect_session_id,
                        "socket_generation": self._socket_generation,
                    },
                ))
                self._last_heartbeat = time.time()
            except Exception:
                pass
            # Sleep in small increments for fast shutdown response
            for _ in range(20):
                if self._shutdown_requested:
                    return
                time.sleep(0.5)

    def _ipc_listener_loop(self):
        """Listen for downstream messages from orchestrator."""
        while not self._shutdown_requested:
            try:
                msg = self.ipc.recv(timeout=1.0)
                if msg is None:
                    if not self.ipc.connected:
                        time.sleep(0.2)
                    continue

                if msg.msg_type == "permission_response":
                    payload = msg.payload
                    self.permission_gate.receive_response(
                        payload.get("request_id", ""),
                        payload.get("allowed", False),
                        payload.get("reason", ""),
                    )

                elif msg.msg_type == "shutdown":
                    self._shutdown_requested = True
                    if self.agent:
                        self.agent.request_shutdown()

                elif msg.msg_type == "instruction":
                    if self.agent:
                        self.agent.receive_instruction(
                            msg.payload.get("text", "")
                        )

                elif msg.msg_type == "ping":
                    self.ipc.send(IPCMessage(
                        "pong",
                        self.agent_id,
                        {
                            "iteration": self.agent.iteration if self.agent else 0,
                            "status": self.agent.status if self.agent else "unknown",
                            "socket_generation": self._socket_generation,
                        },
                    ))

                elif msg.msg_type == "agent_reconnect_ack":
                    self._reconnect_acked = msg.payload.get("accepted", False)

            except Exception:
                if self._shutdown_requested:
                    return
                time.sleep(0.5)

    def _cleanup(self):
        """Clean up resources."""
        self._shutdown_requested = True
        # Close browser if agent created one
        if self.agent and self.agent.browser:
            try:
                self.agent.browser.close()
            except Exception:
                pass
        # Close IPC
        self.ipc.close()
        # Close log stream
        if hasattr(self, '_log_stream') and self._log_stream:
            try:
                self._log_stream.close()
            except Exception:
                pass


def main():
    """Subprocess entry point."""
    if len(sys.argv) < 2:
        print("Usage: python -m agents.process_runner <manifest_path>", file=sys.stderr)
        sys.exit(1)

    manifest_path = sys.argv[1]
    if not os.path.exists(manifest_path):
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    runner = AgentProcessRunner(manifest_path)
    runner.run()


if __name__ == "__main__":
    main()
