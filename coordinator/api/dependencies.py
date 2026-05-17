from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.services.event_bus import EventBus
from coordinator.services.encryption import EncryptionService

if TYPE_CHECKING:
    from coordinator.services.live_feed_manager import LiveFeedManager
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator
    from coordinator.services.backtest_runner import BacktestRunner
    from coordinator.services.data_service import DataService
    from coordinator.services.live_sample_sink import LiveSampleSink
    from coordinator.services.live_finalizer import LiveFinalizer
    from coordinator.services.tick_scheduler import TickScheduler


class ServiceContainer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        encryption: EncryptionService,
        scheduler: Optional[object] = None,
    ) -> None:
        self.session_factory = session_factory
        self.event_bus = event_bus
        self.encryption = encryption
        self.scheduler = scheduler
        self.live_feed_manager: Optional["LiveFeedManager"] = None
        self.live_feed_aggregator: Optional["LiveFeedAggregator"] = None
        self.backtest_runner: Optional["BacktestRunner"] = None
        self.data_service: Optional["DataService"] = None
        self.live_sample_sink: Optional["LiveSampleSink"] = None
        self.live_finalizer: Optional["LiveFinalizer"] = None
        self.tick_scheduler: Optional["TickScheduler"] = None


_container: ServiceContainer | None = None


def set_container(container: ServiceContainer) -> None:
    global _container
    _container = container


def get_container() -> ServiceContainer:
    assert _container is not None, "ServiceContainer not initialized"
    return _container


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    container = get_container()
    async with container.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
