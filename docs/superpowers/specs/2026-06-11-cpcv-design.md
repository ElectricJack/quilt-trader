# Combinatorial Purged Cross-Validation (CPCV)

**Status:** Design
**Date:** 2026-06-11
**Owner:** validation lab
**References:** López de Prado, *Advances in Financial Machine Learning* (2018), Ch. 7 + Ch. 12 §12.4; Bailey & López de Prado, *The Deflated Sharpe Ratio* (2014).

## Problem

The validation lab today supports `sweep` (parameter search) and `walk-forward` (one chronological train/test split per fold). Both produce a single Sharpe per (config, fold), which gives a single point estimate per config and a small handful of fold-level OOS Sharpes for walk-forward. That isn't enough signal for two questions we increasingly need to answer:

1. **"Is this config's OOS edge real, or noise from one chronological split?"** Walk-forward gives a few folds; multiple-testing inflation hits hard when you've also been sweeping.
2. **"Is the *selection process* (sweep → pick winner → trade) robust, or am I overfitting via the lookback?"** Walk-forward's chronological cuts don't probe selection-process variance.

CPCV addresses both by producing a *distribution* of OOS metrics — many synthetic backtest paths — that feed Bailey & LdP's deflated Sharpe ratio and the existing block-bootstrap CI machinery. It's the methodological complement to the multiple-testing corrections shipped in `multi_test.py` (Bonferroni, BH, SPA).

## Goal

Add CPCV as a third orchestrator sibling to `run_sweep` and `run_walk_forward` in `coordinator/services/validation/`. Two modes:

- **Mode A — purged k-fold CV.** Fixed config. Evaluate the strategy on N purged groups; report per-segment Sharpes + block-bootstrap CI of the mean.
- **Mode B — full CPCV with path reconstruction.** For each combinatorial split, run an inner sweep on train groups (selecting the best config), then evaluate that config on the test groups. Reconstruct `C(N-1, k-1)` complete synthetic backtest paths and report a distribution of path Sharpes, deflated Sharpe, and PSR.

Performance: comparable to the existing sweep at default sizes once a job-scoped bars cache eliminates redundant parquet loads — N=6, k=2, max_trials=20 finishes in ~3 minutes at parallelism=6 for typical 1-year backtests.

## Non-goals

- **Dashboard UI for CPCV results.** CLI prints summary; dashboard rendering is a follow-up spec under `/algorithms/:id/research/:session_id`.
- **Manifest-declared `trade_horizon`** auto-populating `purge_horizon`. v1 takes purge_horizon as user input (default 0).
- **CPCV-driven parameter promotion** (auto-pick winning config and write a `ParameterSet`).
- **Multi-symbol union timeline edge cases.** v1 uses the cost-profile default symbol's timeline for group boundaries.
- **Process-pool CPU parallelism.** Documented as a backlog item; this spec uses the existing asyncio.Semaphore lab parallelism.
- **Engine two-pass warmup elimination.** Same — backlog.

## Architecture

One new orchestrator file `coordinator/services/validation/cpcv.py` with `async def run_cpcv(db, runner_factory, *, session_id, mode, n_groups, test_groups_per_split, embargo, purge_horizon, ...)`. Sibling to `run_sweep`, `run_walk_forward`. Mode lives in the request payload.

`ResearchJob.kind` gets a new value: `"cpcv"`. `ResearchJobManager._dispatch_cpcv()` routes to `run_cpcv`. Status vocabulary (`queued | running | completed | failed | cancelled`) unchanged.

HTTP: `POST /api/research/sessions/:id/cpcv` returns `202 + JobResponse(job_id, kind="cpcv", status="queued")`. `GET /api/research/sessions/:id/jobs/:job_id` is kind-agnostic.

Each backtest is still a `BacktestRun` row, tagged with `optimization_session_id`. CPCV-specific structure (which split, group, role) lives in the `ResearchJob.result` JSON. No new tables.

The inner sweep logic is extracted from `run_sweep` into a reusable helper `_run_inner_sweep(window, base_config, parameter_space, search, max_trials, objective, …) -> InnerSweepResult` that returns the winning config + all run_ids. `run_sweep` continues to call this helper (no behavior change).

## Algorithm — groups, embargo, purge, splits

**Group partitioning.** Partition the session's `[date_range_start, date_range_end]` into `n_groups` chronologically contiguous, equal-bar-count groups. Bar count comes from the cost-profile default symbol's timeline. Groups are derived once at job start.

**Embargo.** For each test group's window `[t_start, t_end]`, skip the first `embargo` bars; the backtest's effective `date_range_start` is `t_start + embargo`. This handles: trades opened in a preceding train group that close in test, and lookback-window contamination at the boundary.

**Purge (mode B only).** For each split's train window, drop the last `purge_horizon` bars of every train group that's chronologically adjacent to a test group. Reason: if the inner sweep's "training" strategy enters positions that would close inside a test group, the train-side fitness is contaminated. Default `purge_horizon = 0`; recommended value = strategy's max trade horizon.

**Split enumeration.** `compute_cpcv_splits(N, k)` returns `C(N, k)` splits via `itertools.combinations(range(N), k)`. Each split: `{train_groups: list[int], test_groups: list[int]}`. Mode A doesn't use splits — it runs `N` backtests, one per group, with the fixed config.

**Boundary validation:**
- `n_groups < 4` → reject (path count ≤ 1; CPCV degenerates).
- `test_groups_per_split > n_groups // 2` → reject (LdP convention: more train than test).
- `embargo + purge_horizon ≥ group_size` → reject (would consume the entire group).

**Determinism.** Group boundaries are integer-derived from the union timeline; embargo/purge are integer bar counts; split combinations enumerate in `itertools` canonical order. Random seeds matter only for `search ∈ {random, latin, tpe}` in mode B; each split's inner sweep uses `seed = base_seed + split_index` so reruns are reproducible.

## Execution flow

### Mode A — purged k-fold CV

```
preload_bars_into_cache(symbols=manifest.assets, date_range=session.dates)
for group_i in range(n_groups):
    create BacktestRun(
        algorithm_id=session.algorithm_id,
        optimization_session_id=session.id,
        config_overrides=base_config,
        date_range_start=group_i.start + embargo_offset,
        date_range_end=group_i.end,
    )
    schedule via runner_factory(run_id, bars_cache=cache)
await all N runs honoring asyncio.Semaphore(parallelism)
read each run's sharpe_ratio + equity_curve from BacktestRun
result.segment_sharpes = [..N..]
result.bootstrap_ci = block_bootstrap_sharpe(concatenated_returns, n=10000)
```

Total backtests: **N**. No splits, no path reconstruction.

### Mode B — full CPCV

```
preload_bars_into_cache(symbols=manifest.assets, date_range=session.dates)
splits = compute_cpcv_splits(n_groups, test_groups_per_split)   # C(N,k)
for split in splits:                                            # honor parallelism
    train_window = merge_groups([groups[i] for i in split.train], purge=purge_horizon)
    selected = await _run_inner_sweep(
        train_window, base_config, parameter_space,
        search, max_trials_per_split, objective, objective_direction,
        seed=base_seed + split.index, runner_factory=runner_factory, bars_cache=cache,
    )
    split.selected_config = selected.config_overrides
    split.inner_trial_run_ids = selected.all_run_ids
    for group_idx in split.test:
        create BacktestRun(
            config_overrides=selected.config,
            date_range_start=groups[group_idx].start + embargo_offset,
            date_range_end=groups[group_idx].end,
        )
        split.oos_segment_run_ids[group_idx] = run_id
        schedule via runner_factory(run_id, bars_cache=cache)
await all OOS runs
paths = reconstruct_paths(splits, n_groups, test_groups_per_split)
result.summary = compute_summary(paths)
```

Total backtests: `C(N,k) × (max_trials_per_split + k)`.

**Parallelism semantics.** The session's `parallelism` cap applies to total in-flight backtests, not per-loop. The same `asyncio.Semaphore` already used by `run_sweep` governs dispatch.

**Cancellation.** Standard `ResearchJobManager` cancel-flag check between outer iterations and inside `_run_inner_sweep`'s trial loop. In-flight backtests honor the runner's existing cancel signal.

**Progress.** `progress_pct = completed_backtests / expected_total`. `progress_message`:
- Mode A: `"group 3/6 evaluated"`
- Mode B: `"split 7/15 inner-sweep trial 12/20"`

## Path reconstruction (mode B)

Each group appears as a test group in exactly `C(N-1, k-1)` splits. By choosing one split-membership per group such that each split is used by exactly one path slot, you reconstruct `C(N-1, k-1)` complete paths through the timeline.

For defaults (N=6, k=2): each group appears as test in 5 splits → **5 reconstructed paths**, each spanning the full timeline.

```python
def reconstruct_paths(splits, n_groups, k):
    group_splits = {g: [] for g in range(n_groups)}
    for split_idx, split in enumerate(splits):
        for g in split.test_groups:
            group_splits[g].append(split_idx)
    # Each group_splits[g] has C(N-1,k-1) splits. Take the j-th from each
    # group's list to form path j.
    n_paths = comb(n_groups - 1, k - 1)
    paths = []
    for path_idx in range(n_paths):
        path = []
        for group_idx in range(n_groups):
            split_idx = group_splits[group_idx][path_idx]
            run_id = splits[split_idx].oos_segment_run_ids[group_idx]
            path.append({"group": group_idx, "split": split_idx, "run_id": run_id})
        paths.append(path)
    return paths
```

**Per-path Sharpe.** Stitch the `n_groups` segment equity curves in time order; rebase each segment so equity is continuous (`segment_returns = pct_change(segment_equity)`, concatenate, `(1+r).cumprod()`); compute Sharpe on the concatenated returns at the bar frequency, annualized via the engine's existing `sharpe_ratio` helper.

## Summary metrics

**Mode A (per-segment):**
- `segment_sharpes: list[float]` — N values.
- `mean_segment_sharpe`, `median_segment_sharpe`, `std_segment_sharpe`.
- `bootstrap_ci_lower`, `bootstrap_ci_upper` — block-bootstrap CI of mean segment Sharpe (reuses `bootstrap.py:block_bootstrap_sharpe`).
- Per-segment table: `[{group, run_id, sharpe, total_return}, ...]`.

**Mode B (path-reconstructed + per-segment diagnostic):**
- `path_sharpes: list[float]` — `C(N-1, k-1)` values.
- `mean_path_sharpe`, `median_path_sharpe`, `std_path_sharpe`.
- `bootstrap_ci_lower`, `bootstrap_ci_upper` — bootstrap CI of mean path Sharpe; for each path bootstrap its returns once, then average across paths.
- **`deflated_sharpe_ratio`** (DSR) — new function `multi_test.py:deflated_sharpe(best_sharpe, returns_of_best_path, n_trials)` per Bailey & LdP (2014). `n_trials` = total inner-sweep trials across all splits (= `C(N,k) × max_trials_per_split`); accounts for multiple-testing inflation.
- **`probabilistic_sharpe_ratio`** (PSR) — new function `multi_test.py:probabilistic_sharpe(sharpe, returns, sharpe_benchmark=0.0)`; reports per-path PSR.
- Per-segment heatmap data: for each `(group, split)` cell that exists, the segment's standalone Sharpe + total return.

## Job-scoped bars cache

`coordinator/services/validation/bars_cache.py` exposes `BacktestBarsCache`:

```python
class BacktestBarsCache:
    def __init__(self):
        self._bars: dict[tuple[str, str], pd.DataFrame] = {}

    def preload(self, requirements: list[tuple[str, str]], start, end, db) -> None:
        """Load each (symbol, timeframe) once at cache construction."""

    def get(self, symbol: str, timeframe: str, start, end) -> pd.DataFrame:
        """Slice the preloaded DataFrame to a window. Returns a view (no copy)."""
```

The cache lives for the duration of one CPCV job. The orchestrator constructs it at job start, preloads each `(symbol, timeframe)` declared in the algorithm's manifest for the full session date range, and passes it into every `runner_factory(run_id, bars_cache=cache)` call. The cache is dropped at job end (GC reclaims memory).

**`BacktestRunner.run`** gains an optional `bars_cache: BacktestBarsCache | None = None` parameter. When provided, the runner constructs `BacktestTickContext` with a `_bars` dict populated from the cache instead of loading from disk. When `None`, behavior is unchanged (legacy callers, sweep, walk-forward).

**Memory bound.** Typical algo universe: 1–5 symbols × 1–2 timeframes × ≤5 years of bars. A 5-year 1-min SPY parquet is ~30 MB in pandas. Five such symbols ≈ 150 MB. Stays well under 1 GB even for heavy multi-symbol universes. Memory is monitored per-job; if cache size exceeds a configurable cap (default 2 GB), the orchestrator logs a warning and proceeds without preloading (graceful degradation to current behavior).

**Slicing semantics.** Each preloaded DataFrame is sorted by timestamp. `df.loc[start:end]` returns a view-compatible slice; ticking through the slice creates no additional row allocations.

**Future expansion.** `BacktestBarsCache` is designed so `run_sweep` and `run_walk_forward` can later adopt it without a redesign — orthogonal follow-up.

## Time estimates (with bars cache + parallelism)

Assumes per-backtest engine tick-loop cost of ~3s for a 1-year backtest on 1-min bars after setup overhead is eliminated. Realistic asyncio parallelism speedup at 6 concurrent ≈ 3x (CPU-bound, GIL-bound).

| Config | Total backtests | Wall time @ parallelism=6 |
|---|---|---|
| Mode A, N=6 | 6 | ~10 s |
| Mode A, N=10 | 10 | ~15 s |
| Mode B, N=6, k=2, max_trials=20 (default) | 330 | **~3 min** |
| Mode B, N=8, k=2, max_trials=30 | 924 | **~8 min** |
| Mode B, N=10, k=2, max_trials=50 (heavy) | 2,340 | **~20 min** |

For per-backtest costs > 3 s (e.g., 5-year backtests on 1-min bars) the estimates scale linearly. The pre-flight in `POST /api/research/sessions/:id/cpcv` returns the projected backtest count so the user sees the scale before kicking off.

## HTTP API

```
POST /api/research/sessions/:id/cpcv
```

Request body (Pydantic `CPCVRequest`):

```python
class CPCVRequest(BaseModel):
    mode: Literal["fixed", "select"] = "fixed"
    n_groups: int = 6                      # 4..20
    test_groups_per_split: int = 2         # 1..n_groups//2
    embargo: int = 5                       # bars; 0..group_size
    purge_horizon: int = 0                 # bars; mode="select" only
    # mode="select" only — required when mode is "select":
    parameter_space: dict | None = None
    search: Literal["grid", "random", "latin", "tpe"] | None = None
    max_trials_per_split: int | None = None
    objective: str | None = None           # defaults to session.default_objective
    objective_direction: Literal["max", "min"] | None = None
    parallelism: int | None = None         # defaults to session.default_parallelism
    seed: int | None = None
```

Response:
```python
class JobResponse(BaseModel):
    job_id: str
    kind: Literal["cpcv"]
    status: Literal["queued"]
    projected_backtest_count: int          # pre-flight estimate
```

Validation:
- `mode="select"` without `parameter_space` → 400.
- `n_groups < 4` or `test_groups_per_split > n_groups // 2` → 400.
- `embargo + purge_horizon` exceeds estimated `group_size` → 400.

`GET /api/research/sessions/:id/jobs/:job_id` returns the existing `JobResponse` shape augmented with the CPCV-specific `result` JSON when `status == "completed"`.

## CLI

```
quilt research cpcv <session_id>
    [--mode fixed|select]
    [--n-groups 6]
    [--test-groups 2]
    [--embargo 5]
    [--purge-horizon 0]
    [--search random]
    [--max-trials 20]
    [--objective sharpe_ratio]
    [--objective-direction max]
    [--parallelism]
    [--seed 0]
```

Thin client mirroring `quilt research walk-forward`: submits to the HTTP endpoint, polls the job, prints a progress bar, prints the result summary on completion.

On completion, prints to stdout:
- Mode A: `segment_sharpes` table, bootstrap CI, deflated Sharpe sentinel ("N/A — no selection").
- Mode B: per-path Sharpe table, mean ± CI, deflated Sharpe, mean PSR. Hints to view per-segment heatmap via the dashboard (when implemented).

## Persistence

`ResearchJob.result` JSON (≤ ~200 KB for max defaults):

```json
{
  "mode": "select",
  "n_groups": 6, "test_groups_per_split": 2,
  "embargo": 5, "purge_horizon": 0,
  "groups": [
    {"index": 0, "start": "2023-01-01T...", "end": "2023-03-15T...", "n_bars": 5040}
  ],
  "splits": [
    {
      "index": 0,
      "train_groups": [0,1,2,3], "test_groups": [4,5],
      "selected_config": {"lookback": 20, "vol_target": 0.10},
      "selected_objective": 1.43,
      "inner_trial_run_ids": ["run-…","…"],
      "oos_segment_run_ids": {"4": "run-…", "5": "run-…"}
    }
  ],
  "paths": [
    {"index": 0, "segments": [{"group": 0, "split": 3, "run_id": "run-…", "sharpe": 0.9}]}
  ],
  "summary": {
    "path_sharpes": [1.2, 0.8, 1.5, 0.9, 1.1],
    "mean_path_sharpe": 1.10,
    "median_path_sharpe": 1.10,
    "std_path_sharpe": 0.26,
    "bootstrap_ci_lower": 0.74, "bootstrap_ci_upper": 1.45,
    "deflated_sharpe_ratio": 0.95,
    "probabilistic_sharpe_zero": 0.82
  }
}
```

Individual backtests stay in `BacktestRun` rows linked via `optimization_session_id`. The mapping back from a `BacktestRun.id` to its CPCV role is a JSON lookup on the job's result. If profiling shows the JSON pattern becomes annoying for dashboard rendering, a thin `cpcv_segments` table is a future migration.

## Testing

**Unit tests** (`tests/coordinator/services/validation/`):

- `test_compute_groups` — N divides bar count vs doesn't, multi-symbol union timeline, off-by-one boundary checks.
- `test_compute_cpcv_splits` — N=6, k=2 → 15 splits; assert canonical ordering; counts for several `(N, k)` pairs.
- `test_reconstruct_paths` — N=6, k=2 → 5 paths; each group covered once per path; chronological ordering of segments; same `(run_id)` referenced consistently.
- `test_embargo_offsets_test_window_start` — test window's effective start = group start + embargo.
- `test_purge_trims_train_window_end` — train group adjacent to test loses last `purge_horizon` bars.
- `test_validation_rejects_n_lt_4`, `test_validation_rejects_k_too_large`, `test_validation_rejects_embargo_consumes_group`.
- `test_deflated_sharpe_known_values` — Bailey & LdP example reproduces.
- `test_psr_known_values` — known textbook example reproduces.
- `test_bars_cache_preload_and_slice` — preload (symbol, timeframe), retrieve a slice, confirm view semantics (no row-data copy), confirm a second slice request hits the cache without touching disk.
- `test_bars_cache_memory_cap` — exceeding cap logs a warning and the orchestrator proceeds without preloading.

**Integration** (mirroring `test_walk_forward.py`):

- `test_run_cpcv_mode_fixed` — N=4, k=1, fake `runner_factory` that fills BacktestRun rows with synthetic equity. Assert `result.segment_sharpes` length, bootstrap CI populated, no `result.paths`.
- `test_run_cpcv_mode_select` — N=4, k=2 (6 splits, 3 paths). Fake runner. Assert each split has selected_config + inner_trial_run_ids + oos_segment_run_ids; `result.paths` length = 3; each path covers all 4 groups in time order; `path_sharpes` populated; deflated Sharpe + PSR present.
- `test_cpcv_progress_and_cancellation` — mid-job cancel halts further dispatch; status transitions to cancelled.
- `test_cpcv_bars_cache_avoids_redundant_load` — instrumented runner counts disk loads; with cache enabled, count == 1 per (symbol, timeframe) across 50+ backtests.

**HTTP**:

- `test_post_cpcv_creates_job_returns_202`.
- `test_post_cpcv_rejects_invalid_mode_combo` — `mode=select` without `parameter_space` returns 400.
- `test_post_cpcv_rejects_degenerate_n_or_k` — 400 with helpful error.
- `test_get_job_polls_cpcv_status_and_returns_result_when_complete`.

## Risks

- **Compute cost at high config.** Mode B at N=10, k=2, max_trials=50 = 2,340 backtests. With the bars cache + parallelism=6, ~20 minutes for a typical 1-year 1-min backtest. Documented in CLI help and reflected in the pre-flight count returned by the POST endpoint. The user can lower `max_trials_per_split` if the projection exceeds their patience.
- **Memory.** Bars cache can balloon for huge multi-symbol multi-timeframe universes. The 2 GB cap + graceful degradation handles this.
- **JSON size.** Heavy jobs (~5,000 trial run_ids) push `result` to ~200 KB. Acceptable for SQLite JSON columns. If a future job needs more, table-extract.
- **Determinism of TPE.** Optuna's TPE depends on RNG state. Per-split seed (`base_seed + split_index`) gives reproducibility; documented in CLI.
- **Path equity continuity.** Stitching segments at group boundaries can produce a discontinuity if the cumulative equity at segment end != segment start of the next group's curve. We compute path Sharpe from `pct_change` per segment then concatenate returns — eliminates the level-mismatch issue. Documented in the path-reconstruction code.

## Open questions (resolved)

None blocking. The two decisions made during brainstorming:

- **Mode A doesn't path-reconstruct.** Fixed config → all paths collapse; report per-segment Sharpes + bootstrap CI instead. Decision: keep mode A as purged k-fold CV, not synthetic-CPCV.
- **Bars cache is job-scoped, not framework-wide.** First-shot scope; sweep and walk-forward can adopt the same `BacktestBarsCache` in a follow-up. Decision: ship in CPCV, document framework-wide adoption as backlog.

## Out of scope (logged in backlog)

- Process-pool CPU parallelism — real 6–8x speedup but framework-wide; needs `ProcessPoolExecutor`, pickling discipline, per-worker cache. Logged in backlog.
- Engine two-pass warmup elimination — 2x cost on first tick; refactor candidate.
- Per-tick allocation elimination in engine — `__slots__`, vectorized state.
- Manifest-declared `trade_horizon` auto-populating `purge_horizon`.
- CPCV-driven parameter promotion (auto-write a `ParameterSet` from path-winner).
- Dashboard UI for CPCV results (per-segment heatmap, path distribution chart) — lands under `/algorithms/:id/research/:session_id`.
