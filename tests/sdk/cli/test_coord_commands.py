import json
import pytest
from click.testing import CliRunner
from unittest.mock import patch
from sdk.cli.main import quilt


def test_coord_status_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    runner = CliRunner()
    with patch("sdk.cli.process.is_healthy_url", return_value=False):
        result = runner.invoke(quilt, ["coord", "status"])
    assert result.exit_code == 0
    assert "stopped" in result.output.lower()


def test_coord_status_json(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    runner = CliRunner()
    with patch("sdk.cli.process.is_healthy_url", return_value=False):
        result = runner.invoke(quilt, ["--json", "coord", "status"])
    body = json.loads(result.output)
    assert body["state"] == "stopped"


def test_coord_stop_no_pid_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(quilt, ["coord", "stop"])
    assert result.exit_code == 0
    assert "not running" in result.output.lower()


def test_coord_logs_when_no_log_file_fails_user_error(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(quilt, ["coord", "logs"])
    assert result.exit_code == 2


def test_up_invokes_coord_start(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    runner = CliRunner()
    with patch("sdk.cli.process.start_coord_daemon", return_value=12345):
        with patch("sdk.cli.process.read_pid", return_value=None):
            result = runner.invoke(quilt, ["up"])
    assert result.exit_code == 0
    assert "12345" in result.output or "started" in result.output.lower()


def test_down_invokes_coord_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(quilt, ["down"])
    assert result.exit_code == 0
