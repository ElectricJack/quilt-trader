import pytest
from sqlalchemy import select

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import (
    Account, Algorithm, AlgorithmInstance, Base,
    LiveSubscription, SubscriptionConsumer, Worker,
)
from coordinator.services.lifecycle import LifecycleService
from coordinator.services.live_feed_manager import LiveFeedManager
from coordinator.services.scraper_manager import ScraperManager


@pytest.mark.asyncio
async def test_pre_start_creates_live_subscription_and_consumer():
    """Algorithm with assets in its manifest: auto-creates subscription + algo consumer."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities", "crypto"])
        algo = Algorithm(
            repo_url="x", name="multi",
            assets=[
                {"symbol": "SPY", "asset_class": "equities"},
                {"symbol": "BTCUSD", "asset_class": "crypto"},
            ],
        )
        session.add_all([worker, acct, algo])
        await session.flush()
        inst = AlgorithmInstance(
            algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
            status="stopped",
        )
        session.add(inst)
        await session.commit()
        inst_id = inst.id

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )

    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        acct = (await session.execute(select(Account))).scalar_one()
        inst = (await session.execute(select(AlgorithmInstance))).scalar_one()
        await service.pre_start_checks(acct, algo, inst)

    async with sf() as session:
        subs = (await session.execute(select(LiveSubscription))).scalars().all()
        assert len(subs) == 2
        symbols = sorted(s.symbol for s in subs)
        assert symbols == ["BTCUSD", "SPY"]
        for s in subs:
            consumers = (await session.execute(
                select(SubscriptionConsumer).where(
                    SubscriptionConsumer.subscription_id == s.id
                )
            )).scalars().all()
            assert len(consumers) == 1
            assert consumers[0].consumer_type == "algo"
            assert consumers[0].consumer_id == inst_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_post_stop_deletes_algo_consumer_and_auto_deletes_orphan_subscription():
    """Post-stop releases the algo consumer; if no other consumers, the
    subscription row is auto-deleted."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities"])
        algo = Algorithm(
            repo_url="x", name="single",
            assets=[{"symbol": "SPY", "asset_class": "equities"}],
        )
        session.add_all([worker, acct, algo])
        await session.flush()
        inst = AlgorithmInstance(
            algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
            status="stopped",
        )
        session.add(inst)
        await session.commit()

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )
    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        acct = (await session.execute(select(Account))).scalar_one()
        inst = (await session.execute(select(AlgorithmInstance))).scalar_one()
        await service.pre_start_checks(acct, algo, inst)
        await service.post_stop_actions(acct, algo, inst)

    async with sf() as session:
        subs = (await session.execute(select(LiveSubscription))).scalars().all()
        assert subs == [], "subscription should be auto-deleted when last consumer is released"

    await engine.dispose()


@pytest.mark.asyncio
async def test_post_stop_preserves_subscription_held_by_manual_consumer():
    """Post-stop deletes only the algo consumer; manual consumer keeps the sub alive."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities"])
        algo = Algorithm(
            repo_url="x", name="single",
            assets=[{"symbol": "SPY", "asset_class": "equities"}],
        )
        session.add_all([worker, acct, algo])
        await session.flush()
        inst = AlgorithmInstance(
            algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
            status="stopped",
        )
        # Pre-existing subscription with a manual consumer.
        sub = LiveSubscription(account_id=acct.id, broker="alpaca", symbol="SPY",
                               asset_class="equities", status="running")
        session.add_all([inst, sub])
        await session.flush()
        session.add(SubscriptionConsumer(
            subscription_id=sub.id, consumer_type="manual", consumer_id=None,
        ))
        await session.commit()

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )
    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        acct = (await session.execute(select(Account))).scalar_one()
        inst = (await session.execute(select(AlgorithmInstance))).scalar_one()
        await service.pre_start_checks(acct, algo, inst)
        await service.post_stop_actions(acct, algo, inst)

    async with sf() as session:
        subs = (await session.execute(select(LiveSubscription))).scalars().all()
        assert len(subs) == 1
        consumers = (await session.execute(
            select(SubscriptionConsumer).where(
                SubscriptionConsumer.subscription_id == subs[0].id
            )
        )).scalars().all()
        assert len(consumers) == 1
        assert consumers[0].consumer_type == "manual"

    await engine.dispose()


@pytest.mark.asyncio
async def test_pre_start_creates_subscription_under_deployment_account():
    """The subscription gets account_id = instance.account_id."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities"])
        algo = Algorithm(
            repo_url="x", name="single",
            assets=[{"symbol": "SPY", "asset_class": "equities"}],
        )
        session.add_all([worker, acct, algo])
        await session.flush()
        inst = AlgorithmInstance(
            algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
            status="stopped",
        )
        session.add(inst)
        await session.commit()

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )
    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        acct = (await session.execute(select(Account))).scalar_one()
        inst = (await session.execute(select(AlgorithmInstance))).scalar_one()
        await service.pre_start_checks(acct, algo, inst)

    async with sf() as session:
        sub = (await session.execute(select(LiveSubscription))).scalar_one()
        assert sub.account_id == acct.id
        assert sub.broker == "alpaca"

    await engine.dispose()


@pytest.mark.asyncio
async def test_pre_start_raises_when_account_does_not_support_asset_class():
    """Algorithm needs crypto, account only supports equities → fail at pre_start."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        acct = Account(name="A", broker_type="alpaca", credentials="{}",
                       supported_asset_types=["equities"])
        # NOTE: deliberately leaving required_asset_types empty so the general
        # _check_compatibility passes and the new per-asset-class guard is
        # what raises (otherwise we'd be testing the wrong code path).
        algo = Algorithm(
            repo_url="x", name="crypto-only",
            assets=[{"symbol": "BTCUSD", "asset_class": "crypto"}],
        )
        session.add_all([worker, acct, algo])
        await session.flush()
        inst = AlgorithmInstance(
            algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
            status="stopped",
        )
        session.add(inst)
        await session.commit()

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )
    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        acct = (await session.execute(select(Account))).scalar_one()
        inst = (await session.execute(select(AlgorithmInstance))).scalar_one()
        from coordinator.services.lifecycle import CompatibilityError
        with pytest.raises(CompatibilityError):
            await service.pre_start_checks(acct, algo, inst)

    await engine.dispose()


@pytest.mark.asyncio
async def test_same_algo_on_two_accounts_creates_two_subscriptions():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with sf() as session:
        worker = Worker(name="W", status="online")
        a1 = Account(name="A1", broker_type="alpaca", credentials="{}",
                     supported_asset_types=["equities"])
        a2 = Account(name="A2", broker_type="alpaca", credentials="{}",
                     supported_asset_types=["equities"])
        algo = Algorithm(
            repo_url="x", name="dual",
            assets=[{"symbol": "SPY", "asset_class": "equities"}],
        )
        session.add_all([worker, a1, a2, algo])
        await session.flush()
        i1 = AlgorithmInstance(algorithm_id=algo.id, account_id=a1.id,
                               worker_id=worker.id, status="stopped")
        i2 = AlgorithmInstance(algorithm_id=algo.id, account_id=a2.id,
                               worker_id=worker.id, status="stopped")
        session.add_all([i1, i2])
        await session.commit()

    service = LifecycleService(
        scraper_manager=ScraperManager(),
        live_feed_manager=LiveFeedManager(),
        session_factory=sf,
    )
    async with sf() as session:
        algo = (await session.execute(select(Algorithm))).scalar_one()
        instances = (await session.execute(select(AlgorithmInstance))).scalars().all()
        for inst in instances:
            acct = (await session.execute(
                select(Account).where(Account.id == inst.account_id)
            )).scalar_one()
            await service.pre_start_checks(acct, algo, inst)

    async with sf() as session:
        subs = (await session.execute(select(LiveSubscription))).scalars().all()
        assert len(subs) == 2
        assert {s.account_id for s in subs} == {instances[0].account_id, instances[1].account_id}

    await engine.dispose()
