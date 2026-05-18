import asyncio
import pytest
from unittest.mock import MagicMock

from coordinator.services.live_feed_aggregator import LiveFeedAggregator


@pytest.mark.asyncio
async def test_aggregator_passes_asset_class_to_adapter(tmp_path, monkeypatch):
    """When start_subscription is called with asset_class='crypto', the
    broker adapter's start_market_data_stream receives asset_class='crypto'."""
    captured = {}

    class FakeHandle:
        def close(self): pass

    class FakeAdapter:
        def start_market_data_stream(self, symbols, on_trade, on_quote, asset_class="equities"):
            captured["symbols"] = symbols
            captured["asset_class"] = asset_class
            return FakeHandle()
        def close(self): pass

    from coordinator.services import live_feed_aggregator as mod

    async def fake_adapter_for_broker(broker):
        return FakeAdapter()

    agg = LiveFeedAggregator(
        session_factory=None,
        encryption=None,
        flush_interval_s=60.0,  # long flush so task parks in sleep, not DB writes
    )
    agg._loop = asyncio.get_running_loop()
    monkeypatch.setattr(agg, "_adapter_for_broker", fake_adapter_for_broker)
    # Path setup: writes go under tmp_path.
    monkeypatch.setattr(agg, "_ticks_dir",
                        lambda b, s: tmp_path / b / s / "ticks")

    await agg.start_subscription("alpaca", "BTCUSD", "crypto")

    # Yield control so the _run task can execute up through start_market_data_stream.
    await asyncio.sleep(0.05)

    # Cancel the background task to clean up.
    await agg.stop_subscription("alpaca", "BTCUSD")

    assert captured["symbols"] == ["BTCUSD"]
    assert captured["asset_class"] == "crypto"
