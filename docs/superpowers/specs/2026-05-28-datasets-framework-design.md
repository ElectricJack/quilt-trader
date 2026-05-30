# Time-Series Datasets Framework (FMP first) — Design

## Problem

The existing data layer is built around a single shape: OHLC bars keyed by `(provider, symbol, timeframe, date range)`. Providers (Polygon, YFinance, Tradier, Alpaca, Theta) all conform to a `fetch_bars(...)` duck-typed contract; storage lives at `data/market/<provider>/<symbol>/<timeframe>.parquet`; the `DownloadManager` / `MarketDataDownload` / `DataGoal` / `GoalProcessor` chain orchestrates it.

The user wants to add arbitrary time-series data sources that don't fit that mold:

- **Headline use case:** US House financial disclosures (via Financial Modeling Prep). The data is a stream of disclosure records, not bars. A canonical row has a *trade date* (when the politician traded) and a *disclosure date* (when the public learned, typically 30–45 days later). A copy-trade backtest must only see the row as-of the disclosure date — using the trade date silently injects forward-looking information that destroys the backtest's validity.
- **Generalization:** FMP alone offers ~100 endpoints (fundamentals with `acceptedDate` filing timestamps, insider trades with `filingDate`, earnings calendars, news, SEC filings, economic indicators). Other providers (Quandl, Alpha Vantage, direct SEC EDGAR) will follow. All of them produce time-series data with arbitrary fields, but each endpoint has its own naming for "when did this happen" vs "when was it knowable."
- **Operational constraints:** Free-tier API budgets are tight (FMP: 250 calls/day, no quota headers from the server). Downloads need to be persistent, resumable, and respect the quota across process restarts. The system must be usable for research without immediately requiring a paid plan.
- **Algorithm contract:** Algorithms must use the *same* data API in backtest and live mode. No conditional code, no `if backtest:` branches. The runtime decides what "now" means; the algorithm just asks for data.

## Goals

- A generic **bitemporal time-series adapter framework** that any provider can plug into.
- **Forward-bias prevention at the API boundary** — physically impossible for an algorithm to receive a row whose knowledge timestamp is in the future of its current simulation clock.
- **Persistent, quota-aware downloads** that survive process restarts and pause-then-resume on quota exhaustion.
- **One concrete provider implementation (FMP)** with five v1 datasets covering all framework variations (firehose vs symbol-keyed, true bitemporal vs single-timestamp, three pagination styles).
- **Browsable from the dashboard.** The existing "Available Data" tab gains a Datasets view (coverage list + paginated row preview).
- **Easy to add datasets and providers later** — new dataset = ~15 lines of declarative config; new provider = one adapter class.

## Non-goals (v1)

- The actual House-disclosures copy-trade strategy (a separate strategy package in `data/packages/`, not framework code).
- A declarative `DatasetGoal` model (parallel to `DataGoal`). v1 uses explicit per-download queueing.
- A background refresh scheduler keeping datasets fresh in live mode automatically. v1 expects manual or external scheduling.
- Bulk actions on datasets in the UI (Compare / Fill Gaps / Delete are out; view-only).
- A SQL/DuckDB query layer or lazy/streaming `DataFrame` returns. Parquet + pandas eager loads cover the scale we're at.
- Auto-discovery of FMP endpoints. Every dataset is explicitly registered, with intentional bitemporal mapping.

## Architecture

Two parallel data lanes that share infrastructure.

```
                       ┌─────────────────────────────┐
                       │     DownloadManager         │  shared: async runtime,
                       │   + per-provider semaphores │  per-provider concurrency,
                       │   + JobDispatcher registry  │  status broadcast WS channel
                       └─────────┬───────────┬───────┘
                                 │           │
                                 ▼           ▼
         ┌───────────────────────────┐   ┌─────────────────────────────────┐
         │   BARS LANE (existing)    │   │   DATASETS LANE (new)            │
         ├───────────────────────────┤   ├─────────────────────────────────┤
         │ PolygonProvider, etc.     │   │ FMPAdapter (+ future)            │
         │ ↓                         │   │ ↓                                │
         │ fetch_bars(...)           │   │ DatasetAdapter.fetch_dataset(...)│
         │ ↓                         │   │ ↓                                │
         │ MarketDataDownload (DB)   │   │ DatasetDownload (DB)             │
         │ ↓                         │   │ ↓                                │
         │ DataService               │   │ DatasetService                   │
         │ ↓                         │   │ ↓                                │
         │ data/market/<prov>/       │   │ data/datasets/<prov>/<name>/...  │
         │   <sym>/<tf>.parquet      │   │                                  │
         │                           │   │ + QuotaTracker (DB-backed)       │
         │ GoalProcessor (DataGoal)  │   │ + DatasetRegistry (declarative)  │
         │                           │   │ + load_dataset(name, as_of=…)    │
         └───────────────────────────┘   └─────────────────────────────────┘
```

The bars lane is unchanged. The datasets lane is the new work.

### New components

| Component | File | Responsibility |
|---|---|---|
| `DatasetAdapter` ABC | `coordinator/services/datasets/adapter.py` | Provider-agnostic contract every adapter implements. |
| `DatasetSpec`, `Pagination`, `DatasetRegistry` | `coordinator/services/datasets/registry.py` | Declarative per-dataset config and registry. |
| `QuotaTracker` | `coordinator/services/datasets/quota.py` | DB-backed per-provider daily counter; reset semantics; 429 escalation. |
| `DatasetService` | `coordinator/services/datasets/storage.py` | Bitemporal parquet I/O + `load_dataset(name, as_of, …)` query helper. |
| `DatasetDownload` model | `coordinator/database/models.py` | Parallel to `MarketDataDownload`; tracks queued / running / completed / failed / cancelled / paused_quota. |
| `QuotaUsage` model | `coordinator/database/models.py` | One row per `(provider, reset_window)`. |
| `JobDispatcher` ABC + `DatasetJobDispatcher` | `coordinator/services/download_job.py` | Extracts per-job-type dispatch from DownloadManager so adding job types doesn't require touching the manager. |
| `BarsJobDispatcher` | same file | Existing bars-execution logic lifted from DownloadManager into a dispatcher (no behavior change). |
| `FMPAdapter` | `coordinator/services/datasets/providers/fmp.py` | First concrete adapter. |
| FMP dataset registrations | `coordinator/services/datasets/providers/fmp_datasets.py` | The v1 catalog of five FMP endpoints. |
| REST routes | `coordinator/api/routes/datasets.py` | `/api/datasets/*` surface. |
| CLI commands | `sdk/cli/commands/data.py` (extension) | `quilt data datasets {list,show,download,downloads,quota}`. |
| Frontend | `dashboard/src/components/DatasetsAvailableSection.tsx`, `DatasetPreviewModal.tsx` (extended), `DatasetsFilterBar.tsx` | Browsing on the existing Available Data tab. |

### Bars lane: minimal disruption

`DownloadManager` currently inlines its bars-execution logic. The only refactor is to extract that into `BarsJobDispatcher` (no behavior change) and have `DownloadManager` dispatch by job model class. This keeps the bars lane working exactly as it does today while letting `DatasetJobDispatcher` slot in as a peer.

## Bitemporal storage layer

### On-disk schema

Every parquet file in `data/datasets/` has these two columns plus adapter-specific fields:

| Column | Type | Source |
|---|---|---|
| `event_date` | timestamp (UTC) | renamed from `spec.event_date_column` at write time |
| `knowledge_date` | timestamp (UTC) | renamed from `spec.knowledge_date_column`; equals `event_date` when spec has no knowledge column |
| ...adapter columns | per `spec.columns` | persisted as returned by the API |

Uniform `event_date` / `knowledge_date` naming means every downstream consumer speaks one schema regardless of which provider's adapter populated it.

### File layout

```
data/datasets/
├── fmp/
│   ├── house_disclosures.parquet                 ← firehose (symbol_keyed=False)
│   ├── senate_disclosures.parquet
│   ├── earnings_calendar.parquet
│   ├── insider_trading/                          ← symbol-keyed
│   │   ├── AAPL.parquet
│   │   └── …
│   └── income_statement/
│       ├── AAPL.parquet
│       └── …
└── <future_provider>/
    └── …
```

Path resolution:

```python
def _path_for(spec: DatasetSpec, symbol: str | None) -> Path:
    short_name = spec.name.split(".", 1)[1]
    base = DATA_ROOT / "datasets" / spec.provider
    if spec.symbol_keyed:
        return base / short_name / f"{symbol}.parquet"
    return base / f"{short_name}.parquet"
```

No partitioning in v1. Single file per (dataset, symbol). The upsert helper logs a warning when a file exceeds a configurable threshold (default 500MB). Year-partitioning is a v1.1 follow-up if a real dataset crosses that line.

### Upsert / dedup

```python
class DatasetService:
    async def upsert(self, spec: DatasetSpec, rows: list[dict],
                     symbol: str | None = None) -> int:
        df = self._normalize(spec, rows)               # rename to event_date/knowledge_date, parse dates
        path = self._path_for(spec, symbol)
        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=list(spec.id_columns), keep="last")
        df = df.sort_values(["event_date", "knowledge_date"])
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_parquet_write(df, path, compression="zstd")
        return len(df)
```

**Dedup semantics:** `spec.id_columns` includes `knowledge_date` itself, so amendments (same business key, later filing date) are *separate rows*. A re-fetch of the identical record is an exact duplicate and gets dropped. The bitemporal filter at read time naturally surfaces the right version per `as_of`.

**Atomic write:** write to `<path>.tmp` then `os.replace` to `<path>` — standard pattern, prevents corruption on crash mid-write.

### Schema evolution

Adapters return whatever the API gives. If FMP adds a field, it appears in new rows; old rows have NaN. If they remove one, new rows have NaN; old rows retain it. Pyarrow's parquet writer handles schema union on append. No explicit migration tooling for v1.

### `load_dataset` — the bitemporal chokepoint

```python
# coordinator/services/datasets/storage.py

def load_dataset(
    name: str,
    *,
    as_of: datetime,                       # required keyword — no default
    symbol: str | None = None,
    start: date | None = None,             # event_date >= start
    end: date | None = None,               # event_date <= end
    columns: list[str] | None = None,
) -> pd.DataFrame:
    spec = registry.get(name)
    path = _path_for(spec, symbol)
    if not path.exists():
        return _empty_frame_for(spec)
    df = pd.read_parquet(path, columns=columns)
    df = df[df["knowledge_date"] <= pd.Timestamp(as_of)]   # ← bitemporal filter
    if start: df = df[df["event_date"] >= pd.Timestamp(start)]
    if end:   df = df[df["event_date"] <= pd.Timestamp(end)]
    return df.sort_values(["event_date", "knowledge_date"]).reset_index(drop=True)
```

**Two invariants kept here, nowhere else:**

1. `as_of` has no default. Calling without it is a `TypeError`. There is no "forget to filter."
2. The `knowledge_date <= as_of` filter is the single chokepoint for forward-bias prevention. The whole framework's safety reduces to "did this line execute." A hypothesis-based property test pins this (Testing § below).

## Adapter framework

### `DatasetSpec` — declarative per-dataset config

```python
class Pagination(StrEnum):
    SINGLE     = "single"        # one request returns the full payload
    PAGE       = "page"          # ?page=0&limit=N, terminate on empty array
    DATE_RANGE = "date_range"    # ?from=...&to=..., chunked by date window

@dataclass(frozen=True)
class DatasetSpec:
    # Identity
    name: str                                # "fmp.house_disclosures"
    provider: str                            # "fmp"
    endpoint_path: str                       # "/stable/house-latest"

    # Bitemporal field mapping
    event_date_column: str                   # name of the API field holding event date
    knowledge_date_column: str | None        # None ⇒ knowledge_date := event_date

    # Storage
    symbol_keyed: bool
    id_columns: tuple[str, ...]              # composite key for dedup; should include knowledge col

    # Schema (hint, not strictly enforced)
    columns: dict[str, str]                  # field_name -> dtype hint

    # Pagination
    pagination: Pagination
    page_size: int = 100                     # for PAGE
    date_chunk_days: int = 365               # for DATE_RANGE

    # Tier gating (informational)
    free_tier: bool = True
```

**Renaming convention:** the suffix `_column` is deliberate — these are *column names* (strings), not dates. The whole framework's "bitemporal awareness" is one mapping table per dataset.

### `DatasetAdapter` — the provider contract

```python
class DatasetAdapter(ABC):
    provider: str  # class attribute

    @abstractmethod
    async def fetch_dataset(
        self,
        spec: DatasetSpec,
        params: dict,
        *,
        on_page: PageCallback | None = None,
        on_status: StatusCallback | None = None,
        on_rows: RowsCallback | None = None,   # invoked per page BEFORE following pagination
    ) -> list[dict]:
        ...
```

The framework handles: schema normalization (renaming bitemporal columns), upsert, status broadcast, persisting progress for resumability. The adapter only owns: HTTP, auth, pagination, response shape normalization (returning `list[dict]`).

### Registry

```python
_REGISTRY: dict[str, DatasetSpec] = {}

def register(spec: DatasetSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"duplicate dataset: {spec.name}")
    _REGISTRY[spec.name] = spec

def get(name: str) -> DatasetSpec: return _REGISTRY[name]
def list_all() -> list[DatasetSpec]: return list(_REGISTRY.values())
```

Per-provider dataset modules call `register(...)` at import time. The provider package's `__init__.py` imports its dataset module to trigger registration. App startup imports the providers package once; all specs land in `_REGISTRY`.

### Why ABC instead of Protocol

Adapter has shared behavior worth inheriting (quota check, http client lifecycle, common error → status mapping). Protocol-only would push that boilerplate into every adapter.

### Adding a new provider

1. Create `coordinator/services/datasets/providers/<name>.py` with a subclass of `DatasetAdapter`.
2. Create `coordinator/services/datasets/providers/<name>_datasets.py` with `register(DatasetSpec(...))` calls.
3. Wire credentials in `coordinator/main.py` lifespan (existing `Setting` + `EncryptionService` pattern).
4. Add the provider to `DownloadManager._provider_semaphores`.

No changes to models, registry, query helper, CLI, REST, storage, or frontend.

## Download orchestration + quota

### `DatasetDownload` DB model

```python
class DatasetDownload(Base):
    __tablename__ = "dataset_downloads"

    id: int                                # PK
    dataset_name: str                      # "fmp.house_disclosures"
    provider: str                          # denormalized for fast filter
    request_payload: str                   # JSON, adapter-specific (column name matches
                                           # ResearchJob convention; see "Prior art" below)

    status: str                            # queued | running | completed | failed
                                           #   | cancelled | paused_quota
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    rows_fetched: int = 0
    calls_consumed: int = 0

    # Progress / error reporting — column names match ResearchJob convention
    progress_pct: float = 0.0              # 0.0–1.0
    progress_message: str | None = None
    error_message: str | None = None

    # Resumability state
    last_page: int = 0
    last_event_date: datetime | None = None

    created_by: str                        # "manual" | "api" (| "scheduler" in v1.1)
```

### `QuotaUsage` DB model

```python
class QuotaUsage(Base):
    __tablename__ = "quota_usage"

    id: int                                # PK
    provider: str                          # "fmp"
    reset_window: date                     # date in reset_tz; (provider, reset_window) unique
    calls_used: int = 0
    daily_limit: int                       # snapshotted from Setting at row creation
    exhausted: bool = False                # forced True when 429 received
```

### `QuotaTracker`

```python
class QuotaExhausted(Exception):
    def __init__(self, provider, used, limit): ...

class QuotaTracker:
    def __init__(self, session_factory, reset_tz: tzinfo = timezone.utc):
        self._sf = session_factory
        self._tz = reset_tz
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, provider: str, daily_limit: int) -> None:
        """Atomic counter increment before a network call. Raises QuotaExhausted at limit."""

    async def mark_exhausted(self, provider: str) -> None:
        """Called when the server returns 429 despite local counter saying OK."""

    async def remaining(self, provider: str, daily_limit: int) -> int:
        ...
```

**Behaviors:**
- *Acquire-before-call*: adapter calls `quota.acquire()` before every HTTP request. If it raises, no call is made.
- *Per-provider async lock*: increments serialize within a provider so concurrent fetches don't race past the limit.
- *Reset window is wall-clock-driven*: when "today" in `reset_tz` differs from the row's window, a new row is created and the counter is fresh.
- *`mark_exhausted` is one-way for the current window*: trust the server; resume next reset.
- *Configurable `reset_tz`*: Setting `dataset_quota_reset_tz` (default `"UTC"`). FMP's actual reset is undocumented; user can change if they observe a different pattern.

### 429 handling

```python
# inside FMPAdapter, around each request
try:
    await self._quota.acquire("fmp", self._daily_limit)
except QuotaExhausted:
    raise   # bubbles to DownloadManager → status=paused_quota

resp = await self._http.get(url, params=params)
if resp.status_code == 429:
    await self._quota.mark_exhausted("fmp")
    raise QuotaExhausted("fmp", -1, self._daily_limit)
```

**`paused_quota` is not `failed`.** Progress (`last_page`, `last_event_date`) is retained. The DownloadManager rechecks paused jobs each outer-loop tick; when quota resets they resume automatically.

### DownloadManager integration

```python
# coordinator/services/download_job.py (new)

class JobDispatcher(ABC):
    job_model: type[Base]
    @abstractmethod
    async def execute(self, job, manager: "DownloadManager") -> None: ...

class BarsJobDispatcher(JobDispatcher):
    job_model = MarketDataDownload
    # existing logic lifted from DownloadManager

class DatasetJobDispatcher(JobDispatcher):
    job_model = DatasetDownload
    async def execute(self, job, manager):
        spec = registry.get(job.dataset_name)
        adapter = self.adapters[spec.provider]
        try:
            rows = await adapter.fetch_dataset(spec, json.loads(job.request_payload),
                                               on_rows=partial(self._persist, job, spec),
                                               on_page=partial(self._progress, job),
                                               on_status=...)
            job.status = "completed"
        except QuotaExhausted:
            job.status = "paused_quota"
        except asyncio.CancelledError:
            job.status = "cancelled"; raise
        except Exception as e:
            job.status = "failed"; job.error_message = str(e)

    async def recover_orphaned_jobs(self, session) -> None:
        """At startup, flip any jobs left in 'running' (process killed mid-fetch) back to
        'queued' so the manager picks them up on resume. Mirrors ResearchJobManager.recover."""
        ...
```

`DownloadManager` keeps its per-provider semaphores, async queue, and status broadcast. Adding a future job type (e.g., `OptionsChainDownload`) is one more dispatcher registration with no DownloadManager change.

### Prior art: `ResearchJobManager`

The `ResearchJobManager` shipped in the backtest-lab merge (`coordinator/services/research_job_manager.py`) implements the same shape: DB row → `asyncio.create_task` → poll → cancel-flag → progress callback → orphan recovery on restart. It's research-specific (hard-bound to sweep / walk-forward and to `OptimizationSession` FK), so we don't reuse it directly, but we adopt its conventions:

- **Column names**: `request_payload`, `progress_pct`, `progress_message`, `error_message` (matched above).
- **Status vocabulary**: `queued | running | completed | failed | cancelled` — plus our dataset-specific `paused_quota` for the FMP-budget-exhausted case.
- **`recover_orphaned_jobs()`** on the dispatcher at startup — any row left `running` (process killed mid-fetch) flips to `queued` so the manager picks it up on resume.

**Future cleanup, explicitly v1.1:** extract a shared `AsyncJobManager` base used by both `ResearchJobManager` and `DatasetJobDispatcher` (and any future async-job consumers). This is a non-trivial refactor with no immediate user value, so it is *out of scope for v1*. The convention-alignment here makes that future extraction mechanical.

## Algorithm-facing API

The existing `TickContext` ABC in `sdk/context.py` is the unified surface for algorithm data access. Both `BacktestTickContext` and `LiveTickContext` already expose `ctx.market_data(...)`, `ctx.data(...)`, `ctx.timestamp`. We add `ctx.dataset(...)` to the ABC and the algorithm gets one line that works in both modes.

### `ctx.dataset` on `TickContext`

```python
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
    if lag < timedelta(0):
        raise ValueError("lag must be non-negative")
    effective_as_of = self.timestamp - lag
    if lookback_days is not None:
        if start is not None or end is not None:
            raise ValueError("lookback_days is mutually exclusive with start/end")
        end = effective_as_of.date()
        start = end - timedelta(days=lookback_days)
    return load_dataset(name, as_of=effective_as_of, symbol=symbol,
                        start=start, end=end, columns=columns)
```

**The algorithm signature has no `as_of` parameter.** Not optional, not overridable. The runtime clock is the only source of truth. The foot-gun is eliminated at the type level — no parameter to misuse.

**`lag` only delays.** Validated `>= 0`. Used by strategies that deliberately model their own decision latency (e.g., "trade on this disclosure but with a 1-day end-of-day-review delay"). Cannot express "see the future."

**`load_dataset()` remains a free function** for notebooks, scripts, REST handlers — anywhere outside an algorithm. The `as_of` keyword is required there.

### Type cleanup

`LiveTickContext` currently matches `TickContext` structurally but does not formally inherit. v1 closes this gap so the new `dataset()` method is shared via inheritance and the type system enforces the contract.

### Caching

Per-tick reads of parquet would be brutal in backtests. So:

- **TickContext maintains a per-(name, symbol, columns) DataFrame cache** loaded lazily on first call; held for the lifetime of the run.
- **In-memory filtering per tick.** The cached DataFrame is the full file; the per-tick filter applies `knowledge_date <= effective_as_of` and the event_date window inline. Fast.
- **Live mode TTL** of 60s (configurable). On expiry, re-read from disk to pick up new rows written by any external refresh.

Cache invariant: the cache is bytes-from-disk; the per-tick filter is applied *on every call*, never cached. You can't accidentally return stale-filter data.

**Interaction with two-pass backtest execution.** The merged `BacktestEngine` two-pass model (`coordinator/services/backtest_engine_v2.py:137-169`) runs a discovery pass, calls `ctx.reset_for_replay()` (`backtest_tick_context.py:78-94`), then runs the real pass. `reset_for_replay()` deliberately preserves `_bars` and `_data_service` while clearing sim_time / cash / positions. The dataset cache should be added to the *preserved* set — same rationale as `_bars`: the bytes-from-disk are pass-invariant, and the per-tick `knowledge_date <= effective_as_of` filter still re-applies on every call. Pass-1 may populate the cache; pass-2 reuses it (a positive side effect — half the disk reads). Implementation note for whoever extends `reset_for_replay`: do not clear the dataset cache attribute.

### Live freshness

The existing live runtime's `RollingDataBuffer` pattern (pre-backfilled market data) is the right model. **v1 does not ship a dataset refresh scheduler.** Live algorithms run against whatever the user has queued via CLI / REST. Adding `DatasetRefreshScheduler` alongside the existing `GoalProcessor` is a v1.1 follow-up.

## FMP adapter + v1 datasets

### `FMPAdapter`

```python
class FMPAdapter(DatasetAdapter):
    provider = "fmp"
    BASE_URL = "https://financialmodelingprep.com"

    def __init__(self, api_key, http_client, quota_tracker,
                 daily_limit: int = 250, min_request_interval_s: float = 0.0):
        ...

    async def fetch_dataset(self, spec, params, *, on_page=None, on_status=None, on_rows=None):
        dispatch = {
            Pagination.SINGLE:     self._fetch_single,
            Pagination.PAGE:       self._fetch_paged,
            Pagination.DATE_RANGE: self._fetch_date_range,
        }[spec.pagination]
        return await dispatch(spec, params, on_page, on_status, on_rows)

    async def _request(self, endpoint_path: str, params: dict) -> Any:
        async with self._lock:                              # pacing + quota acquire under lock
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

**Three pagination strategies:**
- `_fetch_single` — one request; unwraps `{historical: […]}` for legacy v3, flat array for stable.
- `_fetch_paged` — `?page=0&limit=N`, terminate on empty. Each page passed to `on_rows` before incrementing.
- `_fetch_date_range` — walks `[from, to]` in `spec.date_chunk_days` windows; one request per window.

**Auth:** `?apikey=` query parameter (FMP does not support `Authorization: Bearer`).

**No retries on 5xx in v1.** Job fails; user re-queues. Adding bounded retry-with-backoff is a small follow-up if it proves noisy.

### v1 dataset catalog

Five datasets, picked to deliver the headline use case and exercise every framework variation so we don't ship untested code paths.

| Dataset | Shape | Bitemporal | Pagination |
|---|---|---|---|
| `fmp.house_disclosures` | firehose | ✓ (`transactionDate`, `disclosureDate`) | PAGE |
| `fmp.senate_disclosures` | firehose | ✓ (`transactionDate`, `disclosureDate`) | PAGE |
| `fmp.insider_trading` | symbol-keyed | ✓ (`transactionDate`, `filingDate`) | PAGE |
| `fmp.income_statement` | symbol-keyed | ✓ (`date`, `acceptedDate`) | SINGLE |
| `fmp.earnings_calendar` | firehose | single-timestamp (`date`) | DATE_RANGE |

Each `DatasetSpec` is ~15 lines of declarative config in `fmp_datasets.py`. Adding the next 10 FMP endpoints (price targets, dividends, news, SEC filings, …) after v1 ships is not spec work — it's incremental registrations.

**FMP's `fillingDate` typo on the fundamentals endpoint is preserved as a column** (informational); the bitemporal mapping uses `acceptedDate` (the real knowledge timestamp).

### Wiring at startup

```python
# coordinator/main.py lifespan — additions
fmp_key       = await _get_setting(session, "fmp_api_key", encryption)
fmp_limit     = int(await _get_setting(session, "fmp_daily_quota_limit") or 250)
fmp_interval  = float(await _get_setting(session, "fmp_min_request_interval_s") or 0.0)
quota_reset_tz = ZoneInfo(await _get_setting(session, "dataset_quota_reset_tz") or "UTC")

quota_tracker = QuotaTracker(session_factory, reset_tz=quota_reset_tz)

import coordinator.services.datasets.providers.fmp_datasets  # registers specs

dataset_adapters = {}
if fmp_key:
    dataset_adapters["fmp"] = FMPAdapter(
        api_key=fmp_key, http_client=http_client, quota_tracker=quota_tracker,
        daily_limit=fmp_limit, min_request_interval_s=fmp_interval,
    )

download_manager.register_dispatcher(DatasetJobDispatcher(adapters=dataset_adapters))
```

Same shape as the existing Polygon / Tradier wiring.

## User surface

### Settings (DB `Setting` table)

| Key | Type | Default | Notes |
|---|---|---|---|
| `fmp_api_key` | str (encrypted via existing `EncryptionService`) | — | required to enable adapter |
| `fmp_daily_quota_limit` | int | `250` | |
| `fmp_min_request_interval_s` | float | `0.0` | |
| `dataset_quota_reset_tz` | str | `"UTC"` | shared across providers |

Missing `fmp_api_key` ⇒ adapter not instantiated, datasets remain registered, `POST /api/datasets/downloads` for `fmp.*` returns 400 with a helpful message.

### CLI — `quilt data datasets *`

```
quilt data datasets list
quilt data datasets show <name>
quilt data datasets download <name>
    [--symbol AAPL] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--param key=value]
quilt data datasets downloads [--status …] [--provider fmp]
quilt data datasets quota
```

Each command is a thin shell over the REST endpoints below; matches existing `quilt data subscribe` style.

### REST — `/api/datasets/*`

```
GET    /api/datasets                          # list registered DatasetSpecs
GET    /api/datasets/{name}                   # spec details
GET    /api/datasets/providers                # per-adapter availability matrix:
                                              # [{name, available, reason}, …]
                                              # shape matches /api/data/providers (see
                                              # coordinator/api/routes/data.py:156-186);
                                              # "available" reflects whether credentials
                                              # were present at startup
GET    /api/datasets/coverage                 # coverage index for Available Data tab
GET    /api/datasets/{name}/coverage          # per-dataset coverage detail
GET    /api/datasets/{name}/rows              # paginated row preview
    ?symbol=AAPL
    ?as_of=YYYY-MM-DD[THH:MM:SS]              # default: now
    ?start=YYYY-MM-DD&end=YYYY-MM-DD          # event_date window
    ?columns=col1,col2
    ?limit=100&offset=0

POST   /api/datasets/downloads                # queue download
GET    /api/datasets/downloads                # list (?status=, ?provider=)
GET    /api/datasets/downloads/{id}           # one download detail
DELETE /api/datasets/downloads/{id}           # cancel queued

GET    /api/datasets/quota                    # all providers
GET    /api/datasets/quota/{provider}         # one provider
```

**Browsing default:** `/rows` defaults `as_of` to wall-clock now. Humans browsing files want to see everything on disk; the bitemporal cutoff exists to protect *algorithm* execution, not UI inspection. Users can pass `as_of` to interactively answer "what would I have known on March 15, 2024?" — useful for diagnosing strategies.

**Status broadcast** for in-progress `DatasetDownload`s reuses the existing WebSocket channel by tagging events with `job_type: "dataset"` alongside `"market_data"`.

### Frontend — Datasets in Available Data tab

The existing `AvailableDataTab.tsx` gains a `MarketData | Datasets` toggle at the top (default MarketData; existing UX preserved). The Datasets pane is a sibling view, not a replacement.

**New components in `dashboard/src/components/`:**
- `DatasetsAvailableSection.tsx` — top-level for the Datasets pane. Calls `useDatasetCoverage()`. Renders a `DataTable` of datasets with columns: name, provider, scope (firehose / per-symbol count), total rows, event range, "fresh as of" (max knowledge_date), file size. Row click opens preview modal.
- `DatasetPreviewModal.tsx` — generic counterpart to the existing market-data modal. Reuses `DataTable<T>` for the row grid. Top bar: dataset name + spec summary, symbol selector (if `symbol_keyed`), `as_of` date picker (default = now), event_date range picker. Pagination via TanStack. Powered by `usePagedDatasetRows(name, symbol, as_of, start, end, page)` (mirrors existing `usePagedMarketData`).
- `DatasetsFilterBar.tsx` — search + provider chips, mirroring existing `DataFilterBar`.

**API client additions** (`dashboard/src/api.ts`):
- `api.listDatasets()`
- `api.getDatasetCoverage()`
- `api.getDatasetCoverageDetail(name)`
- `api.getDatasetRows(name, params)`

**View-only in v1.** No bulk operations on datasets (Compare / Fill Gaps / Delete). Add later if useful.

## Testing strategy

Five layers, in priority order.

### 1. Forward-bias property test (safety-critical)

`tests/coordinator/services/datasets/test_forward_bias.py`:

```python
@hypothesis.given(rows=row_strategy, as_of=date_strategy)
def test_load_dataset_never_returns_future_knowledge(rows, as_of):
    _write_parquet(rows)
    df = load_dataset("test.fixture", as_of=as_of)
    assert (df["knowledge_date"] <= pd.Timestamp(as_of)).all()
```

Paired with:
- `load_dataset` without `as_of` raises `TypeError`
- `ctx.dataset(lag=timedelta(seconds=-1))` raises `ValueError`
- For any `lag >= 0`, the as_of computed inside `ctx.dataset` is `<= ctx.timestamp`

These guarantee at the API boundary that the framework cannot leak forward-looking data.

### 2. Storage tests

`tests/coordinator/services/datasets/test_storage.py`:
- Upsert into empty path creates file with normalized columns
- Upsert with overlapping rows dedups by `id_columns` keeping latest
- Upsert with amendments (same business key, different knowledge_date) keeps both rows
- Schema evolution: adding a column merges cleanly; removing one keeps old rows intact
- File layout: symbol_keyed vs firehose write to expected paths
- Single-timestamp datasets (`knowledge_date_column=None`) get `knowledge_date := event_date`

### 3. Adapter contract tests

`tests/coordinator/services/datasets/providers/test_fmp_adapter.py` (mocked httpx, same pattern as existing `test_tradier.py`):
- PAGE pagination terminates on empty response; `on_rows` called per page before incrementing
- SINGLE response unwrapped from `{historical: […]}` wrapper when present
- DATE_RANGE chunks `[from, to]` into windows; one request per window
- `?apikey=` appended to every request
- 401 raises `AdapterAuthError`; 429 raises `QuotaExhausted` and calls `quota.mark_exhausted`
- Pacing: `min_request_interval_s=0.1` produces ≥0.1s gap between back-to-back requests

Per-dataset round-trip: one test per registered dataset (`house_disclosures`, `senate_disclosures`, `insider_trading`, `income_statement`, `earnings_calendar`) — mock a realistic FMP payload, assert resulting parquet has the right normalized columns.

### 4. Quota tracker tests

`tests/coordinator/services/datasets/test_quota.py`:
- `acquire` increments; at limit raises `QuotaExhausted`, no further increment
- New reset window creates new row, counter starts at 0
- `mark_exhausted` flips flag; subsequent `acquire` raises even below limit
- 100 concurrent `acquire` calls never overshoot the limit
- Configurable `reset_tz` produces correct day boundaries (UTC vs `America/New_York`)

### 5. Orchestration + REST tests

`tests/coordinator/services/test_dataset_dispatcher.py`:
- `DatasetJobDispatcher.execute` success → `status=completed`, `rows_fetched` set
- Mid-fetch `QuotaExhausted` → `status=paused_quota` (not `failed`); `last_page` retained
- Generic exception → `status=failed`, `error_message` set
- `asyncio.CancelledError` → `status=cancelled`
- Resume: re-running a `paused_quota` job starts from `last_page`
- `recover_orphaned_jobs`: rows left `running` from a killed process get flipped back to `queued` at startup (matches ResearchJobManager.recover_orphaned_jobs)

`tests/coordinator/api/test_datasets_routes.py` — standard REST tests for every endpoint, including `/rows` with `as_of` applying the bitemporal filter.

### 6. Frontend tests

Mirror the existing `AvailableDataTab.tsx` test pattern:
- `DatasetsAvailableSection` renders coverage table from mocked API
- `DatasetPreviewModal` paginates and reflects `as_of` selector

### Out of scope

- Real FMP calls in CI. Real-API smoke gated behind `QUILT_FMP_LIVE_TESTS=1` for manual one-off runs (same pattern as existing providers).
- Load tests for quota tracker or storage upsert at scale.
- Fuzz / chaos beyond the one hypothesis property test.

## Migrations

Two new DB models (`DatasetDownload`, `QuotaUsage`) require an Alembic migration (or whatever the project uses). No changes to existing tables. The `BarsJobDispatcher` refactor is a code reshape — no data migration.

## Configuration sequence at first install

1. User installs/upgrades; migration creates `dataset_downloads` and `quota_usage` tables.
2. User opens Settings → adds `fmp_api_key` (encrypted).
3. Restart coordinator; lifespan loads key, instantiates `FMPAdapter`, registers `DatasetJobDispatcher`.
4. User runs `quilt data datasets list` or visits Available Data → Datasets tab.
5. User queues a download (CLI, REST, or future UI button).
6. Download executes; rows land at `data/datasets/fmp/<name>/...parquet`.
7. User browses rows in the Datasets preview modal or writes a strategy that calls `ctx.dataset("fmp.house_disclosures")`.

## Deferred for v1.1+

These are intentionally cut and tracked in `docs/superpowers/backlog.md`:

- **`DatasetGoal` declarative model** — parallel to `DataGoal`, lets users say "I want all house disclosures from 2020 forward, keep current." v1 uses explicit per-download queueing.
- **`DatasetRefreshScheduler`** — background scheduler that keeps datasets fresh in live mode automatically. v1 expects external scheduling.
- **Retry-with-backoff for 5xx** — v1 fails the job and surfaces the error.
- **Per-dataset quota budgets** — v1 enforces only a per-provider daily limit.
- **Streaming / lazy `DataFrame` returns** (Polars LazyFrame / DuckDB query) — v1 reads full parquet into pandas.
- **Year-partitioned parquet** for huge datasets — v1 is single-file per (dataset, symbol).
- **Bulk dataset operations in UI** (Compare / Fill Gaps / Delete) — v1 is view-only.
- **`/api/datasets/{name}/rows` query endpoint** is supplied for browsing only; if a future UI needs richer querying (joins, aggregations), consider a server-side DuckDB layer at that point.
- **Auto-discovery of FMP endpoints** — v1 requires explicit registration with intentional bitemporal mapping.
