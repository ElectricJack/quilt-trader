from sdk.algorithm import QuiltAlgorithm


def test_on_position_closed_default_is_noop():
    class MyAlgo(QuiltAlgorithm):
        def on_start(self, config, restored_state): pass
        def on_tick(self, ctx): return []
        def on_stop(self): return {}
        def save_state(self): return {}
    algo = MyAlgo()
    algo.on_position_closed("SPY", "manual_close", {"position_id": "pos-1"})


def test_on_position_closed_can_be_overridden():
    received = {}
    class MyAlgo(QuiltAlgorithm):
        def on_start(self, config, restored_state): pass
        def on_tick(self, ctx): return []
        def on_stop(self): return {}
        def save_state(self): return {}
        def on_position_closed(self, symbol, reason, details):
            received["symbol"] = symbol
            received["reason"] = reason
    algo = MyAlgo()
    algo.on_position_closed("SPY", "manual_close", {})
    assert received["symbol"] == "SPY"
