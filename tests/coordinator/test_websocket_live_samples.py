import pytest
import pytest_asyncio
from pathlib import Path

from coordinator.main import create_app
from coordinator.api.dependencies import get_container


@pytest_asyncio.fixture
async def running_app():
    import asyncio
    app = create_app(
        database_url="sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
        encryption_key="test-key-32-bytes-long!!!!!!!!",
    )
    async with app.router.lifespan_context(app):
        # Drain so background tasks' first iterations finish before fixtures
        # start writing — avoids SQLite "table is locked" under shared-cache.
        await asyncio.sleep(0.05)
        yield app


@pytest_asyncio.fixture
async def db_session(running_app):
    container = get_container()
    async with container.session_factory() as session:
        yield session
        await session.rollback()


@pytest.mark.asyncio
async def test_equity_sample_routed_to_sink(running_app, db_session, tmp_path):
    from coordinator.services.live_sample_sink import LiveSampleSink

    container = get_container()
    container.live_sample_sink = LiveSampleSink(
        base_dir=tmp_path, buffer_size=1, flush_interval_seconds=60,
    )

    from coordinator.api.websocket import handle_worker_message
    await handle_worker_message(None, {
        "type": "equity_sample",
        "worker_id": "w1", "instance_id": "d1", "run_id": "r1",
        "timestamp": "2026-05-16T12:00:00Z",
        "portfolio_value": 100.0, "cash": 50.0,
    })
    assert (tmp_path / "d1" / "r1" / "equity.parquet").exists()


@pytest.mark.asyncio
async def test_trade_sample_routed_to_sink(running_app, db_session, tmp_path):
    from coordinator.services.live_sample_sink import LiveSampleSink

    container = get_container()
    container.live_sample_sink = LiveSampleSink(
        base_dir=tmp_path, buffer_size=1, flush_interval_seconds=60,
    )

    from coordinator.api.websocket import handle_worker_message
    await handle_worker_message(None, {
        "type": "trade_sample",
        "worker_id": "w1", "instance_id": "d1", "run_id": "r1",
        "timestamp": "2026-05-16T12:00:00Z",
        "symbol": "AAPL", "asset_type": "equities", "side": "buy",
        "quantity": 10.0,
        "requested_price": 100.0, "fill_price": 100.5,
        "slippage_dollars": 5.0, "slippage_bps_applied": 0.5,
        "fees": 1.0, "fee_breakdown": "{}",
        "signal_id": "s1", "realized_pnl": None,
    })
    assert (tmp_path / "d1" / "r1" / "trades.parquet").exists()


@pytest.mark.asyncio
async def test_equity_sample_without_run_id_is_ignored(running_app, tmp_path):
    from coordinator.services.live_sample_sink import LiveSampleSink

    container = get_container()
    container.live_sample_sink = LiveSampleSink(
        base_dir=tmp_path, buffer_size=1, flush_interval_seconds=60,
    )
    from coordinator.api.websocket import handle_worker_message
    await handle_worker_message(None, {
        "type": "equity_sample",
        "worker_id": "w1", "instance_id": "d1",
        "portfolio_value": 100.0,
    })
    # no parquet should exist for the missing run_id
    assert not any(tmp_path.rglob("*.parquet"))
