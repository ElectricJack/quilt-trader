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


def test_market_data_falls_back_to_positions_price():
    """When the buffer doesn't have the symbol, fall back to broker positions."""
    from worker.context import LiveTickContext
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL", "timeframe": "1min"}])
    broker = MagicMock()
    broker.get_positions.return_value = {
        "MSFT": {"symbol": "MSFT", "quantity": 10, "current_price": 420.0},
    }
    data_client = AsyncMock()
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client, buffer=buf,
    )
    result = ctx.market_data("MSFT", "1min", 5)
    assert isinstance(result, pd.DataFrame)
    assert float(result["close"].iloc[-1]) == 420.0


def test_market_data_returns_none_for_unknown_symbol():
    """Symbol not in buffer or positions → returns None."""
    from worker.context import LiveTickContext
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL", "timeframe": "1min"}])
    broker = MagicMock()
    broker.get_positions.return_value = {}
    data_client = AsyncMock()
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client, buffer=buf,
    )
    result = ctx.market_data("UNKNOWN_XYZ", "1min", 5)
    assert result is None


def test_market_data_returns_none_when_buffer_is_none_no_positions():
    """No buffer and no positions → returns None."""
    from worker.context import LiveTickContext
    broker = MagicMock()
    broker.get_positions.return_value = {}
    data_client = AsyncMock()
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client,
    )
    result = ctx.market_data("AAPL", "1min", 5)
    assert result is None
