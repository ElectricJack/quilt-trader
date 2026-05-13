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

    from coordinator.api.routes.accounts import router as accounts_router
    app.include_router(accounts_router)

    from coordinator.api.routes.workers import router as workers_router
    app.include_router(workers_router)

    from coordinator.api.routes.algorithms import router as algorithms_router
    app.include_router(algorithms_router)

    from coordinator.api.routes.settings import router as settings_router
    app.include_router(settings_router)

    from coordinator.api.routes.events import router as events_router
    app.include_router(events_router)

    from coordinator.api.websocket import router as ws_router
    app.include_router(ws_router)

    from coordinator.api.routes.github import router as github_router
    app.include_router(github_router)

    import os
    dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
    if os.path.isdir(dashboard_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")

    return app
