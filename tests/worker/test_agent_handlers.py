import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from worker.agent import WorkerAgent, MessageRouter


class TestMessageRouter:
    @pytest.mark.asyncio
    async def test_dispatch_calls_handler(self):
        router = MessageRouter()
        handler = AsyncMock()
        router.register("test_type", handler)
        await router.dispatch({"type": "test_type", "data": "hello"})
        handler.assert_called_once_with({"type": "test_type", "data": "hello"})

    @pytest.mark.asyncio
    async def test_dispatch_unknown_type(self):
        router = MessageRouter()
        await router.dispatch({"type": "unknown"})  # should not raise


class TestWorkerAgent:
    @pytest.mark.asyncio
    async def test_handlers_registered(self):
        ws = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        assert "start_instance" in agent.router._handlers
        assert "stop_instance" in agent.router._handlers
        assert "heartbeat_ack" in agent.router._handlers

    @pytest.mark.asyncio
    async def test_start_instance(self):
        ws = AsyncMock()
        ws.send = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        await agent.router.dispatch({
            "type": "start_instance",
            "instance_id": "inst-1",
            "config": {"param": "value"},
        })
        assert "inst-1" in agent._running_instances

    @pytest.mark.asyncio
    async def test_stop_instance(self):
        ws = AsyncMock()
        ws.send = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        agent._running_instances["inst-1"] = {"status": "running"}
        await agent.router.dispatch({
            "type": "stop_instance",
            "instance_id": "inst-1",
        })
        assert "inst-1" not in agent._running_instances

    @pytest.mark.asyncio
    async def test_start_instance_sends_event(self):
        ws = AsyncMock()
        ws.send = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        await agent.router.dispatch({
            "type": "start_instance",
            "instance_id": "inst-2",
            "config": {},
        })
        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "instance_started"
        assert sent["instance_id"] == "inst-2"

    @pytest.mark.asyncio
    async def test_stop_instance_sends_event(self):
        ws = AsyncMock()
        ws.send = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        agent._running_instances["inst-3"] = {"status": "running"}
        await agent.router.dispatch({
            "type": "stop_instance",
            "instance_id": "inst-3",
        })
        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "instance_stopped"
        assert sent["instance_id"] == "inst-3"

    @pytest.mark.asyncio
    async def test_stop_instance_with_runner(self):
        ws = AsyncMock()
        ws.send = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        from worker.runner import AlgorithmRunner
        mock_runner = MagicMock(spec=AlgorithmRunner)
        mock_runner.stop.return_value = {"final": "state"}
        agent._running_instances["inst-4"] = {"status": "running", "runner": mock_runner}
        await agent.router.dispatch({
            "type": "stop_instance",
            "instance_id": "inst-4",
        })
        mock_runner.stop.assert_called_once()
        # Two sends: state_checkpoint + instance_stopped
        assert ws.send.call_count == 2
        calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        types = {c["type"] for c in calls}
        assert "state_checkpoint" in types
        assert "instance_stopped" in types

    @pytest.mark.asyncio
    async def test_heartbeat_ack_no_op(self):
        ws = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        # Should complete without error and without sending anything
        await agent.router.dispatch({"type": "heartbeat_ack"})
        ws.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_instance_stores_persisted_state(self):
        ws = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        await agent.router.dispatch({
            "type": "start_instance",
            "instance_id": "inst-5",
            "config": {"x": 1},
            "persisted_state": {"positions": ["TSLA"]},
        })
        info = agent._running_instances["inst-5"]
        assert info["persisted_state"] == {"positions": ["TSLA"]}
        assert info["config"] == {"x": 1}
        assert info["status"] == "starting"
