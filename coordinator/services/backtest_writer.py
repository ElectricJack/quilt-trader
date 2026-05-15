"""Backtest streaming pipeline.

ChunkingObserver: implements EngineObserver, buffers per-tick events
and pushes chunks to a queue on simulated-day boundaries with adaptive
sizing (target ticks per chunk, clamped to a day range).

ParquetWriterThread (added in Task 6): consumes chunks from the queue
and appends to parquet files using pyarrow.parquet.ParquetWriter.
"""
from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, date
from pathlib import Path
from queue import Queue
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


_EQUITY_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns")),
    ("portfolio_value", pa.float64()),
    ("cash", pa.float64()),
])

_TRADE_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns")),
    ("symbol", pa.string()),
    ("asset_type", pa.string()),
    ("side", pa.string()),
    ("quantity", pa.float64()),
    ("requested_price", pa.float64()),
    ("fill_price", pa.float64()),
    ("slippage_dollars", pa.float64()),
    ("slippage_bps_applied", pa.float64()),
    ("fees", pa.float64()),
    ("fee_breakdown", pa.string()),  # JSON-serialized
    ("signal_id", pa.string()),
    ("realized_pnl", pa.float64()),
])

TARGET_TICKS_PER_CHUNK = 5_000
MIN_DAYS_PER_CHUNK = 1
MAX_DAYS_PER_CHUNK = 30


def compute_days_per_chunk(clock_series: pd.DataFrame) -> int:
    """Adaptive: target ~5k ticks per chunk, clamped to [1, 30] days."""
    if clock_series is None or len(clock_series) == 0:
        return MIN_DAYS_PER_CHUNK
    unique_calendar_dates = int(clock_series["timestamp"].dt.date.nunique())
    total_days = max(5, unique_calendar_dates)
    avg_ticks_per_day = max(1.0, len(clock_series) / total_days)
    days = math.ceil(TARGET_TICKS_PER_CHUNK / avg_ticks_per_day)
    return max(MIN_DAYS_PER_CHUNK, min(MAX_DAYS_PER_CHUNK, days))


class ChunkingObserver:
    """EngineObserver that emits chunks every N simulated days to a queue."""

    def __init__(
        self,
        *, queue: Queue, clock_series: pd.DataFrame,
        days_per_chunk_override: Optional[int] = None,
    ) -> None:
        self._q = queue
        self.days_per_chunk: int = (
            days_per_chunk_override
            if days_per_chunk_override is not None
            else compute_days_per_chunk(clock_series)
        )
        self._buf_equity: list[dict] = []
        self._buf_trades: list[dict] = []
        self._chunk_start_date: Optional[date] = None
        self._chunk_window_days: int = 0
        self._lock = threading.Lock()
        self._daily_aggregate: dict[date, float] = {}  # date -> last portfolio_value
        self.writer_error: Optional[BaseException] = None

    # ---- EngineObserver protocol ----

    def on_tick(self, sim_time: datetime, ctx_snapshot: dict) -> None:
        pass

    def on_signals_emitted(self, sim_time: datetime, signals) -> None:
        pass

    def on_signal_rejected(self, sim_time: datetime, signal, reason: str) -> None:
        pass

    def on_equity_point(
        self, sim_time: datetime, portfolio_value: float, cash: float, positions
    ) -> None:
        d = sim_time.date()
        if self._chunk_start_date is None:
            self._chunk_start_date = d
            self._chunk_window_days = 1
        elif d != self._chunk_start_date and d not in (self._chunk_start_date,):
            # New day. Detect rollover by counting unique dates in the buffer.
            if self._chunk_window_days >= self.days_per_chunk:
                self._flush_chunk()
                self._chunk_start_date = d
                self._chunk_window_days = 1
            else:
                # Same chunk, advance day window
                if not self._buf_equity or self._buf_equity[-1]["timestamp"].date() != d:
                    self._chunk_window_days += 1
        self._buf_equity.append({
            "timestamp": sim_time,
            "portfolio_value": float(portfolio_value),
            "cash": float(cash),
        })
        with self._lock:
            self._daily_aggregate[d] = float(portfolio_value)

    def on_fill(self, fill) -> None:
        self._buf_trades.append({
            "timestamp": fill.timestamp,
            "symbol": fill.symbol,
            "asset_type": fill.asset_type,
            "side": fill.side,
            "quantity": float(fill.quantity),
            "requested_price": fill.requested_price,
            "fill_price": fill.fill_price,
            "slippage_dollars": fill.slippage_dollars,
            "slippage_bps_applied": fill.slippage_bps_applied,
            "fees": fill.fees,
            "fee_breakdown": fill.fee_breakdown,
            "signal_id": fill.signal_id,
            "realized_pnl": fill.realized_pnl,
        })

    def on_complete(self, summary) -> None:
        self.flush()

    def on_error(self, exc) -> None:
        self.flush()

    # ---- Public ----

    def flush(self) -> None:
        if self._buf_equity or self._buf_trades:
            self._flush_chunk()

    def daily_aggregate_snapshot(self) -> list[dict]:
        """Return a thread-safe snapshot of the running daily curve."""
        with self._lock:
            return [
                {"timestamp": d.isoformat(), "portfolio_value": v}
                for d, v in sorted(self._daily_aggregate.items())
            ]

    # ---- Internal ----

    def _flush_chunk(self) -> None:
        chunk = {
            "equity": self._buf_equity,
            "trades": self._buf_trades,
            "window_start": self._buf_equity[0]["timestamp"] if self._buf_equity else None,
            "window_end": self._buf_equity[-1]["timestamp"] if self._buf_equity else None,
        }
        self._buf_equity = []
        self._buf_trades = []
        self._chunk_start_date = None
        self._chunk_window_days = 0
        self._q.put(chunk)


class ParquetWriterThread(threading.Thread):
    """Drain (queue → parquet) until a None sentinel, then close writers."""

    def __init__(
        self, *, queue: Queue, equity_path: Path, trades_path: Path,
    ) -> None:
        super().__init__(daemon=True, name="backtest-writer")
        self._q = queue
        self._eq_path = Path(equity_path)
        self._tr_path = Path(trades_path)
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        eq_writer: Optional[pq.ParquetWriter] = None
        tr_writer: Optional[pq.ParquetWriter] = None
        try:
            self._eq_path.parent.mkdir(parents=True, exist_ok=True)
            eq_writer = pq.ParquetWriter(self._eq_path, _EQUITY_SCHEMA, compression="snappy")
            tr_writer = pq.ParquetWriter(self._tr_path, _TRADE_SCHEMA, compression="snappy")
            while True:
                chunk = self._q.get()
                if chunk is None:
                    return
                self._write_chunk(eq_writer, tr_writer, chunk)
        except Exception as exc:
            logger.exception("ParquetWriterThread failed")
            self.error = exc
            # Drain remaining items so the producer doesn't block forever
            while True:
                try:
                    item = self._q.get_nowait()
                except Exception:
                    break
                if item is None:
                    break
        finally:
            if eq_writer is not None:
                try: eq_writer.close()
                except Exception: pass
            if tr_writer is not None:
                try: tr_writer.close()
                except Exception: pass

    @staticmethod
    def _write_chunk(eq_writer, tr_writer, chunk: dict) -> None:
        import json
        eq_rows = chunk["equity"]
        if not isinstance(eq_rows, list):
            raise TypeError(f"chunk['equity'] must be a list, got {type(eq_rows).__name__}")
        if eq_rows:
            table = pa.Table.from_pylist(eq_rows, schema=_EQUITY_SCHEMA)
            eq_writer.write_table(table)
        tr_rows = chunk.get("trades") or []
        if tr_rows:
            normalized = [
                {**t, "fee_breakdown": json.dumps(t.get("fee_breakdown") or {})}
                for t in tr_rows
            ]
            table = pa.Table.from_pylist(normalized, schema=_TRADE_SCHEMA)
            tr_writer.write_table(table)
