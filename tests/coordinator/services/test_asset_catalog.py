import pytest
from coordinator.services.asset_catalog import (
    BROKER_ASSET_TYPES, asset_types_for_broker,
)

def test_alpaca_supports_equities_options_crypto():
    assert asset_types_for_broker("alpaca") == ["equities", "options", "crypto"]

def test_tradier_supports_equities_options():
    assert asset_types_for_broker("tradier") == ["equities", "options"]

def test_unknown_broker_raises():
    with pytest.raises(ValueError, match="Unknown broker"):
        asset_types_for_broker("ibkr")

def test_returns_a_copy_not_mutable_reference():
    out = asset_types_for_broker("alpaca")
    out.append("forex")
    assert "forex" not in BROKER_ASSET_TYPES["alpaca"]
