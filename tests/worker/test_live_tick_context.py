import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone


def test_market_data_reads_from_buffer_when_available():
    from worker.context import LiveTickContext
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL", "timeframe": "1min", "history_bars": 10}])
    buf.ingest({"AAPL": {"timeframe": "1min", "bars": [{"close": 100.0}, {"close": 101.0}]}})
    broker = MagicMock()
    data_client = AsyncMock()
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client, buffer=buf,
    )
    df = ctx.market_data("AAPL", "1min", 5)
    assert len(df) == 2
    data_client.get_market_data.assert_not_called()


def test_market_data_returns_none_when_symbol_not_in_buffer():
    """When the buffer doesn't have the symbol, return None (sync handler
    can't await the HTTP fallback)."""
    from worker.context import LiveTickContext
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL", "timeframe": "1min"}])
    broker = MagicMock()
    data_client = AsyncMock()
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client, buffer=buf,
    )
    result = ctx.market_data("MSFT", "1min", 5)
    assert result is None


def test_market_data_returns_none_when_buffer_is_none():
    """No buffer at all → returns None."""
    from worker.context import LiveTickContext
    broker = MagicMock()
    data_client = AsyncMock()
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client,
    )
    result = ctx.market_data("AAPL", "1min", 5)
    assert result is None
