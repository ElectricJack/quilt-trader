from enum import Enum
from typing import Any, Optional
from sdk.signals import Signal


class RunnerState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


class AlgorithmRunner:
    def __init__(self, instance_id: str, algorithm: Any, config: dict, restored_state: Optional[dict]) -> None:
        self.instance_id = instance_id
        self._algorithm = algorithm
        self._config = config
        self._restored_state = restored_state
        self.state = RunnerState.STOPPED

    def start(self) -> None:
        self._algorithm.on_start(self._config, self._restored_state)
        self.state = RunnerState.RUNNING

    def stop(self) -> dict:
        final_state = self._algorithm.on_stop()
        self.state = RunnerState.STOPPED
        return final_state

    def tick(self, ctx: Any) -> list[Signal]:
        if self.state != RunnerState.RUNNING:
            raise RuntimeError("Algorithm is not running")
        signals = self._algorithm.on_tick(ctx)
        return signals if signals else []

    def save_state(self) -> dict:
        return self._algorithm.save_state()

    def on_signal_rejected(self, signal: Signal, reason: str) -> None:
        self._algorithm.on_signal_rejected(signal, reason)

    def on_trade_executed(self, signal: Signal, fill: Any) -> None:
        self._algorithm.on_trade_executed(signal, fill)
