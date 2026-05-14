from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sdk.context import TickContext
    from sdk.models import TradeFill
    from sdk.signals import Signal


class QuiltAlgorithm:
    """Base class that all trading algorithms must implement."""

    def __init__(self) -> None:
        self._pending_notifications: list[dict] = []

    def on_start(self, config: dict, restored_state: Optional[dict]) -> None:
        raise NotImplementedError

    def on_tick(self, ctx: TickContext) -> list[Signal]:
        raise NotImplementedError

    def on_stop(self) -> dict:
        raise NotImplementedError

    def save_state(self) -> dict:
        raise NotImplementedError

    def on_signal_rejected(self, signal: Signal, reason: str) -> None:
        pass

    def on_trade_executed(self, signal: Signal, fill: TradeFill) -> None:
        pass

    def notify(self, event_name: str, message: str, data: Optional[dict] = None) -> None:
        self._pending_notifications.append({
            "event_name": event_name,
            "message": message,
            "data": data,
        })

    def drain_notifications(self) -> list[dict]:
        events = list(self._pending_notifications)
        self._pending_notifications.clear()
        return events
