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

import pandas as pd

from coordinator.services.backtest_tick_context import BacktestTickContext, timeframe_to_seconds
from coordinator.services.backtest_config import SlippageModel, TradingFee
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


@dataclass
class _PositionState:
    quantity: float = 0.0
    avg_price: float = 0.0
    asset_type: str = "equities"


class BacktestEngine:
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
    ) -> None:
        try:
            self._run_internal(
                algorithm=algorithm, ctx=ctx, clock=clock_series,
                clock_tf=clock_timeframe, clock_source=clock_source, clock_symbol=clock_symbol,
                slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                initial_cash=initial_cash, observer=observer, cancel=cancel_token,
                progress=progress_callback, rng_seed=rng_seed,
            )
        except Exception as exc:
            logger.exception("BacktestEngine.run failed")
            observer.on_error(exc)

    def _run_internal(
        self, *, algorithm, ctx, clock, clock_tf, clock_source, clock_symbol,
        slippage, buy_fees, sell_fees, initial_cash, observer, cancel,
        progress, rng_seed,
    ):
        cash = initial_cash
        positions: dict[tuple, _PositionState] = {}
        pending: list[_PendingOrder] = []
        all_fills: list[FillRecord] = []
        all_signals_count = 0
        tf_duration = timeframe_to_seconds(clock_tf)
        rng = random.Random(rng_seed)

        # Wrap algorithm lifecycle in try/except so errors propagate via observer
        algorithm.on_start({}, None)

        for bar_idx in range(len(clock)):
            if cancel.is_set():
                logger.info("BacktestEngine cancelled at bar %d", bar_idx)
                return

            bar = clock.iloc[bar_idx]
            sim_time = (bar["timestamp"].to_pydatetime() +
                        pd.Timedelta(seconds=tf_duration).to_pytimedelta())
            ctx.set_sim_time(sim_time)
            # Update context with current account state for the algorithm's `ctx.positions/cash` reads
            ctx_positions = self._positions_for_context(positions, bar)
            ctx.update_account(
                cash=cash,
                account_value=cash + self._positions_market_value(positions, bar),
                buying_power=cash,
                positions=ctx_positions,
            )

            # ---- 1. Tick the algorithm ----
            observer.on_tick(sim_time, {"cash": cash})
            signals = algorithm.on_tick(ctx) or []

            if signals:
                # Validate options asset_type — fast fail per Spec D §12
                for sig in signals:
                    for leg in sig.legs:
                        if leg.asset_type == "options":
                            raise UnsupportedAssetTypeError(
                                f"Options backtest not yet supported (leg: {leg.symbol}). "
                                f"Tracked as a follow-up; see Spec D §12."
                            )
                all_signals_count += len(signals)
                observer.on_signals_emitted(sim_time, signals)
                # Schedule pending orders for the NEXT bar
                for sig in signals:
                    sig_id = str(uuid.uuid4())
                    for leg in sig.legs:
                        pending.append(_PendingOrder(
                            signal_id=sig_id, leg=leg,
                            scheduled_for_bar_index=bar_idx + 1,
                        ))

            # ---- 2. Process pending orders that target THIS bar ----
            still_pending: list[_PendingOrder] = []
            for po in pending:
                if po.scheduled_for_bar_index > bar_idx:
                    still_pending.append(po)
                    continue
                # Try to fill against THIS bar
                fill, advance_for_stop = self._try_fill(
                    po, bar=bar, slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                    cash=cash, positions=positions, rng=rng, sim_time=bar["timestamp"].to_pydatetime(),
                )
                if fill is not None:
                    cash = self._apply_fill(cash, positions, fill)
                    all_fills.append(fill)
                    observer.on_fill(fill)
                elif advance_for_stop:
                    # Stop triggered this bar — re-schedule for next bar as a market order
                    po.is_stop_triggered = True
                    po.scheduled_for_bar_index = bar_idx + 1
                    still_pending.append(po)
                else:
                    # Not filled, not stop-trigger — apply expiry (v1 = 1 bar)
                    observer.on_signal_rejected(
                        sim_time, Signal(legs=[po.leg]), "no_fill_within_timeout"
                    )
            pending = still_pending

            # ---- 3. Mark-to-market equity point ----
            mtm_value = cash + self._positions_market_value(positions, bar)
            observer.on_equity_point(
                sim_time, mtm_value, cash, self._positions_snapshot(positions, bar),
            )

            if progress is not None and bar_idx % 100 == 0:
                progress(bar_idx / max(len(clock), 1))

        algorithm.on_stop()

        observer.on_complete(EngineSummary(
            total_bars=len(clock),
            total_signals=all_signals_count,
            total_fills=len(all_fills),
            final_cash=cash,
            final_portfolio_value=cash + self._positions_market_value(positions, clock.iloc[-1]),
        ))

    # ---- Fill simulation ----

    def _try_fill(
        self, po: _PendingOrder, *, bar, slippage: SlippageModel,
        buy_fees, sell_fees, cash, positions, rng, sim_time,
    ) -> tuple[Optional[FillRecord], bool]:
        """Returns (fill_or_none, stop_triggered).

        stop_triggered=True means this is a stop order that triggered this bar
        and should be re-scheduled as a market order for the next bar.
        """
        leg = po.leg
        ot = leg.order_type
        side = "buy" if leg.signal_type in (SignalType.BUY, SignalType.BUY_TO_COVER) else "sell"
        fees_list = buy_fees if side == "buy" else sell_fees

        if ot == OrderType.MARKET or po.is_stop_triggered:
            return self._fill_market(po, bar, side, slippage, fees_list, rng, sim_time), False

        if ot == OrderType.LIMIT:
            return self._fill_limit(po, bar, side, slippage, fees_list, sim_time), False

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

    def _fill_market(self, po, bar, side, slippage, fees_list, rng, sim_time) -> FillRecord:
        leg = po.leg
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

    def _fill_limit(self, po, bar, side, slippage, fees_list, sim_time) -> Optional[FillRecord]:
        leg = po.leg
        limit = leg.limit_price
        if limit is None:
            return None
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
        notional = fill.fill_price * fill.quantity
        if fill.side == "buy":
            # Weighted average price update
            total_qty = ps.quantity + fill.quantity
            if total_qty == 0:
                ps.avg_price = 0.0
            else:
                ps.avg_price = (ps.avg_price * ps.quantity + fill.fill_price * fill.quantity) / total_qty
            ps.quantity = total_qty
            cash -= notional + fill.fees
        else:  # sell
            # Realized PnL on the sold portion
            realized = (fill.fill_price - ps.avg_price) * fill.quantity - fill.fees
            fill.realized_pnl = realized
            ps.quantity -= fill.quantity
            if ps.quantity == 0:
                ps.avg_price = 0.0
            cash += notional - fill.fees
        positions[key] = ps
        if ps.quantity == 0:
            del positions[key]
        return cash

    def _positions_market_value(self, positions: dict, bar) -> float:
        # v1 simplification: use the clock bar's close as the price proxy for ALL held positions.
        # Multi-symbol with different clocks would need per-symbol lookup — out of scope for v1.
        close = float(bar["close"])
        return sum(ps.quantity * close for ps in positions.values())

    def _positions_snapshot(self, positions: dict, bar) -> list[dict]:
        close = float(bar["close"])
        return [
            {"symbol": k[0], "quantity": ps.quantity, "avg_price": ps.avg_price,
             "current_price": close, "market_value": ps.quantity * close,
             "asset_type": ps.asset_type}
            for k, ps in positions.items()
        ]

    def _positions_for_context(self, positions: dict, bar=None) -> dict:
        """Convert internal state to sdk.models.Position dict the algorithm reads via ctx.positions.

        The SDK's Position dataclass uses `avg_cost` and `current_price` (not `avg_price`).
        We use the current bar's close as the current_price proxy. If no bar is provided
        (edge case), we fall back to avg_price.
        """
        from sdk.models import Position
        close = float(bar["close"]) if bar is not None else None
        out: dict = {}
        for (sym,), ps in positions.items():
            current_price = close if close is not None else ps.avg_price
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
