import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import (
    Account, Base, Algorithm, AlgorithmInstance, AlgorithmRun,
    BacktestRun, OptimizationSession,
)
from coordinator.services.algorithm_summary import AlgorithmSummaryService


@pytest_asyncio.fixture
async def async_session_factory(tmp_path):
    db_file = tmp_path / "summary.db"
    sync_url = f"sqlite:///{db_file}"
    async_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_engine(sync_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    engine.dispose()
    async_engine = create_async_engine(async_url)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    yield factory
    await async_engine.dispose()


def _make_algo(s, **kw):
    algo_id = kw.pop("id", f"a-{uuid.uuid4().hex[:6]}")
    algo = Algorithm(
        id=algo_id, repo_url="https://x.com/r", name=kw.pop("name", "test"),
        source_path="/tmp/a", install_status="installed",
        **kw,
    )
    s.add(algo)
    return algo


def _make_account(s, environment="paper", **kw):
    acct = Account(
        id=kw.pop("id", f"acct-{uuid.uuid4().hex[:6]}"),
        name=kw.pop("name", f"acct-{environment}"),
        broker_type=kw.pop("broker_type", "alpaca"),
        environment=environment,
        credentials="{}",
        supported_asset_types=["us_equity"],
        **kw,
    )
    s.add(acct)
    return acct


@pytest.mark.asyncio
async def test_idle_algorithm_no_data(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["status"] == "idle"
    assert summary["status_source"] is None
    assert summary["headline_sharpe"] is None
    assert summary["equity_sparkline"] == []
    assert summary["counts"] == {"deployments": 0, "backtests": 0, "research_sessions": 0}
    assert summary["last_activity_at"] is None


@pytest.mark.asyncio
async def test_running_instance_on_live_account_reports_live(async_session_factory):
    """A running instance on a live broker account makes the algorithm 'live',
    not 'idle'. Regression for the bug where _pick_status_instance compared
    instance.status to 'live'/'paper' even though the DB stores 'running'.
    """
    async with async_session_factory() as s:
        algo = _make_algo(s)
        acct = _make_account(s, environment="live")
        live = AlgorithmInstance(
            id=f"i-{uuid.uuid4().hex[:6]}", algorithm_id=algo.id,
            account_id=acct.id, worker_id="w-1", status="running",
        )
        s.add(live)
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["status"] == "live"
    assert summary["status_source"] == live.id


@pytest.mark.asyncio
async def test_live_status_promoted_over_paper(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        paper_acct = _make_account(s, environment="paper")
        live_acct = _make_account(s, environment="live")
        s.add(AlgorithmInstance(
            id=f"i-{uuid.uuid4().hex[:6]}", algorithm_id=algo.id,
            account_id=paper_acct.id, worker_id="w-1", status="running",
        ))
        live = AlgorithmInstance(
            id=f"i-{uuid.uuid4().hex[:6]}", algorithm_id=algo.id,
            account_id=live_acct.id, worker_id="w-1", status="running",
        )
        s.add(live)
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["status"] == "live"
    assert summary["status_source"] == live.id
    assert summary["counts"]["deployments"] == 2


@pytest.mark.asyncio
async def test_stopped_instance_on_live_account_is_idle(async_session_factory):
    """A live-account instance that is not currently running shouldn't promote
    the algorithm to live."""
    async with async_session_factory() as s:
        algo = _make_algo(s)
        acct = _make_account(s, environment="live")
        s.add(AlgorithmInstance(
            id=f"i-{uuid.uuid4().hex[:6]}", algorithm_id=algo.id,
            account_id=acct.id, worker_id="w-1", status="stopped",
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["status"] == "idle"
    assert summary["status_source"] is None


@pytest.mark.asyncio
async def test_sharpe_falls_back_to_last_backtest(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        s.add(BacktestRun(
            id="r-1", algorithm_id=algo.id, status="completed",
            sharpe_ratio=1.4, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            date_range_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2025, 12, 31, tzinfo=timezone.utc),
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["headline_sharpe"] == pytest.approx(1.4)
    assert summary["headline_sharpe_source"] == "last_backtest"


@pytest.mark.asyncio
async def test_sparkline_downsampled_to_60(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        s.add(BacktestRun(
            id="r-1", algorithm_id=algo.id, status="completed",
            equity_curve=[{"equity": float(i)} for i in range(500)],
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            date_range_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2025, 12, 31, tzinfo=timezone.utc),
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert len(summary["equity_sparkline"]) == 60
    assert summary["equity_sparkline"][0] == 0.0
    assert summary["equity_sparkline"][-1] >= 480.0


@pytest.mark.asyncio
async def test_counts_and_last_activity(async_session_factory):
    async with async_session_factory() as s:
        algo = _make_algo(s)
        s.add(BacktestRun(
            id="r-1", algorithm_id=algo.id, status="completed",
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            date_range_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2025, 12, 31, tzinfo=timezone.utc),
        ))
        s.add(BacktestRun(
            id="r-2", algorithm_id=algo.id, status="completed",
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            date_range_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2025, 12, 31, tzinfo=timezone.utc),
        ))
        s.add(OptimizationSession(
            name="sess-1", hypothesis="h", algorithm_id=algo.id,
            base_config={}, parameter_space="{}", pre_registered_criteria="{}",
            status="open",
            date_range_start=date(2023, 1, 1), date_range_end=date(2024, 12, 31),
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["counts"] == {"deployments": 0, "backtests": 2, "research_sessions": 1}
    assert summary["last_activity_at"].startswith("2026-06-01")


@pytest.mark.asyncio
async def test_live_sharpe_overrides_backtest(async_session_factory):
    """When a live instance reports sharpe_30d, that wins over last backtest sharpe."""
    async with async_session_factory() as s:
        algo = _make_algo(s)
        acct = _make_account(s, environment="live")
        s.add(AlgorithmInstance(
            id=f"i-{uuid.uuid4().hex[:6]}", algorithm_id=algo.id,
            account_id=acct.id, worker_id="w-1", status="running",
            lifetime_metrics={"sharpe_30d": 2.1},
        ))
        s.add(BacktestRun(
            id="r-1", algorithm_id=algo.id, status="completed",
            sharpe_ratio=0.9,
            date_range_start=date(2025, 1, 1), date_range_end=date(2025, 12, 31),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["headline_sharpe"] == pytest.approx(2.1)
    assert summary["headline_sharpe_source"] == "live_30d"


@pytest.mark.asyncio
async def test_live_sparkline_from_any_instance(async_session_factory):
    """Sparkline pulls from the latest AlgorithmRun across all instances, even when no active instance exists."""
    async with async_session_factory() as s:
        algo = _make_algo(s)
        inst = AlgorithmInstance(
            id=f"i-{uuid.uuid4().hex[:6]}", algorithm_id=algo.id,
            account_id="acct-1", worker_id="w-1", status="stopped",
        )
        s.add(inst)
        await s.flush()
        s.add(AlgorithmRun(
            instance_id=inst.id, run_number=1,
            equity_curve=[{"equity": 100.0 + i} for i in range(10)],
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            stopped_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ))
        await s.commit()
        svc = AlgorithmSummaryService()
        summary = await svc.build_for(algo.id, s)

    assert summary["status"] == "idle"
    assert summary["equity_sparkline_source"] == "live"
    assert len(summary["equity_sparkline"]) == 10
    assert summary["equity_sparkline"][0] == 100.0
