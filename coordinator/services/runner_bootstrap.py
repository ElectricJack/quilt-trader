"""Standalone bootstrap helpers for backtest execution outside the FastAPI app.

The coordinator's HTTP API wires services through `coordinator/main.py` at
startup. CLI commands and other non-HTTP entry points need the same services
but without the FastAPI app. This module exposes a helper that constructs the
minimum dependency graph needed to execute backtests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine


@dataclass
class RunnerServices:
    """Bundle of services needed to execute backtests outside the FastAPI app."""

    session_factory: async_sessionmaker[AsyncSession]
    data_service: Any   # DataService
    download_manager: Any  # DownloadManager
    coverage_index: Any  # CoverageIndex
    runner: Any  # BacktestRunner


def _resolve_async_db_url() -> str:
    """Match the coordinator's DB URL with the async driver prefix."""
    raw = os.environ.get("QUILT_DB_URL")
    if raw:
        if raw.startswith("sqlite:///") and not raw.startswith("sqlite+aiosqlite"):
            return raw.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return raw
    return f"sqlite+aiosqlite:///{Path('data') / 'quilt_trader.db'}"


def bootstrap_runner_services(
    *,
    market_data_dir: str | Path = "data/market",
    custom_data_dir: str | Path = "data/custom",
) -> RunnerServices:
    """Construct the minimum service graph needed to execute backtests.

    Mirrors the relevant parts of `coordinator/main.py`'s startup. Does NOT
    initialize the scheduler, websocket aggregator, account lifecycle, broker
    accounts, or anything else — only the chain required by BacktestRunner.

    Providers are NOT wired here (providers={} empty dict). The
    download_manager will return errors if a backtest references symbols whose
    data isn't already on disk. This is intentional: the CLI assumes the user
    has data pre-downloaded (via `quilt data fetch ...`).

    Constructor signatures confirmed from source:
      - DataService(market_data_dir: str, custom_data_dir: str)
      - DownloadManager(session_factory, data_service, providers, on_download_complete=None)
      - CoverageIndex(data_service: DataService)
      - BacktestRunner(session_factory, download_manager, data_service, coverage_index=None)
    """
    from coordinator.services.data_service import DataService
    from coordinator.services.download_manager import DownloadManager
    from coordinator.services.coverage_index import CoverageIndex
    from coordinator.services.backtest_runner import BacktestRunner

    db_url = _resolve_async_db_url()
    engine: AsyncEngine = create_async_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    data_svc = DataService(
        market_data_dir=str(market_data_dir),
        custom_data_dir=str(custom_data_dir),
    )
    # providers={} means no auto-download; backtests on symbols without cached
    # data on disk will fail. Users must pre-download via `quilt data fetch`.
    download_manager = DownloadManager(
        session_factory=session_factory,
        data_service=data_svc,
        providers={},
    )
    coverage_index = CoverageIndex(data_svc)

    runner = BacktestRunner(
        session_factory=session_factory,
        download_manager=download_manager,
        data_service=data_svc,
        coverage_index=coverage_index,
    )

    return RunnerServices(
        session_factory=session_factory,
        data_service=data_svc,
        download_manager=download_manager,
        coverage_index=coverage_index,
        runner=runner,
    )
