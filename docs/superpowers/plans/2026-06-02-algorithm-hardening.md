# Algorithm Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the mixed-TZ pd.to_datetime fix in `backtest_tick_context.py`, add `ctx.market_time()` + `ctx.is_market_open()` SDK helpers with a `market_timezone:` manifest field, migrate the 3 affected algorithms (`options-rolling-calls`, `options-ema-spreads`, `options-condor-martingale`) to the new helpers, then push the accumulated canonical-symbol + ET-helper patches to ~17 upstream `quilt-algo-*` repos via a scripted draft-PR rollout.

**Architecture:** Three strict phases with hard dependencies. Phase 1 ships framework code (5 commits) that's behavior-preserving for algorithms that don't use the new helpers. Phase 2 edits algorithm files in `/tmp/quilt-algos/` + mirrored `data/packages/` — gitignored, verified per-algo by re-running the 6/01 audit's smoke sweeps. Phase 3 runs a Python script that walks `/tmp/quilt-algos/`, branches + commits + pushes each modified repo, and opens draft PRs via `gh pr create`. Idempotent with `--dry-run` + `--only` safety flags.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio, pandas, `pandas-market-calendars>=4.4.0` (new dep), `zoneinfo` (stdlib), `gh` CLI (external).

**Spec:** [`docs/superpowers/specs/2026-06-02-algorithm-hardening-design.md`](../specs/2026-06-02-algorithm-hardening-design.md)

---

## File map

### Created
- `scripts/push_algorithm_patches.py` — one-off PR rollout script (Phase 3)
- `tests/scripts/test_push_algorithm_patches.py` — subprocess-mocked unit tests

### Modified
- `coordinator/services/backtest_tick_context.py` — mixed-TZ fix (2 spots) + `market_time()`/`is_market_open()` impls
- `sdk/context.py` — 2 abstract methods on `TickContext` ABC
- `sdk/manifest.py` — `market_timezone` field + smart-default logic + validation
- `worker/context.py` — `market_time()`/`is_market_open()` impls on `LiveTickContext`
- `worker/tick_loop.py` — thread `market_timezone` + `asset_types` through `TickProcessor` to `LiveTickContext`
- `worker/live_instance_runtime.py` — read `market_timezone` + `asset_types` from manifest, pass to `TickProcessor`
- `coordinator/services/backtest_runner.py` — pass `market_timezone` + `asset_types` to `BacktestTickContext`
- `pyproject.toml` — add `pandas-market-calendars`
- `tests/coordinator/services/test_backtest_tick_context.py` — 8 new tests
- `tests/sdk/test_manifest.py` — 5 new tests

### Algorithm files (Phase 2 — gitignored + external, mirrored in two locations)
- `/tmp/quilt-algos/<name>/{quilt.yaml,algorithm.py}` for: crypto-double-ema-4h, crypto-double-ema-trending, crypto-custom-etf, options-rolling-calls, options-ema-spreads, options-condor-martingale
- `/home/jkern/dev/quilt-trader/data/packages/<name>/{quilt.yaml,algorithm.py}` — same names, mirror

---

## Task 1: Add `pandas-market-calendars` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Read current dependency block**

```bash
grep -n "^dependencies\|^optional-dependencies\|pandas" pyproject.toml | head -10
```

Find the `dependencies = [...]` array (it's a top-level `[project]` block in PEP 621 format).

- [ ] **Step 2: Add the new dep**

Add the entry `"pandas-market-calendars>=4.4.0"` to the `dependencies` array (alphabetically sorted with the rest if the file does so; otherwise just append). For example, if the array currently contains `"pandas", "pyarrow", ...`, add it as `"pandas", "pandas-market-calendars>=4.4.0", "pyarrow", ...`.

- [ ] **Step 3: Install + smoke import**

```bash
pip install pandas-market-calendars>=4.4.0
python3 -c "import pandas_market_calendars as mcal; cal = mcal.get_calendar('XNYS'); print(cal.name, type(cal).__name__)"
```

Expected output:
```
NYSE NYSEExchangeCalendar
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add pandas-market-calendars>=4.4.0

Used by the new ctx.is_market_open() helper to check NYSE/CBOE
trading-day + holiday schedules.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Fix mixed-TZ `pd.to_datetime` crash in `backtest_tick_context.py`

**Files:**
- Modify: `coordinator/services/backtest_tick_context.py`
- Test: `tests/coordinator/services/test_backtest_tick_context.py`

### Step 1: Write the failing test

Append to `tests/coordinator/services/test_backtest_tick_context.py`:

```python
def test_market_data_loads_mixed_tz_string_timestamps_without_crash():
    """yfinance parquet output sometimes stores `timestamp` as ISO strings
    with mixed UTC offsets (DST transitions). pd.to_datetime without utc=True
    raises ValueError on that input. The tick context must coerce through UTC."""
    import pandas as pd
    disk_df = pd.DataFrame({
        "timestamp": [
            "2024-01-15T09:30:00-05:00",  # EST
            "2024-06-15T09:30:00-04:00",  # EDT — different offset, same column
        ],
        "open": [100.0, 105.0],
        "high": [101.0, 106.0],
        "low": [99.0, 104.0],
        "close": [100.5, 105.5],
        "volume": [1000, 2000],
    })

    mock_ds = type("DS", (), {
        "load_market_data": lambda self, src, sym, tf: disk_df.copy(),
    })()

    ctx = BacktestTickContext(bars={}, positions={}, cash=0, data_service=mock_ds)
    ctx.set_sim_time(datetime(2024, 7, 1, tzinfo=timezone.utc))

    # Should NOT raise ValueError on mixed-tz input
    out = ctx.market_data("AAPL", "1day", 10, source="polygon")
    # And the output should be tz-naive (UTC-normalized)
    if not out.empty and "timestamp" in out.columns:
        # cached bars get stored under self._bars; verify the cached form is naive
        key = ("polygon", "AAPL", "1day")
        cached = ctx._bars[key]
        assert cached["timestamp"].dt.tz is None, "expected naive datetime after UTC normalization"
```

### Step 2: Run test, verify fail

```bash
python3 -m pytest tests/coordinator/services/test_backtest_tick_context.py::test_market_data_loads_mixed_tz_string_timestamps_without_crash -v
```

Expected: FAIL with `ValueError: Mixed timezones detected`.

### Step 3: Apply the fix

Edit `coordinator/services/backtest_tick_context.py`. Find both call sites that do `pd.to_datetime(...).dt.tz_localize(None)` — there are two: one in the disk-load path (around line 165) and one in the on_miss path (around line 186). Replace each with the UTC-first coercion.

Old code (both spots):
```python
disk_df["timestamp"] = pd.to_datetime(disk_df["timestamp"]).dt.tz_localize(None)
```

New code (both spots):
```python
disk_df["timestamp"] = (
    pd.to_datetime(disk_df["timestamp"], utc=True)
    .dt.tz_convert("UTC")
    .dt.tz_localize(None)
)
```

For the on_miss path, the variable name is `fetched` instead of `disk_df`:
```python
fetched["timestamp"] = (
    pd.to_datetime(fetched["timestamp"], utc=True)
    .dt.tz_convert("UTC")
    .dt.tz_localize(None)
)
```

### Step 4: Run test, verify pass

```bash
python3 -m pytest tests/coordinator/services/test_backtest_tick_context.py::test_market_data_loads_mixed_tz_string_timestamps_without_crash -v
```

Expected: PASS.

### Step 5: Run the full test file for regressions

```bash
python3 -m pytest tests/coordinator/services/test_backtest_tick_context.py tests/coordinator/services/test_backtest_tick_context_reset.py -q
```

Expected: all green (the 20+ pre-existing tests + the 1 new one).

### Step 6: Commit

```bash
git add coordinator/services/backtest_tick_context.py tests/coordinator/services/test_backtest_tick_context.py
git commit -m "fix(tick-context): coerce mixed-tz string timestamps through UTC

pd.to_datetime without utc=True raises ValueError on parquet columns
containing ISO strings with mixed UTC offsets (DST transitions, common
in yfinance output). Surfaced during 6/01 algorithm audit when loading
data/market/yfinance/VIX/1day.parquet.

Both call sites (disk-load + on_miss) now pass utc=True then strip the
tz to retain the existing naive-UTC convention downstream.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Add `market_timezone` field to `QuiltManifest`

**Files:**
- Modify: `sdk/manifest.py`
- Test: `tests/sdk/test_manifest.py`

### Step 1: Write the failing tests

Append to `tests/sdk/test_manifest.py`:

```python
class TestMarketTimezone:
    def _make(self, asset_types, market_timezone=None):
        data = {
            "name": "test-algo",
            "type": "algorithm",
            "entry_point": "algorithm.py",
            "class_name": "TestAlgo",
            "requirements": {"asset_types": asset_types},
            "assets": [{"symbol": "BTCUSD" if asset_types == ["crypto"] else "AAPL",
                        "asset_class": asset_types[0]}],
        }
        if market_timezone is not None:
            data["market_timezone"] = market_timezone
        return data

    def test_market_timezone_explicit_field_honored(self):
        m = QuiltManifest._parse(self._make(["equities"], market_timezone="Europe/London"))
        assert m.market_timezone == "Europe/London"

    def test_market_timezone_default_for_equities(self):
        m = QuiltManifest._parse(self._make(["equities"]))
        assert m.market_timezone == "America/New_York"

    def test_market_timezone_default_for_crypto(self):
        m = QuiltManifest._parse(self._make(["crypto"]))
        assert m.market_timezone == "UTC"

    def test_market_timezone_default_for_mixed(self):
        # Mixed crypto + equities → most restrictive (ET) wins
        data = {
            "name": "test-algo", "type": "algorithm",
            "entry_point": "algorithm.py", "class_name": "TestAlgo",
            "requirements": {"asset_types": ["crypto", "equities"]},
            "assets": [
                {"symbol": "BTCUSD", "asset_class": "crypto"},
                {"symbol": "AAPL", "asset_class": "equities"},
            ],
        }
        m = QuiltManifest._parse(data)
        assert m.market_timezone == "America/New_York"

    def test_market_timezone_rejects_invalid_string(self):
        with pytest.raises(ManifestError, match="invalid market_timezone"):
            QuiltManifest._parse(self._make(["equities"], market_timezone="Not/A/Real/Zone"))
```

### Step 2: Run tests, verify fail

```bash
python3 -m pytest tests/sdk/test_manifest.py::TestMarketTimezone -v
```

Expected: 5 failures — `QuiltManifest` has no `market_timezone` attribute.

### Step 3: Add the field + default-derivation logic

Edit `sdk/manifest.py`. First, add the import at the top:

```python
from zoneinfo import available_timezones, ZoneInfo
```

Add a module-level helper for deriving the default timezone from asset types:

```python
def _default_market_timezone(asset_types: list[str]) -> str:
    """Return the most-restrictive default market timezone for a set of asset types.

    - Equities or options (alone or mixed with crypto) → America/New_York
    - Crypto only → UTC
    - Other / unknown → UTC fallback
    """
    types = set(asset_types or [])
    if types & {"equities", "options"}:
        return "America/New_York"
    if types == {"crypto"}:
        return "UTC"
    return "UTC"
```

Find the `QuiltManifest` class definition. Add `market_timezone: str` as a dataclass field (if it's a `@dataclass`) or as an attribute on `__init__`. Since the existing pattern in the file uses a class with attributes set in `_parse`, do this:

In `_parse`, after the `asset_types` is parsed (around the `requirements.asset_types` validation), add:

```python
        # Parse market_timezone — explicit field with smart default per asset_types
        explicit_tz = data.get("market_timezone")
        if explicit_tz is not None:
            if not isinstance(explicit_tz, str) or explicit_tz not in available_timezones():
                raise ManifestError(
                    f"invalid market_timezone {explicit_tz!r}; "
                    f"must be a valid IANA timezone name (e.g. America/New_York)"
                )
            market_timezone = explicit_tz
        else:
            market_timezone = _default_market_timezone(asset_types)
```

Then set `manifest.market_timezone = market_timezone` after constructing the `QuiltManifest` instance (or pass it through the constructor if the class uses one).

Use this command to find the right spot:

```bash
grep -n "QuiltManifest(\|manifest = \|asset_types =" sdk/manifest.py | head -10
```

Locate where `asset_types` becomes available, then where the manifest object is returned. Insert the `market_timezone` resolution after `asset_types` and before the return.

### Step 4: Run tests, verify pass

```bash
python3 -m pytest tests/sdk/test_manifest.py::TestMarketTimezone -v
```

Expected: all 5 pass.

### Step 5: Run full manifest test suite for regressions

```bash
python3 -m pytest tests/sdk/test_manifest.py -q
```

Expected: all green (no regressions).

### Step 6: Commit

```bash
git add sdk/manifest.py tests/sdk/test_manifest.py
git commit -m "feat(manifest): market_timezone field with smart default per asset_types

Adds optional top-level 'market_timezone' field, validated against
IANA timezones. When unset, derived from requirements.asset_types:
equities/options → America/New_York; crypto-only → UTC; mixed →
America/New_York (most restrictive). Surfaces on QuiltManifest as
.market_timezone (always populated).

Powers the upcoming ctx.market_time() and ctx.is_market_open()
SDK helpers, which need a per-algorithm timezone hint.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add `market_time()` + `is_market_open()` abstract methods to `TickContext`

**Files:**
- Modify: `sdk/context.py`

### Step 1: Read current ABC structure

```bash
grep -n "^class\|@abstractmethod\|^    def \|^    @property" sdk/context.py | head -30
```

Confirm `TickContext` is an ABC with `@abstractmethod` decorators (existing pattern). Identify where `market_data` is declared.

### Step 2: Add the two abstract methods

Edit `sdk/context.py`. After the existing `market_data` abstract method declaration (around line 50-52), add:

```python
    @abstractmethod
    def market_time(self) -> datetime:
        """Current sim time in the manifest's `market_timezone` (tz-aware datetime).

        For naive timestamps (the convention for `self.timestamp`), the value
        is localized to UTC first then converted to the market timezone.
        DST transitions are handled correctly via zoneinfo.
        """
        ...

    @abstractmethod
    def is_market_open(self) -> bool:
        """True if the current sim time is during the regular trading session
        for the manifest's asset_types.

        Equities/options manifests use the NYSE calendar (`XNYS`) via
        `pandas_market_calendars` — checks weekday + 09:30-16:00 ET window +
        excludes US trading holidays. Crypto-only manifests always return
        True. Mixed manifests use the most restrictive (equities calendar).
        """
        ...
```

Make sure `datetime` is imported at the top of `sdk/context.py` (probably already is).

### Step 3: Compile-check the ABC

```bash
python3 -c "from sdk.context import TickContext; print('TickContext methods:', [m for m in dir(TickContext) if not m.startswith('_')])"
```

Expected output includes `market_time` and `is_market_open`.

### Step 4: Run any existing context tests for regressions

```bash
python3 -m pytest tests/ -k "context" -q --tb=no 2>&1 | tail -5
```

Some tests may FAIL with `TypeError: Can't instantiate abstract class ... with abstract methods is_market_open, market_time` — that's the expected next-task work. If existing tests construct `TickContext` directly (not via a concrete subclass), this'll surface immediately.

If tests fail with `TypeError` about abstract methods, do NOT commit yet — proceed to Task 5 and Task 6 first to add the implementations, then commit Tasks 4 + 5 + 6 together at the end of Task 6. If all tests pass, commit now:

```bash
git add sdk/context.py
git commit -m "feat(sdk): add market_time() + is_market_open() abstract methods to TickContext

market_time() returns the current sim time tz-converted to the manifest's
market_timezone. is_market_open() returns True only during regular trading
hours for the manifest's asset_types (using pandas_market_calendars NYSE
calendar for equities/options, always-true for crypto-only).

Concrete implementations in BacktestTickContext + LiveTickContext follow
in the next two commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If tests failed and you deferred the commit: continue to Task 5, then commit at the end of Task 6.

---

## Task 5: Implement `market_time()` + `is_market_open()` in `BacktestTickContext`

**Files:**
- Modify: `coordinator/services/backtest_tick_context.py`
- Modify: `coordinator/services/backtest_runner.py` (thread `market_timezone` + `asset_types`)
- Test: `tests/coordinator/services/test_backtest_tick_context.py`

### Step 1: Write the failing tests

Append to `tests/coordinator/services/test_backtest_tick_context.py`:

```python
def test_market_time_returns_et_aware_during_edt():
    """During EDT (April-October), market_time should return UTC-4."""
    from zoneinfo import ZoneInfo
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        market_timezone="America/New_York",
        asset_types=["equities"],
    )
    ctx.set_sim_time(datetime(2024, 6, 15, 13, 30, tzinfo=timezone.utc))
    mt = ctx.market_time()
    assert mt.tzinfo is not None
    assert mt.utcoffset().total_seconds() == -4 * 3600  # EDT = UTC-4
    assert mt.hour == 9 and mt.minute == 30


def test_market_time_returns_et_aware_during_est():
    """During EST (November-March), market_time should return UTC-5."""
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        market_timezone="America/New_York",
        asset_types=["equities"],
    )
    ctx.set_sim_time(datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc))
    mt = ctx.market_time()
    assert mt.utcoffset().total_seconds() == -5 * 3600  # EST = UTC-5
    assert mt.hour == 9 and mt.minute == 30


def test_is_market_open_equities_during_session():
    """Tue 2024-06-18 14:00 UTC = 10:00 EDT = middle of session → True."""
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        market_timezone="America/New_York",
        asset_types=["equities"],
    )
    ctx.set_sim_time(datetime(2024, 6, 18, 14, 0, tzinfo=timezone.utc))
    assert ctx.is_market_open() is True


def test_is_market_open_equities_weekend():
    """Sat 2024-06-15 14:00 UTC → False."""
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        market_timezone="America/New_York",
        asset_types=["equities"],
    )
    ctx.set_sim_time(datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc))
    assert ctx.is_market_open() is False


def test_is_market_open_equities_pre_open():
    """Tue 2024-06-18 13:00 UTC = 09:00 EDT (30 min before open) → False."""
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        market_timezone="America/New_York",
        asset_types=["equities"],
    )
    ctx.set_sim_time(datetime(2024, 6, 18, 13, 0, tzinfo=timezone.utc))
    assert ctx.is_market_open() is False


def test_is_market_open_equities_holiday():
    """Mon 2024-01-01 (NY Day) 14:30 UTC = 09:30 EST → False (holiday)."""
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        market_timezone="America/New_York",
        asset_types=["equities"],
    )
    ctx.set_sim_time(datetime(2024, 1, 1, 14, 30, tzinfo=timezone.utc))
    assert ctx.is_market_open() is False


def test_is_market_open_crypto_always_true():
    """Sat 2024-06-15 14:00 UTC with crypto-only manifest → True (24/7)."""
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=0,
        market_timezone="UTC",
        asset_types=["crypto"],
    )
    ctx.set_sim_time(datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc))
    assert ctx.is_market_open() is True
```

### Step 2: Run, verify fail

```bash
python3 -m pytest tests/coordinator/services/test_backtest_tick_context.py -k "market_time or is_market_open" -v
```

Expected: failures — `BacktestTickContext.__init__` doesn't accept `market_timezone` / `asset_types` kwargs; methods don't exist.

### Step 3: Update `BacktestTickContext`

Edit `coordinator/services/backtest_tick_context.py`. Add the imports at the top:

```python
from zoneinfo import ZoneInfo
```

Add module-level cache for the market calendar (lazy-loaded, process-wide):

```python
_CALENDAR_CACHE: dict[str, object] = {}


def _get_calendar_cached(name: str) -> object:
    """Lazy-load and cache a pandas_market_calendars instance by name."""
    if name not in _CALENDAR_CACHE:
        import pandas_market_calendars as mcal
        _CALENDAR_CACHE[name] = mcal.get_calendar(name)
    return _CALENDAR_CACHE[name]


def _needs_market_calendar(asset_types: list[str]) -> bool:
    """True if any of the manifest's asset_types requires market-hours gating.
    Crypto-only manifests are 24/7 and never need a calendar."""
    types = set(asset_types or [])
    return bool(types & {"equities", "options"})


def _calendar_name_for(asset_types: list[str]) -> str:
    """Return the pandas_market_calendars name for the most-restrictive asset
    type in the manifest. Defaults to XNYS for equities/options. For
    crypto-only or unknown, returns XNYS as a placeholder — not consulted
    in those cases because _needs_market_calendar returns False."""
    return "XNYS"  # NYSE = US equities + listed options
```

Update `BacktestTickContext.__init__` signature to accept the new kwargs (find the existing `__init__`, around line 30-60):

```python
def __init__(
    self,
    bars: dict,
    positions: dict,
    cash: float,
    *,
    data_service: Optional[Any] = None,
    on_miss: Optional[Any] = None,
    default_source: Optional[str] = None,
    market_timezone: str = "UTC",            # NEW
    asset_types: Optional[list[str]] = None,  # NEW
    # ... preserve all existing kwargs unchanged ...
) -> None:
    # ... existing assignments unchanged ...
    self._market_timezone = market_timezone
    self._asset_types = asset_types or []
    self._needs_calendar = _needs_market_calendar(self._asset_types)
    self._calendar_name = _calendar_name_for(self._asset_types)
```

Add the two methods (place near `market_data`, after it):

```python
def market_time(self) -> datetime:
    if self._sim_time_now is None:
        raise RuntimeError("set_sim_time must be called before market_time")
    tz = ZoneInfo(self._market_timezone)
    ts = self._sim_time_now
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(tz)

def is_market_open(self) -> bool:
    if not self._needs_calendar:
        return True
    cal = _get_calendar_cached(self._calendar_name)
    now_market = self.market_time()
    schedule = cal.schedule(
        start_date=now_market.date(),
        end_date=now_market.date(),
    )
    if schedule.empty:
        return False
    open_ts = schedule.iloc[0]["market_open"].tz_convert(now_market.tzinfo)
    close_ts = schedule.iloc[0]["market_close"].tz_convert(now_market.tzinfo)
    return open_ts <= now_market < close_ts
```

### Step 4: Thread the new kwargs through `backtest_runner.py`

Find the `BacktestTickContext(` construction in `coordinator/services/backtest_runner.py`:

```bash
grep -n "BacktestTickContext(" coordinator/services/backtest_runner.py
```

Update that call to pass `market_timezone` and `asset_types` from the manifest. The manifest is already loaded in scope (typically as `manifest` variable). Change:

```python
ctx = BacktestTickContext(
    bars=bars, positions={}, cash=initial_cash,
    default_source=default_src,
    data_service=self._ds,
    on_miss=on_miss,
)
```

to:

```python
ctx = BacktestTickContext(
    bars=bars, positions={}, cash=initial_cash,
    default_source=default_src,
    data_service=self._ds,
    on_miss=on_miss,
    market_timezone=manifest.market_timezone,
    asset_types=(manifest.requirements.asset_types or []),
)
```

### Step 5: Run tests, verify pass

```bash
python3 -m pytest tests/coordinator/services/test_backtest_tick_context.py -k "market_time or is_market_open or mixed_tz" -v
```

Expected: all 8 new tests pass.

### Step 6: Run wider test suite for regressions

```bash
python3 -m pytest tests/coordinator/services/test_backtest_tick_context.py tests/coordinator/services/test_backtest_tick_context_reset.py tests/coordinator/services/test_backtest_engine.py tests/coordinator/services/test_backtest_engine_two_pass_clock.py -q
```

Expected: all green. If any pre-existing test constructs `BacktestTickContext` without the new kwargs, the defaults (`market_timezone="UTC"`, `asset_types=[]`) keep behavior unchanged for them.

### Step 7: Commit

```bash
git add coordinator/services/backtest_tick_context.py coordinator/services/backtest_runner.py tests/coordinator/services/test_backtest_tick_context.py
git commit -m "feat(backtest): BacktestTickContext implements market_time + is_market_open

Adds market_timezone + asset_types kwargs to __init__ (default UTC +
empty list — behavior-preserving for tests that don't set them).
backtest_runner passes the manifest values into the constructor.

market_time(): tz-converts self._sim_time_now to the manifest timezone.
is_market_open(): equities/options check NYSE calendar (cached per
process) with full holiday + session-hours support; crypto-only
always returns True.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If Task 4 was deferred (existing tests failed on abstract-method check), include `sdk/context.py` in the add:
```bash
git add sdk/context.py coordinator/services/backtest_tick_context.py coordinator/services/backtest_runner.py tests/coordinator/services/test_backtest_tick_context.py
```

---

## Task 6: Implement `market_time()` + `is_market_open()` in `LiveTickContext`

**Files:**
- Modify: `worker/context.py`
- Modify: `worker/tick_loop.py` (thread kwargs through `TickProcessor`)
- Modify: `worker/live_instance_runtime.py` (read from manifest)

### Step 1: Write a focused unit test

Append to a new file or existing live-context test (check `tests/worker/` first). Create `tests/worker/test_live_tick_context.py` if it doesn't exist:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from worker.context import LiveTickContext


def test_live_market_time_returns_et_aware():
    ctx = LiveTickContext(
        timestamp=datetime(2024, 6, 15, 13, 30, tzinfo=timezone.utc),
        mode="live",
        broker=MagicMock(),
        data_client=MagicMock(),
        market_timezone="America/New_York",
        asset_types=["equities"],
    )
    mt = ctx.market_time()
    assert mt.tzinfo is not None
    assert mt.utcoffset().total_seconds() == -4 * 3600
    assert mt.hour == 9 and mt.minute == 30


def test_live_is_market_open_crypto_always_true():
    ctx = LiveTickContext(
        timestamp=datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc),  # Saturday
        mode="live",
        broker=MagicMock(),
        data_client=MagicMock(),
        market_timezone="UTC",
        asset_types=["crypto"],
    )
    assert ctx.is_market_open() is True


def test_live_is_market_open_equities_weekend():
    ctx = LiveTickContext(
        timestamp=datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc),  # Saturday
        mode="live",
        broker=MagicMock(),
        data_client=MagicMock(),
        market_timezone="America/New_York",
        asset_types=["equities"],
    )
    assert ctx.is_market_open() is False
```

### Step 2: Run, verify fail

```bash
python3 -m pytest tests/worker/test_live_tick_context.py -v
```

Expected: failures — `LiveTickContext` doesn't accept `market_timezone` / `asset_types` kwargs.

### Step 3: Update `LiveTickContext`

Edit `worker/context.py`. Add imports at the top:

```python
from zoneinfo import ZoneInfo
```

Reuse the helpers from the backtest implementation. To avoid duplication, import them:

```python
from coordinator.services.backtest_tick_context import (
    _get_calendar_cached,
    _needs_market_calendar,
    _calendar_name_for,
)
```

Update `LiveTickContext.__init__` signature to accept the new kwargs:

```python
def __init__(
    self,
    timestamp: datetime,
    mode: str,
    broker: BrokerAdapter,
    data_client: DataClient,
    buffer: Any = None,
    custom_data: Optional[dict[str, pd.DataFrame]] = None,
    *,
    market_timezone: str = "UTC",
    asset_types: Optional[list[str]] = None,
) -> None:
    # ... existing assignments unchanged ...
    self._market_timezone = market_timezone
    self._asset_types = asset_types or []
    self._needs_calendar = _needs_market_calendar(self._asset_types)
    self._calendar_name = _calendar_name_for(self._asset_types)
```

Add the two methods (anywhere in the class body, suggest near `market_data`):

```python
def market_time(self) -> datetime:
    tz = ZoneInfo(self._market_timezone)
    ts = self._timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(tz)

def is_market_open(self) -> bool:
    if not self._needs_calendar:
        return True
    cal = _get_calendar_cached(self._calendar_name)
    now_market = self.market_time()
    schedule = cal.schedule(
        start_date=now_market.date(),
        end_date=now_market.date(),
    )
    if schedule.empty:
        return False
    open_ts = schedule.iloc[0]["market_open"].tz_convert(now_market.tzinfo)
    close_ts = schedule.iloc[0]["market_close"].tz_convert(now_market.tzinfo)
    return open_ts <= now_market < close_ts
```

### Step 4: Thread kwargs through `TickProcessor`

Edit `worker/tick_loop.py`. Update `TickProcessor.__init__` (around line 31-37) to accept the new kwargs:

```python
def __init__(self, runner: AlgorithmRunner, broker: BrokerAdapter,
             data_client: DataClient, coordinator_client: Any,
             idle_threshold_seconds: int = 60,
             live_observer: Optional["LiveObserver"] = None,
             buffer: Any = None,
             data_deps: Optional[list[dict]] = None,
             *,
             market_timezone: str = "UTC",
             asset_types: Optional[list[str]] = None) -> None:
    # ... existing assignments ...
    self._market_timezone = market_timezone
    self._asset_types = asset_types or []
```

Update the `LiveTickContext` construction in `process_tick` (around line 68-72):

```python
ctx = LiveTickContext(
    timestamp=timestamp, mode="live", broker=self._broker,
    data_client=self._data_client, buffer=self._buffer,
    custom_data=custom_data,
    market_timezone=self._market_timezone,
    asset_types=self._asset_types,
)
```

### Step 5: Thread kwargs through `live_instance_runtime.py`

Edit `worker/live_instance_runtime.py`. Find the `TickProcessor(` construction (around line 117). Manifest is already in scope as `manifest` (a dict from line 71).

Compute the timezone + asset_types from the manifest dict:

```python
# Around line 116, before TickProcessor construction:
from sdk.manifest import _default_market_timezone  # new helper from Task 3
mkt_tz = manifest.get("market_timezone") or _default_market_timezone(
    (manifest.get("requirements") or {}).get("asset_types") or []
)
asset_types_list = (manifest.get("requirements") or {}).get("asset_types") or []
```

Then pass them into `TickProcessor`:

```python
tick_processor = TickProcessor(
    runner=runner,
    broker=broker,
    data_client=data_client,
    coordinator_client=agent,
    live_observer=observer,
    buffer=buffer,
    data_deps=data_deps,
    market_timezone=mkt_tz,
    asset_types=asset_types_list,
)
```

### Step 6: Run tests, verify pass

```bash
python3 -m pytest tests/worker/test_live_tick_context.py -v
```

Expected: 3 new tests pass.

### Step 7: Run wider worker test suite for regressions

```bash
python3 -m pytest tests/worker/ -q --tb=no 2>&1 | tail -10
```

Expected: no new failures (pre-existing failures unrelated to this work are acceptable).

### Step 8: Run full coordinator + sdk test suite for cross-cutting regressions

```bash
python3 -m pytest tests/coordinator/ tests/sdk/ -q --tb=no 2>&1 | tail -5
```

Expected: no NEW failures. Pre-existing failures (the ~14 from the canonical-symbol branch) remain.

### Step 9: Commit

```bash
git add worker/context.py worker/tick_loop.py worker/live_instance_runtime.py tests/worker/test_live_tick_context.py
git commit -m "feat(worker): LiveTickContext implements market_time + is_market_open

Same implementation as BacktestTickContext (helpers shared via import
from coordinator.services.backtest_tick_context). market_timezone +
asset_types kwargs threaded from manifest → live_instance_runtime →
TickProcessor → LiveTickContext.

Algorithms running live now see market_time() return ET-aware datetimes
identical to backtest mode, fixing the UTC-vs-ET hour-comparison bug
that affected options-rolling-calls and options-condor-martingale.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If Task 4 was deferred, also include `sdk/context.py`:
```bash
git add sdk/context.py worker/context.py worker/tick_loop.py worker/live_instance_runtime.py tests/worker/test_live_tick_context.py
```

---

## Task 7: End-to-end smoke after Phase 1

**Files:** (no edits)

### Step 1: Run the full test suite for cross-cutting regressions

```bash
python3 -m pytest tests/coordinator/ tests/sdk/ tests/worker/ -q --tb=no 2>&1 | tail -10
```

Compare against the canonical-symbol-branch baseline (~14 pre-existing failures). Acceptable: same 14 still failing for the same reasons. NOT acceptable: any new failures attributable to Tasks 1-6.

### Step 2: Restart coord + smoke-check the manifest endpoint

```bash
quilt coord restart
sleep 2
python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://127.0.0.1:8000/api/algorithms')
algos = json.loads(r.read())
print(f'algorithms registered: {len(algos)}')
print('coord boots cleanly with framework changes ✓')
"
```

Expected: clean output, no exceptions.

### Step 3: Run a quick sanity sweep against a known-good crypto algorithm

```python
python3 <<'EOF'
import urllib.request, json, time
algo_id = "34b3eeec-9c7f-41bb-81ee-c348789571ec"  # crypto-double-ema-4h
body = {
    "name": f"phase1-smoke-{int(time.time())}",
    "hypothesis": "Phase 1 framework changes don't regress crypto-double-ema-4h",
    "algorithm_id": algo_id,
    "base_config": {
        "symbols": "BTCUSD",
        "ema1_short_minutes": 30, "ema1_long_minutes": 120,
        "ema2_short_minutes": 30, "ema2_long_minutes": 240,
        "rebalance_threshold": 0.01, "pct_invest": 0.9,
    },
    "parameter_space": {"ema1_long_minutes": [120, 360]},
    "pre_registered_criteria": {"min_sharpe": 0.0},
    "date_range_start": "2026-05-20",
    "date_range_end": "2026-05-31",
    "initial_cash": 10000.0,
    "cost_profile": "default",
}
req = urllib.request.Request(
    "http://127.0.0.1:8000/api/research/sessions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
sess = json.loads(urllib.request.urlopen(req).read())
sid = sess["id"]
print(f"session {sid}")
# Queue sweep
req = urllib.request.Request(
    f"http://127.0.0.1:8000/api/research/sessions/{sid}/sweep",
    data=json.dumps({"search": "grid", "max_trials": 2, "parallelism": 1, "seed": 0}).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
job = json.loads(urllib.request.urlopen(req).read())
job_id = job["job_id"]
for _ in range(60):
    j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:8000/api/research/sessions/{sid}/jobs/{job_id}").read())
    if j["status"] in ("completed", "failed", "cancelled"):
        break
    time.sleep(2)
print(f"sweep {j['status']}, runs: {len(j.get('run_ids', []))}")
import sqlite3
con = sqlite3.connect('data/quilt_trader.db')
con.row_factory = sqlite3.Row
for r in con.execute("SELECT id, status, trade_count, total_return FROM backtest_runs WHERE optimization_session_id=?", (sid,)):
    print(dict(r))
EOF
```

Expected: 2 runs `completed`, `trade_count > 0` on at least one, no errors.

### Step 4: No commit (verification only).

---

## Task 8: Apply canonical-symbol patches to 3 crypto algo Python files

**Files:** (gitignored + external — no quilt-trader commit)
- Edit: `data/packages/crypto-double-ema-4h/algorithm.py` AND `/tmp/quilt-algos/crypto-double-ema-4h/algorithm.py`
- Edit: `data/packages/crypto-double-ema-trending/algorithm.py` AND `/tmp/quilt-algos/crypto-double-ema-trending/algorithm.py`
- Edit: `data/packages/crypto-custom-etf/algorithm.py` AND `/tmp/quilt-algos/crypto-custom-etf/algorithm.py`

### Step 1: Locate bare-symbol string literals

For each of the 3 algos, find any string literal `"BTC"`, `"ETH"`, `"SOL"`, `"LTC"`, etc. that needs canonical replacement:

```bash
for algo in crypto-double-ema-4h crypto-double-ema-trending crypto-custom-etf; do
    echo "=== $algo ==="
    grep -nE '"BTC"|"ETH"|"SOL"|"LTC"|"DOGE"|"AVAX"|"LINK"|"BCH"|"XRP"|"ADA"|"ETC"' /tmp/quilt-algos/$algo/algorithm.py 2>/dev/null
done
```

### Step 2: Apply canonical replacements

For each algo, change the default `symbols` config string to use canonical names:
- `"BTC"` → `"BTCUSD"`
- `"ETH"` → `"ETHUSD"`
- comma-list `"BTC,ETH"` → `"BTCUSD,ETHUSD"`

Edit both copies (the `data/packages/` and `/tmp/quilt-algos/` versions in sync).

Specific spots (from prior audit):
- `crypto-double-ema-4h/algorithm.py`: `config.get("symbols", "BTC")` → `config.get("symbols", "BTCUSD")`
- `crypto-double-ema-trending/algorithm.py`: same pattern, default string update
- `crypto-custom-etf/algorithm.py`: default portfolio string (likely a comma list)

### Step 3: Verify each algo loads cleanly + runs a smoke backtest

For each of the 3, run the audit smoke pattern via API:

```bash
python3 <<EOF
import urllib.request, json, time, sqlite3

# Algorithm-id lookup
algos = json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/algorithms').read())
ids = {a["name"]: a["id"] for a in algos if a.get("source_path")}

for name in ["crypto-double-ema-4h", "crypto-double-ema-trending", "crypto-custom-etf"]:
    print(f"\n=== {name} ===")
    if name not in ids:
        print(f"  not found in /api/algorithms")
        continue
    body = {
        "name": f"phase2-{name}-{int(time.time())}",
        "hypothesis": f"verify {name} canonical-symbol Python fix",
        "algorithm_id": ids[name],
        "base_config": {},  # rely on algo's default (now canonical)
        "parameter_space": {"_unused": [1, 2]},  # dummy 2-trial sweep
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2026-05-20",
        "date_range_end": "2026-05-31",
        "initial_cash": 10000.0,
        "cost_profile": "default",
    }
    try:
        sess = json.loads(urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:8000/api/research/sessions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )).read())
        sid = sess["id"]
        print(f"  session {sid} created")
    except urllib.error.HTTPError as e:
        print(f"  CREATE FAILED: {e.code} {e.read().decode()[:200]}")
        continue
EOF
```

For Phase 2 acceptance, each algo's session must be createable (manifest passes Gate 1 + Gate 2 canonical-symbol validation). Don't worry yet about trades produced — that's Task 11.

### Step 4: No commit (gitignored).

---

## Task 9: Migrate `options-rolling-calls` to `ctx.market_time()`

**Files:** (gitignored + external)
- Edit: `data/packages/options-rolling-calls/algorithm.py` AND `/tmp/quilt-algos/options-rolling-calls/algorithm.py`

### Step 1: Inspect the existing ET-comparison code

```bash
grep -nE "now\.time\(|EARLIEST_ENTRY|CLOSE_BEFORE_EOD|now =|ctx\.timestamp" /tmp/quilt-algos/options-rolling-calls/algorithm.py
```

Expected: lines with `now = ctx.timestamp` (or similar) followed by `now.time() >= self.EARLIEST_ENTRY` etc.

### Step 2: Apply the helper migration

In both copies of `algorithm.py`, change `now = ctx.timestamp` to `now = ctx.market_time()`. The rest of the algorithm logic (`now.time()`, `now.hour`, etc.) stays the same — those values now reflect ET because `market_time()` is tz-converted.

### Step 3: Optionally add the `is_market_open()` guard at the top of `on_tick`

If the intent of `EARLIEST_ENTRY`/`CLOSE_BEFORE_EOD` is "only trade during regular hours", adding `if not ctx.is_market_open(): return []` at the top of `on_tick` makes the gate explicit and also handles holidays. Recommended for `options-rolling-calls` (clearly intended for regular hours).

### Step 4: Run a smoke backtest

```python
python3 <<'EOF'
import urllib.request, json, time, sqlite3

algos = json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/algorithms').read())
algo_id = next(a["id"] for a in algos if a["name"] == "options-rolling-calls")

# Use a window where SPY option chains exist (per audit notes: June 2024)
body = {
    "name": f"phase2-rolling-calls-{int(time.time())}",
    "hypothesis": "options-rolling-calls now fires during ET market hours",
    "algorithm_id": algo_id,
    "base_config": {},
    "parameter_space": {"_unused": [1, 2]},
    "pre_registered_criteria": {"min_sharpe": 0.0},
    "date_range_start": "2024-06-01",
    "date_range_end": "2024-06-25",
    "initial_cash": 10000.0,
    "cost_profile": "default",
}
sess = json.loads(urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:8000/api/research/sessions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)).read())
sid = sess["id"]
print(f"session {sid}")
job = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"http://127.0.0.1:8000/api/research/sessions/{sid}/sweep",
    data=json.dumps({"search": "grid", "max_trials": 2, "parallelism": 1, "seed": 0}).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)).read())
job_id = job["job_id"]
for _ in range(180):
    j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:8000/api/research/sessions/{sid}/jobs/{job_id}").read())
    if j["status"] in ("completed","failed","cancelled"):
        break
    time.sleep(2)
print(f"sweep {j['status']}")
con = sqlite3.connect('data/quilt_trader.db')
con.row_factory = sqlite3.Row
for r in con.execute("SELECT id, status, trade_count, total_return FROM backtest_runs WHERE optimization_session_id=?", (sid,)):
    print(dict(r))
EOF
```

Expected: at least one run with `trade_count > 0` (the original 0-trade bug is fixed).

### Step 5: No commit (gitignored).

---

## Task 10: Migrate `options-ema-spreads` to `ctx.market_time()`

**Files:** (gitignored + external)
- Edit: `data/packages/options-ema-spreads/algorithm.py` AND `/tmp/quilt-algos/options-ema-spreads/algorithm.py`

### Step 1: Inspect existing ET-comparison code

```bash
grep -nE "ts\.hour|ts\.minute|ts =|ctx\.timestamp" /tmp/quilt-algos/options-ema-spreads/algorithm.py
```

### Step 2: Apply the helper migration

Change `ts = ctx.timestamp` to `ts = ctx.market_time()` in both copies. Existing `ts.hour < start_hour`, `ts.hour == start_hour and ts.minute < start_min`, `ts.hour >= 15 and ts.minute >= 30` comparisons keep working because `market_time()` now returns ET.

### Step 3: Run a smoke backtest

Same template as Task 9 Step 4, swap algo name to `options-ema-spreads`. Expected: trade_count > 0.

### Step 4: No commit (gitignored).

---

## Task 11: Migrate `options-condor-martingale` to `ctx.market_time()`

**Files:** (gitignored + external)
- Edit: `data/packages/options-condor-martingale/algorithm.py` AND `/tmp/quilt-algos/options-condor-martingale/algorithm.py`

### Step 1: Inspect existing ET-comparison code

```bash
grep -nE "now\.hour|now\.minute|now =|ctx\.timestamp|create_hour|create_minute" /tmp/quilt-algos/options-condor-martingale/algorithm.py
```

### Step 2: Apply the helper migration

Change `now = ctx.timestamp` to `now = ctx.market_time()` in both copies. The existing `now.hour > self.create_hour`, `now.hour == self.create_hour and now.minute >= self.create_minute` checks now operate on ET as intended.

### Step 3: Optionally add `is_market_open()` guard

Same reasoning as Task 9. Recommended.

### Step 4: Run a smoke backtest

Same template as Task 9 Step 4, swap algo name to `options-condor-martingale`. Per the audit, `martingale_quantities` default starts with `0` (skip first cycle) — override in `base_config` to force trades:

```python
"base_config": {"martingale_quantities": "2,5,15,45"},
```

Expected: trade_count > 0 on at least one trial.

### Step 5: No commit (gitignored).

---

## Task 12: Write the upstream PR rollout script

**Files:**
- Create: `scripts/push_algorithm_patches.py`
- Create: `tests/scripts/test_push_algorithm_patches.py`

### Step 1: Write the failing tests

Create `tests/scripts/test_push_algorithm_patches.py`:

```python
"""Unit test for the upstream PR rollout script (Phase 3 of algorithm hardening)."""
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys
import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "push_algorithm_patches.py"


@pytest.fixture
def fake_repos(tmp_path):
    """Build a fake /tmp/quilt-algos/-equivalent directory with 3 repos:
    - clean (no changes)
    - dirty-in-scope (only quilt.yaml + algorithm.py modified)
    - dirty-out-of-scope (extra files modified — script should refuse)
    """
    root = tmp_path / "quilt-algos"
    for name in ("repo-clean", "repo-in-scope", "repo-out-of-scope"):
        d = root / name
        d.mkdir(parents=True)
        (d / "quilt.yaml").write_text("name: " + name + "\n")
        (d / "algorithm.py").write_text("# stub\n")
    return root


def _run(args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env,
    )


def test_dry_run_invokes_no_side_effects(fake_repos):
    """--dry-run prints intended actions but never invokes gh or git push."""
    result = _run(["--repos-root", str(fake_repos), "--dry-run"])
    # Script should run without errors
    assert result.returncode == 0, result.stderr
    # Output should mention each repo
    assert "repo-clean" in result.stdout
    assert "DRY-RUN" in result.stdout or "dry" in result.stdout.lower()


def test_only_filter_processes_single_repo(fake_repos):
    """--only filters to a single repo."""
    result = _run(["--repos-root", str(fake_repos), "--only", "repo-clean", "--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "repo-clean" in result.stdout
    # Other repos should not be mentioned in output
    assert "repo-in-scope" not in result.stdout
    assert "repo-out-of-scope" not in result.stdout


def test_missing_repos_root_exits_nonzero(tmp_path):
    """If --repos-root doesn't exist, script exits with an error."""
    bogus = tmp_path / "does-not-exist"
    result = _run(["--repos-root", str(bogus), "--dry-run"])
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "does not exist" in result.stderr.lower()
```

### Step 2: Run, verify fail

```bash
mkdir -p tests/scripts && touch tests/scripts/__init__.py
python3 -m pytest tests/scripts/test_push_algorithm_patches.py -v
```

Expected: fail because script doesn't exist.

### Step 3: Write the script

Create `scripts/push_algorithm_patches.py`:

```python
#!/usr/bin/env python3
"""One-off script: push canonical-symbol + ET-helper patches to each upstream
quilt-algo-* repo as a fix branch, open a draft PR via gh CLI.

Idempotent — re-running skips repos that have no local changes or already
have the branch pushed. Safety: refuses to push if files outside
quilt.yaml/algorithm.py are modified. Always opens PRs as drafts.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ALLOWED_FILES = {"quilt.yaml", "algorithm.py"}
BRANCH = "fix/canonical-symbols-and-market-time"
PR_TITLE = "fix: canonical symbols + ctx.market_time() compatibility"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repos-root", default="/tmp/quilt-algos",
                   help="Directory containing one subdir per upstream repo (default /tmp/quilt-algos)")
    p.add_argument("--only", default=None,
                   help="If set, process only this repo name (exact match)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print intended actions; perform no git/gh side effects")
    p.add_argument("--quilt-trader-sha", default=None,
                   help="quilt-trader main SHA to reference in PR body (default: read from current repo HEAD)")
    args = p.parse_args()

    root = Path(args.repos_root)
    if not root.exists():
        print(f"error: --repos-root {root} not found / does not exist", file=sys.stderr)
        return 1

    quilt_sha = args.quilt_trader_sha or _read_quilt_trader_sha()

    results: list[tuple[str, str, str]] = []  # (repo, status, detail)
    for repo_dir in sorted(root.iterdir()):
        if not repo_dir.is_dir():
            continue
        if args.only and repo_dir.name != args.only:
            continue
        status, detail = _process_repo(repo_dir, quilt_sha, args.dry_run)
        results.append((repo_dir.name, status, detail))
        print(f"{repo_dir.name:40} → {status}  {detail}")

    print()
    print(f"SUMMARY: {sum(1 for _,s,_ in results if s == 'PR_OPENED')} opened, "
          f"{sum(1 for _,s,_ in results if s == 'SKIP_CLEAN')} clean, "
          f"{sum(1 for _,s,_ in results if s == 'SKIP_BRANCH_EXISTS')} branch already pushed, "
          f"{sum(1 for _,s,_ in results if s.startswith('REFUSED'))} refused")
    return 0


def _read_quilt_trader_sha() -> str:
    """Best-effort: read the SHA of the current HEAD of the quilt-trader repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parents[1],
            text=True,
        )
        return out.strip()
    except subprocess.CalledProcessError:
        return "<unknown>"


def _process_repo(repo: Path, quilt_sha: str, dry_run: bool) -> tuple[str, str]:
    # 1. Check git status
    try:
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"],
            text=True,
        )
    except subprocess.CalledProcessError as e:
        return ("REFUSED_NOT_GIT_REPO", str(e))

    if not status.strip():
        return ("SKIP_CLEAN", "no local changes")

    # 2. Sanity-check: every changed file is in ALLOWED_FILES
    changed = []
    for line in status.splitlines():
        # Porcelain v1: "XY path" — strip the 2-char status + space
        path = line[3:].strip()
        changed.append(path)
    out_of_scope = [p for p in changed if Path(p).name not in ALLOWED_FILES]
    if out_of_scope:
        return ("REFUSED_OUT_OF_SCOPE", f"unexpected files: {out_of_scope}")

    if dry_run:
        return ("DRY-RUN", f"would commit + push + PR ({len(changed)} files)")

    # 3. Check if branch already exists locally
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", BRANCH],
        capture_output=True, text=True,
    )
    if branch_check.returncode == 0:
        # Branch exists — check if pushed
        remote_check = subprocess.run(
            ["git", "-C", str(repo), "ls-remote", "--heads", "origin", BRANCH],
            capture_output=True, text=True,
        )
        if remote_check.stdout.strip():
            return ("SKIP_BRANCH_EXISTS", "branch already pushed to origin")

    # 4. Create/check out branch, stage, commit, push
    subprocess.check_call(["git", "-C", str(repo), "checkout", "-B", BRANCH])
    subprocess.check_call(["git", "-C", str(repo), "add", *changed])
    commit_msg = (
        f"fix: canonical symbols + ctx.market_time() compat with quilt-trader\n\n"
        f"Apply changes required by quilt-trader main as of {quilt_sha}.\n\n"
        f"- Canonical-symbol manifest fixes (BTC→BTCUSD, ^VIX→VIX, etc.)\n"
        f"- ctx.timestamp → ctx.market_time() where ET wall-clock was intended"
    )
    subprocess.check_call(["git", "-C", str(repo), "commit", "-m", commit_msg])
    subprocess.check_call(["git", "-C", str(repo), "push", "-u", "origin", BRANCH])

    # 5. Open draft PR via gh CLI
    pr_body = _pr_body(repo.name, quilt_sha, changed)
    try:
        out = subprocess.check_output(
            ["gh", "pr", "create",
             "--draft",
             "--title", PR_TITLE,
             "--body", pr_body,
             "--repo", _origin_slug(repo)],
            cwd=str(repo), text=True,
        )
        return ("PR_OPENED", out.strip())
    except subprocess.CalledProcessError as e:
        return ("REFUSED_PR_FAILED", str(e))


def _origin_slug(repo: Path) -> str:
    """Extract owner/repo slug from origin URL."""
    out = subprocess.check_output(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    # https://github.com/owner/repo.git → owner/repo
    if out.endswith(".git"):
        out = out[:-4]
    if "github.com/" in out:
        return out.split("github.com/", 1)[1]
    return out


def _pr_body(repo_name: str, quilt_sha: str, changed_files: list[str]) -> str:
    return f"""## What

Apply canonical-symbol and ET market-time updates required by
quilt-trader main as of `{quilt_sha}`.

## Why

The framework (quilt-trader) now:
1. Rejects non-canonical symbols (`BTC` → `BTCUSD`, `^VIX` → `VIX`,
   OCC without `O:` prefix, etc.) at three validation gates — manifest
   install, data-store I/O, asset-service inputs.
   Spec: `docs/superpowers/specs/2026-05-31-canonical-symbol-design.md`.
2. Provides `ctx.market_time()` (tz-aware datetime in the manifest's
   `market_timezone`) and `ctx.is_market_open()` helpers.
   Spec: `docs/superpowers/specs/2026-06-02-algorithm-hardening-design.md`.
   Algorithms that previously compared `ctx.timestamp.hour` against
   ET-window literals misfired (UTC vs ET).

## Changes

Files touched: {changed_files}

## Validation

This patch was applied locally and verified via a backtest sweep before
opening this PR. See the spec links above for the full audit trail.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""


if __name__ == "__main__":
    sys.exit(main())
```

Make executable:

```bash
chmod +x scripts/push_algorithm_patches.py
```

### Step 4: Run tests, verify pass

```bash
python3 -m pytest tests/scripts/test_push_algorithm_patches.py -v
```

Expected: 3 tests pass.

### Step 5: Commit

```bash
git add scripts/push_algorithm_patches.py tests/scripts/test_push_algorithm_patches.py
git commit -m "feat(scripts): one-off PR rollout for canonical-symbol+ET-helper algo patches

Walks /tmp/quilt-algos/ (overridable via --repos-root), processes each
upstream repo per the playbook in 2026-06-02-algorithm-hardening-design.md:
status check → in-scope sanity (only quilt.yaml + algorithm.py allowed)
→ branch + commit + push → gh pr create --draft.

Idempotent: skips repos with no local changes or with the branch already
pushed. Safety: refuses if files outside quilt.yaml/algorithm.py modified.
--dry-run prints intended actions; --only filters to one repo.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Dry-run rollout against real `/tmp/quilt-algos/`

**Files:** (no edits — verification only)

### Step 1: Dry-run + capture output

```bash
python3 scripts/push_algorithm_patches.py --dry-run 2>&1 | tee /tmp/rollout-dry-run.log
```

Expected: prints one line per `/tmp/quilt-algos/<name>/` directory, with status SKIP_CLEAN, DRY-RUN (would push), or REFUSED_OUT_OF_SCOPE. The SUMMARY at the end shows the breakdown.

### Step 2: Inspect for surprises

```bash
grep "REFUSED" /tmp/rollout-dry-run.log
```

Expected: empty (all dirty repos should be in-scope per the Phase 2 work). If any REFUSED appear, investigate the unexpected files before proceeding.

### Step 3: No commit (verification only).

---

## Task 14: Open the actual draft PRs

**Files:** (no edits in quilt-trader — opens PRs in 17ish external repos)

### Step 1: Confirm gh CLI is authenticated

```bash
gh auth status 2>&1 | head -3
```

Expected: `Logged in to github.com as <user>`.

### Step 2: Execute the rollout

```bash
python3 scripts/push_algorithm_patches.py 2>&1 | tee /tmp/rollout.log
```

Expected: per-repo lines including PR URLs for newly-opened drafts. SUMMARY at end shows count breakdown.

### Step 3: Inspect SUMMARY + report URLs

```bash
grep -E "PR_OPENED|SUMMARY" /tmp/rollout.log
```

Output captures all PR URLs for follow-up review by the human.

### Step 4: No commit (the script is already committed; this task only runs it).

---

## Self-Review

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §2.1 Mixed-TZ fix | Task 2 |
| §2.2 Manifest `market_timezone` field | Task 3 |
| §2.3 SDK abstract methods | Task 4 |
| §2.4 Concrete impls (Backtest + Live) | Tasks 5 + 6 |
| §2.5 `pandas-market-calendars` dep | Task 1 |
| §3.1 Canonical-symbol patches in 3 algo Python | Task 8 |
| §3.2 ET-helper migration in 3 algos | Tasks 9, 10, 11 |
| §3.3 Optional `is_market_open` guards | Tasks 9, 11 (covered as optional step) |
| §3.4 Phase 2 local verification | Tasks 9-11 step 4 + Task 8 step 3 |
| §4 Phase 3 PR script + safety affordances | Task 12 |
| §4 Dry-run validation | Task 13 |
| §4 Actual rollout | Task 14 |
| §5.1 Phase 1 unit tests | Tasks 2, 3, 5, 6 (tests inline per task) |
| §5.2 Phase 2 integration acceptance | Tasks 9-11 |
| §5.3 Phase 3 script tests | Task 12 |
| §5.4 End-to-end sanity | Task 7 + Tasks 9-11 verify trades |

**Placeholder scan:** None. Every step contains the actual file paths, code, commands, and expected outputs.

**Type consistency:**
- `market_timezone: str` consistent across `QuiltManifest`, `BacktestTickContext.__init__`, `LiveTickContext.__init__`, `TickProcessor.__init__`
- `asset_types: Optional[list[str]]` consistent across the same constructors
- `_default_market_timezone(asset_types: list[str]) -> str` defined once in `sdk/manifest.py`, imported in `worker/live_instance_runtime.py`
- `_get_calendar_cached(name: str)` / `_needs_market_calendar(asset_types)` / `_calendar_name_for(asset_types)` defined once in `coordinator/services/backtest_tick_context.py`, imported from `worker/context.py`
- Both `BacktestTickContext.market_time()` and `LiveTickContext.market_time()` return `datetime` (tz-aware) — matches `sdk/context.py` ABC signature
- Both implementations of `is_market_open()` return `bool` — matches ABC

One subtle consistency note: `BacktestTickContext` uses `self._sim_time_now` (set via `set_sim_time`) while `LiveTickContext` uses `self._timestamp` (set in `__init__`). Both impls handle the naive-vs-aware case the same way (`if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)`).
