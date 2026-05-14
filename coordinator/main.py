import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base, Setting
from coordinator.services.event_bus import EventBus
from coordinator.services.encryption import EncryptionService
from coordinator.api.dependencies import ServiceContainer, set_container

logger = logging.getLogger(__name__)


def create_app(
    database_url: str = "sqlite+aiosqlite:///data/quilt_trader.db",
    encryption_key: str = "default-dev-key-32-bytes-long!!!",
) -> FastAPI:
    engine = create_engine(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Idempotent column add for existing DBs
            try:
                await conn.execute(text(
                    "ALTER TABLE market_data_downloads ADD COLUMN progress_message TEXT"
                ))
            except Exception:
                pass  # column already exists
            try:
                await conn.execute(text(
                    "ALTER TABLE market_data_downloads ADD COLUMN current_symbol_pct REAL"
                ))
            except Exception:
                pass  # column already exists
        session_factory = create_session_factory(engine)
        event_bus = EventBus()
        encryption = EncryptionService(encryption_key)

        # Scheduler
        from coordinator.services.scheduler import SchedulerService
        scheduler = SchedulerService()
        scheduler.start()

        # Data service
        from coordinator.services.data_service import DataService
        data_svc = DataService(market_data_dir="data/market", custom_data_dir="data/custom")
        from coordinator.api.routes.data import set_data_service
        set_data_service(data_svc)

        # Scraper registry — auto-discover packages/, register cron jobs
        import os
        from coordinator.services.scraper_engine import ScraperEngine
        from coordinator.services.scraper_registry import ScraperRegistry
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scraper_engine = ScraperEngine(
            packages_dir=os.path.join(repo_root, "packages"),
            output_dir=os.path.join(repo_root, "data", "custom"),
        )
        scraper_registry = ScraperRegistry(
            engine=scraper_engine,
            scheduler=scheduler,
            packages_dir=os.path.join(repo_root, "packages"),
            configs_dir=os.path.join(repo_root, "data", "scraper_configs"),
            session_factory=session_factory,
        )
        scraper_registry.discover_and_register()
        from coordinator.api.routes.scrapers import set_registry
        set_registry(scraper_registry)

        # Download manager — read provider credentials from settings and wire up
        http_client = httpx.AsyncClient(timeout=30.0)
        providers: dict = {}

        async with session_factory() as session:
            polygon_row = (
                await session.execute(
                    select(Setting).where(Setting.key == "polygon_api_key")
                )
            ).scalar_one_or_none()
            polygon_key: str | None = None
            if polygon_row is not None:
                try:
                    polygon_key = encryption.decrypt(polygon_row.value)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to decrypt polygon_api_key: %s", e)

            theta_user_row = (
                await session.execute(
                    select(Setting).where(Setting.key == "theta_data_username")
                )
            ).scalar_one_or_none()
            theta_pass_row = (
                await session.execute(
                    select(Setting).where(Setting.key == "theta_data_password")
                )
            ).scalar_one_or_none()
            theta_username: str | None = None
            theta_password: str | None = None
            if theta_user_row is not None and theta_pass_row is not None:
                try:
                    theta_username = encryption.decrypt(theta_user_row.value)
                    theta_password = encryption.decrypt(theta_pass_row.value)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to decrypt theta credentials: %s", e)

        if polygon_key:
            from coordinator.services.data_providers.polygon import PolygonProvider
            providers["polygon"] = PolygonProvider(api_key=polygon_key, http_client=http_client, min_request_interval_s=13.0)
            logger.info("PolygonProvider wired into DownloadManager")
        else:
            logger.warning(
                "polygon_api_key not configured; PolygonProvider will be unavailable. "
                "Set it in Settings to enable downloads."
            )

        if theta_username and theta_password:
            from coordinator.services.data_providers.theta import ThetaDataProvider
            providers["theta"] = ThetaDataProvider(
                username=theta_username, password=theta_password, http_client=http_client
            )
            logger.info("ThetaDataProvider wired into DownloadManager")
        else:
            logger.info("Theta credentials not configured; ThetaDataProvider will be unavailable.")

        from coordinator.services.download_manager import DownloadManager
        download_manager = DownloadManager(
            session_factory=session_factory,
            data_service=data_svc,
            providers=providers,
        )
        from coordinator.api.routes.data import set_download_manager
        set_download_manager(download_manager)

        n_recovered = await download_manager.recover_orphaned_downloads()
        if n_recovered > 0:
            logger.info("Recovered %d orphaned download row(s) from previous run", n_recovered)

        container = ServiceContainer(session_factory, event_bus, encryption, scheduler)

        from coordinator.services.live_feed_manager import LiveFeedManager
        from coordinator.services.live_feed_aggregator import LiveFeedAggregator
        container.live_feed_manager = LiveFeedManager()
        container.live_feed_aggregator = LiveFeedAggregator(session_factory)
        await container.live_feed_aggregator.start()

        set_container(container)
        yield

        if container.live_feed_aggregator:
            await container.live_feed_aggregator.stop()
        await http_client.aclose()
        scheduler.shutdown()
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

    from coordinator.api.routes.data import router as data_router
    app.include_router(data_router)

    from coordinator.api.routes.runs import router as runs_router
    app.include_router(runs_router)

    from coordinator.api.routes.cash_flows import router as cash_flows_router
    app.include_router(cash_flows_router)

    from coordinator.api.routes.backtests import router as backtests_router
    app.include_router(backtests_router)

    from coordinator.api.routes.scrapers import router as scrapers_router
    app.include_router(scrapers_router)

    from coordinator.api.routes.portfolio import router as portfolio_router
    app.include_router(portfolio_router)

    from coordinator.api.routes.positions import router as positions_router
    app.include_router(positions_router)

    from coordinator.api.routes.trades import router as trades_router
    app.include_router(trades_router)

    from coordinator.api.routes.alerts import router as alerts_router
    app.include_router(alerts_router)

    from coordinator.api.routes import brokers as brokers_routes
    app.include_router(brokers_routes.router)

    from coordinator.api.routes import live_subscriptions as live_subs_routes
    app.include_router(live_subs_routes.router)

    from coordinator.api.routes import options_chain as options_chain_routes
    app.include_router(options_chain_routes.router)

    import os
    dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
    if os.path.isdir(dashboard_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")

    return app
