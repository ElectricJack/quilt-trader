"""Verify on_disconnect callbacks fire correctly on the broker stream handles."""
from worker.broker_adapter import MarketDataStreamHandle


def test_default_set_on_disconnect_stores_callback():
    h = MarketDataStreamHandle()
    fired = []
    h.set_on_disconnect(lambda handle: fired.append(handle))
    h._fire_on_disconnect()
    assert fired == [h]


def test_fire_on_disconnect_is_safe_without_callback():
    h = MarketDataStreamHandle()
    h._fire_on_disconnect()  # should not raise


def test_fire_on_disconnect_swallows_callback_exceptions():
    h = MarketDataStreamHandle()
    def boom(handle):
        raise RuntimeError("test")
    h.set_on_disconnect(boom)
    # Must not raise out of _fire_on_disconnect
    h._fire_on_disconnect()


def test_alpaca_handle_inherits_on_disconnect():
    """The _AlpacaStreamHandle should call super().__init__() so the callback
    storage works. We don't construct one (it would start a thread), but verify
    the class has the inherited contract."""
    from worker.alpaca_adapter import _AlpacaStreamHandle
    assert hasattr(_AlpacaStreamHandle, 'set_on_disconnect')
    assert hasattr(_AlpacaStreamHandle, '_fire_on_disconnect')


def test_tradier_handle_inherits_on_disconnect():
    from worker.tradier_adapter import _TradierStreamHandle
    assert hasattr(_TradierStreamHandle, 'set_on_disconnect')
    assert hasattr(_TradierStreamHandle, '_fire_on_disconnect')


# ---- add_symbols / remove_symbols ----

def test_default_add_symbols_raises_not_implemented():
    """The base class default makes the contract explicit: adapters that
    don't override get a clear error rather than silent no-op."""
    import pytest
    from worker.broker_adapter import MarketDataStreamHandle
    h = MarketDataStreamHandle()
    with pytest.raises(NotImplementedError, match="does not support add_symbols"):
        h.add_symbols(["AAPL"])
    with pytest.raises(NotImplementedError, match="does not support remove_symbols"):
        h.remove_symbols(["AAPL"])


def test_tradier_handle_has_add_remove_symbols():
    from worker.tradier_adapter import _TradierStreamHandle
    # We don't instantiate (would start a thread + HTTP); just verify the API.
    assert hasattr(_TradierStreamHandle, "add_symbols")
    assert hasattr(_TradierStreamHandle, "remove_symbols")
    # And both are overrides, not the inherited NotImplementedError stubs.
    assert _TradierStreamHandle.add_symbols is not __import__("worker.broker_adapter").broker_adapter.MarketDataStreamHandle.add_symbols
    assert _TradierStreamHandle.remove_symbols is not __import__("worker.broker_adapter").broker_adapter.MarketDataStreamHandle.remove_symbols


def test_alpaca_handle_has_add_remove_symbols():
    from worker.alpaca_adapter import _AlpacaStreamHandle
    assert hasattr(_AlpacaStreamHandle, "add_symbols")
    assert hasattr(_AlpacaStreamHandle, "remove_symbols")


def test_tradier_handle_no_op_when_symbols_already_subscribed(monkeypatch):
    """Adding a symbol that's already in the list should not trigger a
    reconnect (no force_reconnect call)."""
    from worker.tradier_adapter import _TradierStreamHandle
    # Build a partially-initialized instance to avoid the thread/HTTP path
    h = _TradierStreamHandle.__new__(_TradierStreamHandle)
    h._symbols = ["AAPL", "MSFT"]
    h._response = None
    reconnect_calls = []
    monkeypatch.setattr(h, "_force_reconnect", lambda: reconnect_calls.append(1))
    # All symbols already subscribed
    h.add_symbols(["AAPL", "MSFT"])
    assert reconnect_calls == []
    # New symbol triggers reconnect
    h.add_symbols(["TSLA"])
    assert reconnect_calls == [1]
    assert "TSLA" in h._symbols
