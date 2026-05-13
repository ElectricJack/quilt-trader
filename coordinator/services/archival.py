import logging
import os
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

class ArchivalService:
    def __init__(self, archive_dir: str) -> None:
        self._archive_dir = archive_dir

    def archive_path(self, table_name: str, start: str, end: str) -> str:
        return os.path.join(self._archive_dir, table_name, f"{start}_to_{end}.parquet")

    def export_to_parquet(self, table_name: str, start: str, end: str, df: pd.DataFrame) -> Optional[str]:
        if df.empty:
            return None
        path = self.archive_path(table_name, start, end)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_parquet(path, index=False)
        return path

    def list_archives(self) -> list[dict]:
        results = []
        if not os.path.exists(self._archive_dir):
            return results
        for table_name in os.listdir(self._archive_dir):
            table_dir = os.path.join(self._archive_dir, table_name)
            if not os.path.isdir(table_dir):
                continue
            for f in os.listdir(table_dir):
                if f.endswith(".parquet"):
                    path = os.path.join(table_dir, f)
                    results.append({
                        "table_name": table_name, "file_name": f,
                        "file_path": path, "size_bytes": os.path.getsize(path),
                    })
        return results

    def load_archive(self, path: str) -> pd.DataFrame:
        return pd.read_parquet(path)
