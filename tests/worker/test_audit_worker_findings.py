"""Failing tests for worker audit findings F16, F17, F18.

See docs/superpowers/research/2026-06-11-correctness-audit-findings.md §6.
Each test asserts the CORRECT behavior; it fails today because the bug exists
and must pass once the finding is fixed.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from sdk.signals import Signal, SignalType
from worker.agent import WorkerAgent
from worker.broker_adapter import MockBrokerAdapter
from worker.caching_broker_adapter import CachingBrokerAdapter
from worker.runner import AlgorithmRunner
from worker.tick_loop import TickProcessor


# ---------------------------------------------------------------------------
# F16 — CachingBrokerAdapter.invalidate() never called after fills
# ---------------------------------------------------------------------------

class _SimpleAlgo:
    def __init__(self):
        self._signals = []

    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx): return list(self._signals)
    def on_stop(self): return {}
    def save_state(self): return {}
    def on_signal_rejected(self, signal, reason): pass
    def on_trade_executed(self, signal, fill): pass
    def notify(self, event_name, message, data=None): pass
    def drain_notifications(self): return []


class _SpyCachingAdapter(CachingBrokerAdapter):
    def __init__(self, inner, **kwargs):
        super().__init__(inner, **kwargs)
        self.invalidate_calls = 0

    def invalidate(self) -> None:
        self.invalidate_calls += 1
        super().invalidate()


@pytest.mark.asyncio
async def test_f16_invalidate_called_after_successful_order():
    """F16: worker/caching_broker_adapter.py:41 defines invalidate() and the
    module docstring (line 9) requires calling it after an order succeeds, but
    no code path ever calls it — position/balance reads stay stale for the
    full cache TTL after a fill."""
    inner = MockBrokerAdapter()
    inner.set_fill_price(150.0)
    broker = _SpyCachingAdapter(inner, account_state_ttl=30)

    algo = _SimpleAlgo()
    algo._signals = [Signal.simple("AAPL", SignalType.BUY, 100)]
    runner = AlgorithmRunner(
        instance_id="inst-1", algorithm=algo, config={}, restored_state=None,
    )
    runner.start()

    coordinator = AsyncMock()
    coordinator.request_signal_approval.return_value = {"approved": True}

    processor = TickProcessor(
        runner=runner, broker=broker,
        data_client=AsyncMock(), coordinator_client=coordinator,
    )
    result = await processor.process_tick(datetime.now(timezone.utc))

    assert result.trades_executed == 1  # sanity: the order path actually ran
    assert broker.invalidate_calls >= 1, (
        "CachingBrokerAdapter.invalidate() must be called after a successful "
        f"order so the next tick reads fresh positions; call count was "
        f"{broker.invalidate_calls}"
    )


# ---------------------------------------------------------------------------
# F17 — signal-approval futures keyed by instance_id only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f17_concurrent_signal_approvals_for_same_instance_both_resolve():
    """F17: worker/agent.py:113 — request_signal_approval stores its future as
    _pending_signal_responses[instance_id]; a second concurrent request for the
    same instance overwrites the first future, which then never resolves (30 s
    timeout -> spurious rejection)."""
    ws = AsyncMock()
    agent = WorkerAgent(worker_id="w", worker_name="pi", websocket=ws)

    t1 = asyncio.create_task(
        agent.request_signal_approval(instance_id="inst-1", signal={"n": 1})
    )
    t2 = asyncio.create_task(
        agent.request_signal_approval(instance_id="inst-1", signal={"n": 2})
    )
    # Let both requests register their futures and send.
    await asyncio.sleep(0.01)

    # Coordinator answers both outstanding requests.
    await agent.router.dispatch(
        {"type": "signal_response", "instance_id": "inst-1", "approved": True}
    )
    await agent.router.dispatch(
        {"type": "signal_response", "instance_id": "inst-1", "approved": True}
    )

    done, pending = await asyncio.wait({t1, t2}, timeout=1.0)
    for task in pending:
        task.cancel()
    assert not pending, (
        f"{len(pending)} of 2 concurrent signal-approval requests for the "
        "same instance never resolved within 1 s — its future was overwritten "
        "by the second request (futures keyed by instance_id only)"
    )
    for task in (t1, t2):
        result = task.result()
        assert result.get("approved") is True, (
            f"both approvals must resolve to the coordinator's response, got "
            f"{result!r} (timeout-rejection dict)"
        )


# ---------------------------------------------------------------------------
# F18 — fire-and-forget tick tasks lose per-instance ordering
# ---------------------------------------------------------------------------

class _RecordingRuntime:
    """Fake runtime: first entry is slow, second is instant — if the agent
    does not serialize per-instance processing, the second entry finishes
    before the first."""

    def __init__(self):
        self.events: list[tuple[str, str]] = []
        self._delays = {"e1": 0.05, "e2": 0.0}

    def is_healthy(self):
        return True

    async def on_tick_batch_entry(self, entry: dict) -> None:
        eid = entry["entry_id"]
        self.events.append((eid, "start"))
        await asyncio.sleep(self._delays[eid])
        self.events.append((eid, "end"))


@pytest.mark.asyncio
async def test_f18_tick_batch_entries_for_one_instance_are_serialized():
    """F18: worker/agent.py:224 — _handle_tick_batch fires
    asyncio.create_task(runtime.on_tick_batch_entry(entry)) with no reference
    kept and no per-instance serialization, so two entries for the same
    instance interleave out of order."""
    ws = AsyncMock()
    agent = WorkerAgent(worker_id="w", worker_name="pi", websocket=ws)
    runtime = _RecordingRuntime()
    agent._running_instances["inst-1"] = runtime

    await agent.router.dispatch({
        "type": "tick_batch",
        "ticks": [
            {"instance_id": "inst-1", "entry_id": "e1",
             "timestamp": "2026-01-02T14:30:00+00:00", "data": {}},
            {"instance_id": "inst-1", "entry_id": "e2",
             "timestamp": "2026-01-02T14:31:00+00:00", "data": {}},
        ],
    })

    # Bounded wait for both entries to finish processing.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while len(runtime.events) < 4 and loop.time() < deadline:
        await asyncio.sleep(0.01)

    assert len(runtime.events) == 4, (
        f"both entries must complete within 1 s; recorded {runtime.events!r}"
    )
    assert runtime.events == [
        ("e1", "start"), ("e1", "end"), ("e2", "start"), ("e2", "end"),
    ], (
        "tick entries for the same instance must be processed in order "
        f"(e1 must END before e2 STARTS); recorded {runtime.events!r}"
    )
