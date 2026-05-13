import pytest
from coordinator.services.event_bus import EventBus, SystemEvent


@pytest.mark.asyncio
async def test_subscribe_and_publish():
    bus = EventBus()
    received = []
    async def handler(event): received.append(event)
    bus.subscribe("trade_executed", handler)
    await bus.publish(SystemEvent(event_type="trade_executed", source_type="algorithm", source_id="inst-123", severity="info", payload={"symbol": "AAPL"}))
    assert len(received) == 1
    assert received[0].payload["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_multiple_subscribers():
    bus = EventBus()
    a, b = [], []
    async def ha(e): a.append(e)
    async def hb(e): b.append(e)
    bus.subscribe("algo_started", ha)
    bus.subscribe("algo_started", hb)
    await bus.publish(SystemEvent(event_type="algo_started", source_type="system", severity="info"))
    assert len(a) == 1 and len(b) == 1


@pytest.mark.asyncio
async def test_wildcard_subscriber():
    bus = EventBus()
    received = []
    async def catch_all(e): received.append(e)
    bus.subscribe("*", catch_all)
    await bus.publish(SystemEvent(event_type="trade_executed", source_type="algorithm", severity="info"))
    await bus.publish(SystemEvent(event_type="algo_error", source_type="system", severity="error"))
    assert len(received) == 2


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    received = []
    async def handler(e): received.append(e)
    bus.subscribe("algo_stopped", handler)
    await bus.publish(SystemEvent(event_type="algo_stopped", source_type="system", severity="info"))
    assert len(received) == 1
    bus.unsubscribe("algo_stopped", handler)
    await bus.publish(SystemEvent(event_type="algo_stopped", source_type="system", severity="info"))
    assert len(received) == 1


@pytest.mark.asyncio
async def test_no_subscribers_does_not_error():
    bus = EventBus()
    await bus.publish(SystemEvent(event_type="unknown", source_type="system", severity="info"))


@pytest.mark.asyncio
async def test_system_event_fields():
    event = SystemEvent(event_type="pdt_warning", source_type="system", source_id="account-456", severity="warning", payload={"day_trade_count": 3})
    assert event.event_type == "pdt_warning"
    assert event.source_id == "account-456"
    assert event.payload["day_trade_count"] == 3


@pytest.mark.asyncio
async def test_handler_error_isolation():
    bus = EventBus()
    results = []
    async def bad(e): raise ValueError("boom")
    async def good(e): results.append(e)
    bus.subscribe("test_event", bad)
    bus.subscribe("test_event", good)
    await bus.publish(SystemEvent(event_type="test_event", source_type="system", severity="info"))
    assert len(results) == 1
