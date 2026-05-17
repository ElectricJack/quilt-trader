import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_agent():
    agent = MagicMock()
    agent.worker_id = "w1"
    agent.worker_install_token = "tok"
    agent.coordinator_http_url = "http://fake-coord:8000"
    agent._send = AsyncMock()
    agent.send_event = AsyncMock()
    agent.send_activity_event = AsyncMock()
    agent.send_state_checkpoint = AsyncMock()
    return agent


def _make_manifest():
    return {
        "name": "test-algo",
        "entry_point": "test_algo.algorithm",
        "class_name": "TestAlgo",
        "trigger": "bar:1min",
        "requirements": {
            "data_dependencies": [
                {"symbol": "AAPL", "timeframe": "1min", "history_bars": 50},
            ],
        },
    }


class FakeAlgo:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.tick_count = 0
        self.state = {}
    def on_start(self, config, restored_state):
        self.started = True
        if restored_state:
            self.state = restored_state
    def on_tick(self, ctx):
        self.tick_count += 1
        return []
    def on_stop(self):
        self.stopped = True
        return self.state
    def save_state(self):
        return {"ticks": self.tick_count}
    def on_signal_rejected(self, signal, reason): pass
    def on_trade_executed(self, signal, fill): pass


def _fake_broker():
    b = MagicMock()
    b.get_account_info = MagicMock(return_value={
        "cash": 100, "portfolio_value": 150, "buying_power": 100,
    })
    b.get_positions = MagicMock(return_value={})
    return b


@pytest.mark.asyncio
async def test_bring_up_loads_algorithm_and_starts_runner(tmp_path, monkeypatch):
    from worker import live_instance_runtime, package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(live_instance_runtime.package_cache, "ensure",
                        AsyncMock(return_value=tmp_path / "fake"))
    monkeypatch.setattr(live_instance_runtime.package_cache, "load_algorithm_class",
                        MagicMock(return_value=FakeAlgo))
    monkeypatch.setattr(live_instance_runtime, "make_broker_adapter",
                        MagicMock(return_value=_fake_broker()))

    agent = _make_agent()
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=__import__("pandas").DataFrame())
    runtime = await live_instance_runtime.LiveInstanceRuntime.bring_up(
        agent=agent, instance_id="d1", run_id="r1",
        algorithm_id="algo-1", algorithm_commit_sha="sha-abc",
        manifest=_make_manifest(),
        config={"foo": "bar"}, persisted_state=None,
        broker_type="alpaca", environment="paper",
        credentials={"api_key": "k", "secret_key": "s"},
        data_client=data_client,
    )
    assert runtime.is_healthy()


@pytest.mark.asyncio
async def test_bring_up_passes_persisted_state_to_algorithm(tmp_path, monkeypatch):
    from worker import live_instance_runtime, package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(live_instance_runtime.package_cache, "ensure",
                        AsyncMock(return_value=tmp_path / "fake"))
    monkeypatch.setattr(live_instance_runtime.package_cache, "load_algorithm_class",
                        MagicMock(return_value=FakeAlgo))
    monkeypatch.setattr(live_instance_runtime, "make_broker_adapter",
                        MagicMock(return_value=_fake_broker()))
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=__import__("pandas").DataFrame())
    runtime = await live_instance_runtime.LiveInstanceRuntime.bring_up(
        agent=_make_agent(), instance_id="d1", run_id="r1",
        algorithm_id="algo-1", algorithm_commit_sha="sha-abc",
        manifest=_make_manifest(),
        config={}, persisted_state={"last_signal": "buy"},
        broker_type="alpaca", environment="paper",
        credentials={"api_key": "k", "secret_key": "s"},
        data_client=data_client,
    )
    assert runtime._runner._algorithm.state == {"last_signal": "buy"}


@pytest.mark.asyncio
async def test_shut_down_calls_algorithm_on_stop(tmp_path, monkeypatch):
    from worker import live_instance_runtime, package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(live_instance_runtime.package_cache, "ensure",
                        AsyncMock(return_value=tmp_path / "fake"))
    monkeypatch.setattr(live_instance_runtime.package_cache, "load_algorithm_class",
                        MagicMock(return_value=FakeAlgo))
    monkeypatch.setattr(live_instance_runtime, "make_broker_adapter",
                        MagicMock(return_value=_fake_broker()))
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=__import__("pandas").DataFrame())
    runtime = await live_instance_runtime.LiveInstanceRuntime.bring_up(
        agent=_make_agent(), instance_id="d1", run_id="r1",
        algorithm_id="algo-1", algorithm_commit_sha="sha-abc",
        manifest=_make_manifest(), config={}, persisted_state=None,
        broker_type="alpaca", environment="paper",
        credentials={"api_key": "k", "secret_key": "s"},
        data_client=data_client,
    )
    final = await runtime.shut_down()
    assert runtime._runner._algorithm.stopped
    assert isinstance(final, dict)


@pytest.mark.asyncio
async def test_on_tick_batch_entry_calls_algo_on_tick(tmp_path, monkeypatch):
    from worker import live_instance_runtime, package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(live_instance_runtime.package_cache, "ensure",
                        AsyncMock(return_value=tmp_path / "fake"))
    monkeypatch.setattr(live_instance_runtime.package_cache, "load_algorithm_class",
                        MagicMock(return_value=FakeAlgo))
    monkeypatch.setattr(live_instance_runtime, "make_broker_adapter",
                        MagicMock(return_value=_fake_broker()))
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=__import__("pandas").DataFrame())
    agent = _make_agent()
    runtime = await live_instance_runtime.LiveInstanceRuntime.bring_up(
        agent=agent, instance_id="d1", run_id="r1",
        algorithm_id="algo-1", algorithm_commit_sha="sha-abc",
        manifest=_make_manifest(), config={}, persisted_state=None,
        broker_type="alpaca", environment="paper",
        credentials={"api_key": "k", "secret_key": "s"},
        data_client=data_client,
    )
    await runtime.on_tick_batch_entry({
        "instance_id": "d1", "run_id": "r1",
        "timestamp": "2026-05-16T12:00:00Z",
        "trigger_kind": "bar",
        "data": {},
    })
    assert runtime._runner._algorithm.tick_count == 1
    # state_checkpoint should have been sent
    agent.send_state_checkpoint.assert_awaited()


@pytest.mark.asyncio
async def test_5_consecutive_tick_failures_triggers_instance_error(tmp_path, monkeypatch):
    """Algorithm raises on every tick — runtime should self-stop after 5 strikes."""
    from worker import live_instance_runtime, package_cache

    class BadAlgo(FakeAlgo):
        def on_tick(self, ctx):
            raise RuntimeError("boom")

    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(live_instance_runtime.package_cache, "ensure",
                        AsyncMock(return_value=tmp_path / "fake"))
    monkeypatch.setattr(live_instance_runtime.package_cache, "load_algorithm_class",
                        MagicMock(return_value=BadAlgo))
    monkeypatch.setattr(live_instance_runtime, "make_broker_adapter",
                        MagicMock(return_value=_fake_broker()))
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=__import__("pandas").DataFrame())
    agent = _make_agent()
    runtime = await live_instance_runtime.LiveInstanceRuntime.bring_up(
        agent=agent, instance_id="d1", run_id="r1",
        algorithm_id="algo-1", algorithm_commit_sha="sha-abc",
        manifest=_make_manifest(), config={}, persisted_state=None,
        broker_type="alpaca", environment="paper",
        credentials={"api_key": "k", "secret_key": "s"},
        data_client=data_client,
    )
    for _ in range(5):
        await runtime.on_tick_batch_entry({
            "instance_id": "d1", "run_id": "r1",
            "timestamp": "2026-05-16T12:00:00Z",
            "trigger_kind": "bar", "data": {},
        })
    # After 5 strikes, an instance_error should have been sent.
    sent_event_types = [c.args[0] for c in agent.send_event.call_args_list]
    assert "instance_error" in sent_event_types
    assert not runtime.is_healthy()
