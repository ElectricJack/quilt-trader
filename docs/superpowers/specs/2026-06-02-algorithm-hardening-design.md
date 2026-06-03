# Algorithm Hardening: Mixed-TZ Fix + Market-Time SDK Helpers + Upstream PR Rollout

**Status:** Design approved 2026-06-02. Pending implementation plan.

**Motivation:** The canonical-symbol refactor (specs `2026-05-31-canonical-symbol-design.md` + commits `51f74eb..134fbae`) landed cleanly on `main`, and the 6/01 algorithm portfolio audit verified 16/19 installed algorithms produce real trades against it. Three follow-ups remain open in the backlog and have the same root theme — *make algorithms work reliably with our recent framework changes, and prevent the most common recurring failure modes*. They are tightly coupled in motivation but mechanically very different in shape, so this spec phases them rather than intermixing the work:

1. **Algorithm upstream PRs.** Local patches in `data/packages/<algo>/` (gitignored) and `/tmp/quilt-algos/<algo>/` (external repos) survive on the dev machine but won't survive a fresh install on another box. ~17 upstream `github.com/ElectricJack/quilt-algo-*` repos need PRs.
2. **Mixed-TZ `pd.to_datetime` crash in tick context.** `coordinator/services/backtest_tick_context.py` crashes loading any parquet whose `timestamp` column contains ISO strings with mixed UTC offsets (DST transitions, common in yfinance output). Surfaced during the audit when loading `yfinance/VIX/1day.parquet`.
3. **`ctx.market_time()` SDK helper.** Three audited algorithms (`options-rolling-calls`, `options-ema-spreads`, `options-condor-martingale`) hardcode ET wall-clock comparisons against `ctx.timestamp.hour` — but `ctx.timestamp` is UTC, so those windows fire at the wrong wall-clock time and miss the NY trading session. Caused all three to register `0 trades` in the audit.

**Goal:** ship a small framework addition (~5 commits) that makes (2) and (3) impossible to repeat, migrate the three affected algorithms to the new helpers, then push the accumulated `quilt-algo-*` patches upstream in a single scripted rollout.

---

## 1. Architecture

Three phases, strict dependency order, each producing a working/testable artifact.

### Phase 1 — Framework (this repo)
- 4-line mixed-TZ fix in `backtest_tick_context.py` (two call sites).
- New SDK abstract methods: `ctx.market_time()` (tz-aware datetime in manifest's `market_timezone`) and `ctx.is_market_open()` (weekday + hours + holidays via `pandas_market_calendars`).
- New manifest field `market_timezone:` with smart defaults derived from `requirements.asset_types` (equities/options → `America/New_York`, crypto-only → `UTC`, mixed → ET).
- Concrete implementations in `BacktestTickContext` and the live worker's `TickContext`.
- New `pandas-market-calendars` dependency.
- Full TDD coverage per `Testing` section.

**Phase 1 acceptance:** Phase 1 PR landed on `main`. All new unit tests pass. No existing test regresses. Algorithms that don't use the new helpers continue to work unchanged.

### Phase 2 — Algorithm content (gitignored + external)
- Apply canonical-symbol patches to algorithm Python in 3 algos that need them (`crypto-double-ema-4h`, `crypto-double-ema-trending`, `crypto-custom-etf` — default-symbol string constants).
- Migrate the 3 ET-using algos to `ctx.market_time()`:
  - `options-rolling-calls`: `now = ctx.timestamp` → `now = ctx.market_time()`.
  - `options-ema-spreads`: `ts = ctx.timestamp` → `ts = ctx.market_time()`.
  - `options-condor-martingale`: `now = ctx.timestamp` → `now = ctx.market_time()`.
- Optionally add `if not ctx.is_market_open(): return` guards at the top of `on_tick` in the same three algos (strategy decision per algo; apply where intent is clear).

Edits land in both `/tmp/quilt-algos/<algo>/` (upstream working copy) AND `/home/jkern/dev/quilt-trader/data/packages/<algo>/` (installed copy used by the runner). No commits to `quilt-trader` from this phase.

**Phase 2 acceptance:** re-run the 6/01 audit playbook against all 19 algorithms. 18/19 produce ≥ 1 trade in their audit sweep (the `options-ema-spreads-v2` PARTIAL remains, documented as a separate algorithm-internal issue with its own backlog entry).

### Phase 3 — Upstream PR rollout
- `scripts/push_algorithm_patches.py` walks `/tmp/quilt-algos/<algo>/` per dir, detects uncommitted patches, sanity-checks the diff scope, creates branch `fix/canonical-symbols-and-market-time`, pushes, opens PR via `gh pr create --draft` with a templated body.
- Idempotent: re-runs skip repos that have no local changes or that already have the branch pushed.
- Safety flags: `--dry-run`, `--only <algo>`, refuses to push if files outside `quilt.yaml`/`algorithm.py` are modified.
- PR body links to the canonical-symbol spec + this spec; includes per-algo change summary + a 1-line backtest validation note.
- User reviews + merges each PR manually per repo's normal workflow.

**Phase 3 acceptance:** all PRs created (target 17–19, depending on how many already exist as commits-not-PRs). Script log + URL list captured in the controller's terminal.

---

## 2. Framework (Phase 1)

### 2.1 Mixed-TZ `pd.to_datetime` fix

`coordinator/services/backtest_tick_context.py` has two call sites that crash on parquet columns with mixed UTC offsets:

```python
# Current (~lines 165 and 186) — crashes on mixed-tz string input:
disk_df["timestamp"] = pd.to_datetime(disk_df["timestamp"]).dt.tz_localize(None)
```

Fix: coerce through UTC first.

```python
# After:
disk_df["timestamp"] = (
    pd.to_datetime(disk_df["timestamp"], utc=True)
    .dt.tz_convert("UTC")
    .dt.tz_localize(None)
)
```

Same treatment applied to both call sites (disk-load path + `on_miss` path).

### 2.2 Manifest `market_timezone:` field

`sdk/manifest.py` `_parse` gains optional top-level `market_timezone:` field. When unset, value is derived from `requirements.asset_types`:

| `asset_types` contents | derived `market_timezone` |
|---|---|
| `["equities"]` or `["options"]` or both | `America/New_York` |
| `["crypto"]` only | `UTC` |
| mixed crypto + equities/options | `America/New_York` (most restrictive) |
| anything else (e.g. `["index"]`) | `UTC` |

Validated at parse time against `zoneinfo.available_timezones()`. Invalid timezone string raises `ManifestError`. The derived field surfaces on `QuiltManifest` as `manifest.market_timezone` (always populated, never `None`).

### 2.3 SDK `TickContext` helpers

`sdk/context.py` `TickContext` ABC gains two abstract methods:

```python
@abstractmethod
def market_time(self) -> datetime:
    """Current sim time in the manifest's market_timezone (tz-aware datetime).

    Equivalent to `self.timestamp.astimezone(ZoneInfo(manifest.market_timezone))`
    when timestamp is tz-aware; for naive timestamps (always UTC by convention),
    localize to UTC first then convert.
    """

@abstractmethod
def is_market_open(self) -> bool:
    """True if the current sim time is during the regular trading session
    for the manifest's asset_types.

    - Equities/options manifests: NYSE calendar via `pandas_market_calendars`.
      Returns True iff today is a trading day AND current time is within the
      regular session window (09:30-16:00 ET) AND not a holiday.
    - Crypto-only manifests: always True (24/7 markets).
    - Mixed manifests: most-restrictive wins (equities calendar applies).
    - Other (index-only, etc.): always True (no defined "market hours").
    """
```

### 2.4 Concrete implementations

`coordinator/services/backtest_tick_context.py` (`BacktestTickContext`):

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
    if not self._needs_market_calendar:
        return True
    cal = _get_calendar_cached(self._calendar_name)  # process-wide cache
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

`_market_timezone`, `_calendar_name`, and `_needs_market_calendar` are passed into `BacktestTickContext.__init__` by the runner, sourced from `manifest.market_timezone` + `manifest.requirements.asset_types`.

The live worker's `TickContext` (`worker/...`) gets parallel implementations. Both read from the same in-memory manifest object.

### 2.5 New dependency

Add to `pyproject.toml`:

```toml
"pandas-market-calendars>=4.4.0"
```

Single small dependency; pure-Python; already pulls `pandas` (which we have).

### 2.6 Files touched (Phase 1)

| File | Change |
|---|---|
| `coordinator/services/backtest_tick_context.py` | 2-spot mixed-TZ fix + `market_time()`/`is_market_open()` + cached-calendar helper |
| `sdk/context.py` | 2 abstract methods on `TickContext` ABC |
| `sdk/manifest.py` | `market_timezone` field + smart-default logic + validation |
| `worker/<tick_context module>` | parallel `market_time()`/`is_market_open()` impls (exact path TBD per worker code structure — discover during plan) |
| `pyproject.toml` | add `pandas-market-calendars>=4.4.0` |
| `tests/coordinator/services/test_backtest_tick_context.py` | mixed-TZ test + 5 market-hours tests |
| `tests/sdk/test_manifest.py` | 5 market-timezone tests |

---

## 3. Algorithm migrations (Phase 2)

All edits land in **both** `/tmp/quilt-algos/<algo>/` (upstream working copy) AND `/home/jkern/dev/quilt-trader/data/packages/<algo>/` (installed copy). No `quilt-trader` commits from this phase; Phase 3 picks up the upstream copies as PRs.

### 3.1 Canonical-symbol patches in algorithm Python (3 algos)

These were inventoried but not applied in the 6/01 audit:

| Algorithm | Patch |
|---|---|
| `crypto-double-ema-4h/algorithm.py` | `config.get("symbols", "BTC")` → `"BTCUSD"` |
| `crypto-double-ema-trending/algorithm.py` | same default-symbols string update |
| `crypto-custom-etf/algorithm.py` | default portfolio string canonicalized |

### 3.2 ET-helper migration (3 algos)

| Algorithm | Before | After |
|---|---|---|
| `options-rolling-calls/algorithm.py` | `now = ctx.timestamp; if now.time() >= self.EARLIEST_ENTRY` | `now = ctx.market_time(); if now.time() >= self.EARLIEST_ENTRY` |
| `options-ema-spreads/algorithm.py` | `ts = ctx.timestamp; if ts.hour < start_hour` | `ts = ctx.market_time(); if ts.hour < start_hour` |
| `options-condor-martingale/algorithm.py` | `now = ctx.timestamp; if now.hour > self.create_hour` | `now = ctx.market_time(); if now.hour > self.create_hour` |

`.hour`/`.minute`/`.time()` access now returns ET because `market_time()` is tz-converted.

### 3.3 Optional `is_market_open()` guards

For each of the 3 above, optionally add `if not ctx.is_market_open(): return` at the top of `on_tick`. Apply where the algorithm's intent is clearly "only trade during regular hours" — `options-rolling-calls` and `options-condor-martingale` clearly want this; `options-ema-spreads` already has its own hour-window logic and may not need the redundant gate. Implementer judgement.

### 3.4 Local verification

For each of the ~17 algorithms touched (3 from Section 3.1 + 3 from Section 3.2 + the ~16 from the 6/01 audit that received manifest-only patches), re-run the audit-style smoke sweep using the playbook captured in the 6/01 audit report. Assert `trade_count ≥ 1` for the parameter range known to trigger.

**Phase 2 acceptance gate:** 18/19 produce trades. The 19th (`options-ema-spreads-v2`) remains PARTIAL with its specific filter-chain issue documented in the backlog.

### 3.5 Files touched (Phase 2)

Algorithm-package files only. All gitignored or external. No surfacing commits in `quilt-trader`.

---

## 4. Upstream PR rollout (Phase 3)

### 4.1 The script: `scripts/push_algorithm_patches.py`

Python (not bash) for cleaner per-repo state-machine handling. Walks `/tmp/quilt-algos/<algo>/` per directory.

For each repo, the script:

1. Runs `git status --porcelain`.
2. **Clean**: skip (no patches to push).
3. **Dirty**: inspect changed file list.
   - Refuse + report if files outside `{quilt.yaml, algorithm.py}` are modified.
4. Run `git rev-parse --verify fix/canonical-symbols-and-market-time 2>&1`.
   - Branch exists locally and pushed → skip PR creation (assume already done; log).
   - Branch exists locally only → push it, then PR.
   - Branch doesn't exist → create from current HEAD, stage tracked changes, commit with templated message, push, PR.
5. `gh pr create --draft --title <templated> --body <templated>`.
6. Log the returned PR URL.

Final summary printed: per-repo status + URLs.

### 4.2 PR body template

```markdown
## What

Apply canonical-symbol and ET market-time updates required by
quilt-trader main as of <quilt-trader HEAD SHA>.

## Why

The framework (quilt-trader) now:
1. Rejects non-canonical symbols (`BTC` → `BTCUSD`, `^VIX` → `VIX`,
   OCC without `O:` prefix, etc.) at three validation gates —
   manifest install, data-store I/O, asset-service inputs.
   Spec: `docs/superpowers/specs/2026-05-31-canonical-symbol-design.md`.
2. Provides `ctx.market_time()` (tz-aware datetime in the manifest's
   `market_timezone`) and `ctx.is_market_open()` helpers.
   Spec: `docs/superpowers/specs/2026-06-02-algorithm-hardening-design.md`.
   Algorithms that previously compared `ctx.timestamp.hour` against
   ET-window literals misfired (UTC vs ET).

## Changes

- Manifest: <list of symbol → canonical fixes>
- Algorithm Python: <list of ctx.timestamp → ctx.market_time and default-symbols string fixes>

## Validation

Backtest run with cached coinbase data 2026-05-28 → 2026-05-31 produced
<N trades / no trades — note if zero-trade is expected per backlog>.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### 4.3 Safety affordances

- `--dry-run` flag: prints intended actions, performs no side effects.
- `--only <algo>` flag: process a single repo (validate flow on the first 1-2 PRs before bulk).
- Refuses to push if `git status` shows files outside `quilt.yaml`/`algorithm.py` — bails with "please review manually for <repo>".
- PRs created as `--draft`. User marks ready-for-review after eyeballing each.
- Script does **not** auto-merge. Each PR is merged manually per repo workflow.

### 4.4 Files touched (Phase 3)

| File | Change |
|---|---|
| `scripts/push_algorithm_patches.py` | created |
| `tests/scripts/test_push_algorithm_patches.py` | created — subprocess-mocked unit tests |

Per the precedent of `scripts/migrate_canonical_symbols.py`, this is a one-off script. It can be deleted in a follow-up after the PRs are merged, or kept for future bulk patches.

---

## 5. Testing strategy

Three test layers, mirroring the three phases.

### 5.1 Unit tests (Phase 1 — framework)

**`tests/coordinator/services/test_backtest_tick_context.py` — 8 new tests:**

- `test_market_data_loads_mixed_tz_timestamps_without_crash` — feed a DataFrame with mixed-TZ ISO-string timestamps (one EST, one EDT) through `market_data()`. Asserts no `ValueError` raised, output column is naive UTC.
- `test_market_time_returns_et_aware_during_edt` — sim time `2024-06-15 13:30:00 UTC` → `ctx.market_time()` returns datetime with tzinfo `-04:00`.
- `test_market_time_returns_et_aware_during_est` — sim time `2024-01-15 14:30:00 UTC` → `ctx.market_time()` returns datetime with tzinfo `-05:00`.
- `test_is_market_open_equities_during_session` — Tue 2024-06-18 14:00 UTC (10:00 ET) → True.
- `test_is_market_open_equities_weekend` — Sat 2024-06-15 14:00 UTC → False.
- `test_is_market_open_equities_pre_open` — Tue 2024-06-18 13:00 UTC (09:00 ET) → False.
- `test_is_market_open_equities_holiday` — Mon 2024-01-01 14:30 UTC (NY Day) → False.
- `test_is_market_open_crypto_always_true` — Sat 2024-06-15 14:00 UTC, crypto-only manifest → True.

**`tests/sdk/test_manifest.py` — 5 new tests:**

- `test_market_timezone_explicit_field_honored` — `market_timezone: Europe/London` → that value.
- `test_market_timezone_default_for_equities` — equities-only manifest, no field → `America/New_York`.
- `test_market_timezone_default_for_crypto` — crypto-only manifest, no field → `UTC`.
- `test_market_timezone_default_for_mixed` — `crypto` + `equities` in `asset_types` → `America/New_York`.
- `test_market_timezone_rejects_invalid_string` — `market_timezone: Not/A/Real/Zone` → `ManifestError`.

### 5.2 Integration tests (Phase 2 — algorithm migrations)

No new automated tests. Acceptance is empirical via the 6/01 audit playbook:

1. For each of the 19 algorithms in `data/packages/`, run a sweep using the same per-algo parameter recipe captured in the 6/01 audit.
2. Assert `trade_count ≥ 1` for the parameter range known to trigger (per the audit's findings table).
3. The 3 ET-helper migrations specifically must move `options-rolling-calls` and `options-condor-martingale` from PARTIAL to PASS. `options-ema-spreads-v2` remains PARTIAL (algorithm-internal issue, not in scope).

**Phase 2 acceptance gate:** 18/19 PASS.

### 5.3 Script tests (Phase 3 — upstream PR rollout)

**`tests/scripts/test_push_algorithm_patches.py` — 4 tests:**

- Mock `subprocess.run` for `gh` CLI + `git` invocations.
- Set up a fake `/tmp/quilt-algos/<name>/` tmpdir with simulated `git status --porcelain` outputs.
- `test_clean_repo_skipped` — repo with empty `git status` → script logs SKIP, no `gh` call.
- `test_dirty_repo_in_scope_creates_pr` — repo with only `quilt.yaml`+`algorithm.py` changes → script invokes `git checkout -b`, `git commit`, `git push`, `gh pr create --draft`.
- `test_dirty_repo_out_of_scope_refused` — repo with extra files modified → script bails with clear error, no `gh` call.
- `test_dry_run_invokes_no_side_effects` — `--dry-run` flag → script prints intended actions, all subprocess mocks called with `--dry-run` or not called at all.

Real `/tmp/quilt-algos/` is left alone during the test suite — only the actual one-off rollout invocation touches it.

### 5.4 End-to-end verification (post-Phase-2 sanity)

After Phase 1 lands on `main` and Phase 2 algorithm patches are applied locally:

1. Pick `options-rolling-calls` (was 0 trades in the 6/01 audit due to UTC/ET bug).
2. Create a research session + queue a sweep using the audit's parameter recipe.
3. Inspect run results:
   - `backtest_runs.status` = `completed`
   - `backtest_runs.trade_count` ≥ 1
   - `backtest_runs.equity_curve` has ≥ 1 snapshot
   - `/api/backtest-runs/<id>/trades` returns ≥ 1 trade
4. Verify the algorithm fired during the ET trading session (sample trade timestamps fall within 09:30–16:00 ET on weekdays).

This is the "did we actually fix the original ET/UTC zero-trade bug" gate. Required before Phase 3 PR rollout.

---

## 6. Out of scope

- **Holiday calendar customization.** v1 uses `pandas_market_calendars` defaults: NYSE for equities/options, no holidays for crypto (always-open). Other exchanges (LSE, TSE, etc.) supported via the same library but only documented; algorithms specify `market_timezone: Europe/London` and the framework infers `XLON` calendar etc. The exact derivation table goes in the implementation plan, not the spec.
- **Extended-hours sessions** (pre-market, after-hours). `is_market_open()` returns True for regular session only. If a future algorithm needs extended hours, add a manifest field `trading_session: equities_extended` (mentioned in backlog as option Y from the brainstorm).
- **Fixing the `options-ema-spreads-v2` PARTIAL.** Algorithm-internal filter-chain issue, not addressable by this spec. Tracked in backlog separately.
- **Migrating other algorithms not in the 3-algo ET-helper list.** Algorithms that already work using UTC `ctx.timestamp` continue to work. Only the 3 audit-identified ET-using algos get migrated.
- **Auto-merging the Phase 3 PRs.** Manual review + merge per repo.
- **Notification / status dashboard for Phase 3 PR state.** Script prints to stdout; user tracks PR state via GitHub's own UI.

---

## 7. Files touched (full list across all 3 phases)

### Phase 1 (this repo, committable)
- `coordinator/services/backtest_tick_context.py` — modified
- `sdk/context.py` — modified
- `sdk/manifest.py` — modified
- `worker/<tick_context module>` — modified (path discovered during plan)
- `pyproject.toml` — `pandas-market-calendars` added
- `tests/coordinator/services/test_backtest_tick_context.py` — modified
- `tests/sdk/test_manifest.py` — modified

### Phase 2 (gitignored + external, not committable to this repo)
- `data/packages/<algo>/{quilt.yaml,algorithm.py}` × ~6 algos materially touched
- `/tmp/quilt-algos/<algo>/{quilt.yaml,algorithm.py}` × ~17 algos (mirror of data/packages plus the 11 manifest-only-canonical that were already patched in audit)

### Phase 3 (this repo, committable)
- `scripts/push_algorithm_patches.py` — created
- `tests/scripts/test_push_algorithm_patches.py` — created
