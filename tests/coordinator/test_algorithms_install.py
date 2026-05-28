import pytest


def test_validate_assets_rejects_entry_without_symbol():
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="missing or empty 'symbol'"):
        _validate_assets([{"asset_class": "equities", "broker": "alpaca"}])


def test_validate_assets_rejects_entry_with_unknown_asset_class():
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="invalid asset_class"):
        _validate_assets([{"symbol": "SPY", "asset_class": "forex", "broker": "alpaca"}])


def test_validate_assets_defaults_missing_asset_class_to_equities():
    from coordinator.api.routes.algorithms import _validate_assets
    out = _validate_assets([{"symbol": "SPY", "broker": "alpaca"}])
    assert out == [{"symbol": "SPY", "asset_class": "equities", "broker": "alpaca"}]


def test_validate_assets_passes_well_formed_entries():
    from coordinator.api.routes.algorithms import _validate_assets
    out = _validate_assets([
        {"symbol": "SPY", "asset_class": "equities", "broker": "alpaca"},
        {"symbol": "BTCUSD", "asset_class": "crypto", "broker": "coinbase"},
    ])
    assert out == [
        {"symbol": "SPY", "asset_class": "equities", "broker": "alpaca"},
        {"symbol": "BTCUSD", "asset_class": "crypto", "broker": "coinbase"},
    ]


def test_validate_assets_rejects_missing_broker():
    """New in 2026-05-27: broker is required, not silently dropped."""
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="missing or empty 'broker'"):
        _validate_assets([{"symbol": "SPY", "asset_class": "equities"}])


def test_validate_assets_rejects_unknown_broker():
    """yfinance is data-source-only, never a live broker; reject it here."""
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="unknown broker"):
        _validate_assets([{"symbol": "BTC-USD", "asset_class": "crypto", "broker": "yfinance"}])


def test_validate_assets_rejects_non_list():
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="expected list"):
        _validate_assets({"symbol": "SPY", "broker": "alpaca"})


def test_validate_assets_rejects_non_dict_entry():
    from coordinator.api.routes.algorithms import _validate_assets
    with pytest.raises(ValueError, match="expected dict"):
        _validate_assets(["SPY"])
