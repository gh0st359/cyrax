"""
CYRAX Subprocess Agent Pool
Manages agent subprocesses with IPC, tmux dashboard, and kill capability.

Each agent runs as a separate Python process with its own ModelManager,
ToolExecutor, and BrowserManager, communicating via TCP localhost sockets.
Cross-platform: works on Windows, Linux, and macOS.
"""

import os
import sys
import json
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Callable

from agents.ipc import IPCServer, IPCMessage
from utils.tmux import TmuxDashboard
from utils.logging import get_logger

# Platform detection
_IS_WINDOWS = os.name == "nt"


def _is_pid_alive(pid: int) -> bool:
    """Cross-platform check if a process is still running."""
    if _IS_WINDOWS:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def _kill_pid(pid: int, forceful: bool = False):
    """Cross-platform kill a process by PID."""
    if _IS_WINDOWS:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
    else:
        import signal
        sig = signal.SIGKILL if forceful else signal.SIGTERM
        os.kill(pid, sig)


class AgentProcess:
    """Tracks a single agent subprocess."""

    def __init__(
        self,
        agent_id: str,
        agent_type: str,
        task: str,
        pid: int,
        process: Optional[subprocess.Popen],
        manifest_path: str,
        log_file: str,
        socket_path: str,
        socket_generation: int = 1,
    ):
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.task = task
        self.pid = pid
        self.process = process
        self.manifest_path = manifest_path
        self.log_file = log_file
        self.socket_path = socket_path
        self.socket_generation = socket_generation
        self.status = "starting"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.iteration = 0
        self.last_heartbeat = time.time()
        self.last_cmd = ""
        self.report: Optional[dict] = None


class SubprocessAgentPool:
    """
    Manages agent subprocesses with IPC, tmux dashboard, and kill capability.
    Replaces the old ThreadPoolExecutor-based AgentPool.
    """

    def __init__(
        self,
        session_id: str,
        max_concurrent: int = 10,
        on_finding: Optional[Callable] = None,
        on_report: Optional[Callable] = None,
        on_permission_request: Optional[Callable] = None,
        on_agent_complete: Optional[Callable] = None,
        on_agent_status: Optional[Callable] = None,
    ):
        self.session_id = session_id
        self.max_concurrent = max_concurrent
        self._agents: dict[str, AgentProcess] = {}
        self._completed: list[dict] = []
        self._lock = threading.Lock()
        self._logger = get_logger()
        self._reconnect_acks: dict[str, threading.Event] = {}

        # IPC server
        self._ipc = IPCServer(session_id, on_message=self._handle_ipc_message)
        self._ipc.start()

        # Tmux dashboard
        self._tmux = TmuxDashboard(f"cyrax-{session_id}")
        self._tmux_enabled = self._tmux.available

        # Callbacks
        self._on_finding = on_finding
        self._on_report = on_report
        self._on_permission_request = on_permission_request
        self._on_agent_complete = on_agent_complete
        self._on_agent_status = on_agent_status

        # Watchdog thread for dead process detection
        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="agent-watchdog"
        )
        self._watchdog_thread.start()

    @property
    def socket_dir(self) -> Path:
        return self._ipc.socket_dir

    def spawn(
        self,
        agent_id: str,
        agent_type: str,
        task: str,
        model_config: dict,
        tool_config: dict,
        scope_config: dict,
        permission_config: dict,
        mission_briefing: str,
        mission_memory_snapshot: dict,
        campaign_dir: str = "",
    ) -> str:
        """
        Spawn a new agent subprocess. Returns agent_id immediately (non-blocking).
        """
        with self._lock:
            active = sum(1 for a in self._agents.values() if a.status in ("starting", "active"))
            if active >= self.max_concurrent:
                self._logger.warning(
                    f"Agent pool at capacity ({self.max_concurrent}). "
                    f"Waiting for a slot..."
                )
                # Wait for a slot
                self._lock.release()
                try:
                    self._wait_for_slot()
                finally:
                    self._lock.acquire()

        # Create IPC socket for this agent
        socket_generation = 1
        socket_path = self._ipc.create_socket(agent_id)

        # Prepare log file
        log_file = str(self._ipc.socket_dir / f"{agent_id}.log")

        # Write manifest
        manifest = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "task": task,
            "max_iterations": 20,
            "model_config": model_config,
            "tool_config": tool_config,
            "ipc_socket_path": socket_path,
            "scope": scope_config,
            "permission_config": permission_config,
            "mission_briefing": mission_briefing,
            "mission_memory_snapshot": mission_memory_snapshot,
            "log_file": log_file,
            "campaign_dir": campaign_dir,
            "can_spawn_agents": False,
            "reconnect": {
                "session_id": self.session_id,
                "agent_id": agent_id,
                "socket_generation": socket_generation,
                "last_heartbeat": time.time(),
            },
        }

        manifest_path = str(self._ipc.socket_dir / f"{agent_id}_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)

        # Launch subprocess (platform-specific for daemon capability)
        python_exe = sys.executable
        log_handle = open(log_file, "a")
        popen_kwargs = {
            "cwd": str(Path(__file__).parent.parent),  # Project root
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "env": os.environ.copy(),  # Inherit API keys from environment
        }
        if _IS_WINDOWS:
            # CREATE_NEW_PROCESS_GROUP: survives parent exit on Windows
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # start_new_session: survives parent exit on Unix (daemon)
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(
            [python_exe, "-m", "agents.process_runner", manifest_path],
            **popen_kwargs,
        )

        agent_proc = AgentProcess(
            agent_id=agent_id,
            agent_type=agent_type,
            task=task,
            pid=process.pid,
            process=process,
            manifest_path=manifest_path,
            log_file=log_file,
            socket_path=socket_path,
            socket_generation=socket_generation,
        )

        with self._lock:
            self._agents[agent_id] = agent_proc

        self._logger.info(
            f"Agent {agent_id} spawned as subprocess (PID {process.pid})"
        )

        # Create tmux pane if available
        if self._tmux_enabled:
            if not self._tmux._session_exists:
                self._tmux.create_session()
            self._tmux.add_agent_pane(agent_id, log_file)

        return agent_id

    def kill(self, agent_id: str, graceful: bool = True, timeout: float = 15.0) -> bool:
        """
        Kill an agent process with 3-stage escalation:
        1. Graceful IPC shutdown
        2. SIGTERM
        3. SIGKILL
        """
        with self._lock:
            agent = self._agents.get(agent_id)
        if not agent or agent.status in ("completed", "failed", "killed"):
            return False

        if graceful:
            # Stage 1: IPC shutdown request
            self._ipc.send(agent_id, IPCMessage(
                "shutdown", agent_id,
                {"reason": "Kill requested by orchestrator", "graceful": True},
            ))
            deadline = time.time() + timeout
            while time.time() < deadline:
                if agent.process.poll() is not None:
                    agent.status = "killed"
                    self._cleanup_agent(agent_id)
                    return True
                time.sleep(0.5)

        # Stage 2: terminate (SIGTERM on Unix, TerminateProcess on Windows)
        try:
            if agent.process:
                agent.process.terminate()
            else:
                # Reconnected agent — no Popen object
                _kill_pid(agent.pid, forceful=False)
        except (ProcessLookupError, PermissionError, OSError):
            agent.status = "killed"
            self._cleanup_agent(agent_id)
            return True

        sigterm_deadline = time.time() + 5.0
        while time.time() < sigterm_deadline:
            if agent.process and agent.process.poll() is not None:
                agent.status = "killed"
                self._cleanup_agent(agent_id)
                return True
            elif not agent.process and not _is_pid_alive(agent.pid):
                agent.status = "killed"
                self._cleanup_agent(agent_id)
                return True
            time.sleep(0.5)

        # Stage 3: force kill (SIGKILL on Unix, TerminateProcess on Windows)
        try:
            if agent.process:
                agent.process.kill()
            else:
                _kill_pid(agent.pid, forceful=True)
        except (ProcessLookupError, PermissionError, OSError):
            pass

        try:
            agent.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

        agent.status = "killed"
        self._cleanup_agent(agent_id)
        return True

    def kill_all(self, graceful: bool = True):
        """Kill all running agents."""
        with self._lock:
            running = [
                aid for aid, a in self._agents.items()
                if a.status in ("starting", "active")
            ]
        for agent_id in running:
            self.kill(agent_id, graceful=graceful, timeout=5.0)

    def get_status(self) -> dict[str, dict]:
        """Get status of all agents (running and completed)."""
        with self._lock:
            status = {}
            for aid, agent in self._agents.items():
                status[aid] = {
                    "type": agent.agent_type,
                    "task": agent.task[:80],
                    "status": agent.status,
                    "pid": agent.pid,
                    "iteration": agent.iteration,
                    "last_cmd": agent.last_cmd[:60] if agent.last_cmd else "",
                    "started_at": agent.started_at,
                    "socket_generation": agent.socket_generation,
                    "socket_path": agent.socket_path,
                    "last_heartbeat": agent.last_heartbeat,
                }
            return status

    def get_running(self) -> list[str]:
        """Get IDs of currently running agents."""
        with self._lock:
            return [
                aid for aid, a in self._agents.items()
                if a.status in ("starting", "active")
            ]

    def get_completed(self) -> list[dict]:
        """Pop and return completed agent reports."""
        with self._lock:
            completed = list(self._completed)
            self._completed.clear()
        return completed

    def active_count(self) -> int:
        """Number of currently running agent processes."""
        with self._lock:
            return sum(
                1 for a in self._agents.values()
                if a.status in ("starting", "active")
            )

    def wait_all(self, timeout: float = None) -> list[dict]:
        """Wait for all running agents to complete."""
        deadline = time.time() + timeout if timeout else None
        while True:
            running = self.get_running()
            if not running:
                break
            if deadline and time.time() >= deadline:
                break
            time.sleep(1.0)
        return self.get_completed()

    def send_instruction(self, agent_id: str, instruction: str):
        """Send an instruction to a running agent via IPC."""
        self._ipc.send(agent_id, IPCMessage(
            "instruction", agent_id, {"text": instruction},
        ))

    def respond_permission(self, agent_id: str, request_id: str,
                           allowed: bool, reason: str = ""):
        """Send a permission response to an agent."""
        self._ipc.send(agent_id, IPCMessage(
            "permission_response", agent_id,
            {"request_id": request_id, "allowed": allowed, "reason": reason},
        ))

    def reconnect_agent(self, agent_id: str, pid: int, agent_info: dict,
                        socket_path: str):
        """Reconnect to an orphaned agent process (daemon mode)."""
        if not _is_pid_alive(pid):
            return False

        expected_session_id = agent_info.get("session_id", "")
        if expected_session_id and expected_session_id != self.session_id:
            self._logger.warning(
                f"Skipping reconnect for {agent_id}: session mismatch "
                f"({expected_session_id} != {self.session_id})"
            )
            return False

        previous_generation = int(agent_info.get("socket_generation", 1) or 1)
        next_generation = previous_generation + 1

        # Re-create IPC socket and wait for explicit reconnect handshake
        new_socket_path = self._ipc.create_socket(agent_id)

        manifest = {
            "ipc_socket_path": new_socket_path,
            "reconnect": {
                "session_id": self.session_id,
                "agent_id": agent_id,
                "socket_generation": next_generation,
                "last_heartbeat": time.time(),
            },
        }
        manifest_path = self._ipc.socket_dir / f"{agent_id}_reconnect.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

        ack_event = threading.Event()
        with self._lock:
            self._reconnect_acks[agent_id] = ack_event
            self._agents[agent_id] = AgentProcess(
                agent_id=agent_id,
                agent_type=agent_info.get("type", "unknown"),
                task=agent_info.get("task", "unknown"),
                pid=pid,
                process=None,
                manifest_path="",
                log_file=agent_info.get("log_file", ""),
                socket_path=new_socket_path,
                socket_generation=next_generation,
            )
            self._agents[agent_id].status = "reconnecting"

        deadline = time.time() + 15.0
        while time.time() < deadline:
            if not _is_pid_alive(pid):
                break
            if ack_event.wait(timeout=0.5):
                with self._lock:
                    self._reconnect_acks.pop(agent_id, None)
                self._logger.info(
                    f"Reconnected to orphaned agent {agent_id} (PID {pid}, gen {next_generation})"
                )
                return True

        with self._lock:
            self._reconnect_acks.pop(agent_id, None)
            agent = self._agents.get(agent_id)
            if agent:
                agent.status = "failed"
        self._ipc.close_agent(agent_id)
        self._logger.warning(f"Reconnect ack not received for {agent_id}; failing fast")
        return False

    def _handle_ipc_message(self, message: IPCMessage):
        """Central IPC message dispatcher."""
        agent_id = message.agent_id
        msg_type = message.msg_type
        payload = message.payload

        with self._lock:
            agent = self._agents.get(agent_id)

        if msg_type == "agent_ready":
            if agent:
                agent.status = "active"
                agent.pid = payload.get("pid", agent.pid)
                agent.socket_generation = int(payload.get("socket_generation", agent.socket_generation) or agent.socket_generation)
                agent.last_heartbeat = time.time()
                self._logger.info(f"Agent {agent_id} ready (PID {agent.pid})")

        elif msg_type == "agent_reconnect":
            if agent:
                expected_gen = agent.socket_generation
                provided_gen = int(payload.get("socket_generation", 0) or 0)
                provided_session = payload.get("session_id", "")
                if provided_session != self.session_id or provided_gen != expected_gen:
                    self._logger.warning(
                        f"Rejecting reconnect from {agent_id}: session/gen mismatch"
                    )
                    self._ipc.send(agent_id, IPCMessage(
                        "agent_reconnect_ack", agent_id,
                        {
                            "accepted": False,
                            "reason": "session_or_generation_mismatch",
                            "expected_generation": expected_gen,
                        },
                    ))
                else:
                    agent.status = "active"
                    agent.pid = payload.get("pid", agent.pid)
                    agent.last_heartbeat = time.time()
                    self._ipc.send(agent_id, IPCMessage(
                        "agent_reconnect_ack", agent_id,
                        {
                            "accepted": True,
                            "socket_generation": expected_gen,
                        },
                    ))
                    with self._lock:
                        event = self._reconnect_acks.get(agent_id)
                    if event:
                        event.set()

        elif msg_type == "status_update":
            if agent:
                agent.iteration = payload.get("iteration", agent.iteration)
                agent.status = payload.get("status", agent.status)
                agent.last_cmd = payload.get("last_cmd", "")
                agent.socket_generation = int(payload.get("socket_generation", agent.socket_generation) or agent.socket_generation)
                agent.last_heartbeat = time.time()
                # Update tmux pane title
                if self._tmux_enabled:
                    self._tmux.update_pane_title(
                        agent_id,
                        f"{agent_id}: iter {agent.iteration} - {agent.status}",
                    )
                if self._on_agent_status:
                    self._on_agent_status(agent_id, payload)

        elif msg_type == "finding":
            if self._on_finding:
                self._on_finding(agent_id, payload)

        elif msg_type == "report":
            if self._on_report:
                self._on_report(agent_id, payload.get("update", ""))

        elif msg_type == "permission_request":
            if self._on_permission_request:
                self._on_permission_request(agent_id, payload)

        elif msg_type == "agent_complete":
            report = payload.get("report", {})
            if agent:
                agent.status = report.get("status", "completed")
                agent.report = report
            with self._lock:
                self._completed.append(report)
            self._logger.info(
                f"Agent {agent_id} completed: {report.get('status', '?')} "
                f"({len(report.get('findings', []))} findings)"
            )
            if self._on_agent_complete:
                self._on_agent_complete(agent_id, report)
            # Clean up tmux pane
            if self._tmux_enabled:
                self._tmux.update_pane_title(agent_id, f"{agent_id}: DONE")
                self._tmux.remove_agent_pane(agent_id, delay=5.0)
            # Clean up IPC socket
            self._ipc.close_agent(agent_id)

        elif msg_type == "agent_error":
            error = payload.get("error", "Unknown error")
            self._logger.log_error(agent_id, f"Agent crashed: {error}")
            if agent:
                agent.status = "failed"
                agent.report = {
                    "agent_id": agent_id,
                    "task": agent.task,
                    "status": "failed",
                    "iterations": agent.iteration,
                    "summary": f"Agent crashed: {error}",
                    "findings": [],
                }
                with self._lock:
                    self._completed.append(agent.report)
            if self._on_agent_complete and agent:
                self._on_agent_complete(agent_id, agent.report)
            if self._tmux_enabled:
                self._tmux.update_pane_title(agent_id, f"{agent_id}: FAILED")
                self._tmux.remove_agent_pane(agent_id, delay=5.0)
            self._ipc.close_agent(agent_id)

        elif msg_type == "log_command":
            self._logger.log_command(
                agent_id,
                payload.get("command", ""),
                payload.get("output_preview", ""),
                payload.get("exit_code", -1),
            )

        elif msg_type == "pong":
            if agent:
                agent.last_heartbeat = time.time()
                agent.iteration = payload.get("iteration", agent.iteration)

    def _watchdog_loop(self):
        """Monitor agent processes for unexpected exits and stale heartbeats."""
        while self._watchdog_running:
            with self._lock:
                agents_to_check = [
                    (aid, a) for aid, a in self._agents.items()
                    if a.status in ("starting", "active")
                ]

            for agent_id, agent in agents_to_check:
                # Check if process exited
                if agent.process and agent.process.poll() is not None:
                    exit_code = agent.process.returncode
                    if agent.status in ("starting", "active"):
                        if exit_code == 0:
                            self._logger.info(
                                f"Agent {agent_id} exited cleanly without completion report"
                            )
                            agent.status = "completed"
                            fallback_status = "completed"
                        else:
                            self._logger.warning(
                                f"Agent {agent_id} process exited unexpectedly "
                                f"(code {exit_code})"
                            )
                            agent.status = "failed"
                            fallback_status = "failed"
                        if not agent.report:
                            agent.report = {
                                "agent_id": agent_id,
                                "task": agent.task,
                                "status": fallback_status,
                                "iterations": agent.iteration,
                                "summary": f"Process exited with code {exit_code}",
                                "findings": [],
                            }
                            with self._lock:
                                self._completed.append(agent.report)
                            if self._on_agent_complete:
                                self._on_agent_complete(agent_id, agent.report)
                        if self._tmux_enabled:
                            self._tmux.update_pane_title(
                                agent_id, f"{agent_id}: CRASHED"
                            )
                            self._tmux.remove_agent_pane(agent_id, delay=5.0)
                        self._ipc.close_agent(agent_id)

                # Check heartbeat staleness (>120s = presumed dead)
                elif (time.time() - agent.last_heartbeat) > 120:
                    self._logger.warning(
                        f"Agent {agent_id} heartbeat stale (>120s). Sending ping."
                    )
                    self._ipc.send(agent_id, IPCMessage("ping", agent_id))
                    # Give it 30 more seconds before declaring dead
                    if (time.time() - agent.last_heartbeat) > 150:
                        self._logger.warning(
                            f"Agent {agent_id} presumed dead (no heartbeat >150s)"
                        )
                        self.kill(agent_id, graceful=False, timeout=5.0)

            time.sleep(5.0)

    def _wait_for_slot(self):
        """Wait until a slot opens in the pool."""
        while True:
            if self.active_count() < self.max_concurrent:
                return
            time.sleep(1.0)

    def _cleanup_agent(self, agent_id: str):
        """Clean up resources for a terminated agent."""
        if self._tmux_enabled:
            self._tmux.remove_agent_pane(agent_id, delay=3.0)
        self._ipc.close_agent(agent_id)

    def shutdown(self, wait: bool = True):
        """Shut down the pool: kill all agents, close IPC, destroy tmux."""
        self._watchdog_running = False

        try:
            if wait:
                self.kill_all(graceful=True)
            else:
                self.kill_all(graceful=False)
        except KeyboardInterrupt:
            # Force cleanup on repeated Ctrl+C without crashing the parent process
            self.kill_all(graceful=False)

        self._ipc.shutdown()

        if self._tmux_enabled and self._tmux._session_exists:
            self._tmux.kill_session()


# Backward compatibility alias
AgentPool = SubprocessAgentPool
