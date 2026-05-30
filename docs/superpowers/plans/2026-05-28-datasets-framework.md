# Time-Series Datasets Framework (FMP first) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a generic bitemporal time-series datasets framework to quilt-trader, with FMP as the first provider and five FMP datasets (House/Senate disclosures, insider trading, income statements, earnings calendar). Algorithms can call `ctx.dataset(name)` in both backtest and live mode with no forward-bias risk.

**Architecture:** New "datasets lane" parallel to the existing bars lane. Shared `DownloadManager` via a new `JobDispatcher` abstraction. New parquet storage under `data/datasets/<provider>/<name>/...`. Single-chokepoint forward-bias filter (`knowledge_date <= as_of`) lives in `load_dataset()`; algorithm-facing `ctx.dataset()` injects `as_of` from the runtime clock — no algorithm-side override.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x async, Alembic, httpx, pandas + pyarrow, FastAPI, pytest + pytest-asyncio + hypothesis, React 18 + Vite + TypeScript + TanStack Table/Query + TailwindCSS.

**Spec:** [`docs/superpowers/specs/2026-05-28-datasets-framework-design.md`](../specs/2026-05-28-datasets-framework-design.md)

---

## File map

### Created
- `coordinator/services/datasets/__init__.py`
- `coordinator/services/datasets/registry.py` — `DatasetSpec`, `Pagination`, `register`, `get`, `list_all`
- `coordinator/services/datasets/storage.py` — `DatasetService` (upsert) + free function `load_dataset`
- `coordinator/services/datasets/quota.py` — `QuotaTracker`, `QuotaExhausted`
- `coordinator/services/datasets/adapter.py` — `DatasetAdapter` ABC + callback type aliases + `AdapterAuthError`
- `coordinator/services/datasets/providers/__init__.py`
- `coordinator/services/datasets/providers/fmp.py` — `FMPAdapter`
- `coordinator/services/datasets/providers/fmp_datasets.py` — registers five `DatasetSpec`s
- `coordinator/services/download_job.py` — `JobDispatcher` ABC, `BarsJobDispatcher`, `DatasetJobDispatcher`
- `coordinator/api/routes/datasets.py` — new REST routes
- `coordinator/database/migrations/versions/<hash>_datasets_framework.py` — Alembic migration
- `tests/coordinator/services/datasets/__init__.py`
- `tests/coordinator/services/datasets/test_registry.py`
- `tests/coordinator/services/datasets/test_storage.py`
- `tests/coordinator/services/datasets/test_forward_bias.py`
- `tests/coordinator/services/datasets/test_quota.py`
- `tests/coordinator/services/datasets/test_adapter_base.py`
- `tests/coordinator/services/datasets/providers/__init__.py`
- `tests/coordinator/services/datasets/providers/test_fmp_adapter.py`
- `tests/coordinator/services/datasets/providers/test_fmp_datasets.py`
- `tests/coordinator/services/test_dataset_dispatcher.py`
- `tests/coordinator/api/test_datasets_routes.py`
- `tests/sdk/test_tick_context_dataset.py`
- `dashboard/src/components/DatasetsAvailableSection.tsx`
- `dashboard/src/components/DatasetPreviewModal.tsx` (new bitemporal one; old market-data one renamed to `MarketDataPreviewModal.tsx`)
- `dashboard/src/components/DatasetsFilterBar.tsx`
- `dashboard/src/hooks/usePagedDatasetRows.ts`
- `dashboard/src/hooks/useDatasetCoverage.ts`

### Modified
- `coordinator/database/models.py` — add `DatasetDownload`, `QuotaUsage`
- `coordinator/services/download_manager.py` — extract bars logic into `BarsJobDispatcher`, add `register_dispatcher`/dispatch loop
- `coordinator/main.py` — lifespan loads FMP settings, constructs `QuotaTracker` + `FMPAdapter`, registers dispatcher
- `coordinator/api/routes/__init__.py` (or wherever routers are mounted) — mount new `datasets` router
- `sdk/context.py` — add `dataset(...)` abstract method to `TickContext`
- `sdk/cli/commands/data.py` — add `datasets` subcommand group
- `coordinator/services/backtest_tick_context.py` — implement `dataset(...)` (inherits but needs cache state); ensure dataset cache survives `reset_for_replay`
- `worker/context.py` — make `LiveTickContext` formally inherit `TickContext`; implement `dataset(...)` with TTL cache
- `dashboard/src/components/AvailableDataTab.tsx` — add `MarketData | Datasets` toggle; rename import
- `dashboard/src/components/DatasetPreviewModal.tsx` → renamed to `MarketDataPreviewModal.tsx` (also update its single caller in `AvailableDataTab.tsx`)
- `dashboard/src/api.ts` (or wherever `api.getCoverage()` lives) — add `listDatasets`, `getDatasetCoverage`, `getDatasetCoverageDetail`, `getDatasetRows`, `listDatasetProviders`

---

## Task 1: `DatasetSpec`, `Pagination`, and registry

**Files:**
- Create: `coordinator/services/datasets/__init__.py` (empty)
- Create: `coordinator/services/datasets/registry.py`
- Create: `tests/coordinator/services/datasets/__init__.py` (empty)
- Create: `tests/coordinator/services/datasets/test_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/coordinator/services/datasets/test_registry.py
import pytest
from coordinator.services.datasets.registry import (
    DatasetSpec, Pagination, register, get, list_all, clear_registry,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_registry()
    yield
    clear_registry()


def _spec(name="fmp.house_disclosures", knowledge_col="disclosureDate"):
    return DatasetSpec(
        name=name,
        provider="fmp",
        endpoint_path="/stable/house-latest",
        event_date_column="transactionDate",
        knowledge_date_column=knowledge_col,
        symbol_keyed=False,
        id_columns=("disclosureDate", "transactionDate", "name", "symbol"),
        columns={"symbol": "str", "transactionDate": "date", "disclosureDate": "date"},
        pagination=Pagination.PAGE,
    )


def test_register_then_get_round_trips():
    s = _spec()
    register(s)
    assert get("fmp.house_disclosures") is s


def test_register_duplicate_raises():
    register(_spec())
    with pytest.raises(ValueError, match="duplicate dataset: fmp.house_disclosures"):
        register(_spec())


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        get("nope.nada")


def test_list_all_returns_all_registered():
    register(_spec("a.one"))
    register(_spec("a.two"))
    assert {s.name for s in list_all()} == {"a.one", "a.two"}


def test_spec_is_frozen():
    s = _spec()
    with pytest.raises(Exception):  # FrozenInstanceError
        s.endpoint_path = "/different"


def test_knowledge_column_can_be_none():
    s = _spec(knowledge_col=None)
    assert s.knowledge_date_column is None


def test_pagination_enum_values():
    assert Pagination.SINGLE == "single"
    assert Pagination.PAGE == "page"
    assert Pagination.DATE_RANGE == "date_range"
```

- [ ] **Step 2: Run tests, confirm they fail**

```
pytest tests/coordinator/services/datasets/test_registry.py -v
```

Expected: `ModuleNotFoundError: No module named 'coordinator.services.datasets'`.

- [ ] **Step 3: Implement registry**

```python
# coordinator/services/datasets/registry.py
from dataclasses import dataclass, field
from enum import StrEnum
from datetime import timedelta


class Pagination(StrEnum):
    SINGLE = "single"
    PAGE = "page"
    DATE_RANGE = "date_range"


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    provider: str
    endpoint_path: str
    event_date_column: str
    knowledge_date_column: str | None
    symbol_keyed: bool
    id_columns: tuple[str, ...]
    columns: dict[str, str] = field(default_factory=dict)
    pagination: Pagination = Pagination.PAGE
    page_size: int = 100
    date_chunk_days: int = 365
    knowledge_date_lag: timedelta = timedelta(0)
    free_tier: bool = True


_REGISTRY: dict[str, DatasetSpec] = {}


def register(spec: DatasetSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"duplicate dataset: {spec.name}")
    _REGISTRY[spec.name] = spec


def get(name: str) -> DatasetSpec:
    return _REGISTRY[name]


def list_all() -> list[DatasetSpec]:
    return list(_REGISTRY.values())


def clear_registry() -> None:
    """Test helper. Do not call from production code."""
    _REGISTRY.clear()
```

Also create the empty `__init__.py` files for the new packages.

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/test_registry.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/__init__.py \
        coordinator/services/datasets/registry.py \
        tests/coordinator/services/datasets/__init__.py \
        tests/coordinator/services/datasets/test_registry.py
git commit -m "feat(datasets): DatasetSpec + Pagination + module-level registry"
```

---

## Task 2: `DatasetService.upsert` with bitemporal normalization

**Files:**
- Create: `coordinator/services/datasets/storage.py`
- Create: `tests/coordinator/services/datasets/test_storage.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/coordinator/services/datasets/test_storage.py
import pytest
import pandas as pd
from pathlib import Path
from coordinator.services.datasets.registry import DatasetSpec, Pagination, register, clear_registry
from coordinator.services.datasets.storage import DatasetService


@pytest.fixture(autouse=True)
def _clean():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def service(tmp_path):
    return DatasetService(data_root=tmp_path)


def _bitemporal_spec(name="fmp.house_disclosures", symbol_keyed=False, knowledge_col="disclosureDate"):
    spec = DatasetSpec(
        name=name, provider="fmp", endpoint_path="/x",
        event_date_column="transactionDate",
        knowledge_date_column=knowledge_col,
        symbol_keyed=symbol_keyed,
        id_columns=("transactionDate", "disclosureDate", "name", "symbol") if knowledge_col
                   else ("date", "symbol"),
        columns={"symbol": "str", "transactionDate": "date", "disclosureDate": "date",
                 "name": "str", "amount": "str"},
        pagination=Pagination.PAGE,
    )
    register(spec)
    return spec


@pytest.mark.asyncio
async def test_upsert_creates_file_with_normalized_columns(service, tmp_path):
    spec = _bitemporal_spec()
    rows = [
        {"transactionDate": "2024-01-15", "disclosureDate": "2024-02-12",
         "symbol": "NVDA", "name": "Pelosi", "amount": "$1M-$5M"},
    ]
    n = await service.upsert(spec, rows)
    assert n == 1
    path = tmp_path / "datasets" / "fmp" / "house_disclosures.parquet"
    assert path.exists()
    df = pd.read_parquet(path)
    assert "event_date" in df.columns
    assert "knowledge_date" in df.columns
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-01-15")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-02-12")
    assert df.iloc[0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_upsert_dedups_by_id_columns_keeping_latest(service):
    spec = _bitemporal_spec()
    base = {"transactionDate": "2024-01-15", "disclosureDate": "2024-02-12",
            "symbol": "NVDA", "name": "Pelosi"}
    await service.upsert(spec, [{**base, "amount": "old"}])
    await service.upsert(spec, [{**base, "amount": "new"}])
    df = pd.read_parquet(spec_path(service, spec))
    assert len(df) == 1
    assert df.iloc[0]["amount"] == "new"


@pytest.mark.asyncio
async def test_upsert_keeps_amendments_as_separate_rows(service):
    spec = _bitemporal_spec()
    base = {"transactionDate": "2024-01-15", "symbol": "NVDA", "name": "Pelosi"}
    await service.upsert(spec, [{**base, "disclosureDate": "2024-02-12", "amount": "$1M"}])
    await service.upsert(spec, [{**base, "disclosureDate": "2024-02-20", "amount": "$2M"}])
    df = pd.read_parquet(spec_path(service, spec))
    assert len(df) == 2


@pytest.mark.asyncio
async def test_upsert_symbol_keyed_writes_per_symbol(service, tmp_path):
    spec = _bitemporal_spec(name="fmp.insider_trading", symbol_keyed=True)
    await service.upsert(spec, [{"transactionDate": "2024-01-01", "disclosureDate": "2024-01-15",
                                  "symbol": "AAPL", "name": "X"}], symbol="AAPL")
    await service.upsert(spec, [{"transactionDate": "2024-01-02", "disclosureDate": "2024-01-16",
                                  "symbol": "NVDA", "name": "Y"}], symbol="NVDA")
    aapl = tmp_path / "datasets" / "fmp" / "insider_trading" / "AAPL.parquet"
    nvda = tmp_path / "datasets" / "fmp" / "insider_trading" / "NVDA.parquet"
    assert aapl.exists() and nvda.exists()


@pytest.mark.asyncio
async def test_upsert_single_timestamp_dataset_copies_event_to_knowledge(service):
    spec = DatasetSpec(
        name="fmp.earnings_calendar", provider="fmp", endpoint_path="/x",
        event_date_column="date", knowledge_date_column=None,
        symbol_keyed=False, id_columns=("date", "symbol"),
        columns={"date": "date", "symbol": "str"}, pagination=Pagination.DATE_RANGE,
    )
    register(spec)
    await service.upsert(spec, [{"date": "2024-03-01", "symbol": "AAPL"}])
    df = pd.read_parquet(spec_path(service, spec))
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-03-01")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-03-01")


@pytest.mark.asyncio
async def test_upsert_schema_evolution_adds_new_column_as_nan(service):
    spec = _bitemporal_spec()
    await service.upsert(spec, [{"transactionDate": "2024-01-15", "disclosureDate": "2024-02-12",
                                  "symbol": "X", "name": "A"}])
    await service.upsert(spec, [{"transactionDate": "2024-01-16", "disclosureDate": "2024-02-13",
                                  "symbol": "X", "name": "B", "amount": "$1M",
                                  "newField": "hello"}])
    df = pd.read_parquet(spec_path(service, spec))
    assert "newField" in df.columns
    old_row = df[df["name"] == "A"].iloc[0]
    assert pd.isna(old_row["newField"])


def spec_path(service, spec):
    short = spec.name.split(".", 1)[1]
    if spec.symbol_keyed:
        return service._data_root / "datasets" / spec.provider / short  # caller picks symbol file
    return service._data_root / "datasets" / spec.provider / f"{short}.parquet"
```

- [ ] **Step 2: Run tests, confirm they fail**

```
pytest tests/coordinator/services/datasets/test_storage.py -v
```

Expected: `ImportError: cannot import name 'DatasetService'`.

- [ ] **Step 3: Implement `DatasetService`**

```python
# coordinator/services/datasets/storage.py
from __future__ import annotations
from pathlib import Path
import os
import pandas as pd
from coordinator.services.datasets.registry import DatasetSpec


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
        # Rename bitemporal columns into uniform names
        rename = {spec.event_date_column: "event_date"}
        if spec.knowledge_date_column is not None:
            rename[spec.knowledge_date_column] = "knowledge_date"
        df = df.rename(columns=rename)
        # Parse to UTC timestamps
        df["event_date"] = pd.to_datetime(df["event_date"], utc=True, errors="coerce")
        if "knowledge_date" in df.columns:
            df["knowledge_date"] = pd.to_datetime(df["knowledge_date"], utc=True, errors="coerce")
        else:
            # Single-timestamp dataset: knowledge equals event (+ optional lag)
            df["knowledge_date"] = df["event_date"] + spec.knowledge_date_lag
        return df

    def _id_columns_after_rename(self, spec: DatasetSpec) -> list[str]:
        """spec.id_columns is defined against the raw API field names; translate to post-rename."""
        rename = {spec.event_date_column: "event_date"}
        if spec.knowledge_date_column is not None:
            rename[spec.knowledge_date_column] = "knowledge_date"
        return [rename.get(c, c) for c in spec.id_columns]

    async def upsert(self, spec: DatasetSpec, rows: list[dict], symbol: str | None = None) -> int:
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
        # Atomic write
        tmp = path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, compression="zstd")
        os.replace(tmp, path)
        # Warn on large files
        if path.stat().st_size > 500 * 1024 * 1024:
            import logging
            logging.getLogger(__name__).warning(
                "%s exceeded 500MB; consider partitioning", path
            )
        return len(df)
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/test_storage.py -v
```

Expected: 6 passed. If a `pytest.ini` / `pyproject.toml` doesn't already enable async mode for asyncio tests, add `pytest_plugins = ["pytest_asyncio"]` to the conftest or set `asyncio_mode = "auto"` in pyproject — match existing test conventions.

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/storage.py \
        tests/coordinator/services/datasets/test_storage.py
git commit -m "feat(datasets): DatasetService.upsert with bitemporal normalization + dedup"
```

---

## Task 3: `load_dataset()` query helper with bitemporal filter

**Files:**
- Modify: `coordinator/services/datasets/storage.py` (add free function)
- Modify: `tests/coordinator/services/datasets/test_storage.py` (add query tests)

- [ ] **Step 1: Add failing tests to `test_storage.py`**

```python
# Append to tests/coordinator/services/datasets/test_storage.py
import pytest
from datetime import datetime, timezone
from coordinator.services.datasets.storage import load_dataset, set_default_service


@pytest.fixture
def configured_service(service):
    set_default_service(service)
    yield service
    set_default_service(None)


@pytest.mark.asyncio
async def test_load_dataset_requires_as_of_keyword(configured_service):
    _bitemporal_spec()
    with pytest.raises(TypeError):
        load_dataset("fmp.house_disclosures")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_load_dataset_filters_knowledge_after_as_of(configured_service):
    spec = _bitemporal_spec()
    await configured_service.upsert(spec, [
        {"transactionDate": "2024-01-15", "disclosureDate": "2024-02-12",
         "symbol": "NVDA", "name": "P"},  # visible at as_of=2024-03-01
        {"transactionDate": "2024-03-01", "disclosureDate": "2024-04-01",
         "symbol": "TSLA", "name": "G"},  # hidden at as_of=2024-03-01
    ])
    df = load_dataset("fmp.house_disclosures", as_of=datetime(2024, 3, 1, tzinfo=timezone.utc))
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_load_dataset_returns_empty_when_path_missing(configured_service):
    _bitemporal_spec()
    df = load_dataset("fmp.house_disclosures", as_of=datetime(2024, 3, 1, tzinfo=timezone.utc))
    assert df.empty


@pytest.mark.asyncio
async def test_load_dataset_applies_event_date_window(configured_service):
    spec = _bitemporal_spec()
    await configured_service.upsert(spec, [
        {"transactionDate": "2023-12-01", "disclosureDate": "2023-12-15",
         "symbol": "A", "name": "X"},
        {"transactionDate": "2024-01-15", "disclosureDate": "2024-01-30",
         "symbol": "B", "name": "Y"},
        {"transactionDate": "2024-06-01", "disclosureDate": "2024-06-15",
         "symbol": "C", "name": "Z"},
    ])
    df = load_dataset(
        "fmp.house_disclosures",
        as_of=datetime(2024, 12, 31, tzinfo=timezone.utc),
        start=pd.Timestamp("2024-01-01").date(),
        end=pd.Timestamp("2024-05-31").date(),
    )
    assert list(df["symbol"]) == ["B"]


@pytest.mark.asyncio
async def test_load_dataset_symbol_keyed_reads_correct_file(configured_service, tmp_path):
    spec = _bitemporal_spec(name="fmp.insider_trading", symbol_keyed=True)
    await configured_service.upsert(spec, [
        {"transactionDate": "2024-01-01", "disclosureDate": "2024-01-15",
         "symbol": "AAPL", "name": "X"}
    ], symbol="AAPL")
    df = load_dataset("fmp.insider_trading", as_of=datetime(2024, 12, 31, tzinfo=timezone.utc),
                      symbol="AAPL")
    assert len(df) == 1
```

- [ ] **Step 2: Run tests, confirm they fail**

```
pytest tests/coordinator/services/datasets/test_storage.py::test_load_dataset_requires_as_of_keyword -v
```

Expected: ImportError / NameError on `load_dataset`.

- [ ] **Step 3: Add `load_dataset` to `storage.py`**

```python
# Append to coordinator/services/datasets/storage.py
from datetime import date, datetime
from coordinator.services.datasets import registry as _registry

_default_service: DatasetService | None = None


def set_default_service(svc: DatasetService | None) -> None:
    """Wire the singleton at app startup so module-level helpers know where to read from."""
    global _default_service
    _default_service = svc


def _get_service() -> DatasetService:
    if _default_service is None:
        raise RuntimeError("DatasetService not configured; call set_default_service() at startup")
    return _default_service


def _empty_frame_for(spec) -> pd.DataFrame:
    cols = ["event_date", "knowledge_date"] + [c for c in spec.columns
                                                if c not in (spec.event_date_column,
                                                             spec.knowledge_date_column)]
    return pd.DataFrame(columns=cols)


def load_dataset(
    name: str,
    *,
    as_of: datetime,
    symbol: str | None = None,
    start: date | None = None,
    end: date | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Read a bitemporal dataset and apply the forward-bias filter.

    The `knowledge_date <= as_of` filter is the framework's single chokepoint for
    forward-bias prevention. `as_of` is a required keyword — never default to "now".
    """
    spec = _registry.get(name)
    svc = _get_service()
    path = svc._path_for(spec, symbol)
    if not path.exists():
        return _empty_frame_for(spec)
    df = pd.read_parquet(path, columns=columns)
    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.tz_localize("UTC")
    df = df[df["knowledge_date"] <= as_of_ts]
    if start is not None:
        df = df[df["event_date"] >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df["event_date"] <= pd.Timestamp(end, tz="UTC")]
    return df.sort_values(["event_date", "knowledge_date"]).reset_index(drop=True)
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/test_storage.py -v
```

Expected: all 11 storage tests pass.

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/storage.py \
        tests/coordinator/services/datasets/test_storage.py
git commit -m "feat(datasets): load_dataset() with mandatory bitemporal as_of filter"
```

---

## Task 4: Hypothesis property test for forward-bias prevention

**Files:**
- Create: `tests/coordinator/services/datasets/test_forward_bias.py`

This is the safety-critical test. If it ever fails, the framework's whole premise is broken.

- [ ] **Step 1: Write the property test**

```python
# tests/coordinator/services/datasets/test_forward_bias.py
from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest
from hypothesis import given, strategies as st, settings as hsettings

from coordinator.services.datasets.registry import (
    DatasetSpec, Pagination, register, clear_registry,
)
from coordinator.services.datasets.storage import (
    DatasetService, load_dataset, set_default_service,
)


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    clear_registry()
    svc = DatasetService(data_root=tmp_path)
    set_default_service(svc)
    register(DatasetSpec(
        name="test.fixture", provider="test", endpoint_path="/x",
        event_date_column="ev", knowledge_date_column="kn",
        symbol_keyed=False, id_columns=("ev", "kn", "id"),
        columns={"ev": "date", "kn": "date", "id": "str"}, pagination=Pagination.PAGE,
    ))
    yield svc
    clear_registry()
    set_default_service(None)


_dates = st.dates(min_value=pd.Timestamp("2000-01-01").date(),
                  max_value=pd.Timestamp("2030-12-31").date())


@given(
    rows=st.lists(
        st.tuples(_dates, _dates, st.text(min_size=1, max_size=8)),
        min_size=0, max_size=100,
    ),
    as_of_date=_dates,
)
@hsettings(max_examples=200, deadline=None)
@pytest.mark.asyncio
async def test_load_dataset_never_returns_future_knowledge(_clean, rows, as_of_date):
    svc = _clean
    payload = [{"ev": str(ev), "kn": str(kn), "id": f"{i}-{tag}"}
               for i, (ev, kn, tag) in enumerate(rows)]
    await svc.upsert(register_or_get(), payload) if False else None  # placeholder
    # The fixture already registered the spec; just upsert against it.
    from coordinator.services.datasets.registry import get as _get
    await svc.upsert(_get("test.fixture"), payload)
    as_of_ts = pd.Timestamp(as_of_date, tz="UTC")
    df = load_dataset("test.fixture", as_of=as_of_ts.to_pydatetime())
    if not df.empty:
        assert (df["knowledge_date"] <= as_of_ts).all()


def test_load_dataset_without_as_of_raises_type_error():
    with pytest.raises(TypeError):
        load_dataset("test.fixture")  # type: ignore[call-arg]
```

- [ ] **Step 2: Run test to confirm it actually exercises the path**

```
pytest tests/coordinator/services/datasets/test_forward_bias.py -v
```

Expected: 2 passed (the property test should complete its 200 examples without finding a counterexample; the `TypeError` test passes immediately).

- [ ] **Step 3: Commit**

```
git add tests/coordinator/services/datasets/test_forward_bias.py
git commit -m "test(datasets): hypothesis property test pins forward-bias chokepoint"
```

---

## Task 5: `DatasetDownload` + `QuotaUsage` SQLAlchemy models + Alembic migration

**Files:**
- Modify: `coordinator/database/models.py`
- Create: `coordinator/database/migrations/versions/<hash>_datasets_framework.py` (via autogenerate)

- [ ] **Step 1: Add model classes to `models.py`**

Locate the section of `models.py` where existing job-style models live (`MarketDataDownload`, `ResearchJob`). Add after `ResearchJob` to keep async-job conventions adjacent. Read the existing column / type / mixin conventions first (`Mapped[…]`, `mapped_column(...)`, `server_default=func.now()`, JSON column type) and match exactly.

```python
# Append to coordinator/database/models.py
from sqlalchemy import UniqueConstraint, JSON


class DatasetDownload(Base):
    __tablename__ = "dataset_downloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    queued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    rows_fetched: Mapped[int] = mapped_column(default=0)
    calls_consumed: Mapped[int] = mapped_column(default=0)

    progress_pct: Mapped[float] = mapped_column(default=0.0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_page: Mapped[int] = mapped_column(default=0)
    last_event_date: Mapped[datetime | None] = mapped_column(nullable=True)

    created_by: Mapped[str] = mapped_column(String(32), default="manual")


class QuotaUsage(Base):
    __tablename__ = "quota_usage"
    __table_args__ = (UniqueConstraint("provider", "reset_window", name="uq_quota_window"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    reset_window: Mapped[date] = mapped_column(index=True)
    calls_used: Mapped[int] = mapped_column(default=0)
    daily_limit: Mapped[int]
    exhausted: Mapped[bool] = mapped_column(default=False)
```

If the existing models in the file use different column constructor patterns (e.g. legacy `Column(...)` style), match those instead. The above assumes 2.x `Mapped[...]` typed columns — verify by reading the existing `MarketDataDownload` or `ResearchJob` definitions.

- [ ] **Step 2: Generate Alembic migration**

```
alembic revision --autogenerate -m "datasets framework: dataset_downloads + quota_usage"
```

Inspect the generated file under `coordinator/database/migrations/versions/<hash>_datasets_framework*.py`. It should `op.create_table("dataset_downloads", ...)` and `op.create_table("quota_usage", ...)` with the unique constraint. Edit out any spurious changes autogenerate may have included from drift (e.g. type alterations on unrelated tables — never apply those unless they're intentional and reviewed).

- [ ] **Step 3: Run migration against a test DB**

```
alembic upgrade head
```

Expected: clean upgrade, no errors. If your test setup uses an in-memory SQLite that recreates schema from `Base.metadata`, also confirm `Base.metadata.create_all()` produces a matching schema.

- [ ] **Step 4: Smoke test the model with a quick insert**

```
python -c "
import asyncio
from coordinator.database.session import async_session_factory
from coordinator.database.models import DatasetDownload

async def main():
    async with async_session_factory() as s:
        d = DatasetDownload(dataset_name='fmp.house_disclosures', provider='fmp',
                            request_payload={'symbol': None})
        s.add(d); await s.commit()
        print('inserted id:', d.id)
asyncio.run(main())
"
```

Expected: prints `inserted id: 1` (or next available).

- [ ] **Step 5: Commit**

```
git add coordinator/database/models.py \
        coordinator/database/migrations/versions/*_datasets_framework*.py
git commit -m "feat(db): DatasetDownload + QuotaUsage models + migration"
```

---

## Task 6: `QuotaTracker` with reset semantics + 429 escalation

**Files:**
- Create: `coordinator/services/datasets/quota.py`
- Create: `tests/coordinator/services/datasets/test_quota.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/coordinator/services/datasets/test_quota.py
import asyncio
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
import pytest
from coordinator.services.datasets.quota import QuotaTracker, QuotaExhausted
from coordinator.database.models import QuotaUsage
from sqlalchemy import select


@pytest.fixture
async def tracker(db_session_factory):  # use whatever existing fixture provides a session factory
    return QuotaTracker(db_session_factory, reset_tz=timezone.utc)


@pytest.mark.asyncio
async def test_acquire_increments_counter(tracker, db_session_factory):
    await tracker.acquire("fmp", daily_limit=3)
    async with db_session_factory() as s:
        row = (await s.execute(select(QuotaUsage).where(QuotaUsage.provider == "fmp"))).scalar_one()
        assert row.calls_used == 1


@pytest.mark.asyncio
async def test_acquire_raises_at_limit(tracker):
    for _ in range(3):
        await tracker.acquire("fmp", daily_limit=3)
    with pytest.raises(QuotaExhausted):
        await tracker.acquire("fmp", daily_limit=3)


@pytest.mark.asyncio
async def test_mark_exhausted_blocks_further_acquire(tracker):
    await tracker.acquire("fmp", daily_limit=100)
    await tracker.mark_exhausted("fmp")
    with pytest.raises(QuotaExhausted):
        await tracker.acquire("fmp", daily_limit=100)


@pytest.mark.asyncio
async def test_remaining_reflects_count_and_flag(tracker):
    await tracker.acquire("fmp", daily_limit=10)
    assert await tracker.remaining("fmp", daily_limit=10) == 9
    await tracker.mark_exhausted("fmp")
    assert await tracker.remaining("fmp", daily_limit=10) == 0


@pytest.mark.asyncio
async def test_concurrent_acquires_never_overshoot(tracker):
    async def one():
        try:
            await tracker.acquire("fmp", daily_limit=10)
            return True
        except QuotaExhausted:
            return False
    results = await asyncio.gather(*[one() for _ in range(100)])
    assert sum(results) == 10


@pytest.mark.asyncio
async def test_new_window_creates_fresh_counter(db_session_factory):
    # Build tracker that we can manipulate "today" via injection.
    tracker = QuotaTracker(db_session_factory, reset_tz=timezone.utc)
    # Manually insert a "yesterday" row at-limit
    async with db_session_factory() as s:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        s.add(QuotaUsage(provider="fmp", reset_window=yesterday, calls_used=10, daily_limit=10))
        await s.commit()
    # Today's acquire should succeed (new row)
    await tracker.acquire("fmp", daily_limit=10)
    async with db_session_factory() as s:
        rows = (await s.execute(select(QuotaUsage).where(QuotaUsage.provider == "fmp"))).scalars().all()
        assert len(rows) == 2
```

The `db_session_factory` fixture should match whatever existing tests use to get an async session factory (look at e.g. `tests/coordinator/test_data_api.py` or `tests/coordinator/services/test_research_job_manager.py` for the pattern). If no shared fixture exists yet, add one to `tests/coordinator/conftest.py`.

- [ ] **Step 2: Run tests, confirm they fail**

```
pytest tests/coordinator/services/datasets/test_quota.py -v
```

Expected: `ImportError` on QuotaTracker.

- [ ] **Step 3: Implement `QuotaTracker`**

```python
# coordinator/services/datasets/quota.py
from __future__ import annotations
import asyncio
from collections import defaultdict
from datetime import datetime, date, tzinfo, timezone
from sqlalchemy import select
from coordinator.database.models import QuotaUsage


class QuotaExhausted(Exception):
    def __init__(self, provider: str, used: int, limit: int):
        super().__init__(f"{provider} quota exhausted: {used}/{limit}")
        self.provider = provider
        self.used = used
        self.limit = limit


class QuotaTracker:
    def __init__(self, session_factory, reset_tz: tzinfo = timezone.utc):
        self._sf = session_factory
        self._tz = reset_tz
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _current_window(self) -> date:
        return datetime.now(self._tz).date()

    async def _get_or_create(self, session, provider: str, daily_limit: int) -> QuotaUsage:
        window = self._current_window()
        row = (await session.execute(
            select(QuotaUsage).where(
                QuotaUsage.provider == provider,
                QuotaUsage.reset_window == window,
            )
        )).scalar_one_or_none()
        if row is None:
            row = QuotaUsage(provider=provider, reset_window=window,
                             calls_used=0, daily_limit=daily_limit, exhausted=False)
            session.add(row)
            await session.flush()  # populate id
        return row

    async def acquire(self, provider: str, daily_limit: int) -> None:
        async with self._locks[provider]:
            async with self._sf() as session:
                row = await self._get_or_create(session, provider, daily_limit)
                if row.exhausted or row.calls_used >= row.daily_limit:
                    raise QuotaExhausted(provider, row.calls_used, row.daily_limit)
                row.calls_used += 1
                await session.commit()

    async def mark_exhausted(self, provider: str) -> None:
        async with self._locks[provider]:
            async with self._sf() as session:
                row = await self._get_or_create(session, provider, daily_limit=0)
                row.exhausted = True
                await session.commit()

    async def remaining(self, provider: str, daily_limit: int) -> int:
        async with self._sf() as session:
            row = await self._get_or_create(session, provider, daily_limit)
            await session.commit()
            if row.exhausted:
                return 0
            return max(0, row.daily_limit - row.calls_used)
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/test_quota.py -v
```

Expected: 6 passed. The concurrent test verifies the per-provider lock works.

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/quota.py \
        tests/coordinator/services/datasets/test_quota.py
git commit -m "feat(datasets): QuotaTracker with DB-backed daily counter + 429 escalation"
```

---

## Task 7: `DatasetAdapter` ABC + callback types + auth error

**Files:**
- Create: `coordinator/services/datasets/adapter.py`
- Create: `tests/coordinator/services/datasets/test_adapter_base.py`

- [ ] **Step 1: Write the contract test**

```python
# tests/coordinator/services/datasets/test_adapter_base.py
import pytest
from coordinator.services.datasets.adapter import (
    DatasetAdapter, AdapterAuthError, PageCallback, StatusCallback, RowsCallback,
)


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        DatasetAdapter()  # type: ignore[abstract]


def test_adapter_auth_error_has_message():
    e = AdapterAuthError("nope")
    assert str(e) == "nope"


def test_callback_types_exist():
    # Type aliases — just import-time validation
    assert PageCallback is not None
    assert StatusCallback is not None
    assert RowsCallback is not None


def test_subclass_must_implement_fetch_dataset():
    class Bad(DatasetAdapter):
        provider = "x"
    with pytest.raises(TypeError):
        Bad()  # type: ignore[abstract]


def test_subclass_implementing_fetch_dataset_works():
    class Good(DatasetAdapter):
        provider = "x"
        async def fetch_dataset(self, spec, params, *, on_page=None, on_status=None, on_rows=None):
            return []
    g = Good()
    assert g.provider == "x"
```

- [ ] **Step 2: Run tests, confirm they fail**

```
pytest tests/coordinator/services/datasets/test_adapter_base.py -v
```

Expected: ImportError on `coordinator.services.datasets.adapter`.

- [ ] **Step 3: Implement the ABC**

```python
# coordinator/services/datasets/adapter.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Any
from coordinator.services.datasets.registry import DatasetSpec


PageCallback   = Callable[[int, int], Awaitable[None]]                  # (page_idx, cumulative_rows)
StatusCallback = Callable[[str], Awaitable[None]]                       # (human-readable message)
RowsCallback   = Callable[[list[dict], int], Awaitable[None]]           # (rows, page_idx)


class AdapterAuthError(Exception):
    """Raised when an adapter's credentials are rejected (e.g. HTTP 401)."""


class DatasetAdapter(ABC):
    provider: str  # class attribute, e.g. "fmp"

    @abstractmethod
    async def fetch_dataset(
        self,
        spec: DatasetSpec,
        params: dict,
        *,
        on_page: PageCallback | None = None,
        on_status: StatusCallback | None = None,
        on_rows: RowsCallback | None = None,
    ) -> list[dict]:
        ...
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/test_adapter_base.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/adapter.py \
        tests/coordinator/services/datasets/test_adapter_base.py
git commit -m "feat(datasets): DatasetAdapter ABC + callback type aliases"
```

---

## Task 8: `FMPAdapter` scaffolding — auth + `_request` + pacing + 429/401

**Files:**
- Create: `coordinator/services/datasets/providers/__init__.py` (empty)
- Create: `coordinator/services/datasets/providers/fmp.py`
- Create: `tests/coordinator/services/datasets/providers/__init__.py` (empty)
- Create: `tests/coordinator/services/datasets/providers/test_fmp_adapter.py`

- [ ] **Step 1: Write failing tests for `_request`**

```python
# tests/coordinator/services/datasets/providers/test_fmp_adapter.py
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock
from coordinator.services.datasets.providers.fmp import FMPAdapter
from coordinator.services.datasets.quota import QuotaTracker, QuotaExhausted
from coordinator.services.datasets.adapter import AdapterAuthError


def _resp(status: int, json_body=None, body_text: str | None = None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=json_body or [])
    r.text = body_text or ""
    r.raise_for_status = MagicMock()
    if status >= 400:
        from httpx import HTTPStatusError, Request, Response
        r.raise_for_status.side_effect = HTTPStatusError(
            "err", request=Request("GET", "http://x"), response=Response(status))
    return r


@pytest.fixture
def quota_ok():
    q = MagicMock()
    q.acquire = AsyncMock(return_value=None)
    q.mark_exhausted = AsyncMock(return_value=None)
    return q


@pytest.fixture
def http():
    h = MagicMock()
    h.get = AsyncMock()
    return h


@pytest.fixture
def adapter(quota_ok, http):
    return FMPAdapter(api_key="K", http_client=http, quota_tracker=quota_ok,
                      daily_limit=250, min_request_interval_s=0.0)


@pytest.mark.asyncio
async def test_request_appends_apikey_query_param(adapter, http):
    http.get.return_value = _resp(200, json_body=[])
    await adapter._request("/stable/something", {"page": 0})
    args, kwargs = http.get.call_args
    assert kwargs["params"] == {"page": 0, "apikey": "K"}
    assert args[0] == "https://financialmodelingprep.com/stable/something"


@pytest.mark.asyncio
async def test_request_acquires_quota_before_calling(adapter, http, quota_ok):
    http.get.return_value = _resp(200, json_body=[])
    await adapter._request("/x", {})
    quota_ok.acquire.assert_awaited_once_with("fmp", 250)


@pytest.mark.asyncio
async def test_429_marks_exhausted_and_raises(adapter, http, quota_ok):
    http.get.return_value = _resp(429)
    with pytest.raises(QuotaExhausted):
        await adapter._request("/x", {})
    quota_ok.mark_exhausted.assert_awaited_once_with("fmp")


@pytest.mark.asyncio
async def test_401_raises_adapter_auth_error(adapter, http):
    http.get.return_value = _resp(401)
    with pytest.raises(AdapterAuthError):
        await adapter._request("/x", {})


@pytest.mark.asyncio
async def test_pacing_enforces_minimum_interval(quota_ok, http):
    http.get.return_value = _resp(200, json_body=[])
    a = FMPAdapter(api_key="K", http_client=http, quota_tracker=quota_ok,
                   daily_limit=250, min_request_interval_s=0.1)
    t0 = time.monotonic()
    await a._request("/x", {})
    await a._request("/x", {})
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.1


@pytest.mark.asyncio
async def test_acquire_raising_short_circuits_http(adapter, http, quota_ok):
    quota_ok.acquire.side_effect = QuotaExhausted("fmp", 250, 250)
    with pytest.raises(QuotaExhausted):
        await adapter._request("/x", {})
    http.get.assert_not_awaited()
```

- [ ] **Step 2: Run tests, confirm they fail**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_adapter.py -v
```

Expected: ImportError on FMPAdapter.

- [ ] **Step 3: Implement scaffolding**

```python
# coordinator/services/datasets/providers/fmp.py
from __future__ import annotations
import asyncio
import time
from typing import Any
from coordinator.services.datasets.adapter import (
    DatasetAdapter, AdapterAuthError, PageCallback, StatusCallback, RowsCallback,
)
from coordinator.services.datasets.quota import QuotaTracker, QuotaExhausted
from coordinator.services.datasets.registry import DatasetSpec, Pagination


class FMPAdapter(DatasetAdapter):
    provider = "fmp"
    BASE_URL = "https://financialmodelingprep.com"

    def __init__(
        self,
        api_key: str,
        http_client: Any,
        quota_tracker: QuotaTracker,
        daily_limit: int = 250,
        min_request_interval_s: float = 0.0,
    ):
        self._api_key = api_key
        self._http = http_client
        self._quota = quota_tracker
        self._daily_limit = daily_limit
        self._min_interval = min_request_interval_s
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def fetch_dataset(self, spec, params, *, on_page=None, on_status=None, on_rows=None):
        raise NotImplementedError("pagination dispatch added in later tasks")

    async def _request(self, endpoint_path: str, params: dict) -> Any:
        async with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            await self._quota.acquire(self.provider, self._daily_limit)
            url = f"{self.BASE_URL}{endpoint_path}"
            qs = {**params, "apikey": self._api_key}
            resp = await self._http.get(url, params=qs, timeout=30.0)
            self._last_call = time.monotonic()

        if resp.status_code == 429:
            await self._quota.mark_exhausted(self.provider)
            raise QuotaExhausted(self.provider, -1, self._daily_limit)
        if resp.status_code == 401:
            raise AdapterAuthError("FMP API key rejected")
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_adapter.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/providers/__init__.py \
        coordinator/services/datasets/providers/fmp.py \
        tests/coordinator/services/datasets/providers/__init__.py \
        tests/coordinator/services/datasets/providers/test_fmp_adapter.py
git commit -m "feat(datasets/fmp): adapter scaffolding — auth, pacing, 429/401 handling"
```

---

## Task 9: FMPAdapter PAGE pagination

**Files:**
- Modify: `coordinator/services/datasets/providers/fmp.py`
- Modify: `tests/coordinator/services/datasets/providers/test_fmp_adapter.py`

- [ ] **Step 1: Add failing PAGE tests**

```python
# Append to tests/coordinator/services/datasets/providers/test_fmp_adapter.py
from coordinator.services.datasets.registry import DatasetSpec, Pagination


def _page_spec():
    return DatasetSpec(
        name="fmp.t_page", provider="fmp", endpoint_path="/stable/page-thing",
        event_date_column="d", knowledge_date_column="d",
        symbol_keyed=False, id_columns=("d", "x"),
        columns={"d": "date", "x": "int"}, pagination=Pagination.PAGE, page_size=2,
    )


@pytest.mark.asyncio
async def test_page_pagination_terminates_on_empty(adapter, http):
    http.get.side_effect = [
        _resp(200, [{"d": "2024-01-01", "x": 1}, {"d": "2024-01-02", "x": 2}]),
        _resp(200, [{"d": "2024-01-03", "x": 3}]),
        _resp(200, []),
    ]
    rows = await adapter.fetch_dataset(_page_spec(), {})
    assert len(rows) == 3
    assert http.get.await_count == 3


@pytest.mark.asyncio
async def test_page_pagination_invokes_on_rows_per_page(adapter, http):
    http.get.side_effect = [
        _resp(200, [{"d": "2024-01-01", "x": 1}]),
        _resp(200, []),
    ]
    seen = []
    async def on_rows(rows, page_idx): seen.append((page_idx, len(rows)))
    await adapter.fetch_dataset(_page_spec(), {}, on_rows=on_rows)
    assert seen == [(0, 1)]


@pytest.mark.asyncio
async def test_page_pagination_passes_page_and_limit(adapter, http):
    http.get.side_effect = [_resp(200, [])]
    await adapter.fetch_dataset(_page_spec(), {"symbol": "AAPL"})
    args, kwargs = http.get.call_args_list[0]
    assert kwargs["params"]["page"] == 0
    assert kwargs["params"]["limit"] == 2
    assert kwargs["params"]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_page_pagination_invokes_on_page_after_each(adapter, http):
    http.get.side_effect = [
        _resp(200, [{"d": "2024-01-01", "x": 1}]),
        _resp(200, [{"d": "2024-01-02", "x": 2}]),
        _resp(200, []),
    ]
    pages = []
    async def on_page(idx, total): pages.append((idx, total))
    await adapter.fetch_dataset(_page_spec(), {}, on_page=on_page)
    assert pages == [(0, 1), (1, 2)]
```

- [ ] **Step 2: Run tests, confirm they fail**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_adapter.py -v -k "page"
```

Expected: `NotImplementedError` from the placeholder `fetch_dataset`.

- [ ] **Step 3: Implement dispatch + `_fetch_paged`**

```python
# Modify coordinator/services/datasets/providers/fmp.py — replace fetch_dataset and add _fetch_paged
    async def fetch_dataset(self, spec, params, *, on_page=None, on_status=None, on_rows=None):
        if spec.pagination == Pagination.PAGE:
            return await self._fetch_paged(spec, params, on_page, on_status, on_rows)
        raise NotImplementedError(f"pagination={spec.pagination}")

    async def _fetch_paged(self, spec, params, on_page, on_status, on_rows):
        all_rows: list[dict] = []
        page = int(params.pop("_start_page", 0))
        while True:
            page_rows = await self._request(
                spec.endpoint_path,
                {**params, "page": page, "limit": spec.page_size},
            )
            if not page_rows:
                break
            if on_rows is not None:
                await on_rows(page_rows, page)
            all_rows.extend(page_rows)
            if on_page is not None:
                await on_page(page, len(all_rows))
            page += 1
        return all_rows
```

Re-export `Pagination` from `fmp.py` if needed (`from coordinator.services.datasets.registry import Pagination`) — adjust the import.

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_adapter.py -v
```

Expected: all FMP adapter tests pass (10 total now).

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/providers/fmp.py \
        tests/coordinator/services/datasets/providers/test_fmp_adapter.py
git commit -m "feat(datasets/fmp): PAGE pagination with on_rows interruption safety"
```

---

## Task 10: FMPAdapter SINGLE pagination (with legacy `{historical: […]}` unwrap)

**Files:**
- Modify: `coordinator/services/datasets/providers/fmp.py`
- Modify: `tests/coordinator/services/datasets/providers/test_fmp_adapter.py`

- [ ] **Step 1: Write tests**

```python
# Append to test_fmp_adapter.py
def _single_spec():
    return DatasetSpec(
        name="fmp.t_single", provider="fmp", endpoint_path="/api/v3/single-thing",
        event_date_column="d", knowledge_date_column="d",
        symbol_keyed=True, id_columns=("d",),
        columns={"d": "date"}, pagination=Pagination.SINGLE,
    )


@pytest.mark.asyncio
async def test_single_returns_flat_array(adapter, http):
    http.get.return_value = _resp(200, [{"d": "2024-01-01"}, {"d": "2024-01-02"}])
    rows = await adapter.fetch_dataset(_single_spec(), {"symbol": "AAPL"})
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_single_unwraps_historical_key(adapter, http):
    http.get.return_value = _resp(200, {"symbol": "AAPL", "historical": [{"d": "2024-01-01"}]})
    rows = await adapter.fetch_dataset(_single_spec(), {"symbol": "AAPL"})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_single_invokes_on_rows_once(adapter, http):
    http.get.return_value = _resp(200, [{"d": "2024-01-01"}])
    seen = []
    async def on_rows(rows, page_idx): seen.append((page_idx, len(rows)))
    await adapter.fetch_dataset(_single_spec(), {"symbol": "AAPL"}, on_rows=on_rows)
    assert seen == [(0, 1)]
```

- [ ] **Step 2: Confirm tests fail (`NotImplementedError`)**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_adapter.py -v -k "single"
```

- [ ] **Step 3: Add `_fetch_single` + dispatch case**

```python
# Modify fmp.py — add to dispatch and add helper
    async def fetch_dataset(self, spec, params, *, on_page=None, on_status=None, on_rows=None):
        if spec.pagination == Pagination.PAGE:
            return await self._fetch_paged(spec, params, on_page, on_status, on_rows)
        if spec.pagination == Pagination.SINGLE:
            return await self._fetch_single(spec, params, on_status, on_rows)
        raise NotImplementedError(f"pagination={spec.pagination}")

    async def _fetch_single(self, spec, params, on_status, on_rows):
        payload = await self._request(spec.endpoint_path, params)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            # FMP legacy v3 wraps under "historical"; some endpoints under "data"
            rows = payload.get("historical") or payload.get("data") or []
        else:
            rows = []
        if on_rows is not None and rows:
            await on_rows(rows, 0)
        return rows
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_adapter.py -v
```

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/providers/fmp.py \
        tests/coordinator/services/datasets/providers/test_fmp_adapter.py
git commit -m "feat(datasets/fmp): SINGLE pagination with legacy {historical: …} unwrap"
```

---

## Task 11: FMPAdapter DATE_RANGE pagination

**Files:**
- Modify: `coordinator/services/datasets/providers/fmp.py`
- Modify: `tests/coordinator/services/datasets/providers/test_fmp_adapter.py`

- [ ] **Step 1: Write tests**

```python
# Append to test_fmp_adapter.py
from datetime import date


def _range_spec():
    return DatasetSpec(
        name="fmp.t_range", provider="fmp", endpoint_path="/stable/calendar",
        event_date_column="date", knowledge_date_column=None,
        symbol_keyed=False, id_columns=("date", "symbol"),
        columns={"date": "date", "symbol": "str"},
        pagination=Pagination.DATE_RANGE, date_chunk_days=30,
    )


@pytest.mark.asyncio
async def test_date_range_chunks_into_windows(adapter, http):
    http.get.side_effect = [
        _resp(200, [{"date": "2024-01-05", "symbol": "AAPL"}]),
        _resp(200, [{"date": "2024-02-10", "symbol": "MSFT"}]),
        _resp(200, []),
    ]
    rows = await adapter.fetch_dataset(_range_spec(), {
        "from": date(2024, 1, 1),
        "to": date(2024, 3, 1),
    })
    assert len(rows) == 2
    # 3 windows: Jan, Feb, partial March
    assert http.get.await_count == 3


@pytest.mark.asyncio
async def test_date_range_requires_from_and_to(adapter, http):
    http.get.return_value = _resp(200, [])
    with pytest.raises(ValueError, match="from.*to"):
        await adapter.fetch_dataset(_range_spec(), {})


@pytest.mark.asyncio
async def test_date_range_invokes_on_rows_per_window(adapter, http):
    http.get.side_effect = [
        _resp(200, [{"date": "2024-01-05", "symbol": "X"}]),
        _resp(200, []),
    ]
    seen = []
    async def on_rows(rows, page_idx): seen.append((page_idx, len(rows)))
    await adapter.fetch_dataset(_range_spec(), {
        "from": date(2024, 1, 1), "to": date(2024, 1, 31),
    }, on_rows=on_rows)
    assert (0, 1) in seen
```

- [ ] **Step 2: Confirm tests fail**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_adapter.py -v -k "range"
```

- [ ] **Step 3: Add `_fetch_date_range`**

```python
# Modify fmp.py — extend dispatch + add helper
from datetime import date, timedelta

    async def fetch_dataset(self, spec, params, *, on_page=None, on_status=None, on_rows=None):
        if spec.pagination == Pagination.PAGE:
            return await self._fetch_paged(spec, params, on_page, on_status, on_rows)
        if spec.pagination == Pagination.SINGLE:
            return await self._fetch_single(spec, params, on_status, on_rows)
        if spec.pagination == Pagination.DATE_RANGE:
            return await self._fetch_date_range(spec, params, on_page, on_status, on_rows)
        raise NotImplementedError(f"pagination={spec.pagination}")

    async def _fetch_date_range(self, spec, params, on_page, on_status, on_rows):
        start = params.pop("from", None)
        end = params.pop("to", None)
        if start is None or end is None:
            raise ValueError("DATE_RANGE pagination requires 'from' and 'to' in params")
        if isinstance(start, str): start = date.fromisoformat(start)
        if isinstance(end, str): end = date.fromisoformat(end)

        all_rows: list[dict] = []
        window_idx = 0
        chunk = timedelta(days=spec.date_chunk_days)
        cursor = start
        while cursor <= end:
            window_end = min(cursor + chunk - timedelta(days=1), end)
            page_rows = await self._request(spec.endpoint_path, {
                **params, "from": cursor.isoformat(), "to": window_end.isoformat(),
            })
            if on_rows is not None and page_rows:
                await on_rows(page_rows, window_idx)
            all_rows.extend(page_rows)
            if on_page is not None:
                await on_page(window_idx, len(all_rows))
            window_idx += 1
            cursor = window_end + timedelta(days=1)
        return all_rows
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_adapter.py -v
```

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/providers/fmp.py \
        tests/coordinator/services/datasets/providers/test_fmp_adapter.py
git commit -m "feat(datasets/fmp): DATE_RANGE pagination chunked by spec.date_chunk_days"
```

---

## Task 12: Register the five v1 FMP dataset specs + per-spec round-trip tests

**Files:**
- Create: `coordinator/services/datasets/providers/fmp_datasets.py`
- Create: `tests/coordinator/services/datasets/providers/test_fmp_datasets.py`

- [ ] **Step 1: Write round-trip tests** (each dataset gets a mocked-HTTP test that asserts a realistic FMP payload produces the right normalized parquet)

```python
# tests/coordinator/services/datasets/providers/test_fmp_datasets.py
import json
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock
from coordinator.services.datasets.providers.fmp import FMPAdapter
from coordinator.services.datasets.storage import DatasetService
from coordinator.services.datasets.registry import get, clear_registry


def _resp(status, body):
    r = MagicMock(); r.status_code = status
    r.json = MagicMock(return_value=body); r.raise_for_status = MagicMock()
    return r


@pytest.fixture(autouse=True)
def _reset():
    clear_registry()
    # Import to trigger registrations
    import importlib
    import coordinator.services.datasets.providers.fmp_datasets as fd
    importlib.reload(fd)
    yield
    clear_registry()


@pytest.fixture
def svc(tmp_path):
    return DatasetService(data_root=tmp_path)


@pytest.fixture
def quota():
    q = MagicMock(); q.acquire = AsyncMock(); q.mark_exhausted = AsyncMock()
    return q


def _adapter(http, quota):
    return FMPAdapter(api_key="K", http_client=http, quota_tracker=quota,
                      daily_limit=250, min_request_interval_s=0.0)


@pytest.mark.asyncio
async def test_house_disclosures_round_trip(svc, quota, tmp_path):
    http = MagicMock()
    http.get = AsyncMock(side_effect=[
        _resp(200, [{
            "symbol": "NVDA", "name": "Pelosi, Nancy", "office": "CA-12",
            "district": "12", "transactionDate": "2024-01-15",
            "disclosureDate": "2024-02-12", "amount": "$1,000,001 - $5,000,000",
            "type": "Buy", "assetDescription": "NVIDIA stock", "link": "http://x",
        }]),
        _resp(200, []),
    ])
    spec = get("fmp.house_disclosures")
    rows = await _adapter(http, quota).fetch_dataset(spec, {})
    await svc.upsert(spec, rows)
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "house_disclosures.parquet")
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-01-15", tz="UTC")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-02-12", tz="UTC")
    assert df.iloc[0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_senate_disclosures_round_trip(svc, quota, tmp_path):
    http = MagicMock()
    http.get = AsyncMock(side_effect=[
        _resp(200, [{
            "symbol": "TSLA", "firstName": "Tommy", "lastName": "Tuberville",
            "office": "Senate AL", "transactionDate": "2024-03-01",
            "disclosureDate": "2024-04-10", "amount": "$15,001 - $50,000",
            "type": "Sale", "assetDescription": "Tesla stock", "link": "http://y",
        }]),
        _resp(200, []),
    ])
    spec = get("fmp.senate_disclosures")
    rows = await _adapter(http, quota).fetch_dataset(spec, {})
    await svc.upsert(spec, rows)
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "senate_disclosures.parquet")
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-03-01", tz="UTC")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-04-10", tz="UTC")


@pytest.mark.asyncio
async def test_insider_trading_round_trip(svc, quota, tmp_path):
    http = MagicMock()
    http.get = AsyncMock(side_effect=[
        _resp(200, [{
            "symbol": "AAPL", "reportingName": "Cook, Timothy",
            "typeOfOwner": "officer: CEO", "transactionType": "S-Sale",
            "transactionDate": "2024-05-01", "filingDate": "2024-05-03",
            "securitiesTransacted": 1000, "price": 175.5,
            "securityName": "Common Stock", "link": "http://z",
        }]),
        _resp(200, []),
    ])
    spec = get("fmp.insider_trading")
    rows = await _adapter(http, quota).fetch_dataset(spec, {"symbol": "AAPL"})
    await svc.upsert(spec, rows, symbol="AAPL")
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "insider_trading" / "AAPL.parquet")
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-05-01", tz="UTC")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-05-03", tz="UTC")
    assert df.iloc[0]["price"] == 175.5


@pytest.mark.asyncio
async def test_income_statement_round_trip(svc, quota, tmp_path):
    http = MagicMock()
    http.get = AsyncMock(return_value=_resp(200, [{
        "symbol": "AAPL", "date": "2024-09-30", "acceptedDate": "2024-10-29 18:06:25",
        "fillingDate": "2024-10-30", "period": "Q4", "calendarYear": "2024",
        "cik": "0000320193", "reportedCurrency": "USD",
        "revenue": 90000000000, "netIncome": 24000000000, "eps": 1.55, "epsDiluted": 1.54,
    }]))
    spec = get("fmp.income_statement")
    rows = await _adapter(http, quota).fetch_dataset(spec, {"symbol": "AAPL"})
    await svc.upsert(spec, rows, symbol="AAPL")
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "income_statement" / "AAPL.parquet")
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-09-30", tz="UTC")
    # acceptedDate is the knowledge timestamp
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-10-29 18:06:25", tz="UTC")
    assert "fillingDate" in df.columns  # FMP's typo, preserved as informational column


@pytest.mark.asyncio
async def test_earnings_calendar_round_trip_single_timestamp(svc, quota, tmp_path):
    from datetime import date
    http = MagicMock()
    http.get = AsyncMock(side_effect=[
        _resp(200, [{
            "date": "2024-04-25", "symbol": "AMZN",
            "eps": 1.23, "epsEstimated": 1.10,
            "revenue": 140000000000, "revenueEstimated": 138000000000,
            "time": "amc", "fiscalDateEnding": "2024-03-31",
        }]),
        _resp(200, []),
    ])
    spec = get("fmp.earnings_calendar")
    rows = await _adapter(http, quota).fetch_dataset(
        spec, {"from": date(2024, 4, 1), "to": date(2024, 4, 30)},
    )
    await svc.upsert(spec, rows)
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "earnings_calendar.parquet")
    # Single-timestamp dataset: knowledge_date == event_date
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-04-25", tz="UTC")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-04-25", tz="UTC")
```

- [ ] **Step 2: Confirm tests fail (module not found)**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_datasets.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the five registrations**

```python
# coordinator/services/datasets/providers/fmp_datasets.py
"""Register the v1 FMP dataset catalog.

Importing this module registers DatasetSpecs into the module-level registry.
The coordinator's lifespan imports this module once at startup.
"""
from coordinator.services.datasets.registry import DatasetSpec, Pagination, register


register(DatasetSpec(
    name="fmp.house_disclosures",
    provider="fmp",
    endpoint_path="/stable/house-latest",
    event_date_column="transactionDate",
    knowledge_date_column="disclosureDate",
    symbol_keyed=False,
    id_columns=("disclosureDate", "transactionDate", "name", "symbol", "amount", "type"),
    columns={
        "symbol": "str", "name": "str", "office": "str", "district": "str",
        "transactionDate": "date", "disclosureDate": "date",
        "amount": "str", "type": "str", "assetDescription": "str", "link": "str",
    },
    pagination=Pagination.PAGE, page_size=100,
))

register(DatasetSpec(
    name="fmp.senate_disclosures",
    provider="fmp",
    endpoint_path="/stable/senate-latest",
    event_date_column="transactionDate",
    knowledge_date_column="disclosureDate",
    symbol_keyed=False,
    id_columns=("disclosureDate", "transactionDate", "firstName", "lastName",
                "symbol", "amount", "type"),
    columns={
        "symbol": "str", "firstName": "str", "lastName": "str", "office": "str",
        "transactionDate": "date", "disclosureDate": "date",
        "amount": "str", "type": "str", "assetDescription": "str", "link": "str",
    },
    pagination=Pagination.PAGE, page_size=100,
))

register(DatasetSpec(
    name="fmp.insider_trading",
    provider="fmp",
    endpoint_path="/stable/insider-trading/search",
    event_date_column="transactionDate",
    knowledge_date_column="filingDate",
    symbol_keyed=True,
    id_columns=("filingDate", "transactionDate", "reportingName",
                "transactionType", "securitiesTransacted", "price"),
    columns={
        "symbol": "str", "reportingName": "str", "typeOfOwner": "str",
        "transactionType": "str", "securitiesTransacted": "int", "price": "float",
        "transactionDate": "date", "filingDate": "datetime",
        "securityName": "str", "link": "str",
    },
    pagination=Pagination.PAGE, page_size=100,
))

register(DatasetSpec(
    name="fmp.income_statement",
    provider="fmp",
    endpoint_path="/stable/income-statement",
    event_date_column="date",
    knowledge_date_column="acceptedDate",
    symbol_keyed=True,
    id_columns=("date", "acceptedDate", "period"),
    columns={
        "symbol": "str", "date": "date", "acceptedDate": "datetime",
        "fillingDate": "date",  # preserved (FMP's typo, informational)
        "period": "str", "calendarYear": "str", "cik": "str",
        "reportedCurrency": "str",
        "revenue": "float", "netIncome": "float",
        "eps": "float", "epsDiluted": "float",
    },
    pagination=Pagination.SINGLE,
))

register(DatasetSpec(
    name="fmp.earnings_calendar",
    provider="fmp",
    endpoint_path="/stable/earnings-calendar",
    event_date_column="date",
    knowledge_date_column=None,
    symbol_keyed=False,
    id_columns=("date", "symbol"),
    columns={
        "date": "date", "symbol": "str",
        "eps": "float", "epsEstimated": "float",
        "revenue": "float", "revenueEstimated": "float",
        "time": "str", "fiscalDateEnding": "date",
    },
    pagination=Pagination.DATE_RANGE, date_chunk_days=365,
))
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pytest tests/coordinator/services/datasets/providers/test_fmp_datasets.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add coordinator/services/datasets/providers/fmp_datasets.py \
        tests/coordinator/services/datasets/providers/test_fmp_datasets.py
git commit -m "feat(datasets/fmp): v1 catalog — house/senate/insider/income/earnings"
```

---

## Task 13: `JobDispatcher` ABC + extract `BarsJobDispatcher` from `DownloadManager`

This is the carefully-scoped refactor that lets the new datasets lane plug in without touching `DownloadManager`'s core logic. Behavior must not change.

**Files:**
- Create: `coordinator/services/download_job.py`
- Modify: `coordinator/services/download_manager.py`
- Create: `tests/coordinator/services/test_download_job.py`

- [ ] **Step 1: Read `download_manager.py` end-to-end first.**

```
cat coordinator/services/download_manager.py | head -250
```

Identify the function (or functions) that take a `MarketDataDownload` row and run it through the appropriate provider's `fetch_bars`. That's the body that moves into `BarsJobDispatcher.execute`. Note the imports/dependencies it pulls (providers dict, semaphores, status broadcast, persistence calls) — the dispatcher needs access to all of them via `manager` or via constructor.

- [ ] **Step 2: Write the contract test**

```python
# tests/coordinator/services/test_download_job.py
import pytest
from coordinator.services.download_job import JobDispatcher, BarsJobDispatcher
from coordinator.database.models import MarketDataDownload


def test_jobdispatcher_is_abstract():
    with pytest.raises(TypeError):
        JobDispatcher()  # type: ignore[abstract]


def test_bars_dispatcher_declares_job_model():
    assert BarsJobDispatcher.job_model is MarketDataDownload


def test_bars_dispatcher_subclass_is_concrete():
    # Has an execute method, can be instantiated with whatever args BarsJobDispatcher needs
    # Inspect __init__ signature to confirm it doesn't require kw-args we forgot to expose
    import inspect
    sig = inspect.signature(BarsJobDispatcher.__init__)
    # Should at least take providers; adapt the assertion to whatever the real ctor needs
    assert "providers" in sig.parameters or "manager" in sig.parameters
```

Add a behavior test that goes through `DownloadManager` end-to-end with a mock `MarketDataDownload` and asserts the bars flow still works — adapt from any existing `test_download_manager.py` if present.

- [ ] **Step 3: Run tests, confirm they fail (ImportError)**

```
pytest tests/coordinator/services/test_download_job.py -v
```

- [ ] **Step 4: Implement `JobDispatcher` ABC and `BarsJobDispatcher`**

```python
# coordinator/services/download_job.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, ClassVar
from coordinator.database.models import MarketDataDownload, DatasetDownload, Base


class JobDispatcher(ABC):
    job_model: ClassVar[type[Base]]

    @abstractmethod
    async def execute(self, job, manager: "DownloadManager") -> None:
        ...


class BarsJobDispatcher(JobDispatcher):
    job_model = MarketDataDownload

    def __init__(self, providers: dict[str, Any]):
        self._providers = providers

    async def execute(self, job: MarketDataDownload, manager) -> None:
        # Lifted from DownloadManager's prior inline bars-execution body.
        # Implementer: copy the existing logic verbatim (calls fetch_bars on the right
        # provider, persists rows via DataService, updates job status, broadcasts WS events).
        ...
```

In `download_manager.py`:
- Add a `register_dispatcher(dispatcher: JobDispatcher) -> None` method that stores dispatchers in `self._dispatchers: dict[type, JobDispatcher]`.
- Replace the inline bars-execution body in the existing process loop with a call to `dispatcher = self._dispatchers[type(job)]; await dispatcher.execute(job, self)`.
- Wire `BarsJobDispatcher(providers=self._providers)` into the constructor (or expose registration to startup) so the existing bars behavior is preserved.

- [ ] **Step 5: Run the full existing test suite for downloads/bars to confirm no regression**

```
pytest tests/coordinator/services/test_download_manager.py tests/coordinator/test_data_api.py -v
```

Expected: green (no behavior change). If a test fails, the refactor lost something — restore.

- [ ] **Step 6: Run new dispatcher test**

```
pytest tests/coordinator/services/test_download_job.py -v
```

- [ ] **Step 7: Commit**

```
git add coordinator/services/download_job.py \
        coordinator/services/download_manager.py \
        tests/coordinator/services/test_download_job.py
git commit -m "refactor(download): extract BarsJobDispatcher; introduce JobDispatcher ABC"
```

---

## Task 14: `DatasetJobDispatcher` + `recover_orphaned_jobs`

**Files:**
- Modify: `coordinator/services/download_job.py`
- Create: `tests/coordinator/services/test_dataset_dispatcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/coordinator/services/test_dataset_dispatcher.py
import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import select
from coordinator.database.models import DatasetDownload
from coordinator.services.download_job import DatasetJobDispatcher
from coordinator.services.datasets.quota import QuotaExhausted
from coordinator.services.datasets.registry import (
    DatasetSpec, Pagination, register, clear_registry,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_registry()
    register(DatasetSpec(
        name="fmp.t", provider="fmp", endpoint_path="/x",
        event_date_column="d", knowledge_date_column=None,
        symbol_keyed=False, id_columns=("d",),
        columns={"d": "date"}, pagination=Pagination.PAGE,
    ))
    yield
    clear_registry()


@pytest.fixture
def adapter():
    a = MagicMock()
    a.provider = "fmp"
    a.fetch_dataset = AsyncMock(return_value=[{"d": "2024-01-01"}])
    return a


@pytest.fixture
def svc():
    s = MagicMock()
    s.upsert = AsyncMock(return_value=1)
    return s


@pytest.mark.asyncio
async def test_execute_success_marks_completed(adapter, svc, db_session_factory):
    job = DatasetDownload(dataset_name="fmp.t", provider="fmp",
                          request_payload={}, status="queued")
    async with db_session_factory() as s:
        s.add(job); await s.commit(); await s.refresh(job)
    d = DatasetJobDispatcher(adapters={"fmp": adapter}, service=svc,
                             session_factory=db_session_factory)
    await d.execute(job, manager=None)
    assert job.status == "completed"
    assert job.rows_fetched == 1
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_execute_quota_exhausted_marks_paused_quota(adapter, svc, db_session_factory):
    adapter.fetch_dataset.side_effect = QuotaExhausted("fmp", 250, 250)
    job = DatasetDownload(dataset_name="fmp.t", provider="fmp",
                          request_payload={}, status="queued")
    async with db_session_factory() as s:
        s.add(job); await s.commit(); await s.refresh(job)
    d = DatasetJobDispatcher(adapters={"fmp": adapter}, service=svc,
                             session_factory=db_session_factory)
    await d.execute(job, manager=None)
    assert job.status == "paused_quota"


@pytest.mark.asyncio
async def test_execute_generic_exception_marks_failed_with_message(adapter, svc, db_session_factory):
    adapter.fetch_dataset.side_effect = RuntimeError("boom")
    job = DatasetDownload(dataset_name="fmp.t", provider="fmp",
                          request_payload={}, status="queued")
    async with db_session_factory() as s:
        s.add(job); await s.commit(); await s.refresh(job)
    d = DatasetJobDispatcher(adapters={"fmp": adapter}, service=svc,
                             session_factory=db_session_factory)
    await d.execute(job, manager=None)
    assert job.status == "failed"
    assert "boom" in (job.error_message or "")


@pytest.mark.asyncio
async def test_execute_cancelled_marks_cancelled_and_reraises(adapter, svc, db_session_factory):
    adapter.fetch_dataset.side_effect = asyncio.CancelledError
    job = DatasetDownload(dataset_name="fmp.t", provider="fmp",
                          request_payload={}, status="queued")
    async with db_session_factory() as s:
        s.add(job); await s.commit(); await s.refresh(job)
    d = DatasetJobDispatcher(adapters={"fmp": adapter}, service=svc,
                             session_factory=db_session_factory)
    with pytest.raises(asyncio.CancelledError):
        await d.execute(job, manager=None)
    assert job.status == "cancelled"


@pytest.mark.asyncio
async def test_recover_orphaned_jobs_flips_running_to_queued(db_session_factory):
    async with db_session_factory() as s:
        s.add_all([
            DatasetDownload(dataset_name="fmp.t", provider="fmp",
                            request_payload={}, status="running"),
            DatasetDownload(dataset_name="fmp.t", provider="fmp",
                            request_payload={}, status="completed"),
        ])
        await s.commit()
    d = DatasetJobDispatcher(adapters={}, service=None,
                             session_factory=db_session_factory)
    await d.recover_orphaned_jobs()
    async with db_session_factory() as s:
        statuses = sorted(r.status for r in (await s.execute(select(DatasetDownload))).scalars())
        assert statuses == ["completed", "queued"]
```

- [ ] **Step 2: Run, confirm failure**

```
pytest tests/coordinator/services/test_dataset_dispatcher.py -v
```

- [ ] **Step 3: Implement `DatasetJobDispatcher`**

```python
# Append to coordinator/services/download_job.py
import asyncio
import json
from datetime import datetime, timezone
from functools import partial
from sqlalchemy import select, update
from coordinator.services.datasets.quota import QuotaExhausted
from coordinator.services.datasets.registry import get as _registry_get


class DatasetJobDispatcher(JobDispatcher):
    job_model = DatasetDownload

    def __init__(self, adapters: dict[str, Any], service, session_factory):
        self._adapters = adapters
        self._service = service
        self._sf = session_factory

    async def _set(self, job, **fields):
        async with self._sf() as s:
            for k, v in fields.items():
                setattr(job, k, v)
            s.add(job)
            await s.commit()

    async def execute(self, job: DatasetDownload, manager) -> None:
        spec = _registry_get(job.dataset_name)
        adapter = self._adapters[spec.provider]
        params = job.request_payload or {}
        symbol = params.get("symbol") if spec.symbol_keyed else None

        await self._set(job, status="running", started_at=datetime.now(timezone.utc))

        async def on_rows(rows, page_idx):
            await self._service.upsert(spec, rows, symbol=symbol)
            await self._set(job,
                            rows_fetched=job.rows_fetched + len(rows),
                            last_page=page_idx + 1)

        async def on_page(idx, total):
            await self._set(job, progress_message=f"page {idx} / {total} rows")

        try:
            await adapter.fetch_dataset(spec, dict(params),
                                        on_rows=on_rows, on_page=on_page)
            await self._set(job, status="completed",
                            completed_at=datetime.now(timezone.utc),
                            progress_pct=1.0)
        except QuotaExhausted:
            await self._set(job, status="paused_quota",
                            progress_message="quota exhausted; paused until reset")
        except asyncio.CancelledError:
            await self._set(job, status="cancelled")
            raise
        except Exception as e:
            await self._set(job, status="failed", error_message=str(e),
                            completed_at=datetime.now(timezone.utc))

    async def recover_orphaned_jobs(self) -> None:
        """Flip any DatasetDownload rows left 'running' (from a killed process) back to
        'queued' so the manager picks them up on resume. Mirrors ResearchJobManager."""
        async with self._sf() as s:
            await s.execute(
                update(DatasetDownload)
                .where(DatasetDownload.status == "running")
                .values(status="queued", started_at=None)
            )
            await s.commit()
```

- [ ] **Step 4: Run tests**

```
pytest tests/coordinator/services/test_dataset_dispatcher.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```
git add coordinator/services/download_job.py \
        tests/coordinator/services/test_dataset_dispatcher.py
git commit -m "feat(download-job): DatasetJobDispatcher with status transitions + orphan recovery"
```

---

## Task 15: `TickContext.dataset()` — ABC method, validation, free-function delegation

**Files:**
- Modify: `sdk/context.py`
- Create: `tests/sdk/test_tick_context_dataset.py`

- [ ] **Step 1: Read `sdk/context.py` to confirm the ABC shape**

```
cat sdk/context.py
```

Identify how existing abstract methods are declared (`@abstractmethod`, return types, signature style).

- [ ] **Step 2: Write failing tests**

```python
# tests/sdk/test_tick_context_dataset.py
from datetime import datetime, date, timedelta, timezone
import pandas as pd
import pytest
from unittest.mock import patch
from sdk.context import TickContext


class _FakeCtx(TickContext):
    """Minimal TickContext subclass for testing the default dataset() implementation."""
    def __init__(self, timestamp):
        self._ts = timestamp

    @property
    def timestamp(self):
        return self._ts

    # Stub the other abstracts so the class is concrete
    @property
    def cash(self): return 0
    @property
    def account_value(self): return 0
    @property
    def buying_power(self): return 0
    @property
    def positions(self): return {}
    def market_data(self, *a, **kw): return pd.DataFrame()
    def data(self, *a, **kw): return pd.DataFrame()
    def option_chain(self, *a, **kw): return pd.DataFrame()


@pytest.fixture
def ctx():
    return _FakeCtx(datetime(2024, 6, 1, tzinfo=timezone.utc))


def test_dataset_rejects_negative_lag(ctx):
    with pytest.raises(ValueError, match="lag must be non-negative"):
        ctx.dataset("fmp.house_disclosures", lag=timedelta(seconds=-1))


def test_dataset_rejects_lookback_with_start(ctx):
    with pytest.raises(ValueError, match="mutually exclusive"):
        ctx.dataset("fmp.x", lookback_days=30, start=date(2024, 1, 1))


def test_dataset_passes_effective_as_of_to_load_dataset(ctx):
    with patch("sdk.context.load_dataset") as mock_load:
        mock_load.return_value = pd.DataFrame()
        ctx.dataset("fmp.x")
        kwargs = mock_load.call_args.kwargs
        assert kwargs["as_of"] == datetime(2024, 6, 1, tzinfo=timezone.utc)


def test_dataset_lag_subtracts_from_timestamp(ctx):
    with patch("sdk.context.load_dataset") as mock_load:
        mock_load.return_value = pd.DataFrame()
        ctx.dataset("fmp.x", lag=timedelta(days=1))
        kwargs = mock_load.call_args.kwargs
        assert kwargs["as_of"] == datetime(2024, 5, 31, tzinfo=timezone.utc)


def test_dataset_lookback_days_derives_window(ctx):
    with patch("sdk.context.load_dataset") as mock_load:
        mock_load.return_value = pd.DataFrame()
        ctx.dataset("fmp.x", lookback_days=30)
        kwargs = mock_load.call_args.kwargs
        assert kwargs["end"] == date(2024, 6, 1)
        assert kwargs["start"] == date(2024, 5, 2)


def test_dataset_has_no_as_of_parameter():
    """Algorithm-facing API must NOT accept as_of — runtime clock is sole source of truth."""
    import inspect
    sig = inspect.signature(TickContext.dataset)
    assert "as_of" not in sig.parameters
```

- [ ] **Step 3: Confirm failure**

```
pytest tests/sdk/test_tick_context_dataset.py -v
```

- [ ] **Step 4: Add `dataset()` to `TickContext`**

```python
# Modify sdk/context.py — add at the bottom of the class
from datetime import date, timedelta
import pandas as pd
from coordinator.services.datasets.storage import load_dataset  # delegate


class TickContext(ABC):
    # ... existing abstract members ...

    def dataset(
        self,
        name: str,
        *,
        symbol: str | None = None,
        start: date | None = None,
        end: date | None = None,
        lookback_days: int | None = None,
        lag: timedelta = timedelta(0),
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Load a registered bitemporal dataset, filtered to what was knowable as-of
        the runtime clock (minus optional `lag`). NO `as_of` parameter — the runtime
        is the only source of truth. `lag` must be >= 0 — it can only delay, never peek."""
        if lag < timedelta(0):
            raise ValueError("lag must be non-negative")
        effective_as_of = self.timestamp - lag
        if lookback_days is not None:
            if start is not None or end is not None:
                raise ValueError("lookback_days is mutually exclusive with start/end")
            end = effective_as_of.date() if hasattr(effective_as_of, "date") else effective_as_of
            start = end - timedelta(days=lookback_days)
        return load_dataset(name, as_of=effective_as_of, symbol=symbol,
                            start=start, end=end, columns=columns)
```

Note: this is **not** abstract — it's a concrete default that delegates to `load_dataset`. Subclasses can override for caching (Task 16).

- [ ] **Step 5: Run tests**

```
pytest tests/sdk/test_tick_context_dataset.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```
git add sdk/context.py tests/sdk/test_tick_context_dataset.py
git commit -m "feat(sdk): TickContext.dataset() — runtime-clock-only, lag-only-delays"
```

---

## Task 16: `LiveTickContext` formal inheritance + dataset caching in both contexts

**Files:**
- Modify: `worker/context.py` — make `LiveTickContext(TickContext)` formally
- Modify: `coordinator/services/backtest_tick_context.py` — confirm `dataset()` works; ensure dataset cache survives `reset_for_replay()`
- Modify: `sdk/context.py` (optional: extract cache helper into the ABC) **or** put per-context cache on each subclass

For minimum risk, put the cache on `BacktestTickContext` (no TTL needed — backtest snapshots) and on `LiveTickContext` (with TTL).

- [ ] **Step 1: Write tests for both**

```python
# tests/sdk/test_tick_context_dataset.py — append
def test_backtest_context_caches_across_ticks(tmp_path, monkeypatch):
    from coordinator.services.backtest_tick_context import BacktestTickContext
    from coordinator.services.datasets.registry import (
        register, clear_registry, DatasetSpec, Pagination,
    )
    from coordinator.services.datasets.storage import set_default_service, DatasetService
    import asyncio

    clear_registry()
    register(DatasetSpec(
        name="t.cache", provider="t", endpoint_path="/x",
        event_date_column="d", knowledge_date_column="d",
        symbol_keyed=False, id_columns=("d",),
        columns={"d": "date"}, pagination=Pagination.PAGE,
    ))
    svc = DatasetService(data_root=tmp_path)
    set_default_service(svc)
    asyncio.run(svc.upsert("t.cache" and __import__("coordinator.services.datasets.registry",
                                                    fromlist=["get"]).get("t.cache"),
                           [{"d": "2024-01-01"}]))
    # Construct a BacktestTickContext per the existing test pattern in the repo
    # (look at tests/coordinator/test_backtest_tick_context.py for the constructor args)
    ctx = BacktestTickContext(...)  # populate per existing test pattern

    # First call loads from disk
    df1 = ctx.dataset("t.cache")
    # Second call should hit the cache (verify by checking that pd.read_parquet wasn't called twice)
    import unittest.mock
    with unittest.mock.patch("pandas.read_parquet") as m:
        df2 = ctx.dataset("t.cache")
        m.assert_not_called()


def test_reset_for_replay_preserves_dataset_cache():
    from coordinator.services.backtest_tick_context import BacktestTickContext
    # ... construct ctx with cache populated ...
    # call ctx.reset_for_replay()
    # assert ctx._dataset_cache is not empty (or equivalent introspection)
    pass  # implementer: fill in once you've read the existing BacktestTickContext constructor signature
```

(The two stubs above need concrete constructor calls — read `tests/coordinator/test_backtest_tick_context.py` and `coordinator/services/backtest_tick_context.py` first to fill in the `BacktestTickContext(...)` args correctly.)

Also test:

```python
def test_live_tick_context_inherits_tick_context():
    from worker.context import LiveTickContext
    from sdk.context import TickContext
    assert issubclass(LiveTickContext, TickContext)
```

- [ ] **Step 2: Run, confirm failure**

```
pytest tests/sdk/test_tick_context_dataset.py -v
```

- [ ] **Step 3: Make `LiveTickContext` inherit + add dataset cache**

In `worker/context.py`, change `class LiveTickContext:` to `class LiveTickContext(TickContext):`. Resolve any abstract-method gaps (existing structural methods should already cover them).

In both contexts, override `dataset()` to add caching. Sketch:

```python
# coordinator/services/backtest_tick_context.py — add to __init__
self._dataset_cache: dict[tuple, pd.DataFrame] = {}

def dataset(self, name, *, symbol=None, start=None, end=None,
            lookback_days=None, lag=timedelta(0), columns=None):
    cache_key = (name, symbol, tuple(columns) if columns else None)
    if cache_key not in self._dataset_cache:
        # Read the whole file once via the parent default, then cache the bytes-from-disk
        from coordinator.services.datasets.storage import _get_service
        from coordinator.services.datasets import registry as _reg
        spec = _reg.get(name)
        path = _get_service()._path_for(spec, symbol)
        self._dataset_cache[cache_key] = (pd.read_parquet(path, columns=columns)
                                          if path.exists() else pd.DataFrame())
    df = self._dataset_cache[cache_key]
    if df.empty:
        return df
    # Apply per-tick filter (re-applied EVERY call — never cached)
    if lag < timedelta(0):
        raise ValueError("lag must be non-negative")
    effective_as_of = pd.Timestamp(self.timestamp - lag)
    if effective_as_of.tzinfo is None: effective_as_of = effective_as_of.tz_localize("UTC")
    out = df[df["knowledge_date"] <= effective_as_of]
    if lookback_days is not None:
        if start is not None or end is not None:
            raise ValueError("lookback_days is mutually exclusive with start/end")
        end = effective_as_of.date(); start = end - timedelta(days=lookback_days)
    if start is not None: out = out[out["event_date"] >= pd.Timestamp(start, tz="UTC")]
    if end   is not None: out = out[out["event_date"] <= pd.Timestamp(end,   tz="UTC")]
    return out.sort_values(["event_date", "knowledge_date"]).reset_index(drop=True)
```

For `reset_for_replay()`: locate the method (`coordinator/services/backtest_tick_context.py:78-94`). **Do not** clear `self._dataset_cache`. Add a comment explaining why (mirrors the existing `_bars` preservation).

For `LiveTickContext`: add a TTL variant. Sketch:

```python
# worker/context.py
self._dataset_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
self._dataset_cache_ttl_s = 60.0

def dataset(self, name, *, ...):
    cache_key = (name, symbol, tuple(columns) if columns else None)
    now = time.monotonic()
    entry = self._dataset_cache.get(cache_key)
    if entry is None or (now - entry[0]) > self._dataset_cache_ttl_s:
        # load + cache
        ...
    df = entry[1]
    # ... same per-tick filter as backtest version ...
```

(Consider extracting the filter helper into a small `_filter_bitemporal(df, ts, lag, start, end, lookback_days)` free function shared between both contexts — DRY.)

- [ ] **Step 4: Run tests**

```
pytest tests/sdk/test_tick_context_dataset.py tests/coordinator/test_backtest_tick_context.py tests/worker -v
```

Expected: green, including any existing tests that touched the two contexts.

- [ ] **Step 5: Commit**

```
git add sdk/context.py worker/context.py \
        coordinator/services/backtest_tick_context.py \
        tests/sdk/test_tick_context_dataset.py
git commit -m "feat(context): LiveTickContext inherits TickContext; cache datasets; survive replay"
```

---

## Task 17: Lifespan wiring — settings, QuotaTracker, FMPAdapter, dispatcher registration

**Files:**
- Modify: `coordinator/main.py`

- [ ] **Step 1: Read the existing lifespan**

```
grep -n "polygon_api_key\|theta_data_username\|providers\[" coordinator/main.py | head -20
```

Note the existing pattern for: loading settings via `_get_setting`, decrypting via `EncryptionService`, constructing the provider, adding it to a `providers` dict, then passing the dict to `DownloadManager`.

- [ ] **Step 2: Add FMP / datasets wiring inside the lifespan**

```python
# coordinator/main.py — within the existing async lifespan block, after the bars providers
# are constructed:

from coordinator.services.datasets.quota import QuotaTracker
from coordinator.services.datasets.providers.fmp import FMPAdapter
from coordinator.services.datasets.storage import DatasetService, set_default_service
from coordinator.services.download_job import BarsJobDispatcher, DatasetJobDispatcher
from zoneinfo import ZoneInfo
import coordinator.services.datasets.providers.fmp_datasets  # noqa: F401 — registers specs

# Settings
async with session_factory() as s:
    fmp_key       = await _get_setting(s, "fmp_api_key", encryption=encryption)
    fmp_limit     = int(await _get_setting(s, "fmp_daily_quota_limit") or 250)
    fmp_interval  = float(await _get_setting(s, "fmp_min_request_interval_s") or 0.0)
    reset_tz_name = await _get_setting(s, "dataset_quota_reset_tz") or "UTC"
quota_reset_tz = ZoneInfo(reset_tz_name)

# Singletons
quota_tracker = QuotaTracker(session_factory, reset_tz=quota_reset_tz)
dataset_service = DatasetService(data_root=Path("data"))
set_default_service(dataset_service)

dataset_adapters: dict[str, object] = {}
if fmp_key:
    dataset_adapters["fmp"] = FMPAdapter(
        api_key=fmp_key, http_client=http_client, quota_tracker=quota_tracker,
        daily_limit=fmp_limit, min_request_interval_s=fmp_interval,
    )

# Register dispatchers on DownloadManager
download_manager.register_dispatcher(BarsJobDispatcher(providers=providers))
dataset_dispatcher = DatasetJobDispatcher(
    adapters=dataset_adapters, service=dataset_service, session_factory=session_factory,
)
download_manager.register_dispatcher(dataset_dispatcher)

# Orphan recovery on startup
await dataset_dispatcher.recover_orphaned_jobs()

# Expose to FastAPI dependency injection (match existing pattern)
app.state.quota_tracker = quota_tracker
app.state.dataset_adapters = dataset_adapters
app.state.dataset_service = dataset_service
```

Match the actual `_get_setting` signature and the actual session-factory variable name used in `main.py` — these vary slightly across the project.

- [ ] **Step 3: Smoke-run the coordinator**

```
# bring it up, hit /health, then shut down
python -m coordinator.main &
sleep 3
curl -sf http://localhost:8000/api/health
kill %1
```

Expected: no startup errors. Logs should show "dataset adapters: ['fmp']" (or similar — add a log line if useful).

- [ ] **Step 4: Commit**

```
git add coordinator/main.py
git commit -m "feat(coord): wire QuotaTracker + FMPAdapter + DatasetJobDispatcher into lifespan"
```

---

## Task 18: REST routes — `/api/datasets/*`

**Files:**
- Create: `coordinator/api/routes/datasets.py`
- Modify: route mounting in `coordinator/api/routes/__init__.py` (or wherever `data.py` is included)
- Create: `tests/coordinator/api/test_datasets_routes.py`

- [ ] **Step 1: Write failing route tests**

```python
# tests/coordinator/api/test_datasets_routes.py
import pytest
from datetime import date
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_datasets(test_client: AsyncClient):
    r = await test_client.get("/api/datasets")
    assert r.status_code == 200
    body = r.json()
    names = {d["name"] for d in body}
    assert "fmp.house_disclosures" in names


@pytest.mark.asyncio
async def test_get_dataset_detail(test_client):
    r = await test_client.get("/api/datasets/fmp.house_disclosures")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "fmp"
    assert body["pagination"] == "page"
    assert body["event_date_column"] == "transactionDate"


@pytest.mark.asyncio
async def test_get_dataset_unknown_returns_404(test_client):
    r = await test_client.get("/api/datasets/nope.nada")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_dataset_providers_returns_availability_matrix(test_client):
    r = await test_client.get("/api/datasets/providers")
    assert r.status_code == 200
    body = r.json()
    assert any(p["name"] == "fmp" for p in body)


@pytest.mark.asyncio
async def test_queue_download(test_client):
    r = await test_client.post("/api/datasets/downloads", json={
        "name": "fmp.house_disclosures", "params": {},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["dataset_name"] == "fmp.house_disclosures"


@pytest.mark.asyncio
async def test_quota_endpoint_returns_usage(test_client):
    r = await test_client.get("/api/datasets/quota")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # If fmp configured, should include an "fmp" entry
    fmp = [p for p in body if p["provider"] == "fmp"]
    if fmp:
        assert "calls_used" in fmp[0]
        assert "daily_limit" in fmp[0]


@pytest.mark.asyncio
async def test_rows_endpoint_applies_as_of_filter(test_client, tmp_path):
    # Seed a parquet with two rows: one knowable at as_of, one not
    from coordinator.services.datasets.registry import get
    from coordinator.services.datasets.storage import _get_service
    spec = get("fmp.house_disclosures")
    await _get_service().upsert(spec, [
        {"transactionDate": "2024-01-01", "disclosureDate": "2024-02-01",
         "symbol": "A", "name": "X", "amount": "$1"},
        {"transactionDate": "2024-03-01", "disclosureDate": "2024-04-01",
         "symbol": "B", "name": "Y", "amount": "$2"},
    ])
    r = await test_client.get(
        "/api/datasets/fmp.house_disclosures/rows",
        params={"as_of": "2024-02-15"},
    )
    assert r.status_code == 200
    body = r.json()
    symbols = {row["symbol"] for row in body["rows"]}
    assert symbols == {"A"}  # only the row knowable as of 2024-02-15
```

The `test_client` fixture should match whatever the existing route tests use (`tests/coordinator/api/conftest.py` or similar).

- [ ] **Step 2: Confirm failure**

```
pytest tests/coordinator/api/test_datasets_routes.py -v
```

- [ ] **Step 3: Implement routes**

```python
# coordinator/api/routes/datasets.py
from __future__ import annotations
from datetime import datetime, timezone, date
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, desc
import pandas as pd
from coordinator.database.models import DatasetDownload, QuotaUsage
from coordinator.services.datasets.registry import get as _registry_get, list_all as _list_specs
from coordinator.services.datasets.storage import load_dataset, _get_service


router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _spec_to_dict(spec) -> dict:
    return {
        "name": spec.name, "provider": spec.provider,
        "endpoint_path": spec.endpoint_path,
        "event_date_column": spec.event_date_column,
        "knowledge_date_column": spec.knowledge_date_column,
        "symbol_keyed": spec.symbol_keyed,
        "id_columns": list(spec.id_columns),
        "columns": spec.columns,
        "pagination": spec.pagination,
        "page_size": spec.page_size,
        "date_chunk_days": spec.date_chunk_days,
        "free_tier": spec.free_tier,
    }


@router.get("")
async def list_datasets():
    return [_spec_to_dict(s) for s in _list_specs()]


@router.get("/providers")
async def list_dataset_providers(request: Request):
    adapters: dict[str, Any] = getattr(request.app.state, "dataset_adapters", {})
    seen = {s.provider for s in _list_specs()}
    result = []
    for prov in sorted(seen):
        available = prov in adapters
        result.append({
            "name": prov,
            "available": available,
            "reason": None if available else f"{prov}_api_key setting missing",
        })
    return result


@router.get("/{name}")
async def get_dataset(name: str):
    try:
        spec = _registry_get(name)
    except KeyError:
        raise HTTPException(404, f"unknown dataset: {name}")
    return _spec_to_dict(spec)


@router.get("/{name}/coverage")
async def get_dataset_coverage(name: str):
    spec = _registry_get(name)
    svc = _get_service()
    out: list[dict] = []
    if spec.symbol_keyed:
        short = spec.name.split(".", 1)[1]
        base = svc._data_root / "datasets" / spec.provider / short
        if base.exists():
            for p in sorted(base.glob("*.parquet")):
                out.append(_coverage_entry(p, symbol=p.stem))
    else:
        p = svc._path_for(spec, None)
        if p.exists():
            out.append(_coverage_entry(p, symbol=None))
    return {"name": name, "symbols": out}


def _coverage_entry(path, symbol):
    df = pd.read_parquet(path, columns=["event_date", "knowledge_date"])
    return {
        "symbol": symbol,
        "row_count": int(len(df)),
        "event_date_min": str(df["event_date"].min()) if len(df) else None,
        "event_date_max": str(df["event_date"].max()) if len(df) else None,
        "knowledge_date_max": str(df["knowledge_date"].max()) if len(df) else None,
        "file_size_bytes": int(path.stat().st_size),
        "last_modified": int(path.stat().st_mtime),
    }


@router.get("/coverage")
async def coverage_index():
    return [{
        "name": s.name, "provider": s.provider, "symbol_keyed": s.symbol_keyed,
        "detail_url": f"/api/datasets/{s.name}/coverage",
    } for s in _list_specs()]


@router.get("/{name}/rows")
async def get_dataset_rows(
    name: str,
    symbol: str | None = Query(None),
    as_of: str | None = Query(None),
    start: date | None = Query(None),
    end: date | None = Query(None),
    columns: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    spec = _registry_get(name)
    as_of_dt = pd.Timestamp(as_of, tz="UTC").to_pydatetime() if as_of else datetime.now(timezone.utc)
    cols = columns.split(",") if columns else None
    df = load_dataset(name, as_of=as_of_dt, symbol=symbol, start=start, end=end, columns=cols)
    total = len(df)
    page = df.iloc[offset:offset + limit]
    return {
        "total": int(total),
        "rows": page.to_dict(orient="records"),
        "spec_summary": {
            "columns": spec.columns,
            "event_date_column": spec.event_date_column,
            "knowledge_date_column": spec.knowledge_date_column,
        },
    }


class DownloadRequest(BaseModel):
    name: str
    params: dict = {}


@router.post("/downloads")
async def queue_download(req: DownloadRequest, request: Request):
    spec = _registry_get(req.name)  # 404 if unknown
    adapters = getattr(request.app.state, "dataset_adapters", {})
    if spec.provider not in adapters:
        raise HTTPException(400, f"{spec.provider} adapter not configured "
                                 f"(missing {spec.provider}_api_key)")
    sf = request.app.state.session_factory  # adjust to actual attr name
    async with sf() as s:
        row = DatasetDownload(dataset_name=req.name, provider=spec.provider,
                              request_payload=req.params, status="queued",
                              created_by="api")
        s.add(row); await s.commit(); await s.refresh(row)
        return _row_to_dict(row)


@router.get("/downloads")
async def list_downloads(request: Request,
                         status: str | None = None,
                         provider: str | None = None):
    sf = request.app.state.session_factory
    async with sf() as s:
        q = select(DatasetDownload).order_by(desc(DatasetDownload.queued_at))
        if status: q = q.where(DatasetDownload.status == status)
        if provider: q = q.where(DatasetDownload.provider == provider)
        rows = (await s.execute(q.limit(500))).scalars().all()
        return [_row_to_dict(r) for r in rows]


@router.get("/downloads/{download_id}")
async def get_download(download_id: int, request: Request):
    sf = request.app.state.session_factory
    async with sf() as s:
        row = await s.get(DatasetDownload, download_id)
        if row is None: raise HTTPException(404)
        return _row_to_dict(row)


@router.delete("/downloads/{download_id}")
async def cancel_download(download_id: int, request: Request):
    sf = request.app.state.session_factory
    async with sf() as s:
        row = await s.get(DatasetDownload, download_id)
        if row is None: raise HTTPException(404)
        if row.status == "queued":
            row.status = "cancelled"
            await s.commit()
        return _row_to_dict(row)


def _row_to_dict(r: DatasetDownload) -> dict:
    return {
        "id": r.id, "dataset_name": r.dataset_name, "provider": r.provider,
        "request_payload": r.request_payload, "status": r.status,
        "queued_at": r.queued_at.isoformat() if r.queued_at else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "rows_fetched": r.rows_fetched, "calls_consumed": r.calls_consumed,
        "progress_pct": r.progress_pct, "progress_message": r.progress_message,
        "error_message": r.error_message,
    }


@router.get("/quota")
async def list_quota(request: Request):
    sf = request.app.state.session_factory
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    async with sf() as s:
        rows = (await s.execute(
            select(QuotaUsage).where(QuotaUsage.reset_window == today)
        )).scalars().all()
        return [{
            "provider": r.provider, "reset_window": r.reset_window.isoformat(),
            "calls_used": r.calls_used, "daily_limit": r.daily_limit,
            "exhausted": r.exhausted,
        } for r in rows]


@router.get("/quota/{provider}")
async def get_quota(provider: str, request: Request):
    sf = request.app.state.session_factory
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    async with sf() as s:
        row = (await s.execute(
            select(QuotaUsage).where(QuotaUsage.provider == provider,
                                     QuotaUsage.reset_window == today)
        )).scalar_one_or_none()
        if row is None:
            return {"provider": provider, "calls_used": 0, "daily_limit": None,
                    "exhausted": False}
        return {"provider": row.provider, "reset_window": row.reset_window.isoformat(),
                "calls_used": row.calls_used, "daily_limit": row.daily_limit,
                "exhausted": row.exhausted}
```

Wire `router` into the FastAPI app — locate the file where `data.py`'s `router` is included (`include_router(...)`) and add `include_router(datasets.router)` next to it.

- [ ] **Step 2: Run tests**

```
pytest tests/coordinator/api/test_datasets_routes.py -v
```

Expected: all pass (some assertions may need tweaking based on exact response shape — adapt rather than relax).

- [ ] **Step 3: Commit**

```
git add coordinator/api/routes/datasets.py \
        coordinator/api/routes/__init__.py \
        tests/coordinator/api/test_datasets_routes.py
git commit -m "feat(api): /api/datasets/* — list/spec/coverage/rows/downloads/quota/providers"
```

---

## Task 19: CLI — `quilt data datasets *`

**Files:**
- Modify: `sdk/cli/commands/data.py` (add `datasets` subcommand group)
- Create: `tests/sdk/cli/test_data_datasets.py`

- [ ] **Step 1: Inspect existing CLI structure**

```
grep -n "subscribe\|app.command\|Typer\|click" sdk/cli/commands/data.py | head -20
```

Match the framework (Typer / Click) and the existing pattern for thin REST-shells.

- [ ] **Step 2: Write failing CLI tests**

```python
# tests/sdk/cli/test_data_datasets.py
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner  # or click.testing.CliRunner — match existing
from sdk.cli.commands.data import app as data_app  # adjust import

runner = CliRunner()


def test_datasets_list_hits_correct_endpoint():
    with patch("sdk.cli.commands.data._api_get") as m:
        m.return_value = [{"name": "fmp.house_disclosures", "provider": "fmp",
                           "pagination": "page", "symbol_keyed": False}]
        result = runner.invoke(data_app, ["datasets", "list"])
        assert result.exit_code == 0
        m.assert_called_once_with("/api/datasets")
        assert "fmp.house_disclosures" in result.stdout


def test_datasets_download_posts_with_name_and_params():
    with patch("sdk.cli.commands.data._api_post") as m:
        m.return_value = {"id": 1, "status": "queued"}
        result = runner.invoke(data_app, [
            "datasets", "download", "fmp.insider_trading",
            "--symbol", "AAPL", "--from", "2024-01-01", "--to", "2024-06-01",
        ])
        assert result.exit_code == 0
        args = m.call_args
        assert args[0][0] == "/api/datasets/downloads"
        body = args[0][1]
        assert body["name"] == "fmp.insider_trading"
        assert body["params"]["symbol"] == "AAPL"
        assert body["params"]["from"] == "2024-01-01"


def test_datasets_quota_prints_summary():
    with patch("sdk.cli.commands.data._api_get") as m:
        m.return_value = [{"provider": "fmp", "calls_used": 147, "daily_limit": 250,
                           "exhausted": False}]
        result = runner.invoke(data_app, ["datasets", "quota"])
        assert result.exit_code == 0
        assert "147" in result.stdout and "250" in result.stdout
```

(Adjust `_api_get` / `_api_post` to the actual helper names in `sdk/cli/commands/data.py`.)

- [ ] **Step 3: Run, confirm failure**

```
pytest tests/sdk/cli/test_data_datasets.py -v
```

- [ ] **Step 4: Add `datasets` subcommand group**

Sketch (adjust to actual CLI framework):

```python
# sdk/cli/commands/data.py — add at end (assuming Typer)
import typer
datasets_app = typer.Typer(help="Manage time-series datasets (FMP and beyond).")
app.add_typer(datasets_app, name="datasets")


@datasets_app.command("list")
def datasets_list():
    rows = _api_get("/api/datasets")
    for r in rows:
        print(f"{r['name']:<32} provider={r['provider']} pagination={r['pagination']}")


@datasets_app.command("show")
def datasets_show(name: str):
    spec = _api_get(f"/api/datasets/{name}")
    print(json.dumps(spec, indent=2))


@datasets_app.command("download")
def datasets_download(
    name: str,
    symbol: str | None = typer.Option(None, "--symbol"),
    date_from: str | None = typer.Option(None, "--from"),
    date_to: str | None = typer.Option(None, "--to"),
    param: list[str] = typer.Option([], "--param", help="key=value"),
):
    params: dict = {}
    if symbol: params["symbol"] = symbol
    if date_from: params["from"] = date_from
    if date_to: params["to"] = date_to
    for p in param:
        k, _, v = p.partition("=")
        params[k] = v
    row = _api_post("/api/datasets/downloads", {"name": name, "params": params})
    print(f"queued download #{row['id']} for {name} (status={row['status']})")


@datasets_app.command("downloads")
def datasets_downloads(status: str | None = None, provider: str | None = None):
    qs = "?" + "&".join(f"{k}={v}" for k, v in [("status", status), ("provider", provider)] if v)
    rows = _api_get(f"/api/datasets/downloads{qs if qs != '?' else ''}")
    for r in rows:
        print(f"#{r['id']:<5} {r['dataset_name']:<32} {r['status']:<14} "
              f"rows={r['rows_fetched']} progress={r['progress_pct']*100:.0f}%")


@datasets_app.command("quota")
def datasets_quota():
    rows = _api_get("/api/datasets/quota")
    if not rows:
        print("(no usage yet today)"); return
    for r in rows:
        flag = " EXHAUSTED" if r["exhausted"] else ""
        print(f"{r['provider']:<8} {r['calls_used']}/{r['daily_limit']}{flag}")
```

- [ ] **Step 5: Run tests**

```
pytest tests/sdk/cli/test_data_datasets.py -v
```

- [ ] **Step 6: Commit**

```
git add sdk/cli/commands/data.py tests/sdk/cli/test_data_datasets.py
git commit -m "feat(cli): quilt data datasets {list,show,download,downloads,quota}"
```

---

## Task 20: Frontend — API client + `useDatasetCoverage` + `usePagedDatasetRows` hooks

**Files:**
- Modify: `dashboard/src/api.ts` (or wherever `api.getCoverage()` lives)
- Create: `dashboard/src/hooks/useDatasetCoverage.ts`
- Create: `dashboard/src/hooks/usePagedDatasetRows.ts`

- [ ] **Step 1: Add API client functions**

Locate where `api.getCoverage()` is defined (search for `getCoverage` in `dashboard/src/`). Add nearby:

```typescript
// dashboard/src/api.ts — additions
export interface DatasetSpec {
  name: string; provider: string; endpoint_path: string;
  event_date_column: string; knowledge_date_column: string | null;
  symbol_keyed: boolean; id_columns: string[]; columns: Record<string, string>;
  pagination: "single" | "page" | "date_range";
  page_size: number; date_chunk_days: number; free_tier: boolean;
}

export interface DatasetCoverageEntry {
  symbol: string | null; row_count: number;
  event_date_min: string | null; event_date_max: string | null;
  knowledge_date_max: string | null;
  file_size_bytes: number; last_modified: number;
}

export interface DatasetRowsResponse {
  total: number;
  rows: Record<string, unknown>[];
  spec_summary: { columns: Record<string, string>;
                  event_date_column: string; knowledge_date_column: string | null; };
}

export const api = {
  // ... existing ...
  listDatasets: () => http<DatasetSpec[]>("/api/datasets"),
  getDataset: (name: string) => http<DatasetSpec>(`/api/datasets/${name}`),
  listDatasetProviders: () => http<{name: string; available: boolean; reason: string | null}[]>(
    "/api/datasets/providers"),
  getDatasetCoverageIndex: () => http<{name: string; provider: string; symbol_keyed: boolean;
                                       detail_url: string}[]>("/api/datasets/coverage"),
  getDatasetCoverage: (name: string) =>
    http<{name: string; symbols: DatasetCoverageEntry[]}>(`/api/datasets/${name}/coverage`),
  getDatasetRows: (name: string, params: {
    symbol?: string; as_of?: string; start?: string; end?: string;
    columns?: string; limit?: number; offset?: number;
  }) => http<DatasetRowsResponse>(`/api/datasets/${name}/rows`, { params }),
};
```

(Match the actual existing `http` helper signature and the existing `api` object shape — the above is illustrative.)

- [ ] **Step 2: Add hooks**

```typescript
// dashboard/src/hooks/useDatasetCoverage.ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export function useDatasetCoverage() {
  return useQuery({
    queryKey: ["datasets", "coverage"],
    queryFn: api.getDatasetCoverageIndex,
  });
}

export function useDatasetCoverageDetail(name: string, enabled = true) {
  return useQuery({
    queryKey: ["datasets", "coverage", name],
    queryFn: () => api.getDatasetCoverage(name),
    enabled,
  });
}
```

```typescript
// dashboard/src/hooks/usePagedDatasetRows.ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export function usePagedDatasetRows(name: string | null, params: {
  symbol?: string; as_of?: string; start?: string; end?: string;
  page: number; pageSize: number;
}) {
  return useQuery({
    queryKey: ["datasets", "rows", name, params],
    queryFn: () => api.getDatasetRows(name!, {
      symbol: params.symbol, as_of: params.as_of,
      start: params.start, end: params.end,
      limit: params.pageSize, offset: params.page * params.pageSize,
    }),
    enabled: !!name,
    keepPreviousData: true,
  });
}
```

- [ ] **Step 3: Compile / typecheck**

```
cd dashboard && npm run typecheck   # or tsc --noEmit, whichever exists
```

Expected: no errors.

- [ ] **Step 4: Commit**

```
git add dashboard/src/api.ts \
        dashboard/src/hooks/useDatasetCoverage.ts \
        dashboard/src/hooks/usePagedDatasetRows.ts
git commit -m "feat(dashboard): API client + react-query hooks for datasets"
```

---

## Task 21: Frontend — rename existing `DatasetPreviewModal` to `MarketDataPreviewModal`

The existing `DatasetPreviewModal.tsx` is misnamed — it's the OHLCV market-data preview. Rename so the new bitemporal one can take the `DatasetPreviewModal` name.

**Files:**
- Move: `dashboard/src/components/DatasetPreviewModal.tsx` → `dashboard/src/components/MarketDataPreviewModal.tsx`
- Modify: `dashboard/src/components/AvailableDataTab.tsx` (the single caller; update import + JSX tag)

- [ ] **Step 1: Rename file and update internals**

```
git mv dashboard/src/components/DatasetPreviewModal.tsx \
       dashboard/src/components/MarketDataPreviewModal.tsx
```

Inside the renamed file, change:
- `interface DatasetPreviewModalProps` → `interface MarketDataPreviewModalProps`
- `export function DatasetPreviewModal(` → `export function MarketDataPreviewModal(`
- Any default export or display name → `MarketDataPreviewModal`

- [ ] **Step 2: Update the single caller**

```typescript
// dashboard/src/components/AvailableDataTab.tsx
- import { DatasetPreviewModal } from "./DatasetPreviewModal";
+ import { MarketDataPreviewModal } from "./MarketDataPreviewModal";

// ...
- <DatasetPreviewModal ... />
+ <MarketDataPreviewModal ... />
```

- [ ] **Step 3: Typecheck**

```
cd dashboard && npm run typecheck
```

Expected: clean.

- [ ] **Step 4: Smoke run the dashboard, confirm market-data preview still works**

```
cd dashboard && npm run dev
```

Click Available Data → click a symbol's row → modal opens with bars (no regression).

- [ ] **Step 5: Commit**

```
git add dashboard/src/components/MarketDataPreviewModal.tsx \
        dashboard/src/components/AvailableDataTab.tsx
git rm dashboard/src/components/DatasetPreviewModal.tsx
git commit -m "refactor(dashboard): rename DatasetPreviewModal → MarketDataPreviewModal"
```

---

## Task 22: Frontend — new `DatasetPreviewModal` (bitemporal row browser)

**Files:**
- Create: `dashboard/src/components/DatasetPreviewModal.tsx`

- [ ] **Step 1: Implement the modal**

```typescript
// dashboard/src/components/DatasetPreviewModal.tsx
import { useState, useMemo } from "react";
import { X } from "lucide-react";
import { DataTable } from "./DataTable";
import { usePagedDatasetRows } from "../hooks/usePagedDatasetRows";
import { useDatasetCoverageDetail } from "../hooks/useDatasetCoverage";
import type { ColumnDef } from "@tanstack/react-table";

interface Props {
  open: boolean;
  onClose: () => void;
  datasetName: string;
  symbolKeyed: boolean;
}

export function DatasetPreviewModal({ open, onClose, datasetName, symbolKeyed }: Props) {
  const [symbol, setSymbol] = useState<string | undefined>(undefined);
  const [asOf, setAsOf] = useState<string>(new Date().toISOString().slice(0, 10));
  const [start, setStart] = useState<string>("");
  const [end, setEnd] = useState<string>("");
  const [page, setPage] = useState(0);
  const pageSize = 50;

  const coverage = useDatasetCoverageDetail(datasetName, open);
  const rows = usePagedDatasetRows(open ? datasetName : null, {
    symbol, as_of: asOf, start: start || undefined, end: end || undefined, page, pageSize,
  });

  const columns = useMemo<ColumnDef<Record<string, unknown>>[]>(() => {
    const r0 = rows.data?.rows[0];
    if (!r0) return [];
    return Object.keys(r0).map(k => ({ header: k, accessorKey: k }));
  }, [rows.data]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white dark:bg-zinc-900 rounded-lg shadow-xl w-[90vw] max-w-6xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-lg font-semibold">{datasetName}</h2>
          <button onClick={onClose} className="p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded">
            <X size={20} />
          </button>
        </div>

        <div className="p-4 border-b grid grid-cols-4 gap-3 text-sm">
          {symbolKeyed && (
            <select className="border rounded px-2 py-1"
                    value={symbol || ""}
                    onChange={e => { setSymbol(e.target.value || undefined); setPage(0); }}>
              <option value="">— select symbol —</option>
              {coverage.data?.symbols.map(s =>
                <option key={s.symbol} value={s.symbol!}>{s.symbol} ({s.row_count.toLocaleString()})</option>
              )}
            </select>
          )}
          <label className="flex items-center gap-2">
            <span>as_of</span>
            <input type="date" value={asOf}
                   onChange={e => { setAsOf(e.target.value); setPage(0); }}
                   className="border rounded px-2 py-1" />
          </label>
          <label className="flex items-center gap-2">
            <span>start</span>
            <input type="date" value={start}
                   onChange={e => { setStart(e.target.value); setPage(0); }}
                   className="border rounded px-2 py-1" />
          </label>
          <label className="flex items-center gap-2">
            <span>end</span>
            <input type="date" value={end}
                   onChange={e => { setEnd(e.target.value); setPage(0); }}
                   className="border rounded px-2 py-1" />
          </label>
        </div>

        <div className="flex-1 overflow-auto p-4">
          {rows.isLoading && <div>Loading…</div>}
          {rows.error && <div className="text-red-500">Error loading rows</div>}
          {rows.data && (
            <>
              <DataTable
                data={rows.data.rows}
                columns={columns}
                enableSorting
                emptyMessage={
                  symbolKeyed && !symbol
                    ? "Select a symbol to preview rows"
                    : "No rows match the current filters"
                }
              />
              <div className="flex items-center justify-between mt-3 text-sm">
                <span>{rows.data.total.toLocaleString()} total rows</span>
                <div className="flex items-center gap-2">
                  <button disabled={page === 0}
                          onClick={() => setPage(page - 1)}
                          className="px-2 py-1 border rounded disabled:opacity-40">Prev</button>
                  <span>page {page + 1}</span>
                  <button disabled={(page + 1) * pageSize >= rows.data.total}
                          onClick={() => setPage(page + 1)}
                          className="px-2 py-1 border rounded disabled:opacity-40">Next</button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```
git add dashboard/src/components/DatasetPreviewModal.tsx
git commit -m "feat(dashboard): DatasetPreviewModal — bitemporal row browser"
```

---

## Task 23: Frontend — `DatasetsAvailableSection` + AvailableDataTab toggle

**Files:**
- Create: `dashboard/src/components/DatasetsAvailableSection.tsx`
- Create: `dashboard/src/components/DatasetsFilterBar.tsx`
- Modify: `dashboard/src/components/AvailableDataTab.tsx` — add `MarketData | Datasets` toggle

- [ ] **Step 1: Implement `DatasetsAvailableSection`**

```typescript
// dashboard/src/components/DatasetsAvailableSection.tsx
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { DataTable } from "./DataTable";
import { DatasetPreviewModal } from "./DatasetPreviewModal";
import { DatasetsFilterBar } from "./DatasetsFilterBar";
import type { ColumnDef } from "@tanstack/react-table";

interface Row {
  name: string; provider: string; symbol_keyed: boolean;
  total_rows: number; event_date_min: string | null; event_date_max: string | null;
  knowledge_date_max: string | null; file_size_bytes: number;
}

export function DatasetsAvailableSection() {
  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ name: string; symbolKeyed: boolean } | null>(null);

  const specs = useQuery({ queryKey: ["datasets", "list"], queryFn: api.listDatasets });
  const coverageIndex = useQuery({
    queryKey: ["datasets", "coverage-index"], queryFn: api.getDatasetCoverageIndex,
  });

  // Fetch per-dataset coverage in parallel to compute aggregate row counts
  const detailQueries = useQuery({
    queryKey: ["datasets", "coverage-details", specs.data?.map(s => s.name)],
    queryFn: async () => {
      if (!specs.data) return {};
      const results = await Promise.all(specs.data.map(async s => {
        try { return [s.name, await api.getDatasetCoverage(s.name)] as const; }
        catch { return [s.name, { name: s.name, symbols: [] }] as const; }
      }));
      return Object.fromEntries(results);
    },
    enabled: !!specs.data,
  });

  const rows = useMemo<Row[]>(() => {
    if (!specs.data) return [];
    const details = detailQueries.data || {};
    return specs.data
      .filter(s => !providerFilter || s.provider === providerFilter)
      .filter(s => !search || s.name.includes(search))
      .map(s => {
        const cov = details[s.name]?.symbols || [];
        const totalRows = cov.reduce((acc: number, x: any) => acc + (x.row_count || 0), 0);
        const eventMin = cov.length ? cov.reduce((a: any, b: any) =>
            a.event_date_min && (!b.event_date_min || a.event_date_min < b.event_date_min) ? a : b
        ).event_date_min : null;
        const eventMax = cov.length ? cov.reduce((a: any, b: any) =>
            a.event_date_max && (!b.event_date_max || a.event_date_max > b.event_date_max) ? a : b
        ).event_date_max : null;
        const knowledgeMax = cov.length ? cov.reduce((a: any, b: any) =>
            a.knowledge_date_max && (!b.knowledge_date_max || a.knowledge_date_max > b.knowledge_date_max) ? a : b
        ).knowledge_date_max : null;
        const totalBytes = cov.reduce((acc: number, x: any) => acc + (x.file_size_bytes || 0), 0);
        return {
          name: s.name, provider: s.provider, symbol_keyed: s.symbol_keyed,
          total_rows: totalRows, event_date_min: eventMin, event_date_max: eventMax,
          knowledge_date_max: knowledgeMax, file_size_bytes: totalBytes,
        };
      });
  }, [specs.data, detailQueries.data, search, providerFilter]);

  const columns = useMemo<ColumnDef<Row>[]>(() => [
    { header: "Dataset", accessorKey: "name" },
    { header: "Provider", accessorKey: "provider" },
    { header: "Scope", accessorFn: r => r.symbol_keyed ? "per-symbol" : "firehose" },
    { header: "Rows", accessorFn: r => r.total_rows.toLocaleString() },
    { header: "Event range",
      accessorFn: r => r.event_date_min && r.event_date_max
                          ? `${r.event_date_min.slice(0,10)} → ${r.event_date_max.slice(0,10)}` : "—" },
    { header: "Fresh as of",
      accessorFn: r => r.knowledge_date_max ? r.knowledge_date_max.slice(0, 10) : "—" },
    { header: "Size",
      accessorFn: r => `${(r.file_size_bytes / 1024 / 1024).toFixed(1)} MB` },
  ], []);

  return (
    <div className="space-y-3">
      <DatasetsFilterBar
        search={search} onSearch={setSearch}
        provider={providerFilter} onProvider={setProviderFilter}
        providers={Array.from(new Set(specs.data?.map(s => s.provider) || []))}
      />
      <DataTable
        data={rows} columns={columns}
        isLoading={specs.isLoading || coverageIndex.isLoading}
        emptyMessage="No datasets registered yet"
        onRowClick={(r) => setPreview({ name: r.name, symbolKeyed: r.symbol_keyed })}
      />
      {preview && (
        <DatasetPreviewModal
          open={true}
          onClose={() => setPreview(null)}
          datasetName={preview.name}
          symbolKeyed={preview.symbolKeyed}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Implement `DatasetsFilterBar`**

```typescript
// dashboard/src/components/DatasetsFilterBar.tsx
import { Search } from "lucide-react";

interface Props {
  search: string; onSearch: (s: string) => void;
  provider: string | null; onProvider: (p: string | null) => void;
  providers: string[];
}

export function DatasetsFilterBar({ search, onSearch, provider, onProvider, providers }: Props) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      <div className="relative">
        <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-400" />
        <input value={search} onChange={e => onSearch(e.target.value)}
               placeholder="Search datasets…"
               className="pl-7 pr-3 py-1 border rounded text-sm" />
      </div>
      <div className="flex gap-1">
        <button onClick={() => onProvider(null)}
                className={`px-2 py-1 rounded text-xs border ${!provider ? "bg-zinc-800 text-white" : ""}`}>
          all
        </button>
        {providers.map(p =>
          <button key={p} onClick={() => onProvider(p)}
                  className={`px-2 py-1 rounded text-xs border ${provider === p ? "bg-zinc-800 text-white" : ""}`}>
            {p}
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add `MarketData | Datasets` toggle to `AvailableDataTab.tsx`**

```typescript
// dashboard/src/components/AvailableDataTab.tsx — additions
import { useState } from "react";
import { DatasetsAvailableSection } from "./DatasetsAvailableSection";

// Inside the component, near the top of the JSX:
const [view, setView] = useState<"market" | "datasets">("market");

return (
  <div>
    <div className="flex gap-1 mb-3">
      <button onClick={() => setView("market")}
              className={`px-3 py-1 rounded ${view === "market" ? "bg-zinc-800 text-white" : "border"}`}>
        Market Data
      </button>
      <button onClick={() => setView("datasets")}
              className={`px-3 py-1 rounded ${view === "datasets" ? "bg-zinc-800 text-white" : "border"}`}>
        Datasets
      </button>
    </div>
    {view === "market" && (
      // ... existing tab body ...
    )}
    {view === "datasets" && <DatasetsAvailableSection />}
  </div>
);
```

Wrap the existing tab body in the `view === "market"` block — don't lose any existing UI.

- [ ] **Step 4: Typecheck and smoke-run**

```
cd dashboard && npm run typecheck
cd dashboard && npm run dev
```

Open the Available Data tab; the Market Data toggle should look identical to before. Click Datasets — should show the five FMP datasets in the table. Click a row — preview modal opens; with `as_of` defaulting to today, you should see rows (if the underlying parquet has data).

- [ ] **Step 5: Commit**

```
git add dashboard/src/components/DatasetsAvailableSection.tsx \
        dashboard/src/components/DatasetsFilterBar.tsx \
        dashboard/src/components/AvailableDataTab.tsx
git commit -m "feat(dashboard): Datasets view in Available Data tab"
```

---

## Task 24: End-to-end manual smoke (no commit; gated by user)

This is a real-API smoke that proves the whole stack works. **Only run after the user provides a real FMP API key** (free-tier is fine).

- [ ] **Step 1: Set the FMP key via settings**

```
curl -X PUT http://localhost:8000/api/settings/fmp-api-key -d 'key=<real_key>'
```

(Or whatever the existing settings endpoint pattern is — verify by reading `coordinator/api/routes/settings.py`.)

- [ ] **Step 2: Restart the coordinator** (settings are loaded at lifespan)

- [ ] **Step 3: Queue a small download**

```
quilt data datasets download fmp.house_disclosures
```

- [ ] **Step 4: Watch quota and download progress**

```
quilt data datasets quota
quilt data datasets downloads
```

Expected: `calls_used` ticks up; download moves `queued → running → completed`.

- [ ] **Step 5: Verify parquet on disk**

```
ls -la data/datasets/fmp/
python -c "import pandas as pd; print(pd.read_parquet('data/datasets/fmp/house_disclosures.parquet').head())"
```

Expected: rows with `event_date`, `knowledge_date`, `symbol`, `name`, etc.

- [ ] **Step 6: Browse in the dashboard**

Open `http://localhost:5173/data` (or whatever the dashboard port is), Available Data → Datasets → click `fmp.house_disclosures`. Modal opens, table populated.

- [ ] **Step 7: Run an algorithm-side test**

In a Python REPL or notebook:

```python
from datetime import datetime, timezone
from coordinator.services.datasets.storage import load_dataset, set_default_service, DatasetService
from pathlib import Path
import coordinator.services.datasets.providers.fmp_datasets  # noqa

set_default_service(DatasetService(data_root=Path("data")))
df_now  = load_dataset("fmp.house_disclosures", as_of=datetime.now(timezone.utc))
df_2023 = load_dataset("fmp.house_disclosures", as_of=datetime(2023, 1, 1, tzinfo=timezone.utc))
print("today:", len(df_now), "rows")
print("as of 2023-01-01:", len(df_2023), "rows (should be smaller)")
```

Expected: `as_of=2023-01-01` returns strictly fewer rows than `as_of=now`. If it returns more or equal, the bitemporal filter is broken — STOP.

---

## Self-review

**Spec coverage check:**
- ✅ Bitemporal storage layer (Tasks 2–3)
- ✅ `load_dataset` chokepoint + property test (Tasks 3–4)
- ✅ `DatasetAdapter` ABC + registry (Tasks 1, 7)
- ✅ `DatasetSpec` (Task 1) — with `_column` naming as spec requires
- ✅ `FMPAdapter` with three pagination strategies (Tasks 8–11)
- ✅ Five v1 datasets registered (Task 12)
- ✅ `DatasetDownload` + `QuotaUsage` models + Alembic migration (Task 5)
- ✅ `QuotaTracker` with reset semantics + 429 escalation (Task 6)
- ✅ `JobDispatcher` ABC + `BarsJobDispatcher` extraction + `DatasetJobDispatcher` with orphan recovery (Tasks 13–14)
- ✅ `ctx.dataset()` with no `as_of`, lag-only-delays, lookback exclusivity (Task 15)
- ✅ `LiveTickContext` formal inheritance + dataset cache surviving `reset_for_replay` (Task 16)
- ✅ Lifespan wiring (Task 17)
- ✅ REST `/api/datasets/*` including `/providers` (Task 18)
- ✅ CLI `quilt data datasets *` (Task 19)
- ✅ Frontend Available Data → Datasets toggle, coverage list, preview modal (Tasks 20–23)
- ✅ Existing `DatasetPreviewModal` rename to free the name (Task 21)

**Placeholder scan:** none. Every code-bearing step has actual code. The frontend tasks reference existing `DataTable` / `useQuery` patterns that the implementer will need to read once but the integration code is shown end-to-end.

**Type consistency:** `event_date`, `knowledge_date`, `request_payload`, `progress_pct`, `progress_message`, `error_message`, and the status vocabulary `queued | running | completed | failed | cancelled | paused_quota` are used consistently across model definition (Task 5), dispatcher (Task 14), and REST serialization (Task 18). Pagination strategies (`SINGLE | PAGE | DATE_RANGE`) match between registry (Task 1), adapter dispatch (Tasks 9–11), and dataset registrations (Task 12).

**Known external dependency:** Task 6 + Task 14 use `db_session_factory` and Task 18 uses `test_client` — both fixtures depend on existing conftest patterns. Implementer should look at e.g. `tests/coordinator/services/test_research_job_manager.py` for `db_session_factory` and `tests/coordinator/api/conftest.py` for `test_client` before writing new ones.

---

## Plan complete and saved.
