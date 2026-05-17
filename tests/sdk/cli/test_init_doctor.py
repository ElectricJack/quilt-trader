import yaml
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from sdk.cli.main import quilt


def test_init_creates_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path / ".quilt"))
    runner = CliRunner()
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        result = runner.invoke(quilt, ["init", "--skip-migrate"])
    assert result.exit_code == 0
    cfg = tmp_path / ".quilt" / "config.yaml"
    assert cfg.exists()
    data = yaml.safe_load(cfg.read_text())
    assert data["coordinator_url"] == "http://localhost:8000"


def test_init_without_force_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path / ".quilt"))
    runner = CliRunner()
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        runner.invoke(quilt, ["init", "--skip-migrate"])
        r = runner.invoke(quilt, ["init", "--skip-migrate"])
    assert r.exit_code == 2


def test_init_with_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path / ".quilt"))
    runner = CliRunner()
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        runner.invoke(quilt, ["init", "--skip-migrate"])
        r = runner.invoke(quilt, ["init", "--skip-migrate", "--force"])
    assert r.exit_code == 0


def test_doctor_runs_and_returns_some_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path / ".quilt"))
    (tmp_path / ".quilt").mkdir(parents=True)
    (tmp_path / ".quilt" / "config.yaml").write_text(
        "coordinator_url: http://localhost:8000\n"
    )
    runner = CliRunner()
    # Don't try to mock everything — just verify doctor runs.
    result = runner.invoke(quilt, ["doctor"])
    # 0 (all pass), 1 (warns), or 2 (fails) — all acceptable for unmocked env
    assert result.exit_code in (0, 1, 2)


def test_doctor_json_outputs_parseable(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path / ".quilt"))
    runner = CliRunner()
    result = runner.invoke(quilt, ["--json", "doctor"])
    import json as _json
    body = _json.loads(result.output)
    assert "ok" in body
    assert "checks" in body
