"""End-to-end smoke test: a tick_batch ws message into the worker results in
an algorithm tick + equity_sample + state_checkpoint being emitted back.

This test mocks the package fetch and the broker but exercises the real
WorkerAgent, LiveInstanceRuntime, AlgorithmRunner, RollingDataBuffer,
LiveObserver, TickProcessor, and LiveTickContext composed together.
"""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_tick_batch_results_in_algo_tick_and_equity_sample_emission(tmp_path, monkeypatch):
    import pandas as pd
    from worker.agent import WorkerAgent
    from worker import live_instance_runtime, package_cache

    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(
        live_instance_runtime.package_cache, "ensure",
        AsyncMock(return_value=tmp_path / "fake"),
    )

    # Track that the algorithm actually saw a tick.
    class FakeAlgo:
        ticks: list = []
        def on_start(self, config, restored_state):
            pass
        def on_tick(self, ctx):
            FakeAlgo.ticks.append(ctx.timestamp)
            return []
        def on_stop(self):
            return {}
        def save_state(self):
            return {"n": len(FakeAlgo.ticks)}
        def on_signal_rejected(self, *args):
            pass
        def on_trade_executed(self, *args):
            pass

    monkeypatch.setattr(
        live_instance_runtime.package_cache, "load_algorithm_class",
        MagicMock(return_value=FakeAlgo),
    )

    fake_broker = MagicMock()
    fake_broker.get_account_info = MagicMock(return_value={
        "cash": 100.0,
        "portfolio_value": 150.0,
        "buying_power": 100.0,
    })
    fake_broker.get_positions = MagicMock(return_value={})
    monkeypatch.setattr(
        live_instance_runtime, "make_broker_adapter",
        MagicMock(return_value=fake_broker),
    )

    sent_jsons: list[dict] = []
    ws = AsyncMock()
    async def fake_send(s):
        sent_jsons.append(json.loads(s))
    ws.send = AsyncMock(side_effect=fake_send)

    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=pd.DataFrame())

    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
        data_client=data_client,
    )

    # Bring up the instance.
    await agent._handle_start_instance({
        "instance_id": "d1",
        "run_id": "r1",
        "algorithm_id": "algo-1",
        "algorithm_commit_sha": "sha",
        "manifest": {
            "entry_point": "x",
            "class_name": "F",
            "trigger": "bar:1min",
            "requirements": {"data_dependencies": []},
        },
        "broker_type": "alpaca",
        "environment": "paper",
        "credentials": {"api_key": "k", "secret_key": "s"},
        "config": {},
        "persisted_state": None,
    })
    assert "d1" in agent._running_instances

    # Send a tick_batch carrying a tick for this instance.
    await agent._handle_tick_batch({
        "type": "tick_batch",
        "ticks": [{
            "instance_id": "d1",
            "run_id": "r1",
            "timestamp": "2026-05-16T13:34:00Z",
            "trigger_kind": "bar",
            "trigger_meta": {"timeframe": "1min"},
            "data": {},
        }],
    })

    # The handler spawns per-instance tasks via create_task; give them a tick.
    await asyncio.sleep(0.1)

    # The algorithm received exactly one tick.
    assert len(FakeAlgo.ticks) == 1

    # The agent emitted an equity sample and a state checkpoint after the tick.
    types_sent = [m.get("type") for m in sent_jsons]
    assert "equity_sample" in types_sent, f"Expected equity_sample in {types_sent}"
    assert "state_checkpoint" in types_sent, f"Expected state_checkpoint in {types_sent}"


@pytest.mark.asyncio
async def test_tick_batch_for_unknown_instance_is_silently_ignored(tmp_path, monkeypatch):
    """A tick_batch entry for an unknown instance should not raise."""
    from worker.agent import WorkerAgent

    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
        data_client=AsyncMock(),
    )

    # No instances running.
    await agent._handle_tick_batch({
        "type": "tick_batch",
        "ticks": [{
            "instance_id": "nonexistent",
            "run_id": "r",
            "timestamp": "2026-05-16T13:34:00Z",
            "data": {},
        }],
    })
    # No crash, no messages sent.
    await asyncio.sleep(0.05)
    assert ws.send.call_count == 0
