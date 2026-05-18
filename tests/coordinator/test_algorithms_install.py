import pytest


def test_validate_assets_rejects_entry_without_symbol():
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="missing 'symbol'"):
        _validate_assets([{"asset_class": "equities"}])


def test_validate_assets_rejects_entry_with_unknown_asset_class():
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="invalid asset_class"):
        _validate_assets([{"symbol": "SPY", "asset_class": "forex"}])


def test_validate_assets_defaults_missing_asset_class_to_equities():
    from coordinator.api.routes.algorithms import _validate_assets
    out = _validate_assets([{"symbol": "SPY"}])
    assert out == [{"symbol": "SPY", "asset_class": "equities"}]


def test_validate_assets_passes_well_formed_entries():
    from coordinator.api.routes.algorithms import _validate_assets
    out = _validate_assets([
        {"symbol": "SPY", "asset_class": "equities"},
        {"symbol": "BTCUSD", "asset_class": "crypto"},
    ])
    assert out == [
        {"symbol": "SPY", "asset_class": "equities"},
        {"symbol": "BTCUSD", "asset_class": "crypto"},
    ]
