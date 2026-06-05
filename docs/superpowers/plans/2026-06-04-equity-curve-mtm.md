# Equity Curve MTM — Conservative Options Valuation Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `ps.avg_price` fallback in `backtest_engine_v2.py` with a layered, direction-aware, conservative MTM (chain mid → carry-forward-IV Black-Scholes → intrinsic) parametrized by a session-level `mtm_realism ∈ [0, 1]` lerp dial.

**Architecture:** A new pure-Python helper module (`coordinator/services/options_mtm.py`) holds the Black-Scholes function, the per-run IV/mid caches, and the envelope/lerp logic. The engine instantiates one helper per `run()`, calls `helper.observe(...)` on every successful chain-data read, and calls `helper.mtm_price(...)` whenever chain data is missing. Three call sites (`_positions_market_value`, `_positions_snapshot`, `_positions_for_context`) thread `ps.quantity` through so the helper knows direction. Session schema gains `mtm_realism`; it flows session → payload → sweep/walk-forward → BacktestRun row → runner → `BacktestEngine.run(mtm_realism=...)` → `helper.mtm_price(alpha=...)`.

**Tech Stack:** Python 3.x, SQLAlchemy 2.x (`Mapped`/`mapped_column`), Pydantic v2 (`model_validator(mode="after")`), Alembic (`batch_alter_table`), `scipy.stats.norm` (available as transitive dep via `pandas-market-calendars`), pytest, Click (CLI), React/TypeScript + Vitest (dashboard).

---

## File Structure

**Created:**
- `coordinator/services/options_mtm.py` — the helper module + BS function. One responsibility: produce a conservative MTM estimate for an option given parsed OCC, underlying, and direction.
- `tests/coordinator/services/test_options_mtm.py` — unit tests for the helper.
- `coordinator/database/migrations/versions/<rev>_add_mtm_realism.py` — additive Alembic migration adding `mtm_realism` to `optimization_sessions` and `backtest_runs`.

**Modified:**
- `coordinator/services/backtest_engine_v2.py` — instantiate helper, rewire `_lookup_option_mtm_price` to call observe/mtm_price, thread `position_quantity` through three call sites, drop cost-basis fallback.
- `tests/coordinator/services/test_backtest_engine.py` — add bug-regression test for the chain-data-gap short-call scenario.
- `coordinator/database/models.py:458` (`OptimizationSession`) and `:516` (`BacktestRun`) — add `mtm_realism` column to both.
- `coordinator/api/routes/research.py:44` (`CreateSessionRequest`) and `:76` (`SessionResponse`) — add `mtm_realism: float = 0.0` with Pydantic range validator.
- `coordinator/services/research_job_manager.py:157` (`_dispatch_sweep`) and `:182` (`_dispatch_walk_forward`) — read `payload["mtm_realism"]` and pass to `run_sweep`/`run_walk_forward`.
- `coordinator/services/validation/sweep.py:175` (`run_sweep`), `:116` (`_run_one_backtest`), `:140` (`run_walk_forward`), `:89` (`_run_oos_backtest`) — add `mtm_realism` parameter and persist to BacktestRun rows.
- `coordinator/services/backtest_runner.py:209` (BacktestRunner.run) — read `run.mtm_realism` and pass to `BacktestEngine.run(mtm_realism=...)`.
- `sdk/cli/commands/research.py:64` (`session_create`) — add `--mtm-realism FLOAT` flag and include in API payload.
- `dashboard/src/components/ExperimentScopeFields.tsx` — add numeric input for `mtm_realism` (range 0–1, step 0.05, default 0.0).
- `tests/coordinator/api/test_research_routes.py` — validation tests for `mtm_realism`.
- `tests/sdk/cli/test_research_cli.py` — CLI flag passes through to request body.
- `dashboard/src/components/ExperimentScopeFields.test.tsx` — input rendered, change emits new value.

---

## Conventions

- **Test layout**: Unit tests under `tests/coordinator/services/test_options_mtm.py` use the patterns shown in `test_backtest_engine.py` — explicit `pytest.approx`, no fixtures unless needed.
- **OCC parser**: `from coordinator.services.chain_builder import parse_occ_symbol`. Returns `{"underlying": str, "expiration": "YYYY-MM-DD" str, "option_type": "call"|"put", "strike": float, "raw_symbol": str}`. Note: `option_type` is `"call"`/`"put"`, not `"C"`/`"P"`.
- **scipy.stats**: Already imported elsewhere (`coordinator/services/options_math.py:64`). Safe to use directly.
- **Migration naming**: `<alembic_rev>_<snake_case_description>.py` — let Alembic generate the rev hash via `alembic revision -m "add mtm_realism"`.
- **Commits**: After each task's tests pass, commit with `feat:`/`refactor:`/`test:` prefix and a one-line summary. Co-Authored-By line per project convention.

---

## Task 1: Create `options_mtm.py` module skeleton with dataclasses + constants

**Files:**
- Create: `coordinator/services/options_mtm.py`
- Create: `tests/coordinator/services/test_options_mtm.py`

- [ ] **Step 1: Write the module skeleton**

```python
# coordinator/services/options_mtm.py
"""Conservative options-MTM helper for backtest valuation.

Used when live chain mid is unavailable. Produces a Black-Scholes
estimate with a direction-aware envelope so no algorithm can exploit
chain-data sparseness to mis-size positions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
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
```

- [ ] **Step 2: Write a smoke test that imports the constants**

```python
# tests/coordinator/services/test_options_mtm.py
"""Tests for the conservative options MTM helper."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from coordinator.services.options_mtm import (
    FALLBACK_SIGMA,
    RISK_FREE_RATE,
    _IVCacheEntry,
    _MidCacheEntry,
)


def test_constants_have_expected_values():
    assert RISK_FREE_RATE == 0.045
    assert FALLBACK_SIGMA == 0.40


def test_iv_cache_entry_holds_sim_time_and_iv():
    entry = _IVCacheEntry(
        sim_time=datetime(2024, 1, 1, tzinfo=timezone.utc), iv=0.25
    )
    assert entry.iv == 0.25
    assert entry.sim_time.year == 2024


def test_mid_cache_entry_holds_sim_time_and_mid():
    entry = _MidCacheEntry(
        sim_time=datetime(2024, 1, 1, tzinfo=timezone.utc), mid=1.23
    )
    assert entry.mid == pytest.approx(1.23)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add coordinator/services/options_mtm.py tests/coordinator/services/test_options_mtm.py
git commit -m "$(cat <<'EOF'
feat(options-mtm): scaffold helper module with constants and cache dataclasses

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement `black_scholes_price`

**Files:**
- Modify: `coordinator/services/options_mtm.py`
- Modify: `tests/coordinator/services/test_options_mtm.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/services/test_options_mtm.py`:

```python
from coordinator.services.options_mtm import black_scholes_price


def test_bs_atm_call_one_year_textbook_value():
    # ATM call, S=100, K=100, T=1, r=0.05, sigma=0.20
    # Textbook value ≈ 10.4506 (Hull, Options Futures, Table 13.1)
    price = black_scholes_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type="call"
    )
    assert price == pytest.approx(10.4506, rel=0.001)


def test_bs_atm_put_one_year_textbook_value():
    # Same ATM, put. Put-call parity: P = C - S + K*exp(-rT)
    # = 10.4506 - 100 + 100*exp(-0.05) ≈ 5.5735
    price = black_scholes_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type="put"
    )
    assert price == pytest.approx(5.5735, rel=0.001)


def test_bs_itm_call_intrinsic_floor():
    # Deep ITM call near expiry collapses to intrinsic
    price = black_scholes_price(
        S=150.0, K=100.0, T=0.001, r=0.05, sigma=0.20, option_type="call"
    )
    assert price == pytest.approx(50.0, abs=0.1)


def test_bs_otm_put_zero_at_expiry():
    # OTM put at T=0 → intrinsic = max(K-S, 0) = 0
    price = black_scholes_price(
        S=110.0, K=100.0, T=0.0, r=0.05, sigma=0.20, option_type="put"
    )
    assert price == pytest.approx(0.0, abs=1e-6)


def test_bs_itm_put_at_expiry_returns_intrinsic():
    # ITM put at T=0 → intrinsic = K - S = 10
    price = black_scholes_price(
        S=90.0, K=100.0, T=0.0, r=0.05, sigma=0.20, option_type="put"
    )
    assert price == pytest.approx(10.0, abs=1e-6)


def test_bs_accepts_short_form_option_type():
    # Helper accepts "C"/"P" as well as "call"/"put"
    p1 = black_scholes_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type="C"
    )
    p2 = black_scholes_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type="call"
    )
    assert p1 == pytest.approx(p2)


def test_bs_negative_T_treated_as_expired():
    # Sim-time past expiration → treat T=0, return intrinsic
    price = black_scholes_price(
        S=110.0, K=100.0, T=-0.01, r=0.05, sigma=0.20, option_type="call"
    )
    assert price == pytest.approx(10.0, abs=1e-6)


def test_bs_zero_sigma_returns_discounted_intrinsic():
    # σ=0 → no time value; for an ITM call, BS reduces to S - K*exp(-rT)
    price = black_scholes_price(
        S=110.0, K=100.0, T=1.0, r=0.05, sigma=0.0, option_type="call"
    )
    expected = 110.0 - 100.0 * math.exp(-0.05)
    import math as _math  # noqa
    assert price == pytest.approx(expected, abs=1e-6)
```

Add `import math` near the top of the test file if not already present.

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v`
Expected: 8 new failures with `ImportError: cannot import name 'black_scholes_price'`.

- [ ] **Step 3: Implement `black_scholes_price`**

Append to `coordinator/services/options_mtm.py`:

```python
def black_scholes_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,
) -> float:
    """Black-Scholes price for a European option.

    Args:
        S: underlying price
        K: strike
        T: time to expiry in years (≤ 0 returns intrinsic)
        r: risk-free rate
        sigma: implied volatility (≤ 0 returns discounted intrinsic)
        option_type: "call"/"C" or "put"/"P" (case-insensitive)

    Returns:
        Theoretical option price ≥ 0.
    """
    is_call = option_type[0].upper() == "C"

    # Expiration / past-expiration: return intrinsic
    if T <= 0:
        if is_call:
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    # Zero vol: discounted intrinsic (the deterministic value)
    if sigma <= 0:
        if is_call:
            return max(S - K * math.exp(-r * T), 0.0)
        return max(K * math.exp(-r * T) - S, 0.0)

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    if is_call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v`
Expected: All passing.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/options_mtm.py tests/coordinator/services/test_options_mtm.py
git commit -m "$(cat <<'EOF'
feat(options-mtm): add black_scholes_price with European-style pricing

- Accepts both 'call'/'put' and 'C'/'P' option_type tokens
- Handles T ≤ 0 (expired) by returning intrinsic
- Handles sigma == 0 by returning discounted intrinsic
- Validated against Hull Table 13.1 textbook ATM call/put values

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Implement `OptionsMTMHelper.__init__` and `observe`

**Files:**
- Modify: `coordinator/services/options_mtm.py`
- Modify: `tests/coordinator/services/test_options_mtm.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/services/test_options_mtm.py`:

```python
from coordinator.services.options_mtm import OptionsMTMHelper


def test_helper_initial_state_is_empty():
    h = OptionsMTMHelper()
    assert h._iv_by_symbol == {}
    assert h._iv_by_expiry == {}
    assert h._iv_by_underlying == {}
    assert h._mid_by_symbol == {}


def test_helper_observe_populates_all_four_caches():
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe(
        symbol="O:SPY240621C00500000",
        mid=12.5,
        iv=0.22,
        sim_time=sim,
        underlying="SPY",
        expiration_str="2024-06-21",
    )
    assert h._iv_by_symbol["O:SPY240621C00500000"].iv == pytest.approx(0.22)
    assert h._iv_by_symbol["O:SPY240621C00500000"].sim_time == sim
    assert h._iv_by_expiry[("SPY", "2024-06-21")].iv == pytest.approx(0.22)
    assert h._iv_by_underlying["SPY"].iv == pytest.approx(0.22)
    assert h._mid_by_symbol["O:SPY240621C00500000"].mid == pytest.approx(12.5)
    assert h._mid_by_symbol["O:SPY240621C00500000"].sim_time == sim


def test_helper_observe_overwrites_with_newer_sim_time():
    h = OptionsMTMHelper()
    older = datetime(2024, 6, 1, tzinfo=timezone.utc)
    newer = datetime(2024, 6, 2, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00500000", 10.0, 0.20, older, "SPY", "2024-06-21")
    h.observe("O:SPY240621C00500000", 11.0, 0.21, newer, "SPY", "2024-06-21")
    assert h._iv_by_symbol["O:SPY240621C00500000"].iv == pytest.approx(0.21)
    assert h._mid_by_symbol["O:SPY240621C00500000"].mid == pytest.approx(11.0)
    # And the (underlying, expiry) cache should also have the newer entry
    assert h._iv_by_expiry[("SPY", "2024-06-21")].iv == pytest.approx(0.21)


def test_helper_observe_ignores_non_positive_iv():
    # Some chain rows have iv=0 (data quality). Don't poison the caches.
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00500000", 10.0, 0.0, sim, "SPY", "2024-06-21")
    assert "O:SPY240621C00500000" not in h._iv_by_symbol
    assert ("SPY", "2024-06-21") not in h._iv_by_expiry
    assert "SPY" not in h._iv_by_underlying
    # Mid should still be cached (mid > 0 is independent signal)
    assert h._mid_by_symbol["O:SPY240621C00500000"].mid == pytest.approx(10.0)


def test_helper_observe_ignores_non_positive_mid():
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00500000", 0.0, 0.20, sim, "SPY", "2024-06-21")
    assert "O:SPY240621C00500000" not in h._mid_by_symbol
    # IV is positive, so its caches DO get populated
    assert h._iv_by_symbol["O:SPY240621C00500000"].iv == pytest.approx(0.20)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v`
Expected: New tests fail with `ImportError: cannot import name 'OptionsMTMHelper'`.

- [ ] **Step 3: Implement `OptionsMTMHelper` `__init__` + `observe`**

Append to `coordinator/services/options_mtm.py`:

```python
class OptionsMTMHelper:
    """Per-run helper: caches IVs/mids from live chain reads and produces
    a conservative MTM estimate when chain data is unavailable.

    Construct one per BacktestEngine.run(). No persistence; rebuilt each
    run.
    """

    def __init__(self) -> None:
        # Tier 1: exact OCC symbol → most recent IV observation
        self._iv_by_symbol: dict[str, _IVCacheEntry] = {}
        # Tier 2: (underlying, expiration ISO date) → most recent IV
        self._iv_by_expiry: dict[tuple[str, str], _IVCacheEntry] = {}
        # Tier 3: underlying → most recent ATM-ish IV (any contract seen)
        self._iv_by_underlying: dict[str, _IVCacheEntry] = {}
        # Last-known mid per OCC symbol
        self._mid_by_symbol: dict[str, _MidCacheEntry] = {}

    def observe(
        self,
        symbol: str,
        mid: float,
        iv: float,
        sim_time: datetime,
        underlying: str,
        expiration_str: str,
    ) -> None:
        """Populate caches from a successful live chain read.

        Non-positive iv or mid is dropped to avoid poisoning the cache
        with bad data — but the two are independent (a row with good mid
        and bad iv still updates the mid cache).
        """
        if mid > 0:
            self._mid_by_symbol[symbol] = _MidCacheEntry(sim_time=sim_time, mid=mid)
        if iv > 0:
            entry = _IVCacheEntry(sim_time=sim_time, iv=iv)
            self._iv_by_symbol[symbol] = entry
            self._iv_by_expiry[(underlying, expiration_str)] = entry
            self._iv_by_underlying[underlying] = entry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v`
Expected: All passing (12 total at this point).

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/options_mtm.py tests/coordinator/services/test_options_mtm.py
git commit -m "$(cat <<'EOF'
feat(options-mtm): add OptionsMTMHelper with three-tier IV cache and mid cache

observe() drops non-positive iv/mid independently so a row with bad iv
but good mid (or vice versa) still updates the surviving cache.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement `_resolve_iv` helper method

**Files:**
- Modify: `coordinator/services/options_mtm.py`
- Modify: `tests/coordinator/services/test_options_mtm.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/services/test_options_mtm.py`:

```python
def test_resolve_iv_prefers_exact_symbol():
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00500000", 10.0, 0.30, sim, "SPY", "2024-06-21")
    h.observe("O:SPY240621C00600000", 1.0, 0.25, sim, "SPY", "2024-06-21")
    iv = h._resolve_iv(
        symbol="O:SPY240621C00500000", underlying="SPY",
        expiration_str="2024-06-21",
    )
    assert iv == pytest.approx(0.30)


def test_resolve_iv_falls_back_to_expiry_tier():
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00600000", 1.0, 0.25, sim, "SPY", "2024-06-21")
    iv = h._resolve_iv(
        symbol="O:SPY240621C00500000",
        underlying="SPY",
        expiration_str="2024-06-21",
    )
    assert iv == pytest.approx(0.25)


def test_resolve_iv_falls_back_to_underlying_tier():
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    # Different expiry's IV is the only thing in cache
    h.observe("O:SPY240920C00600000", 5.0, 0.18, sim, "SPY", "2024-09-20")
    iv = h._resolve_iv(
        symbol="O:SPY240621C00500000",
        underlying="SPY",
        expiration_str="2024-06-21",
    )
    assert iv == pytest.approx(0.18)


def test_resolve_iv_falls_back_to_constant_when_cache_cold():
    h = OptionsMTMHelper()
    iv = h._resolve_iv(
        symbol="O:SPY240621C00500000",
        underlying="SPY",
        expiration_str="2024-06-21",
    )
    assert iv == pytest.approx(FALLBACK_SIGMA)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v -k resolve_iv`
Expected: 4 failures with `AttributeError: 'OptionsMTMHelper' object has no attribute '_resolve_iv'`.

- [ ] **Step 3: Implement `_resolve_iv`**

Append to the `OptionsMTMHelper` class:

```python
    def _resolve_iv(
        self, symbol: str, underlying: str, expiration_str: str,
    ) -> float:
        """Walk the three-tier cache; return FALLBACK_SIGMA on full miss."""
        entry = self._iv_by_symbol.get(symbol)
        if entry is not None:
            return entry.iv
        entry = self._iv_by_expiry.get((underlying, expiration_str))
        if entry is not None:
            return entry.iv
        entry = self._iv_by_underlying.get(underlying)
        if entry is not None:
            return entry.iv
        return FALLBACK_SIGMA
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v`
Expected: All passing.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/options_mtm.py tests/coordinator/services/test_options_mtm.py
git commit -m "$(cat <<'EOF'
feat(options-mtm): add three-tier IV resolution with constant-sigma fallback

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Implement `_apply_envelope` (direction-aware + alpha lerp)

**Files:**
- Modify: `coordinator/services/options_mtm.py`
- Modify: `tests/coordinator/services/test_options_mtm.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/services/test_options_mtm.py`:

```python
def test_envelope_long_alpha_0_caps_at_last_mid():
    # Long, BS > last_mid → conservative = min(BS, last_mid) = last_mid
    # alpha=0 → mtm = last_mid (full cap)
    h = OptionsMTMHelper()
    mtm = h._apply_envelope(
        bs_or_intrinsic=5.0, intrinsic=2.0, last_known_mid=3.0,
        position_quantity=10.0, alpha=0.0,
    )
    assert mtm == pytest.approx(3.0)


def test_envelope_long_alpha_1_returns_bs_unbiased():
    h = OptionsMTMHelper()
    mtm = h._apply_envelope(
        bs_or_intrinsic=5.0, intrinsic=2.0, last_known_mid=3.0,
        position_quantity=10.0, alpha=1.0,
    )
    assert mtm == pytest.approx(5.0)


def test_envelope_long_alpha_half_lerps():
    h = OptionsMTMHelper()
    # conservative = 3.0, bs = 5.0 → 0.5 * 5 + 0.5 * 3 = 4.0
    mtm = h._apply_envelope(
        bs_or_intrinsic=5.0, intrinsic=2.0, last_known_mid=3.0,
        position_quantity=10.0, alpha=0.5,
    )
    assert mtm == pytest.approx(4.0)


def test_envelope_long_no_last_mid_passes_bs_through_at_alpha_0():
    # min(bs, +inf) = bs → no cap applied → mtm = bs regardless of alpha
    h = OptionsMTMHelper()
    mtm = h._apply_envelope(
        bs_or_intrinsic=5.0, intrinsic=2.0, last_known_mid=None,
        position_quantity=10.0, alpha=0.0,
    )
    assert mtm == pytest.approx(5.0)


def test_envelope_short_alpha_0_floors_at_max():
    # Short, BS < intrinsic and BS < last_mid → floor at max(BS, intrinsic, last_mid)
    # BS=0.5, intrinsic=2.0, last_mid=0.8 → max = 2.0
    # alpha=0 → mtm = 2.0
    h = OptionsMTMHelper()
    mtm = h._apply_envelope(
        bs_or_intrinsic=0.5, intrinsic=2.0, last_known_mid=0.8,
        position_quantity=-5.0, alpha=0.0,
    )
    assert mtm == pytest.approx(2.0)


def test_envelope_short_alpha_1_returns_bs_unbiased():
    h = OptionsMTMHelper()
    mtm = h._apply_envelope(
        bs_or_intrinsic=0.5, intrinsic=2.0, last_known_mid=0.8,
        position_quantity=-5.0, alpha=1.0,
    )
    assert mtm == pytest.approx(0.5)


def test_envelope_short_alpha_half_lerps():
    h = OptionsMTMHelper()
    # conservative = 2.0, bs = 0.5 → 0.5*0.5 + 0.5*2.0 = 1.25
    mtm = h._apply_envelope(
        bs_or_intrinsic=0.5, intrinsic=2.0, last_known_mid=0.8,
        position_quantity=-5.0, alpha=0.5,
    )
    assert mtm == pytest.approx(1.25)


def test_envelope_short_no_last_mid_uses_zero_floor():
    # last_known_mid=None → floor = max(bs, intrinsic, 0)
    h = OptionsMTMHelper()
    # BS=0.5, intrinsic=0 → max=0.5; alpha=0 → mtm = 0.5
    mtm = h._apply_envelope(
        bs_or_intrinsic=0.5, intrinsic=0.0, last_known_mid=None,
        position_quantity=-5.0, alpha=0.0,
    )
    assert mtm == pytest.approx(0.5)


def test_envelope_zero_quantity_bypasses_envelope():
    # No position direction → unbiased return regardless of alpha
    h = OptionsMTMHelper()
    mtm = h._apply_envelope(
        bs_or_intrinsic=5.0, intrinsic=2.0, last_known_mid=3.0,
        position_quantity=0.0, alpha=0.0,
    )
    assert mtm == pytest.approx(5.0)


def test_envelope_never_returns_negative():
    # Even with bad inputs, mtm clamps at 0
    h = OptionsMTMHelper()
    mtm = h._apply_envelope(
        bs_or_intrinsic=-0.1, intrinsic=0.0, last_known_mid=None,
        position_quantity=10.0, alpha=1.0,
    )
    assert mtm >= 0
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v -k envelope`
Expected: 10 failures with `AttributeError: ... _apply_envelope`.

- [ ] **Step 3: Implement `_apply_envelope`**

Append to the `OptionsMTMHelper` class:

```python
    @staticmethod
    def _apply_envelope(
        bs_or_intrinsic: float,
        intrinsic: float,
        last_known_mid: Optional[float],
        position_quantity: float,
        alpha: float,
    ) -> float:
        """Apply the direction-aware envelope, lerped by alpha ∈ [0, 1].

        alpha=0.0: full envelope (most conservative for the position).
        alpha=1.0: no envelope (unbiased BS).
        alpha in between: linear interpolation.

        position_quantity == 0 bypasses the envelope entirely.
        """
        if position_quantity == 0:
            return max(bs_or_intrinsic, 0.0)

        # Long: cap by last_known_mid; None → no cap
        if position_quantity > 0:
            if last_known_mid is None:
                conservative = bs_or_intrinsic
            else:
                conservative = min(bs_or_intrinsic, last_known_mid)
        else:
            # Short: floor at max(BS, intrinsic, last_known_mid or 0)
            floor_components = [bs_or_intrinsic, intrinsic]
            if last_known_mid is not None:
                floor_components.append(last_known_mid)
            else:
                floor_components.append(0.0)
            conservative = max(floor_components)

        mtm = alpha * bs_or_intrinsic + (1.0 - alpha) * conservative
        return max(mtm, 0.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v`
Expected: All passing.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/options_mtm.py tests/coordinator/services/test_options_mtm.py
git commit -m "$(cat <<'EOF'
feat(options-mtm): add direction-aware envelope with alpha lerp

Long positions cap at last_known_mid; short positions floor at
max(BS, intrinsic, last_known_mid or 0). Quantity == 0 bypasses
the envelope. alpha lerps between full envelope (0) and unbiased
BS (1).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Implement `mtm_price` (Layers 2/3 + envelope integration)

**Files:**
- Modify: `coordinator/services/options_mtm.py`
- Modify: `tests/coordinator/services/test_options_mtm.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/services/test_options_mtm.py`:

```python
def test_mtm_price_uses_layer_2_bs_with_cached_iv():
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00500000", 12.0, 0.20, sim,
              "SPY", "2024-06-21")
    occ = {
        "underlying": "SPY",
        "expiration": "2024-06-21",
        "option_type": "call",
        "strike": 500.0,
    }
    # No position (qty=0) → envelope bypassed → pure BS
    price = h.mtm_price(
        symbol="O:SPY240621C00500000",
        sim_time=datetime(2024, 6, 7, tzinfo=timezone.utc),
        underlying_price=505.0,
        position_quantity=0.0,
        occ_parsed=occ,
        alpha=0.0,
    )
    # BS(S=505, K=500, T=14/365, r=0.045, sigma=0.20, call) ≈ 7.4
    assert 5.0 < price < 12.0


def test_mtm_price_uses_constant_sigma_when_cache_cold():
    h = OptionsMTMHelper()
    occ = {
        "underlying": "AAPL",
        "expiration": "2024-09-20",
        "option_type": "call",
        "strike": 200.0,
    }
    price = h.mtm_price(
        symbol="O:AAPL240920C00200000",
        sim_time=datetime(2024, 6, 1, tzinfo=timezone.utc),
        underlying_price=200.0,
        position_quantity=0.0,
        occ_parsed=occ,
        alpha=0.0,
    )
    # ATM call ~111 days out, sigma=0.40 → BS ≈ 14.6 (large because sigma is high)
    assert 10.0 < price < 25.0


def test_mtm_price_returns_intrinsic_when_expired():
    h = OptionsMTMHelper()
    occ = {
        "underlying": "SPY",
        "expiration": "2024-06-21",
        "option_type": "call",
        "strike": 500.0,
    }
    # Sim time AFTER expiry
    price = h.mtm_price(
        symbol="O:SPY240621C00500000",
        sim_time=datetime(2024, 7, 1, tzinfo=timezone.utc),
        underlying_price=510.0,
        position_quantity=0.0,
        occ_parsed=occ,
        alpha=1.0,
    )
    # Intrinsic = 510 - 500 = 10
    assert price == pytest.approx(10.0, abs=0.01)


def test_mtm_price_short_envelope_uses_intrinsic_when_bs_below():
    h = OptionsMTMHelper()
    occ = {
        "underlying": "SPY",
        "expiration": "2024-06-21",
        "option_type": "call",
        "strike": 500.0,
    }
    # ITM call (S=510, K=500), short position, alpha=0
    # BS will probably be ~10-11; intrinsic = 10
    # max(BS, intrinsic, last_mid or 0) — last_mid not cached → 0
    # So conservative = max(BS, 10, 0) ≈ BS (since BS >= intrinsic for ITM)
    price = h.mtm_price(
        symbol="O:SPY240621C00500000",
        sim_time=datetime(2024, 6, 7, tzinfo=timezone.utc),
        underlying_price=510.0,
        position_quantity=-100.0,
        occ_parsed=occ,
        alpha=0.0,
    )
    # Must be at least intrinsic
    assert price >= 10.0


def test_mtm_price_handles_put_option_type():
    h = OptionsMTMHelper()
    occ = {
        "underlying": "SPY",
        "expiration": "2024-06-21",
        "option_type": "put",
        "strike": 500.0,
    }
    # ITM put: S=490, K=500
    price = h.mtm_price(
        symbol="O:SPY240621P00500000",
        sim_time=datetime(2024, 6, 7, tzinfo=timezone.utc),
        underlying_price=490.0,
        position_quantity=0.0,
        occ_parsed=occ,
        alpha=0.0,
    )
    # BS put ITM by $10 with ~2 weeks left → ≥ 10 (intrinsic)
    assert price >= 9.0  # near-intrinsic with small discount


def test_mtm_price_intrinsic_path_when_underlying_unavailable():
    # Per spec, when caller can't supply underlying_price, helper returns
    # intrinsic via underlying_price=None signal. But in our design the
    # engine always supplies S (or skips the call). We still test the
    # explicit "expired" path which exercises the intrinsic-only return.
    h = OptionsMTMHelper()
    occ = {
        "underlying": "SPY",
        "expiration": "2024-06-21",
        "option_type": "put",
        "strike": 500.0,
    }
    # Past expiry → Layer 3
    price = h.mtm_price(
        symbol="O:SPY240621P00500000",
        sim_time=datetime(2024, 7, 1, tzinfo=timezone.utc),
        underlying_price=480.0,
        position_quantity=0.0,
        occ_parsed=occ,
        alpha=0.0,
    )
    # Intrinsic = max(500-480, 0) = 20
    assert price == pytest.approx(20.0, abs=0.01)


def test_mtm_price_long_envelope_caps_at_last_mid():
    h = OptionsMTMHelper()
    sim_open = datetime(2024, 6, 1, tzinfo=timezone.utc)
    # Open observation: last_mid = 1.0, iv = 0.20
    h.observe("O:SPY240621C00500000", 1.0, 0.20, sim_open,
              "SPY", "2024-06-21")
    occ = {
        "underlying": "SPY",
        "expiration": "2024-06-21",
        "option_type": "call",
        "strike": 500.0,
    }
    # Now BS would say ~7 (S=505) but we have a long position and
    # last_known_mid = 1.0 → cap at 1.0 at alpha=0
    price = h.mtm_price(
        symbol="O:SPY240621C00500000",
        sim_time=datetime(2024, 6, 10, tzinfo=timezone.utc),
        underlying_price=505.0,
        position_quantity=10.0,
        occ_parsed=occ,
        alpha=0.0,
    )
    assert price == pytest.approx(1.0, abs=0.01)


def test_mtm_price_short_envelope_floors_at_last_mid():
    h = OptionsMTMHelper()
    sim_open = datetime(2024, 6, 1, tzinfo=timezone.utc)
    # Open: last_mid = 5.0
    h.observe("O:SPY240621C00500000", 5.0, 0.20, sim_open,
              "SPY", "2024-06-21")
    occ = {
        "underlying": "SPY",
        "expiration": "2024-06-21",
        "option_type": "call",
        "strike": 500.0,
    }
    # BS for OTM call (S=480) ≈ low; but last_mid = 5.0 → floor at 5.0
    price = h.mtm_price(
        symbol="O:SPY240621C00500000",
        sim_time=datetime(2024, 6, 10, tzinfo=timezone.utc),
        underlying_price=480.0,
        position_quantity=-10.0,
        occ_parsed=occ,
        alpha=0.0,
    )
    assert price >= 5.0
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v -k mtm_price`
Expected: 8 failures with `AttributeError: 'OptionsMTMHelper' object has no attribute 'mtm_price'`.

- [ ] **Step 3: Implement `mtm_price`**

Append to the `OptionsMTMHelper` class:

```python
    def mtm_price(
        self,
        symbol: str,
        sim_time: datetime,
        underlying_price: float,
        position_quantity: float,
        occ_parsed: dict,
        alpha: float = 0.0,
    ) -> float:
        """Conservative MTM price for a single option.

        Args:
            symbol: OCC symbol (e.g. "O:SPY240621C00500000")
            sim_time: current sim datetime (UTC)
            underlying_price: most recent underlying close at sim_time
            position_quantity: signed share count (>0 long, <0 short)
            occ_parsed: dict with keys 'underlying' (str),
              'expiration' (ISO 'YYYY-MM-DD' str), 'option_type'
              ('call'/'put' or 'C'/'P'), 'strike' (float)
            alpha: session mtm_realism in [0, 1]. 0 = full envelope,
              1 = unbiased BS.

        Returns:
            Non-negative MTM price per share/contract unit.
        """
        underlying = occ_parsed["underlying"]
        expiration_str = occ_parsed["expiration"]
        option_type = occ_parsed["option_type"]
        K = float(occ_parsed["strike"])

        # Parse expiration → date → days
        expiration_date = date.fromisoformat(expiration_str)
        sim_date = sim_time.date() if hasattr(sim_time, "date") else sim_time
        days_to_expiry = (expiration_date - sim_date).days
        T = max(days_to_expiry, 0) / 365.0

        # Layer 3: expired → intrinsic only
        is_call = option_type[0].upper() == "C"
        if is_call:
            intrinsic = max(underlying_price - K, 0.0)
        else:
            intrinsic = max(K - underlying_price, 0.0)

        if days_to_expiry <= 0:
            bs_or_intrinsic = intrinsic
        else:
            # Layer 2: Black-Scholes with carry-forward IV
            sigma = self._resolve_iv(symbol, underlying, expiration_str)
            bs_or_intrinsic = black_scholes_price(
                S=underlying_price, K=K, T=T, r=RISK_FREE_RATE,
                sigma=sigma, option_type=option_type,
            )

        last_known_mid_entry = self._mid_by_symbol.get(symbol)
        last_known_mid = (
            last_known_mid_entry.mid if last_known_mid_entry is not None else None
        )

        return self._apply_envelope(
            bs_or_intrinsic=bs_or_intrinsic,
            intrinsic=intrinsic,
            last_known_mid=last_known_mid,
            position_quantity=position_quantity,
            alpha=alpha,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/test_options_mtm.py -v`
Expected: All passing.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/options_mtm.py tests/coordinator/services/test_options_mtm.py
git commit -m "$(cat <<'EOF'
feat(options-mtm): implement mtm_price with layered BS/intrinsic + envelope

Layer 2 uses three-tier IV resolution; Layer 3 returns intrinsic
when expired. Envelope is applied on top, lerped by alpha.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Rewire `BacktestEngine.run` + `_lookup_option_mtm_price`

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py:106-124` (run signature)
- Modify: `coordinator/services/backtest_engine_v2.py:913-930` (`_lookup_option_mtm_price`)
- Modify: `coordinator/services/backtest_engine_v2.py:932-947` (`_positions_market_value`)
- Modify: `coordinator/services/backtest_engine_v2.py:949-968` (`_positions_snapshot`)
- Modify: `coordinator/services/backtest_engine_v2.py:~1000-1020` (`_positions_for_context`)
- Test: `tests/coordinator/services/test_backtest_engine.py`

- [ ] **Step 1: Find the actual current line numbers**

Run: `grep -n "def _lookup_option_mtm_price\|def _positions_market_value\|def _positions_snapshot\|def _positions_for_context\|def run(" coordinator/services/backtest_engine_v2.py`

Note the four method line numbers; they may have drifted from the plan's numbers.

- [ ] **Step 2: Write the failing regression test FIRST**

Add this to `tests/coordinator/services/test_backtest_engine.py` (after existing tests):

```python
def test_chain_data_gap_short_option_mtm_does_not_collapse_to_initial_cash():
    """Bug regression: 2026-06-04 equity-curve-MTM design.

    A short option held through bars with no chain data must show
    negative position_mv (not be marked at cost basis). Equity must
    reflect the true risk.
    """
    from coordinator.database.models import AssetType  # noqa: F401  (registry side-effect)

    # 5-bar SPY clock at $580 → $600 (calls become more ITM over time)
    clock = _bars(
        "2024-12-01", 5,
        opens=[580, 585, 590, 595, 600],
        highs=[582, 587, 592, 597, 602],
        lows=[578, 583, 588, 593, 598],
        closes=[580, 585, 590, 595, 600],
    )
    # NO option chain cache populated → forces Layer 2/3 fallback
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={
            ("O:SPY241220C00586000",): _make_position(
                quantity=-100.0,      # short 100 contracts
                avg_price=0.02,       # opened at $0.02 (the bug scenario)
                asset_type="options",
            ),
        },
        cash=10_000.0 + (100 * 0.02 * 100),   # initial + premium received
    )
    obs = RecordingObserver()
    engine = BacktestEngine()

    class _DoNothing:
        def on_init(self, ctx): pass
        def get_signals(self, ctx): return []

    engine.run(
        algorithm=_DoNothing(),
        ctx=ctx,
        clock_series=clock,
        clock_timeframe="1day",
        clock_source="polygon",
        clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0.0),
        buy_fees=[], sell_fees=[],
        initial_cash=10_200.0,
        observer=obs,
        cancel_token=CancelToken(),
        mtm_realism=0.0,
    )

    # By final bar (SPY=$600), the short $586 call is at least $14 intrinsic.
    # position_mv = -100 * 14 * 100 = -$140,000.
    # equity = cash (~$10,200) + position_mv (≤ -140,000) → strongly negative.
    final_pv = obs.equity[-1]["pv"]
    assert final_pv < -50_000, (
        f"Expected deeply-negative equity for short call held through chain "
        f"gap; got pv={final_pv}. The cost-basis fallback would give "
        f"pv ≈ initial_cash = $10,200."
    )
    # Confirm we didn't accidentally collapse to initial cash
    assert final_pv < 5_000
```

You will need a helper `_make_position(...)` if one doesn't already exist. If `tests/coordinator/services/test_backtest_engine.py` has its own way of seeding positions, follow that pattern instead. To check, run: `grep -n "_make_position\|def _seed\|Position(" tests/coordinator/services/test_backtest_engine.py` — if there is a fixture/factory already, use it. If not, add this helper near the top of the test file:

```python
def _make_position(quantity, avg_price, asset_type="options"):
    """Build a fake position dict matching the engine's internal _PositionState shape."""
    from coordinator.services.backtest_engine_v2 import _PositionState
    return _PositionState(
        quantity=quantity, avg_price=avg_price, asset_type=asset_type,
    )
```

- [ ] **Step 3: Run the failing test to confirm it fails for the right reason**

Run: `pytest tests/coordinator/services/test_backtest_engine.py::test_chain_data_gap_short_option_mtm_does_not_collapse_to_initial_cash -v`

Expected: FAIL — either with a `TypeError` (engine.run doesn't accept `mtm_realism`) OR with the assertion error (`final_pv ≈ 10,200`, far above `-50,000`). Either failure mode confirms the test is exercising the bug path.

- [ ] **Step 4: Update `BacktestEngine.run` signature**

Open `coordinator/services/backtest_engine_v2.py` and modify the `BacktestEngine.run` signature (currently around lines 106-124). Change FROM:

```python
def run(
    self,
    *,
    algorithm,
    ctx: BacktestTickContext,
    clock_series: pd.DataFrame,
    clock_timeframe: str,
    clock_source: str,
    clock_symbol: str,
    slippage: SlippageModel,
    buy_fees: list[TradingFee],
    sell_fees: list[TradingFee],
    initial_cash: float,
    observer: EngineObserver,
    cancel_token: CancelToken,
    progress_callback: Optional[Callable[[float], None]] = None,
    rng_seed: int = 12345,
    config: Optional[dict] = None,
) -> None:
```

TO:

```python
def run(
    self,
    *,
    algorithm,
    ctx: BacktestTickContext,
    clock_series: pd.DataFrame,
    clock_timeframe: str,
    clock_source: str,
    clock_symbol: str,
    slippage: SlippageModel,
    buy_fees: list[TradingFee],
    sell_fees: list[TradingFee],
    initial_cash: float,
    observer: EngineObserver,
    cancel_token: CancelToken,
    progress_callback: Optional[Callable[[float], None]] = None,
    rng_seed: int = 12345,
    config: Optional[dict] = None,
    mtm_realism: float = 0.0,
) -> None:
```

Then, at the top of the run body (right after the docstring / pre-validation, before the main loop), add:

```python
        from coordinator.services.options_mtm import OptionsMTMHelper
        if not (0.0 <= mtm_realism <= 1.0):
            raise ValueError(
                f"mtm_realism must be in [0.0, 1.0]; got {mtm_realism!r}"
            )
        self._mtm_realism: float = mtm_realism
        self._mtm_helper: OptionsMTMHelper = OptionsMTMHelper()
```

- [ ] **Step 5: Rewire `_lookup_option_mtm_price` to thread quantity, observe on hit, mtm_price on miss**

Find the current `_lookup_option_mtm_price` (around line 913). Replace its entire body with:

```python
    def _lookup_option_mtm_price(
        self, sym: str, ctx, position_quantity: float = 0.0, sim_time=None,
    ) -> float:
        """Get the MTM price for an option from cached chain data, or
        fall back to a conservative Black-Scholes estimate.

        Args:
            sym: OCC symbol
            ctx: BacktestTickContext (may be None for some defensive paths)
            position_quantity: signed quantity for direction-aware envelope
            sim_time: current sim datetime; only used on the fallback path
              (if None, the helper still returns a sensible intrinsic).

        Returns:
            Non-negative price (never None). Callers no longer need the
            cost-basis fallback.
        """
        from coordinator.services.chain_builder import parse_occ_symbol

        # Layer 1: live chain mid
        if ctx is not None:
            for key, df in ctx._option_chain_cache.items():
                if df is None or (hasattr(df, "empty") and df.empty):
                    continue
                for col in ("ticker", "symbol"):
                    if col in df.columns:
                        match = df[df[col] == sym]
                        if not match.empty:
                            row = match.iloc[0]
                            bid = float(row.get("bid", 0))
                            ask = float(row.get("ask", 0))
                            mid = (
                                (bid + ask) / 2
                                if bid > 0 and ask > 0
                                else (ask if ask > 0 else bid)
                            )
                            iv = float(row.get("implied_volatility", 0) or 0)
                            # Populate the carry-forward caches
                            occ = parse_occ_symbol(sym)
                            if occ is not None and sim_time is not None:
                                self._mtm_helper.observe(
                                    symbol=sym,
                                    mid=mid,
                                    iv=iv,
                                    sim_time=sim_time,
                                    underlying=occ["underlying"],
                                    expiration_str=occ["expiration"],
                                )
                            return mid

        # Layer 2/3: BS or intrinsic via helper
        occ = parse_occ_symbol(sym)
        if occ is None:
            # Unparseable symbol — last-resort: 0 (will surface as flat position).
            return 0.0
        underlying = occ["underlying"]
        # Resolve underlying close
        underlying_price = self._lookup_symbol_close(underlying, sim_time, ctx, None)
        if underlying_price <= 0:
            # No underlying price either: return intrinsic-equivalent via helper
            # with underlying_price=0, which falls through to intrinsic=0.
            underlying_price = 0.0
        return self._mtm_helper.mtm_price(
            symbol=sym,
            sim_time=sim_time if sim_time is not None else datetime.now(timezone.utc),
            underlying_price=underlying_price,
            position_quantity=position_quantity,
            occ_parsed=occ,
            alpha=self._mtm_realism,
        )
```

Add the imports at the top of the file if not present:

```python
from datetime import datetime, timezone
```

(Check first — `datetime` is probably already imported; `timezone` may not be.)

- [ ] **Step 6: Update three call sites to pass `ps.quantity` and `sim_time`**

Find `_positions_market_value` (around line 932). Change the option branch FROM:

```python
        if svc.asset_type == AssetType.OPTIONS:
            option_price = self._lookup_option_mtm_price(sym, ctx)
            price = option_price if option_price is not None else ps.avg_price
```

TO:

```python
        if svc.asset_type == AssetType.OPTIONS:
            price = self._lookup_option_mtm_price(
                sym, ctx, position_quantity=ps.quantity, sim_time=sim_time,
            )
```

Find `_positions_snapshot` (around line 949). Change the option branch FROM:

```python
        if svc.asset_type == AssetType.OPTIONS:
            option_price = self._lookup_option_mtm_price(sym, ctx)
            current_price = option_price if option_price is not None else ps.avg_price
```

TO:

```python
        if svc.asset_type == AssetType.OPTIONS:
            current_price = self._lookup_option_mtm_price(
                sym, ctx, position_quantity=ps.quantity, sim_time=sim_time,
            )
```

Find `_positions_for_context` (around line 1006). Change the option branch FROM:

```python
        if svc.asset_type == AssetType.OPTIONS:
            option_price = self._lookup_option_mtm_price(sym, ctx)
            current_price = option_price if option_price is not None else ps.avg_price
```

TO:

```python
        if svc.asset_type == AssetType.OPTIONS:
            current_price = self._lookup_option_mtm_price(
                sym, ctx, position_quantity=ps.quantity, sim_time=sim_time,
            )
```

Note: `_positions_for_context` may not currently accept `sim_time`. If it doesn't, look at its callers and thread `sim_time` through to it. If threading is awkward, an acceptable fallback is to read `sim_time` from `ctx` if a `_current_sim_time` attribute is available, or to pass `sim_time=None` and let the helper degrade gracefully (the engine will still compute a sane intrinsic).

- [ ] **Step 7: Run the bug regression test**

Run: `pytest tests/coordinator/services/test_backtest_engine.py::test_chain_data_gap_short_option_mtm_does_not_collapse_to_initial_cash -v`
Expected: PASS.

- [ ] **Step 8: Run the full engine test suite to confirm no regression**

Run: `pytest tests/coordinator/services/test_backtest_engine.py -v`
Expected: All passing (existing tests + new bug regression).

- [ ] **Step 9: Run the broader backtest test suite**

Run: `pytest tests/coordinator/services/test_backtest_runner.py tests/coordinator/services/test_backtest_runner_options.py tests/coordinator/services/test_backtest_tick_context.py tests/coordinator/services/test_backtest_finalizer.py -v`
Expected: All passing.

- [ ] **Step 10: Commit**

```bash
git add coordinator/services/backtest_engine_v2.py tests/coordinator/services/test_backtest_engine.py
git commit -m "$(cat <<'EOF'
feat(backtest-engine): use OptionsMTMHelper for option MTM with mtm_realism

Replaces the ps.avg_price fallback in _lookup_option_mtm_price with a
layered conservative MTM: chain mid (Layer 1) → carry-forward-IV BS
(Layer 2) → intrinsic (Layer 3) → direction-aware envelope lerped by
mtm_realism. Threads position_quantity through three call sites
(_positions_market_value, _positions_snapshot, _positions_for_context).

Adds a regression test that opens a short SPY call and walks it
through five bars with no chain data; equity must reflect the true
negative position value, not collapse to initial cash.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Add `mtm_realism` column to OptimizationSession and BacktestRun

**Files:**
- Modify: `coordinator/database/models.py:458` (`OptimizationSession`)
- Modify: `coordinator/database/models.py:516` (`BacktestRun`)
- Test: (covered by Alembic round-trip in Task 9; SQLAlchemy model unit test below)

- [ ] **Step 1: Add column to `OptimizationSession`**

Find the `cost_profile` line (~478-480) and add directly after it:

```python
    mtm_realism: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0",
    )
```

- [ ] **Step 2: Add column to `BacktestRun`**

Find the `cost_profile` line in `BacktestRun` (~536-537) and add directly after it:

```python
    mtm_realism: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0",
    )
```

- [ ] **Step 3: Verify the imports**

`Float` and `Mapped`/`mapped_column` must be imported at the top of `coordinator/database/models.py`. Verify with `grep -n "Float\|mapped_column" coordinator/database/models.py | head`. They should already be there since `initial_cash` uses them.

- [ ] **Step 4: Smoke-test the models by importing them**

Run: `python -c "from coordinator.database.models import OptimizationSession, BacktestRun; print(OptimizationSession.mtm_realism, BacktestRun.mtm_realism)"`
Expected: Two `Mapped[...]` proxies print; no errors.

- [ ] **Step 5: Commit**

```bash
git add coordinator/database/models.py
git commit -m "$(cat <<'EOF'
feat(models): add mtm_realism column to OptimizationSession and BacktestRun

Both default to 0.0 (most conservative MTM envelope). BacktestRun stores
the value used at run-time so each backtest is reproducible from its row.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Alembic migration for `mtm_realism` columns

**Files:**
- Create: `coordinator/database/migrations/versions/<rev>_add_mtm_realism.py`

- [ ] **Step 1: Inspect the most-recent migration to get the parent revision id**

Run: `ls -1t coordinator/database/migrations/versions/*.py | head -3` and read the top of the newest file to find its `revision = "<id>"` line. Note that id — it's the `down_revision` for the new migration.

- [ ] **Step 2: Generate the migration scaffold**

Run: `alembic -c coordinator/alembic.ini revision -m "add mtm_realism"`
Expected: A new file appears under `coordinator/database/migrations/versions/`. Note its filename and revision id.

If the project doesn't expose `alembic.ini` at that path, run `find . -name alembic.ini -not -path '*/node_modules/*'` and adjust the `-c` flag accordingly. If alembic auto-generation isn't wired up, copy an existing migration file as a template and manually set `revision = "<short hex hash>"` and `down_revision = "<prev>"`.

- [ ] **Step 3: Write the migration body**

Open the new file and replace `upgrade`/`downgrade` with:

```python
"""add mtm_realism

Revision ID: <whatever alembic generated>
Revises: <previous head>
Create Date: 2026-06-04 ...
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<keep what alembic generated>"
down_revision: Union[str, None] = "<keep what alembic generated>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("optimization_sessions") as batch:
        batch.add_column(
            sa.Column(
                "mtm_realism", sa.Float(),
                nullable=False, server_default="0.0",
            ),
        )
    with op.batch_alter_table("backtest_runs") as batch:
        batch.add_column(
            sa.Column(
                "mtm_realism", sa.Float(),
                nullable=False, server_default="0.0",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("backtest_runs") as batch:
        batch.drop_column("mtm_realism")
    with op.batch_alter_table("optimization_sessions") as batch:
        batch.drop_column("mtm_realism")
```

- [ ] **Step 4: Run the migration against a scratch DB**

Run: `alembic -c coordinator/alembic.ini upgrade head 2>&1 | tail`
Expected: A line like `INFO  [alembic.runtime.migration] Running upgrade <prev> -> <new>, add mtm_realism`. No errors.

If the project has a make target or test fixture that brings up a clean DB, use it instead.

- [ ] **Step 5: Verify the columns landed**

Run: `python -c "
from sqlalchemy import create_engine, inspect
from coordinator.database.session import _DEFAULT_SYNC_URL
engine = create_engine(_DEFAULT_SYNC_URL)
insp = inspect(engine)
for tbl in ('optimization_sessions', 'backtest_runs'):
    cols = [c['name'] for c in insp.get_columns(tbl)]
    assert 'mtm_realism' in cols, f'{tbl} missing mtm_realism: {cols}'
    print(tbl, 'OK')
"`

(Substitute the project's actual session-URL accessor — grep `coordinator/database/session.py` for `_DEFAULT` / `DATABASE_URL` / etc.)

Expected: `optimization_sessions OK` and `backtest_runs OK`.

- [ ] **Step 6: Commit**

```bash
git add coordinator/database/migrations/versions/
git commit -m "$(cat <<'EOF'
chore(db): add mtm_realism column to optimization_sessions and backtest_runs

Additive Float NOT NULL with server_default 0.0; existing rows get the
most-conservative default.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: API — `CreateSessionRequest`, `SessionResponse`, validation

**Files:**
- Modify: `coordinator/api/routes/research.py:44` (`CreateSessionRequest`)
- Modify: `coordinator/api/routes/research.py:76` (`SessionResponse`)
- Modify: `coordinator/api/routes/research.py` — the POST handler that builds the OptimizationSession row from the request (find it via `grep -n "OptimizationSession(" coordinator/api/routes/research.py`)
- Modify: `coordinator/api/routes/research.py` — the response serializer that converts the row to `SessionResponse` (find it via `grep -n "SessionResponse(" coordinator/api/routes/research.py`)
- Test: `tests/coordinator/api/test_research_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/api/test_research_routes.py`:

```python
@pytest.mark.asyncio
async def test_create_session_defaults_mtm_realism_to_zero(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-mtm-default",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        # mtm_realism omitted → server defaults to 0.0
    })
    assert resp.status_code == 200
    assert resp.json()["mtm_realism"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_create_session_accepts_explicit_mtm_realism(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-mtm-explicit",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "mtm_realism": 0.5,
    })
    assert resp.status_code == 200
    sid = resp.json()["id"]
    # Round-trip via GET
    get_resp = await test_client.get(f"/api/research/sessions/{sid}")
    assert get_resp.json()["mtm_realism"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_create_session_rejects_mtm_realism_above_one(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-mtm-bad-high",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "mtm_realism": 1.5,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_rejects_mtm_realism_below_zero(
    test_client, seeded_algorithm,
):
    resp = await test_client.post("/api/research/sessions", json={
        "name": "t-mtm-bad-low",
        "hypothesis": "h",
        "algorithm_id": seeded_algorithm.id,
        "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 0.0},
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "mtm_realism": -0.1,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_session_accepts_endpoints_0_and_1(
    test_client, seeded_algorithm,
):
    for value in (0.0, 1.0):
        resp = await test_client.post("/api/research/sessions", json={
            "name": f"t-mtm-{value}",
            "hypothesis": "h",
            "algorithm_id": seeded_algorithm.id,
            "base_config": {},
            "parameter_space": {"x": [1]},
            "pre_registered_criteria": {"min_sharpe": 0.0},
            "date_range_start": "2023-01-01",
            "date_range_end": "2024-12-31",
            "mtm_realism": value,
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["mtm_realism"] == pytest.approx(value)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/coordinator/api/test_research_routes.py -v -k mtm_realism`
Expected: 5 failures — either 422 because field unknown, or KeyError on response.

- [ ] **Step 3: Update `CreateSessionRequest`**

Open `coordinator/api/routes/research.py`. Find `class CreateSessionRequest(BaseModel):` (around line 44). Inside the class, after the `benchmark_source: str | None = None` line, add:

```python
    mtm_realism: float = 0.0
```

Then find the existing `@model_validator(mode="after")` decorator in this class (e.g., `_date_range_valid`). After the last existing validator method, add:

```python
    @model_validator(mode="after")
    def _mtm_realism_in_range(self):
        if not (0.0 <= self.mtm_realism <= 1.0):
            raise ValueError(
                f"mtm_realism must be in [0.0, 1.0]; got {self.mtm_realism!r}"
            )
        return self
```

- [ ] **Step 4: Update `SessionResponse`**

Find `class SessionResponse(BaseModel):` (around line 76). After the `benchmark_source: str | None = None` line, add:

```python
    mtm_realism: float = 0.0
```

- [ ] **Step 5: Update the POST handler to persist `mtm_realism`**

Run: `grep -n "OptimizationSession(" coordinator/api/routes/research.py` to find the create handler. In the kwargs passed to `OptimizationSession(...)`, add:

```python
        mtm_realism=req.mtm_realism,
```

- [ ] **Step 6: Update the response serializer**

Run: `grep -n "SessionResponse(" coordinator/api/routes/research.py` to find every constructor of SessionResponse. In each, add `mtm_realism=row.mtm_realism,` (or the equivalent field-from-row passthrough).

- [ ] **Step 7: Run the test suite**

Run: `pytest tests/coordinator/api/test_research_routes.py -v -k mtm_realism`
Expected: All 5 passing.

Then run the entire research-routes test suite to catch regressions:

Run: `pytest tests/coordinator/api/test_research_routes.py -v`
Expected: All passing.

- [ ] **Step 8: Commit**

```bash
git add coordinator/api/routes/research.py tests/coordinator/api/test_research_routes.py
git commit -m "$(cat <<'EOF'
feat(api/research): expose mtm_realism on session create/response

CreateSessionRequest accepts mtm_realism: float = 0.0 with a
model_validator enforcing [0.0, 1.0]. SessionResponse echoes the
persisted value.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: SDK CLI — `quilt research session create --mtm-realism FLOAT`

**Files:**
- Modify: `sdk/cli/commands/research.py:64` (`session_create`)
- Test: `tests/sdk/cli/test_research_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/sdk/cli/test_research_cli.py`:

```python
def test_session_create_passes_mtm_realism_in_payload():
    from unittest.mock import AsyncMock, patch

    runner = CliRunner()
    mock_response = {
        "id": 99, "name": "t", "hypothesis": "h",
        "algorithm_id": "algo-x", "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 1.0},
        "status": "open", "notes": "",
        "created_at": "2026-06-04", "completed_at": None, "n_runs": 0,
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "initial_cash": 10000.0,
        "cost_profile": "default",
        "benchmark_symbol": None,
        "benchmark_source": None,
        "mtm_realism": 0.25,
    }
    with patch("sdk.cli.commands.research._client") as mock_client_factory:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_factory.return_value = mock_client
        result = runner.invoke(research_group, [
            "session", "create",
            "--name", "t",
            "--hypothesis", "h",
            "--algorithm-id", "algo-x",
            "--base-config", "{}",
            "--parameter-space", '{"x":[1]}',
            "--criteria", '{"min_sharpe":1.0}',
            "--start", "2023-01-01",
            "--end", "2024-12-31",
            "--mtm-realism", "0.25",
        ])
        assert result.exit_code == 0, result.output
        payload = mock_client.post.call_args[1]["json"]
        assert payload["mtm_realism"] == pytest.approx(0.25)


def test_session_create_defaults_mtm_realism_to_zero():
    from unittest.mock import AsyncMock, patch

    runner = CliRunner()
    mock_response = {
        "id": 100, "name": "t", "hypothesis": "h",
        "algorithm_id": "algo-x", "base_config": {},
        "parameter_space": {"x": [1]},
        "pre_registered_criteria": {"min_sharpe": 1.0},
        "status": "open", "notes": "",
        "created_at": "2026-06-04", "completed_at": None, "n_runs": 0,
        "date_range_start": "2023-01-01",
        "date_range_end": "2024-12-31",
        "initial_cash": 10000.0,
        "cost_profile": "default",
        "benchmark_symbol": None,
        "benchmark_source": None,
        "mtm_realism": 0.0,
    }
    with patch("sdk.cli.commands.research._client") as mock_client_factory:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_factory.return_value = mock_client
        result = runner.invoke(research_group, [
            "session", "create",
            "--name", "t",
            "--hypothesis", "h",
            "--algorithm-id", "algo-x",
            "--base-config", "{}",
            "--parameter-space", '{"x":[1]}',
            "--criteria", '{"min_sharpe":1.0}',
            "--start", "2023-01-01",
            "--end", "2024-12-31",
        ])
        assert result.exit_code == 0, result.output
        payload = mock_client.post.call_args[1]["json"]
        assert payload["mtm_realism"] == pytest.approx(0.0)
```

If `pytest` isn't already imported in this file, add `import pytest` at the top.

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/sdk/cli/test_research_cli.py -v -k mtm_realism`
Expected: 2 failures — exit code 2 (unknown option) or KeyError on `mtm_realism` in payload.

- [ ] **Step 3: Add `--mtm-realism` option to the CLI command**

Open `sdk/cli/commands/research.py`. Find `@session_group.command("create")` (around line 64). After the existing `@click.option(...)` decorators (after `--benchmark-source`), add:

```python
@click.option("--mtm-realism", type=float, default=0.0,
              help="Backtest MTM realism in [0.0, 1.0]; "
                   "0.0 = most conservative (default), "
                   "1.0 = broker-like (potentially exploitable)")
```

Then update the function signature to accept the new parameter:

Change FROM:
```python
def session_create(ctx, name, hypothesis, algorithm_id, base_config,
                   parameter_space, criteria, notes,
                   date_range_start, date_range_end, initial_cash,
                   cost_profile, benchmark_symbol, benchmark_source):
```

TO:
```python
def session_create(ctx, name, hypothesis, algorithm_id, base_config,
                   parameter_space, criteria, notes,
                   date_range_start, date_range_end, initial_cash,
                   cost_profile, benchmark_symbol, benchmark_source,
                   mtm_realism):
```

Then update the payload dict inside the function. After the `"benchmark_source": benchmark_source,` line, add:

```python
        "mtm_realism": mtm_realism,
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/sdk/cli/test_research_cli.py -v`
Expected: All passing.

- [ ] **Step 5: Commit**

```bash
git add sdk/cli/commands/research.py tests/sdk/cli/test_research_cli.py
git commit -m "$(cat <<'EOF'
feat(cli/research): add --mtm-realism flag to 'session create'

Default 0.0 (full conservative envelope). Pass-through to the
research API payload.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Thread `mtm_realism` through sweep + walk-forward

**Files:**
- Modify: `coordinator/services/validation/sweep.py:116` (`_run_one_backtest`)
- Modify: `coordinator/services/validation/sweep.py:175` (`run_sweep`)
- Modify: `coordinator/services/validation/sweep.py:140` (`run_walk_forward`)
- Modify: `coordinator/services/validation/sweep.py:89` (`_run_oos_backtest`)

- [ ] **Step 1: Update `_run_one_backtest` signature and BacktestRun creation**

In `coordinator/services/validation/sweep.py`, find `async def _run_one_backtest(` (around line 116). In its kwargs (after `benchmark_source`), add:

```python
    mtm_realism: float,
```

Then in the `BacktestRun(...)` construction inside the function, after `benchmark_source=benchmark_source,`, add:

```python
        mtm_realism=mtm_realism,
```

- [ ] **Step 2: Update `_run_oos_backtest` signature and BacktestRun creation**

In the same file, find `async def _run_oos_backtest(` (around line 89). Same edits — add `mtm_realism: float,` kwarg and `mtm_realism=mtm_realism,` in the `BacktestRun(...)`.

- [ ] **Step 3: Update `run_sweep`**

Find `async def run_sweep(` (around line 175). Add `mtm_realism: float = 0.0,` as a kwarg. Then in the call to `_run_one_backtest(...)` inside `_bounded`, add `mtm_realism=mtm_realism,`.

- [ ] **Step 4: Update `run_walk_forward`**

Find `async def run_walk_forward(` (around line 140). Add `mtm_realism: float = 0.0,` as a kwarg. Then in both the `run_sweep(...)` call (line ~183) and the `_run_oos_backtest(...)` call (line ~203), add `mtm_realism=mtm_realism,`.

- [ ] **Step 5: Update the dispatchers in `research_job_manager.py`**

Open `coordinator/services/research_job_manager.py`. Find `async def _dispatch_sweep(` (around line 157). In the call to `self._sweep_fn(...)`, after `benchmark_source=payload.get("benchmark_source"),`, add:

```python
            mtm_realism=payload.get("mtm_realism", 0.0),
```

Find `async def _dispatch_walk_forward(` (around line 182). Add the same line to the call to `self._wf_fn(...)`.

- [ ] **Step 6: Update job-creation endpoints to source `mtm_realism` from session into payload**

Run: `grep -n "create_sweep_job\|create_walk_forward_job\|request_payload" coordinator/api/routes/research.py` to find the API handlers that build the payload dict. In each, when reading session fields like `initial_cash`, `cost_profile` to put into the payload, also read `mtm_realism`:

```python
        "mtm_realism": session.mtm_realism,
```

- [ ] **Step 7: Run the validation-sweep tests**

Run: `pytest tests/coordinator/services/validation -v 2>&1 | tail -50`
Expected: All passing.

Run: `pytest tests/coordinator/api/test_research_routes.py -v 2>&1 | tail -30`
Expected: All passing.

- [ ] **Step 8: Commit**

```bash
git add coordinator/services/validation/sweep.py coordinator/services/research_job_manager.py coordinator/api/routes/research.py
git commit -m "$(cat <<'EOF'
feat(research): thread mtm_realism from session through sweep/walk-forward

Session row → API payload → dispatcher → run_sweep / run_walk_forward
→ _run_one_backtest / _run_oos_backtest → BacktestRun.mtm_realism.
The runner picks it up from the row in the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Runner passes `mtm_realism` to `BacktestEngine.run`

**Files:**
- Modify: `coordinator/services/backtest_runner.py:209` (BacktestRunner.run)

- [ ] **Step 1: Read the relevant section to confirm line numbers**

Run: `grep -n "run.initial_cash\|run.cost_profile\|BacktestEngine(config=" coordinator/services/backtest_runner.py`

You should see references around lines 226-238 (BacktestRun field snapshot) and ~477 (engine invocation).

- [ ] **Step 2: Snapshot `mtm_realism` from the BacktestRun row**

Open `coordinator/services/backtest_runner.py`. Find the block (around line 226-237) that snapshots fields off the `run` row, e.g.:

```python
        initial_cash = run.initial_cash
        slippage_cfg = run.slippage_model
        ...
        cost_profile = run.cost_profile or "default"
```

After `cost_profile = run.cost_profile or "default"`, add:

```python
        mtm_realism = float(run.mtm_realism) if run.mtm_realism is not None else 0.0
```

- [ ] **Step 3: Pass `mtm_realism` into `BacktestEngine.run(...)`**

Find the `BacktestEngine(config=engine_config).run(` call (around line 477). In the kwargs, after `config=config_overrides,`, add:

```python
                    mtm_realism=mtm_realism,
```

- [ ] **Step 4: Run the runner test suite**

Run: `pytest tests/coordinator/services/test_backtest_runner.py tests/coordinator/services/test_backtest_runner_options.py -v`
Expected: All passing.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_runner.py
git commit -m "$(cat <<'EOF'
feat(backtest-runner): pass mtm_realism from BacktestRun row into engine.run

Snapshots run.mtm_realism (default 0.0) and forwards it as the
mtm_realism kwarg to BacktestEngine.run.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Dashboard — `ExperimentScopeFields` numeric input for `mtm_realism`

**Files:**
- Modify: `dashboard/src/components/ExperimentScopeFields.tsx`
- Modify: `dashboard/src/components/ExperimentScopeFields.test.tsx`
- Modify: any parent component that passes props to `ExperimentScopeFields` (find via `grep -rn "ExperimentScopeFields" dashboard/src/`)

- [ ] **Step 1: Write the failing test**

Append to `dashboard/src/components/ExperimentScopeFields.test.tsx`:

```typescript
  it("renders an MTM realism input with default value", () => {
    render(<ExperimentScopeFields {...baseProps} mtmRealism={0.0} />);
    const input = screen.getByLabelText(/mtm realism/i) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe("0");
  });

  it("emits new mtm_realism on change", () => {
    const onChange = vi.fn();
    render(
      <ExperimentScopeFields
        {...baseProps}
        mtmRealism={0.0}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/mtm realism/i), {
      target: { value: "0.5" },
    });
    const last = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(last.mtm_realism).toBe(0.5);
  });

  it("onValidityChange false when mtm_realism out of range", () => {
    const onValidityChange = vi.fn();
    render(
      <ExperimentScopeFields
        {...baseProps}
        startDate="2023-01-01"
        endDate="2024-12-31"
        mtmRealism={1.5}
        onValidityChange={onValidityChange}
      />,
    );
    expect(onValidityChange).toHaveBeenLastCalledWith(false);
  });
```

Also update `baseProps` near the top of the test file to include `mtmRealism: 0.0`:

```typescript
const baseProps = {
  startDate: "",
  endDate: "",
  initialCash: 10000,
  costProfile: "default",
  benchmarkSymbol: "",
  benchmarkSource: "",
  mtmRealism: 0.0,
  onChange: () => {},
  onValidityChange: () => {},
};
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd dashboard && npx vitest run src/components/ExperimentScopeFields.test.tsx 2>&1 | tail`
Expected: 3 new tests failing — either "Cannot find getByLabelText with /mtm realism/i" or a TypeScript error about unknown prop.

- [ ] **Step 3: Add `mtmRealism` to the `Props` interface and the component body**

Open `dashboard/src/components/ExperimentScopeFields.tsx`. In the `Props` interface, add:

```typescript
  mtmRealism: number;
```

(right after `benchmarkSource: string;`)

Update the `onChange` callback signature to include `mtm_realism: number`:

```typescript
  onChange: (next: {
    date_range_start: string;
    date_range_end: string;
    initial_cash: number;
    cost_profile: string;
    benchmark_symbol: string | null;
    benchmark_source: string | null;
    mtm_realism: number;
  }) => void;
```

Update `isValid`:

```typescript
function isValid(p: Props): boolean {
  if (!p.startDate || !p.endDate) return false;
  if (p.endDate <= p.startDate) return false;
  if (!(p.initialCash > 0)) return false;
  if (!p.costProfile.trim()) return false;
  const bsEmpty = !p.benchmarkSymbol.trim();
  const bSrcEmpty = !p.benchmarkSource.trim();
  if (bsEmpty !== bSrcEmpty) return false;
  if (p.mtmRealism < 0 || p.mtmRealism > 1) return false;
  return true;
}
```

Update the destructure inside the component body to include `mtmRealism`:

```typescript
  const {
    startDate, endDate, initialCash, costProfile,
    benchmarkSymbol, benchmarkSource, mtmRealism,
    onChange, onValidityChange, disabled,
  } = props;
```

Update the `useEffect` dependency array to include `mtmRealism`:

```typescript
  useEffect(() => {
    onValidityChange?.(isValid(props));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startDate, endDate, initialCash, costProfile, benchmarkSymbol, benchmarkSource, mtmRealism]);
```

Update `emit` to include `mtm_realism` in the outgoing payload:

```typescript
  function emit(overrides: Partial<Props>) {
    const merged = { ...props, ...overrides };
    onChange({
      date_range_start: merged.startDate,
      date_range_end: merged.endDate,
      initial_cash: merged.initialCash,
      cost_profile: merged.costProfile,
      benchmark_symbol: merged.benchmarkSymbol.trim() || null,
      benchmark_source: merged.benchmarkSource.trim() || null,
      mtm_realism: merged.mtmRealism,
    });
  }
```

Add the input field to the rendered JSX (place inside the existing 2-column benchmark grid as a new third grid, or extend the 4-column scope grid to 5; the cleanest is a new row at the end):

```tsx
      <div className="grid grid-cols-1 gap-2">
        <div className="space-y-1">
          <label htmlFor="sf-mtm" className="text-sm text-gray-300">
            MTM realism (0 = conservative, 1 = broker-like)
          </label>
          <input
            id="sf-mtm" type="number" min={0} max={1} step={0.05}
            value={mtmRealism} disabled={disabled}
            onChange={(e) => emit({ mtmRealism: parseFloat(e.target.value || "0") })}
            className={input}
          />
        </div>
      </div>
```

- [ ] **Step 4: Update parent components that use `ExperimentScopeFields`**

Run: `grep -rn "ExperimentScopeFields" dashboard/src/ --include="*.tsx" --include="*.ts"` to find every caller.

For each caller, add:
- A new piece of state for `mtmRealism` (initial 0.0)
- Pass `mtmRealism={mtmRealism}` as a prop
- In the `onChange` handler, capture the new `mtm_realism` from the emitted object and update state
- When submitting to the API, include `mtm_realism` in the payload

For each caller file, the changes will look roughly like:

```typescript
// State
const [mtmRealism, setMtmRealism] = useState(0.0);

// Pass prop
<ExperimentScopeFields
  ...
  mtmRealism={mtmRealism}
  onChange={(next) => {
    setStartDate(next.date_range_start);
    setEndDate(next.date_range_end);
    setInitialCash(next.initial_cash);
    setCostProfile(next.cost_profile);
    setBenchmarkSymbol(next.benchmark_symbol ?? "");
    setBenchmarkSource(next.benchmark_source ?? "");
    setMtmRealism(next.mtm_realism);
  }}
  ...
/>

// In the submit payload
const payload = {
  ...
  mtm_realism: mtmRealism,
};
```

- [ ] **Step 5: Run dashboard tests**

Run: `cd dashboard && npx vitest run 2>&1 | tail -50`
Expected: All passing (existing + new tests).

- [ ] **Step 6: Run type-check**

Run: `cd dashboard && npx tsc --noEmit 2>&1 | tail`
Expected: No type errors.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/
git commit -m "$(cat <<'EOF'
feat(dashboard): add mtm_realism input to ExperimentScopeFields

Numeric input range 0-1 step 0.05 default 0.0; flows into the
session-create API payload alongside the other scope fields.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Full-suite sanity sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the full coordinator test suite**

Run: `pytest tests/coordinator -q 2>&1 | tail -30`
Expected: All passing.

- [ ] **Step 2: Run the SDK CLI test suite**

Run: `pytest tests/sdk -q 2>&1 | tail -30`
Expected: All passing.

- [ ] **Step 3: Run the dashboard test suite + type-check**

Run: `cd dashboard && npx vitest run 2>&1 | tail -10 && npx tsc --noEmit 2>&1 | tail`
Expected: All passing, no type errors.

- [ ] **Step 4: Quick smoke run of the engine**

Run: `python -c "
from coordinator.services.options_mtm import OptionsMTMHelper, black_scholes_price
h = OptionsMTMHelper()
print('BS ATM call 1y:', black_scholes_price(100, 100, 1, 0.045, 0.20, 'call'))
print('helper OK:', h is not None)
"`
Expected: Reasonable BS price (~10) and `helper OK: True`.

- [ ] **Step 5: Push the branch**

```bash
git push origin HEAD 2>&1 | tail
```

---

## Self-Review Notes

(Run after writing the plan; fix issues inline; do not re-review.)

**Spec coverage:**

- §1 Architecture (single seam, helper module, per-run cache) → Tasks 1–7
- §2.1 Layer 1 (chain mid) → Task 7 Step 5
- §2.2 Layer 2 (BS + carry-forward IV) → Tasks 2, 4, 6
- §2.3 Layer 3 (intrinsic) → Task 6
- §2.4 Direction-aware envelope + lerp → Task 5
- §2.5 Worked example regression → Task 7 Step 2 (bug-regression test)
- §3 Module structure → Tasks 1–6
- §4.1 Session schema addition → Tasks 8, 9
- §4.2 Engine plumbing → Tasks 10–13
- §4.3 Engine internals (kwargs, helper, rewire) → Task 7
- §5 Testing → tests live with each task; bug-regression in Task 7
- §6 Performance → addressed by the design itself; smoke test in Task 15
- §7 Out of scope / tradeoffs → no implementation needed
- §8 Files touched → all paths covered

**Placeholder scan:** No "TBD" / "TODO" / "fill in later". The only intentional placeholder is `<rev>` in the Alembic migration filename, which Alembic auto-generates (Task 9 Step 2). The dispatcher edit (Task 12 Step 5) acknowledges that the job-creation API endpoint location must be located via `grep`; the surrounding instructions are concrete.

**Type consistency:**

- `OptionsMTMHelper.observe(symbol, mid, iv, sim_time, underlying, expiration_str)` — same in Tasks 3 and 7.
- `OptionsMTMHelper.mtm_price(symbol, sim_time, underlying_price, position_quantity, occ_parsed, alpha=0.0)` — same in Tasks 6 and 7.
- `BacktestEngine.run(..., mtm_realism: float = 0.0)` — same in Tasks 7 and 13.
- `mtm_realism` column type: `Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")` — same in Task 8.
- Pydantic field default: `mtm_realism: float = 0.0` — same in Tasks 10 and 11.
- `parse_occ_symbol` returns `option_type` as `"call"`/`"put"`; `mtm_price` handles both `"call"`/`"put"` and `"C"`/`"P"` via `option_type[0].upper() == "C"`.
