import pytest
from sdk.signals import SignalLeg, SignalType, OrderType, TimeInForce, Signal


def test_time_in_force_default_is_day():
    leg = SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=1)
    assert leg.time_in_force == TimeInForce.DAY


def test_time_in_force_gtc():
    leg = SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=1, time_in_force=TimeInForce.GTC)
    assert leg.time_in_force == TimeInForce.GTC


def test_time_in_force_serializes_to_dict():
    leg = SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=1, time_in_force=TimeInForce.GTC)
    d = leg.to_dict()
    assert d["time_in_force"] == "GTC"


def test_time_in_force_deserializes_from_dict():
    d = {"symbol": "SPY", "signal_type": "buy", "quantity": 1, "time_in_force": "GTC"}
    leg = SignalLeg.from_dict(d)
    assert leg.time_in_force == TimeInForce.GTC


def test_time_in_force_missing_from_dict_defaults_to_day():
    d = {"symbol": "SPY", "signal_type": "buy", "quantity": 1}
    leg = SignalLeg.from_dict(d)
    assert leg.time_in_force == TimeInForce.DAY


def test_signal_simple_accepts_time_in_force():
    sig = Signal.simple(symbol="SPY", signal_type=SignalType.BUY, quantity=10,
                        order_type=OrderType.LIMIT, limit_price=450.0, time_in_force=TimeInForce.GTC)
    assert sig.legs[0].time_in_force == TimeInForce.GTC
