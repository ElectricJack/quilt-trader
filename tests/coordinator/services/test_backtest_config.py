import pytest
from pydantic import ValidationError
from coordinator.services.backtest_config import TradingFee, SlippageModel


def test_trading_fee_defaults():
    tf = TradingFee()
    assert tf.flat_fee == 0.0
    assert tf.percent_fee == 0.0
    assert tf.maker is True
    assert tf.taker is True


def test_trading_fee_negative_rejected():
    with pytest.raises(ValidationError):
        TradingFee(flat_fee=-1.0)
    with pytest.raises(ValidationError):
        TradingFee(percent_fee=-0.001)


def test_slippage_model_defaults_are_conservative():
    sm = SlippageModel()
    assert sm.market_bps == 5.0  # Conservative default
    assert sm.limit_bps == 0.0
    assert sm.use_bar_range is False
    assert sm.volume_impact_bps_per_pct == 0.0


def test_slippage_model_validation():
    with pytest.raises(ValidationError):
        SlippageModel(market_bps=-1.0)
    with pytest.raises(ValidationError):
        SlippageModel(volume_impact_bps_per_pct=-5.0)
