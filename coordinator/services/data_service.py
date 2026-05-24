import logging
import os
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

class DataService:
    def __init__(self, market_data_dir: str, custom_data_dir: str) -> None:
        self._market_dir = market_data_dir
        self._custom_dir = custom_data_dir

    def market_data_path(self, provider: str, symbol: str, timeframe: str) -> str:
        return os.path.join(self._market_dir, provider, symbol, f"{timeframe}.parquet")

    def delete_market_data(self, provider: str, symbol: str, timeframe: str) -> bool:
        """Delete a specific parquet file. Returns True if it existed."""
        path = self.market_data_path(provider, symbol, timeframe)
        if os.path.exists(path):
            os.remove(path)
            # Clean up empty directories
            symbol_dir = os.path.dirname(path)
            if os.path.isdir(symbol_dir) and not os.listdir(symbol_dir):
                os.rmdir(symbol_dir)
            return True
        return False

    def custom_data_path(self, name: str, fmt: str) -> str:
        return os.path.join(self._custom_dir, f"{name}.{fmt}")

    def save_market_data(self, provider: str, symbol: str, timeframe: str, df: pd.DataFrame) -> str:
        path = self.market_data_path(provider, symbol, timeframe)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            existing = pd.read_parquet(path)
            # Keep new on collision: put existing first, then new; drop_duplicates keeps the last.
            # If "timestamp" column missing in either df, fall back to overwrite.
            if "timestamp" in df.columns and "timestamp" in existing.columns:
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.drop_duplicates(subset="timestamp", keep="last")
                combined = combined.sort_values("timestamp").reset_index(drop=True)
                df = combined
        df.to_parquet(path, index=False)
        return path

    # Aliases for timeframes that can be derived from 1-min data.
    _DERIVABLE = {"5min", "15min", "1h", "1hour", "1d", "1day"}

    # Map caller-supplied timeframe strings to the keys aggregate_bars understands.
    _TF_ALIAS: dict[str, str] = {
        "5min": "5min",
        "15min": "15min",
        "1h": "1h",
        "1hour": "1h",
        "1d": "1d",
        "1day": "1d",
    }

    def _resolve_provider_path(self, provider: str, symbol: str, timeframe: str) -> Optional[str]:
        """Find the parquet file, checking both the exact provider and {provider}_live."""
        path = self.market_data_path(provider, symbol, timeframe)
        if os.path.exists(path):
            return path
        live_path = self.market_data_path(f"{provider}_live", symbol, timeframe)
        if os.path.exists(live_path):
            return live_path
        return None

    def load_market_data(self, provider: str, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        path = self._resolve_provider_path(provider, symbol, timeframe)
        if path:
            return pd.read_parquet(path)

        # Derive from 1-min when the exact file is absent and the timeframe is aggregatable.
        if timeframe in self._DERIVABLE:
            one_min_path = self.market_data_path(provider, symbol, "1min")
            if os.path.exists(one_min_path):
                df_1min = pd.read_parquet(one_min_path)
                agg_key = self._TF_ALIAS.get(timeframe, timeframe)
                try:
                    return self.aggregate_bars(df_1min, agg_key)
                except ValueError:
                    pass

        return None

    def latest_market_data_timestamp(
        self, provider: str, symbol: str, timeframe: str
    ) -> Optional[pd.Timestamp]:
        """Return the max timestamp in the saved parquet, or None if no file/column."""
        path = self._resolve_provider_path(provider, symbol, timeframe)
        if not path:
            return None
        try:
            df = pd.read_parquet(path, columns=["timestamp"])
        except Exception:
            df = pd.read_parquet(path)
            if "timestamp" not in df.columns:
                return None
        if df.empty:
            return None
        return pd.to_datetime(df["timestamp"], utc=True).max()

    def earliest_market_data_timestamp(
        self, provider: str, symbol: str, timeframe: str
    ) -> Optional[pd.Timestamp]:
        """Return the min timestamp in the saved parquet, or None if no file/column."""
        path = self._resolve_provider_path(provider, symbol, timeframe)
        if not path:
            return None
        try:
            df = pd.read_parquet(path, columns=["timestamp"])
        except Exception:
            df = pd.read_parquet(path)
            if "timestamp" not in df.columns:
                return None
        if df.empty:
            return None
        return pd.to_datetime(df["timestamp"], utc=True).min()

    def save_custom_data(self, name: str, df: pd.DataFrame, fmt: str) -> str:
        path = self.custom_data_path(name, fmt)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if fmt == "csv":
            df.to_csv(path, index=False)
        elif fmt == "json":
            df.to_json(path, orient="records")
        elif fmt == "parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False)
        return path

    def load_custom_data(self, name: str, fmt: str) -> Optional[pd.DataFrame]:
        path = self.custom_data_path(name, fmt)
        if not os.path.exists(path):
            direct = os.path.join(self._custom_dir, name)
            if os.path.exists(direct):
                path = direct
            else:
                return None
        if fmt == "csv":
            return pd.read_csv(path)
        elif fmt == "json":
            return pd.read_json(path)
        elif fmt == "parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

    @staticmethod
    def aggregate_bars(df_1min: pd.DataFrame, target: str) -> pd.DataFrame:
        """Lazily aggregate 1-min OHLCV bars to a higher timeframe.

        target: "1min" (pass-through) | "5min" | "15min" | "1h" | "1d".
        df_1min must have columns: timestamp, open, high, low, close, volume.
        Returns the aggregated DataFrame, same column shape.
        """
        if target == "1min":
            return df_1min
        pandas_rule = {"5min": "5min", "15min": "15min", "1h": "1h", "1d": "1D"}.get(target)
        if pandas_rule is None:
            raise ValueError(f"unsupported target timeframe: {target!r}")
        df = df_1min.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
        out = df.resample(pandas_rule, label="left", closed="left").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna(subset=["open"]).reset_index()
        return out

    # -- Option chain storage ---------------------------------------------------

    def option_chain_path(self, provider: str, symbol: str, expiration) -> str:
        from datetime import date as _date
        exp_str = expiration.isoformat() if isinstance(expiration, _date) else str(expiration)
        return os.path.join(self._market_dir, provider, symbol, "options", exp_str, "chain.parquet")

    def save_option_chain(self, provider: str, symbol: str, expiration, df: pd.DataFrame) -> str:
        path = self.option_chain_path(provider, symbol, expiration)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_parquet(path, index=False)
        return path

    def load_option_chain(self, provider: str, symbol: str, expiration) -> Optional[pd.DataFrame]:
        path = self.option_chain_path(provider, symbol, expiration)
        if not os.path.exists(path):
            return None
        return pd.read_parquet(path)

    def list_option_chain_expirations(self, provider: str, symbol: str) -> list:
        from datetime import date as _date
        options_dir = os.path.join(self._market_dir, provider, symbol, "options")
        if not os.path.isdir(options_dir):
            return []
        expirations = []
        for name in os.listdir(options_dir):
            chain_path = os.path.join(options_dir, name, "chain.parquet")
            if os.path.exists(chain_path):
                try:
                    expirations.append(_date.fromisoformat(name))
                except ValueError:
                    continue
        return sorted(expirations)

    def list_option_contracts(
        self, provider: str, underlying: str, expiration: "date",
    ) -> list[str]:
        """List OCC symbols on disk for a given underlying + expiration."""
        from coordinator.services.chain_builder import parse_occ_symbol
        provider_dir = os.path.join(self._market_dir, provider)
        if not os.path.isdir(provider_dir):
            return []
        exp_str = expiration.isoformat()
        result = []
        for name in os.listdir(provider_dir):
            parsed = parse_occ_symbol(name)
            if parsed and parsed["underlying"] == underlying and parsed["expiration"] == exp_str:
                if os.path.exists(os.path.join(provider_dir, name, "1day.parquet")):
                    result.append(name)
        return sorted(result)

    def list_option_expirations(self, provider: str, underlying: str) -> list:
        """List unique expiration dates for an underlying from OCC bar files on disk."""
        from datetime import date as _date
        from coordinator.services.chain_builder import parse_occ_symbol
        provider_dir = os.path.join(self._market_dir, provider)
        if not os.path.isdir(provider_dir):
            return []
        expirations = set()
        for name in os.listdir(provider_dir):
            parsed = parse_occ_symbol(name)
            if parsed and parsed["underlying"] == underlying:
                if os.path.exists(os.path.join(provider_dir, name, "1day.parquet")):
                    expirations.add(_date.fromisoformat(parsed["expiration"]))
        return sorted(expirations)

    def build_chain(
        self, provider: str, underlying: str, expiration, as_of=None,
    ) -> pd.DataFrame:
        """Build an option chain from stored contract bar files."""
        from datetime import date as _date
        from coordinator.services.chain_builder import build_chain_from_bars
        if isinstance(expiration, str):
            expiration = _date.fromisoformat(expiration)
        contracts = self.list_option_contracts(provider, underlying, expiration)
        if not contracts:
            return pd.DataFrame()
        bars = {}
        for sym in contracts:
            df = self.load_market_data(provider, sym, "1day")
            if df is not None and not df.empty:
                bars[sym] = df
        if as_of is None:
            as_of = pd.Timestamp.now()
        return build_chain_from_bars(bars, as_of=pd.Timestamp(as_of))

    # -- Market data listing ----------------------------------------------------

    def list_available_market_data(self) -> list[dict]:
        results = []
        if not os.path.exists(self._market_dir):
            return results
        for provider in os.listdir(self._market_dir):
            provider_dir = os.path.join(self._market_dir, provider)
            if not os.path.isdir(provider_dir):
                continue
            for symbol in os.listdir(provider_dir):
                symbol_dir = os.path.join(provider_dir, symbol)
                if not os.path.isdir(symbol_dir):
                    continue
                for f in os.listdir(symbol_dir):
                    if f.endswith(".parquet"):
                        timeframe = f.replace(".parquet", "")
                        path = os.path.join(symbol_dir, f)
                        results.append({
                            "provider": provider, "symbol": symbol, "timeframe": timeframe,
                            "file_path": path, "size_bytes": os.path.getsize(path),
                        })
                # Include option chains stored under options/{expiration}/chain.parquet
                options_dir = os.path.join(symbol_dir, "options")
                if os.path.isdir(options_dir):
                    for exp_name in sorted(os.listdir(options_dir)):
                        chain_path = os.path.join(options_dir, exp_name, "chain.parquet")
                        if os.path.exists(chain_path):
                            results.append({
                                "provider": provider, "symbol": symbol,
                                "timeframe": "options",
                                "file_path": chain_path,
                                "size_bytes": os.path.getsize(chain_path),
                                "data_type": "option_chain",
                                "expiration": exp_name,
                            })
        return results
