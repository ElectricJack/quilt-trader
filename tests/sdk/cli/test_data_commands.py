# tests/sdk/cli/test_data_commands.py
import json as _json
from click.testing import CliRunner
from unittest.mock import AsyncMock, MagicMock, patch
from sdk.cli.main import quilt


def _resp(json_body, status=200):
    r = MagicMock()
    r.status_code = status
    r.headers = {"content-type": "application/json"}
    r.content = _json.dumps(json_body).encode() if json_body is not None else b""
    r.json.return_value = json_body
    return r


def test_data_subscribe():
    runner = CliRunner()
    with patch("httpx.AsyncClient.post",
                new=AsyncMock(return_value=_resp({"id": "sub-1", "broker": "alpaca", "symbol": "AAPL"}))):
        result = runner.invoke(quilt, ["data", "subscribe", "alpaca", "AAPL"])
    assert result.exit_code == 0
    assert "sub-1" in result.output


def test_data_subscriptions_list():
    runner = CliRunner()
    rows = [{"id": "s1", "broker": "alpaca", "symbol": "AAPL", "status": "running",
              "tick_rate_per_min": 100, "last_tick_at": None,
              "dependent_count": 1}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["data", "subscriptions"])
    assert result.exit_code == 0
    assert "AAPL" in result.output


def test_data_download():
    runner = CliRunner()
    with patch("httpx.AsyncClient.post",
                new=AsyncMock(return_value=_resp({"id": "dl-1"}))):
        result = runner.invoke(quilt, [
            "data", "download",
            "--symbol", "AAPL", "--symbol", "MSFT",
            "--start", "2024-01-01", "--end", "2024-12-31",
        ])
    assert result.exit_code == 0
    assert "dl-1" in result.output


def test_data_downloads_list():
    runner = CliRunner()
    rows = [{"id": "dl-1", "provider": "polygon", "status": "queued",
              "progress_current": 0, "progress_total": 100,
              "date_range_start": "2024-01-01", "date_range_end": "2024-12-31"}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["data", "downloads"])
    assert result.exit_code == 0
    assert "dl-1" in result.output


def test_data_scrapers_list():
    runner = CliRunner()
    rows = [{"name": "alpha-picks", "version": "1.0", "status": "stopped",
              "schedule": "0 9 * * *", "last_success": None,
              "dependent_algorithm_count": 0}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["data", "scrapers"])
    assert result.exit_code == 0
    assert "alpha-picks" in result.output


def test_data_unsubscribe_finds_id_and_calls_unsubscribe():
    runner = CliRunner()
    list_resp = _resp([
        {"id": "s1", "broker": "alpaca", "symbol": "AAPL"},
        {"id": "s2", "broker": "alpaca", "symbol": "MSFT"},
    ])
    unsub_resp = _resp({"ok": True})
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=list_resp)):
        with patch("httpx.AsyncClient.post",
                    new=AsyncMock(return_value=unsub_resp)) as post_mock:
            result = runner.invoke(quilt, ["data", "unsubscribe", "alpaca", "MSFT"])
    assert result.exit_code == 0
    # Verify we hit /api/live-subscriptions/s2/unsubscribe
    args, kwargs = post_mock.call_args
    assert "s2" in args[0]
