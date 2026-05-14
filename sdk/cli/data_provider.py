from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import pandas as pd
import httpx
from sdk.cli.config import QuiltDevConfig

class DataProvider(ABC):
    @abstractmethod
    def get_market_data(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]: ...
    @abstractmethod
    def get_custom_data(self, source_name: str) -> Optional[pd.DataFrame]: ...

class StandaloneDataProvider(DataProvider):
    def __init__(self, config: QuiltDevConfig, data_dir: Optional[Path] = None):
        self.config = config
        self.data_dir = data_dir or Path("data")

    def get_market_data(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        market_dir = self.data_dir / "market"
        csv_path = market_dir / f"{symbol}_{timeframe}.csv"
        if csv_path.exists():
            return pd.read_csv(csv_path)
        parquet_path = market_dir / f"{symbol}_{timeframe}.parquet"
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        return None

    def get_custom_data(self, source_name: str) -> Optional[pd.DataFrame]:
        custom_dir = self.data_dir / "custom"
        path = custom_dir / source_name
        if not path.exists():
            return None
        if source_name.endswith(".csv"):
            return pd.read_csv(path)
        elif source_name.endswith(".json"):
            return pd.read_json(path)
        elif source_name.endswith(".parquet"):
            return pd.read_parquet(path)
        return None

class ConnectedDataProvider(DataProvider):
    def __init__(self, config: QuiltDevConfig):
        self.config = config
        self.base_url = config.coordinator_url

    def get_market_data(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        response = httpx.get(f"{self.base_url}/api/data/market/{symbol}",
                            params={"timeframe": timeframe}, timeout=30)
        if response.status_code != 200:
            return None
        return pd.DataFrame(response.json()["data"])

    def get_custom_data(self, source_name: str) -> Optional[pd.DataFrame]:
        response = httpx.get(f"{self.base_url}/api/data/custom/{source_name}", timeout=30)
        if response.status_code != 200:
            return None
        return pd.DataFrame(response.json()["data"])
