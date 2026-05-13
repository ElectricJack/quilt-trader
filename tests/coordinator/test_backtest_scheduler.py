import pytest
import pytest_asyncio
from datetime import datetime, timezone

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base, AlgorithmInstance, Algorithm, Account, Worker, DecisionLog
from coordinator.services.backtest_scheduler import BacktestSchedulerJob


@pytest_asyncio.fixture
async def db_engine():
    engine = create_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return create_session_factory(db_engine)


@pytest_asyncio.fixture
async def seeded_instance(session_factory):
    async with session_factory() as session:
        account = Account(
            name="Test Account", broker_type="alpaca",
            credentials="{}", supported_asset_types=["equities"],
        )
        session.add(account)
        await session.flush()

        worker = Worker(name="pi-1", tailscale_ip="100.64.0.1")
        session.add(worker)
        await session.flush()

        algo = Algorithm(repo_url="https://github.com/test/algo", name="TestAlgo")
        session.add(algo)
        await session.flush()

        instance = AlgorithmInstance(
            algorithm_id=algo.id, account_id=account.id,
            worker_id=worker.id, status="running",
        )
        session.add(instance)
        await session.flush()

        instance_id = instance.id
        algo_id = algo.id
        await session.commit()

    return instance_id, algo_id


class TestBacktestSchedulerJob:
    @pytest.mark.asyncio
    async def test_run_no_instances(self, session_factory):
        job = BacktestSchedulerJob(session_factory=session_factory)
        results = await job.run()
        assert results == []

    @pytest.mark.asyncio
    async def test_run_no_decisions(self, session_factory, seeded_instance):
        job = BacktestSchedulerJob(session_factory=session_factory)
        results = await job.run()
        assert len(results) == 1
        assert results[0]["status"] == "no_data"

    @pytest.mark.asyncio
    async def test_run_with_matching_decisions(self, session_factory, seeded_instance):
        instance_id, algo_id = seeded_instance
        now = datetime.now(timezone.utc)

        async with session_factory() as session:
            for i in range(3):
                session.add(DecisionLog(
                    instance_id=instance_id, mode="live",
                    timestamp=now, signals_produced=[],
                ))
                session.add(DecisionLog(
                    instance_id=instance_id, mode="backtest",
                    timestamp=now, signals_produced=[],
                ))
            await session.commit()

        job = BacktestSchedulerJob(session_factory=session_factory)
        results = await job.run()

        assert len(results) == 1
        assert results[0]["match_percentage"] == 100.0
        assert results[0]["exceeds_threshold"] is False

    @pytest.mark.asyncio
    async def test_run_with_divergence(self, session_factory, seeded_instance):
        instance_id, algo_id = seeded_instance
        now = datetime.now(timezone.utc)

        async with session_factory() as session:
            session.add(DecisionLog(
                instance_id=instance_id, mode="live",
                timestamp=now,
                signals_produced=[{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}],
            ))
            session.add(DecisionLog(
                instance_id=instance_id, mode="backtest",
                timestamp=now,
                signals_produced=[{"legs": [{"symbol": "AAPL", "signal_type": "sell"}]}],
            ))
            await session.commit()

        job = BacktestSchedulerJob(session_factory=session_factory, threshold=5.0)
        results = await job.run()

        assert len(results) == 1
        assert results[0]["match_percentage"] == 0.0
        assert results[0]["exceeds_threshold"] is True

    @pytest.mark.asyncio
    async def test_creates_backtest_comparison_record(self, session_factory, seeded_instance):
        instance_id, algo_id = seeded_instance
        now = datetime.now(timezone.utc)

        async with session_factory() as session:
            session.add(DecisionLog(
                instance_id=instance_id, mode="live",
                timestamp=now, signals_produced=[],
            ))
            session.add(DecisionLog(
                instance_id=instance_id, mode="backtest",
                timestamp=now, signals_produced=[],
            ))
            await session.commit()

        job = BacktestSchedulerJob(session_factory=session_factory)
        await job.run()

        from sqlalchemy import select
        from coordinator.database.models import BacktestComparison
        async with session_factory() as session:
            result = await session.execute(select(BacktestComparison))
            comparisons = result.scalars().all()
            assert len(comparisons) == 1
            assert comparisons[0].instance_id == instance_id
            assert comparisons[0].match_percentage == 100.0
