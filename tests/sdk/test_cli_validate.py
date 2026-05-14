import pytest
from pathlib import Path
from click.testing import CliRunner
from sdk.cli.main import quilt

@pytest.fixture
def valid_algo_dir(tmp_path):
    manifest = tmp_path / "quilt.yaml"
    manifest.write_text("name: test-algo\ntype: algorithm\nversion: 1.0.0\nentry_point: algo.py\nclass_name: TestAlgo\nrequirements:\n  asset_types:\n    - equities\n")
    algo_file = tmp_path / "algo.py"
    algo_file.write_text("from sdk.algorithm import QuiltAlgorithm\n\nclass TestAlgo(QuiltAlgorithm):\n    def on_start(self, config, restored_state):\n        pass\n    def on_tick(self, ctx):\n        return []\n    def on_stop(self):\n        return {}\n    def save_state(self):\n        return {}\n")
    return tmp_path

@pytest.fixture
def valid_scraper_dir(tmp_path):
    manifest = tmp_path / "quilt.yaml"
    manifest.write_text("name: test-scraper\ntype: scraper\nversion: 1.0.0\nschedule: '*/30 * * * *'\n")
    return tmp_path

class TestValidateCommand:
    def test_validate_valid_algorithm(self, valid_algo_dir):
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "validate", "--path", str(valid_algo_dir)], catch_exceptions=False)
        assert result.exit_code == 0
        assert "valid" in result.output.lower() or "pass" in result.output.lower()

    def test_validate_missing_manifest(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "validate", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "quilt.yaml" in result.output.lower()

    def test_validate_invalid_manifest(self, tmp_path):
        (tmp_path / "quilt.yaml").write_text("type: algorithm\nversion: 1.0.0\n")
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "validate", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "name" in result.output.lower()

    def test_validate_missing_entry_point_file(self, tmp_path):
        (tmp_path / "quilt.yaml").write_text("name: test-algo\ntype: algorithm\nversion: 1.0.0\nentry_point: missing_algo.py\nclass_name: MissingAlgo\nrequirements:\n  asset_types:\n    - equities\n")
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "validate", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "missing_algo.py" in result.output

    def test_validate_class_not_found(self, tmp_path):
        (tmp_path / "quilt.yaml").write_text("name: test-algo\ntype: algorithm\nversion: 1.0.0\nentry_point: algo.py\nclass_name: WrongClassName\nrequirements:\n  asset_types:\n    - equities\n")
        (tmp_path / "algo.py").write_text("from sdk.algorithm import QuiltAlgorithm\n\nclass ActualAlgo(QuiltAlgorithm):\n    def on_start(self, config, restored_state): pass\n    def on_tick(self, ctx): return []\n    def on_stop(self): return {}\n    def save_state(self): return {}\n")
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "validate", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "WrongClassName" in result.output

    def test_validate_class_not_subclass(self, tmp_path):
        (tmp_path / "quilt.yaml").write_text("name: test-algo\ntype: algorithm\nversion: 1.0.0\nentry_point: algo.py\nclass_name: NotAnAlgo\nrequirements:\n  asset_types:\n    - equities\n")
        (tmp_path / "algo.py").write_text("class NotAnAlgo:\n    pass\n")
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "validate", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "QuiltAlgorithm" in result.output

    def test_validate_scraper_skips_class_check(self, valid_scraper_dir):
        runner = CliRunner()
        result = runner.invoke(quilt, ["dev", "validate", "--path", str(valid_scraper_dir)], catch_exceptions=False)
        assert result.exit_code == 0
