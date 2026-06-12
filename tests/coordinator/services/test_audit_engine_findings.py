"""Failing tests for the 2026-06-11 correctness audit — backtest engine findings.

Each test asserts the CORRECT behavior and fails today because the documented
bug exists (see docs/superpowers/research/2026-06-11-correctness-audit-findings.md
§1). They must pass once the corresponding fix lands. No production code is
modified by this file.

Findings covered: F1, F1b (off-clock fill flavor of F1), F2 (both flip
directions), F3, F7.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from coordinator.services.backtest_engine_v2 import (
    BacktestEngine,
    FillRecord,
    _PositionState,
)
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import BacktestConfig, SlippageModel
from coordinator.services.asset_services.registry import AssetServiceRegistry
from sdk.signals import Signal, SignalLeg, SignalType, OrderType


# ---------------------------------------------------------------------------
# Harness (mirrors tests/coordinator/services/test_backtest_engine_two_pass_clock.py)
# ---------------------------------------------------------------------------

def _make_bar_df(timestamps, base=100.0):
    n = len(timestamps)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps),
        "open": [base] * n,
        "high": [base * 1.01] * n,
        "low": [base * 0.99] * n,
        "close": [base] * n,
        "volume": [1.0] * n,
    })


def _make_flat_bar_df(timestamps, levels):
    """One bar per timestamp with open=high=low=close=level — makes the fill
    bar unambiguous regardless of the slippage formula (≤ ~0.1%)."""
    assert len(timestamps) == len(levels)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps),
        "open": [float(v) for v in levels],
        "high": [float(v) for v in levels],
        "low": [float(v) for v in levels],
        "close": [float(v) for v in levels],
        "volume": [1000.0] * len(levels),
    })


def _make_close_series_df(timestamps, closes):
    """Bars with DISTINCT closes per day; open/high/low kept consistent."""
    assert len(timestamps) == len(closes)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps),
        "open": [float(c) for c in closes],
        "high": [float(c) * 1.001 for c in closes],
        "low": [float(c) * 0.999 for c in closes],
        "close": [float(c) for c in closes],
        "volume": [1000.0] * len(closes),
    })


class _RecordingObserver:
    """Captures every event for assertion."""
    def __init__(self):
        self.events = []
    def on_tick(self, *a, **k): self.events.append(("tick", a, k))
    def on_signals_emitted(self, *a, **k): self.events.append(("sig", a, k))
    def on_signal_rejected(self, *a, **k): self.events.append(("rej", a, k))
    def on_fill(self, *a, **k): self.events.append(("fill", a, k))
    def on_error(self, *a, **k): self.events.append(("err", a, k))
    def on_summary(self, *a, **k): self.events.append(("sum", a, k))
    def on_equity_point(self, *a, **k): pass
    def on_complete(self, *a, **k): self.events.append(("complete", a, k))


def _cancel_stub():
    return type("X", (), {"is_set": lambda self: False})()


def _make_engine(start="2024-01-01", end="2024-01-05", cash=100_000.0):
    return BacktestEngine(config=BacktestConfig(
        start=start, end=end, initial_cash=cash, cost_profile=None,
    ))


def _micro_engine():
    """Engine prepared for direct calls to private helpers (the engine
    normally initialises these on entry to _run_internal)."""
    eng = _make_engine()
    eng._asset_registry = AssetServiceRegistry()
    eng._ts_cache = {}
    return eng


def _make_fill(symbol, side, quantity, price, asset_type="equities"):
    return FillRecord(
        timestamp=datetime(2024, 1, 2),
        symbol=symbol, asset_type=asset_type, side=side,
        quantity=float(quantity),
        requested_price=float(price), fill_price=float(price),
        slippage_dollars=0.0, slippage_bps_applied=0.0,
        fees=0.0, fee_breakdown=[], signal_id="test-signal",
    )


# ---------------------------------------------------------------------------
# F1 — one-bar look-ahead in _lookup_symbol_close
# ---------------------------------------------------------------------------

def test_f1_lookup_symbol_close_excludes_bar_stamped_at_sim_time():
    """F1: one-bar look-ahead in _lookup_symbol_close (backtest_engine_v2.py:916).

    Bars are stamped at OPEN time; a 1day bar stamped at T is only known at
    T+1day. The engine sets sim_time = bar.timestamp + tf_duration, so sim_time
    equals the NEXT bar's open stamp. _lookup_symbol_close does
    `searchsorted(ns, cutoff, side="right") - 1` with cutoff = sim_time, which
    includes the bar stamped exactly at sim_time — a bar whose interval has NOT
    elapsed — returning the next bar's close. At sim_time = day1 + 1day only
    day1's bar is complete, so the correct answer is day1's close (100.0); the
    buggy code returns day2's close (200.0).
    """
    bars = {
        ("yfinance", "ETH-USD", "1day"): _make_close_series_df(
            ["2024-01-01", "2024-01-02", "2024-01-03"],
            closes=[100.0, 200.0, 300.0],
        ),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="yfinance",
    )
    eng = _micro_engine()

    eth_df = bars[("yfinance", "ETH-USD", "1day")]
    day2_bar = eth_df.iloc[1]
    # sim_time = day1 timestamp + 1day == day2's open stamp.
    sim_time = datetime(2024, 1, 2)
    price = eng._lookup_symbol_close(
        sym="ETHUSD",
        sim_time=sim_time,
        ctx=ctx,
        fallback_bar=day2_bar,
    )
    # Only day1's bar (close=100) has fully elapsed at this sim_time. The
    # buggy inclusive cutoff returns day2's close (200) — a look-ahead.
    assert price == pytest.approx(100.0), (
        f"F1 look-ahead: expected day1 close 100.0 (only completed bar at "
        f"sim_time {sim_time}), got {price}"
    )


# ---------------------------------------------------------------------------
# F1b — off-clock fill executes one bar ahead of documented "next bar open"
# ---------------------------------------------------------------------------

def test_f1b_off_clock_market_order_fills_at_next_bar_not_two_bars_ahead():
    """F1b: off-clock fill-bar resolution looks ahead (backtest_engine_v2.py:320-326).

    The fill-bar lookup for a non-clock symbol uses the same inclusive
    `searchsorted(..., side="right") - 1` at cutoff = sim_time as F1, so a
    market order signalled at bar T fills using bar T+2's prices instead of
    the documented "next bar (T+1) open" (module docstring / Spec D §3).
    ETH bars are flat per day at 2500/2600/2700/2800 → a signal on day1's tick
    must fill at day2's bar ≈ 2600; the buggy code fills at day3 ≈ 2700.
    """
    class _BuyEthAlgo:
        def on_start(self, config, restored_state):
            self._fired = False

        def on_tick(self, ctx):
            # Reference both symbols in pass-1 so the union clock has both
            # symbols' timestamps.
            try:
                ctx.market_data("BTCUSD", n=1)
                ctx.market_data("ETHUSD", n=1)
            except Exception:
                pass
            if self._fired:
                return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="ETHUSD",
                signal_type=SignalType.BUY,
                quantity=1.0,
                asset_type="crypto",
                order_type=OrderType.MARKET,
            )])]

        def on_stop(self): pass

    days = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    bars = {
        # BTC (clock symbol) flat at 42000 so a wrong-symbol fill is detectable.
        ("yfinance", "BTC-USD", "1day"): _make_flat_bar_df(
            days, [42_000.0] * 4,
        ),
        # ETH flat per day, distinct level each day → the fill bar is unambiguous.
        ("yfinance", "ETH-USD", "1day"): _make_flat_bar_df(
            days, [2_500.0, 2_600.0, 2_700.0, 2_800.0],
        ),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=100_000.0,
        default_source="yfinance",
    )
    obs = _RecordingObserver()
    eng = _make_engine(start=days[0], end=days[-1])
    eng.run(
        algorithm=_BuyEthAlgo(), ctx=ctx,
        clock_series=bars[("yfinance", "BTC-USD", "1day")],
        clock_timeframe="1day", clock_source="yfinance",
        clock_symbol="BTC-USD",
        slippage=SlippageModel(),  # default 5 bps market slippage
        buy_fees=[], sell_fees=[],
        initial_cash=100_000.0,
        observer=obs,
        cancel_token=_cancel_stub(),
    )

    fills = [e[1][0] for e in obs.events if e[0] == "fill"]
    assert len(fills) >= 1, "expected at least one fill"
    fill = fills[0]
    # Signal at day1's tick → fill at day2's bar (2600 flat). Default 5 bps
    # slippage keeps the fill within (2595, 2615). The buggy inclusive cutoff
    # resolves day3's bar (≈2700); a wrong-symbol fill would be ≈42000.
    assert 2_595 < fill.fill_price < 2_615, (
        f"F1b: signal at day1 must fill at day2's bar (~2600), "
        f"got {fill.fill_price} (day3 bar is 2700 — one-bar look-ahead)"
    )


# ---------------------------------------------------------------------------
# F2 — position flips keep stale avg_price in _apply_fill
# ---------------------------------------------------------------------------

def test_f2_short_to_long_flip_resets_avg_price_to_fill_price():
    """F2: short→long flip keeps stale avg_price (backtest_engine_v2.py:799-801).

    _apply_fill only resets avg_price when the post-fill quantity == 0. A buy
    that covers a short AND flips long (short -5, buy 8 → long +3) must realize
    PnL on the closed 5 at the old short basis and set avg_price = fill price
    (the new long's cost basis). The buggy code keeps the old short avg_price
    (100.0) for the new long.
    """
    eng = _micro_engine()
    key = ("AAPL",)
    positions = {
        key: _PositionState(quantity=-5.0, avg_price=100.0, asset_type="equities"),
    }
    fill = _make_fill("AAPL", side="buy", quantity=8.0, price=90.0)

    cash = eng._apply_fill(10_000.0, positions, fill)

    ps = positions[key]
    assert ps.quantity == pytest.approx(3.0), (
        f"expected flip to long +3, got quantity {ps.quantity}"
    )
    # Realized PnL on the 5 covered shorts: (100 - 90) * 5 = 50 (multiplier 1).
    assert fill.realized_pnl == pytest.approx(50.0), (
        f"expected realized 50.0 on the covered 5 shorts, got {fill.realized_pnl}"
    )
    # The new long's basis is the flip fill price — NOT the old short basis.
    assert ps.avg_price == pytest.approx(90.0), (
        f"F2 flip: new long basis must be the fill price 90.0, "
        f"got stale short avg_price {ps.avg_price}"
    )
    # Cash sanity: buy 8 @ 90 with no fees costs 720.
    assert cash == pytest.approx(10_000.0 - 720.0)


def test_f2_long_to_short_flip_resets_avg_price_to_fill_price():
    """F2: long→short flip keeps stale avg_price (backtest_engine_v2.py:818).

    `ps.quantity -= fill.quantity` can cross zero for options (equity
    overselling is rejected earlier, but option sells are not). A sell that
    closes a long AND flips short (long +5, sell 8 → short -3) must realize
    PnL on the closed 5 at the old long basis and set avg_price = fill price
    for the new short. The buggy code keeps the old long avg_price (100.0).
    """
    eng = _micro_engine()
    occ = "AAPL250620C00100000"  # options path: flips are reachable for options
    key = (occ,)
    positions = {
        key: _PositionState(quantity=5.0, avg_price=100.0, asset_type="options"),
    }
    fill = _make_fill(occ, side="sell", quantity=8.0, price=110.0,
                      asset_type="options")

    eng._apply_fill(1_000_000.0, positions, fill)

    ps = positions[key]
    assert ps.quantity == pytest.approx(-3.0), (
        f"expected flip to short -3, got quantity {ps.quantity}"
    )
    # Realized on the closed 5 longs: (110 - 100) * 5 * 100 (option multiplier).
    assert fill.realized_pnl == pytest.approx(5_000.0), (
        f"expected realized 5000.0 on the closed 5 longs, got {fill.realized_pnl}"
    )
    # The new short's basis is the flip fill price — NOT the old long basis.
    assert ps.avg_price == pytest.approx(110.0), (
        f"F2 flip: new short basis must be the fill price 110.0, "
        f"got stale long avg_price {ps.avg_price}"
    )


# ---------------------------------------------------------------------------
# F3 — union clock rows mix symbols' OHLC (keep="first")
# ---------------------------------------------------------------------------

def test_f3_clock_symbol_fill_uses_clock_symbols_own_ohlc():
    """F3: union-clock rows carry another symbol's OHLC (backtest_engine_v2.py:1053).

    _build_union_clock concatenates all symbol frames and drop_duplicates on
    timestamp with keep="first" — the surviving row's OHLC belongs to whichever
    frame was inserted into ctx._bars first. Clock-symbol fills use the clock
    row directly (line ~300), so with ETH's frame inserted before BTC's, a BUY
    of the clock symbol BTC fills at ETH's ~2500 instead of BTC's ~42000.

    The bars dict is keyed by canonical symbols ("BTCUSD"), matching how
    BacktestRunner builds it (backtest_runner.py:362-393 keys bars by the
    manifest dep symbol, and clock_symbol comes from that same key) — so
    leg.symbol == clock_symbol and the engine takes the clock-row fill path.
    """
    class _BuyBtcAlgo:
        def on_start(self, config, restored_state):
            self._fired = False

        def on_tick(self, ctx):
            # Touch both symbols in pass-1 (mirrors the existing two-pass
            # tests) so the union clock is rebuilt from ctx._bars.
            try:
                ctx.market_data("BTCUSD", n=1)
                ctx.market_data("ETHUSD", n=1)
            except Exception:
                pass
            if self._fired:
                return []
            self._fired = True
            # leg.symbol equals clock_symbol (both canonical, as in
            # production) so the engine takes the clock-row fill path
            # (line ~300) rather than the cache lookup.
            return [Signal(legs=[SignalLeg(
                symbol="BTCUSD",
                signal_type=SignalType.BUY,
                quantity=0.1,
                asset_type="crypto",
                order_type=OrderType.MARKET,
            )])]

        def on_stop(self): pass

    days = ["2024-01-01", "2024-01-02", "2024-01-03"]
    # ETH inserted FIRST: keep="first" makes every shared-timestamp union row
    # carry ETH's OHLC. Keys use canonical symbols, as BacktestRunner does.
    bars = {
        ("yfinance", "ETHUSD", "1day"): _make_flat_bar_df(days, [2_500.0] * 3),
        ("yfinance", "BTCUSD", "1day"): _make_flat_bar_df(days, [42_000.0] * 3),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=100_000.0,
        default_source="yfinance",
    )
    obs = _RecordingObserver()
    eng = _make_engine(start=days[0], end=days[-1])
    eng.run(
        algorithm=_BuyBtcAlgo(), ctx=ctx,
        clock_series=bars[("yfinance", "BTCUSD", "1day")],
        clock_timeframe="1day", clock_source="yfinance",
        clock_symbol="BTCUSD",
        slippage=SlippageModel(),
        buy_fees=[], sell_fees=[],
        initial_cash=100_000.0,
        observer=obs,
        cancel_token=_cancel_stub(),
    )

    fills = [e[1][0] for e in obs.events if e[0] == "fill"]
    assert len(fills) >= 1, "expected at least one fill"
    fill = fills[0]
    # The clock symbol BTC must fill at BTC's own bar (~42000) — never at
    # ETH's (~2500) just because ETH's frame happened to be first in ctx._bars.
    assert 41_000 < fill.fill_price < 43_500, (
        f"F3: BTC (clock symbol) must fill at BTC's own OHLC ~42000, "
        f"got {fill.fill_price} (ETH's bar is 2500 — union row mixed symbols)"
    )


# ---------------------------------------------------------------------------
# F7 — option expiry settles at post-expiry underlying price
# ---------------------------------------------------------------------------

def test_f7_option_expiry_settles_at_expiry_day_underlying_close():
    """F7: expiry settlement uses post-expiry underlying close (backtest_engine_v2.py:839-875).

    _settle_expired_options → OptionsAssetService.handle_expiry resolves the
    underlying with an inclusive at-or-before-sim_time bar lookup. The engine
    first detects expiry on the bar AFTER expiry day, where sim_time equals
    the day-after-expiry bar's open stamp — so the lookup returns the close of
    the bar after expiry (same root cause as F1, at the expiry boundary).
    Underlying closes 100 on expiry day and 130 the day after: a long call
    strike 105 must settle WORTHLESS (intrinsic 0 at the 100 close); the buggy
    code settles ITM at 130 - 105 = 25.
    """
    occ = "AAPL240102C00105000"  # call, strike 105, expires 2024-01-02
    bars = {
        ("polygon", "AAPL", "1day"): _make_close_series_df(
            ["2024-01-01", "2024-01-02", "2024-01-03"],
            closes=[95.0, 100.0, 130.0],  # 100 on expiry day, 130 the day after
        ),
    }
    ctx = BacktestTickContext(
        bars=dict(bars), positions={}, cash=10_000.0,
        default_source="polygon",
    )
    eng = _micro_engine()
    obs = _RecordingObserver()
    all_fills: list = []
    positions = {
        (occ,): _PositionState(quantity=1.0, avg_price=2.0, asset_type="options"),
    }
    # The engine ticks the bar stamped on expiry day (2024-01-02) with
    # sim_time = bar.timestamp + 1day = 2024-01-03 — the first tick where
    # sim_date > expiration, so settlement runs here.
    sim_time = datetime(2024, 1, 3)
    ctx.set_sim_time(sim_time)

    cash_after, positions_after = eng._settle_expired_options(
        10_000.0, positions, sim_time, ctx, obs, all_fills,
    )

    assert len(all_fills) == 1, "expected exactly one expiry settlement fill"
    settle = all_fills[0]
    assert (occ,) not in positions_after, "expired position must be removed"
    # At sim_time 2024-01-03 only the expiry-day bar (close 100) is complete;
    # strike 105 call is OTM → settles worthless, no cash movement, realized
    # PnL = full premium loss: -(2.0 * 1 * 100) = -200.
    assert settle.fill_price == pytest.approx(0.0), (
        f"F7: call strike 105 vs expiry-day close 100 must settle worthless, "
        f"got intrinsic {settle.fill_price} (post-expiry close 130 → 25 ITM)"
    )
    assert cash_after == pytest.approx(10_000.0), (
        f"F7: worthless expiry must not move cash, got {cash_after}"
    )
    assert settle.realized_pnl == pytest.approx(-200.0), (
        f"F7: realized must be the lost premium -200.0, got {settle.realized_pnl}"
    )
