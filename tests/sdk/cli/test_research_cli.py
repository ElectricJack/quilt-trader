from click.testing import CliRunner
import pytest

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
                "--start", "2023-01-01",
                "--end", "2024-12-31",
            ],
        )
        assert result.exit_code == 0, result.output
        # Check that the client.post was called with the right payload
        call_args = mock_client.post.call_args
        assert call_args is not None
        payload = call_args[1]["json"]
        assert payload["algorithm_id"] == "algo-x"
        assert payload["base_config"] == {"k": "v"}


def test_session_create_passes_date_range_and_cash():
    from unittest.mock import AsyncMock, patch

    runner = CliRunner()
    mock_response = {
        "id": 42, "name": "t", "hypothesis": "h",
        "algorithm_id": "algo-x", "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 1.0},
        "status": "open", "notes": "",
        "created_at": "2026-05-31", "completed_at": None, "n_runs": 0,
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "initial_cash": 25000.0,
        "cost_profile": "default",
        "benchmark_symbol": None,
        "benchmark_source": None,
    }
    with patch("sdk.cli.commands.research._client") as mock_client_factory:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_factory.return_value = mock_client
        result = runner.invoke(research_group, [
            "session", "create",
            "--name", "t",
            "--hypothesis", "h",
            "--algorithm-id", "algo-x",
            "--base-config", "{}",
            "--parameter-space", '{"x":[1]}',
            "--criteria", '{"min_sharpe":1.0}',
            "--start", "2023-01-01",
            "--end", "2024-12-31",
            "--initial-cash", "25000",
        ])
        assert result.exit_code == 0, result.output
        payload = mock_client.post.call_args[1]["json"]
        assert payload["date_range_start"] == "2023-01-01"
        assert payload["date_range_end"] == "2024-12-31"
        assert payload["initial_cash"] == 25000.0


def test_session_create_rejects_unpaired_benchmark():
    runner = CliRunner()
    result = runner.invoke(research_group, [
        "session", "create",
        "--name", "t",
        "--hypothesis", "h",
        "--algorithm-id", "algo-x",
        "--base-config", "{}",
        "--parameter-space", '{"x":[1]}',
        "--criteria", '{"min_sharpe":1.0}',
        "--start", "2023-01-01",
        "--end", "2024-12-31",
        "--benchmark-symbol", "SPY",
        # --benchmark-source omitted
    ])
    assert result.exit_code != 0
    assert "benchmark" in result.output.lower()


def test_session_create_rejects_missing_start():
    """`session create` without --start must fail with a clear missing-option error."""
    runner = CliRunner()
    result = runner.invoke(research_group, [
        "session", "create",
        "--name", "t",
        "--hypothesis", "h",
        "--algorithm-id", "algo-x",
        "--base-config", "{}",
        "--parameter-space", '{"x":[1]}',
        "--criteria", '{"min_sharpe":1.0}',
        "--end", "2024-12-31",
        # --start omitted
    ])
    assert result.exit_code != 0
    assert "--start" in result.output


def test_session_create_passes_mtm_realism_in_payload():
    from unittest.mock import AsyncMock, patch

    runner = CliRunner()
    mock_response = {
        "id": 99, "name": "t", "hypothesis": "h",
        "algorithm_id": "algo-x", "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 1.0},
        "status": "open", "notes": "",
        "created_at": "2026-06-04", "completed_at": None, "n_runs": 0,
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "initial_cash": 10000.0,
        "cost_profile": "default",
        "benchmark_symbol": None,
        "benchmark_source": None,
        "mtm_realism": 0.25,
    }
    with patch("sdk.cli.commands.research._client") as mock_client_factory:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_factory.return_value = mock_client
        result = runner.invoke(research_group, [
            "session", "create",
            "--name", "t",
            "--hypothesis", "h",
            "--algorithm-id", "algo-x",
            "--base-config", "{}",
            "--parameter-space", '{"x":[1]}',
            "--criteria", '{"min_sharpe":1.0}',
            "--start", "2023-01-01",
            "--end", "2024-12-31",
            "--mtm-realism", "0.25",
        ])
        assert result.exit_code == 0, result.output
        payload = mock_client.post.call_args[1]["json"]
        assert payload["mtm_realism"] == pytest.approx(0.25)


def test_session_create_defaults_mtm_realism_to_zero():
    from unittest.mock import AsyncMock, patch

    runner = CliRunner()
    mock_response = {
        "id": 100, "name": "t", "hypothesis": "h",
        "algorithm_id": "algo-x", "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 1.0},
        "status": "open", "notes": "",
        "created_at": "2026-06-04", "completed_at": None, "n_runs": 0,
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "initial_cash": 10000.0,
        "cost_profile": "default",
        "benchmark_symbol": None,
        "benchmark_source": None,
        "mtm_realism": 0.0,
    }
    with patch("sdk.cli.commands.research._client") as mock_client_factory:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_factory.return_value = mock_client
        result = runner.invoke(research_group, [
            "session", "create",
            "--name", "t",
            "--hypothesis", "h",
            "--algorithm-id", "algo-x",
            "--base-config", "{}",
            "--parameter-space", '{"x":[1]}',
            "--criteria", '{"min_sharpe":1.0}',
            "--start", "2023-01-01",
            "--end", "2024-12-31",
        ])
        assert result.exit_code == 0, result.output
        payload = mock_client.post.call_args[1]["json"]
        assert payload["mtm_realism"] == pytest.approx(0.0)
