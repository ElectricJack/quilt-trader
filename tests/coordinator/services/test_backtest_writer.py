"""Tests for ChunkingObserver."""
from datetime import datetime
from queue import Queue

import pandas as pd
import pytest

from coordinator.services.backtest_writer import ChunkingObserver


def _clock(start: str, periods: int, freq: str) -> pd.DataFrame:
    """Build a clock_series-shaped DataFrame for the observer."""
    return pd.DataFrame({"timestamp": pd.date_range(start, periods=periods, freq=freq)})


def test_days_per_chunk_with_minute_bars_nyse():
    # ~390 ticks/day for NYSE minute bars
    clock = _clock("2024-01-02 09:30", periods=390 * 5, freq="1min")
    obs = ChunkingObserver(queue=Queue(), clock_series=clock)
    # ceil(5000/390) = 13 days
    assert obs.days_per_chunk == 13


def test_days_per_chunk_with_daily_bars_capped_at_max():
    clock = _clock("2024-01-02", periods=365, freq="1D")
    obs = ChunkingObserver(queue=Queue(), clock_series=clock)
    # ceil(5000/1) = 5000 → capped at MAX_DAYS_PER_CHUNK (30)
    assert obs.days_per_chunk == 30


def test_days_per_chunk_with_24h_minute_bars():
    clock = _clock("2024-01-02", periods=1440 * 5, freq="1min")
    obs = ChunkingObserver(queue=Queue(), clock_series=clock)
    # ceil(5000/1440) = 4 days
    assert obs.days_per_chunk == 4


def test_chunk_emitted_on_day_boundary():
    clock = _clock("2024-01-02 00:00", periods=2880, freq="1min")  # 2 days at 1min
    q: Queue = Queue()
    obs = ChunkingObserver(queue=q, clock_series=clock, days_per_chunk_override=1)
    # Simulate the engine calling on_equity_point per tick
    for ts in clock["timestamp"]:
        obs.on_equity_point(ts.to_pydatetime(), 100.0, 100.0, [])
    obs.flush()
    # Drain queue
    chunks = []
    while not q.empty():
        chunks.append(q.get())
    # 2 days, 1 day per chunk → 2 chunks
    assert len(chunks) == 2
    assert chunks[0]["equity"][0]["timestamp"].date() == datetime(2024, 1, 2).date()
    assert chunks[1]["equity"][0]["timestamp"].date() == datetime(2024, 1, 3).date()


def test_chunk_includes_trades_for_window():
    clock = _clock("2024-01-02 00:00", periods=1440, freq="1min")
    q: Queue = Queue()
    obs = ChunkingObserver(queue=q, clock_series=clock, days_per_chunk_override=1)
    obs.on_equity_point(datetime(2024, 1, 2, 10, 0), 100.0, 100.0, [])
    # Mock fill object with the FillRecord shape
    class _F:
        def __init__(self, ts):
            self.timestamp = ts
            self.symbol = "SPY"; self.asset_type = "stock"; self.side = "buy"
            self.quantity = 1.0; self.requested_price = 1.0; self.fill_price = 1.0
            self.slippage_dollars = 0.0; self.slippage_bps_applied = 0.0
            self.fees = 0.0; self.fee_breakdown = {}; self.signal_id = "x"
            self.realized_pnl = None
    obs.on_fill(_F(datetime(2024, 1, 2, 10, 1)))
    obs.flush()
    chunk = q.get()
    assert len(chunk["trades"]) == 1
    assert chunk["trades"][0]["symbol"] == "SPY"


import time
from pathlib import Path
import pyarrow.parquet as pq

from coordinator.services.backtest_writer import ParquetWriterThread


def test_writer_thread_appends_chunks_to_parquet(tmp_path):
    q: Queue = Queue()
    eq_path = tmp_path / "equity_native.parquet"
    tr_path = tmp_path / "trades.parquet"
    t = ParquetWriterThread(queue=q, equity_path=eq_path, trades_path=tr_path)
    t.start()
    # Push 2 chunks
    q.put({
        "equity": [
            {"timestamp": datetime(2024, 1, 2, 10, 0), "portfolio_value": 100.0, "cash": 100.0},
            {"timestamp": datetime(2024, 1, 2, 10, 1), "portfolio_value": 101.0, "cash": 99.0},
        ],
        "trades": [],
        "window_start": datetime(2024, 1, 2, 10, 0),
        "window_end": datetime(2024, 1, 2, 10, 1),
    })
    q.put({
        "equity": [
            {"timestamp": datetime(2024, 1, 3, 10, 0), "portfolio_value": 102.0, "cash": 98.0},
        ],
        "trades": [],
        "window_start": datetime(2024, 1, 3, 10, 0),
        "window_end": datetime(2024, 1, 3, 10, 0),
    })
    q.put(None)  # sentinel
    t.join(timeout=5)
    assert not t.is_alive()
    table = pq.read_table(eq_path)
    assert table.num_rows == 3
    assert "portfolio_value" in table.column_names


def test_writer_thread_records_error_on_bad_chunk(tmp_path):
    q: Queue = Queue()
    t = ParquetWriterThread(
        queue=q,
        equity_path=tmp_path / "equity_native.parquet",
        trades_path=tmp_path / "trades.parquet",
    )
    t.start()
    # Send a malformed chunk (string instead of dict for equity rows)
    q.put({"equity": "not-a-list", "trades": [], "window_start": None, "window_end": None})
    q.put(None)
    t.join(timeout=5)
    assert t.error is not None
