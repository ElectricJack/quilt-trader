import pytest
import pandas as pd
from unittest.mock import AsyncMock


def test_init_creates_buffers_per_dependency():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 100},
        {"symbol": "SPY", "timeframe": "1min"},
    ])
    assert buf.has("AAPL", "1min")
    assert buf.has("SPY", "1min")
    assert not buf.has("MSFT", "1min")


def test_init_skips_entries_without_symbol():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"timeframe": "1min", "name": "some_scraper"},
        {"symbol": "AAPL"},
    ])
    assert buf.has("AAPL", "1min")


@pytest.mark.asyncio
async def test_backfill_populates_each_buffer():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 50},
    ])
    sample_df = pd.DataFrame([
        {"timestamp": "2026-05-16T12:00:00Z", "open": 100.0, "high": 101.0,
         "low": 99.5, "close": 100.5, "volume": 1000.0},
        {"timestamp": "2026-05-16T12:01:00Z", "open": 100.5, "high": 101.5,
         "low": 100.0, "close": 101.0, "volume": 1500.0},
    ])
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=sample_df)
    await buf.backfill(data_client)
    out = buf.get("AAPL", "1min", 10)
    assert len(out) == 2
    assert out.iloc[0]["close"] == 100.5


def test_ingest_appends_new_bars():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 50},
    ])
    buf.ingest({
        "AAPL": {
            "timeframe": "1min",
            "bars": [
                {"timestamp": "2026-05-16T12:02:00Z", "close": 102.0},
                {"timestamp": "2026-05-16T12:03:00Z", "close": 103.0},
            ],
        },
    })
    out = buf.get("AAPL", "1min", 10)
    assert len(out) == 2
    assert out.iloc[-1]["close"] == 103.0


def test_ingest_ignores_unknown_symbols():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL", "timeframe": "1min"}])
    buf.ingest({"MSFT": {"timeframe": "1min", "bars": [{"close": 99}]}})
    assert buf.get("MSFT", "1min", 10).empty


def test_get_returns_last_n_bars():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 50},
    ])
    for i in range(20):
        buf.ingest({"AAPL": {"timeframe": "1min", "bars": [{"i": i}]}})
    out = buf.get("AAPL", "1min", 5)
    assert len(out) == 5
    assert list(out["i"]) == [15, 16, 17, 18, 19]


def test_get_unknown_returns_empty_df():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL"}])
    assert buf.get("MSFT", "1min", 10).empty


def test_maxlen_enforced():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 3},
    ])
    for i in range(10):
        buf.ingest({"AAPL": {"timeframe": "1min", "bars": [{"i": i}]}})
    out = buf.get("AAPL", "1min", 100)
    assert len(out) == 3
    assert list(out["i"]) == [7, 8, 9]
