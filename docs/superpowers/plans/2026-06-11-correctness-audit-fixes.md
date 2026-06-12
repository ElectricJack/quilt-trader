# Correctness Audit Fixes (F1–F20b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 33 red-phase audit tests pass by fixing the 20 confirmed correctness bugs documented in `docs/superpowers/research/2026-06-11-correctness-audit-findings.md`, without breaking the existing suite.

**Architecture:** Each task is one finding (or a tightly coupled group in one file): run the already-written failing test, apply the prescribed fix to production code, verify green, run the surrounding suite, commit. The failing tests are the spec — they were written first (TDD red phase) and verified to fail for the documented reason. **Do not modify the audit test files** (`tests/**/test_audit_*_findings.py`) except where a task explicitly says to update a *pre-existing* test that encodes buggy behavior.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (strict), pandas/numpy, SQLAlchemy (sqlite in tests), FastAPI coordinator, asyncio worker.

**Conventions you must know:**
- All test commands run from the repo root `/home/jkern/dev/quilt-trader` using `.venv/bin/pytest`.
- Bars are stamped at **OPEN time**; a bar stamped `T` with timeframe duration `d` is only fully known at `T + d`. The engine sets `sim_time = bar.timestamp + tf_duration`. The canonical no-look-ahead lookup (see `coordinator/services/backtest_tick_context.py:215-256`) subtracts the bar duration from the cutoff before `searchsorted(..., side="right")`.
- Pre-existing, unrelated collection error: `tests/coordinator/services/datasets/test_forward_bias.py` fails to collect (missing `hypothesis` package). Ignore it / deselect it; it is not caused by this work.
- Commit after every task. Do not use `--no-verify`.

---

### Task 1: F4 + F5 — Black-Scholes sigma=0 pricing and IV intrinsic floor

**Files:**
- Modify: `coordinator/services/options_math.py:28-62`
- Test: `tests/coordinator/services/test_audit_pricing_metrics_findings.py` (tests `test_f4_*`, `test_f5_*`)

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest "tests/coordinator/services/test_audit_pricing_metrics_findings.py::test_f4_bs_price_zero_sigma_itm_call_is_discounted_intrinsic" "tests/coordinator/services/test_audit_pricing_metrics_findings.py::test_f4_bs_price_zero_sigma_otm_call_is_zero_and_never_negative" "tests/coordinator/services/test_audit_pricing_metrics_findings.py::test_f5_bs_iv_accepts_deep_itm_european_put_below_undiscounted_intrinsic" -q
```
Expected: 3 FAILED. F4 ITM expects ~14.4003 but gets ~7.20; F4 OTM gets a negative price; F5 gets `None`.

- [ ] **Step 2: Fix `bs_price` (F4)**

In `coordinator/services/options_math.py`, replace the current `bs_price` body (lines 28–44):

```python
def bs_price(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call",
) -> float:
    """European option price via Black-Scholes."""
    if T <= 1e-10:
        if option_type == "call":
            return max(0.0, S - K)
        return max(0.0, K - S)

    if sigma <= 0 or S <= 0 or K <= 0:
        # Deterministic (zero-vol) limit: discounted intrinsic, floored at 0.
        discount = math.exp(-r * T)
        if option_type == "call":
            return max(0.0, S - K * discount)
        return max(0.0, K * discount - S)

    d1, d2 = _d1d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)

    if option_type == "call":
        return S * _norm_cdf(d1) - K * discount * _norm_cdf(d2)
    else:
        return K * discount * _norm_cdf(-d2) - S * _norm_cdf(-d1)
```

- [ ] **Step 3: Fix `bs_iv` intrinsic floor (F5)**

In the same file, replace lines 60–62 (`intrinsic = max(0.0, S - K) ...` / `if price < intrinsic - tol: return None`) with the **discounted** intrinsic:

```python
    discount = math.exp(-r * T)
    if option_type == "call":
        intrinsic = max(0.0, S - K * discount)
    else:
        intrinsic = max(0.0, K * discount - S)
    if price < intrinsic - tol:
        return None
```

- [ ] **Step 4: Verify green**

Run the same command as Step 1. Expected: 3 passed.

- [ ] **Step 5: Run the surrounding suite**

Run: `.venv/bin/pytest tests/ -q -k "options_math or options_mtm"`
Expected: all pass (the sigma>0 and T<=0 paths are unchanged).

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/options_math.py
git commit -m "fix(options): sigma<=0 BS price = discounted intrinsic; IV floor uses discounted intrinsic (F4, F5)"
```

---

### Task 2: F6 + F20b — option expiry must not mask a missing underlying; short-option unrealized PnL

**Files:**
- Modify: `coordinator/services/asset_services/options.py:131-137` and `:175-177`
- Test: `tests/coordinator/services/test_audit_pricing_metrics_findings.py` (tests `test_f6_*`, `test_f20b_*`)

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest "tests/coordinator/services/test_audit_pricing_metrics_findings.py::test_f6_handle_expiry_missing_underlying_price_raises" "tests/coordinator/services/test_audit_pricing_metrics_findings.py::test_f20b_compute_unrealized_pnl_short_option_position" -q
```
Expected: 2 FAILED (F6: DID NOT RAISE; F20b: got 0.0, expected 200.0).

- [ ] **Step 2: Fix `handle_expiry` (F6)**

In `coordinator/services/asset_services/options.py`, replace lines 175–177:

```python
        underlying_price = self._get_underlying_price(parsed["underlying"], sim_time, ctx)
        if underlying_price is None:
            underlying_price = parsed["strike"]
```

with:

```python
        underlying_price = self._get_underlying_price(parsed["underlying"], sim_time, ctx)
        if underlying_price is None:
            raise ValueError(
                f"Cannot settle expired option {symbol}: no underlying price "
                f"available for {parsed['underlying']} at {sim_time}"
            )
```

- [ ] **Step 3: Fix `compute_unrealized_pnl` (F20b)**

In the same file, replace lines 131–137:

```python
    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float:
        if quantity == 0 or market_value == 0:
            return 0.0
        # Signed cost basis: negative for shorts, so shorts profit when
        # market_value (also negative) rises toward zero.
        cost = avg_price * quantity * self.get_multiplier()
        return market_value - cost
```

(`market_value == 0` keeps the previous "no price data → report 0" behavior.)

- [ ] **Step 4: Verify green**

Run the Step 1 command. Expected: 2 passed.

- [ ] **Step 5: Run the surrounding suite**

Run: `.venv/bin/pytest tests/ -q -k "asset_service or options"`
Expected: all pass. If a pre-existing test asserted strike-substitution on missing underlying or `0.0` unrealized for shorts, it encodes the bug — update its assertion to the new behavior and note it in the commit message.

- [ ] **Step 6: Commit**

```bash
git add -A coordinator/services/asset_services/options.py tests/
git commit -m "fix(options): raise on missing underlying at expiry; signed-cost unrealized PnL for shorts (F6, F20b)"
```

---

### Task 3: F1 + F1b + F3 — engine look-ahead in price/fill lookups; clock-symbol fills

This is the core simulation-correctness task. All three share one root cause: lookups use `cutoff = sim_time` *inclusively*, but a bar stamped at `sim_time` has not elapsed yet. The fix is the canonical pattern: subtract the bar duration from the cutoff. F3 additionally removes the `sym != clock_symbol` guard so the clock symbol also resolves its **own** bars (union-clock rows can carry another symbol's OHLC).

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py:300-326` (fill-bar resolution), `:573-600` (`_lookup_option_price`), `:877-920` (`_lookup_symbol_close`)
- Test: `tests/coordinator/services/test_audit_engine_findings.py` (tests `test_f1_*`, `test_f1b_*`, `test_f3_*`)

Note: `timeframe_to_seconds` is already imported at `backtest_engine_v2.py:28`.

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest "tests/coordinator/services/test_audit_engine_findings.py::test_f1_lookup_symbol_close_excludes_bar_stamped_at_sim_time" "tests/coordinator/services/test_audit_engine_findings.py::test_f1b_off_clock_market_order_fills_at_next_bar_not_two_bars_ahead" "tests/coordinator/services/test_audit_engine_findings.py::test_f3_clock_symbol_fill_uses_clock_symbols_own_ohlc" -q
```
Expected: 3 FAILED (F1 gets 200.0 instead of 100.0; F1b fills ~2700 instead of ~2600; F3 fills ~2500 instead of ~42000).

- [ ] **Step 2: Fix `_lookup_symbol_close` (F1)**

Replace lines 913–916 (inside the loop, after `ns, closes = self._ts_cache[cache_key]`):

```python
            cutoff = pd.Timestamp(sim_time)
            if cutoff.tz is not None:
                cutoff = cutoff.tz_convert("UTC").tz_localize(None)
            idx = np.searchsorted(ns, cutoff.value, side="right") - 1
```

with:

```python
            cutoff = pd.Timestamp(sim_time)
            if cutoff.tz is not None:
                cutoff = cutoff.tz_convert("UTC").tz_localize(None)
            # A bar stamped at T (open time) is only known at T + duration:
            # exclude bars whose interval has not elapsed at sim_time.
            duration_ns = int(timeframe_to_seconds(tf)) * 1_000_000_000
            idx = np.searchsorted(ns, cutoff.value - duration_ns, side="right") - 1
```

(`tf` is already in scope from the loop key `for (src, s, tf), df in ctx._bars.items():`.)

- [ ] **Step 3: Fix fill-bar resolution (F1b + F3)**

Replace lines 300–326 (from `fill_bar = bar` through the `break` of the resolution loop):

```python
                fill_bar = bar
                sym = po.leg.symbol
                if sym != clock_symbol:
                    svc_for_sym = self._asset_registry.get_service(sym)
                    for (src, s, tf), df in ctx._bars.items():
                        ...
                        idx = np.searchsorted(ns, cutoff.value, side="right") - 1
                        if idx >= 0:
                            fill_bar = df.iloc[idx]
                        break
```

with (note: the `if sym != clock_symbol:` guard is REMOVED — the whole loop now runs for every symbol, one indent level out — and the cutoff subtracts the bar duration):

```python
                fill_bar = bar
                sym = po.leg.symbol
                # Every symbol resolves its OWN frame — including the clock
                # symbol: in union-clock mode the clock row at a timestamp may
                # carry ANOTHER symbol's OHLC (drop_duplicates keep="first" in
                # _build_union_clock), so the clock row is only a fallback.
                svc_for_sym = self._asset_registry.get_service(sym)
                for (src, s, tf), df in ctx._bars.items():
                    if df.empty:
                        continue
                    resolved = svc_for_sym.resolve_symbol(sym, src)
                    if s != sym and s != resolved:
                        continue
                    cache_key = id(df)
                    if cache_key not in self._ts_cache:
                        ts_col = pd.to_datetime(df["timestamp"])
                        if ts_col.dt.tz is not None:
                            ts_col = ts_col.dt.tz_convert("UTC").dt.tz_localize(None)
                        # pandas 3.0 datetime64[us] default — force ns
                        ns = ts_col.values.astype("datetime64[ns]").view("int64")
                        closes = df["close"].values.astype(float)
                        self._ts_cache[cache_key] = (ns, closes)
                    ns, _ = self._ts_cache[cache_key]
                    cutoff = pd.Timestamp(sim_time)
                    if cutoff.tz is not None:
                        cutoff = cutoff.tz_convert("UTC").tz_localize(None)
                    # Last bar whose interval has elapsed at sim_time. Since
                    # sim_time = current_bar.timestamp + duration, for the
                    # symbol's own frame this selects the bar stamped at the
                    # CURRENT tick — i.e. the documented "next bar after the
                    # signal" — never a later one.
                    duration_ns = int(timeframe_to_seconds(tf)) * 1_000_000_000
                    idx = np.searchsorted(ns, cutoff.value - duration_ns, side="right") - 1
                    if idx >= 0:
                        fill_bar = df.iloc[idx]
                    break
```

Keep the comment block above `fill_bar = bar` (lines 293–299) but update its first sentence to: `# Resolve the fill bar: every symbol prefers its OWN data over the clock bar.`

- [ ] **Step 4: Fix `_lookup_option_price` cutoff (F1 family)**

Replace lines 588–591:

```python
                cutoff = pd.Timestamp(ctx._sim_time_now)
                if cutoff.tz is not None:
                    cutoff = cutoff.tz_convert("UTC").tz_localize(None)
                visible = df[ts <= cutoff]
```

with:

```python
                cutoff = pd.Timestamp(ctx._sim_time_now)
                if cutoff.tz is not None:
                    cutoff = cutoff.tz_convert("UTC").tz_localize(None)
                # 1day contract bars are stamped at open; exclude the bar
                # whose interval has not elapsed at sim_time.
                cutoff = cutoff - pd.Timedelta(seconds=timeframe_to_seconds("1day"))
                visible = df[ts <= cutoff]
```

- [ ] **Step 5: Verify green**

Run the Step 1 command. Expected: 3 passed.

- [ ] **Step 6: Run the full engine suite**

Run: `.venv/bin/pytest tests/coordinator/services/ -q -k "backtest_engine or two_pass or backtest_runner"`
Expected: all pass. If any pre-existing test fails, inspect it: a test that asserts an equity/MTM/fill value derived from the *inclusive* cutoff (i.e. it baked in the one-bar look-ahead) must have its expected value shifted back one bar per the open-stamp convention. Flat-bar tests are unaffected. Do NOT weaken assertions — recompute the correct expected value by hand from the test's bar data.

- [ ] **Step 7: Commit**

```bash
git add -A coordinator/services/backtest_engine_v2.py tests/
git commit -m "fix(engine): eliminate one-bar look-ahead in price/fill lookups; clock symbol fills from own bars (F1, F1b, F3)"
```

---

### Task 4: F7 — option expiry settles at post-expiry underlying close

**Files:**
- Modify: `coordinator/services/asset_services/base.py:49-69` (`_bar_lookup`), `coordinator/services/asset_services/options.py:208-216` (`_get_underlying_price`)
- Test: `tests/coordinator/services/test_audit_engine_findings.py::test_f7_option_expiry_settles_at_expiry_day_underlying_close`

- [ ] **Step 1: Run the failing test**

Run:
```bash
.venv/bin/pytest "tests/coordinator/services/test_audit_engine_findings.py::test_f7_option_expiry_settles_at_expiry_day_underlying_close" -q
```
Expected: FAILED — settles ITM at 25 (post-expiry close 130) instead of worthless at the expiry-day close 100.

- [ ] **Step 2: Add a duration parameter to `_bar_lookup`**

In `coordinator/services/asset_services/base.py`, replace the whole `_bar_lookup` (lines 49–69):

```python
def _bar_lookup(
    df: pd.DataFrame, sim_time: Any, timeframe_seconds: float = 0.0,
) -> Optional[float]:
    """Return the close of the last bar fully elapsed at ``sim_time``.

    Bars are stamped at OPEN time: a bar stamped T with duration d is only
    known at T + d. Pass the bar duration as ``timeframe_seconds`` to exclude
    the not-yet-elapsed bar (0.0 preserves the legacy inclusive behavior for
    callers that have no timeframe available).

    Handles tz-naive and tz-aware timestamps on either side by normalizing
    both to UTC-naive before comparing. Returns None if df is empty or no
    bar exists at/before the cutoff.
    """
    if df is None or len(df) == 0:
        return None
    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    cutoff = pd.Timestamp(sim_time)
    if cutoff.tz is not None:
        cutoff = cutoff.tz_convert("UTC").tz_localize(None)
    # pandas 3.0 datetime64[us] default — force ns before viewing as int64
    ns = ts.values.astype("datetime64[ns]").view("int64")
    cutoff_ns = cutoff.value - int(timeframe_seconds) * 1_000_000_000
    idx = int(np.searchsorted(ns, cutoff_ns, side="right")) - 1
    if idx < 0:
        return None
    return float(df.iloc[idx]["close"])
```

(This also fixes the latent pandas-3.0 hazard `ts.values.view("int64")` → `astype("datetime64[ns]").view("int64")`.)

- [ ] **Step 3: Pass the timeframe from `_get_underlying_price`**

In `coordinator/services/asset_services/options.py`, replace `_get_underlying_price` (lines 208–216):

```python
    def _get_underlying_price(
        self, underlying: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        if ctx is None or not hasattr(ctx, "_bars"):
            return None
        # Local import: asset_services must not import backtest modules at
        # module load (circular-import risk).
        from coordinator.services.backtest_tick_context import timeframe_to_seconds
        for (_src, sym, tf), df in ctx._bars.items():
            if sym == underlying:
                return _bar_lookup(
                    df, sim_time, timeframe_seconds=timeframe_to_seconds(tf),
                )
        return None
```

- [ ] **Step 4: Verify green**

Run the Step 1 command. Expected: PASS. Also re-run Task 2's F6 test (it shares `handle_expiry`): expected PASS.

- [ ] **Step 5: Run the surrounding suite**

Run: `.venv/bin/pytest tests/coordinator/services/ -q -k "asset_service or expiry or options"`
Expected: all pass. Other `_bar_lookup` callers (equities/crypto `get_price`) pass no `timeframe_seconds` and keep legacy behavior — that residual seam is recorded on the backlog in Task 17.

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/asset_services/base.py coordinator/services/asset_services/options.py
git commit -m "fix(options): expiry settlement uses last ELAPSED underlying bar, not post-expiry close (F7)"
```

---

### Task 5: F2 — position flips keep stale avg_price in `_apply_fill`

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py:793-821`
- Test: `tests/coordinator/services/test_audit_engine_findings.py` (tests `test_f2_*`)

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest "tests/coordinator/services/test_audit_engine_findings.py::test_f2_short_to_long_flip_resets_avg_price_to_fill_price" "tests/coordinator/services/test_audit_engine_findings.py::test_f2_long_to_short_flip_resets_avg_price_to_fill_price" -q
```
Expected: 2 FAILED — flipped position keeps the old basis 100.0 instead of the fill price.

- [ ] **Step 2: Fix the buy-to-close branch**

In `_apply_fill`, replace lines 794–802:

```python
            if ps.quantity < 0:
                # Buy-to-close: covering a short position
                close_qty = min(fill.quantity, abs(ps.quantity))
                realized = (ps.avg_price - fill.fill_price) * close_qty * multiplier - fill.fees
                fill.realized_pnl = realized
                remainder = fill.quantity - close_qty
                ps.quantity += fill.quantity
                if remainder > 0:
                    # Crossed through zero: the residual is a NEW long whose
                    # basis is this fill's price, not the old short basis.
                    ps.avg_price = fill.fill_price
                elif ps.quantity == 0:
                    ps.avg_price = 0.0
                cash -= notional + fill.fees
```

- [ ] **Step 3: Fix the sell-to-close branch**

Replace lines 813–821:

```python
            if ps.quantity > 0:
                # Sell-to-close: closing a long position
                close_qty = min(fill.quantity, ps.quantity)
                realized = (fill.fill_price - ps.avg_price) * close_qty * multiplier - fill.fees
                fill.realized_pnl = realized
                remainder = fill.quantity - close_qty
                ps.quantity -= fill.quantity
                if remainder > 0:
                    # Crossed through zero: the residual is a NEW short whose
                    # basis is this fill's price, not the old long basis.
                    ps.avg_price = fill.fill_price
                elif ps.quantity == 0:
                    ps.avg_price = 0.0
                cash += notional - fill.fees
```

- [ ] **Step 4: Verify green**

Run the Step 1 command. Expected: 2 passed.

- [ ] **Step 5: Run the engine suite**

Run: `.venv/bin/pytest tests/coordinator/services/ -q -k "backtest_engine or apply_fill or position"`
Expected: all pass (non-flip paths unchanged).

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/backtest_engine_v2.py
git commit -m "fix(engine): reset cost basis to fill price when a fill flips position direction (F2)"
```

---

### Task 6: F8 — standardize Sortino in MetricsEngine and bootstrap

Standard definition used by both fixes: `downside_dev = sqrt(mean over ALL returns of min(r,0)^2)`; annualized Sortino = `(mean*252 - rf) / (downside_dev * sqrt(252))` (equivalently `mean/downside_dev*sqrt(252)` when rf=0).

**Files:**
- Modify: `coordinator/services/metrics_engine.py:56-63`, `coordinator/services/validation/bootstrap.py:49-59`
- Test: `tests/coordinator/services/test_audit_pricing_metrics_findings.py` (tests `test_f8_*`)

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/test_audit_pricing_metrics_findings.py -q -k "f8"
```
Expected: 3 FAILED (5.29 vs 6.48; 0.0 vs 6.48; NaN vs finite).

- [ ] **Step 2: Fix MetricsEngine Sortino**

In `coordinator/services/metrics_engine.py`, replace lines 56–63:

```python
        # Sortino — standard definition: downside deviation is the root mean
        # of min(r, 0)^2 over ALL returns (not just the negative ones).
        if len(returns) > 1:
            downside_var = sum(min(r, 0.0) ** 2 for r in returns) / len(returns)
            downside_dev = math.sqrt(downside_var) * math.sqrt(252)
            sortino = (mean_return * 252 - risk_free_rate) / downside_dev if downside_dev > 0 else 0
        else:
            sortino = 0
```

- [ ] **Step 3: Fix bootstrap `_annualized_sortino`**

In `coordinator/services/validation/bootstrap.py`, replace lines 49–59:

```python
def _annualized_sortino(returns: np.ndarray, periods_per_year: int = 252) -> float:
    if returns.size == 0:
        return 0.0
    mu = float(np.mean(returns))
    # Standard downside deviation: root mean of min(r, 0)^2 over ALL returns.
    sigma_d = float(np.sqrt(np.mean(np.minimum(returns, 0.0) ** 2)))
    if sigma_d == 0:
        return 0.0
    return mu / sigma_d * np.sqrt(periods_per_year)
```

- [ ] **Step 4: Verify green**

Run the Step 1 command. Expected: 3 passed.

- [ ] **Step 5: Run the surrounding suite**

Run: `.venv/bin/pytest tests/ -q -k "metrics or bootstrap"`
Expected: all pass. Any pre-existing test asserting a Sortino value computed under the old (negatives-only) formula must be updated to the standard value (recompute by hand: `mean*252 / (sqrt(mean(min(r,0)^2))*sqrt(252))`).

- [ ] **Step 6: Commit**

```bash
git add -A coordinator/services/metrics_engine.py coordinator/services/validation/bootstrap.py tests/
git commit -m "fix(metrics): standard Sortino downside deviation in MetricsEngine and bootstrap (F8)"
```

---

### Task 7: F9 — max-drawdown duration sentinel and trailing flush

**Files:**
- Modify: `coordinator/services/metrics_engine.py:65-88`
- Test: `tests/coordinator/services/test_audit_pricing_metrics_findings.py` (tests `test_f9_*`)

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/test_audit_pricing_metrics_findings.py -q -k "f9"
```
Expected: 2 FAILED (reports 2 instead of 3; reports 0 instead of 3).

- [ ] **Step 2: Fix the drawdown-duration loop**

Replace lines 65–88 (the whole max-drawdown block, up to and including the line before `calmar = ...`):

```python
        # Max drawdown. Duration is measured peak-to-recovery in bars:
        # current_dd_start holds the INDEX OF THE PEAK preceding the open
        # drawdown (None = not in drawdown). Using None as the sentinel keeps
        # index 0 valid as a peak; an unrecovered drawdown is flushed at the
        # end of the series.
        peak = equities[0]
        max_dd_pct = 0
        max_dd_dollars = 0
        max_dd_duration = 0
        current_dd_start = None

        for i, eq in enumerate(equities):
            if eq > peak:
                if current_dd_start is not None:
                    max_dd_duration = max(max_dd_duration, i - current_dd_start)
                    current_dd_start = None
                peak = eq
            else:
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                dd_abs = peak - eq
                if dd > max_dd_pct:
                    max_dd_pct = dd
                    max_dd_dollars = dd_abs
                if eq < peak and current_dd_start is None:
                    current_dd_start = i - 1  # the prior point was the peak

        if current_dd_start is not None:
            max_dd_duration = max(
                max_dd_duration, len(equities) - 1 - current_dd_start
            )
```

- [ ] **Step 3: Verify green**

Run the Step 1 command. Expected: 2 passed.

- [ ] **Step 4: Run the surrounding suite**

Run: `.venv/bin/pytest tests/ -q -k "metrics"`
Expected: all pass. Pre-existing tests asserting durations measured from the first below-peak point (old semantics, one bar shorter) must be updated to peak-to-recovery semantics.

- [ ] **Step 5: Commit**

```bash
git add -A coordinator/services/metrics_engine.py tests/
git commit -m "fix(metrics): drawdown duration handles index-0 peak and unrecovered trailing drawdown (F9)"
```

---

### Task 8: F10 — bootstrap crashes when block_size exceeds series length

**Files:**
- Modify: `coordinator/services/validation/bootstrap.py:41-46`
- Test: `tests/coordinator/services/test_audit_pricing_metrics_findings.py::test_f10_block_bootstrap_sharpe_handles_series_shorter_than_block`

- [ ] **Step 1: Run the failing test**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/test_audit_pricing_metrics_findings.py -q -k "f10"
```
Expected: FAILED with `ValueError` from `rng.integers(0, n - block_size + 1)` (negative high bound).

- [ ] **Step 2: Fix `_block_resample`**

Replace lines 41–46:

```python
def _block_resample(returns: np.ndarray, block_size: int, rng: np.random.Generator) -> np.ndarray:
    n = returns.size
    if n == 0:
        return returns
    block_size = min(block_size, n)  # short series: cap at series length
    n_blocks = int(np.ceil(n / block_size))
    starts = rng.integers(0, n - block_size + 1, size=n_blocks)
    out = np.concatenate([returns[s : s + block_size] for s in starts])
    return out[:n]
```

- [ ] **Step 3: Verify green**

Run the Step 1 command. Expected: PASS.

- [ ] **Step 4: Run the surrounding suite**

Run: `.venv/bin/pytest tests/ -q -k "bootstrap"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/bootstrap.py
git commit -m "fix(bootstrap): cap block_size at series length instead of crashing (F10)"
```

---

### Task 9: F11 — CPCV must raise (not silently drop) segments missing equity_curve

**Files:**
- Modify: `coordinator/services/validation/cpcv.py:336-342` (mode A aggregation) and `:485-490` (mode B per-path loop)
- Test: `tests/coordinator/services/validation/test_audit_cpcv_findings.py` (tests `test_f11_*`)

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/validation/test_audit_cpcv_findings.py -q -k "f11"
```
Expected: 2 FAILED (DID NOT RAISE ValueError).

- [ ] **Step 2: Fix mode A aggregation**

In `_run_mode_fixed`, replace lines 336–342:

```python
    for rid in result.segment_run_ids:
        row = db.query(BacktestRun).filter_by(id=rid).one()
        sharpes.append(float(row.sharpe_ratio or 0.0))
        if not row.equity_curve:
            raise ValueError(
                f"CPCV segment run {rid} completed without an equity_curve; "
                "refusing to aggregate with silently dropped segments"
            )
        equity = pd.Series([float(p.get("equity", 1.0)) for p in row.equity_curve])
        rets = equity.pct_change().dropna().tolist()
        concat_returns.extend(rets)
```

- [ ] **Step 3: Fix mode B per-path Sharpe loop**

In `_run_mode_select`, replace lines 485–490 (inside `for seg in path:`):

```python
        for seg in path:
            row = db.query(BacktestRun).filter_by(id=seg.run_id).one()
            if not row.equity_curve:
                raise ValueError(
                    f"CPCV path segment run {seg.run_id} has no equity_curve; "
                    "refusing to compute a silently degraded path Sharpe"
                )
            equity = pd.Series([float(p.get("equity", 1.0)) for p in row.equity_curve])
            rets = equity.pct_change().dropna().tolist()
            path_returns.extend(rets)
```

(Leave the best-path loop at ~503–507 unchanged — after this fix every path segment is guaranteed to have an equity curve before it runs.)

- [ ] **Step 4: Verify green**

Run the Step 1 command. Expected: 2 passed.

- [ ] **Step 5: Run the CPCV suite**

Run: `.venv/bin/pytest tests/coordinator/services/validation/ -q`
Expected: all pass (the pre-existing `_complete_run` helper in `test_cpcv.py` always sets `equity_curve`).

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/validation/cpcv.py
git commit -m "fix(cpcv): raise on segments missing equity_curve instead of silently dropping them (F11)"
```

---

### Task 10: F12 — purge/embargo applied on the correct side (Lopez de Prado)

Per Lopez de Prado: **test windows stay full-size**. Purge trims TRAIN samples adjacent to a test boundary; embargo additionally excludes TRAIN samples immediately **after** the test window. The current code instead shifts test starts and only ever trims the train end. This task also rewrites the two pre-existing tests that assert the buggy behavior.

**Files:**
- Modify: `coordinator/services/validation/cpcv.py:299-311` (mode A), `:395-401` (mode B train window), `:424-437` (mode B OOS windows)
- Modify: `tests/coordinator/services/validation/test_cpcv.py:250-334` (two tests encoding the bug)
- Test: `tests/coordinator/services/validation/test_audit_cpcv_findings.py` (tests `test_f12_*`)

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/validation/test_audit_cpcv_findings.py -q -k "f12"
```
Expected: 2 FAILED (train window abuts the test end; OOS starts shifted by embargo).

- [ ] **Step 2: Fix mode A — test windows cover full groups**

In `_run_mode_fixed`'s `_one`, replace lines 299–312:

```python
            run_id = f"cpcv-{uuid.uuid4().hex[:8]}"
            embargo_offset = timedelta(days=embargo)  # v1: bars approximated as days
            test_start = group.start + embargo_offset
            # guard: test_start must not exceed group.end
            if test_start > group.end:
                test_start = group.end
            db.add(BacktestRun(
                ...
                date_range_start=test_start,
                date_range_end=group.end,
            ))
```

with:

```python
            run_id = f"cpcv-{uuid.uuid4().hex[:8]}"
            # Test windows are NEVER shrunk (Lopez de Prado): purge/embargo
            # exclude TRAIN data near test boundaries, and mode A has no
            # training — every group is evaluated over its full window.
            db.add(BacktestRun(
                id=run_id, algorithm_id=session.algorithm_id,
                optimization_session_id=session.id,
                status="queued",
                config_overrides=session.base_config or {},
                date_range_start=group.start,
                date_range_end=group.end,
            ))
```

- [ ] **Step 3: Fix mode B train window (purge both sides, embargo after test)**

In `_run_mode_select`'s `_process_split`, replace lines 395–401:

```python
            train_first, train_last = _longest_contiguous_train_run(split.train_groups)
            train_start = groups[train_first].start
            # purge trims the last purge_horizon bars from the train window
            train_end_raw = groups[train_last].end
            train_end = train_end_raw - timedelta(days=purge_horizon)
            if train_end < train_start:
                train_end = train_start
```

with:

```python
            train_first, train_last = _longest_contiguous_train_run(split.train_groups)
            train_start = groups[train_first].start
            train_end = groups[train_last].end
            # Purge: trim the train END where a test group immediately
            # follows (train labels near the boundary overlap test data).
            if (train_last + 1) in split.test_groups:
                train_end = train_end - timedelta(days=purge_horizon)
            # Purge + embargo: push the train START forward where a test
            # group immediately precedes — embargo excludes TRAIN data
            # after the test window (test windows stay full-size).
            if (train_first - 1) in split.test_groups:
                train_start = train_start + timedelta(days=purge_horizon + embargo)
            if train_end < train_start:
                train_end = train_start
```

- [ ] **Step 4: Fix mode B OOS windows — no embargo shift**

Replace lines 424–437 (the OOS loop body up to `db.commit()`):

```python
            oos_runs: dict[int, str] = {}
            for g_idx in split.test_groups:
                rid = f"cpcv-oos-{uuid.uuid4().hex[:8]}"
                g = groups[g_idx]
                # Full-size test window: embargo is applied to the train
                # window (above), never to the test window.
                db.add(BacktestRun(
                    id=rid, algorithm_id=session.algorithm_id,
                    optimization_session_id=session.id,
                    status="queued",
                    config_overrides=inner.winning_config or {},
                    date_range_start=g.start, date_range_end=g.end,
                ))
                db.commit()
```

(The remainder of the loop — `await runner_factory(...)`, `oos_runs[g_idx] = rid` — is unchanged.)

- [ ] **Step 5: Rewrite the two pre-existing tests that encode the bug**

In `tests/coordinator/services/validation/test_cpcv.py`, replace `test_run_cpcv_mode_fixed_applies_embargo_to_each_test_window` (lines ~250–285) with:

```python
@pytest.mark.asyncio
async def test_run_cpcv_mode_fixed_test_windows_cover_full_groups(seeded_session, db_session):
    """Test windows are never shrunk: each segment runs over its full group
    window regardless of embargo (embargo excludes TRAIN data, and mode A
    has no training)."""
    from coordinator.database.models import BacktestRun
    runner_factory = AsyncMock()

    captured: list = []
    async def fake_runner(run_id, bars_cache=None):
        # The orchestrator inserts the row before calling the runner — fetch it.
        row = db_session.query(BacktestRun).filter_by(id=run_id).one()
        captured.append((row.date_range_start, row.date_range_end))
        _complete_run(db_session, run_id, sharpe=0.5)
        db_session.commit()
    runner_factory.side_effect = fake_runner

    await run_cpcv(
        db=db_session, runner_factory=runner_factory,
        session_id=seeded_session.id,
        mode="fixed", n_groups=4, test_groups_per_split=1,
        embargo=10, purge_horizon=0, parallelism=1, bar_count_estimate=400,
    )
    from coordinator.services.validation.cpcv import compute_groups
    expected_groups = compute_groups(
        timeline_start=seeded_session.date_range_start,
        timeline_end=seeded_session.date_range_end,
        bar_count=400, n_groups=4,
    )
    expected_starts = {g.start for g in expected_groups}
    # SQLite may return datetime.datetime; normalise to date for comparison.
    actual_starts = {
        row[0].date() if hasattr(row[0], "date") else row[0]
        for row in captured
    }
    assert actual_starts == expected_starts
```

and replace `test_run_cpcv_mode_select_applies_embargo_to_oos_segments` (lines ~288–334) with:

```python
@pytest.mark.asyncio
async def test_run_cpcv_mode_select_oos_windows_full_size_embargo_on_train(seeded_session, db_session, monkeypatch):
    """Mode B: OOS test windows start at their group starts (full-size);
    embargo is applied by shifting the TRAIN window start where a test
    group immediately precedes it (Lopez de Prado)."""
    from coordinator.services.validation import cpcv as cpcv_mod
    from coordinator.services.validation.sweep import InnerSweepResult
    from coordinator.database.models import BacktestRun

    captured_train_windows: list = []

    async def fake_inner_sweep(**kwargs):
        captured_train_windows.append(
            (kwargs["date_range_start"], kwargs["date_range_end"])
        )
        ids = [f"inner-{uuid.uuid4().hex[:6]}"]
        return InnerSweepResult(
            all_run_ids=ids, winning_run_id=ids[0],
            winning_config={"x": 1}, winning_objective=1.0,
        )
    monkeypatch.setattr(cpcv_mod, "_run_inner_sweep", fake_inner_sweep)

    runner_factory = AsyncMock()
    captured: list = []
    async def fake_runner(run_id, bars_cache=None):
        row = db_session.query(BacktestRun).filter_by(id=run_id).one()
        if row.id.startswith("cpcv-oos-"):
            captured.append((row.date_range_start, row.date_range_end))
        _complete_run(db_session, run_id, sharpe=0.5)
        db_session.commit()
    runner_factory.side_effect = fake_runner

    EMBARGO = 7
    await run_cpcv(
        db=db_session, runner_factory=runner_factory,
        session_id=seeded_session.id,
        mode="select", n_groups=4, test_groups_per_split=2,
        embargo=EMBARGO, purge_horizon=0, parallelism=1, bar_count_estimate=400,
        parameter_space={"x": [1]}, search="grid", max_trials_per_split=1,
    )
    from coordinator.services.validation.cpcv import compute_groups
    expected_groups = compute_groups(
        timeline_start=seeded_session.date_range_start,
        timeline_end=seeded_session.date_range_end,
        bar_count=400, n_groups=4,
    )
    # OOS windows are full-size: starts equal group starts exactly.
    expected_starts = {g.start for g in expected_groups}
    actual_starts = {
        row[0].date() if hasattr(row[0], "date") else row[0]
        for row in captured
    }
    assert actual_starts == expected_starts
    # Embargo lands on the train side: any train window whose preceding
    # group is a test group must start >= that group's end + EMBARGO days.
    group_starts = sorted(g.start for g in expected_groups)
    for train_start, _train_end in captured_train_windows:
        ts = train_start.date() if hasattr(train_start, "date") else train_start
        if ts not in expected_starts:
            # shifted start → the shift must be exactly purge(0) + embargo
            preceding = max(s for s in group_starts if s <= ts)
            assert ts == preceding + timedelta(days=EMBARGO)
```

- [ ] **Step 6: Verify green**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/validation/test_audit_cpcv_findings.py tests/coordinator/services/validation/test_cpcv.py -q
```
Expected: all pass (including the F11 tests from Task 9 and the rewritten embargo tests).

- [ ] **Step 7: Commit**

```bash
git add coordinator/services/validation/cpcv.py tests/coordinator/services/validation/test_cpcv.py
git commit -m "fix(cpcv): full-size test windows; purge/embargo applied to train side per Lopez de Prado (F12)"
```

---

### Task 11: F20a — Benjamini-Hochberg adjusted p-values must be monotone

**Files:**
- Modify: `coordinator/services/validation/multi_test.py:60-83` (the `bh` branch of `correct`)
- Test: `tests/coordinator/services/test_audit_pricing_metrics_findings.py::test_f20a_bh_corrected_p_values_are_monotone_nondecreasing`

- [ ] **Step 1: Run the failing test**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/test_audit_pricing_metrics_findings.py -q -k "f20a"
```
Expected: FAILED — adjusted values `[0.04, 0.04, 0.0333, 0.04]` are non-monotone.

- [ ] **Step 2: Rewrite the `bh` branch with cumulative-min enforcement**

In `coordinator/services/validation/multi_test.py`, replace the `elif method == "bh":` block (lines 60–83):

```python
    elif method == "bh":
        n = len(raw_p_values)
        if n == 0:
            return []
        order = sorted(range(n), key=lambda i: raw_p_values[i])
        sorted_p = [raw_p_values[i] for i in order]
        # BH adjusted p-values: running minimum from the largest rank down
        # enforces monotonicity (adj[k] = min(adj[k+1], p[k] * n / (k+1))).
        adjusted_sorted = [0.0] * n
        running = 1.0
        for k in range(n - 1, -1, -1):
            running = min(running, sorted_p[k] * n_tested / (k + 1))
            adjusted_sorted[k] = min(running, 1.0)
        thresholds = [alpha * (k + 1) / n_tested for k in range(n)]
        # Find largest k where sorted_p[k] <= thresholds[k]
        k_max = -1
        for k in range(n):
            if sorted_p[k] <= thresholds[k]:
                k_max = k
        significant_set = set(order[: k_max + 1]) if k_max >= 0 else set()
        corrected_by_index = {order[k]: adjusted_sorted[k] for k in range(n)}
        return [
            CorrectedResult(
                raw_p=raw_p_values[i],
                corrected_p=corrected_by_index[i],
                significant=(i in significant_set),
                method=method,
            )
            for i in range(n)
        ]
```

- [ ] **Step 3: Verify green**

Run the Step 1 command. Expected: PASS.

- [ ] **Step 4: Run the surrounding suite**

Run: `.venv/bin/pytest tests/ -q -k "multi_test"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/multi_test.py
git commit -m "fix(validation): BH adjusted p-values use cumulative-min, restoring monotonicity (F20a)"
```

---

### Task 12: F13 — bitemporal upsert must not destroy original observations

**Files:**
- Modify: `coordinator/services/datasets/storage.py:76-78`
- Test: `tests/coordinator/services/test_audit_data_layer_findings.py::test_f13_revision_preserves_original_observation_for_earlier_as_of`

- [ ] **Step 1: Run the failing test**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/test_audit_data_layer_findings.py -q -k "f13"
```
Expected: FAILED — as-of read returns 0 rows (original observation destroyed by `keep="last"` dedupe).

- [ ] **Step 2: Include knowledge_date in the dedupe key**

In `coordinator/services/datasets/storage.py`, replace lines 76–78:

```python
        id_cols = [c for c in self._id_columns_after_rename(spec) if c in df.columns]
        if id_cols:
            # Bitemporal append-only semantics: a restatement (same logical id,
            # later knowledge_date) must ADD a row, never replace the original
            # observation — otherwise as-of reads leak revised values back in
            # time. keep="last" now only collapses true re-ingests of the SAME
            # observation.
            dedupe_cols = list(id_cols)
            if "knowledge_date" in df.columns and "knowledge_date" not in dedupe_cols:
                dedupe_cols.append("knowledge_date")
            df = df.drop_duplicates(subset=dedupe_cols, keep="last")
```

- [ ] **Step 3: Verify green**

Run the Step 1 command. Expected: PASS.

- [ ] **Step 4: Run the datasets suite**

Run: `.venv/bin/pytest tests/coordinator/services/datasets/ -q --ignore=tests/coordinator/services/datasets/test_forward_bias.py`
Expected: all pass (`test_forward_bias.py` is excluded — pre-existing missing `hypothesis` dependency).

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/datasets/storage.py
git commit -m "fix(datasets): upsert dedupe includes knowledge_date so restatements append, not replace (F13)"
```

---

### Task 13: F14 + F15 — provider timestamps (Theta ET-as-UTC; Tradier midnight stamping)

**Files:**
- Modify: `coordinator/services/data_providers/theta.py:2` (imports) and `:99-108` (intraday stamping)
- Modify: `coordinator/services/data_providers/tradier.py:4` (imports) and `:114-128` (daily stamping)
- Test: `tests/coordinator/services/test_audit_data_layer_findings.py` (tests `test_f14_*`, `test_f15_*`)

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/test_audit_data_layer_findings.py -q -k "f14 or f15"
```
Expected: 3 FAILED (bar at 09:30 UTC instead of 14:30 UTC; ValueError on hour=24; daily bar at midnight UTC).

- [ ] **Step 2: Fix Theta intraday stamping (F14)**

In `coordinator/services/data_providers/theta.py`, change line 2 to:

```python
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
```

Then in `_fetch_intraday`, replace lines 99–108:

```python
                bar_date = r.get("date", "")
                ms = r.get("ms_of_day", 0)
                ts = datetime.combine(
                    date.fromisoformat(str(bar_date)), datetime.min.time(), tzinfo=timezone.utc
                )
                ts = ts.replace(
                    hour=ms // 3600000,
                    minute=(ms % 3600000) // 60000,
                    second=(ms % 60000) // 1000,
                )
```

with:

```python
                bar_date = r.get("date", "")
                ms = r.get("ms_of_day", 0)
                # ms_of_day is milliseconds since midnight US/Eastern (wall
                # clock). timedelta addition on a ZoneInfo-aware datetime is
                # wall-clock arithmetic, so this also absorbs 24:00 prints
                # (ms=86400000 → next day 00:00) without crashing.
                ts = datetime.combine(
                    date.fromisoformat(str(bar_date)),
                    datetime.min.time(),
                    tzinfo=ZoneInfo("America/New_York"),
                ) + timedelta(milliseconds=ms)
                ts = ts.astimezone(timezone.utc)
```

- [ ] **Step 3: Fix Tradier daily stamping (F15)**

In `coordinator/services/data_providers/tradier.py`, change line 4 to:

```python
from datetime import date, datetime, time, timezone
```

and add below the datetime import:

```python
from zoneinfo import ZoneInfo
```

Then replace the bar construction (lines 114–128):

```python
        bars = [
            {
                # Open-time stamp convention: US-equity daily bars open at
                # 09:30 America/New_York, converted to UTC.
                "timestamp": datetime.combine(
                    date.fromisoformat(r["date"]),
                    time(9, 30),
                    tzinfo=ZoneInfo("America/New_York"),
                ).astimezone(timezone.utc).isoformat(),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
            }
            for r in day_data
        ]
```

- [ ] **Step 4: Verify green**

Run the Step 1 command. Expected: 3 passed.

- [ ] **Step 5: Run the provider suites**

Run: `.venv/bin/pytest tests/ -q -k "theta or tradier"`
Expected: all pass. Pre-existing tests asserting midnight-UTC timestamps encode the bug — update their expected timestamps (daily: 14:30Z winter / 13:30Z summer; intraday: ET wall time converted to UTC).

- [ ] **Step 6: Commit**

```bash
git add -A coordinator/services/data_providers/theta.py coordinator/services/data_providers/tradier.py tests/
git commit -m "fix(providers): Theta ms_of_day is US/Eastern; Tradier daily bars stamped at 09:30 ET open (F14, F15)"
```

---

### Task 14: F16 — invalidate broker cache after order submission

**Files:**
- Modify: `worker/tick_loop.py:102-105`
- Test: `tests/worker/test_audit_worker_findings.py::test_f16_invalidate_called_after_successful_order`

- [ ] **Step 1: Run the failing test**

Run:
```bash
.venv/bin/pytest tests/worker/test_audit_worker_findings.py -q -k "f16"
```
Expected: FAILED — `invalidate_calls` is 0.

- [ ] **Step 2: Call `invalidate()` after each submit**

In `worker/tick_loop.py`, after the `submit_order` call (line 102–104), insert the invalidation so the block reads:

```python
                for leg in signal.legs:
                    order_result = self._broker.submit_order(
                        symbol=leg.symbol, side=leg.signal_type.value, quantity=leg.quantity,
                        order_type=leg.order_type.value, limit_price=leg.limit_price, stop_price=leg.stop_price)
                    # The order changed account state: drop cached
                    # positions/balances so the next read is fresh
                    # (CachingBrokerAdapter docstring contract). hasattr guard:
                    # bare adapters (MockBrokerAdapter et al.) have no cache.
                    if hasattr(self._broker, "invalidate"):
                        self._broker.invalidate()
                    result.trade_results.append(TradeResult(signal=signal, order_result=order_result))
```

- [ ] **Step 3: Verify green**

Run the Step 1 command. Expected: PASS.

- [ ] **Step 4: Run the worker suite**

Run: `.venv/bin/pytest tests/worker/ -q`
Expected: all pass except the not-yet-fixed F17/F18 audit tests.

- [ ] **Step 5: Commit**

```bash
git add worker/tick_loop.py
git commit -m "fix(worker): invalidate broker cache after order submission so post-fill reads are fresh (F16)"
```

---

### Task 15: F17 — signal-approval futures: FIFO queue per instance

The coordinator's `signal_response` carries no request id, so concurrent requests for one instance must pair responses FIFO. A second request must never overwrite (and orphan) the first future.

**Files:**
- Modify: `worker/agent.py:45` (init), `:111-121` (`request_signal_approval`), `:197-203` (`_handle_signal_response`)
- Test: `tests/worker/test_audit_worker_findings.py::test_f17_concurrent_signal_approvals_for_same_instance_both_resolve`

- [ ] **Step 1: Run the failing test**

Run:
```bash
.venv/bin/pytest tests/worker/test_audit_worker_findings.py -q -k "f17"
```
Expected: FAILED — 1 of 2 concurrent requests never resolves (future overwritten).

- [ ] **Step 2: Change the pending map to per-instance deques**

In `worker/agent.py`, add to the imports at the top of the file:

```python
from collections import deque
```

Change line 45:

```python
        self._pending_signal_responses: dict[str, deque] = {}
```

- [ ] **Step 3: Rewrite `request_signal_approval`**

Replace lines 111–121:

```python
    async def request_signal_approval(self, instance_id: str, signal: dict) -> dict:
        # Responses carry no request id, so requests and responses for an
        # instance pair FIFO; a deque (not a single slot) keeps concurrent
        # requests from orphaning each other's futures.
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_signal_responses.setdefault(instance_id, deque()).append(fut)
        await self._send({"type": "signal_request", "instance_id": instance_id, "signal": signal,
                         "timestamp": datetime.now(timezone.utc).isoformat()})
        try:
            return await asyncio.wait_for(fut, timeout=30.0)
        except asyncio.TimeoutError:
            return {"approved": False, "reason": "Signal approval timed out"}
        finally:
            queue = self._pending_signal_responses.get(instance_id)
            if queue is not None:
                try:
                    queue.remove(fut)
                except ValueError:
                    pass  # already consumed by _handle_signal_response
                if not queue:
                    self._pending_signal_responses.pop(instance_id, None)
```

- [ ] **Step 4: Rewrite `_handle_signal_response`**

Replace lines 197–203:

```python
    async def _handle_signal_response(self, message: dict) -> None:
        instance_id = message.get("instance_id")
        queue = self._pending_signal_responses.get(instance_id)
        fut = None
        while queue:
            candidate = queue.popleft()
            if not candidate.done():
                fut = candidate
                break
        if fut is not None:
            fut.set_result(message)
        else:
            logger.warning("Received signal_response for %s with no pending request", instance_id)
```

- [ ] **Step 5: Verify green**

Run the Step 1 command. Expected: PASS (both tasks resolve approved within 1 s).

- [ ] **Step 6: Run the worker suite**

Run: `.venv/bin/pytest tests/worker/ -q`
Expected: all pass except the not-yet-fixed F18 audit test.

- [ ] **Step 7: Commit**

```bash
git add worker/agent.py
git commit -m "fix(worker): FIFO deque of approval futures per instance; concurrent requests no longer orphaned (F17)"
```

---

### Task 16: F18 — serialize tick-batch entries per instance, keep task references

**Files:**
- Modify: `worker/agent.py` (init + `_handle_tick_batch`, lines ~216-224)
- Test: `tests/worker/test_audit_worker_findings.py::test_f18_tick_batch_entries_for_one_instance_are_serialized`

- [ ] **Step 1: Run the failing test**

Run:
```bash
.venv/bin/pytest tests/worker/test_audit_worker_findings.py -q -k "f18"
```
Expected: FAILED — events interleave (`e2` ends before `e1`).

- [ ] **Step 2: Add per-instance tail tracking to `__init__`**

In `WorkerAgent.__init__` (after the `_pending_signal_responses` line), add:

```python
        # Per-instance chain tail for tick serialization + strong refs so
        # fire-and-forget tasks aren't garbage-collected mid-flight.
        self._instance_tick_tails: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()
```

- [ ] **Step 3: Rewrite `_handle_tick_batch` with per-instance chaining**

Replace lines 216–224:

```python
    async def _handle_tick_batch(self, message: dict) -> None:
        for entry in (message.get("ticks") or []):
            inst_id = entry.get("instance_id")
            runtime = self._running_instances.get(inst_id)
            if runtime is None:
                logger.debug("tick_batch entry for unknown instance %s; ignoring", inst_id)
                continue
            # Entries for ONE instance are chained (strict arrival order);
            # different instances still run concurrently, so a slow algorithm
            # doesn't block its siblings.
            prev = self._instance_tick_tails.get(inst_id)
            task = asyncio.create_task(
                self._run_tick_entry_serialized(prev, runtime, entry, inst_id)
            )
            self._instance_tick_tails[inst_id] = task
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _run_tick_entry_serialized(
        self, prev: "asyncio.Task | None", runtime: Any, entry: dict, inst_id: str,
    ) -> None:
        if prev is not None:
            try:
                await prev
            except Exception:
                pass  # the previous entry's failure was already logged below
        try:
            await runtime.on_tick_batch_entry(entry)
        except Exception:
            logger.exception("tick_batch entry failed for instance %s", inst_id)
```

- [ ] **Step 4: Verify green**

Run the Step 1 command. Expected: PASS (events strictly `e1 start, e1 end, e2 start, e2 end`).

- [ ] **Step 5: Run the worker suite**

Run: `.venv/bin/pytest tests/worker/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add worker/agent.py
git commit -m "fix(worker): chain tick-batch entries per instance and retain task refs (F18)"
```

---

### Task 17: F19 — SignalLeg validates quantity and prices at the SDK boundary

**Files:**
- Modify: `sdk/signals.py:1-6` (imports) and `:53-54` (`__post_init__`)
- Test: `tests/sdk/test_audit_signals_findings.py`

- [ ] **Step 1: Run the failing tests**

Run:
```bash
.venv/bin/pytest tests/sdk/test_audit_signals_findings.py -q
```
Expected: 4 FAILED (quantity 0 / -5 / NaN / inf all accepted).

- [ ] **Step 2: Add validation to `__post_init__`**

In `sdk/signals.py`, add `import math` below `from __future__ import annotations`:

```python
from __future__ import annotations

import math

from dataclasses import dataclass, field
```

Then replace `__post_init__` (lines 53–54):

```python
    def __post_init__(self) -> None:
        _validate_asset_type(self.asset_type)
        q = self.quantity
        if not isinstance(q, (int, float)) or isinstance(q, bool) \
                or not math.isfinite(q) or q <= 0:
            raise ValueError(
                f"quantity must be a positive finite number, got {q!r}"
            )
        for name, px in (("limit_price", self.limit_price),
                         ("stop_price", self.stop_price)):
            if px is None:
                continue
            if not isinstance(px, (int, float)) or isinstance(px, bool) \
                    or not math.isfinite(px) or px <= 0:
                raise ValueError(
                    f"{name} must be a positive finite number when set, got {px!r}"
                )
```

- [ ] **Step 3: Verify green**

Run the Step 1 command. Expected: 4 passed.

- [ ] **Step 4: Run the SDK + worker suites**

Run: `.venv/bin/pytest tests/sdk/ tests/worker/ -q`
Expected: all pass. Any fixture constructing a `SignalLeg` with quantity ≤ 0 was relying on the missing validation — give it a positive quantity.

- [ ] **Step 5: Commit**

```bash
git add -A sdk/signals.py tests/
git commit -m "fix(sdk): SignalLeg rejects non-positive/non-finite quantity and prices (F19)"
```

---

### Task 18: Final verification + backlog

**Files:**
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Run all 33 audit tests together**

Run:
```bash
.venv/bin/pytest tests/coordinator/services/test_audit_engine_findings.py tests/coordinator/services/test_audit_pricing_metrics_findings.py tests/coordinator/services/validation/test_audit_cpcv_findings.py tests/coordinator/services/test_audit_data_layer_findings.py tests/worker/test_audit_worker_findings.py tests/sdk/test_audit_signals_findings.py -q
```
Expected: **33 passed**.

- [ ] **Step 2: Run the full suite**

Run:
```bash
.venv/bin/pytest tests/ -q --ignore=tests/coordinator/services/datasets/test_forward_bias.py
```
Expected: all pass. (The ignored file has a pre-existing missing-`hypothesis` collection error unrelated to this work.) Fix any stragglers per the per-task guidance (tests encoding old buggy values get corrected expected values — never weaken an audit test).

- [ ] **Step 3: Append deferred items to the backlog**

Add to `docs/superpowers/backlog.md`:

```markdown
- [ ] Audit follow-up (2026-06-11): non-options callers of `asset_services/base.py::_bar_lookup` still use the inclusive (legacy) cutoff — pass `timeframe_seconds` from equity/crypto `get_price` call sites and pin with tests (residual F1-family seam outside the engine).
- [ ] Audit follow-up (2026-06-11): `_build_union_clock` keep="first" union rows still mix symbols' OHLC; harmless for fills after F3 (every symbol resolves its own frame) but consider a symbol-tagged clock for observers that read clock-row OHLC.
- [ ] Audit follow-up (2026-06-11): `hypothesis` missing from the venv — `tests/coordinator/services/datasets/test_forward_bias.py` cannot collect. Install or vendor the dependency.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/backlog.md
git commit -m "chore: record audit follow-ups on backlog; all 33 audit tests green"
```
