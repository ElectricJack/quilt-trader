from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base
from coordinator.services.event_bus import EventBus
from coordinator.services.encryption import EncryptionService
from coordinator.api.dependencies import ServiceContainer, set_container


def create_app(
    database_url: str = "sqlite+aiosqlite:///data/quilt_trader.db",
    encryption_key: str = "default-dev-key-32-bytes-long!!!",
) -> FastAPI:
    engine = create_engine(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = create_session_factory(engine)
        event_bus = EventBus()
        encryption = EncryptionService(encryption_key)
        container = ServiceContainer(session_factory, event_bus, encryption)
        set_container(container)
        yield
        await engine.dispose()

    app = FastAPI(title="QuiltTrader", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health():
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    return app
