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
