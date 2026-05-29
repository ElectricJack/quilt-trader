"""BacktestEngine — observer-driven, persistence-free simulation.

Spec D §3. Conservative-by-default fill model:
- No same-bar fills (signal at bar T → fill at T+1 at earliest).
- Market: next-bar open + slippage.
- Limit: strict cross required (price strictly past limit, not touch).
- Stop / stop-limit: trigger then market/limit on +2 bars.
- Multi-leg: per-leg independent fill timeline.

The engine is intentionally simple-but-pessimistic. See Spec D for full
discussion. The persistence-free design lets two callers consume it:
BacktestRunner (one-shot, persists to BacktestRun) and
ParallelBacktestFeeder (DecisionLog producer for BacktestComparison).
"""
from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

import numpy as np
import pandas as pd

from coordinator.services.asset_services import AssetServiceRegistry, AssetType
from coordinator.services.backtest_tick_context import BacktestTickContext, timeframe_to_seconds
from coordinator.services.backtest_config import SlippageModel, TradingFee
from coordinator.services.validation.cost_model import CostModelProfile, load_named_profile
from sdk.signals import Signal, SignalLeg, SignalType, OrderType

logger = logging.getLogger(__name__)


class UnsupportedAssetTypeError(Exception):
    """Raised when the engine encounters an asset class it doesn't support yet."""


@dataclass
class CancelToken:
    _set: bool = False
    def set(self): self._set = True
    def is_set(self) -> bool: return self._set


@dataclass
class FillRecord:
    timestamp: datetime
    symbol: str
    asset_type: str
    side: str
    quantity: float
    requested_price: float
    fill_price: float
    slippage_dollars: float
    slippage_bps_applied: float
    fees: float
    fee_breakdown: list[dict]
    signal_id: str
    realized_pnl: Optional[float] = None  # Set on closing fills (round-trip)


@dataclass
class EngineSummary:
    total_bars: int
    total_signals: int
    total_fills: int
    final_cash: float
    final_portfolio_value: float


class EngineObserver(Protocol):
    def on_tick(self, sim_time: datetime, ctx_snapshot: dict) -> None: ...
    def on_signals_emitted(self, sim_time: datetime, signals: list[Signal]) -> None: ...
    def on_fill(self, fill: FillRecord) -> None: ...
    def on_signal_rejected(self, sim_time: datetime, signal: Signal, reason: str) -> None: ...
    def on_equity_point(self, sim_time: datetime, portfolio_value: float, cash: float, positions: list[dict]) -> None: ...
    def on_complete(self, summary: EngineSummary) -> None: ...
    def on_error(self, exc: Exception) -> None: ...


@dataclass
class _PendingOrder:
    signal_id: str
    leg: SignalLeg
    scheduled_for_bar_index: int   # Index in clock_series; fill attempted at this bar (and possibly later for stops)
    is_stop_triggered: bool = False  # Stop-to-market two-stage tracking
    created_date: object = None  # date when order was placed (for DAY expiry)
    fill_attempted: bool = False  # True after first fill attempt (used for DAY expiry guard)


@dataclass
class _PositionState:
    quantity: float = 0.0
    avg_price: float = 0.0
    asset_type: str = "equities"


class BacktestEngine:
    def __init__(self, *, config=None):
        self._cost_profile: CostModelProfile | None = None
        if getattr(config, "cost_profile", None):
            self._cost_profile = load_named_profile(config.cost_profile)

    def run(
        self,
        *,
        algorithm,                  # QuiltAlgorithm-like
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
        """Two-pass execution.

        Pass 1: discovery — call `on_start` and one `on_tick` with the warmup bar
        so the algorithm populates `ctx._bars` with every symbol it intends to
        use.  No observers fire; account state is discarded.

        Pass 2: replay — build the canonical union-of-symbol-timelines clock from
        `ctx._bars` (or fall back to the supplied clock_series if pass-1 produced
        no symbols, e.g. scraper-only algorithms).  Reset the context, then run
        `_run_internal` with the real clock.  Observers fire normally; this is
        the canonical execution.
        """
        try:
            self._discovery_pass(
                algorithm=algorithm, ctx=ctx,
                clock_series=clock_series,
                clock_timeframe=clock_timeframe,
                clock_source=clock_source,
                clock_symbol=clock_symbol,
                config=config or {},
            )

            real_clock = self._build_union_clock(ctx._bars)
            if real_clock.empty:
                # Pure scraper-driven algo: fall back to the original clock.
                real_clock = clock_series
            # Always preserve original clock_source / clock_symbol for pass-2.
            # _run_internal uses clock_symbol to decide whether to use the clock
            # bar directly for fill resolution (sym == clock_symbol) or look up
            # the symbol's own bars from the cache (sym != clock_symbol).
            # Using "_union" here would force all fills through the cache lookup
            # path, causing fills to resolve against the wrong bar.
            real_source, real_symbol = clock_source, clock_symbol

            # Reset tick-time state so pass-2 starts from scratch.
            ctx.reset_for_replay()

            self._run_internal(
                algorithm=algorithm, ctx=ctx, clock=real_clock,
                clock_tf=clock_timeframe, clock_source=real_source,
                clock_symbol=real_symbol,
                slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                initial_cash=initial_cash, observer=observer, cancel=cancel_token,
                progress=progress_callback, rng_seed=rng_seed, config=config or {},
            )
        except Exception as exc:
            logger.exception("BacktestEngine.run failed")
            observer.on_error(exc)

    def _discovery_pass(
        self, *, algorithm, ctx, clock_series,
        clock_timeframe, clock_source, clock_symbol,
        config: dict,
    ) -> None:
        """Pass-1 warmup: on_start + one on_tick to populate ctx._bars.

        Observers are NOT called; positions, fills, equity are not recorded.
        If clock_series is empty/None, only `on_start` runs.
        """
        if clock_series is None or len(clock_series) == 0:
            algorithm.on_start(config, None)
            return

        first_bar = clock_series.iloc[0]
        tf_duration = timeframe_to_seconds(clock_timeframe)
        sim_time = (first_bar["timestamp"].to_pydatetime() +
                    pd.Timedelta(seconds=tf_duration).to_pytimedelta())
        ctx.set_sim_time(sim_time)
        algorithm.on_start(config, None)
        try:
            algorithm.on_tick(ctx)
        except Exception:
            # Pass-1 errors are absorbed: pass-2 will surface real errors via
            # the observer.  Pass-1 is just a warmup to populate the bars cache.
            logger.debug(
                "Algorithm raised during pass-1 discovery; continuing",
                exc_info=True,
            )

    def _run_internal(
        self, *, algorithm, ctx, clock, clock_tf, clock_source, clock_symbol,
        slippage, buy_fees, sell_fees, initial_cash, observer, cancel,
        progress, rng_seed, config=None,
    ):
        cash = initial_cash
        positions: dict[tuple, _PositionState] = {}
        pending: list[_PendingOrder] = []
        all_fills: list[FillRecord] = []
        all_signals_count = 0
        tf_duration = timeframe_to_seconds(clock_tf)
        rng = random.Random(rng_seed)

        algorithm.on_start(config if config is not None else {}, None)

        import time as _time
        _t_engine_start = _time.monotonic()
        _n_bars = 0
        self._ts_cache: dict[int, tuple] = {}
        self._asset_registry = AssetServiceRegistry()

        bar_idx = 0
        while bar_idx < len(clock):
            if cancel.is_set():
                logger.info("BacktestEngine cancelled at bar %d", bar_idx)
                return

            _n_bars += 1
            bar = clock.iloc[bar_idx]
            sim_time = (bar["timestamp"].to_pydatetime() +
                        pd.Timedelta(seconds=tf_duration).to_pytimedelta())
            ctx.set_sim_time(sim_time)
            # Update context with current account state for the algorithm's `ctx.positions/cash` reads
            ctx_positions = self._positions_for_context(positions, bar, ctx=ctx, sim_time=sim_time)
            ctx.update_account(
                cash=cash,
                account_value=cash + self._positions_market_value(positions, bar, ctx=ctx, sim_time=sim_time),
                buying_power=cash,
                positions=ctx_positions,
            )

            # ---- 1. Tick the algorithm ----
            observer.on_tick(sim_time, {"cash": cash})
            signals = algorithm.on_tick(ctx) or []

            if signals:
                all_signals_count += len(signals)
                observer.on_signals_emitted(sim_time, signals)
                # Schedule pending orders for the NEXT bar
                for sig in signals:
                    sig_id = str(uuid.uuid4())
                    for leg in sig.legs:
                        bar_date = bar["timestamp"].to_pydatetime().date() if hasattr(bar["timestamp"].to_pydatetime(), 'date') else None
                        pending.append(_PendingOrder(
                            signal_id=sig_id, leg=leg,
                            scheduled_for_bar_index=bar_idx + 1,
                            created_date=bar_date,
                        ))

            # ---- 2. Process pending orders that target THIS bar ----
            still_pending: list[_PendingOrder] = []
            for po in pending:
                if po.scheduled_for_bar_index > bar_idx:
                    still_pending.append(po)
                    continue
                # DAY order expiry: reject before attempting fill if day has changed.
                # Only applies after the first fill attempt — the first attempt
                # always proceeds regardless of date (signal on bar T fills at T+1).
                from sdk.signals import TimeInForce as _TIF
                tif = getattr(po.leg, 'time_in_force', None)
                if tif == _TIF.DAY and po.fill_attempted:
                    order_date = po.created_date
                    bar_ts = bar["timestamp"].to_pydatetime()
                    current_date = bar_ts.date() if hasattr(bar_ts, 'date') else None
                    if order_date is not None and current_date is not None and current_date > order_date:
                        observer.on_signal_rejected(
                            sim_time, Signal(legs=[po.leg]), "day_expired"
                        )
                        continue
                # Resolve the fill bar: prefer the SYMBOL's own data over the
                # clock bar. The clock may be a different symbol (multi-asset
                # algo) or synthetic (scraper-only algo with no market deps).
                # The bars cache is keyed by provider-specific symbol (e.g.
                # "BTC-USD" for yfinance) while leg.symbol is the algorithm-
                # canonical form (e.g. "BTC/USD"). Resolve through the asset
                # registry to match.
                fill_bar = bar
                sym = po.leg.symbol
                if sym != clock_symbol:
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
                        idx = np.searchsorted(ns, cutoff.value, side="right") - 1
                        if idx >= 0:
                            fill_bar = df.iloc[idx]
                        break
                # Try to fill against THIS bar
                po.fill_attempted = True
                fill, advance_for_stop = self._try_fill(
                    po, bar=fill_bar, slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                    cash=cash, positions=positions, rng=rng, sim_time=bar["timestamp"].to_pydatetime(),
                    ctx=ctx,
                )
                if fill is not None:
                    # Buying-power check for buys: paper equities/crypto have no
                    # margin, so a buy that would push cash negative is rejected.
                    # Sells / shorts on existing positions are allowed; opening a
                    # short with no position is also rejected (no margin in v1).
                    if fill.side == "buy":
                        bp_multiplier = self._asset_registry.get_multiplier(fill.symbol)
                        notional_plus_fees = fill.fill_price * fill.quantity * bp_multiplier + fill.fees
                        if notional_plus_fees > cash + 1e-6:
                            observer.on_signal_rejected(
                                sim_time,
                                Signal(legs=[po.leg]),
                                f"insufficient_buying_power: order needs "
                                f"${notional_plus_fees:,.2f} but cash is ${cash:,.2f}",
                            )
                            continue
                    elif fill.side == "sell":
                        # Block accidental short for equities/crypto (can't sell what you don't own).
                        # Options can be sold to open (writing), so allow short sells for options.
                        svc = self._asset_registry.get_service(fill.symbol)
                        if svc.asset_type != AssetType.OPTIONS:
                            key = (fill.symbol,)
                            held = positions.get(key)
                            held_qty = held.quantity if held else 0.0
                            if fill.quantity > held_qty + 1e-9:
                                observer.on_signal_rejected(
                                    sim_time,
                                    Signal(legs=[po.leg]),
                                    f"insufficient_position: sell {fill.quantity} but "
                                    f"holding {held_qty}",
                                )
                                continue
                    cash = self._apply_fill(cash, positions, fill)
                    all_fills.append(fill)
                    observer.on_fill(fill)
                elif advance_for_stop:
                    # Stop triggered this bar — re-schedule for next bar as a market order
                    po.is_stop_triggered = True
                    po.scheduled_for_bar_index = bar_idx + 1
                    still_pending.append(po)
                else:
                    # Options with no price should be rejected immediately,
                    # never carried forward to retry (the chain won't change).
                    svc = self._asset_registry.get_service(po.leg.symbol)
                    if svc.asset_type == AssetType.OPTIONS:
                        observer.on_signal_rejected(
                            sim_time, Signal(legs=[po.leg]), "no_option_price"
                        )
                        continue
                    # Not filled, not stop-trigger — apply TIF-aware expiry
                    from sdk.signals import TimeInForce
                    tif = getattr(po.leg, 'time_in_force', None)
                    if tif is None or tif == TimeInForce.IOC:
                        observer.on_signal_rejected(
                            sim_time, Signal(legs=[po.leg]), "no_fill_within_timeout"
                        )
                    elif tif == TimeInForce.DAY:
                        po.scheduled_for_bar_index = bar_idx + 1
                        still_pending.append(po)
                    elif tif == TimeInForce.GTC:
                        po.scheduled_for_bar_index = bar_idx + 1
                        still_pending.append(po)
                    else:
                        observer.on_signal_rejected(
                            sim_time, Signal(legs=[po.leg]), f"unknown_time_in_force:{tif}"
                        )
            pending = still_pending

            # Check if algorithm requested order cancellation
            if getattr(ctx, '_cancel_orders_requested', False):
                for po in pending:
                    observer.on_signal_rejected(sim_time, Signal(legs=[po.leg]), "cancelled_by_algorithm")
                pending = []
                ctx._cancel_orders_requested = False

            # ---- 2b. Expire options at expiration ----
            cash, positions = self._settle_expired_options(
                cash, positions, sim_time, ctx, observer, all_fills,
            )
            # Also cancel pending orders for expired contracts
            still_pending2 = []
            for po in pending:
                svc = self._asset_registry.get_service(po.leg.symbol)
                settlement = svc.handle_expiry(
                    po.leg.symbol, quantity=1, avg_price=0.0,
                    sim_time=sim_time, ctx=ctx,
                )
                if settlement is not None:
                    observer.on_signal_rejected(sim_time, Signal(legs=[po.leg]), "contract_expired")
                    continue
                still_pending2.append(po)
            pending = still_pending2

            # ---- 3. Mark-to-market equity point ----
            mtm_value = cash + self._positions_market_value(positions, bar, ctx=ctx, sim_time=sim_time)
            # Full position snapshot is expensive — only compute it every 50 bars
            # or on the last bar. The observer still gets the portfolio value on every bar.
            if bar_idx % 50 == 0 or bar_idx == len(clock) - 1:
                snapshot = self._positions_snapshot(positions, bar, ctx=ctx, sim_time=sim_time)
            else:
                snapshot = []
            observer.on_equity_point(sim_time, mtm_value, cash, snapshot)

            if progress is not None and bar_idx % 100 == 0:
                progress(bar_idx / max(len(clock), 1))

            bar_idx += 1

        _t_engine_total = _time.monotonic() - _t_engine_start
        logger.info("[ENGINE_TIMING] bars=%d total=%.3fs", _n_bars, _t_engine_total)
        algorithm.on_stop()

        # Reject any remaining pending GTC orders at end of backtest
        for po in pending:
            observer.on_signal_rejected(
                sim_time, Signal(legs=[po.leg]), "gtc_expired_end_of_backtest"
            )

        observer.on_complete(EngineSummary(
            total_bars=len(clock),
            total_signals=all_signals_count,
            total_fills=len(all_fills),
            final_cash=cash,
            final_portfolio_value=cash + self._positions_market_value(positions, clock.iloc[-1], ctx=ctx, sim_time=sim_time),
        ))

    # ---- Fill simulation ----

    def _try_fill(
        self, po: _PendingOrder, *, bar, slippage: SlippageModel,
        buy_fees, sell_fees, cash, positions, rng, sim_time, ctx=None,
    ) -> tuple[Optional[FillRecord], bool]:
        """Returns (fill_or_none, stop_triggered).

        stop_triggered=True means this is a stop order that triggered this bar
        and should be re-scheduled as a market order for the next bar.
        """
        leg = po.leg
        ot = leg.order_type
        side = "buy" if leg.signal_type in (SignalType.BUY, SignalType.BUY_TO_COVER) else "sell"
        fees_list = buy_fees if side == "buy" else sell_fees

        # Cost-profile override: when a named profile is loaded, replace the
        # legacy slippage/fees with the profile-resolved bundle.  The legacy
        # path (no profile) is unchanged.
        if self._cost_profile is not None:
            venue = ""
            if ctx is not None and hasattr(ctx, "broker_name"):
                venue = getattr(ctx, "broker_name", "") or ""
            bundle = self._cost_profile.resolve(
                venue=venue,
                asset_type=getattr(po.leg, "asset_type", None) or "equity",
                symbol=po.leg.symbol,
            )
            slippage = bundle.slippage
            fees_list = bundle.fees

        if ot == OrderType.MARKET or po.is_stop_triggered:
            return self._fill_market(po, bar, side, slippage, fees_list, rng, sim_time, ctx=ctx), False

        if ot == OrderType.LIMIT:
            return self._fill_limit(po, bar, side, slippage, fees_list, sim_time, ctx=ctx), False

        if ot == OrderType.STOP:
            triggered = self._stop_triggered(po, bar, side)
            if triggered:
                return None, True
            return None, False

        if ot == OrderType.STOP_LIMIT:
            triggered = self._stop_triggered(po, bar, side)
            if triggered:
                # Convert to a pending limit at limit_price for the next bar
                # Engine reschedules; mark by setting order_type to limit via leg replacement.
                # Trick: caller advances via stop_triggered=True path, but we want a LIMIT next bar
                # not a market. Special handling: we set is_stop_triggered=True but the engine's
                # rescheduling will hit MARKET in the next iteration. To preserve limit semantics,
                # we replace the leg's order_type to LIMIT (Python dataclass mutation).
                po.leg = SignalLeg(
                    symbol=leg.symbol, signal_type=leg.signal_type, quantity=leg.quantity,
                    asset_type=leg.asset_type, order_type=OrderType.LIMIT,
                    limit_price=leg.limit_price, stop_price=None,
                )
                return None, True
            return None, False

        raise ValueError(f"Unsupported order_type: {ot}")

    def _lookup_option_price(self, contract_symbol: str, side: str, ctx) -> float | None:
        """Find bid/ask for a contract.

        Priority: direct contract bar data on disk (most accurate) → chain cache.
        """
        # Best path: load the contract's own bar data directly
        if ctx._data_service is not None:
            sym = contract_symbol.removeprefix("O:")
            df = ctx._data_service.load_market_data(
                ctx._default_source or "polygon", sym, "1day",
            )
            if df is not None and not df.empty:
                ts = pd.to_datetime(df["timestamp"])
                if ts.dt.tz is not None:
                    ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
                cutoff = pd.Timestamp(ctx._sim_time_now)
                if cutoff.tz is not None:
                    cutoff = cutoff.tz_convert("UTC").tz_localize(None)
                visible = df[ts <= cutoff]
                if not visible.empty:
                    bar = visible.iloc[-1]
                    close = float(bar["close"])
                    if "bid" in bar.index and "ask" in bar.index and pd.notna(bar["bid"]):
                        return float(bar["ask"]) if side == "buy" else float(bar["bid"])
                    from coordinator.services.options_math import estimate_spread
                    vol = int(bar.get("volume", 0))
                    spread = estimate_spread(close, vol)
                    return (close + spread / 2) if side == "buy" else max(0.0, close - spread / 2)

        # Fallback: search chain cache
        for key, df in ctx._option_chain_cache.items():
            if df is None or (hasattr(df, 'empty') and df.empty):
                continue
            for col in ("ticker", "symbol"):
                if col in df.columns:
                    match = df[df[col] == contract_symbol]
                    if not match.empty:
                        row = match.iloc[0]
                        return float(row.get("ask", 0)) if side == "buy" else float(row.get("bid", 0))

        # Cache miss: try loading chain from data_service.
        # Extract underlying from OCC symbol, e.g. "O:SPY260117C00450000" → "SPY"
        if ctx._data_service is not None and hasattr(ctx._data_service, "build_chain"):
            underlying = self._extract_underlying(contract_symbol)
            if underlying:
                exp = ctx._sim_time_now.date() if ctx._sim_time_now else None
                source = ctx._default_source or "polygon"
                try:
                    chain_df = ctx._data_service.build_chain(source, underlying, exp, as_of=ctx._sim_time_now)
                    if chain_df is not None and not chain_df.empty:
                        cache_key = (source, underlying, exp)
                        ctx._option_chain_cache[cache_key] = chain_df
                        for col in ("ticker", "symbol"):
                            if col in chain_df.columns:
                                match_rows = chain_df[chain_df[col] == contract_symbol]
                                if not match_rows.empty:
                                    row = match_rows.iloc[0]
                                    return float(row.get("ask", 0)) if side == "buy" else float(row.get("bid", 0))
                except Exception:
                    logger.debug("Failed to build chain for %s", underlying, exc_info=True)

        return None

    @staticmethod
    def _extract_underlying(contract_symbol: str) -> str | None:
        """Extract the underlying ticker from an OCC-style option symbol.

        Examples:
            "O:SPY260117C00450000" → "SPY"
            "O:AAPL250620P00175000" → "AAPL"
            "SPY260117C00450000" → "SPY"
        """
        sym = contract_symbol
        if sym.startswith("O:"):
            sym = sym[2:]
        # OCC format: SYMBOL + 6-digit date + C/P + 8-digit strike
        # Find where the date digits start (first digit after letters)
        for i, ch in enumerate(sym):
            if ch.isdigit():
                return sym[:i] if i > 0 else None
        return None

    def _fill_market(self, po, bar, side, slippage, fees_list, rng, sim_time, ctx=None) -> Optional[FillRecord]:
        leg = po.leg

        # Options: use contract bid/ask from cached option chain data
        svc = self._asset_registry.get_service(leg.symbol)
        if svc.asset_type == AssetType.OPTIONS and ctx is not None:
            option_price = self._lookup_option_price(leg.symbol, side, ctx)
            if option_price is not None and option_price > 0:
                if slippage.market_bps > 0:
                    sign = 1 if side == "buy" else -1
                    option_price += option_price * (slippage.market_bps / 10000) * sign
                fees, breakdown = self._compute_fees(leg, option_price, fees_list, order_type=OrderType.MARKET)
                ts = bar["timestamp"]
                if hasattr(ts, "to_pydatetime"):
                    ts = ts.to_pydatetime()
                return FillRecord(
                    timestamp=ts,
                    symbol=leg.symbol, asset_type="options", side=side, quantity=leg.quantity,
                    requested_price=option_price, fill_price=option_price,
                    slippage_dollars=0.0, slippage_bps_applied=slippage.market_bps,
                    fees=fees, fee_breakdown=breakdown, signal_id=po.signal_id,
                )
            else:
                return None  # No option price — don't fall through to equity

        if slippage.use_bar_range:
            fill_price = rng.uniform(float(bar["low"]), float(bar["high"]))
            slip_bps = abs(fill_price - float(bar["open"])) / float(bar["open"]) * 10000
        else:
            sign = 1 if side == "buy" else -1
            slip = float(bar["open"]) * (slippage.market_bps / 10000) * sign
            fill_price = float(bar["open"]) + slip
            slip_bps = slippage.market_bps

        # Volume impact, optionally additive
        if slippage.volume_impact_bps_per_pct > 0 and float(bar["volume"]) > 0:
            pct_consumed = (leg.quantity / float(bar["volume"])) * 100
            extra_bps = pct_consumed * slippage.volume_impact_bps_per_pct
            extra_sign = 1 if side == "buy" else -1
            fill_price += float(bar["open"]) * (extra_bps / 10000) * extra_sign
            slip_bps += extra_bps

        requested = float(bar["open"])
        fees, breakdown = self._compute_fees(leg, fill_price, fees_list, order_type=OrderType.MARKET)
        return FillRecord(
            timestamp=bar["timestamp"].to_pydatetime(), symbol=leg.symbol,
            asset_type=leg.asset_type, side=side, quantity=leg.quantity,
            requested_price=requested, fill_price=fill_price,
            slippage_dollars=abs(fill_price - requested) * leg.quantity,
            slippage_bps_applied=slip_bps, fees=fees, fee_breakdown=breakdown,
            signal_id=po.signal_id,
        )

    def _fill_limit(self, po, bar, side, slippage, fees_list, sim_time, ctx=None) -> Optional[FillRecord]:
        leg = po.leg
        limit = leg.limit_price
        if limit is None:
            return None

        # Options path: use contract bid/ask from chain data
        svc = self._asset_registry.get_service(leg.symbol)
        if svc.asset_type == AssetType.OPTIONS and ctx is not None:
            option_price = self._lookup_option_price(leg.symbol, side, ctx)
            if option_price is not None:
                if side == "buy" and option_price <= limit:
                    fill_price = min(option_price, limit)
                elif side == "sell" and option_price >= limit:
                    fill_price = max(option_price, limit)
                else:
                    return None
                fees, breakdown = self._compute_fees(leg, fill_price, fees_list, order_type=OrderType.LIMIT)
                return FillRecord(
                    timestamp=bar["timestamp"].to_pydatetime() if hasattr(bar["timestamp"], "to_pydatetime") else bar["timestamp"],
                    symbol=leg.symbol, asset_type="options", side=side, quantity=leg.quantity,
                    requested_price=limit, fill_price=fill_price,
                    slippage_dollars=0.0, slippage_bps_applied=0.0,
                    fees=fees, fee_breakdown=breakdown, signal_id=po.signal_id,
                )
            else:
                return None  # No option price — don't fall through to equity

        low, high = float(bar["low"]), float(bar["high"])
        # STRICT cross only — see Spec D §3 conservative-by-default rule
        if side == "buy":
            if not (low < limit):
                return None
        else:
            if not (high > limit):
                return None
        fill_price = limit  # Limits never fill worse than the limit
        if slippage.limit_bps > 0:
            # Edge case: model brokers that take a small bps even on limits
            sign = 1 if side == "buy" else -1
            fill_price += limit * (slippage.limit_bps / 10000) * sign
        fees, breakdown = self._compute_fees(leg, fill_price, fees_list, order_type=OrderType.LIMIT)
        return FillRecord(
            timestamp=bar["timestamp"].to_pydatetime(), symbol=leg.symbol,
            asset_type=leg.asset_type, side=side, quantity=leg.quantity,
            requested_price=limit, fill_price=fill_price,
            slippage_dollars=abs(fill_price - limit) * leg.quantity,
            slippage_bps_applied=slippage.limit_bps, fees=fees, fee_breakdown=breakdown,
            signal_id=po.signal_id,
        )

    def _stop_triggered(self, po, bar, side) -> bool:
        leg = po.leg
        stop = leg.stop_price
        if stop is None:
            return False
        low, high = float(bar["low"]), float(bar["high"])
        return low <= stop <= high

    def _compute_fees(self, leg: SignalLeg, fill_price: float, fees_list: list[TradingFee],
                      order_type: OrderType) -> tuple[float, list[dict]]:
        is_maker = order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
        is_taker = not is_maker
        total = 0.0
        breakdown = []
        for tf in fees_list:
            applies = (tf.maker and is_maker) or (tf.taker and is_taker)
            if not applies:
                continue
            f = tf.flat_fee + fill_price * leg.quantity * tf.percent_fee
            total += f
            breakdown.append({
                "flat_fee": tf.flat_fee, "percent_fee": tf.percent_fee,
                "maker": tf.maker, "taker": tf.taker, "computed": f,
            })
        return total, breakdown

    # ---- Position tracking ----

    def _apply_fill(self, cash: float, positions: dict, fill: FillRecord) -> float:
        key = (fill.symbol,)  # Equities/crypto key for v1
        ps = positions.get(key) or _PositionState(asset_type=fill.asset_type)
        multiplier = self._asset_registry.get_multiplier(fill.symbol)
        notional = fill.fill_price * fill.quantity * multiplier

        if fill.side == "buy":
            if ps.quantity < 0:
                # Buy-to-close: covering a short position
                close_qty = min(fill.quantity, abs(ps.quantity))
                realized = (ps.avg_price - fill.fill_price) * close_qty * multiplier - fill.fees
                fill.realized_pnl = realized
                ps.quantity += fill.quantity
                if ps.quantity == 0:
                    ps.avg_price = 0.0
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
                if ps.quantity == 0:
                    ps.avg_price = 0.0
                cash += notional - fill.fees
            else:
                # Sell-to-open: creating/adding to short position
                existing_short = abs(ps.quantity)
                new_short = existing_short + fill.quantity
                if existing_short == 0:
                    ps.avg_price = fill.fill_price
                else:
                    ps.avg_price = (ps.avg_price * existing_short + fill.fill_price * fill.quantity) / new_short
                ps.quantity = -new_short  # negative = short
                fill.realized_pnl = None  # No realized PnL on opening
                cash += notional - fill.fees  # Receive premium

        positions[key] = ps
        if ps.quantity == 0:
            del positions[key]
        return cash

    def _settle_expired_options(
        self, cash, positions, sim_time, ctx, observer, all_fills,
    ) -> tuple[float, dict]:
        """Auto-settle any expired position via the asset service layer.

        Delegates per-asset expiry rules to AssetService.handle_expiry —
        equities/crypto/indexes return None (no expiry); options return
        a Settlement with intrinsic value and realized PnL.
        """
        for (sym,), ps in list(positions.items()):
            svc = self._asset_registry.get_service(sym)
            settlement = svc.handle_expiry(
                sym, quantity=ps.quantity, avg_price=ps.avg_price,
                sim_time=sim_time, ctx=ctx,
            )
            if settlement is None:
                continue
            multiplier = svc.get_multiplier()
            # Cash movement: ITM positions move cash; OTM (fill_price=0) doesn't.
            if settlement.fill_price > 0:
                cash_delta = settlement.fill_price * settlement.quantity * multiplier
                cash = cash + cash_delta if settlement.side == "sell" else cash - cash_delta
            fill = FillRecord(
                timestamp=sim_time,
                symbol=sym, asset_type=svc.asset_type.value,
                side=settlement.side, quantity=settlement.quantity,
                requested_price=settlement.fill_price, fill_price=settlement.fill_price,
                slippage_dollars=0.0, slippage_bps_applied=0.0,
                fees=0.0, fee_breakdown=[],
                signal_id=f"expiry-{sym}",
                realized_pnl=settlement.realized_pnl,
            )
            all_fills.append(fill)
            observer.on_fill(fill)
            del positions[(sym,)]

        return cash, positions

    def _lookup_symbol_close(self, sym: str, sim_time, ctx, fallback_bar=None) -> float:
        """Return the most recent close price for `sym` from its OWN data series.

        Resolves the algorithm-canonical symbol form (e.g. "BTC/USD") to the
        provider-specific cache key (e.g. "BTC-USD") via the asset registry,
        then does a searchsorted lookup at or before sim_time.

        Returns 0.0 if no cache entry matches the symbol or if sim_time precedes
        the symbol's first bar.  The CALLER is responsible for falling back to
        cost basis on 0.0 — this function must NOT return the clock-bar's close
        (post-P3), since the clock bar belongs to a different symbol in a
        multi-asset backtest and using it would silently mis-price positions
        (the 2026-05-27 25-50× equity-inflation bug).

        The `fallback_bar` parameter is kept for call-site compatibility but
        intentionally unused.
        """
        if ctx is None:
            return 0.0
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
            ns, closes = self._ts_cache[cache_key]
            cutoff = pd.Timestamp(sim_time)
            if cutoff.tz is not None:
                cutoff = cutoff.tz_convert("UTC").tz_localize(None)
            idx = np.searchsorted(ns, cutoff.value, side="right") - 1
            if idx >= 0:
                return float(closes[idx])
            return 0.0  # sim_time precedes symbol's first bar
        return 0.0  # no cache entry matched the symbol

    def _lookup_option_mtm_price(self, sym: str, ctx) -> float | None:
        """Get current mid-price for an option from cached chain data."""
        if ctx is None:
            return None
        for key, df in ctx._option_chain_cache.items():
            if df is None or (hasattr(df, 'empty') and df.empty):
                continue
            for col in ("ticker", "symbol"):
                if col in df.columns:
                    match = df[df[col] == sym]
                    if not match.empty:
                        row = match.iloc[0]
                        bid = float(row.get("bid", 0))
                        ask = float(row.get("ask", 0))
                        if bid > 0 and ask > 0:
                            return (bid + ask) / 2
                        return ask if ask > 0 else bid
        return None

    def _positions_market_value(self, positions: dict, bar, ctx=None, sim_time=None) -> float:
        total = 0.0
        for (sym,), ps in positions.items():
            svc = self._asset_registry.get_service(sym)
            # Options need the chain-cache mid-price; equities can use the bar close.
            if svc.asset_type == AssetType.OPTIONS:
                option_price = self._lookup_option_mtm_price(sym, ctx)
                price = option_price if option_price is not None else ps.avg_price
            else:
                price = self._lookup_symbol_close(sym, sim_time, ctx, bar)
                if price == 0.0:
                    # No MtM available — mark at cost basis (matches the existing
                    # _positions_for_context fallback).
                    price = ps.avg_price
            total += ps.quantity * price * svc.get_multiplier()
        return total

    def _positions_snapshot(self, positions: dict, bar, ctx=None, sim_time=None) -> list[dict]:
        result = []
        for k, ps in positions.items():
            sym = k[0]
            svc = self._asset_registry.get_service(sym)
            if svc.asset_type == AssetType.OPTIONS:
                option_price = self._lookup_option_mtm_price(sym, ctx)
                current_price = option_price if option_price is not None else ps.avg_price
            else:
                current_price = self._lookup_symbol_close(sym, sim_time, ctx, bar)
                if current_price == 0.0:
                    current_price = ps.avg_price
            multiplier = svc.get_multiplier()
            result.append({
                "symbol": sym, "quantity": ps.quantity, "avg_price": ps.avg_price,
                "current_price": current_price,
                "market_value": ps.quantity * current_price * multiplier,
                "asset_type": ps.asset_type,
            })
        return result

    @staticmethod
    def _build_union_clock(bars: dict[tuple, pd.DataFrame]) -> pd.DataFrame:
        """Merge all symbol timelines into a sorted, deduplicated clock DataFrame.

        Each row carries real OHLCV data from whichever symbol contributed that
        timestamp first (never zeros).  Used by the engine to tick through a
        unified timeline when backtesting multi-asset algorithms.

        Timestamps are normalised to UTC-naive (tz stripped after UTC conversion)
        to allow deduplication across DataFrames with heterogeneous timezone
        representations.  Callers that need tz-aware timestamps must re-localise.
        """
        if not bars:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        frames = []
        for key, df in bars.items():
            if df is not None and not df.empty:
                sub = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
                sub["timestamp"] = pd.to_datetime(sub["timestamp"])
                if sub["timestamp"].dt.tz is not None:
                    sub["timestamp"] = sub["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
                frames.append(sub)
        if not frames:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="first")
        combined = combined.sort_values("timestamp").reset_index(drop=True)
        return combined

    def _positions_for_context(self, positions: dict, bar=None, ctx=None, sim_time=None) -> dict:
        """Convert internal state to sdk.models.Position dict the algorithm reads via ctx.positions."""
        from sdk.models import Position
        out: dict = {}
        for (sym,), ps in positions.items():
            svc = self._asset_registry.get_service(sym)
            if svc.asset_type == AssetType.OPTIONS:
                option_price = self._lookup_option_mtm_price(sym, ctx)
                current_price = option_price if option_price is not None else ps.avg_price
            else:
                current_price = self._lookup_symbol_close(sym, sim_time, ctx, bar)
            if current_price == 0.0:
                current_price = ps.avg_price
            try:
                out[sym] = Position(
                    symbol=sym,
                    quantity=ps.quantity,
                    avg_cost=ps.avg_price,
                    current_price=current_price,
                    asset_type=ps.asset_type,
                )
            except TypeError:
                # Defensive fallback if Position signature changes
                out[sym] = {
                    "symbol": sym,
                    "quantity": ps.quantity,
                    "avg_cost": ps.avg_price,
                    "current_price": current_price,
                    "asset_type": ps.asset_type,
                }
        return out
