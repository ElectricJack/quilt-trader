from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.services.event_bus import EventBus
from coordinator.services.encryption import EncryptionService


class ServiceContainer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        encryption: EncryptionService,
    ) -> None:
        self.session_factory = session_factory
        self.event_bus = event_bus
        self.encryption = encryption


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
