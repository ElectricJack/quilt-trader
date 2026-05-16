import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_observer_sends_equity_sample_per_tick():
    from worker.live_observer import LiveObserver
    agent = MagicMock()
    agent.worker_id = "w1"
    agent._send = AsyncMock()
    broker = MagicMock()
    broker.get_account_info = MagicMock(return_value={"cash": 100.0, "portfolio_value": 150.0})
    obs = LiveObserver(agent=agent, broker=broker, instance_id="d1", run_id="r1")

    await obs.on_tick(timestamp="2026-05-16T12:00:00Z")
    sent = agent._send.call_args.args[0]
    assert sent["type"] == "equity_sample"
    assert sent["worker_id"] == "w1"
    assert sent["instance_id"] == "d1"
    assert sent["run_id"] == "r1"
    assert sent["portfolio_value"] == 150.0
    assert sent["cash"] == 100.0
    assert sent["timestamp"] == "2026-05-16T12:00:00Z"


@pytest.mark.asyncio
async def test_observer_falls_back_to_cash_plus_positions_value():
    from worker.live_observer import LiveObserver
    agent = MagicMock()
    agent.worker_id = "w1"
    agent._send = AsyncMock()
    broker = MagicMock()
    broker.get_account_info = MagicMock(return_value={"cash": 100.0, "positions_value": 50.0})
    obs = LiveObserver(agent=agent, broker=broker, instance_id="d1", run_id="r1")

    await obs.on_tick(timestamp="2026-05-16T12:00:00Z")
    sent = agent._send.call_args.args[0]
    assert sent["portfolio_value"] == 150.0


@pytest.mark.asyncio
async def test_observer_sends_trade_sample():
    from worker.live_observer import LiveObserver
    agent = MagicMock()
    agent.worker_id = "w1"
    agent._send = AsyncMock()
    broker = MagicMock()
    obs = LiveObserver(agent=agent, broker=broker, instance_id="d1", run_id="r1")

    await obs.on_trade(trade={
        "timestamp": "2026-05-16T12:00:00Z",
        "symbol": "AAPL", "side": "buy", "quantity": 10.0,
        "fill_price": 100.5,
    })
    sent = agent._send.call_args.args[0]
    assert sent["type"] == "trade_sample"
    assert sent["worker_id"] == "w1"
    assert sent["instance_id"] == "d1"
    assert sent["run_id"] == "r1"
    assert sent["symbol"] == "AAPL"
    assert sent["fill_price"] == 100.5


@pytest.mark.asyncio
async def test_observer_on_tick_uses_fallback_timestamp():
    """When no timestamp is passed, on_tick generates one from utc now."""
    from worker.live_observer import LiveObserver
    agent = MagicMock()
    agent.worker_id = "w1"
    agent._send = AsyncMock()
    broker = MagicMock()
    broker.get_account_info = MagicMock(return_value={"cash": 50.0, "portfolio_value": 75.0})
    obs = LiveObserver(agent=agent, broker=broker, instance_id="d1", run_id="r1")

    await obs.on_tick()
    sent = agent._send.call_args.args[0]
    assert sent["timestamp"] is not None
    assert sent["type"] == "equity_sample"


@pytest.mark.asyncio
async def test_observer_on_tick_swallows_broker_exception():
    """If get_account_info raises, on_tick logs and returns without sending."""
    from worker.live_observer import LiveObserver
    agent = MagicMock()
    agent.worker_id = "w1"
    agent._send = AsyncMock()
    broker = MagicMock()
    broker.get_account_info = MagicMock(side_effect=RuntimeError("broker down"))
    obs = LiveObserver(agent=agent, broker=broker, instance_id="d1", run_id="r1")

    # Should not raise; _send should not have been called.
    await obs.on_tick(timestamp="2026-05-16T12:00:00Z")
    agent._send.assert_not_called()
