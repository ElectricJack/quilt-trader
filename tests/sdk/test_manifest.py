import pytest
from pathlib import Path
from sdk.manifest import QuiltManifest, ManifestError

FIXTURES = Path(__file__).parent / "fixtures"


class TestManifestLoading:
    def test_load_valid_algorithm(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_algorithm.yaml")
        assert manifest.name == "momentum-scalper"
        assert manifest.type == "algorithm"
        assert manifest.version == "1.0.0"
        assert manifest.description == "Intraday momentum scalping strategy"
        assert manifest.entry_point == "algorithm.py"
        assert manifest.class_name == "MomentumScalper"

    def test_load_valid_scraper(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_scraper.yaml")
        assert manifest.name == "alpha-picks-scraper"
        assert manifest.type == "scraper"
        assert manifest.schedule == "*/30 * * * *"

    def test_load_minimal_algorithm(self):
        manifest = QuiltManifest.from_file(FIXTURES / "minimal_algorithm.yaml")
        assert manifest.name == "simple-algo"
        assert manifest.requirements.asset_types == ["equities"]
        assert manifest.requirements.options_level is None
        assert manifest.requirements.account_features == []
        assert manifest.requirements.brokers is None
        assert manifest.requirements.data_dependencies == []

    def test_load_from_string(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 0.1.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
"""
        manifest = QuiltManifest.from_string(yaml_str)
        assert manifest.name == "test-algo"


class TestManifestRequirements:
    def test_full_requirements(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_algorithm.yaml")
        reqs = manifest.requirements
        assert reqs.asset_types == ["equities", "options"]
        assert reqs.options_level == 3
        assert reqs.account_features == ["margin", "short_selling"]
        assert reqs.brokers == ["alpaca", "tradier"]
        assert len(reqs.data_dependencies) == 1
        assert reqs.data_dependencies[0]["name"] == "alpha-picks-scraper"
        assert reqs.data_dependencies[0]["repo"] == "ElectricJack/alpha-picks-scraper"


class TestManifestConfig:
    def test_parameters(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_algorithm.yaml")
        params = manifest.config_parameters
        assert len(params) == 2
        assert params[0]["name"] == "risk_per_trade"
        assert params[0]["type"] == "float"
        assert params[0]["default"] == 0.02
        assert params[1]["name"] == "max_positions"
        assert params[1]["type"] == "int"

    def test_no_config(self):
        manifest = QuiltManifest.from_file(FIXTURES / "minimal_algorithm.yaml")
        assert manifest.config_parameters == []


class TestManifestNotifications:
    def test_custom_events(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_algorithm.yaml")
        events = manifest.custom_events
        assert len(events) == 1
        assert events[0]["name"] == "unusual_volume"
        assert events[0]["severity"] == "info"

    def test_no_notifications(self):
        manifest = QuiltManifest.from_file(FIXTURES / "minimal_algorithm.yaml")
        assert manifest.custom_events == []


class TestManifestValidation:
    def test_missing_name_raises(self):
        with pytest.raises(ManifestError, match="name"):
            QuiltManifest.from_file(FIXTURES / "invalid_missing_name.yaml")

    def test_bad_type_raises(self):
        with pytest.raises(ManifestError, match="type"):
            QuiltManifest.from_file(FIXTURES / "invalid_bad_type.yaml")

    def test_algorithm_missing_entry_point_raises(self):
        yaml_str = """
name: test
type: algorithm
version: 1.0.0
requirements:
  asset_types: [equities]
"""
        with pytest.raises(ManifestError, match="entry_point"):
            QuiltManifest.from_string(yaml_str)

    def test_algorithm_missing_class_name_raises(self):
        yaml_str = """
name: test
type: algorithm
version: 1.0.0
entry_point: algo.py
requirements:
  asset_types: [equities]
"""
        with pytest.raises(ManifestError, match="class_name"):
            QuiltManifest.from_string(yaml_str)

    def test_algorithm_missing_asset_types_raises(self):
        yaml_str = """
name: test
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: Test
"""
        with pytest.raises(ManifestError, match="asset_types"):
            QuiltManifest.from_string(yaml_str)

    def test_scraper_missing_schedule_raises(self):
        yaml_str = """
name: test
type: scraper
version: 1.0.0
"""
        with pytest.raises(ManifestError, match="schedule"):
            QuiltManifest.from_string(yaml_str)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            QuiltManifest.from_file(Path("/nonexistent/quilt.yaml"))
