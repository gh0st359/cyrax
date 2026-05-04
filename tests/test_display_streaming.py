from io import StringIO

import pytest
from rich.console import Console

from utils import display


@pytest.fixture(autouse=True)
def reset_streaming_config():
    yield
    display.configure_streaming()


def test_stream_token_uses_typewriter_chunks_without_delay(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        display,
        "console",
        Console(file=output, force_terminal=False, width=100),
    )
    sleeps = []
    monkeypatch.setattr(display.time, "sleep", lambda delay: sleeps.append(delay))

    display.configure_streaming(
        enabled=True,
        delay=0.01,
        chunk_size=3,
        show_cursor=False,
    )
    display.start_streaming("CYRAX")
    display.stream_token("hello, operator!")
    display.end_streaming()

    rendered = output.getvalue()
    assert "CYRAX responding" in rendered
    assert "│ hello, operator!" in rendered
    assert sleeps == []


def test_configure_streaming_disables_chunk_delays(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        display,
        "console",
        Console(file=output, force_terminal=True, color_system=None, width=100),
    )
    sleeps = []
    monkeypatch.setattr(display.time, "sleep", lambda delay: sleeps.append(delay))

    display.configure_streaming(
        enabled=False,
        delay=0.05,
        chunk_size=1,
        show_cursor=False,
    )
    display.start_streaming("CYRAX")
    display.stream_token("one complete chunk")
    display.end_streaming()

    assert "one complete chunk" in output.getvalue()
    assert sleeps == []
