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


def test_load_profile_from_yaml(tmp_path):
    yaml_text = """
name: alpaca_crypto
fallback:
  fees:
    - flat_fee: 0.0
      percent_fee: 0.0025
  slippage:
    market_bps: 15.0
    use_bar_range: true
bundles:
  alpaca:crypto:
    fees:
      - flat_fee: 0.0
        percent_fee: 0.0015
    slippage:
      market_bps: 10.0
      use_bar_range: true
"""
    path = tmp_path / "alpaca_crypto.yaml"
    path.write_text(yaml_text)

    profile = CostModelProfile.from_yaml(path)
    assert profile.name == "alpaca_crypto"
    bundle = profile.resolve(venue="alpaca", asset_type="crypto", symbol="BTC/USD")
    assert bundle.fees[0].percent_fee == 0.0015
    assert bundle.slippage.market_bps == 10.0


def test_load_profile_falls_back_on_unknown_symbol(tmp_path):
    yaml_text = """
name: alpaca_crypto
fallback:
  fees:
    - flat_fee: 0.0
      percent_fee: 0.0025
  slippage:
    market_bps: 15.0
"""
    path = tmp_path / "alpaca_crypto.yaml"
    path.write_text(yaml_text)
    profile = CostModelProfile.from_yaml(path)
    bundle = profile.resolve(venue="alpaca", asset_type="equity", symbol="AAPL")
    assert bundle.fees[0].percent_fee == 0.0025
    assert bundle.slippage.market_bps == 15.0
