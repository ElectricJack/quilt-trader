"""Job-scoped in-memory cache of preloaded bar data.

The validation lab loads the same (symbol, timeframe) parquet many times across
a single CPCV / sweep job. This cache hoists that load to once per job.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

import pandas as pd


LoaderFn = Callable[[str, str, str, date, date], Optional[pd.DataFrame]]


class BacktestBarsCache:
    """Preload bar DataFrames once per job; serve sliced views to each backtest.

    Keys are (source, symbol, timeframe). Values are pandas DataFrames sorted
    by a timestamp index.
    """

    def __init__(self) -> None:
        self._bars: dict[tuple[str, str, str], pd.DataFrame] = {}

    def preload(
        self,
        requirements: list[tuple[str, str, str]],
        start: date,
        end: date,
        loader: LoaderFn,
    ) -> None:
        for source, symbol, timeframe in requirements:
            key = (source, symbol, timeframe)
            if key in self._bars:
                continue
            df = loader(source, symbol, timeframe, start, end)
            if df is None or df.empty:
                continue
            self._bars[key] = df.sort_index()

    def get(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
    ) -> pd.DataFrame | None:
        df = self._bars.get((source, symbol, timeframe))
        if df is None:
            return None
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=59, seconds=59)
        return df.loc[start_ts:end_ts]

    def memory_estimate_bytes(self) -> int:
        return sum(df.memory_usage(deep=True).sum() for df in self._bars.values())

    def has(self, source: str, symbol: str, timeframe: str) -> bool:
        return (source, symbol, timeframe) in self._bars
