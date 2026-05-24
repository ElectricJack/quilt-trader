# Options Fill Model Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four bugs in the options backtest engine that produce unrealistic results: short position tracking, sell-to-open PnL, option price lookup fallback, and static chain data.

**Architecture:** All fixes are in `backtest_engine_v2.py` and `backtest_tick_context.py`. The `_PositionState` dataclass gets a sign-aware quantity (negative = short). `_apply_fill` gets sell-to-open vs sell-to-close logic. `_lookup_option_price` gets a guaranteed fallback to chain data instead of equity prices. The chain cache gets per-tick repricing from the underlying.

**Tech Stack:** Python 3.12, pandas, dataclasses, pytest. Key files: `coordinator/services/backtest_engine_v2.py`, `coordinator/services/backtest_tick_context.py`, `tests/coordinator/services/test_backtest_engine.py`.

---

## File Structure

| File | Responsibility | Changes |
|------|---------------|---------|
| `coordinator/services/backtest_engine_v2.py` | Backtest simulation engine | Fix `_apply_fill`, `_fill_market`, `_lookup_option_price`, short position tracking, sell rejection |
| `coordinator/services/backtest_tick_context.py` | Market data context for backtest | Add `_reprice_chain_from_underlying` for dynamic chain repricing |
| `tests/coordinator/services/test_options_fill_fixes.py` | Tests for all fixes | New test file |

---

## Sub-project 1: Short Position Tracking

### Task 1: Allow negative quantity in `_PositionState` for short options

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`
- Create: `tests/coordinator/services/test_options_fill_fixes.py`

- [ ] **Step 1: Write failing test for short position creation**

```python
# tests/coordinator/services/test_options_fill_fixes.py
import pytest
import pandas as pd
from datetime import date
from coordinator.services.backtest_engine_v2 import (
    BacktestEngine, CancelToken, FillRecord,
)
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import SlippageModel
from sdk.signals import Signal, SignalLeg, SignalType, OrderType


class RecordingObserver:
    def __init__(self):
        self.fills = []; self.equity = []; self.rejected = []
        self.complete = False; self.error = None
    def on_tick(self, st, cs): pass
    def on_signals_emitted(self, st, s): pass
    def on_fill(self, f): self.fills.append(f)
    def on_signal_rejected(self, st, s, r): self.rejected.append((st, s, r))
    def on_equity_point(self, st, pv, c, p): self.equity.append({"pv": pv, "cash": c})
    def on_complete(self, s): self.complete = True
    def on_error(self, e): self.error = e


def _make_clock(n=5, start="2025-04-01"):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq="D", tz="UTC"),
        "open": [500.0]*n, "high": [505.0]*n,
        "low": [495.0]*n, "close": [502.0]*n, "volume": [1e6]*n,
    })


def _make_chain():
    return pd.DataFrame([
        {"symbol": "O:QQQ250516C00500000", "strike": 500.0, "option_type": "call",
         "bid": 8.00, "ask": 8.50, "last": 8.25, "volume": 100,
         "open_interest": 5000, "implied_volatility": 0.20},
        {"symbol": "O:QQQ250516P00500000", "strike": 500.0, "option_type": "put",
         "bid": 7.00, "ask": 7.50, "last": 7.25, "volume": 80,
         "open_interest": 4000, "implied_volatility": 0.22},
    ])


def test_sell_to_open_creates_short_position():
    """Selling an option we don't own should create a short (negative qty) position."""

    class SellToOpenAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:QQQ250516C00500000",
                signal_type=SignalType.SELL, quantity=2,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=SellToOpenAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 1
    fill = obs.fills[0]
    assert fill.side == "sell"
    assert fill.fill_price == pytest.approx(8.00, abs=0.01)  # bid for sell
    # Sell-to-open should NOT produce realized PnL
    assert fill.realized_pnl is None or fill.realized_pnl == 0.0
    # Cash should INCREASE by premium received (bid * qty * 100)
    # but also account for margin/collateral requirement
    # For now: cash += premium received
    assert obs.equity[-1]["cash"] > 100_000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_options_fill_fixes.py::test_sell_to_open_creates_short_position -v`
Expected: FAIL (realized_pnl is non-zero because `_apply_fill` treats all sells as closing).

- [ ] **Step 3: Fix `_apply_fill` to handle sell-to-open vs sell-to-close**

Edit `coordinator/services/backtest_engine_v2.py`, replace the `_apply_fill` method:

```python
    def _apply_fill(self, cash: float, positions: dict, fill: FillRecord) -> float:
        key = (fill.symbol,)
        ps = positions.get(key) or _PositionState(asset_type=fill.asset_type)
        multiplier = 100 if fill.asset_type == "options" else 1
        notional = fill.fill_price * fill.quantity * multiplier

        if fill.side == "buy":
            if ps.quantity < 0:
                # Buy-to-close: covering a short position
                close_qty = min(fill.quantity, abs(ps.quantity))
                realized = (ps.avg_price - fill.fill_price) * close_qty * multiplier - fill.fees
                fill.realized_pnl = realized
                ps.quantity += fill.quantity
                cash -= notional + fill.fees
            else:
                # Buy-to-open: adding to long position
                total_qty = ps.quantity + fill.quantity
                if total_qty == 0:
                    ps.avg_price = 0.0
                else:
                    ps.avg_price = (ps.avg_price * ps.quantity + fill.fill_price * fill.quantity) / total_qty
                ps.quantity = total_qty
                cash -= notional + fill.fees
        else:  # sell
            if ps.quantity > 0:
                # Sell-to-close: closing a long position
                close_qty = min(fill.quantity, ps.quantity)
                realized = (fill.fill_price - ps.avg_price) * close_qty * multiplier - fill.fees
                fill.realized_pnl = realized
                ps.quantity -= fill.quantity
                cash += notional - fill.fees
            else:
                # Sell-to-open: creating/adding to short position
                total_qty = ps.quantity - fill.quantity  # goes more negative
                if ps.quantity == 0:
                    ps.avg_price = fill.fill_price
                else:
                    existing_short = abs(ps.quantity)
                    ps.avg_price = (ps.avg_price * existing_short + fill.fill_price * fill.quantity) / (existing_short + fill.quantity)
                ps.quantity = total_qty
                fill.realized_pnl = None  # No realized PnL on opening
                cash += notional - fill.fees  # Receive premium

            if ps.quantity == 0:
                ps.avg_price = 0.0

        positions[key] = ps
        if ps.quantity == 0:
            del positions[key]
        return cash
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_options_fill_fixes.py::test_sell_to_open_creates_short_position -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix(options): track short positions with negative quantity, distinguish sell-to-open from sell-to-close"
```

---

### Task 2: Short positions visible in `ctx.positions`

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py` (in `_positions_for_context`)
- Test: `tests/coordinator/services/test_options_fill_fixes.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/coordinator/services/test_options_fill_fixes.py`:

```python
def test_short_position_visible_in_ctx_positions():
    """After sell-to-open, the algorithm must see the short position in ctx.positions."""

    class CheckPositionsAlgo:
        def __init__(self): self._step = 0; self.saw_position = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            self._step += 1
            if self._step == 1:
                return [Signal(legs=[SignalLeg(
                    symbol="O:QQQ250516C00500000",
                    signal_type=SignalType.SELL, quantity=2,
                    asset_type="options", order_type=OrderType.MARKET,
                )])]
            if self._step == 3:
                # Check if we can see the short position
                pos = ctx.positions.get("O:QQQ250516C00500000")
                if pos is not None and pos.quantity != 0:
                    self.saw_position = True
            return []
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()
    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    algo = CheckPositionsAlgo()
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=algo, ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert algo.saw_position, "Algorithm did not see the short position in ctx.positions"
```

- [ ] **Step 2: Fix `_positions_for_context` to expose negative-quantity positions**

In `coordinator/services/backtest_engine_v2.py`, the `_positions_for_context_from_cache` method creates `Position` objects. Currently it may skip or misrepresent negative quantities. Ensure it passes `quantity` as-is (negative for shorts):

Read the method, verify it passes `ps.quantity` directly to `Position(quantity=...)`. If `Position` doesn't support negative quantities, pass `abs(ps.quantity)` and add a `side` or use the sign. Check `sdk/models.py` `Position` dataclass.

The simplest fix: `Position.quantity` stores the absolute value, but the engine tracks the sign internally. For `ctx.positions`, expose negative quantity so algorithms can detect short positions.

- [ ] **Step 3: Run test, fix, commit**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_options_fill_fixes.py -v`
Commit: `git add -A && git commit -m "fix(options): expose short positions in ctx.positions with negative quantity"`

---

### Task 3: Straddle round-trip: sell-to-open then buy-to-close

**Files:**
- Test: `tests/coordinator/services/test_options_fill_fixes.py` (append)

- [ ] **Step 1: Write the round-trip test**

```python
def test_straddle_round_trip_produces_correct_pnl():
    """Sell straddle at $8 bid, buy back at $8.50 ask → loss of $0.50 * 100 per contract."""

    class StraddleRoundTripAlgo:
        def __init__(self): self._step = 0
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            self._step += 1
            if self._step == 1:
                # Sell to open
                return [Signal(legs=[SignalLeg(
                    symbol="O:QQQ250516C00500000",
                    signal_type=SignalType.SELL, quantity=1,
                    asset_type="options", order_type=OrderType.MARKET,
                )])]
            if self._step == 3:
                # Buy to close
                return [Signal(legs=[SignalLeg(
                    symbol="O:QQQ250516C00500000",
                    signal_type=SignalType.BUY, quantity=1,
                    asset_type="options", order_type=OrderType.MARKET,
                )])]
            return []
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()
    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=StraddleRoundTripAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 2

    sell_fill = obs.fills[0]
    buy_fill = obs.fills[1]

    assert sell_fill.side == "sell"
    assert sell_fill.fill_price == pytest.approx(8.00, abs=0.01)  # bid
    assert sell_fill.realized_pnl is None  # sell-to-open, no realized PnL

    assert buy_fill.side == "buy"
    assert buy_fill.fill_price == pytest.approx(8.50, abs=0.01)  # ask
    assert buy_fill.realized_pnl is not None
    # PnL = (sold_at - bought_at) * qty * 100 = (8.00 - 8.50) * 1 * 100 = -$50
    assert buy_fill.realized_pnl == pytest.approx(-50.0, abs=1.0)
```

- [ ] **Step 2: Run test**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_options_fill_fixes.py::test_straddle_round_trip_produces_correct_pnl -v`
Expected: PASS (if Task 1's `_apply_fill` fix is correct).

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "test(options): verify straddle round-trip PnL (sell-to-open then buy-to-close)"
```

---

## Sub-project 2: Option Price Lookup Hardening

### Task 4: Never fall back to equity bar prices for options fills

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`
- Test: `tests/coordinator/services/test_options_fill_fixes.py` (append)

- [ ] **Step 1: Write failing test**

```python
def test_options_fill_never_uses_equity_price():
    """When option chain lookup fails, reject the fill — never use the underlying's bar price."""

    class BuyUnknownOptionAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:QQQ250516C00999000",  # strike 999 — not in chain
                signal_type=SignalType.BUY, quantity=1,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()  # only has strike 500
    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=BuyUnknownOptionAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    # Should NOT fill at $500 (QQQ equity price) — should be rejected
    assert len(obs.fills) == 0
    assert len(obs.rejected) == 1
    assert "no_option_price" in obs.rejected[0][2]
```

- [ ] **Step 2: Fix `_fill_market` and `_fill_limit` to reject when option price is not found**

In `coordinator/services/backtest_engine_v2.py`, modify `_fill_market`:

After the options path that calls `_lookup_option_price`, if the result is `None`, return `None` (no fill) instead of falling through to the equity path:

```python
    def _fill_market(self, po, bar, side, slippage, fees_list, rng, sim_time, ctx=None) -> FillRecord:
        leg = po.leg

        if leg.asset_type == "options" and ctx is not None:
            option_price = self._lookup_option_price(leg.symbol, side, ctx)
            if option_price is not None and option_price > 0:
                # ... existing options fill code ...
            else:
                # No option price found — cannot fill
                return None

        # Original equity path (only reached for non-options)
        ...
```

Also update `_fill_limit` similarly.

Then in `_run_internal`, where `_try_fill` returns `(None, False)` for unfilled orders, add a specific rejection reason for options:

```python
                if fill is None and not advance_for_stop:
                    # Check if this was an options order that couldn't find a price
                    if po.leg.asset_type == "options":
                        observer.on_signal_rejected(
                            sim_time, Signal(legs=[po.leg]), "no_option_price"
                        )
                    else:
                        # existing expiry logic (IOC/DAY/GTC)
                        ...
```

- [ ] **Step 3: Run tests, commit**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_options_fill_fixes.py -v`
Commit: `git add -A && git commit -m "fix(options): reject fills when option price not found instead of using equity price"`

---

## Sub-project 3: Dynamic Chain Repricing

### Task 5: Reprice option chains from underlying price movement

**Files:**
- Edit: `coordinator/services/backtest_tick_context.py`
- Test: `tests/coordinator/services/test_options_fill_fixes.py` (append)

- [ ] **Step 1: Write failing test**

```python
def test_option_chain_reprices_with_underlying():
    """Option prices should change as the underlying moves, not stay static."""
    chain_df = _make_chain()  # call bid=8.00 at underlying ~500

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    # Underlying at $500 initially
    ctx1 = BacktestTickContext(
        bars={}, positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx1.set_sim_time(pd.Timestamp("2025-04-15", tz="UTC").to_pydatetime())
    ctx1._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df.copy()

    chain1 = ctx1.option_chain("QQQ", date(2025, 5, 16))
    call_price_1 = chain1.calls[0].bid

    # Now simulate underlying moving to $520 (+4%)
    # The call at strike 500 should be worth MORE (deeper ITM)
    ctx2 = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): pd.DataFrame({
            "timestamp": [pd.Timestamp("2025-04-16", tz="UTC")],
            "open": [520.0], "high": [525.0], "low": [515.0], "close": [520.0], "volume": [1e6],
        })},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx2.set_sim_time(pd.Timestamp("2025-04-16", tz="UTC").to_pydatetime())
    ctx2._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df.copy()

    chain2 = ctx2.option_chain("QQQ", date(2025, 5, 16))
    call_price_2 = chain2.calls[0].bid

    # Call should be more expensive when underlying is higher
    assert call_price_2 > call_price_1, (
        f"Call price should increase when underlying rises: was {call_price_1}, now {call_price_2}"
    )
```

- [ ] **Step 2: Implement `_reprice_chain` in `option_chain()`**

In `coordinator/services/backtest_tick_context.py`, after loading the chain from cache/disk, adjust prices based on the current underlying price vs the reference price when the chain was snapshotted.

The simplest realistic approach: shift option prices by the intrinsic value change.

```python
def _reprice_chain(self, chain_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Adjust cached option prices based on current underlying price.

    Uses intrinsic value shift: if underlying moved +$5 since the snapshot,
    call prices increase by ~$5 and put prices decrease by ~$5 (capped at 0).
    This is a first-order approximation (delta ≈ 1 for deep ITM, 0 for deep OTM).
    """
    # Get current underlying price
    underlying_price = self._get_underlying_price(symbol)
    if underlying_price is None:
        return chain_df

    # Get the reference price (underlying at snapshot time)
    # Approximate from the chain: ATM strike is where call_price ≈ put_price
    # Or simpler: use the average of nearby strikes weighted by volume
    # Simplest: the underlying price is already available in market_data
    ref_price = chain_df.attrs.get("underlying_ref_price")
    if ref_price is None:
        # First time: store current underlying as reference
        chain_df.attrs["underlying_ref_price"] = underlying_price
        return chain_df

    price_change = underlying_price - ref_price
    if abs(price_change) < 0.01:
        return chain_df

    repriced = chain_df.copy()
    for idx, row in repriced.iterrows():
        strike = row["strike"]
        if row["option_type"] == "call":
            # Call gains intrinsic value when underlying rises
            intrinsic_change = max(0, underlying_price - strike) - max(0, ref_price - strike)
            repriced.at[idx, "bid"] = max(0.01, row["bid"] + intrinsic_change)
            repriced.at[idx, "ask"] = max(0.01, row["ask"] + intrinsic_change)
            repriced.at[idx, "last"] = max(0.01, row["last"] + intrinsic_change)
        else:  # put
            intrinsic_change = max(0, strike - underlying_price) - max(0, strike - ref_price)
            repriced.at[idx, "bid"] = max(0.0, row["bid"] + intrinsic_change)
            repriced.at[idx, "ask"] = max(0.01, row["ask"] + intrinsic_change)
            repriced.at[idx, "last"] = max(0.01, row["last"] + intrinsic_change)

    return repriced

def _get_underlying_price(self, symbol: str) -> float | None:
    """Get the current underlying price from market data bars."""
    for (src, sym, tf), df in self._bars.items():
        if sym == symbol and not df.empty:
            ts = pd.to_datetime(df["timestamp"])
            if ts.dt.tz is not None:
                ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
            cutoff = pd.Timestamp(self._sim_time_now)
            if cutoff.tz is not None:
                cutoff = cutoff.tz_localize(None)
            visible = df[ts <= cutoff]
            if not visible.empty:
                return float(visible.iloc[-1]["close"])
    return None
```

In `option_chain()`, after loading `df` from cache/disk but before building `OptionContract` objects, call:

```python
        if df is not None and not df.empty:
            df = self._reprice_chain(df, symbol)
```

- [ ] **Step 3: Run tests, commit**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_options_fill_fixes.py -v`
Commit: `git add -A && git commit -m "feat(options): reprice cached option chains based on underlying price movement"`

---

### Task 6: Mark-to-market uses repriced chain for short positions

**Files:**
- Test: `tests/coordinator/services/test_options_fill_fixes.py` (append)

- [ ] **Step 1: Write test for MTM of short positions**

```python
def test_short_option_mtm_reflects_liability():
    """A short option position should show as a LIABILITY in portfolio value, not an asset."""

    class SellAndHoldAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:QQQ250516C00500000",
                signal_type=SignalType.SELL, quantity=1,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _make_clock()
    chain_df = _make_chain()  # call bid=8.00
    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2025, 5, 16)]

    ctx = BacktestTickContext(
        bars={("polygon", "QQQ", "1day"): clock},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx._option_chain_cache[("polygon", "QQQ", date(2025, 5, 16))] = chain_df

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=SellAndHoldAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="QQQ",
        slippage=SlippageModel(market_bps=0), buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    # After selling 1 call at $8 bid, received $800 premium.
    # Cash = 100,000 + 800 = 100,800
    # But portfolio value should be LESS than 100,800 because we owe the option.
    # MTM liability = current mid price * qty * 100 = ~8.25 * 1 * 100 = $825
    # Portfolio = cash - liability = 100,800 - 825 = ~$99,975
    # (Slight loss because we sold at bid $8 but MTM at mid $8.25)
    final_pv = obs.equity[-1]["pv"]
    assert final_pv < 100_800, f"Portfolio should reflect short liability, got {final_pv}"
    assert final_pv < 100_000, f"Selling at bid and marking at mid should show a loss, got {final_pv}"
```

- [ ] **Step 2: Fix MTM for short positions**

In `_positions_market_value_from_cache` and related methods, negative-quantity positions should SUBTRACT from portfolio value (they are liabilities):

```python
    def _positions_market_value_from_cache(self, positions, price_cache):
        total = 0.0
        for (sym,), ps in positions.items():
            multiplier = 100 if ps.asset_type == "options" else 1
            # quantity is negative for shorts → market_value is negative (liability)
            total += ps.quantity * price_cache.get(sym, 0.0) * multiplier
        return total
```

This should already work if `ps.quantity` is negative — the multiplication produces a negative contribution. Verify the `_price_cache_for_bar` method correctly looks up option prices from the chain cache.

- [ ] **Step 3: Run tests, commit**

Run: `.venv/bin/python -m pytest tests/coordinator/services/test_options_fill_fixes.py -v`
Commit: `git add -A && git commit -m "fix(options): MTM correctly reflects short option liability"`

---

### Task 7: Full regression check

**Files:**
- Run all tests

- [ ] **Step 1: Run full engine test suite**

```bash
.venv/bin/python -m pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/services/test_options_fill_fixes.py tests/coordinator/services/test_options_e2e.py -v --tb=short
```

- [ ] **Step 2: Run broader test suite**

```bash
.venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Verify no new failures beyond pre-existing ones.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "test(options): full regression verification for options fill model fixes"
```
