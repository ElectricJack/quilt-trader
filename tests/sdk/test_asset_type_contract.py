"""Contract test: SDK's _VALID_ASSET_TYPES must equal the registry's enum values.

If the registry ever gains a new asset type, this test fails and the SDK
must be updated (separate package, separate deployment cadence).
"""
from coordinator.services.asset_services import AssetType
from sdk.signals import _VALID_ASSET_TYPES as SIGNALS_VALID
from sdk.models import _VALID_ASSET_TYPES as MODELS_VALID


def test_signals_matches_enum():
    assert SIGNALS_VALID == {t.value for t in AssetType}


def test_models_matches_enum():
    assert MODELS_VALID == {t.value for t in AssetType}


def test_signals_validates():
    from sdk.signals import SignalLeg, SignalType
    leg = SignalLeg(
        symbol="SPY", signal_type=SignalType.BUY, quantity=1, asset_type="options",
    )
    assert leg.asset_type == "options"


def test_signals_rejects_bogus():
    import pytest
    from sdk.signals import SignalLeg, SignalType
    with pytest.raises(ValueError, match="asset_type"):
        SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=1, asset_type="bogus")


def test_position_validates():
    from sdk.models import Position
    p = Position(symbol="BTCUSD", quantity=1.0, avg_cost=50000.0, current_price=51000.0,
                 asset_type="crypto")
    assert p.asset_type == "crypto"


def test_position_rejects_bogus():
    import pytest
    from sdk.models import Position
    with pytest.raises(ValueError, match="asset_type"):
        Position(symbol="X", quantity=1, avg_cost=1, current_price=1, asset_type="bogus")
