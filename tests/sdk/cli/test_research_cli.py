from click.testing import CliRunner

from sdk.cli.commands.research import research_group


def test_research_help():
    runner = CliRunner()
    result = runner.invoke(research_group, ["--help"])
    assert result.exit_code == 0
    assert "session" in result.output
    assert "sweep" in result.output
    assert "walk-forward" in result.output
    assert "report" in result.output


def test_research_session_create_help():
    runner = CliRunner()
    result = runner.invoke(research_group, ["session", "create", "--help"])
    assert result.exit_code == 0
    assert "--name" in result.output
    assert "--hypothesis" in result.output
