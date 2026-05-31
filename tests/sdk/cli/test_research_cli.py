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
    assert "--algorithm-id" in result.output
    assert "--base-config" in result.output


def test_session_create_passes_algorithm_id_and_base_config():
    from unittest.mock import AsyncMock, patch, MagicMock
    import asyncio

    runner = CliRunner()
    # Mock the _client function to return a mock client
    mock_response = {
        "id": 42,
        "name": "test_session",
        "hypothesis": "test hypothesis",
        "algorithm_id": "algo-x",
        "base_config": {"k": "v"},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 1.0},
        "status": "open",
        "notes": "",
        "created_at": "2026-05-28",
        "completed_at": None,
        "n_runs": 0,
    }

    with patch("sdk.cli.commands.research._client") as mock_client_factory:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_factory.return_value = mock_client

        result = runner.invoke(
            research_group,
            [
                "session",
                "create",
                "--name",
                "test_session",
                "--hypothesis",
                "test hypothesis",
                "--algorithm-id",
                "algo-x",
                "--base-config",
                '{"k": "v"}',
                "--parameter-space",
                '{"x": [1]}',
                "--criteria",
                '{"min_sharpe": 1.0}',
            ],
        )
        assert result.exit_code == 0, result.output
        # Check that the client.post was called with the right payload
        call_args = mock_client.post.call_args
        assert call_args is not None
        payload = call_args[1]["json"]
        assert payload["algorithm_id"] == "algo-x"
        assert payload["base_config"] == {"k": "v"}
