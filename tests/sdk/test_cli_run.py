import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from sdk.cli.main import quilt
from sdk.cli.run import LocalPaperRunner

@pytest.fixture
def algo_dir(tmp_path):
    (tmp_path / "quilt.yaml").write_text("name: test-algo\ntype: algorithm\nversion: 1.0.0\nentry_point: algo.py\nclass_name: TestAlgo\nrequirements:\n  asset_types:\n    - equities\n")
    (tmp_path / "algo.py").write_text("from sdk.algorithm import QuiltAlgorithm\n\nclass TestAlgo(QuiltAlgorithm):\n    def on_start(self, config, restored_state):\n        self._ticks = 0\n    def on_tick(self, ctx):\n        self._ticks += 1\n        return []\n    def on_stop(self):\n        return {'ticks': self._ticks}\n    def save_state(self):\n        return {'ticks': self._ticks}\n")
    (tmp_path / "quilt.config.yaml").write_text("data_mode: standalone\n")
    return tmp_path

class TestLocalPaperRunner:
    def test_init_loads_algorithm(self, algo_dir):
        runner = LocalPaperRunner(algo_dir)
        assert runner.manifest.name == "test-algo"
        assert runner.algo_instance is not None

    def test_start_calls_on_start(self, algo_dir):
        runner = LocalPaperRunner(algo_dir)
        runner.start()
        assert runner.algo_instance._ticks == 0
        assert runner.running is True

    def test_tick_calls_on_tick(self, algo_dir):
        runner = LocalPaperRunner(algo_dir)
        runner.start()
        signals = runner.tick()
        assert signals == []
        assert runner.algo_instance._ticks == 1

    def test_stop_calls_on_stop(self, algo_dir):
        runner = LocalPaperRunner(algo_dir)
        runner.start()
        runner.tick()
        runner.tick()
        state = runner.stop()
        assert state == {"ticks": 2}
        assert runner.running is False

    def test_save_state(self, algo_dir):
        runner = LocalPaperRunner(algo_dir)
        runner.start()
        runner.tick()
        state = runner.save_state()
        assert state == {"ticks": 1}

class TestRunCommand:
    def test_run_validates_first(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "run", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "quilt.yaml" in result.output.lower()

    @patch("sdk.cli.run.LocalPaperRunner")
    def test_run_with_max_ticks(self, mock_runner_cls, algo_dir):
        mock_runner = MagicMock()
        mock_runner.manifest = MagicMock()
        mock_runner.manifest.name = "test-algo"
        mock_runner.manifest.version = "1.0.0"
        mock_runner.tick.return_value = []
        mock_runner.stop.return_value = {}
        mock_runner.running = True
        mock_runner_cls.return_value = mock_runner
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "run", "--path", str(algo_dir), "--max-ticks", "3"], catch_exceptions=False)
        assert result.exit_code == 0
        assert mock_runner.start.call_count == 1
        assert mock_runner.tick.call_count == 3
        assert mock_runner.stop.call_count == 1

    @patch("sdk.cli.run.LocalPaperRunner")
    def test_run_with_tick_interval(self, mock_runner_cls, algo_dir):
        mock_runner = MagicMock()
        mock_runner.manifest = MagicMock()
        mock_runner.manifest.name = "test-algo"
        mock_runner.manifest.version = "1.0.0"
        mock_runner.tick.return_value = []
        mock_runner.stop.return_value = {}
        mock_runner.running = True
        mock_runner_cls.return_value = mock_runner
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "run", "--path", str(algo_dir), "--max-ticks", "2", "--interval", "0"], catch_exceptions=False)
        assert result.exit_code == 0
        assert mock_runner.tick.call_count == 2
