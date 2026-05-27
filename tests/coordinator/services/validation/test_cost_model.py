import pytest
from coordinator.services.backtest_config import SlippageModel, TradingFee
from coordinator.services.validation.cost_model import CostModelProfile, CostBundle


def test_cost_model_profile_defaults_match_legacy():
    profile = CostModelProfile.default()
    bundle = profile.resolve(venue="any", asset_type="equity", symbol="AAPL")
    assert isinstance(bundle.fees, list)
    assert len(bundle.fees) == 1
    assert isinstance(bundle.fees[0], TradingFee)
    assert isinstance(bundle.slippage, SlippageModel)
    assert bundle.fees[0].flat_fee == 0.0
    assert bundle.fees[0].percent_fee == 0.0
    assert bundle.slippage.market_bps == 5.0
