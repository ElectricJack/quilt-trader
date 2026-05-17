"""In-memory per-instance rolling buffer for market data bars.

Each (symbol, timeframe) pair declared in the algorithm's data_dependencies
gets its own deque sized to history_bars. Backfilled once from coordinator
HTTP on start; ingests deltas pushed by coordinator on each tick; serves
ctx.market_data(...) calls from memory without HTTP.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class RollingDataBuffer:
    def __init__(self, data_dependencies: list[dict]) -> None:
        self._buffers: dict[tuple[str, str], deque] = {}
        self._max_bars: dict[tuple[str, str], int] = {}
        for d in data_dependencies or []:
            if not isinstance(d, dict):
                continue
            sym = d.get("symbol")
            if not sym:
                continue
            tf = d.get("timeframe", "1min")
            max_bars = int(d.get("history_bars", 200))
            key = (sym, tf)
            self._buffers[key] = deque(maxlen=max_bars)
            self._max_bars[key] = max_bars

    async def backfill(self, data_client: Any) -> None:
        for (sym, tf), buf in self._buffers.items():
            try:
                df = await data_client.get_market_data(
                    sym, timeframe=tf, bars=self._max_bars[(sym, tf)],
                )
            except Exception:
                logger.exception("Backfill failed for %s/%s", sym, tf)
                continue
            for _, row in df.iterrows():
                buf.append(row.to_dict())

    def ingest(self, push_data: dict) -> None:
        for sym, payload in (push_data or {}).items():
            if not isinstance(payload, dict):
                continue
            tf = payload.get("timeframe", "1min")
            key = (sym, tf)
            if key not in self._buffers:
                continue
            for bar in payload.get("bars", []) or []:
                self._buffers[key].append(bar)

    def get(self, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        key = (symbol, timeframe)
        if key not in self._buffers:
            return pd.DataFrame()
        rows = list(self._buffers[key])[-bars:]
        return pd.DataFrame(rows)

    def has(self, symbol: str, timeframe: str) -> bool:
        return (symbol, timeframe) in self._buffers
