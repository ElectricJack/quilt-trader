from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sdk.signals import Signal
from worker.broker_adapter import BrokerAdapter, OrderResult
from worker.context import LiveTickContext
from worker.data_client import DataClient
from worker.runner import AlgorithmRunner


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
                 data_client: DataClient, coordinator_client: Any) -> None:
        self._runner = runner
        self._broker = broker
        self._data_client = data_client
        self._coordinator = coordinator_client

    async def process_tick(self, timestamp: datetime) -> TickResult:
        ctx = LiveTickContext(timestamp=timestamp, mode="live", broker=self._broker, data_client=self._data_client)
        signals = self._runner.tick(ctx)
        result = TickResult(timestamp=timestamp, signals_produced=len(signals))

        serialized_signals = []
        for signal in signals:
            serialized_signals.append(signal.to_dict())
            approval = await self._coordinator.request_signal_approval(
                instance_id=self._runner.instance_id, signal=signal.to_dict())
            if approval.get("approved"):
                for leg in signal.legs:
                    order_result = self._broker.submit_order(
                        symbol=leg.symbol, side=leg.signal_type.value, quantity=leg.quantity,
                        order_type=leg.order_type.value, limit_price=leg.limit_price, stop_price=leg.stop_price)
                    result.trade_results.append(TradeResult(signal=signal, order_result=order_result))
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
        return result
