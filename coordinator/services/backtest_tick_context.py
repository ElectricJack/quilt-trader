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
        data_service: Optional[Any] = None,
        on_miss: Optional[Any] = None,  # sync callable(symbol, timeframe, source) -> Optional[pd.DataFrame]
    ) -> None:
        self._bars = bars
        self._positions = positions
        self._cash = cash
        self._account_value = account_value if account_value is not None else cash
        self._buying_power = buying_power if buying_power is not None else cash
        self._default_source = default_source
        self._data_service = data_service
        self._on_miss = on_miss
        self._sim_time_now: Optional[datetime] = None
        self._custom_data_cache: Optional[dict[str, pd.DataFrame]] = None
        self._cancel_orders_requested = False
        self._option_chain_cache: dict[tuple, "pd.DataFrame"] = {}

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
        # Use "polygon" as the last-resort default provider
        src = src or "polygon"
        key = (src, symbol, timeframe)
        df = self._bars.get(key)

        # On a cache miss, try loading from disk first (fast path, no network).
        if (df is None or df.empty) and self._data_service is not None:
            disk_df = self._data_service.load_market_data(src, symbol, timeframe)
            if disk_df is not None and not disk_df.empty:
                if "timestamp" in disk_df.columns:
                    disk_df = disk_df.copy()
                    disk_df["timestamp"] = pd.to_datetime(disk_df["timestamp"]).dt.tz_localize(None)
                self._bars[key] = disk_df
                df = disk_df

        # If still not found, call the on_miss hook (downloads and caches to disk).
        if (df is None or df.empty) and self._on_miss is not None:
            import logging
            logging.getLogger(__name__).info(
                "market_data miss — auto-downloading %s %s (%s)", symbol, timeframe, src
            )
            try:
                fetched = self._on_miss(symbol, timeframe, src)
                if fetched is not None and not fetched.empty:
                    if "timestamp" in fetched.columns:
                        fetched = fetched.copy()
                        fetched["timestamp"] = pd.to_datetime(fetched["timestamp"]).dt.tz_localize(None)
                    self._bars[key] = fetched
                    df = fetched
            except Exception:
                import logging as _log
                _log.getLogger(__name__).exception(
                    "Auto-download failed for %s %s", symbol, timeframe
                )

        if df is None or df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        duration_s = timeframe_to_seconds(timeframe)
        cutoff = pd.Timestamp(self._sim_time_now)
        # Normalize cutoff tz to match the column. On-disk bars are stored tz-naive
        # (UTC by convention); pre-loaded bars may be tz-aware. If the column is
        # tz-naive, strip tz from the cutoff so the comparison succeeds.
        ts_col = pd.to_datetime(df["timestamp"])
        if ts_col.dt.tz is None and cutoff.tz is not None:
            cutoff = cutoff.tz_convert("UTC").tz_localize(None)
        # A bar is "fully closed" if its close time (start + duration) is <= cutoff.
        # For 1tick (duration=0), this collapses to timestamp <= cutoff.
        delta = pd.Timedelta(seconds=duration_s)
        visible = df[ts_col + delta <= cutoff]
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

    def cancel_all_orders(self) -> None:
        """Request cancellation of all pending orders. The engine checks this flag each bar."""
        self._cancel_orders_requested = True

    def _get_underlying_price(self, symbol: str) -> float | None:
        """Get the current underlying price from market data bars."""
        for (src, sym, tf), df in self._bars.items():
            if sym == symbol and df is not None and not df.empty:
                ts = pd.to_datetime(df["timestamp"])
                if ts.dt.tz is not None:
                    ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
                cutoff = pd.Timestamp(self._sim_time_now)
                if cutoff.tz is not None:
                    cutoff = cutoff.tz_localize(None)
                visible = df[ts <= cutoff]
                if not visible.empty:
                    return float(visible.iloc[-1]["close"])
        return None

    @staticmethod
    def _infer_snapshot_underlying(chain_df: pd.DataFrame) -> float | None:
        """Infer the underlying price when the chain was snapshotted.

        Uses put-call parity: at the ATM strike, call and put prices are
        closest. Returns the strike where |call_bid - put_bid| is minimised,
        which is a good proxy for the underlying at snapshot time.
        """
        calls = chain_df[chain_df["option_type"] == "call"]
        puts = chain_df[chain_df["option_type"] == "put"]
        if calls.empty or puts.empty:
            # Fallback: median strike
            return float(chain_df["strike"].median()) if not chain_df.empty else None
        # Match by strike
        merged = calls.merge(puts, on="strike", suffixes=("_c", "_p"))
        if merged.empty:
            return float(chain_df["strike"].median())
        merged["diff"] = (merged["bid_c"] - merged["bid_p"]).abs()
        atm_row = merged.loc[merged["diff"].idxmin()]
        return float(atm_row["strike"])

    def _reprice_chain(self, chain_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Adjust cached option prices based on current underlying price.

        Uses intrinsic value shift: if underlying moved since the snapshot,
        adjust call/put prices by the change in intrinsic value.
        """
        underlying_price = self._get_underlying_price(symbol)
        if underlying_price is None:
            return chain_df

        ref_key = "_ref_price_" + symbol
        ref_price = getattr(self, ref_key, None)
        if ref_price is None:
            # Infer what the underlying was when the chain was snapshotted
            ref_price = self._infer_snapshot_underlying(chain_df)
            if ref_price is None:
                return chain_df
            setattr(self, ref_key, ref_price)

        price_change = underlying_price - ref_price
        if abs(price_change) < 0.01:
            return chain_df

        repriced = chain_df.copy()
        for idx, row in repriced.iterrows():
            strike = float(row["strike"])
            if row["option_type"] == "call":
                old_intrinsic = max(0.0, ref_price - strike)
                new_intrinsic = max(0.0, underlying_price - strike)
                delta = new_intrinsic - old_intrinsic
            else:
                old_intrinsic = max(0.0, strike - ref_price)
                new_intrinsic = max(0.0, strike - underlying_price)
                delta = new_intrinsic - old_intrinsic

            for col in ("bid", "ask", "last"):
                if col in repriced.columns:
                    repriced.at[idx, col] = max(0.0, float(row[col]) + delta)

        return repriced

    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        from sdk.models import OptionContract

        if isinstance(expiration, str):
            expiration = date.fromisoformat(expiration)
        exp = expiration or (self._sim_time_now.date() if self._sim_time_now else date.today())
        source = self._default_source or "polygon"

        cache_key = (source, symbol, exp)
        if cache_key in self._option_chain_cache:
            df = self._option_chain_cache[cache_key]
        elif self._data_service is not None and hasattr(self._data_service, "load_option_chain"):
            df = self._data_service.load_option_chain(source, symbol, exp)
            if df is not None and not df.empty:
                self._option_chain_cache[cache_key] = df
            else:
                df = pd.DataFrame()
        else:
            df = pd.DataFrame()

        # Nearest-expiration fallback: if exact expiration not found,
        # try the closest available within 7 days
        if (df is None or df.empty) and self._data_service is not None and hasattr(self._data_service, "list_option_chain_expirations"):
            available = self._data_service.list_option_chain_expirations(source, symbol)
            if available:
                nearest = min(available, key=lambda d: abs((d - exp).days))
                if abs((nearest - exp).days) <= 45:
                    df = self._data_service.load_option_chain(source, symbol, nearest)
                    exp = nearest  # update expiration to the one we actually found
                    if df is not None and not df.empty:
                        self._option_chain_cache[cache_key] = df

        if df is None or df.empty:
            return OptionChain(underlying=symbol, expiration=exp, calls=[], puts=[])

        if df is not None and not df.empty:
            df = self._reprice_chain(df, symbol)

        calls: list[OptionContract] = []
        puts: list[OptionContract] = []
        for _, row in df.iterrows():
            contract = OptionContract(
                symbol=str(row.get("ticker", row.get("symbol", ""))),
                underlying=symbol,
                expiration=exp,
                strike=float(row.get("strike", 0)),
                option_type=str(row.get("option_type", "")),
                bid=float(row.get("bid", 0)),
                ask=float(row.get("ask", 0)),
                last=float(row.get("last", 0)),
                volume=int(row.get("volume", 0)),
                open_interest=int(row.get("open_interest", 0)),
                implied_volatility=float(row.get("implied_volatility", 0)),
            )
            if contract.option_type == "call":
                calls.append(contract)
            elif contract.option_type == "put":
                puts.append(contract)

        return OptionChain(underlying=symbol, expiration=exp, calls=calls, puts=puts)
