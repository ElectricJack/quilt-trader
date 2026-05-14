import pytest
import json
from unittest.mock import AsyncMock

from worker.agent import WorkerAgent, MessageRouter


@pytest.mark.asyncio
async def test_message_router_dispatches():
    router = MessageRouter()
    received = []
    async def handler(msg): received.append(msg)
    router.register("start_algorithm", handler)
    await router.dispatch({"type": "start_algorithm", "instance_id": "i-1"})
    assert len(received) == 1
    assert received[0]["instance_id"] == "i-1"


@pytest.mark.asyncio
async def test_message_router_unknown_type():
    router = MessageRouter()
    await router.dispatch({"type": "unknown_command"})


@pytest.mark.asyncio
async def test_agent_sends_heartbeat():
    ws = AsyncMock()
    agent = WorkerAgent(worker_name="test-pi", websocket=ws)
    await agent.send_heartbeat()
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "heartbeat"
    assert sent["worker_name"] == "test-pi"


@pytest.mark.asyncio
async def test_agent_sends_event():
    ws = AsyncMock()
    agent = WorkerAgent(worker_name="test-pi", websocket=ws)
    await agent.send_event(event_type="trade_executed", instance_id="inst-1", payload={"symbol": "AAPL", "side": "buy"})
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "trade_executed"
    assert sent["instance_id"] == "inst-1"
    assert sent["payload"]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_agent_sends_signal_request():
    ws = AsyncMock()
    ws.recv.return_value = json.dumps({"type": "signal_approved", "approved": True})
    agent = WorkerAgent(worker_name="test-pi", websocket=ws)
    result = await agent.request_signal_approval(instance_id="inst-1", signal={"legs": [{"symbol": "AAPL"}]})
    assert result["approved"] is True
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "signal_request"


@pytest.mark.asyncio
async def test_agent_sends_state_checkpoint():
    ws = AsyncMock()
    agent = WorkerAgent(worker_name="test-pi", websocket=ws)
    await agent.send_state_checkpoint(instance_id="inst-1", state={"positions": ["AAPL"]})
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "state_checkpoint"
    assert sent["state"]["positions"] == ["AAPL"]


@pytest.mark.asyncio
async def test_agent_sends_decision_log():
    ws = AsyncMock()
    agent = WorkerAgent(worker_name="test-pi", websocket=ws)
    await agent.send_decision_log(instance_id="inst-1", log_entry={"timestamp": "2025-01-01T09:30:00", "signals_produced": []})
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "decision_log"
