import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScraperState:
    schedule: str
    running: bool = False
    dependents: set[str] = field(default_factory=set)


class ScraperManager:
    def __init__(self) -> None:
        self._scrapers: dict[str, ScraperState] = {}

    def register(self, name: str, schedule: str) -> None:
        self._scrapers[name] = ScraperState(schedule=schedule)

    def is_registered(self, name: str) -> bool:
        return name in self._scrapers

    def is_running(self, name: str) -> bool:
        state = self._scrapers.get(name)
        return state.running if state else False

    def start(self, name: str) -> None:
        state = self._scrapers.get(name)
        if state:
            state.running = True

    def stop(self, name: str) -> None:
        state = self._scrapers.get(name)
        if state:
            state.running = False

    def add_dependent(self, name: str, instance_id: str) -> None:
        state = self._scrapers.get(name)
        if state:
            state.dependents.add(instance_id)

    def remove_dependent(self, name: str, instance_id: str) -> None:
        state = self._scrapers.get(name)
        if state:
            state.dependents.discard(instance_id)

    def dependent_count(self, name: str) -> int:
        state = self._scrapers.get(name)
        return len(state.dependents) if state else 0

    def should_stop(self, name: str) -> bool:
        state = self._scrapers.get(name)
        return state.running and len(state.dependents) == 0 if state else False

    def ensure_running(self, name: str, instance_id: str) -> None:
        self.add_dependent(name, instance_id)
        if not self.is_running(name):
            self.start(name)

    def release(self, name: str, instance_id: str) -> bool:
        self.remove_dependent(name, instance_id)
        if self.should_stop(name):
            self.stop(name)
            return True
        return False
