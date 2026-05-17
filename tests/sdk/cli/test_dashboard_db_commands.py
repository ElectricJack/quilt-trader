from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from sdk.cli.main import quilt


def test_dashboard_build_invokes_npm_run_build():
    runner = CliRunner()
    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as m:
        result = runner.invoke(quilt, ["dashboard", "build"])
    assert result.exit_code == 0
    args = m.call_args.args[0]
    assert args[:3] == ["npm", "run", "build"]


def test_dashboard_build_fails_when_npm_fails():
    runner = CliRunner()
    with patch("subprocess.run", return_value=MagicMock(returncode=1)):
        result = runner.invoke(quilt, ["dashboard", "build"])
    assert result.exit_code == 4


def test_db_status_runs_alembic_current():
    runner = CliRunner()
    fake = MagicMock(returncode=0, stdout=b"abc1234 (head)\n", stderr=b"")
    with patch("subprocess.run", return_value=fake):
        result = runner.invoke(quilt, ["db", "status"])
    assert result.exit_code == 0
    assert "abc1234" in result.output


def test_db_migrate_runs_alembic_upgrade_head():
    runner = CliRunner()
    fake = MagicMock(returncode=0, stdout=b"OK\n", stderr=b"")
    with patch("subprocess.run", return_value=fake) as m:
        result = runner.invoke(quilt, ["db", "migrate"])
    assert result.exit_code == 0
    args = m.call_args.args[0]
    assert "upgrade" in args and "head" in args


def test_db_revisions_runs_alembic_history():
    runner = CliRunner()
    fake = MagicMock(returncode=0, stdout=b"a -> b (head)\n", stderr=b"")
    with patch("subprocess.run", return_value=fake) as m:
        result = runner.invoke(quilt, ["db", "revisions"])
    assert result.exit_code == 0
    args = m.call_args.args[0]
    assert "history" in args
