from click.testing import CliRunner
from sdk.cli.main import quilt


def test_quilt_runs_without_subcommand_shows_help():
    runner = CliRunner()
    result = runner.invoke(quilt, [])
    assert "Usage:" in result.output


def test_quilt_version_subcommand_prints_version():
    runner = CliRunner()
    result = runner.invoke(quilt, ["version"])
    assert result.exit_code == 0
    out = result.output.lower()
    assert "quilt" in out or "0." in out


def test_quilt_json_flag_is_accepted():
    runner = CliRunner()
    result = runner.invoke(quilt, ["--json", "version"])
    assert result.exit_code == 0


def test_quilt_coord_flag_is_accepted():
    runner = CliRunner()
    result = runner.invoke(quilt, ["--coord", "http://x:1234", "version"])
    assert result.exit_code == 0


def test_quilt_quiet_flag_is_accepted():
    runner = CliRunner()
    result = runner.invoke(quilt, ["-q", "version"])
    assert result.exit_code == 0


def test_dev_group_no_longer_exists():
    runner = CliRunner()
    result = runner.invoke(quilt, ["dev", "validate", "--help"])
    # Click returns exit code 2 for unknown commands
    assert result.exit_code != 0


def test_quilt_validate_at_top_level_exists():
    runner = CliRunner()
    result = runner.invoke(quilt, ["validate", "--help"])
    assert result.exit_code == 0
    assert "Validate" in result.output or "validate" in result.output.lower()
