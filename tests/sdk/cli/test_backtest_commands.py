# tests/sdk/cli/test_backtest_commands.py
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


def test_backtest_run_without_wait_returns_id():
    runner = CliRunner()
    with patch("httpx.AsyncClient.post",
                new=AsyncMock(return_value=_resp({"id": "run-1", "status": "queued"}))):
        result = runner.invoke(quilt, [
            "backtest", "run",
            "--algo", "a",
            "--start", "2024-01-01",
            "--end", "2024-12-31",
        ])
    assert result.exit_code == 0
    assert "run-1" in result.output


def test_backtest_run_invalid_config_json():
    runner = CliRunner()
    result = runner.invoke(quilt, [
        "backtest", "run",
        "--algo", "a", "--start", "2024-01-01", "--end", "2024-12-31",
        "--config", "not-json",
    ])
    assert result.exit_code == 2


def test_backtest_list_renders():
    runner = CliRunner()
    rows = [{
        "id": "r1", "algorithm_id": "a", "status": "completed",
        "date_range_start": "2024-01-01T00:00:00Z",
        "date_range_end": "2024-12-31T00:00:00Z",
        "total_return": 0.15, "sharpe_ratio": 1.5,
    }]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["backtest", "list"])
    assert result.exit_code == 0
    assert "r1" in result.output


def test_backtest_delete_requires_yes():
    runner = CliRunner()
    result = runner.invoke(quilt, ["backtest", "delete", "r1"])
    assert result.exit_code == 2


def test_backtest_run_with_wait_polls_until_completed(monkeypatch):
    runner = CliRunner()
    post_resp = _resp({"id": "r1", "status": "queued"})
    # Sequence of GET responses
    responses = [
        _resp({"id": "r1", "status": "running", "progress_message": "..."}),
        _resp({"id": "r1", "status": "running", "progress_message": "more..."}),
        _resp({"id": "r1", "status": "completed",
                "total_return": 0.1, "sharpe_ratio": 1.0,
                "max_drawdown": 0.05, "trade_count": 12, "win_rate": 0.5}),
    ]
    call_idx = {"i": 0}
    async def fake_get(self, url, **kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[min(i, len(responses) - 1)]
    monkeypatch.setattr("time.sleep", lambda s: None)  # speed up the test
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=post_resp)):
        with patch("httpx.AsyncClient.get", new=fake_get):
            result = runner.invoke(quilt, [
                "backtest", "run",
                "--algo", "a", "--start", "2024-01-01", "--end", "2024-12-31",
                "--wait",
            ])
    assert result.exit_code == 0
    assert "completed" in result.output.lower()


def test_backtest_run_with_wait_failed_exits_4(monkeypatch):
    runner = CliRunner()
    post_resp = _resp({"id": "r1", "status": "queued"})
    responses = [
        _resp({"id": "r1", "status": "failed", "error_message": "boom"}),
    ]
    call_idx = {"i": 0}
    async def fake_get(self, url, **kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[min(i, len(responses) - 1)]
    monkeypatch.setattr("time.sleep", lambda s: None)
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=post_resp)):
        with patch("httpx.AsyncClient.get", new=fake_get):
            result = runner.invoke(quilt, [
                "backtest", "run",
                "--algo", "a", "--start", "2024-01-01", "--end", "2024-12-31",
                "--wait",
            ])
    assert result.exit_code == 4
