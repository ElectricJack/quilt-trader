"""Happy-path smoke tests for the deployment / deploy command group."""
from __future__ import annotations

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from sdk.cli.main import quilt


def _resp(json_body, status=200):
    r = MagicMock()
    r.status_code = status
    r.headers = {"content-type": "application/json"}
    r.content = _json.dumps(json_body).encode() if json_body is not None else b""
    r.json.return_value = json_body
    return r


def test_deployment_list_renders():
    runner = CliRunner()
    rows = [{
        "id": "d1", "algorithm_name": "A", "account_name": "Acc",
        "worker_name": "W", "status": "stopped", "active_run_id": None,
    }]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["deployment", "list"])
    assert result.exit_code == 0, result.output
    assert "d1" in result.output


def test_deployment_start():
    runner = CliRunner()
    with patch("httpx.AsyncClient.post",
               new=AsyncMock(return_value=_resp({"ok": True, "active_run_id": "r1"}))):
        result = runner.invoke(quilt, ["deployment", "start", "d1"])
    assert result.exit_code == 0, result.output
    assert "r1" in result.output


def test_deployment_delete_requires_yes():
    runner = CliRunner()
    result = runner.invoke(quilt, ["deployment", "delete", "d1"])
    assert result.exit_code == 2


def test_deployment_create_invalid_config_json():
    runner = CliRunner()
    result = runner.invoke(quilt, [
        "deployment", "create",
        "--algo", "a", "--account", "ac", "--worker", "w",
        "--config", "not-json",
    ])
    assert result.exit_code == 2


def test_deploy_alias_works():
    runner = CliRunner()
    rows = []
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["deploy", "list"])
    assert result.exit_code == 0, result.output


def test_deployment_activity_follow_not_yet_wired():
    runner = CliRunner()
    result = runner.invoke(quilt, ["deployment", "activity", "d1", "--follow"])
    assert result.exit_code == 2


def test_deployment_show_renders_kv():
    runner = CliRunner()
    payload = {
        "id": "d1", "algorithm_name": "MyAlgo", "account_name": "Paper",
        "worker_name": "Pi-1", "status": "stopped", "active_run_id": None,
        "config_values": {}, "created_at": "2026-01-01T00:00:00Z",
    }
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(payload))):
        result = runner.invoke(quilt, ["deployment", "show", "d1"])
    assert result.exit_code == 0, result.output
    assert "MyAlgo" in result.output


def test_deployment_stop():
    runner = CliRunner()
    with patch("httpx.AsyncClient.post",
               new=AsyncMock(return_value=_resp({"ok": True}))):
        result = runner.invoke(quilt, ["deployment", "stop", "d1"])
    assert result.exit_code == 0, result.output
    assert "stopped" in result.output


def test_deployment_delete_with_yes():
    runner = CliRunner()
    no_content = MagicMock()
    no_content.status_code = 204
    no_content.headers = {"content-type": ""}
    no_content.content = b""
    no_content.json.return_value = {}
    with patch("httpx.AsyncClient.delete", new=AsyncMock(return_value=no_content)):
        result = runner.invoke(quilt, ["deployment", "delete", "d1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "deleted" in result.output


def test_deployment_runs_renders():
    runner = CliRunner()
    rows = [{
        "run_number": 1, "status": "stopped",
        "started_at": "2026-01-01T00:00:00Z", "stopped_at": "2026-01-01T01:00:00Z",
        "net_pnl": 42.0, "trade_count": 5,
    }]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["deployment", "runs", "d1"])
    assert result.exit_code == 0, result.output
    assert "1" in result.output


def test_deployment_create_happy():
    runner = CliRunner()
    payload = {"id": "d2", "status": "stopped"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_resp(payload))):
        result = runner.invoke(quilt, [
            "deployment", "create",
            "--algo", "algo1", "--account", "acct1", "--worker", "w1",
        ])
    assert result.exit_code == 0, result.output
    assert "d2" in result.output


def test_deployment_report_renders():
    runner = CliRunner()
    payload = {
        "deployment_id": "d1",
        "generated_at": "2026-05-17T00:00:00Z",
        "key_metrics": {"strategy": {"cagr": 0.15, "sharpe_ratio": 1.2}},
    }
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(payload))):
        result = runner.invoke(quilt, ["deployment", "report", "d1"])
    assert result.exit_code == 0, result.output
    assert "d1" in result.output
    assert "cagr" in result.output


def test_deployment_trades_renders():
    runner = CliRunner()
    payload = {"items": [{
        "timestamp": "2026-01-02T10:00:00Z", "symbol": "AAPL",
        "side": "buy", "quantity": 10, "fill_price": 150.0,
    }]}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(payload))):
        result = runner.invoke(quilt, ["deployment", "trades", "d1"])
    assert result.exit_code == 0, result.output
    assert "AAPL" in result.output


def test_deployment_activity_renders():
    runner = CliRunner()
    payload = {"items": [{
        "timestamp": "2026-01-02T10:00:00Z", "severity": "info",
        "kind": "log", "event_type": None,
        "logger_name": "algo", "message": "tick processed",
    }]}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(payload))):
        result = runner.invoke(quilt, ["deployment", "activity", "d1"])
    assert result.exit_code == 0, result.output
    assert "tick processed" in result.output


def test_deployment_list_json_flag():
    runner = CliRunner()
    rows = [{"id": "d1", "algorithm_name": "A", "account_name": "Acc",
             "worker_name": "W", "status": "stopped", "active_run_id": None}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["--json", "deployment", "list"])
    assert result.exit_code == 0, result.output
    assert _json.loads(result.output) == rows


def test_deployment_list_status_filter():
    runner = CliRunner()
    rows = [
        {"id": "d1", "algorithm_name": "A", "account_name": "Acc",
         "worker_name": "W", "status": "running", "active_run_id": "r1"},
        {"id": "d2", "algorithm_name": "B", "account_name": "Acc",
         "worker_name": "W", "status": "stopped", "active_run_id": None},
    ]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["deployment", "list", "--status", "running"])
    assert result.exit_code == 0, result.output
    assert "d1" in result.output
    assert "d2" not in result.output
