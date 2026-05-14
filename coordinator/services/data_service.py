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

    def load_market_data(self, provider: str, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        path = self.market_data_path(provider, symbol, timeframe)
        if not os.path.exists(path):
            return None
        return pd.read_parquet(path)

    def latest_market_data_timestamp(
        self, provider: str, symbol: str, timeframe: str
    ) -> Optional[pd.Timestamp]:
        """Return the max timestamp in the saved parquet, or None if no file/column."""
        path = self.market_data_path(provider, symbol, timeframe)
        if not os.path.exists(path):
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
            return None
        if fmt == "csv":
            return pd.read_csv(path)
        elif fmt == "json":
            return pd.read_json(path)
        elif fmt == "parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

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
        return results
