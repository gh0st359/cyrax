import time

import pytest

from agents.ipc import IPCClient, IPCMessage, IPCServer


@pytest.mark.unit
def test_ipc_message_roundtrip_serialization():
    msg = IPCMessage("status", "AGENT-01", {"ok": True, "count": 2})

    restored = IPCMessage.deserialize(msg.serialize())

    assert restored.msg_type == "status"
    assert restored.agent_id == "AGENT-01"
    assert restored.payload == {"ok": True, "count": 2}
    assert restored.timestamp


@pytest.mark.integration
def test_ipc_client_can_reconnect_after_disconnect():
    received = []

    server = IPCServer(session_id="pytest-ipc", on_message=received.append)
    server.start()
    address = server.create_socket("AGENT-RECONNECT")

    try:
        client = IPCClient(address, "AGENT-RECONNECT")
        client.connect(timeout=2)
        client.send(IPCMessage("hello", "AGENT-RECONNECT", {"seq": 1}))
        time.sleep(0.2)
        assert any(m.payload.get("seq") == 1 for m in received)

        # Simulate dropped connection and reconnect.
        server._handle_disconnect("AGENT-RECONNECT")
        time.sleep(0.1)

        reconnected = IPCClient(address, "AGENT-RECONNECT")
        reconnected.connect(timeout=2)
        reconnected.send(IPCMessage("hello", "AGENT-RECONNECT", {"seq": 2}))
        time.sleep(0.2)

        assert any(m.payload.get("seq") == 2 for m in received)

        client.close()
        reconnected.close()
    finally:
        server.shutdown()
