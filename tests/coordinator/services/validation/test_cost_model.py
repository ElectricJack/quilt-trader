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


from coordinator.services.validation.cost_model import load_named_profile


def test_load_named_profile_default():
    profile = load_named_profile("default")
    bundle_crypto = profile.resolve(venue="alpaca", asset_type="crypto", symbol="BTC/USD")
    assert bundle_crypto.fees[0].percent_fee == 0.0025  # taker default
    assert bundle_crypto.slippage.market_bps == 15.0

    bundle_equity = profile.resolve(venue="alpaca", asset_type="equity", symbol="SPY")
    assert bundle_equity.fees[0].flat_fee == 0.0
    assert bundle_equity.fees[0].percent_fee == 0.0
    assert bundle_equity.slippage.market_bps == 2.0

    bundle_options = profile.resolve(venue="tradier", asset_type="options", symbol="SPY230101C00400000")
    assert bundle_options.fees[0].flat_fee == 0.67  # 0.65 + 0.02 regulatory
    assert bundle_options.slippage.market_bps == 50.0


def test_load_named_profile_unknown_raises():
    with pytest.raises(FileNotFoundError):
        load_named_profile("does-not-exist")
