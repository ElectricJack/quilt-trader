"""Tests for I1: startup gating + auto-subscribe in LifecycleManager.

A broker_live data dependency requires a running LiveSubscription on the
account's broker. When that gate passes, the instance is added to the
LiveFeedManager as a dependent and the row's dependent_count is bumped.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import select

from coordinator.database.models import LiveSubscription
from coordinator.services.lifecycle import (
    CompatibilityError,
    LifecycleManager,
    StartError,
)
from coordinator.services.live_feed_manager import LiveFeedManager


@pytest_asyncio.fixture
async def empty_manager() -> LiveFeedManager:
    return LiveFeedManager()


def _account(broker: str = "alpaca"):
    return MagicMock(
        locked_by=None,
        supported_asset_types=["equities"],
        options_level=None,
        account_features=[],
        broker_type=broker,
    )


def _algorithm(deps=None):
    return MagicMock(
        required_asset_types=["equities"],
        required_options_level=None,
        required_account_features=[],
        supported_brokers=None,
        data_dependencies=deps,
    )


@pytest.mark.asyncio
async def test_start_refuses_when_no_live_sub(test_app, empty_manager):
    """Manifest declares broker_live dep but no LiveSubscription row exists."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    lifecycle = LifecycleManager(
        worker_manager=AsyncMock(),
        scraper_manager=MagicMock(is_registered=MagicMock(return_value=False)),
        event_bus=AsyncMock(),
        live_feed_manager=empty_manager,
        session_factory=container.session_factory,
    )

    deps = [{"name": "spy-feed", "symbol": "SPY", "source": "broker_live"}]
    with pytest.raises(StartError, match="no live subscription for SPY on alpaca"):
        await lifecycle.pre_start_checks(
            _account("alpaca"),
            _algorithm(deps),
            MagicMock(id="inst-1"),
        )


@pytest.mark.asyncio
async def test_start_refuses_when_sub_exists_but_not_running(test_app, empty_manager):
    from coordinator.api.dependencies import get_container

    container = get_container()
    async with container.session_factory() as session:
        sub = LiveSubscription(
            broker="alpaca", symbol="SPY", status="stopped", dependent_count=0
        )
        session.add(sub)
        await session.commit()

    lifecycle = LifecycleManager(
        worker_manager=AsyncMock(),
        scraper_manager=MagicMock(is_registered=MagicMock(return_value=False)),
        event_bus=AsyncMock(),
        live_feed_manager=empty_manager,
        session_factory=container.session_factory,
    )
    deps = [{"name": "spy-feed", "symbol": "SPY", "source": "broker_live"}]
    with pytest.raises(StartError, match="no live subscription for SPY on alpaca"):
        await lifecycle.pre_start_checks(
            _account("alpaca"),
            _algorithm(deps),
            MagicMock(id="inst-1"),
        )


@pytest.mark.asyncio
async def test_start_adds_dependent_and_increments_count(test_app, empty_manager):
    from coordinator.api.dependencies import get_container

    container = get_container()
    async with container.session_factory() as session:
        sub = LiveSubscription(
            broker="alpaca", symbol="SPY", status="running", dependent_count=0
        )
        session.add(sub)
        await session.commit()

    lifecycle = LifecycleManager(
        worker_manager=AsyncMock(),
        scraper_manager=MagicMock(is_registered=MagicMock(return_value=False)),
        event_bus=AsyncMock(),
        live_feed_manager=empty_manager,
        session_factory=container.session_factory,
    )
    deps = [{"name": "spy-feed", "symbol": "SPY", "source": "broker_live"}]
    await lifecycle.pre_start_checks(
        _account("alpaca"),
        _algorithm(deps),
        MagicMock(id="inst-1"),
    )
    assert empty_manager.dependent_count("alpaca", "SPY") == 1

    async with container.session_factory() as session:
        row = (
            await session.execute(
                select(LiveSubscription).where(
                    LiveSubscription.broker == "alpaca",
                    LiveSubscription.symbol == "SPY",
                )
            )
        ).scalar_one()
        assert row.dependent_count == 1


@pytest.mark.asyncio
async def test_post_stop_releases_dependent_and_decrements(test_app, empty_manager):
    from coordinator.api.dependencies import get_container

    container = get_container()
    async with container.session_factory() as session:
        sub = LiveSubscription(
            broker="alpaca", symbol="SPY", status="running", dependent_count=1
        )
        session.add(sub)
        await session.commit()
    # Seed in-memory manager so release() finds the dependent.
    empty_manager.register("alpaca", "SPY")
    empty_manager.add_dependent("alpaca", "SPY", "inst-1")
    empty_manager.start("alpaca", "SPY")

    lifecycle = LifecycleManager(
        worker_manager=AsyncMock(),
        scraper_manager=MagicMock(is_registered=MagicMock(return_value=False)),
        event_bus=AsyncMock(),
        live_feed_manager=empty_manager,
        session_factory=container.session_factory,
    )
    deps = [{"name": "spy-feed", "symbol": "SPY", "source": "broker_live"}]
    await lifecycle.post_stop_actions(
        _account("alpaca"),
        _algorithm(deps),
        MagicMock(id="inst-1"),
    )
    assert empty_manager.dependent_count("alpaca", "SPY") == 0

    async with container.session_factory() as session:
        row = (
            await session.execute(
                select(LiveSubscription).where(
                    LiveSubscription.broker == "alpaca",
                    LiveSubscription.symbol == "SPY",
                )
            )
        ).scalar_one()
        assert row.dependent_count == 0


@pytest.mark.asyncio
async def test_historical_deps_do_not_gate(test_app, empty_manager):
    """Non-broker_live deps should not require a live subscription."""
    from coordinator.api.dependencies import get_container

    container = get_container()
    lifecycle = LifecycleManager(
        worker_manager=AsyncMock(),
        scraper_manager=MagicMock(is_registered=MagicMock(return_value=False)),
        event_bus=AsyncMock(),
        live_feed_manager=empty_manager,
        session_factory=container.session_factory,
    )
    deps = [{"name": "spy-polygon", "symbol": "SPY", "source": "polygon"}]
    # No exception
    await lifecycle.pre_start_checks(
        _account("alpaca"),
        _algorithm(deps),
        MagicMock(id="inst-1"),
    )
    assert empty_manager.dependent_count("alpaca", "SPY") == 0


@pytest.mark.asyncio
async def test_no_live_feed_manager_is_backwards_compatible():
    """Existing call sites that don't pass live_feed_manager should still work."""
    lifecycle = LifecycleManager(
        worker_manager=AsyncMock(),
        scraper_manager=MagicMock(is_registered=MagicMock(return_value=False)),
        event_bus=AsyncMock(),
    )
    deps = [{"name": "spy-feed", "symbol": "SPY", "source": "broker_live"}]
    # When the live-feed manager is absent, gating is skipped (early phases).
    await lifecycle.pre_start_checks(
        _account("alpaca"),
        _algorithm(deps),
        MagicMock(id="inst-1"),
    )
