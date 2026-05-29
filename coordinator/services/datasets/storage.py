from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from coordinator.services.datasets.registry import DatasetSpec
from coordinator.services.datasets import registry as _registry

_LOG = logging.getLogger(__name__)

_WARN_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB


class DatasetService:
    def __init__(self, data_root: Path):
        self._data_root = Path(data_root)

    def _path_for(self, spec: DatasetSpec, symbol: str | None) -> Path:
        short = spec.name.split(".", 1)[1]
        base = self._data_root / "datasets" / spec.provider
        if spec.symbol_keyed:
            if symbol is None:
                raise ValueError(f"{spec.name} requires symbol")
            return base / short / f"{symbol}.parquet"
        return base / f"{short}.parquet"

    def _normalize(self, spec: DatasetSpec, rows: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        # Build rename map: raw API column name → canonical bitemporal name
        rename: dict[str, str] = {spec.event_date_column: "event_date"}
        if spec.knowledge_date_column is not None:
            rename[spec.knowledge_date_column] = "knowledge_date"
        df = df.rename(columns=rename)
        # Parse to UTC then strip timezone so we store naive-UTC timestamps
        df["event_date"] = (
            pd.to_datetime(df["event_date"], utc=True, errors="coerce")
            .dt.tz_localize(None)
        )
        if "knowledge_date" in df.columns:
            df["knowledge_date"] = (
                pd.to_datetime(df["knowledge_date"], utc=True, errors="coerce")
                .dt.tz_localize(None)
            )
        else:
            # Single-timestamp dataset: knowledge equals event (+ optional lag)
            df["knowledge_date"] = df["event_date"] + spec.knowledge_date_lag
        return df

    def _id_columns_after_rename(self, spec: DatasetSpec) -> list[str]:
        """Translate spec.id_columns (raw API names) to post-rename column names."""
        rename: dict[str, str] = {spec.event_date_column: "event_date"}
        if spec.knowledge_date_column is not None:
            rename[spec.knowledge_date_column] = "knowledge_date"
        return [rename.get(c, c) for c in spec.id_columns]

    async def upsert(
        self, spec: DatasetSpec, rows: list[dict], symbol: str | None = None
    ) -> int:
        df = self._normalize(spec, rows)
        if df.empty:
            return 0

        path = self._path_for(spec, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df], ignore_index=True)

        id_cols = [c for c in self._id_columns_after_rename(spec) if c in df.columns]
        if id_cols:
            df = df.drop_duplicates(subset=id_cols, keep="last")

        df = df.sort_values(["event_date", "knowledge_date"]).reset_index(drop=True)

        # Atomic write via temp file + os.replace
        tmp = path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, compression="zstd")
        os.replace(tmp, path)

        if path.stat().st_size > _WARN_SIZE_BYTES:
            _LOG.warning("%s exceeded 500 MB; consider partitioning", path)

        return len(df)


# ---------------------------------------------------------------------------
# Module-level singleton + query helper
# ---------------------------------------------------------------------------

_default_service: "DatasetService | None" = None


def set_default_service(svc: "DatasetService | None") -> None:
    """Wire the singleton at app startup so module-level helpers know where to read from."""
    global _default_service
    _default_service = svc


def _get_service() -> "DatasetService":
    if _default_service is None:
        raise RuntimeError(
            "DatasetService not configured; call set_default_service() at startup"
        )
    return _default_service


def _empty_frame_for(spec: DatasetSpec) -> pd.DataFrame:
    bitemp_cols = ["event_date", "knowledge_date"]
    extra = [c for c in spec.columns if c not in (spec.event_date_column, spec.knowledge_date_column)]
    return pd.DataFrame(columns=bitemp_cols + extra)


def _to_naive_utc_ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def load_dataset(
    name: str,
    *,
    as_of: datetime,                        # required keyword — no default
    symbol: str | None = None,
    start: date | None = None,
    end: date | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Read a bitemporal dataset and apply the forward-bias filter.

    The ``knowledge_date <= as_of`` filter is the framework's single chokepoint
    for forward-bias prevention. ``as_of`` is a required keyword — never default
    to "now".
    """
    spec = _registry.get(name)
    svc = _get_service()
    path = svc._path_for(spec, symbol)
    if not path.exists():
        return _empty_frame_for(spec)
    df = pd.read_parquet(path, columns=columns)
    df = df[df["knowledge_date"] <= _to_naive_utc_ts(as_of)]
    if start is not None:
        df = df[df["event_date"] >= _to_naive_utc_ts(start)]
    if end is not None:
        df = df[df["event_date"] <= _to_naive_utc_ts(end)]
    return df.sort_values(["event_date", "knowledge_date"]).reset_index(drop=True)
