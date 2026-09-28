import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from starlette.websockets import WebSocketDisconnect


class _FakeAggregator:
    def __init__(self):
        self.subscribed: list = []
        self.unsubscribed: list = []
    def subscribe_bars(self, broker, symbol, tf, cb):
        self.subscribed.append(("bars", broker, symbol, tf, cb))
    def unsubscribe_bars(self, broker, symbol, tf, cb):
        self.unsubscribed.append(("bars", broker, symbol, tf, cb))
    def subscribe_events(self, broker, symbol, cb):
        self.subscribed.append(("events", broker, symbol, cb))
    def unsubscribe_events(self, broker, symbol, cb):
        self.unsubscribed.append(("events", broker, symbol, cb))


@pytest.mark.asyncio
async def test_start_instance_subscribes_aggregator_for_bar_trigger():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    sched = TickScheduler(aggregator=agg, ws_manager=MagicMock())
    await sched.start_instance({
        "instance_id": "d1", "run_id": "r1", "worker_id": "w1",
        "broker_type": "alpaca", "asset_type": "equities",
        "trigger": "bar:1min",
        "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
    })
    assert any(s[1] == "alpaca" and s[2] == "AAPL" and s[3] == "1min"
               for s in agg.subscribed if s[0] == "bars")
    await sched.shutdown()


@pytest.mark.asyncio
async def test_stop_instance_unsubscribes():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    sched = TickScheduler(aggregator=agg, ws_manager=MagicMock())
    await sched.start_instance({
        "instance_id": "d1", "run_id": "r1", "worker_id": "w1",
        "broker_type": "alpaca", "asset_type": "equities",
        "trigger": "bar:1min",
        "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
    })
    await sched.stop_instance("d1")
    assert any(u[1] == "alpaca" and u[2] == "AAPL" and u[3] == "1min"
               for u in agg.unsubscribed if u[0] == "bars")
    await sched.shutdown()


@pytest.mark.asyncio
async def test_bar_close_callback_enqueues_tick_for_worker():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    ws_manager = MagicMock()
    ws_manager.worker_connections = {"w1": MagicMock()}
    sent: list = []
    async def fake_send(msg):
        sent.append(msg)
    ws_manager.worker_connections["w1"].send_json = fake_send
    sched = TickScheduler(aggregator=agg, ws_manager=ws_manager, coalesce_ms=20)
    await sched.start_instance({
        "instance_id": "d1", "run_id": "r1", "worker_id": "w1",
        "broker_type": "alpaca", "asset_type": "equities",
        "trigger": "bar:1min",
        "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
    })
    bar_cb = next(s[4] for s in agg.subscribed if s[0] == "bars" and s[2] == "AAPL")
    await bar_cb({"timestamp": "2026-05-16T13:34:00Z", "close": 100.0})
    await asyncio.sleep(0.1)
    assert sent
    msg = sent[0]
    assert msg["type"] == "tick_batch"
    assert any(t["instance_id"] == "d1" for t in msg["ticks"])
    await sched.shutdown()


@pytest.mark.asyncio
async def test_multiple_ticks_for_same_worker_coalesce_into_one_batch():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    ws_manager = MagicMock()
    ws_manager.worker_connections = {"w1": MagicMock()}
    sent: list = []
    async def fake_send(msg):
        sent.append(msg)
    ws_manager.worker_connections["w1"].send_json = fake_send
    sched = TickScheduler(aggregator=agg, ws_manager=ws_manager, coalesce_ms=30)
    for inst_id in ("d1", "d2"):
        await sched.start_instance({
            "instance_id": inst_id, "run_id": f"r-{inst_id}", "worker_id": "w1",
            "broker_type": "alpaca", "asset_type": "equities",
            "trigger": "bar:1min",
            "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
        })
    bar_cbs = [s[4] for s in agg.subscribed if s[0] == "bars" and s[2] == "AAPL"]
    for cb in bar_cbs:
        await cb({"timestamp": "2026-05-16T13:34:00Z", "close": 100.0})
    await asyncio.sleep(0.15)
    assert len(sent) == 1
    assert len(sent[0]["ticks"]) == 2
    await sched.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("send_error", "expected_level", "expected_message"),
    [
        (WebSocketDisconnect(code=1006), logging.INFO, "Dropping tick_batch"),
        (RuntimeError("send failed"), logging.ERROR, "Failed to send tick_batch"),
    ],
)
async def test_outbound_send_failure_logging(
    send_error, expected_level, expected_message, caplog,
):
    from coordinator.services.tick_scheduler import _WorkerOutbound

    attempted = asyncio.Event()
    async def fail_send(_message):
        attempted.set()
        raise send_error

    ws = MagicMock()
    ws.send_json = fail_send
    ws_manager = MagicMock()
    ws_manager.worker_connections = {"worker-1": ws}
    outbound = _WorkerOutbound("worker-1", ws_manager, coalesce_ms=0)

    with caplog.at_level(logging.INFO, logger="coordinator.services.tick_scheduler"):
        try:
            await outbound.enqueue({"instance_id": "d1"})
            await asyncio.wait_for(attempted.wait(), timeout=1)
            await asyncio.sleep(0)
        finally:
            await outbound.shutdown()

    send_logs = [
        record for record in caplog.records
        if record.name == "coordinator.services.tick_scheduler"
        and "tick_batch" in record.message
    ]
    assert len(send_logs) == 1
    assert send_logs[0].levelno == expected_level
    assert expected_message in send_logs[0].message
    assert (send_logs[0].exc_info is not None) == (expected_level == logging.ERROR)


@pytest.mark.asyncio
async def test_drop_worker_cancels_subscriptions_for_that_worker():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    sched = TickScheduler(aggregator=agg, ws_manager=MagicMock())
    await sched.start_instance({
        "instance_id": "d1", "run_id": "r1", "worker_id": "w1",
        "broker_type": "alpaca", "asset_type": "equities",
        "trigger": "bar:1min",
        "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
    })
    await sched.drop_worker("w1")
    assert "d1" not in sched._instances
    await sched.shutdown()


@pytest.mark.asyncio
async def test_interval_trigger_survives_symbolless_data_dependencies(monkeypatch):
    """An algorithm whose only data dependency is a custom CSV has no 'symbol'
    key in its deps. The interval loop must fall back to asset_type for the
    market-clock probe rather than dying with KeyError on the first iteration,
    which silently starves the instance of every tick."""
    from coordinator.services.tick_scheduler import TickScheduler
    import coordinator.services.market_clock as market_clock

    monkeypatch.setattr(market_clock, "is_market_open", lambda probe, now: True)

    sched = TickScheduler(aggregator=_FakeAggregator(), ws_manager=MagicMock())
    enqueued: list = []
    async def _capture(worker_id, tick):
        enqueued.append((worker_id, tick))
    monkeypatch.setattr(sched, "_enqueue_tick", _capture)

    await sched.start_instance({
        "instance_id": "d1", "run_id": "r1", "worker_id": "w1",
        "broker_type": "tradier", "asset_type": "equities",
        "trigger": "interval:60s",
        "symbols": [{"source": "custom", "file": "alpha-picks-scraper.csv"}],
    })
    await asyncio.sleep(0.2)

    task = sched._instances["d1"]._interval_task
    assert not task.done(), f"interval loop died: {task.exception()!r}"
    assert enqueued, "no tick was enqueued"
    assert enqueued[0][1]["trigger_kind"] == "interval"
    await sched.shutdown()
