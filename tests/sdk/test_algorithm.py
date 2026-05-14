import pytest
from sdk.algorithm import QuiltAlgorithm
from sdk.signals import Signal, SignalType


class DummyAlgorithm(QuiltAlgorithm):
    def on_start(self, config, restored_state):
        self.config = config
        self.restored_state = restored_state
        self.started = True

    def on_tick(self, ctx):
        return [Signal.simple("AAPL", SignalType.BUY, 100)]

    def on_stop(self):
        return {"final": True}

    def save_state(self):
        return {"checkpoint": True}


class IncompleteAlgorithm(QuiltAlgorithm):
    pass


class TestQuiltAlgorithm:
    def test_subclass_implements_required_methods(self):
        algo = DummyAlgorithm()
        algo.on_start({"risk": 0.02}, None)
        assert algo.started is True
        assert algo.config == {"risk": 0.02}
        assert algo.restored_state is None

    def test_on_tick_returns_signals(self):
        algo = DummyAlgorithm()
        algo.on_start({}, None)
        signals = algo.on_tick(None)
        assert len(signals) == 1
        assert signals[0].legs[0].symbol == "AAPL"

    def test_on_stop_returns_state(self):
        algo = DummyAlgorithm()
        state = algo.on_stop()
        assert state == {"final": True}

    def test_save_state_returns_state(self):
        algo = DummyAlgorithm()
        state = algo.save_state()
        assert state == {"checkpoint": True}

    def test_incomplete_raises_on_required_methods(self):
        algo = IncompleteAlgorithm()
        with pytest.raises(NotImplementedError):
            algo.on_start({}, None)
        with pytest.raises(NotImplementedError):
            algo.on_tick(None)
        with pytest.raises(NotImplementedError):
            algo.on_stop()
        with pytest.raises(NotImplementedError):
            algo.save_state()

    def test_on_signal_rejected_default_noop(self):
        algo = DummyAlgorithm()
        signal = Signal.simple("AAPL", SignalType.BUY, 100)
        algo.on_signal_rejected(signal, "PDT limit reached")

    def test_on_trade_executed_default_noop(self):
        from sdk.models import TradeFill
        from datetime import datetime
        algo = DummyAlgorithm()
        signal = Signal.simple("AAPL", SignalType.BUY, 100)
        fill = TradeFill(
            symbol="AAPL", side="buy", quantity=100,
            filled_price=150.25, fees=1.00, slippage=0.05,
            timestamp=datetime(2026, 5, 12, 10, 30, 0),
        )
        algo.on_trade_executed(signal, fill)

    def test_notify_stores_event(self):
        algo = DummyAlgorithm()
        algo.notify("unusual_volume", "AAPL volume 3x average", {"symbol": "AAPL"})
        assert len(algo._pending_notifications) == 1
        event = algo._pending_notifications[0]
        assert event["event_name"] == "unusual_volume"
        assert event["message"] == "AAPL volume 3x average"
        assert event["data"] == {"symbol": "AAPL"}

    def test_drain_notifications(self):
        algo = DummyAlgorithm()
        algo.notify("event1", "msg1")
        algo.notify("event2", "msg2")
        events = algo.drain_notifications()
        assert len(events) == 2
        assert len(algo._pending_notifications) == 0
