"""Happy-path smoke tests for algorithm / worker / account / settings command groups."""
from __future__ import annotations

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from sdk.cli.main import quilt


def _make_json_resp(payload):
    """Build a fake httpx.Response that satisfies CoordinatorClient._check."""
    raw = _json.dumps(payload).encode()
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.content = raw
    resp.json.return_value = payload
    return resp


def _make_no_content_resp():
    """Simulate a 204 No Content response."""
    resp = MagicMock()
    resp.status_code = 204
    resp.headers = {"content-type": ""}
    resp.content = b""
    resp.json.return_value = {}
    return resp


# ---------------------------------------------------------------------------
# algorithm
# ---------------------------------------------------------------------------

def test_algorithm_list_renders_rows():
    runner = CliRunner()
    payload = [{"id": "a", "name": "X", "version": "1", "commit_hash": "h", "install_status": "installed"}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["algorithm", "list"])
    assert result.exit_code == 0, result.output
    assert "X" in result.output


def test_algorithm_list_json_flag():
    runner = CliRunner()
    payload = [{"id": "a", "name": "X", "version": "1", "commit_hash": "h", "install_status": "installed"}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["--json", "algorithm", "list"])
    assert result.exit_code == 0, result.output
    assert _json.loads(result.output) == payload


def test_algo_alias_list():
    """The 'algo' alias should work the same as 'algorithm'."""
    runner = CliRunner()
    payload = []
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["algo", "list"])
    assert result.exit_code == 0, result.output


def test_algorithm_uninstall_requires_yes():
    runner = CliRunner()
    result = runner.invoke(quilt, ["algorithm", "uninstall", "abc"])
    assert result.exit_code == 2
    assert "yes" in result.output.lower()


def test_algorithm_uninstall_with_yes():
    runner = CliRunner()
    with patch("httpx.AsyncClient.delete", new=AsyncMock(return_value=_make_no_content_resp())):
        result = runner.invoke(quilt, ["algorithm", "uninstall", "abc", "--yes"])
    assert result.exit_code == 0, result.output
    assert "uninstalled" in result.output


def test_algorithm_show_renders_kv():
    runner = CliRunner()
    payload = {"id": "a", "name": "MyAlgo", "version": "2"}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["algorithm", "show", "a"])
    assert result.exit_code == 0, result.output
    assert "MyAlgo" in result.output


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------

def test_worker_list_renders_rows():
    runner = CliRunner()
    payload = [{
        "id": "w", "name": "Pi-1", "status": "online",
        "tailscale_ip": "100.1", "last_heartbeat": None,
        "install_status": "claimed",
    }]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["worker", "list"])
    assert result.exit_code == 0, result.output
    assert "Pi-1" in result.output


def test_worker_delete_requires_yes():
    runner = CliRunner()
    result = runner.invoke(quilt, ["worker", "delete", "w1"])
    assert result.exit_code == 2
    assert "yes" in result.output.lower()


def test_worker_add_renders_id():
    runner = CliRunner()
    payload = {"id": "w2", "name": "Pi-2", "install_token": "tok123", "install_status": "pending"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["worker", "add", "--name", "Pi-2"])
    assert result.exit_code == 0, result.output
    assert "w2" in result.output


def test_worker_regenerate_token():
    runner = CliRunner()
    payload = {"id": "w1", "name": "Pi-1", "install_token": "newtok", "install_status": "pending"}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["worker", "regenerate-token", "w1"])
    assert result.exit_code == 0, result.output
    assert "newtok" in result.output


# ---------------------------------------------------------------------------
# account
# ---------------------------------------------------------------------------

def test_account_list_with_json_emits_json():
    runner = CliRunner()
    payload = []
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["--json", "account", "list"])
    assert result.exit_code == 0, result.output
    assert _json.loads(result.output) == []


def test_account_list_renders_table():
    runner = CliRunner()
    payload = [{
        "id": "acct1", "name": "Paper Alpaca", "broker_type": "alpaca",
        "environment": "paper", "options_level": None, "locked_by": None,
    }]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["account", "list"])
    assert result.exit_code == 0, result.output
    assert "Paper Alpaca" in result.output


def test_account_delete_requires_yes():
    runner = CliRunner()
    result = runner.invoke(quilt, ["account", "delete", "acct1"])
    assert result.exit_code == 2
    assert "yes" in result.output.lower()


def test_account_delete_with_yes():
    runner = CliRunner()
    with patch("httpx.AsyncClient.delete", new=AsyncMock(return_value=_make_no_content_resp())):
        result = runner.invoke(quilt, ["account", "delete", "acct1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "deleted" in result.output


def test_account_unlock_not_supported():
    runner = CliRunner()
    result = runner.invoke(quilt, ["account", "unlock", "acct1"])
    assert result.exit_code == 2
    assert "not supported" in result.output.lower() or "unlock" in result.output.lower()


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def test_settings_get_renders_table():
    runner = CliRunner()
    payload = {"github_pat_set": True, "coordinator_ip": "100.x.y.z"}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["settings", "get"])
    assert result.exit_code == 0, result.output
    assert "github_pat_set" in result.output


def test_settings_list_alias():
    runner = CliRunner()
    payload = {"github_pat_set": False}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["settings", "list"])
    assert result.exit_code == 0, result.output


def test_settings_get_json():
    runner = CliRunner()
    payload = {"github_pat_set": True}
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["--json", "settings", "get"])
    assert result.exit_code == 0, result.output
    assert _json.loads(result.output) == payload


def test_settings_unset_unknown_key():
    runner = CliRunner()
    result = runner.invoke(quilt, ["settings", "unset", "nonexistent_key"])
    assert result.exit_code == 2
    assert "unknown key" in result.output.lower()


def test_settings_set_unknown_key():
    runner = CliRunner()
    result = runner.invoke(quilt, ["settings", "set", "bad_key", "val"])
    assert result.exit_code == 2
    assert "unknown key" in result.output.lower()


def test_settings_unset_github_pat():
    runner = CliRunner()
    payload = {"github_pat_set": False}
    with patch("httpx.AsyncClient.delete", new=AsyncMock(return_value=_make_json_resp(payload))):
        result = runner.invoke(quilt, ["settings", "unset", "github-pat"])
    assert result.exit_code == 0, result.output
    assert "unset" in result.output
