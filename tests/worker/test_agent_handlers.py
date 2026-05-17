import asyncio
import json
import pytest
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
    async def test_start_instance(self, monkeypatch):
        from worker import live_instance_runtime
        fake_runtime = MagicMock()
        fake_runtime.is_healthy = MagicMock(return_value=True)
        monkeypatch.setattr(
            live_instance_runtime.LiveInstanceRuntime, "bring_up",
            AsyncMock(return_value=fake_runtime),
        )
        ws = AsyncMock()
        ws.send = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        await agent.router.dispatch({
            "type": "start_instance",
            "instance_id": "inst-1",
            "run_id": "r1", "algorithm_id": "a", "algorithm_commit_sha": "s",
            "manifest": {"entry_point": "x", "class_name": "Z", "requirements": {"data_dependencies": []}},
            "broker_type": "alpaca", "environment": "paper",
            "credentials": {}, "config": {"param": "value"}, "persisted_state": None,
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
    async def test_start_instance_sends_event(self, monkeypatch):
        from worker import live_instance_runtime
        fake_runtime = MagicMock()
        fake_runtime.is_healthy = MagicMock(return_value=True)
        monkeypatch.setattr(
            live_instance_runtime.LiveInstanceRuntime, "bring_up",
            AsyncMock(return_value=fake_runtime),
        )
        ws = AsyncMock()
        ws.send = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        await agent.router.dispatch({
            "type": "start_instance",
            "instance_id": "inst-2",
            "run_id": "r1", "algorithm_id": "a", "algorithm_commit_sha": "s",
            "manifest": {"entry_point": "x", "class_name": "Z", "requirements": {"data_dependencies": []}},
            "broker_type": "alpaca", "environment": "paper",
            "credentials": {}, "config": {}, "persisted_state": None,
        })
        # Now sends two messages: instance_started + activity_event
        assert ws.send.call_count == 2
        calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        types = [c["type"] for c in calls]
        assert "instance_started" in types
        assert any(c["type"] == "activity_event" and c["event_type"] == "instance_started" for c in calls)

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
        # Now sends two messages: instance_stopped + activity_event
        assert ws.send.call_count == 2
        calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        types = [c["type"] for c in calls]
        assert "instance_stopped" in types
        assert any(c["type"] == "activity_event" and c["event_type"] == "instance_stopped" for c in calls)

    @pytest.mark.asyncio
    async def test_stop_instance_with_runner(self):
        ws = AsyncMock()
        ws.send = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        # New handler calls runtime.shut_down() directly (LiveInstanceRuntime interface).
        mock_runtime = MagicMock()
        mock_runtime.shut_down = AsyncMock(return_value={"final": "state"})
        agent._running_instances["inst-4"] = mock_runtime
        await agent.router.dispatch({
            "type": "stop_instance",
            "instance_id": "inst-4",
        })
        mock_runtime.shut_down.assert_awaited_once()
        # Three sends: state_checkpoint + instance_stopped + activity_event
        assert ws.send.call_count == 3
        calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        types = {c["type"] for c in calls}
        assert "state_checkpoint" in types
        assert "instance_stopped" in types
        assert "activity_event" in types

    @pytest.mark.asyncio
    async def test_heartbeat_ack_no_op(self):
        ws = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        # Should complete without error and without sending anything
        await agent.router.dispatch({"type": "heartbeat_ack"})
        ws.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_instance_stores_persisted_state(self, monkeypatch):
        from worker import live_instance_runtime
        fake_runtime = MagicMock()
        fake_runtime.is_healthy = MagicMock(return_value=True)
        bring_up_mock = AsyncMock(return_value=fake_runtime)
        monkeypatch.setattr(
            live_instance_runtime.LiveInstanceRuntime, "bring_up", bring_up_mock,
        )
        ws = AsyncMock()
        agent = WorkerAgent(worker_id="test-id", worker_name="test", websocket=ws)
        await agent.router.dispatch({
            "type": "start_instance",
            "instance_id": "inst-5",
            "run_id": "r1", "algorithm_id": "a", "algorithm_commit_sha": "s",
            "manifest": {"entry_point": "x", "class_name": "Z", "requirements": {"data_dependencies": []}},
            "broker_type": "alpaca", "environment": "paper",
            "credentials": {},
            "config": {"x": 1},
            "persisted_state": {"positions": ["TSLA"]},
        })
        assert "inst-5" in agent._running_instances
        # Verify bring_up received the config and persisted_state correctly.
        call_kwargs = bring_up_mock.call_args.kwargs
        assert call_kwargs["config"] == {"x": 1}
        assert call_kwargs["persisted_state"] == {"positions": ["TSLA"]}


@pytest.mark.asyncio
async def test_handle_start_instance_invokes_runtime_bring_up(monkeypatch):
    from worker.agent import WorkerAgent
    from worker import live_instance_runtime

    fake_runtime = MagicMock()
    fake_runtime.is_healthy = MagicMock(return_value=True)
    monkeypatch.setattr(
        live_instance_runtime.LiveInstanceRuntime, "bring_up",
        AsyncMock(return_value=fake_runtime),
    )

    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
    )
    await agent._handle_start_instance({
        "instance_id": "d1",
        "run_id": "r1",
        "algorithm_id": "algo-1",
        "algorithm_commit_sha": "sha-abc",
        "manifest": {"entry_point": "x.y", "class_name": "Z", "trigger": "bar:1min",
                     "requirements": {"data_dependencies": []}},
        "broker_type": "alpaca",
        "environment": "paper",
        "credentials": {"api_key": "k", "secret_key": "s"},
        "config": {},
        "persisted_state": None,
    })
    assert "d1" in agent._running_instances
    assert agent._running_instances["d1"] is fake_runtime


@pytest.mark.asyncio
async def test_handle_start_instance_emits_instance_error_on_bring_up_failure(monkeypatch):
    from worker.agent import WorkerAgent
    from worker import live_instance_runtime

    monkeypatch.setattr(
        live_instance_runtime.LiveInstanceRuntime, "bring_up",
        AsyncMock(side_effect=RuntimeError("nope")),
    )

    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
    )
    await agent._handle_start_instance({
        "instance_id": "d1", "run_id": "r1",
        "algorithm_id": "algo-1", "algorithm_commit_sha": "sha",
        "manifest": {"entry_point": "x", "class_name": "Z",
                     "requirements": {"data_dependencies": []}},
        "broker_type": "alpaca", "environment": "paper",
        "credentials": {}, "config": {}, "persisted_state": None,
    })
    sent_jsons = [json.loads(c.args[0]) for c in ws.send.call_args_list]
    assert any(m.get("type") == "instance_error" for m in sent_jsons)
    assert "d1" not in agent._running_instances


@pytest.mark.asyncio
async def test_handle_start_instance_idempotent_when_already_healthy(monkeypatch):
    from worker.agent import WorkerAgent
    from worker import live_instance_runtime

    existing = MagicMock()
    existing.is_healthy = MagicMock(return_value=True)

    bring_up = AsyncMock()
    monkeypatch.setattr(
        live_instance_runtime.LiveInstanceRuntime, "bring_up", bring_up,
    )

    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
    )
    agent._running_instances["d1"] = existing
    await agent._handle_start_instance({
        "instance_id": "d1", "run_id": "r1",
        "algorithm_id": "a", "algorithm_commit_sha": "s",
        "manifest": {"entry_point": "x", "class_name": "Z",
                     "requirements": {"data_dependencies": []}},
        "broker_type": "alpaca", "environment": "paper",
        "credentials": {}, "config": {}, "persisted_state": None,
    })
    bring_up.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_tick_batch_dispatches_to_runtimes():
    from worker.agent import WorkerAgent
    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
    )
    runtime_a = MagicMock()
    runtime_a.on_tick_batch_entry = AsyncMock()
    runtime_b = MagicMock()
    runtime_b.on_tick_batch_entry = AsyncMock()
    agent._running_instances["d1"] = runtime_a
    agent._running_instances["d2"] = runtime_b
    await agent._handle_tick_batch({
        "type": "tick_batch",
        "ticks": [
            {"instance_id": "d1", "run_id": "r1", "timestamp": "2026-05-16T12:00:00Z"},
            {"instance_id": "d2", "run_id": "r2", "timestamp": "2026-05-16T12:00:00Z"},
            {"instance_id": "unknown", "timestamp": "..."},
        ],
    })
    # Spawn happens async via create_task; give it a moment.
    await asyncio.sleep(0.01)
    runtime_a.on_tick_batch_entry.assert_awaited_once()
    runtime_b.on_tick_batch_entry.assert_awaited_once()
