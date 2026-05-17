import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_market_data_reads_from_buffer_when_available():
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
    df = await ctx.market_data("AAPL", "1min", 5)
    assert len(df) == 2
    data_client.get_market_data.assert_not_called()


@pytest.mark.asyncio
async def test_market_data_falls_back_to_http_when_symbol_not_in_buffer():
    from worker.context import LiveTickContext
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL", "timeframe": "1min"}])
    broker = MagicMock()
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=pd.DataFrame([{"close": 999.0}]))
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client, buffer=buf,
    )
    df = await ctx.market_data("MSFT", "1min", 5)
    assert len(df) == 1
    data_client.get_market_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_market_data_falls_back_to_http_when_buffer_is_none():
    """Backwards compat: existing callers may not pass a buffer."""
    from worker.context import LiveTickContext
    broker = MagicMock()
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=pd.DataFrame([{"x": 1}]))
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client,
    )
    await ctx.market_data("AAPL", "1min", 5)
    data_client.get_market_data.assert_awaited_once()
