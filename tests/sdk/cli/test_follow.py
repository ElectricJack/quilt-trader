# tests/sdk/cli/test_follow.py
import json as _json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sdk.cli.follow import _render, follow_target


def test_render_human_format(capsys):
    _render({
        "timestamp": "2026-05-17T13:34:00Z",
        "severity": "info",
        "event_type": "trade_executed",
        "instance_id": "abc12345abc",
        "message": "BUY 10 AAPL",
    }, json_mode=False)
    out = capsys.readouterr().out
    assert "13:34:00" in out
    assert "trade_executed" in out
    assert "BUY 10 AAPL" in out
    assert "abc12345" in out


def test_render_json_format(capsys):
    row = {"timestamp": "x", "type": "activity_event", "message": "hi"}
    _render(row, json_mode=True)
    out = capsys.readouterr().out
    parsed = _json.loads(out)
    assert parsed["message"] == "hi"


@pytest.mark.asyncio
async def test_follow_target_returns_0_on_keyboard_interrupt():
    """Verify a Ctrl+C / cancellation cleanly returns 0."""
    # websockets.connect is used as `async with connect(url) as ws:`
    # so we need a mock that raises KeyboardInterrupt on __aenter__.
    fake_cm = MagicMock()
    fake_cm.__aenter__ = AsyncMock(side_effect=KeyboardInterrupt())
    fake_cm.__aexit__ = AsyncMock(return_value=False)
    with patch("websockets.connect", return_value=fake_cm):
        # No history url → no httpx call
        result = await follow_target(
            ws_url="ws://test/ws/dashboard",
            target="deployment:d1",
            print_history=False,
        )
    assert result == 0
