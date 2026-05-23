import asyncio
import logging
from enum import Enum
from typing import Any, Optional
from sdk.signals import Signal


class RunnerState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


class _AlgoLogShipper(logging.Handler):
    """Captures algorithm log records and ships them to the coordinator."""

    def __init__(self, agent: Any, instance_id: str, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._agent = agent
        self._instance_id = instance_id
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                self._agent.send_algo_log(
                    instance_id=self._instance_id,
                    logger_name=record.name,
                    level=record.levelname,
                    message=record.getMessage(),
                ),
                self._loop,
            )
        except Exception:
            pass


class AlgorithmRunner:
    def __init__(self, instance_id: str, algorithm: Any, config: dict,
                 restored_state: Optional[dict],
                 agent: Optional[Any] = None,
                 loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self.instance_id = instance_id
        self._algorithm = algorithm
        self._config = config
        self._restored_state = restored_state
        self.state = RunnerState.STOPPED
        self._log_shipper: Optional[_AlgoLogShipper] = None

        if agent is not None and loop is not None and hasattr(agent, "send_algo_log"):
            pkg = getattr(algorithm.__class__, "__module__", "").split(".")[0]
            if pkg:
                shipper = _AlgoLogShipper(agent=agent, instance_id=instance_id, loop=loop)
                shipper.setLevel(logging.INFO)
                logging.getLogger(pkg).addHandler(shipper)
                self._log_shipper = shipper

    def start(self) -> None:
        self._algorithm.on_start(self._config, self._restored_state)
        self.state = RunnerState.RUNNING

    def stop(self) -> dict:
        final_state = self._algorithm.on_stop()
        self.state = RunnerState.STOPPED
        if self._log_shipper is not None:
            pkg = getattr(self._algorithm.__class__, "__module__", "").split(".")[0]
            if pkg:
                logging.getLogger(pkg).removeHandler(self._log_shipper)
            self._log_shipper = None
        return final_state

    def tick(self, ctx: Any) -> list[Signal]:
        if self.state != RunnerState.RUNNING:
            raise RuntimeError("Algorithm is not running")
        signals = self._algorithm.on_tick(ctx)
        return signals if signals else []

    def save_state(self) -> dict:
        return self._algorithm.save_state()

    def on_position_closed(self, symbol: str, reason: str, details: dict | None = None) -> None:
        self._algorithm.on_position_closed(symbol, reason, details)

    def on_signal_rejected(self, signal: Signal, reason: str) -> None:
        self._algorithm.on_signal_rejected(signal, reason)

    def on_trade_executed(self, signal: Signal, fill: Any) -> None:
        self._algorithm.on_trade_executed(signal, fill)
