import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from worker.agent import WorkerAgent


@pytest.mark.asyncio
async def test_send_activity_event_emits_well_formed_message():
    ws = MagicMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="w1", worker_name="W", websocket=ws)
    await agent.send_activity_event(
        instance_id="d1",
        event_type="trade_executed",
        severity="info",
        payload={"symbol": "AAPL"},
    )
    sent = ws.send.call_args.args[0]
    msg = json.loads(sent)
    assert msg["type"] == "activity_event"
    assert msg["worker_id"] == "w1"
    assert msg["instance_id"] == "d1"
    assert msg["event_type"] == "trade_executed"
    assert msg["severity"] == "info"
    assert msg["payload"] == {"symbol": "AAPL"}
    assert "timestamp" in msg


@pytest.mark.asyncio
async def test_send_algo_log_emits_well_formed_message():
    ws = MagicMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="w1", worker_name="W", websocket=ws)
    await agent.send_algo_log(
        instance_id="d1",
        logger_name="myalgo.signals",
        level="INFO",
        message="MACD crossed up",
    )
    sent = ws.send.call_args.args[0]
    msg = json.loads(sent)
    assert msg["type"] == "algo_log"
    assert msg["worker_id"] == "w1"
    assert msg["instance_id"] == "d1"
    assert msg["logger_name"] == "myalgo.signals"
    assert msg["level"] == "INFO"
    assert msg["message"] == "MACD crossed up"


@pytest.mark.asyncio
async def test_lifecycle_handlers_emit_activity_events():
    """When _handle_start_instance / _handle_stop_instance run, they
    should send both the legacy 'instance_started' event AND a new
    'activity_event' with event_type='instance_started'."""
    ws = MagicMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="w1", worker_name="W", websocket=ws)
    await agent._handle_start_instance({"instance_id": "d1", "config": {}, "persisted_state": None})

    sent_msgs = [json.loads(c.args[0]) for c in ws.send.call_args_list]
    assert any(m["type"] == "instance_started" for m in sent_msgs)
    assert any(m["type"] == "activity_event" and m["event_type"] == "instance_started" for m in sent_msgs)


@pytest.mark.asyncio
async def test_send_activity_event_default_payload_is_empty_dict():
    ws = MagicMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="w1", worker_name="W", websocket=ws)
    await agent.send_activity_event(instance_id="d1", event_type="idle_tick")
    msg = json.loads(ws.send.call_args.args[0])
    assert msg["payload"] == {}
    assert msg["severity"] == "info"


@pytest.mark.asyncio
async def test_send_activity_event_includes_worker_id():
    ws = MagicMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="pi-device-42", worker_name="W", websocket=ws)
    await agent.send_activity_event(instance_id=None, event_type="worker_ready")
    msg = json.loads(ws.send.call_args.args[0])
    assert msg["worker_id"] == "pi-device-42"
    assert msg["instance_id"] is None


@pytest.mark.asyncio
async def test_stop_lifecycle_emits_activity_event():
    """_handle_stop_instance should also emit an activity_event with event_type='instance_stopped'."""
    ws = MagicMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="w1", worker_name="W", websocket=ws)
    agent._running_instances["d2"] = {"status": "running"}
    await agent._handle_stop_instance({"instance_id": "d2"})

    sent_msgs = [json.loads(c.args[0]) for c in ws.send.call_args_list]
    assert any(m["type"] == "instance_stopped" for m in sent_msgs)
    assert any(m["type"] == "activity_event" and m["event_type"] == "instance_stopped" for m in sent_msgs)
