# coordinator/services/live_sample_sink.py
"""Append-only parquet writer for live algorithm samples.

One pair of parquet files per (deployment_id, run_id):
  data/live/<deployment_id>/<run_id>/equity.parquet
  data/live/<deployment_id>/<run_id>/trades.parquet

Buffers in-memory until `buffer_size` rows or the buffer is flushed explicitly.
Schemas are shared with backtest_writer via streaming_schemas.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from coordinator.services.streaming_schemas import EQUITY_SCHEMA, TRADE_SCHEMA

logger = logging.getLogger(__name__)


def _coerce_ts(s: str | None) -> pd.Timestamp:
    """Parse an ISO timestamp; coerce to tz-naive UTC."""
    if not s:
        return pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
    p = pd.Timestamp(s)
    if p.tz is not None:
        p = p.tz_convert("UTC").tz_localize(None)
    return p


class LiveSampleSink:
    def __init__(
        self, base_dir: Path,
        buffer_size: int = 200, flush_interval_seconds: int = 10,
    ) -> None:
        self._base = Path(base_dir)
        self._buf_size = buffer_size
        self._interval = flush_interval_seconds
        self._equity_buf: dict[tuple[str, str], list[dict]] = {}
        self._trade_buf: dict[tuple[str, str], list[dict]] = {}
        self._lock = asyncio.Lock()

    def _equity_path(self, dep_id: str, run_id: str) -> Path:
        return self._base / dep_id / run_id / "equity.parquet"

    def _trades_path(self, dep_id: str, run_id: str) -> Path:
        return self._base / dep_id / run_id / "trades.parquet"

    async def add_equity_sample(self, dep_id: str, run_id: str, sample: dict) -> None:
        async with self._lock:
            self._equity_buf.setdefault((dep_id, run_id), []).append(sample)
            if len(self._equity_buf[(dep_id, run_id)]) >= self._buf_size:
                await self._flush_equity(dep_id, run_id)

    async def add_trade_sample(self, dep_id: str, run_id: str, sample: dict) -> None:
        async with self._lock:
            self._trade_buf.setdefault((dep_id, run_id), []).append(sample)
            if len(self._trade_buf[(dep_id, run_id)]) >= self._buf_size:
                await self._flush_trades(dep_id, run_id)

    async def flush(self) -> None:
        async with self._lock:
            for key in list(self._equity_buf.keys()):
                await self._flush_equity(*key)
            for key in list(self._trade_buf.keys()):
                await self._flush_trades(*key)

    async def _flush_equity(self, dep_id: str, run_id: str) -> None:
        rows = self._equity_buf.pop((dep_id, run_id), [])
        if not rows:
            return
        df = pd.DataFrame([{
            "timestamp": _coerce_ts(r.get("timestamp")),
            "portfolio_value": float(r["portfolio_value"]),
            "cash": float(r.get("cash") or 0.0),
        } for r in rows])
        await asyncio.to_thread(self._append_parquet,
                                self._equity_path(dep_id, run_id),
                                df, EQUITY_SCHEMA)

    async def _flush_trades(self, dep_id: str, run_id: str) -> None:
        rows = self._trade_buf.pop((dep_id, run_id), [])
        if not rows:
            return
        df = pd.DataFrame([{
            "timestamp": _coerce_ts(r.get("timestamp")),
            "symbol": r["symbol"],
            "asset_type": r.get("asset_type", "equities"),
            "side": r["side"],
            "quantity": float(r["quantity"]),
            "requested_price": float(r.get("requested_price") or 0.0),
            "fill_price": float(r.get("fill_price") or 0.0),
            "slippage_dollars": float(r.get("slippage_dollars") or 0.0),
            "slippage_bps_applied": float(r.get("slippage_bps_applied") or 0.0),
            "fees": float(r.get("fees") or 0.0),
            "fee_breakdown": r.get("fee_breakdown") or "{}",
            "signal_id": r.get("signal_id") or "",
            "realized_pnl": float(r["realized_pnl"]) if r.get("realized_pnl") is not None else None,
        } for r in rows])
        await asyncio.to_thread(self._append_parquet,
                                self._trades_path(dep_id, run_id),
                                df, TRADE_SCHEMA)

    @staticmethod
    def _append_parquet(path: Path, df: pd.DataFrame, schema: pa.Schema) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
        if path.exists():
            existing = pq.read_table(path, schema=schema)
            table = pa.concat_tables([existing, table])
        pq.write_table(table, path, compression="snappy")
