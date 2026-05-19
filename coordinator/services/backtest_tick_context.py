"""Tick context used by BacktestEngine.

Implements sdk.context.TickContext with framework-level no-look-ahead
enforcement: market_data() returns only bars whose close time <=
sim_time_now. Same rule for all timeframes (a 1day bar has duration
86400s; a 1tick bar has duration 0s).

Options chain is declared but NotImplementedError-raising in v1 —
options backtesting is a follow-up spec (Spec D §12).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from sdk.context import TickContext
from sdk.models import OptionChain, Position


_TIMEFRAME_TO_SECONDS = {
    "1tick":  0,
    "1min":   60,
    "5min":   300,
    "15min":  900,
    "1hour":  3600,
    "1day":   86400,
}


def timeframe_to_seconds(tf: str) -> int:
    if tf not in _TIMEFRAME_TO_SECONDS:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return _TIMEFRAME_TO_SECONDS[tf]


class BacktestTickContext(TickContext):
    """Backtest-time TickContext.

    The engine calls `set_sim_time()` before each `on_tick` and the engine
    maintains positions/cash state. `bars` is a dict keyed by
    (source, symbol, timeframe) -> pre-loaded DataFrame.
    """

    def __init__(
        self,
        bars: dict[tuple[str, str, str], pd.DataFrame],
        positions: dict[str, Position],
        cash: float,
        account_value: Optional[float] = None,
        buying_power: Optional[float] = None,
        default_source: Optional[str] = None,
    ) -> None:
        self._bars = bars
        self._positions = positions
        self._cash = cash
        self._account_value = account_value if account_value is not None else cash
        self._buying_power = buying_power if buying_power is not None else cash
        self._default_source = default_source
        self._sim_time_now: Optional[datetime] = None
        self._custom_data_cache: Optional[dict[str, pd.DataFrame]] = None

    # ---- mutation hooks called by the engine ----

    def set_sim_time(self, t: datetime) -> None:
        self._sim_time_now = t

    def update_account(
        self, *, cash: float, account_value: float, buying_power: float,
        positions: dict[str, Position],
    ) -> None:
        self._cash = cash
        self._account_value = account_value
        self._buying_power = buying_power
        self._positions = positions

    # ---- TickContext interface ----

    @property
    def timestamp(self) -> datetime:
        if self._sim_time_now is None:
            raise RuntimeError("BacktestTickContext.set_sim_time must be called before timestamp access")
        return self._sim_time_now

    @property
    def mode(self) -> str:
        return "backtest"

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def account_value(self) -> float:
        return self._account_value

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def buying_power(self) -> float:
        return self._buying_power

    def market_data(
        self, symbol: str, timeframe: str = "1min", bars: int = 100,
        source: Optional[str] = None,
    ) -> pd.DataFrame:
        if self._sim_time_now is None:
            raise RuntimeError("set_sim_time must be called before market_data")
        src = source or self._default_source
        if src is None:
            # Fallback: pick the first available source for the symbol+timeframe
            for (s, sym, tf), _df in self._bars.items():
                if sym == symbol and tf == timeframe:
                    src = s
                    break
        key = (src, symbol, timeframe)
        df = self._bars.get(key)
        if df is None or df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        duration_s = timeframe_to_seconds(timeframe)
        cutoff = self._sim_time_now
        # A bar is "fully closed" if its close time (start + duration) is <= cutoff.
        # For 1tick (duration=0), this collapses to timestamp <= cutoff.
        delta = pd.Timedelta(seconds=duration_s)
        visible = df[df["timestamp"] + delta <= pd.Timestamp(cutoff)]
        return visible.tail(bars).reset_index(drop=True)

    def data(self, source_name: str) -> pd.DataFrame:
        """Load custom data (scraper output, CSV, etc.) from disk."""
        if self._custom_data_cache is not None and source_name in self._custom_data_cache:
            return self._custom_data_cache[source_name]
        # Resolve from data/custom/ using the same smart lookup as StandaloneDataProvider
        from pathlib import Path
        custom_dir = Path("data/custom")
        df = self._resolve_custom_data(custom_dir, source_name)
        if df is not None:
            if self._custom_data_cache is None:
                self._custom_data_cache = {}
            self._custom_data_cache[source_name] = df
            return df
        return pd.DataFrame()

    @staticmethod
    def _resolve_custom_data(custom_dir, source_name: str):
        from pathlib import Path
        # 1. Exact path
        path = custom_dir / source_name
        if path.is_file():
            return BacktestTickContext._read_df(path)
        # 2. Try appending extensions
        for ext in (".csv", ".parquet", ".json"):
            candidate = custom_dir / f"{source_name}{ext}"
            if candidate.is_file():
                return BacktestTickContext._read_df(candidate)
        # 3. Look inside subdirectory matching source_name
        subdir = custom_dir / source_name
        if subdir.is_dir():
            for ext in (".csv", ".parquet", ".json"):
                for f in sorted(subdir.glob(f"*{ext}")):
                    return BacktestTickContext._read_df(f)
        # 4. Strip file extension from source_name and try as subdirectory
        #    (e.g., "alpha-picks-scraper.csv" → subdir "alpha-picks-scraper")
        stem = Path(source_name).stem
        if stem != source_name:
            subdir = custom_dir / stem
            if subdir.is_dir():
                for ext in (".csv", ".parquet", ".json"):
                    for f in sorted(subdir.glob(f"*{ext}")):
                        return BacktestTickContext._read_df(f)
        return None

    @staticmethod
    def _read_df(path):
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        elif suffix == ".json":
            return pd.read_json(path)
        elif suffix == ".parquet":
            return pd.read_parquet(path)
        return None

    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        raise NotImplementedError(
            "option_chain not yet available in backtest contexts; tracked as a follow-up. "
            "Options backtest support is documented in Spec D §12."
        )
