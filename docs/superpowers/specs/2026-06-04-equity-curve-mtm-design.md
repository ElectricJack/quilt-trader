# Equity Curve MTM — Conservative Options Valuation Fallback

**Status:** Design approved 2026-06-04. Pending implementation plan.

**Motivation:** The 2026-06-03 `options-ema-spreads` diagnosis surfaced that the engine's equity curve sat flat at `$50,000` for the entire 6-month window during which the algorithm was holding 17,950 contracts of SPY $586 short calls that ultimately settled at -$10.67M. The drawdown was invisible until expiry. Root cause: `coordinator/services/backtest_engine_v2.py::_positions_market_value` falls back to `ps.avg_price` (cost basis) when option chain data is unavailable for a bar. For a short option, `qty × avg_price × multiplier` is exactly the negative of the cash inflow from the original sell-to-open, so `cash + position_mv` collapses to `initial_cash`. The position's true risk is invisible.

The framework's buying-power constraint (commit `33b140e`) prevents the catastrophic over-leverage that surfaced this bug, so the misleading equity curve is no longer hiding multi-million-dollar losses in practice. But the diagnostic gap remains: any legitimate strategy holding options through sparse chain-data windows shows an inaccurate equity curve, and any subtler MTM optimism is potentially exploitable by an algorithm that reads `ctx.account_value` to size new positions.

**Goal:** Replace the "mark at cost" fallback with a layered, direction-aware, conservative MTM that:
1. Uses real chain mid when available.
2. Otherwise computes a theoretical Black-Scholes price using carry-forward IV.
3. Biases the result against the algorithm's position direction so no edge can be exploited by reading `ctx.account_value` or `ctx.positions[s].current_price` during chain-data gaps.

---

## 1. Architecture

Single seam: `coordinator/services/backtest_engine_v2.py::_lookup_option_mtm_price` (called by `_positions_market_value` and `_positions_snapshot`). Today returns chain mid if available, else `None` (caller falls back to `ps.avg_price`). The fix replaces the `None` return with a real layered fallback that knows the position's direction.

New module `coordinator/services/options_mtm.py` containing:

- `class OptionsMTMHelper` instantiated per engine run, holding two in-memory caches: a three-tier IV carry-forward cache (per-symbol, per-(underlying, expiration), per-underlying ATM) and a per-symbol last-known-mid cache. All entries hold the `sim_time` they were captured at.
- Pure function `black_scholes_price(S, K, T, r, sigma, option_type) -> float` with inline normal-CDF via `scipy.stats.norm.cdf` (already a transitive dep — `pandas-market-calendars` brought it in).
- `OptionsMTMHelper.observe(symbol, mid, iv, sim_time, underlying, expiration_str)` — called whenever the engine successfully reads a live chain bar in `_lookup_option_mtm_price`'s success path; populates all four caches.
- `OptionsMTMHelper.mtm_price(symbol, sim_time, underlying_price, position_quantity, occ_parsed, alpha) -> float` — the workhorse. `occ_parsed` is a dict with keys `underlying`, `expiration` (date), `option_type` (`'C'`/`'P'`), `strike`. `alpha` is the session's `mtm_realism ∈ [0.0, 1.0]`. Applies Layer 2 + Layer 3 + direction-aware envelope.

The engine's `_lookup_option_mtm_price` becomes a thin shim that calls into `OptionsMTMHelper.mtm_price(...)` on chain-data miss and `OptionsMTMHelper.observe(...)` on chain-data hit. `_positions_market_value` and `_positions_snapshot` pass `ps.quantity` so the helper knows direction (positive = long, negative = short).

The cache is rebuilt every engine run (zero persistence) and populated lazily as chain bars are observed during the run. By construction, the cache reflects everything the engine has seen so far in the run.

---

## 2. Valuation logic

Three layers, applied in order:

### 2.1 Layer 1 — Live chain mid

If `ctx._option_chain_cache` has a row for this `(provider, underlying, expiration)` matching `symbol` with a valid `bid` and `ask` for the current `sim_time`, return `mid = (bid + ask) / 2` and call `helper.observe(symbol, row.implied_volatility, sim_time)` so the cache stays warm.

### 2.2 Layer 2 — Black-Scholes with carry-forward IV

When Layer 1 misses:

1. Parse the OCC symbol → `underlying`, `expiration_date`, `option_type` (`C`/`P`), `strike`.
2. Compute `days_to_expiry = (expiration_date - sim_time.date()).days`. If `≤ 0`, fall through to intrinsic-only (Layer 3) since the option has expired.
3. Look up `S = underlying close at sim_time` from `ctx._bars`. If unavailable (rare), fall through to Layer 3.
4. Resolve volatility input in this order:
   - `helper._iv_cache[symbol].iv` if present (most accurate — this specific contract's last observed IV)
   - Otherwise the most recent IV for any contract with the same `(underlying, expiration_date)` we've seen in this run
   - Otherwise the underlying's most recent ATM IV from any chain read
   - Otherwise constant `FALLBACK_SIGMA = 0.40` (high enough that shorts don't get fake relief)
5. Compute `bs_price = black_scholes_price(S, K, T=days_to_expiry/365, r=RISK_FREE_RATE, sigma=resolved_iv, option_type)`.
6. Compute `intrinsic = max(S - K, 0)` for calls, `max(K - S, 0)` for puts.
7. Apply the direction-aware envelope (next section).

`RISK_FREE_RATE` is a module-level constant `= 0.045` (4.5%, a reasonable short-Treasury proxy at the time of writing). Not configurable per run — this is a fallback path, not a research input.

### 2.3 Layer 3 — Intrinsic only (deep fallback)

If we couldn't even resolve `S` or the option has expired:
- Return `intrinsic` (no time value).
- Direction-aware envelope still applies.

### 2.4 Direction-aware envelope with session-tunable realism

The envelope is parametrized by a session-level `mtm_realism: float` in `[0.0, 1.0]`:

- **`mtm_realism = 0.0`** (default, most conservative): full envelope active.
- **`mtm_realism = 1.0`** (most broker-like, potentially exploitable): no envelope; algorithm sees the unbiased Black-Scholes price.
- **Intermediate values**: linear interpolation between the two endpoints.

After Layer 2 or Layer 3 produces a candidate `bs_or_intrinsic`, apply:

**For LONG positions** (`position_quantity > 0`):
```
conservative_estimate = min(bs_or_intrinsic, last_known_mid or +∞)
mtm = α × bs_or_intrinsic + (1 - α) × conservative_estimate
```
where `α = mtm_realism`. At α=0 the cap fully applies; at α=1 the unbiased BS price is used; intermediate α blends them. An algorithm holding a long option with α=0 can never see its position get more valuable than the last actual market quote.

**For SHORT positions** (`position_quantity < 0`):
```
conservative_estimate = max(bs_or_intrinsic, intrinsic, last_known_mid or 0)
mtm = α × bs_or_intrinsic + (1 - α) × conservative_estimate
```
At α=0 the floor fully applies; at α=1 the unbiased BS price is used. An algorithm holding a short option with α=0 can never see its liability shrink below the worst credible estimate.

`last_known_mid` is the most recent chain-mid we've seen for this contract, carried forward indefinitely within the run. (We separately cache `(symbol, sim_time, last_mid)` whenever Layer 1 hits.)

For `position_quantity == 0` (closing fills mid-bar): envelope doesn't apply; returns Layer 2/3 unbiased regardless of α.

**Note:** The envelope (and therefore α) only affects the fallback paths (Layers 2 and 3). When Layer 1 succeeds (live chain mid available — the common case for liquid contracts), the backtest uses real market mid and the value matches what a broker would show, regardless of α.

### 2.5 Worked example — yesterday's bug

Run: short 17,950 SPY $586 calls opened 2024-12-03 at $0.02 fill, account $50k.

On 2024-12-15 (mid-window, no chain data for this contract that bar), SPY closes at $591:

- Layer 1: no chain mid → skip.
- Layer 2:
  - `S=591, K=586, T=18/365, σ=resolved_iv` (suppose the cache has IV=0.18 from the contract's most-recent observation around open)
  - `bs_price ≈ $7.20` (intrinsic $5 + time value $2.20)
  - `intrinsic = max(591-586, 0) = $5`
- Layer 2.4 envelope, three α settings (short, qty = -17950):

| `mtm_realism` α | `conservative_estimate = max($7.20, $5, $0.02)` | `mtm = α × $7.20 + (1-α) × $7.20` | per-contract liability | total position_mv |
|---|---|---|---|---|
| 0.0 (default) | $7.20 | $7.20 | $7.20 | -$12,924,000 |
| 0.5 | $7.20 | $7.20 | $7.20 | -$12,924,000 |
| 1.0 (broker-like) | (n/a) | $7.20 | $7.20 | -$12,924,000 |

In this case `bs_or_intrinsic == conservative_estimate` because BS exceeds both intrinsic and last_known_mid, so α doesn't change the answer — every realism setting catches the bug equally.

The α matters more in cases like "short OTM near expiry with stale chain data": BS might say $0.30 while intrinsic = $0 and last_known_mid = $0.20. At α=0: mtm = max($0.30, $0, $0.20) = $0.30. At α=1: mtm = $0.30. At α=0.5: mtm = $0.30. (Still no difference here.) But for "short near-the-money where BS undershoots last_known_mid" — say BS=$0.50, intrinsic=$0, last_mid=$0.80 — at α=0: max($0.50, $0, $0.80) = $0.80; at α=1: $0.50; at α=0.5: $0.65. That's where the dial actually does something.

Either way, in this bug case, `position_mv = -17950 × $7.20 × 100 = -$12,924,000`. With cash ≈ `$85,900` (initial + premium), `equity ≈ -$12,838,100`. The algorithm sees `ctx.account_value ≈ -$12.8M` on that bar. **It cannot size new positions based on a $50k account it doesn't have.** The equity curve trends down toward this number from the day the position was opened, not in a one-day cliff at expiry.

Contrast: under today's broken fallback, `position_mv = -17950 × $0.02 × 100 = -$35,900`, `equity = $85,900 - $35,900 = $50,000` — same as initial cash, totally hiding the $12.8M risk.

---

## 3. Module structure

`coordinator/services/options_mtm.py` (new file, ~150 lines):

```python
"""Conservative options-MTM helper for backtest valuation.

Used when live chain mid is unavailable. Produces a Black-Scholes
estimate with a direction-aware envelope so no algorithm can exploit
chain-data sparseness to mis-size positions.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from scipy.stats import norm

RISK_FREE_RATE = 0.045
FALLBACK_SIGMA = 0.40


@dataclass
class _IVCacheEntry:
    sim_time: datetime
    iv: float


@dataclass
class _MidCacheEntry:
    sim_time: datetime
    mid: float


def black_scholes_price(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str,
) -> float:
    """Black-Scholes price. option_type is 'C' or 'P'. T in years (≥ 0)."""
    # ... clean inline impl with edge-case guards (T==0 returns intrinsic, etc.)


class OptionsMTMHelper:
    def __init__(self):
        self._iv_cache: dict[str, _IVCacheEntry] = {}      # by OCC symbol
        self._iv_by_expiry: dict[tuple[str, str], _IVCacheEntry] = {}  # (underlying, expiration)
        self._iv_by_underlying: dict[str, _IVCacheEntry] = {}  # underlying ATM IV
        self._mid_cache: dict[str, _MidCacheEntry] = {}    # last-known mid by OCC symbol

    def observe(self, symbol: str, mid: float, iv: float, sim_time: datetime,
                underlying: str, expiration_str: str) -> None:
        """Called whenever the engine reads a live chain bar."""
        # populate all three IV caches + the mid cache

    def mtm_price(self, symbol: str, sim_time: datetime,
                  underlying_price: float, position_quantity: float,
                  occ_parsed: dict, alpha: float = 0.0) -> float:
        """Return a conservative MTM price for the option.

        occ_parsed has keys: underlying, expiration (date), option_type
        ('C'/'P'), strike. alpha is the session's mtm_realism in [0, 1]:
        0 = fully conservative, 1 = unbiased BS (no envelope).
        """
        # Layer 2 + Layer 3 + direction-aware envelope (lerp by alpha)
```

---

## 4. Engine wiring + session plumbing

### 4.1 Session schema addition

`OptimizationSession` model and the corresponding API/CLI/dashboard surfaces gain:

```python
mtm_realism: Mapped[float] = mapped_column(
    Float, nullable=False, server_default="0.0",
)
```

- Validation: `0.0 <= mtm_realism <= 1.0` (Pydantic `model_validator(mode="after")` on `CreateSessionRequest`).
- API: `CreateSessionRequest` and `SessionResponse` add the field.
- CLI: `quilt research session create` adds `--mtm-realism FLOAT` (default 0.0).
- Dashboard: `ExperimentScopeFields` adds a numeric input (range 0-1, step 0.05).

Migration is additive (`Float`, NOT NULL, server_default 0.0) — existing sessions get the conservative default.

### 4.2 Engine plumbing

The session's `mtm_realism` flows through `request_payload` → `_dispatch_sweep` / `_dispatch_walk_forward` → `run_sweep` / `run_walk_forward` → `_run_one_backtest` / `_run_oos_backtest` → `BacktestEngine.run(mtm_realism=...)` → stored on `engine.config["mtm_realism"]` → read by `OptionsMTMHelper.mtm_price(α=...)` when applying the envelope.

### 4.3 Engine internals

`coordinator/services/backtest_engine_v2.py` changes:

1. `BacktestEngine.run` signature gains `mtm_realism: float = 0.0` kwarg, stored on `self._mtm_realism` for the run's duration.
2. Build `self._mtm_helper = OptionsMTMHelper()` inside `run()` (per-run, no cross-run state).
3. In `_lookup_option_mtm_price`:
   - On success (chain mid found): call `self._mtm_helper.observe(symbol, mid, iv, sim_time, underlying, expiration_str)` before returning the mid.
   - On failure (no chain data): no longer return `None`. Instead, parse the OCC symbol, look up the underlying close from `ctx._bars`, and call `self._mtm_helper.mtm_price(symbol, sim_time, underlying_price, position_quantity, occ_parsed, alpha=self._mtm_realism)`.
4. `_positions_market_value` and `_positions_snapshot` pass `ps.quantity` into `_lookup_option_mtm_price`. Backward-compat: `_lookup_option_mtm_price(symbol, ctx, position_quantity=0)` — when called without a position context (e.g. from fill-price resolution), envelope is bypassed (returns Layer 2 unbiased regardless of α).
5. The old "mark at cost" fallback in `_positions_market_value` and `_positions_snapshot` is removed (`option_price if option_price is not None else ps.avg_price` → just `self._lookup_option_mtm_price(sym, ctx, ps.quantity)` which now always returns a real number).

---

## 5. Testing

`tests/coordinator/services/test_options_mtm.py` (new):

- **Black-Scholes correctness:** parametrize against a few known values (ATM ITM/OTM call/put with known σ, T, r — assert within 1% of textbook).
- **Layer 1 — chain mid path:** when chain has `{bid, ask, iv}`, observe is called and mid is returned.
- **Layer 2 — Black-Scholes with cached IV:** observe an IV for symbol X, then call `mtm_price` without a chain mid. Returns BS using that IV. Verify within tolerance.
- **Layer 2 — IV fallback chain:** cache empty for symbol X but populated for `(underlying, expiry)` → uses that. Cache fully empty → uses `FALLBACK_SIGMA = 0.40`.
- **Layer 3 — intrinsic only:** no underlying price → returns intrinsic.
- **Layer 3 — expired option:** `T ≤ 0` → returns intrinsic.

Direction-aware envelope tests:

- **Long position, BS > last_mid:** mtm = last_mid (caps optimism).
- **Long position, BS < last_mid:** mtm = BS (no inflation).
- **Short position, BS < intrinsic:** mtm = intrinsic (floors at intrinsic).
- **Short position, BS > last_mid:** mtm = BS (no relief).
- **Short position, intrinsic > BS > last_mid:** mtm = intrinsic (highest of three).

`tests/coordinator/services/test_backtest_engine.py` (extend):

- **Regression for the bug:** algo opens a short SPY ATM call, simulate 5 bars with NO chain data, assert `observer.equity[-1].pv` reflects the negative position value (within 10% of intrinsic-based estimate). Without the fix, equity stays at `cash + premium = initial_cash + premium`.

---

## 6. Performance

`mtm_price` is called once per open position per bar. The Black-Scholes calc is ~4 transcendental ops (norm.cdf × 2, exp, sqrt) ≈ ~10 µs each. For an algo holding 50 positions over a 2-year daily backtest (500 bars), that's `50 × 500 × 10µs = 0.25s` total overhead. Acceptable.

The IV/mid caches are small dicts keyed by OCC string. No persistence — rebuilt each engine run. Memory ≤ a few MB for any reasonable run.

---

## 7. Out of scope / known tradeoffs

- **American-style early exercise.** Black-Scholes assumes European-style. Real US equity options are American (can be exercised before expiry, mainly relevant for deep-ITM calls on dividend-paying stocks). The European approximation underprices American calls slightly. Worst case: short call MTM is slightly low. Acceptable for v1 because (a) most short-call early-exercise risk is captured by the intrinsic floor, and (b) the engine doesn't model early exercise either.
- **Dividends.** Black-Scholes here treats the dividend yield as 0. For dividend-paying underlyings (most equity indexes), this overprices calls and underprices puts slightly. The intrinsic floor catches the cases that matter. A future enhancement could lookup dividend yield by underlying.
- **Live (paper/real) trading MTM.** This spec is backtest-only. Live trading already has a different MTM path (`worker/...`) that gets quotes from the broker. No changes to that path.
- **Persistent IV cache across runs.** Each engine run starts with an empty cache. Could potentially be sped up with a cross-run cache, but the within-run cache fills quickly and the constant-σ fallback is fine for the cold-start case.
- **Engine recording `position_mv` separately from `cash` in the equity snapshot.** The chunking observer already writes both `portfolio_value` and `cash` to disk, and the finalizer already reads both into `equity_curve`. No schema change.
- **Backtest MTM diverges from broker MTM when `mtm_realism < 1.0` and chain data is sparse.** This is the intentional tradeoff. The conservative envelope biases shorts up and longs down so no algorithm can manufacture a fake edge by reading `ctx.account_value` or `ctx.positions[s].current_price` during chain-data gaps. As a side effect, with α=0 (the default), a backtest will report slightly worse outcomes than the equivalent live run would have produced in those same gap bars. The asymmetry is deliberate: better to undersell a strategy in backtest than oversell it. Users who want the backtest-vs-live behaviors to match exactly should run with `mtm_realism=1.0`, accepting that the algorithm now has access to a (potentially exploitable) unbiased BS price during gap bars. Anyone debugging "why does this work in backtest but lose money live" should read this note.

---

## 8. Files touched

**Created:**
- `coordinator/services/options_mtm.py` — the helper + BS function
- `tests/coordinator/services/test_options_mtm.py` — layered fallback + envelope tests
- `coordinator/migrations/versions/<rev>_add_mtm_realism_to_optimization_sessions.py` — additive Alembic migration

**Modified:**
- `coordinator/services/backtest_engine_v2.py` — instantiate `OptionsMTMHelper` in run setup; rewire `_lookup_option_mtm_price`, `_positions_market_value`, `_positions_snapshot` to thread `position_quantity` and the run's `mtm_realism` into the helper; remove the cost-basis fallback
- `tests/coordinator/services/test_backtest_engine.py` — add bug-regression test (chain-data-gap short call)
- `coordinator/database/models.py:458` (`OptimizationSession`) — add `mtm_realism: Mapped[float]` column
- `coordinator/api/routes/research.py:44` (`CreateSessionRequest`) and `:76` (`SessionResponse`) — add `mtm_realism: float = 0.0` with `model_validator(mode="after")` enforcing `0.0 <= mtm_realism <= 1.0`
- `coordinator/services/research_job_manager.py` — thread `mtm_realism` from session row → dispatch payload → `BacktestEngine.run(mtm_realism=...)` for both sweep and walk-forward paths
- `sdk/cli/commands/research.py:64` (`session create`) — add `--mtm-realism FLOAT` (default 0.0) and pass through to the API request
- `dashboard/src/components/ExperimentScopeFields.tsx` — numeric input for `mtm_realism` (range 0-1, step 0.05, default 0.0, with help text describing the conservative-vs-broker-like tradeoff)
- `tests/coordinator/api/test_research_routes.py` — validation tests for `mtm_realism` (rejects <0 or >1, accepts 0.0 and 1.0 endpoints; persists round-trip)
- `tests/sdk/cli/test_research_cli.py` — CLI flag passes through to the request body
