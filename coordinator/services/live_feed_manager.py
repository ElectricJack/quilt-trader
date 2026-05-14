"""Tracks broker-scoped live data subscriptions and their dependent counts.

Mirrors the API of ScraperManager (coordinator/services/scraper_manager.py)
deliberately - same lifecycle pattern (running while >=1 dependent cares).
"""
from __future__ import annotations

from dataclasses import dataclass, field

Key = tuple[str, str]  # (broker, symbol)


@dataclass
class _State:
    running: bool = False
    dependents: set[str] = field(default_factory=set)


class LiveFeedManager:
    def __init__(self) -> None:
        self._states: dict[Key, _State] = {}

    def register(self, broker: str, symbol: str) -> None:
        self._states.setdefault((broker, symbol), _State())

    def is_registered(self, broker: str, symbol: str) -> bool:
        return (broker, symbol) in self._states

    def is_running(self, broker: str, symbol: str) -> bool:
        s = self._states.get((broker, symbol))
        return s.running if s else False

    def start(self, broker: str, symbol: str) -> None:
        s = self._states.get((broker, symbol))
        if s:
            s.running = True

    def stop(self, broker: str, symbol: str) -> None:
        s = self._states.get((broker, symbol))
        if s:
            s.running = False

    def add_dependent(self, broker: str, symbol: str, instance_id: str) -> None:
        s = self._states.get((broker, symbol))
        if s:
            s.dependents.add(instance_id)

    def remove_dependent(self, broker: str, symbol: str, instance_id: str) -> None:
        s = self._states.get((broker, symbol))
        if s:
            s.dependents.discard(instance_id)

    def dependent_count(self, broker: str, symbol: str) -> int:
        s = self._states.get((broker, symbol))
        return len(s.dependents) if s else 0

    def should_stop(self, broker: str, symbol: str) -> bool:
        s = self._states.get((broker, symbol))
        return bool(s and not s.dependents)

    def ensure_running(self, broker: str, symbol: str, instance_id: str) -> None:
        if not self.is_registered(broker, symbol):
            self.register(broker, symbol)
        self.add_dependent(broker, symbol, instance_id)
        if not self.is_running(broker, symbol):
            self.start(broker, symbol)

    def release(self, broker: str, symbol: str, instance_id: str) -> bool:
        self.remove_dependent(broker, symbol, instance_id)
        if self.should_stop(broker, symbol):
            self.stop(broker, symbol)
            return True
        return False
