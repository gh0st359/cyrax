"""
CYRAX Inter-Process Communication
Cross-platform TCP-based IPC between orchestrator and agent subprocesses.
Uses localhost TCP sockets (works on Windows, Linux, macOS) with
newline-delimited JSON for message passing.
"""

import json
import os
import socket
import select
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Callable


# Platform detection
_IS_WINDOWS = os.name == "nt"


def _make_wake_pair():
    """
    Create a cross-platform socket pair for waking up select().

    On Windows, select() only works with WinSock sockets (AF_INET/AF_INET6).
    Python 3.12+ added socket.socketpair() for Windows, but it creates AF_UNIX
    sockets which may not be reliably selectable on all Windows Server versions
    used in CI. Always use a TCP loopback pair on Windows for maximum compat.

    On Unix, socket.socketpair() creates AF_UNIX sockets that work with select().
    """
    if not _IS_WINDOWS and hasattr(socket, "socketpair"):
        return socket.socketpair()
    # TCP loopback pair — works on all platforms and all Python versions.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", server.getsockname()[1]))
    conn, _ = server.accept()
    server.close()
    return client, conn


class IPCMessage:
    """Typed IPC message with JSON serialization."""

    def __init__(self, msg_type: str, agent_id: str, payload: dict = None):
        self.msg_type = msg_type
        self.agent_id = agent_id
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.payload = payload or {}

    def serialize(self) -> bytes:
        """Serialize to newline-delimited JSON bytes."""
        data = {
            "msg_type": self.msg_type,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }
        return (json.dumps(data, default=str) + "\n").encode("utf-8")

    @classmethod
    def deserialize(cls, line: bytes) -> "IPCMessage":
        """Deserialize from a JSON line."""
        data = json.loads(line.decode("utf-8").strip())
        msg = cls(data["msg_type"], data["agent_id"], data.get("payload", {}))
        msg.timestamp = data.get("timestamp", "")
        return msg

    def __repr__(self):
        return f"IPCMessage({self.msg_type}, {self.agent_id})"


class IPCServer:
    """
    Orchestrator-side IPC server.
    Creates one TCP listener per agent on localhost, listens for messages
    via a background select()-based poll thread.
    """

    def __init__(
        self,
        session_id: str,
        on_message: Callable[[IPCMessage], None],
        on_disconnect: Optional[Callable[[str], None]] = None,
    ):
        self.session_id = session_id
        self.socket_dir = Path(tempfile.gettempdir()) / f"cyrax-ipc-{session_id}"
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        self._on_message = on_message
        # Optional callback fired when an agent's TCP connection is dropped.
        # Allows the agent pool watchdog to react sooner than the 5-second poll interval.
        self._on_disconnect = on_disconnect

        self._server_sockets: dict[str, socket.socket] = {}   # agent_id -> listening socket
        self._agent_ports: dict[str, int] = {}                 # agent_id -> port
        self._connections: dict[str, socket.socket] = {}       # agent_id -> connected client
        self._buffers: dict[str, bytes] = {}                   # agent_id -> recv buffer
        self._lock = threading.Lock()
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None

        # Cross-platform wake mechanism using socket pair
        self._wake_r, self._wake_w = _make_wake_pair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)

    def create_socket(self, agent_id: str) -> str:
        """Create a TCP listener for an agent. Returns 'localhost:port' address."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))  # Auto-assign port
        sock.listen(1)
        sock.setblocking(False)

        port = sock.getsockname()[1]

        with self._lock:
            self._server_sockets[agent_id] = sock
            self._agent_ports[agent_id] = port
            self._buffers[agent_id] = b""

        # Write port file so agent can discover it
        port_file = self.socket_dir / f"{agent_id}.port"
        port_file.write_text(str(port))

        # Wake poll thread to include new socket
        try:
            self._wake_w.send(b"w")
        except OSError:
            pass

        return f"127.0.0.1:{port}"

    def send(self, agent_id: str, message: IPCMessage):
        """Send a message to a specific agent."""
        with self._lock:
            conn = self._connections.get(agent_id)
        if conn:
            try:
                conn.sendall(message.serialize())
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._handle_disconnect(agent_id)

    def start(self):
        """Start the background poll thread."""
        if self._running:
            return
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="ipc-server"
        )
        self._poll_thread.start()

    def _poll_loop(self):
        """select()-based event loop: accept connections, receive messages."""
        while self._running:
            with self._lock:
                read_list = [self._wake_r]
                for agent_id, sock in self._server_sockets.items():
                    read_list.append(sock)
                for agent_id, conn in self._connections.items():
                    read_list.append(conn)
                # Reverse map: socket -> agent_id
                sock_to_agent = {}
                for agent_id, sock in self._server_sockets.items():
                    sock_to_agent[id(sock)] = ("server", agent_id)
                for agent_id, conn in self._connections.items():
                    sock_to_agent[id(conn)] = ("client", agent_id)

            try:
                readable, _, _ = select.select(read_list, [], [], 1.0)
            except (ValueError, OSError):
                # Socket was closed during select
                time.sleep(0.1)
                continue

            for sock in readable:
                if sock is self._wake_r:
                    # Drain wake socket
                    try:
                        self._wake_r.recv(1024)
                    except OSError:
                        pass
                    continue

                info = sock_to_agent.get(id(sock))
                if not info:
                    continue

                kind, agent_id = info

                if kind == "server":
                    # Accept new connection
                    try:
                        conn, _ = sock.accept()
                        conn.setblocking(False)
                        with self._lock:
                            old = self._connections.get(agent_id)
                            self._connections[agent_id] = conn
                        if old:
                            try:
                                old.close()
                            except OSError:
                                pass
                    except (OSError, BlockingIOError):
                        pass
                elif kind == "client":
                    # Read data from connected client
                    try:
                        data = sock.recv(65536)
                        if not data:
                            self._handle_disconnect(agent_id)
                            continue
                        with self._lock:
                            self._buffers[agent_id] = self._buffers.get(agent_id, b"") + data
                            buf = self._buffers[agent_id]

                        # Process complete messages (newline-delimited)
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            with self._lock:
                                self._buffers[agent_id] = buf
                            if line.strip():
                                try:
                                    msg = IPCMessage.deserialize(line)
                                    self._on_message(msg)
                                except (json.JSONDecodeError, KeyError):
                                    pass
                    except (ConnectionResetError, BrokenPipeError, OSError):
                        self._handle_disconnect(agent_id)

    def _handle_disconnect(self, agent_id: str):
        """Handle agent disconnect.

        Closes the connection socket and fires the on_disconnect callback (if set)
        so the agent pool watchdog can react immediately rather than waiting for the
        next heartbeat check interval.
        """
        with self._lock:
            conn = self._connections.pop(agent_id, None)
            if conn:
                try:
                    conn.close()
                except OSError:
                    pass

        # Fire disconnect callback outside the lock to avoid deadlock
        if self._on_disconnect:
            try:
                self._on_disconnect(agent_id)
            except Exception:
                pass

    def close_agent(self, agent_id: str):
        """Close socket for a specific agent and clean up."""
        with self._lock:
            conn = self._connections.pop(agent_id, None)
            if conn:
                try:
                    conn.close()
                except OSError:
                    pass
            srv = self._server_sockets.pop(agent_id, None)
            if srv:
                try:
                    srv.close()
                except OSError:
                    pass
            self._buffers.pop(agent_id, None)
            self._agent_ports.pop(agent_id, None)
        # Remove port file
        port_file = self.socket_dir / f"{agent_id}.port"
        if port_file.exists():
            try:
                port_file.unlink()
            except OSError:
                pass

    def shutdown(self):
        """Close all sockets, remove temp files."""
        self._running = False
        if self._poll_thread and self._poll_thread.is_alive():
            try:
                self._wake_w.send(b"x")
            except OSError:
                pass
            self._poll_thread.join(timeout=3)

        with self._lock:
            for agent_id in list(self._connections):
                conn = self._connections.pop(agent_id, None)
                if conn:
                    try:
                        conn.close()
                    except OSError:
                        pass
            for agent_id in list(self._server_sockets):
                srv = self._server_sockets.pop(agent_id, None)
                if srv:
                    try:
                        srv.close()
                    except OSError:
                        pass
            self._buffers.clear()
            self._agent_ports.clear()

        for sock in (self._wake_r, self._wake_w):
            try:
                sock.close()
            except OSError:
                pass

        # Clean up temp files
        if self.socket_dir.exists():
            for f in self.socket_dir.glob("*.port"):
                try:
                    f.unlink()
                except OSError:
                    pass


class IPCClient:
    """
    Agent-side IPC client.
    Connects to the orchestrator's TCP socket on localhost.
    """

    def __init__(self, socket_path: str, agent_id: str):
        """
        socket_path: either 'host:port' string or a legacy Unix socket path.
        For TCP, expects '127.0.0.1:PORT' format.
        """
        self.socket_path = socket_path
        self.agent_id = agent_id
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._recv_buffer = b""
        self._connected = False

        # Parse connection address
        if ":" in socket_path and not socket_path.startswith("/"):
            # TCP address: host:port
            parts = socket_path.rsplit(":", 1)
            self._host = parts[0]
            self._port = int(parts[1])
            self._use_tcp = True
        else:
            # Legacy Unix socket path
            self._host = ""
            self._port = 0
            self._use_tcp = False

    def connect(self, timeout: float = 30.0):
        """Connect to the orchestrator's socket with retry."""
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            try:
                if self._use_tcp:
                    self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self._sock.settimeout(5.0)
                    self._sock.connect((self._host, self._port))
                else:
                    self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._sock.settimeout(5.0)
                    self._sock.connect(self.socket_path)
                self._connected = True
                return
            except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
                last_err = e
                if self._sock:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                time.sleep(0.5)
        raise ConnectionError(
            f"Failed to connect to IPC socket {self.socket_path}: {last_err}"
        )

    @property
    def connected(self) -> bool:
        """Whether the client currently has an active socket connection."""
        return self._connected

    def update_socket_path(self, socket_path: str):
        """Update the target socket address and reset connection state."""
        self.socket_path = socket_path
        if ":" in socket_path and not socket_path.startswith("/"):
            parts = socket_path.rsplit(":", 1)
            self._host = parts[0]
            self._port = int(parts[1])
            self._use_tcp = True
        else:
            self._host = ""
            self._port = 0
            self._use_tcp = False

        self.close()

    def send(self, message: IPCMessage):
        """Send a message to the orchestrator."""
        if not self._connected:
            return
        with self._lock:
            try:
                self._sock.sendall(message.serialize())
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._connected = False

    def recv(self, timeout: float = None) -> Optional[IPCMessage]:
        """Receive a single message (blocking with optional timeout)."""
        if not self._connected:
            return None
        deadline = time.time() + timeout if timeout else None

        while True:
            # Check buffer for complete message
            if b"\n" in self._recv_buffer:
                line, self._recv_buffer = self._recv_buffer.split(b"\n", 1)
                if line.strip():
                    try:
                        return IPCMessage.deserialize(line)
                    except (json.JSONDecodeError, KeyError):
                        continue

            # Read more data
            remaining = None
            if deadline:
                remaining = max(0.1, deadline - time.time())
                if remaining <= 0:
                    return None

            try:
                self._sock.settimeout(remaining or 1.0)
                data = self._sock.recv(65536)
                if not data:
                    self._connected = False
                    return None
                self._recv_buffer += data
            except socket.timeout:
                if deadline and time.time() >= deadline:
                    return None
                continue
            except (ConnectionResetError, BrokenPipeError, OSError):
                self._connected = False
                return None

    def recv_nonblocking(self) -> list[IPCMessage]:
        """Receive all available messages without blocking."""
        if not self._connected:
            return []

        messages = []
        try:
            self._sock.setblocking(False)
            while True:
                try:
                    data = self._sock.recv(65536)
                    if not data:
                        self._connected = False
                        break
                    self._recv_buffer += data
                except BlockingIOError:
                    break
                except (ConnectionResetError, BrokenPipeError, OSError):
                    self._connected = False
                    break
        finally:
            if self._sock:
                try:
                    self._sock.setblocking(True)
                except OSError:
                    pass

        while b"\n" in self._recv_buffer:
            line, self._recv_buffer = self._recv_buffer.split(b"\n", 1)
            if line.strip():
                try:
                    messages.append(IPCMessage.deserialize(line))
                except (json.JSONDecodeError, KeyError):
                    pass

        return messages

    def close(self):
        """Close the connection."""
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
