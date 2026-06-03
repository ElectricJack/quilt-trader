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


def _base_yaml(extra: str = "") -> str:
    return f"""
name: test-algo
type: algorithm
version: 1.0.0
entry_point: my_algo.algorithm
class_name: MyAlgo
requirements:
  asset_types: [equities]
  brokers: [alpaca]
  data_dependencies:
    - {{ symbol: "AAPL", timeframe: "1min" }}
{extra}
"""


def test_trigger_defaults_to_bar_1min_when_omitted():
    m = QuiltManifest.from_string(_base_yaml())
    assert m.trigger == "bar:1min"


def test_trigger_accepts_bar_timeframe():
    m = QuiltManifest.from_string(_base_yaml("trigger: bar:5min"))
    assert m.trigger == "bar:5min"


def test_trigger_accepts_event():
    m = QuiltManifest.from_string(_base_yaml("trigger: event"))
    assert m.trigger == "event"


def test_trigger_accepts_interval_with_duration_suffix():
    m = QuiltManifest.from_string(_base_yaml("trigger: interval:30s"))
    assert m.trigger == "interval:30s"
    m2 = QuiltManifest.from_string(_base_yaml("trigger: interval:5m"))
    assert m2.trigger == "interval:5m"


def test_trigger_rejects_unknown_format():
    with pytest.raises(ManifestError):
        QuiltManifest.from_string(_base_yaml("trigger: not_a_trigger"))


def test_trigger_rejects_invalid_interval():
    with pytest.raises(ManifestError):
        QuiltManifest.from_string(_base_yaml("trigger: interval:abc"))


def test_data_dependencies_history_bars_default():
    m = QuiltManifest.from_string(_base_yaml())
    deps = m.requirements.data_dependencies
    assert deps[0].get("history_bars", 200) == 200


def test_data_dependencies_history_bars_explicit():
    yaml_text = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: my_algo.algorithm
class_name: MyAlgo
requirements:
  asset_types: [equities]
  brokers: [alpaca]
  data_dependencies:
    - { symbol: "AAPL", timeframe: "1min", history_bars: 500 }
"""
    m = QuiltManifest.from_string(yaml_text)
    assert m.requirements.data_dependencies[0]["history_bars"] == 500


def test_history_bars_rejects_non_positive():
    yaml_text = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: my_algo.algorithm
class_name: MyAlgo
requirements:
  asset_types: [equities]
  brokers: [alpaca]
  data_dependencies:
    - { symbol: "AAPL", timeframe: "1min", history_bars: -5 }
"""
    with pytest.raises(ManifestError):
        QuiltManifest.from_string(yaml_text)


def test_manifest_assets_without_broker_field():
    """Asset entries no longer need `broker:` — only symbol + asset_class."""
    from sdk.manifest import QuiltManifest
    yaml_text = """
name: test
type: algorithm
version: 1.0.0
entry_point: a.py
class_name: A
requirements:
  asset_types: [equities, crypto]
assets:
  - symbol: SPY
    asset_class: equities
  - symbol: BTCUSD
    asset_class: crypto
"""
    m = QuiltManifest.from_string(yaml_text)
    assert len(m.assets) == 2
    assert m.assets[0] == {"symbol": "SPY", "asset_class": "equities"}
    assert m.assets[1] == {"symbol": "BTCUSD", "asset_class": "crypto"}


def test_manifest_assets_strips_broker_field_for_backwards_compat():
    """If an old manifest still has `broker:` in an asset entry, it's parsed
    but the broker field is stripped (the deployment's account decides routing)."""
    from sdk.manifest import QuiltManifest
    yaml_text = """
name: test
type: algorithm
version: 1.0.0
entry_point: a.py
class_name: A
requirements:
  asset_types: [equities]
assets:
  - broker: alpaca
    symbol: SPY
    asset_class: equities
"""
    m = QuiltManifest.from_string(yaml_text)
    assert m.assets == [{"symbol": "SPY", "asset_class": "equities"}]


class TestManifestDataBlock:
    def test_data_block_parsed(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
data:
  - source: alpha-picks-scraper
    type: scraper
  - source: sector-weights.csv
    type: csv
"""
        m = QuiltManifest.from_string(yaml_str)
        assert len(m.data) == 2
        assert m.data[0] == {"source": "alpha-picks-scraper", "type": "scraper"}
        assert m.data[1] == {"source": "sector-weights.csv", "type": "csv"}

    def test_data_block_defaults_to_empty_list(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
"""
        m = QuiltManifest.from_string(yaml_str)
        assert m.data == []

    def test_data_block_ignores_entries_without_source(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
data:
  - type: csv
  - source: my-data
    type: json
"""
        m = QuiltManifest.from_string(yaml_str)
        assert len(m.data) == 1
        assert m.data[0]["source"] == "my-data"

    def test_data_block_validates_type_field(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
data:
  - source: my-data
    type: invalid_type
"""
        with pytest.raises(ManifestError, match="type"):
            QuiltManifest.from_string(yaml_str)


def _make_manifest(assets_block):
    """Build a minimal valid algorithm manifest with the given assets block."""
    return {
        "name": "test-algo",
        "type": "algorithm",
        "entry_point": "algorithm.py",
        "class_name": "TestAlgo",
        "requirements": {"asset_types": ["equities"]},
        "assets": assets_block,
    }


class TestManifestCanonicalValidation:
    def test_accepts_canonical_crypto(self):
        m = _make_manifest([{"symbol": "BTCUSD", "asset_class": "crypto"}])
        QuiltManifest._parse(m)  # no raise

    def test_accepts_canonical_equity(self):
        m = _make_manifest([{"symbol": "AAPL", "asset_class": "equities"}])
        QuiltManifest._parse(m)

    def test_accepts_share_class_equity(self):
        m = _make_manifest([{"symbol": "BRK.B", "asset_class": "equities"}])
        QuiltManifest._parse(m)

    def test_accepts_canonical_index(self):
        m = _make_manifest([{"symbol": "VIX", "asset_class": "index"}])
        QuiltManifest._parse(m)

    def test_rejects_bare_crypto_with_crypto_class(self):
        """Gate 1: 'BTC' fails validate() with multi-class hint."""
        m = _make_manifest([{"symbol": "BTC", "asset_class": "crypto"}])
        with pytest.raises(ManifestError) as exc:
            QuiltManifest._parse(m)
        msg = str(exc.value)
        assert "'BTC'" in msg
        assert "BTCUSD" in msg

    def test_rejects_dashed_crypto(self):
        m = _make_manifest([{"symbol": "BTC-USD", "asset_class": "crypto"}])
        with pytest.raises(ManifestError, match="BTC-USD"):
            QuiltManifest._parse(m)

    def test_rejects_yfinance_index_prefix(self):
        m = _make_manifest([{"symbol": "^VIX", "asset_class": "index"}])
        with pytest.raises(ManifestError, match="\\^VIX"):
            QuiltManifest._parse(m)

    def test_rejects_yfinance_index_alias(self):
        """GSPC was previously valid (yfinance alias); now must be SPX."""
        m = _make_manifest([{"symbol": "GSPC", "asset_class": "index"}])
        with pytest.raises(ManifestError, match="GSPC"):
            QuiltManifest._parse(m)

    def test_rejects_class_mismatch(self):
        """Gate 2: symbol classifies differently than declared."""
        m = _make_manifest([{"symbol": "BTCUSD", "asset_class": "equities"}])
        with pytest.raises(ManifestError) as exc:
            QuiltManifest._parse(m)
        msg = str(exc.value)
        assert "BTCUSD" in msg
        assert "equities" in msg
        assert "crypto" in msg

    def test_rejects_class_mismatch_aapl_as_crypto(self):
        m = _make_manifest([{"symbol": "AAPL", "asset_class": "crypto"}])
        with pytest.raises(ManifestError, match="AAPL"):
            QuiltManifest._parse(m)
