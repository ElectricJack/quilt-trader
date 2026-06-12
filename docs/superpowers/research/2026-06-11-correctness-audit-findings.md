# Correctness Audit — Findings (2026-06-11)

Module-by-module audit of quilt-trader with emphasis on the
simulation/research/backtesting system. Every finding below was verified by
reading the code directly (parallel subagent reports were treated as leads,
not conclusions — two subagent claims were refuted on inspection, see §6).

Severity scale:
- **CRITICAL** — silently corrupts backtest results / violates the
  "honest backtests" contract.
- **HIGH** — wrong numbers in research/validation outputs.
- **MODERATE** — live/worker/data-layer correctness or robustness gaps.
- **MINOR** — statistical nits, edge cases.

Failing tests that demonstrate each finding (33 tests, all red as of this
commit) live in:

- `tests/coordinator/services/test_audit_engine_findings.py` (F1, F1b, F2, F3, F7)
- `tests/coordinator/services/test_audit_pricing_metrics_findings.py` (F4, F5, F6, F8, F9, F10, F20a, F20b)
- `tests/coordinator/services/validation/test_audit_cpcv_findings.py` (F11, F12)
- `tests/coordinator/services/test_audit_data_layer_findings.py` (F13, F14, F15)
- `tests/worker/test_audit_worker_findings.py` (F16, F17, F18)
- `tests/sdk/test_audit_signals_findings.py` (F19)

Each test's docstring cites its finding ID.

---

## 1. Backtest engine (`coordinator/services/backtest_engine_v2.py`)

### F1 — CRITICAL: one-bar look-ahead in engine-side price lookups

The engine sets `sim_time = bar.timestamp + tf_duration` (line ~243) — i.e.
sim_time equals the **next** bar's open timestamp, since bars are stamped at
open time. The tick context correctly subtracts `tf_duration` before its
searchsorted cutoff (`backtest_tick_context.py:241`), but three engine-side
helpers do not:

1. `_lookup_symbol_close` (line ~916):
   `idx = np.searchsorted(ns, cutoff.value, side="right") - 1` with
   `cutoff = sim_time`. A bar stamped exactly at sim_time (the *next* bar,
   whose interval has not yet elapsed) is included, so the helper returns the
   **next bar's close**. Affected consumers:
   - `ctx.update_account` positions marking (lines ~247-252)
   - equity-curve points (line ~479)
   - underlying price fed into options MTM (line ~976)
2. Off-clock fill-bar resolution (lines ~320-326): same inclusive
   `searchsorted(..., "right") - 1` at `cutoff = sim_time` → fills execute on
   a bar one period ahead of the documented "signal at T fills at T+1 open".
3. `_lookup_option_price` (line ~591): `visible = df[ts <= cutoff]` with
   `cutoff = ctx._sim_time_now` → option fills can use the next day's option
   bar.

Consequence: equity curves, option MTM marks, and off-clock fills all peek one
bar into the future. An algorithm cannot exploit it directly (signals still
queue), but every reported equity/PnL number is shifted, and option fills are
optimistic. This violates the core "no same-bar information" rule of Spec D §3.

**Fix direction:** all engine-side lookups must use
`cutoff = sim_time - tf_duration_of_that_symbol's_frame` (match the tick
context), or equivalently use `side="left"` semantics excluding bars stamped
at sim_time whose interval hasn't elapsed. Expiry settlement (F7) shares this
fix.

### F2 — CRITICAL: position-flip leaves stale `avg_price` in `_apply_fill`

`_apply_fill` (lines ~787-837):
- Short→long flip (lines ~799-801): when a buy closes a short and flips long,
  `avg_price` is only reset when the resulting `quantity == 0`. A flip
  (e.g. short −5, buy 8 → long 3) keeps the **old short avg_price** for the
  new long position → all subsequent unrealized/realized PnL on the long is
  wrong.
- Long→short flip (line ~818): `ps.quantity -= fill.quantity` can cross zero
  for options (equity overselling is rejected earlier at lines ~363-377, but
  options sells are not), keeping the old long `avg_price` for the new short.

**Fix direction:** on a crossing fill, realize PnL on the closed portion at
the old avg_price, then set `avg_price = fill price` for the residual new
position (and flip the sign of quantity explicitly).

### F3 — CRITICAL: union-clock rows mix symbols' OHLC (`keep="first"`)

`_build_union_clock` (lines ~1028-1055) concatenates all symbols' frames and
`drop_duplicates(subset=["timestamp"], keep="first")`. The surviving row's
OHLC belongs to whichever symbol's frame was inserted into `ctx._bars` first.
Clock-symbol fills use the clock row directly (line ~300), so in a
multi-symbol backtest a fill for the clock symbol can execute at **another
symbol's open/high/low/close** whenever the other symbol's frame happened to
be first and shares the timestamp.

**Fix direction:** the union clock should carry timestamps only; every fill
must resolve OHLC from the specific symbol's own frame (the off-clock path
already does this — make it the only path).

### F7 — CRITICAL: option expiry settles at post-expiry underlying price

`_settle_expired_options` (lines ~839-875) resolves the underlying price at
sim_time via `OptionsAssetService._get_underlying_price`, whose actual lookup
seam is `_bar_lookup` in `coordinator/services/asset_services/base.py:49-69` —
the **same** inclusive `searchsorted(..., "right") - 1` look-ahead as F1.
ITM/OTM determination and intrinsic settlement value therefore use the close
of the bar *after* expiry. The F1 fix must cover `base.py::_bar_lookup` too.

(Verified correct, no action: buy-to-close short realized-PnL formula at line
~797; expiry side semantics sell-for-long/buy-for-short in
`options.py::handle_expiry`; `mtm_realism` validation; `options_mtm.py` in its
entirety — envelope, IV tiers, T≤0/σ≤0 branches all match
`docs/concepts/backtest-accuracy.md`.)

---

## 2. Options math (`coordinator/services/options_math.py`, `asset_services/options.py`)

### F4 — CRITICAL: `bs_price` returns malformed (even negative) prices for σ≤0

`options_math.bs_price` has no `sigma <= 0` branch; `_d1d2` returns `(0, 0)`,
so a call prices at `0.5·S − 0.5·K·e^(−rT)`:
- S=110, K=100, T=1, r=0.045 → 7.20 (correct deterministic value: 14.40)
- S=90, K=100 → **−2.78** (negative price)

Contrast: `options_mtm.black_scholes_price` handles σ≤0 correctly (discounted
intrinsic). **Fix direction:** port the σ≤0 and T≤0 branches from
`options_mtm.black_scholes_price` (or delegate to it).

### F5 — HIGH: `bs_iv` rejects valid deep-ITM European put quotes

`bs_iv` (lines ~60-62) floors the acceptable price at **undiscounted**
intrinsic `max(K−S, 0)`. A European deep-ITM put's fair value lies between
discounted and undiscounted intrinsic, so genuine market quotes return `None`
→ IV cache never populates for those contracts → fallback σ=0.40 used when
better data existed. **Fix direction:** floor at *discounted* intrinsic
(`K·e^(−rT)−S` for puts).

### F6 — CRITICAL: `handle_expiry` masks missing underlying price with strike

`options.py` lines ~176-177: `if underlying_price is None:
underlying_price = parsed["strike"]` → intrinsic ≡ 0 → every option "expires
worthless". For short positions this **erases the liability** (free profit);
for long ITM positions it erases the payoff. **Fix direction:** missing
underlying at expiry must be an error (or at minimum settle conservatively
per direction: shorts at a worst-case bound, longs at 0 — and log loudly).

### F20b — MINOR: `compute_unrealized_pnl` returns 0.0 for short options

`options.py` lines ~131-137: the `market_value > 0` guard fails for shorts
(negative market value) → unrealized PnL silently reported as 0 for all short
option positions.

---

## 3. Metrics (`coordinator/services/metrics_engine.py`)

### F8 — HIGH: nonstandard Sortino, and a *different* nonstandard Sortino in bootstrap

- `metrics_engine.py` (lines ~57-61): downside variance =
  `sum(min(r,0)²) / len(downside)` — divides by the **count of negative
  returns**, not the total count, deviating from the standard
  `sqrt(mean over ALL returns of min(r,0)²)`. Additional facet: a
  `len(downside) > 1` guard returns Sortino 0.0 whenever there is at most one
  negative return.
- `validation/bootstrap.py::_annualized_sortino`: uses
  `np.std(downside, ddof=1)` — the *centered sample std of the negative
  returns*, a third definition.

The same metric computed two different (both wrong) ways means CPCV/bootstrap
CIs are not comparable with backtest-report Sortino. **Fix direction:**
both compute `downside_dev = sqrt(mean(min(r,0)**2))` over **all** returns;
Sortino = (mean·252 − rf) / (downside_dev·√252).

### F9 — HIGH: `max_dd_duration` sentinel bugs

`metrics_engine.py`: `current_dd_start == 0` is used as the "not in drawdown"
sentinel, so a drawdown that starts at index 0 is never measured; and a
drawdown still open at the end of the series is never counted at all.
**Fix direction:** use `None` as the sentinel and flush the open drawdown
after the loop.

---

## 4. Validation lab (`coordinator/services/validation/`)

### F10 — HIGH: `_block_resample` crashes on short series

`bootstrap.py` (lines ~41-46): `rng.integers(0, n - block_size + 1)` raises
when `block_size > n` (default block ≥ 20, so any equity curve shorter than
~21 points crashes the whole validation run). **Fix direction:**
`block_size = min(block_size, n)`; degenerate n=1 → plain resample.

### F11 — HIGH: CPCV silently drops segments missing `equity_curve`

`cpcv.py` mode A (lines ~339-342) `if row.equity_curve:` and per-path Sharpe
(lines ~487-497) both skip segments with no equity curve **silently**. A path
metric built from 7 of 9 segments is presented as if complete — survivorship
bias inside the anti-overfitting tool itself. **Fix direction:** a missing
equity curve must either fail the run or mark the path/CI as degraded in the
result payload; never silently shrink the sample.

### F12 — HIGH: CPCV embargo/purge applied on the wrong side

López de Prado: *purge* removes train samples whose labels overlap the test
window (both sides); *embargo* removes train samples immediately **after**
the test window. In `cpcv.py`:
- Mode B purge (line ~399) only trims the train **end**
  (`train_end_raw − purge_horizon`) — a train window that *follows* a test
  group is never trimmed at its start.
- Embargo (line ~428 mode B; lines ~300-310 mode A) is applied by shifting the
  **test** start forward — embargo must instead exclude train data following
  the test window.

Net effect: leakage paths that CPCV exists to prevent remain open whenever a
train group sits after a test group (which happens in most C(N,k) splits).
**Fix direction:** for each train run adjacent to a test group: trim train
start by `embargo + purge` when train follows test; trim train end by `purge`
when train precedes test. Test windows stay full-size.

Note for the fixer: two existing tests in
`tests/coordinator/services/validation/test_cpcv.py` encode the buggy
embargo-shifts-test-start behavior and must be updated as part of the F12 fix.

### F20a — MINOR: `multi_test.py` nits

- PSR mixes ddof=1 (Sharpe) with ddof=0 (skew/kurt moments) — small bias.
- BH `corrected_p` lacks the cumulative-min monotonicity enforcement (the
  `significant` flags are computed correctly; only the reported adjusted
  p-values can be non-monotonic).

Verified correct: SPA/White's Reality Check structure, PSR/DSR formulas,
expected-max-Sharpe expression.

---

## 5. Data layer

### F13 — HIGH: bitemporal store destroys history on revision

`datasets/storage.py::upsert` (lines ~72-80): after concat,
`drop_duplicates(subset=id_cols, keep="last")`. When a source *restates* a
row (same id columns, later knowledge_date) the original observation is
deleted. `load_dataset(as_of=t)` for t before the restatement then returns
the **revised** value — look-ahead leakage in the component whose whole job
is point-in-time honesty. **Fix direction:** `knowledge_date` must be part of
the dedupe key (append-only bitemporal semantics); revisions add rows, never
replace them.

### F14 — MODERATE: Theta intraday timestamps off by 4-5 hours

`data_providers/theta.py::_fetch_intraday`: ThetaData's `ms_of_day` is
**US/Eastern** ms-since-midnight, but the code combines it with **UTC**
midnight → every intraday bar lands 4-5 hours early. Also `hour=ms//3600000`
raises `ValueError` for `ms_of_day = 86400000` (24:00 rows). **Fix
direction:** build the timestamp in `America/New_York` then convert to UTC;
clamp/handle the 24:00 row.

### F15 — MODERATE: Tradier daily bars stamped at midnight UTC

`data_providers/tradier.py` (lines ~116-118): daily bars stamped
`datetime.combine(date, min.time())` in UTC. Other providers stamp at market
open; mixing providers in one store yields inconsistent bar-known-at times
under the open-time-stamp convention. **Fix direction:** normalize daily-bar
stamping (market-open ET → UTC) across providers, or document and convert at
read time.

(Refuted: `coverage_index.py` holiday-gap claim — the 3-business-day gap
threshold tolerates holiday clusters by design.)

---

## 6. Worker / live / SDK

### F16 — MODERATE: `CachingBrokerAdapter.invalidate()` has zero call sites

`worker/caching_broker_adapter.py:41` defines `invalidate()`; the module
docstring (line 9) requires calling it after an order succeeds. `grep` shows
no caller anywhere → after a fill, position/balance reads serve stale cache
for the TTL. **Fix direction:** call `invalidate()` on successful order
submission in the runtime's order path.

### F17 — MODERATE: signal-approval futures keyed by `instance_id` only

`worker/agent.py` (~111-121): `self._pending_signal_responses[instance_id] =
fut`. Two concurrent approvals for the same instance (possible — tick
handling is fire-and-forget per entry, see F18) overwrite the first future
(it never resolves → 30 s timeout → spurious rejection), and the `finally`
pop can delete the *newer* future. **Fix direction:** key by a unique
request id echoed back in `signal_response`.

### F18 — MODERATE: fire-and-forget tick tasks

`worker/agent.py::_handle_tick_batch` (~224):
`asyncio.create_task(runtime.on_tick_batch_entry(entry))` —
1. no strong reference kept (tasks can be garbage-collected mid-flight per
   asyncio docs), 2. exceptions vanish unlogged, 3. two batches for the same
instance can interleave out of order. **Fix direction:** hold references in a
set + done-callback for logging; serialize per-instance via an
`asyncio.Queue`/lock per runtime.

### F19 — MODERATE: `sdk/signals.py` accepts nonsense quantities

`SignalLeg.__post_init__` validates only `asset_type`. `quantity` of `0`,
negative, `NaN`, or `inf` passes straight through to the coordinator — this
is a system boundary (user algorithm code → framework). **Fix direction:**
validate `quantity` is finite and > 0; validate `limit_price`/`stop_price`
finite and > 0 when present and required by `order_type`.

---

## 7. Refuted claims (verified false, no action)

| Claim | Verdict |
|---|---|
| Short-cover realized PnL inverted (`_apply_fill` ~797) | False — `(avg − fill)·qty·mult − fees` is correct for shorts |
| Expiry settlement cash sign reversed | False — side semantics are internally consistent with `options.py` |
| Coverage index splits ranges at holidays | False — 3-bday threshold tolerates holiday clusters |
