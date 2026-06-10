import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Account, Base, Setting
from coordinator.services.event_bus import EventBus
from coordinator.services.encryption import EncryptionService
from coordinator.api.dependencies import ServiceContainer, set_container

logger = logging.getLogger(__name__)


def create_app(
    database_url: str = "sqlite+aiosqlite:///data/quilt_trader.db",
    encryption_key: str = "default-dev-key-32-bytes-long!!!",
    engine_kwargs: dict | None = None,
) -> FastAPI:
    engine = create_engine(database_url, **(engine_kwargs or {}))

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
            try:
                await conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS data_goals ("
                    "id TEXT PRIMARY KEY, name TEXT NOT NULL, goal_type TEXT NOT NULL, "
                    "config JSON NOT NULL, status TEXT NOT NULL DEFAULT 'active', "
                    "phase TEXT NOT NULL DEFAULT 'discovering', "
                    "discovered_contracts JSON, discovery_progress TEXT, "
                    "total_items INTEGER NOT NULL DEFAULT 0, completed_items INTEGER NOT NULL DEFAULT 0, "
                    "failed_items INTEGER NOT NULL DEFAULT 0, last_processed_at TIMESTAMP, "
                    "error_message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                ))
            except Exception:
                pass
            for col in ("phase", "discovered_contracts", "discovery_progress", "terminal_symbols"):
                try:
                    await conn.execute(text(f"ALTER TABLE data_goals ADD COLUMN {col} TEXT"))
                except Exception:
                    pass
            # Add missing columns to positions table
            for col, dtype in [
                ("owner_instance_id", "TEXT"),
                ("strategy_type", "TEXT"),
                ("position_intent", "TEXT"),
                ("remaining_quantity", "REAL"),
                ("cost_basis_lots", "JSON"),
            ]:
                try:
                    await conn.execute(text(f"ALTER TABLE positions ADD COLUMN {col} {dtype}"))
                except Exception:
                    pass
            for col, dtype in [
                ("last_attempt_at", "TIMESTAMP"),
                ("attempts_today", "INTEGER NOT NULL DEFAULT 0"),
                ("attempts_day", "DATE"),
            ]:
                try:
                    await conn.execute(text(f"ALTER TABLE scrapers ADD COLUMN {col} {dtype}"))
                except Exception:
                    pass
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

            # Polygon paid-tier override. Free tier = 5 calls/min = 13s interval.
            # Stocks Starter = unlimited but with per-second caps. The user can
            # set these settings to override the conservative defaults.
            polygon_interval_row = (
                await session.execute(
                    select(Setting).where(Setting.key == "polygon_min_request_interval_s")
                )
            ).scalar_one_or_none()
            polygon_concurrency_row = (
                await session.execute(
                    select(Setting).where(Setting.key == "polygon_concurrency")
                )
            ).scalar_one_or_none()

        polygon_interval_s = 13.0  # free-tier default
        polygon_concurrency = 1     # free-tier default
        try:
            if polygon_interval_row is not None:
                polygon_interval_s = max(0.0, float(polygon_interval_row.value))
        except (TypeError, ValueError):
            logger.warning(
                "polygon_min_request_interval_s setting %r is not a number; using default %.1f",
                getattr(polygon_interval_row, "value", None), polygon_interval_s,
            )
        try:
            if polygon_concurrency_row is not None:
                polygon_concurrency = max(1, int(polygon_concurrency_row.value))
        except (TypeError, ValueError):
            logger.warning(
                "polygon_concurrency setting %r is not an int; using default %d",
                getattr(polygon_concurrency_row, "value", None), polygon_concurrency,
            )

        if polygon_key:
            from coordinator.services.data_providers.polygon import PolygonProvider
            providers["polygon"] = PolygonProvider(
                api_key=polygon_key,
                http_client=http_client,
                min_request_interval_s=polygon_interval_s,
            )
            logger.info(
                "PolygonProvider wired into DownloadManager (interval=%.1fs, concurrency=%d)",
                polygon_interval_s, polygon_concurrency,
            )
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

        # yfinance — free historical provider for indices (VIX, SPX), equities, crypto
        try:
            from coordinator.services.data_providers.yfinance_provider import YFinanceProvider
            providers["yfinance"] = YFinanceProvider()
            logger.info("YFinanceProvider wired into DownloadManager (historical only)")
        except ImportError:
            logger.info("yfinance not installed; YFinanceProvider unavailable")

        # Build Tradier provider from first Tradier account
        tradier_provider = None
        try:
            from coordinator.services.data_providers.tradier import TradierProvider
            async with session_factory() as _sess:
                _tradier_acct = (await _sess.execute(
                    select(Account).where(Account.broker_type == "tradier").limit(1)
                )).scalar_one_or_none()
                if _tradier_acct:
                    import json as _json
                    _creds = _json.loads(encryption.decrypt(_tradier_acct.credentials))
                    tradier_provider = TradierProvider(
                        access_token=_creds["access_token"],
                        sandbox=(_tradier_acct.environment != "live"),
                    )
                    logger.info("Tradier data provider initialized from account %s", _tradier_acct.name)
        except Exception:
            logger.warning("Failed to initialize Tradier data provider", exc_info=True)

        # Build Alpaca provider from first Alpaca account
        alpaca_hist_provider = None
        try:
            from coordinator.services.data_providers.alpaca import AlpacaProvider
            async with session_factory() as _sess:
                _alpaca_acct = (await _sess.execute(
                    select(Account).where(Account.broker_type == "alpaca").limit(1)
                )).scalar_one_or_none()
                if _alpaca_acct:
                    import json as _json
                    _creds = _json.loads(encryption.decrypt(_alpaca_acct.credentials))
                    alpaca_hist_provider = AlpacaProvider(
                        api_key=_creds["api_key"],
                        secret_key=_creds["secret_key"],
                    )
                    logger.info("Alpaca data provider initialized from account %s", _alpaca_acct.name)
        except Exception:
            logger.warning("Failed to initialize Alpaca data provider", exc_info=True)

        if tradier_provider is not None:
            providers["tradier"] = tradier_provider
        if alpaca_hist_provider is not None:
            providers["alpaca"] = alpaca_hist_provider

        from coordinator.services.download_manager import DownloadManager
        download_manager = DownloadManager(
            session_factory=session_factory,
            data_service=data_svc,
            providers=providers,
            # Allow polygon concurrency to be overridden by Setting if the
            # user upgraded to a paid tier. Other providers keep their
            # built-in defaults from DownloadManager._DEFAULT_PROVIDER_CONCURRENCY.
            provider_concurrency={"polygon": polygon_concurrency},
        )
        from coordinator.api.routes.data import set_download_manager
        set_download_manager(download_manager)

        n_recovered = await download_manager.recover_orphaned_downloads()

        # -------------------------------------------------------------------
        # Dataset framework wiring: QuotaTracker, FMPAdapter, DatasetJobDispatcher
        # -------------------------------------------------------------------
        from coordinator.services.datasets.quota import QuotaTracker
        from coordinator.services.datasets.providers.fmp import FMPAdapter
        from coordinator.services.datasets.storage import DatasetService, set_default_service
        from coordinator.services.download_job import DatasetJobDispatcher
        from zoneinfo import ZoneInfo
        from pathlib import Path as _Path
        import coordinator.services.datasets.providers.fmp_datasets  # noqa: F401

        async with session_factory() as _ds:
            _fmp_key_row = (await _ds.execute(
                select(Setting).where(Setting.key == "fmp_api_key")
            )).scalar_one_or_none()
            fmp_key: str | None = None
            if _fmp_key_row is not None:
                try:
                    fmp_key = encryption.decrypt(_fmp_key_row.value)
                except Exception as _e:  # noqa: BLE001
                    logger.warning("Failed to decrypt fmp_api_key: %s", _e)

            _fmp_limit_row = (await _ds.execute(
                select(Setting).where(Setting.key == "fmp_daily_quota_limit")
            )).scalar_one_or_none()
            _fmp_interval_row = (await _ds.execute(
                select(Setting).where(Setting.key == "fmp_min_request_interval_s")
            )).scalar_one_or_none()
            _reset_tz_row = (await _ds.execute(
                select(Setting).where(Setting.key == "dataset_quota_reset_tz")
            )).scalar_one_or_none()

        fmp_daily_limit = 250
        try:
            if _fmp_limit_row is not None:
                fmp_daily_limit = int(_fmp_limit_row.value)
        except (TypeError, ValueError):
            logger.warning(
                "fmp_daily_quota_limit setting %r is not an int; using default %d",
                getattr(_fmp_limit_row, "value", None), fmp_daily_limit,
            )

        fmp_min_interval = 0.0
        try:
            if _fmp_interval_row is not None:
                fmp_min_interval = float(_fmp_interval_row.value)
        except (TypeError, ValueError):
            logger.warning(
                "fmp_min_request_interval_s setting %r is not a number; using default %.1f",
                getattr(_fmp_interval_row, "value", None), fmp_min_interval,
            )

        reset_tz_name = (_reset_tz_row.value if _reset_tz_row is not None else None) or "UTC"
        quota_reset_tz = ZoneInfo(reset_tz_name)

        quota_tracker = QuotaTracker(session_factory, reset_tz=quota_reset_tz)
        dataset_service = DatasetService(data_root=_Path("data"))
        set_default_service(dataset_service)

        dataset_adapters: dict = {}
        if fmp_key:
            dataset_adapters["fmp"] = FMPAdapter(
                api_key=fmp_key,
                http_client=http_client,
                quota_tracker=quota_tracker,
                daily_limit=fmp_daily_limit,
                min_request_interval_s=fmp_min_interval,
            )
            logger.info("FMP adapter configured (daily_limit=%d)", fmp_daily_limit)
        else:
            logger.info("fmp_api_key not configured; FMPAdapter will be unavailable.")

        dataset_dispatcher = DatasetJobDispatcher(
            adapters=dataset_adapters,
            service=dataset_service,
            session_factory=session_factory,
        )
        download_manager.register_dispatcher(dataset_dispatcher)
        await dataset_dispatcher.recover_orphaned_jobs()

        app.state.quota_tracker = quota_tracker
        app.state.dataset_adapters = dataset_adapters
        app.state.dataset_service = dataset_service
        app.state.dataset_dispatcher = dataset_dispatcher
        app.state.download_manager = download_manager
        if n_recovered > 0:
            logger.info("Recovered %d orphaned download row(s) from previous run", n_recovered)

        container = ServiceContainer(session_factory, event_bus, encryption, scheduler)
        container.data_service = data_svc

        from coordinator.services.live_feed_manager import LiveFeedManager
        from coordinator.services.live_feed_aggregator import LiveFeedAggregator
        from coordinator.api.websocket import manager as ws_manager_for_aggregator
        container.live_feed_manager = LiveFeedManager()
        container.live_feed_aggregator = LiveFeedAggregator(
            session_factory,
            encryption=encryption,
            ws_manager=ws_manager_for_aggregator,
        )
        await container.live_feed_aggregator.start()

        from coordinator.services.portfolio_tracker import PortfolioTracker
        portfolio_tracker = PortfolioTracker(ws_manager=ws_manager_for_aggregator)
        container.portfolio_tracker = portfolio_tracker

        # Upsert a Worker row for the coordinator itself so that
        # LiveFeedAggregator._emit_stream_event can write worker_activity rows
        # (WorkerActivity.worker_id is a non-nullable FK into workers).
        from coordinator.database.models import Worker as _Worker
        from sqlalchemy import select as _select
        async with session_factory() as _s:
            _coord_worker = (await _s.execute(
                _select(_Worker).where(_Worker.name == "coord")
            )).scalar_one_or_none()
            if _coord_worker is None:
                _coord_worker = _Worker(name="coord", status="online")
                _s.add(_coord_worker)
                await _s.flush()
                await _s.commit()
            container.live_feed_aggregator._coord_worker_id = _coord_worker.id

        from coordinator.services.lifecycle import LifecycleService
        from coordinator.services.scraper_manager import ScraperManager
        container.lifecycle_service = LifecycleService(
            scraper_manager=ScraperManager(),
            live_feed_manager=container.live_feed_manager,
            session_factory=session_factory,
            live_feed_aggregator=container.live_feed_aggregator,
        )

        from coordinator.services.live_sample_sink import LiveSampleSink
        from pathlib import Path
        container.live_sample_sink = LiveSampleSink(
            base_dir=Path(os.environ.get("QT_LIVE_DATA_DIR", "data/live")),
            buffer_size=int(os.environ.get("QT_LIVE_SAMPLE_BUFFER_SIZE", "200")),
            flush_interval_seconds=int(os.environ.get("QT_LIVE_SAMPLE_FLUSH_INTERVAL_SECONDS", "10")),
        )

        from coordinator.services.live_finalizer import LiveFinalizer
        container.live_finalizer = LiveFinalizer(
            session_factory=container.session_factory,
            sink=container.live_sample_sink,
            base_dir=Path(os.environ.get("QT_LIVE_DATA_DIR", "data/live")),
            interval_seconds=int(os.environ.get("QT_LIVE_FINALIZE_INTERVAL_SECONDS", "15")),
        )
        finalizer_task = asyncio.create_task(container.live_finalizer.run_loop())

        from coordinator.services.tick_scheduler import TickScheduler
        from coordinator.api.websocket import manager as ws_manager_obj

        container.tick_scheduler = TickScheduler(
            aggregator=container.live_feed_aggregator,
            ws_manager=ws_manager_obj,
        )

        from coordinator.services.coverage_index import CoverageIndex
        from coordinator.services.cached_snapshot import CachedSnapshot
        from coordinator.api.routes.data import (
            _build_coverage_payload,
            _build_storage_summary_payload,
        )

        coverage_index = CoverageIndex(data_svc)
        container.coverage_index = coverage_index

        # Two snapshot caches back the slow read endpoints. The coverage
        # snapshot's first refresh also warms CoverageIndex._cache as a side
        # effect — it iterates the same (provider, symbol) pairs.
        async def _coverage_producer() -> dict:
            return await asyncio.to_thread(
                _build_coverage_payload, data_svc, coverage_index
            )

        async def _storage_summary_producer() -> dict:
            return await asyncio.to_thread(
                _build_storage_summary_payload, data_svc
            )

        container.coverage_snapshot = CachedSnapshot("coverage", _coverage_producer)
        container.storage_summary_snapshot = CachedSnapshot(
            "storage_summary", _storage_summary_producer
        )

        async def _prewarm_snapshots() -> None:
            try:
                await container.coverage_snapshot.refresh_now()
                await container.storage_summary_snapshot.refresh_now()
                logger.info("Data snapshot prewarm: complete")
            except Exception:
                logger.exception("Data snapshot prewarm failed")

        asyncio.create_task(_prewarm_snapshots())

        def _on_download_complete(
            provider: str,
            symbols: list[str],
            status: str | None = None,
            error_message: str | None = None,
        ) -> None:
            for sym in symbols:
                coverage_index.invalidate(provider, sym)

            cov_snap = getattr(container, "coverage_snapshot", None)
            if cov_snap is not None:
                cov_snap.invalidate()
            store_snap = getattr(container, "storage_summary_snapshot", None)
            if store_snap is not None:
                store_snap.invalidate()

            # Fan out to the goal processor (if constructed) so it can top up
            # its in-flight queue without waiting for the next cron tick, and
            # record terminal "no data" failures persistently on the goal.
            gp = getattr(container, "goal_processor", None)
            if gp is not None:
                try:
                    asyncio.create_task(
                        gp.on_download_complete(
                            provider, symbols, status=status, error_message=error_message,
                        )
                    )
                except RuntimeError:
                    # No running loop (e.g. during unit-test teardown) — skip.
                    pass

        download_manager._on_download_complete = _on_download_complete

        try:
            from coordinator.services.backtest_runner import BacktestRunner
            container.backtest_runner = BacktestRunner(
                session_factory=session_factory,
                download_manager=download_manager,
                data_service=data_svc,
                coverage_index=coverage_index,
            )
            n_recovered = await container.backtest_runner.recover_orphaned_runs()
            if n_recovered > 0:
                logger.info("Recovered %d orphaned backtest run(s) from previous run", n_recovered)
        except ImportError:
            logger.warning(
                "coordinator.services.backtest_runner not available yet (C1 pending); "
                "backtest run endpoints will return 500 if the runner is invoked."
            )

        from coordinator.services.research_job_manager import ResearchJobManager
        from coordinator.services.validation.sweep import run_sweep as _run_sweep_fn
        from coordinator.services.validation.walk_forward import run_walk_forward as _run_walk_forward_fn
        from coordinator.database.session import get_session_factory as _get_sync_session_factory
        from coordinator.api.websocket import manager as _ws_manager

        async def _research_runner_factory(run_id: str) -> None:
            runner = getattr(container, "backtest_runner", None)
            if runner is None:
                raise RuntimeError("backtest_runner not initialized")
            await runner.run(run_id)

        async def _broadcast_research_update(payload: dict) -> None:
            await _ws_manager.broadcast_to_dashboards(
                {"type": "research_job", **payload}
            )

        container.research_job_manager = ResearchJobManager(
            session_factory=session_factory,
            sweep_fn=_run_sweep_fn,
            walk_forward_fn=_run_walk_forward_fn,
            runner_factory=_research_runner_factory,
            sync_session_factory=_get_sync_session_factory(),
            on_job_update=_broadcast_research_update,
        )
        n_recovered_jobs = await container.research_job_manager.recover_orphaned_jobs()
        if n_recovered_jobs > 0:
            logger.info("Recovered %d orphaned research job(s) from previous run", n_recovered_jobs)

        from coordinator.services.account_lifecycle import AccountLifecycleService
        account_lifecycle = AccountLifecycleService(
            session_factory=session_factory,
            encryption=encryption,
            data_service=data_svc,
            download_manager=download_manager,
            ws_manager=ws_manager_for_aggregator,
            default_provider="tradier",
        )
        container.account_lifecycle = account_lifecycle

        # Register periodic account jobs
        scheduler.add_cron_job(
            job_id="account_periodic_sync",
            func=account_lifecycle.periodic_sync,
            cron_expr="*/15 * * * 1-5",  # every 15min Mon-Fri UTC
        )
        scheduler.add_cron_job(
            job_id="account_daily_close",
            func=account_lifecycle.daily_close,
            cron_expr="35 20 * * 1-5",  # 20:35 UTC = 4:35 PM EDT / 3:35 PM EST
        )

        from coordinator.services.goal_processor import GoalProcessor
        goal_processor = GoalProcessor(
            session_factory=session_factory,
            download_manager=download_manager,
            data_service=data_svc,
            providers=providers,
        )
        container.goal_processor = goal_processor
        scheduler.add_cron_job(
            job_id="data_goal_processor",
            func=goal_processor.tick,
            cron_expr="* * * * *",
        )

        set_container(container)

        from coordinator.services.worker_health import run_worker_health_loop
        health_task = asyncio.create_task(
            run_worker_health_loop(
                container.session_factory,
                interval_seconds=int(os.environ.get("QT_WORKER_HEALTH_INTERVAL_SECONDS", "30")),
                offline_after_seconds=int(os.environ.get("QT_WORKER_OFFLINE_TIMEOUT_SECONDS", "60")),
            )
        )

        from coordinator.services.archival import run_worker_activity_retention_loop
        activity_retention_task = asyncio.create_task(
            run_worker_activity_retention_loop(
                container.session_factory,
                interval_seconds=int(os.environ.get("QT_WORKER_ACTIVITY_RETENTION_INTERVAL_SECONDS", "3600")),
                retention_days=int(os.environ.get("QT_WORKER_ACTIVITY_RETENTION_DAYS", "7")),
            )
        )

        try:
            yield
        finally:
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task
            activity_retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await activity_retention_task
            finalizer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await finalizer_task
            if container.tick_scheduler is not None:
                with contextlib.suppress(Exception):
                    await container.tick_scheduler.shutdown()

        if container.live_feed_aggregator:
            await container.live_feed_aggregator.stop()
        if getattr(container, "research_job_manager", None) is not None:
            with contextlib.suppress(Exception):
                await container.research_job_manager.shutdown()
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

    from coordinator.api.routes.parameter_sets import router as parameter_sets_router
    app.include_router(parameter_sets_router)

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

    from coordinator.api.routes.datasets import router as datasets_router
    app.include_router(datasets_router)

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

    from coordinator.api.routes import backtest_runs as backtest_runs_routes
    app.include_router(backtest_runs_routes.router)

    from coordinator.api.routes import deployments as deployments_routes
    app.include_router(deployments_routes.router)

    from coordinator.api.routes.diagnostics import router as diagnostics_router
    app.include_router(diagnostics_router)

    from coordinator.api.routes.data_goals import router as data_goals_router
    app.include_router(data_goals_router)

    from coordinator.api.routes.research import router as research_router
    app.include_router(research_router)

    import os
    dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
    if os.path.isdir(dashboard_dir):
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException

        class SPAStaticFiles(StaticFiles):
            """StaticFiles that falls back to index.html on 404.

            Without this, refreshing on any client-side React Router path
            (e.g. /accounts/<id>) returns a JSON 404 from FastAPI. With it,
            unmatched paths under the mount return index.html so React Router
            can render the correct view on page load.

            API and WS routes are unaffected because they're registered as
            explicit routes BEFORE this mount.
            """
            async def get_response(self, path, scope):
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        app.mount("/", SPAStaticFiles(directory=dashboard_dir, html=True), name="dashboard")

    return app
