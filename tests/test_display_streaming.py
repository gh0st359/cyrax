from io import StringIO

import pytest
from rich.console import Console

from utils import display


@pytest.fixture(autouse=True)
def reset_streaming_config():
    yield
    display.configure_streaming()


def test_stream_token_writes_model_chunks_directly(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        display,
        "console",
        Console(file=output, force_terminal=False, width=100),
    )

    display.configure_streaming(
        enabled=True,
        delay=999,
        chunk_size=999,
        show_cursor=True,
    )
    display.start_streaming("CYRAX")
    display.stream_token("hello")
    display.stream_token(", operator!")
    display.end_streaming()

    rendered = output.getvalue()
    assert "CYRAX responding" in rendered
    assert "│ hello, operator!" in rendered


def test_configure_streaming_can_disable_cursor(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        display,
        "console",
        Console(file=output, force_terminal=True, color_system=None, width=100),
    )

    display.configure_streaming(
        enabled=True,
        show_cursor=False,
    )
    display.start_streaming("CYRAX")
    display.stream_token("one complete chunk")
    display.end_streaming()

    rendered = output.getvalue()
    assert "one complete chunk" in rendered
    assert "▌" not in rendered
    assert "\x1b[?25l" not in rendered
