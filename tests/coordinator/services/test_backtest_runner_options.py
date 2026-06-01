"""Test that asset_class: options flows through the manifest parser correctly."""
from sdk.manifest import QuiltManifest


def test_manifest_parses_options_asset_class():
    manifest = QuiltManifest.from_string("""
name: test-options-algo
type: algorithm
version: 1.0.0
entry_point: algorithm.py
class_name: TestAlgo
trigger: bar:1day
requirements:
  asset_types:
    - options
    - equities
  options_level: 1
assets:
  - symbol: SPY240119C00450000
    asset_class: options
    timeframe: 1day
""")
    assert len(manifest.assets) == 1
    assert manifest.assets[0]["asset_class"] == "options"
    assert manifest.assets[0]["symbol"] == "SPY240119C00450000"
    assert manifest.requirements.options_level == 1
    assert "options" in manifest.requirements.asset_types
