import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from sdk.cli.main import quilt

@pytest.fixture
def algo_dir(tmp_path):
    (tmp_path / "quilt.yaml").write_text("name: test-algo\ntype: algorithm\nversion: 1.0.0\nentry_point: algo.py\nclass_name: TestAlgo\nrequirements:\n  asset_types:\n    - equities\n")
    (tmp_path / "algo.py").write_text("from sdk.algorithm import QuiltAlgorithm\n\nclass TestAlgo(QuiltAlgorithm):\n    def on_start(self, config, restored_state): pass\n    def on_tick(self, ctx): return []\n    def on_stop(self): return {}\n    def save_state(self): return {}\n")
    (tmp_path / "quilt.config.yaml").write_text("data_mode: standalone\n")
    return tmp_path

class TestBacktestCommand:
    def test_backtest_requires_dates(self, algo_dir):
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "backtest", "--path", str(algo_dir)])
        assert result.exit_code != 0
        assert "start" in result.output.lower() or "required" in result.output.lower()

    def test_backtest_validates_first(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "backtest", "--path", str(tmp_path), "--start", "2025-01-01", "--end", "2025-06-01"])
        assert result.exit_code != 0
        assert "quilt.yaml" in result.output.lower()

    @patch("sdk.cli.backtest.run_lumibot_backtest")
    def test_backtest_runs_with_valid_algo(self, mock_run, algo_dir):
        mock_run.return_value = {"total_return": 0.15, "sharpe_ratio": 1.8, "max_drawdown": -0.08, "total_trades": 42}
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "backtest", "--path", str(algo_dir), "--start", "2025-01-01", "--end", "2025-06-01"], catch_exceptions=False)
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["start_date"] == "2025-01-01"
        assert call_kwargs[1]["end_date"] == "2025-06-01"

    @patch("sdk.cli.backtest.run_lumibot_backtest")
    def test_backtest_with_initial_cash(self, mock_run, algo_dir):
        mock_run.return_value = {"total_return": 0.10, "sharpe_ratio": 1.2, "max_drawdown": -0.05, "total_trades": 20}
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "backtest", "--path", str(algo_dir), "--start", "2025-01-01", "--end", "2025-06-01", "--cash", "50000"], catch_exceptions=False)
        assert result.exit_code == 0
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["initial_cash"] == 50000.0

    @patch("sdk.cli.backtest.run_lumibot_backtest")
    def test_backtest_output_shows_results(self, mock_run, algo_dir):
        mock_run.return_value = {"total_return": 0.15, "sharpe_ratio": 1.8, "max_drawdown": -0.08, "total_trades": 42}
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "backtest", "--path", str(algo_dir), "--start", "2025-01-01", "--end", "2025-06-01"], catch_exceptions=False)
        assert "15.0" in result.output or "0.15" in result.output
        assert "1.8" in result.output
