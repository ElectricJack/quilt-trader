from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from sdk.signals import Signal
from worker.broker_adapter import BrokerAdapter, OrderResult
from worker.context import LiveTickContext
from worker.data_client import DataClient
from worker.runner import AlgorithmRunner

if TYPE_CHECKING:
    from worker.live_observer import LiveObserver


@dataclass
class TradeResult:
    signal: Signal
    order_result: OrderResult


@dataclass
class TickResult:
    timestamp: datetime
    signals_produced: int = 0
    trades_executed: int = 0
    trades_rejected: int = 0
    trade_results: list[TradeResult] = field(default_factory=list)
    decision_log: Optional[dict] = None


class TickProcessor:
    def __init__(self, runner: AlgorithmRunner, broker: BrokerAdapter,
                 data_client: DataClient, coordinator_client: Any,
                 idle_threshold_seconds: int = 60,
                 live_observer: Optional["LiveObserver"] = None,
                 buffer: Any = None,
                 data_deps: Optional[list[dict]] = None) -> None:
        self._runner = runner
        self._broker = broker
        self._data_client = data_client
        self._coordinator = coordinator_client
        self._idle_threshold_seconds = idle_threshold_seconds
        self._live_observer = live_observer
        self._buffer = buffer
        self._data_deps = data_deps or []
        self._silent_tick_count: int = 0
        self._last_active_tick_ts: Optional[datetime] = None
        self._last_idle_tick_emitted_ts: Optional[datetime] = None

    async def _fetch_custom_data(self) -> dict:
        result = {}
        for dep in self._data_deps:
            if dep.get("source") != "custom":
                continue
            fname = dep.get("file")
            if not fname:
                continue
            try:
                df = await self._data_client.get_custom_data(fname)
                result[fname] = df
            except Exception:
                import logging
                logging.getLogger(__name__).warning("Failed to fetch custom data %s", fname, exc_info=True)
        return result

    async def process_tick(self, timestamp: datetime) -> TickResult:
        custom_data = await self._fetch_custom_data()
        ctx = LiveTickContext(
            timestamp=timestamp, mode="live", broker=self._broker,
            data_client=self._data_client, buffer=self._buffer,
            custom_data=custom_data,
        )
        signals = self._runner.tick(ctx)
        result = TickResult(timestamp=timestamp, signals_produced=len(signals))

        serialized_signals = []
        for signal in signals:
            serialized_signals.append(signal.to_dict())
            # Emit signal_produced activity event for each signal
            if hasattr(self._coordinator, "send_activity_event"):
                first_leg = signal.legs[0] if signal.legs else None
                await self._coordinator.send_activity_event(
                    self._runner.instance_id,
                    "signal_produced",
                    severity="info",
                    payload={
                        "symbol": first_leg.symbol if first_leg else None,
                        "side": first_leg.signal_type.value if first_leg else None,
                    },
                )
            approval = await self._coordinator.request_signal_approval(
                instance_id=self._runner.instance_id, signal=signal.to_dict())
            if approval.get("approved"):
                for leg in signal.legs:
                    order_result = self._broker.submit_order(
                        symbol=leg.symbol, side=leg.signal_type.value, quantity=leg.quantity,
                        order_type=leg.order_type.value, limit_price=leg.limit_price, stop_price=leg.stop_price)
                    result.trade_results.append(TradeResult(signal=signal, order_result=order_result))
                    # Emit trade_executed activity event for each leg filled
                    if hasattr(self._coordinator, "send_activity_event"):
                        await self._coordinator.send_activity_event(
                            self._runner.instance_id,
                            "trade_executed",
                            severity="info",
                            payload={
                                "symbol": leg.symbol,
                                "side": leg.signal_type.value,
                                "quantity": leg.quantity,
                                "filled_price": order_result.filled_price,
                            },
                        )
                    # Emit trade_sample to coordinator via LiveObserver
                    if self._live_observer is not None:
                        await self._live_observer.on_trade(trade={
                            "timestamp": timestamp.isoformat(),
                            "symbol": leg.symbol,
                            "asset_type": getattr(leg, "asset_type", "equities"),
                            "side": leg.signal_type.value,
                            "quantity": leg.quantity,
                            "requested_price": leg.limit_price,
                            "fill_price": order_result.filled_price,
                            "slippage_dollars": None,
                            "slippage_bps_applied": None,
                            "fees": getattr(order_result, "fees", 0.0),
                            "fee_breakdown": "{}",
                            "signal_id": getattr(signal, "id", None) or "",
                            "realized_pnl": None,
                        })
                result.trades_executed += 1
                self._runner.on_trade_executed(signal, result.trade_results[-1].order_result)
            else:
                reason = approval.get("reason", "Unknown")
                result.trades_rejected += 1
                self._runner.on_signal_rejected(signal, reason)

        result.decision_log = {
            "instance_id": self._runner.instance_id,
            "timestamp": timestamp.isoformat(),
            "mode": "live",
            "signals_produced": serialized_signals,
        }

        # Emit per-tick equity sample to coordinator via LiveObserver
        if self._live_observer is not None:
            await self._live_observer.on_tick(timestamp=timestamp.isoformat())

        # Emit per-tick activity events
        if hasattr(self._coordinator, "send_activity_event"):
            if result.signals_produced > 0 or result.trades_executed > 0:
                await self._coordinator.send_activity_event(
                    self._runner.instance_id,
                    "tick_processed",
                    severity="info",
                    payload={
                        "signals_produced": result.signals_produced,
                        "trades_executed": result.trades_executed,
                        "trades_rejected": result.trades_rejected,
                    },
                )
                self._silent_tick_count = 0
                self._last_active_tick_ts = timestamp
            else:
                self._silent_tick_count += 1
                if self._last_idle_tick_emitted_ts is None:
                    self._last_idle_tick_emitted_ts = timestamp
                else:
                    elapsed = (timestamp - self._last_idle_tick_emitted_ts).total_seconds()
                    if elapsed >= self._idle_threshold_seconds:
                        await self._coordinator.send_activity_event(
                            self._runner.instance_id,
                            "idle_tick",
                            severity="debug",
                            payload={"silent_ticks": self._silent_tick_count},
                        )
                        self._last_idle_tick_emitted_ts = timestamp

        return result
