"""LiveInstanceRuntime — the runtime hosting one running algorithm instance.

Composes M2's building blocks (package_cache, RollingDataBuffer,
CachingBrokerAdapter, AlgorithmRunner, LiveObserver, TickProcessor) into
a single object the worker holds in self._running_instances[inst_id]. Owns
the instance's lifecycle (bring_up, on_tick_batch_entry, shut_down).
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime
from typing import Any, Optional

from worker import package_cache
from worker.adapter_factory import make_broker_adapter
from worker.caching_broker_adapter import CachingBrokerAdapter
from worker.live_observer import LiveObserver
from worker.rolling_data_buffer import RollingDataBuffer
from worker.runner import AlgorithmRunner, RunnerState
from worker.tick_loop import TickProcessor

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 5


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _last_n_traceback_lines(n: int) -> str:
    return "\n".join(traceback.format_exc().splitlines()[-n:])


class LiveInstanceRuntime:
    def __init__(
        self,
        *,
        instance_id: str,
        run_id: str,
        runner: AlgorithmRunner,
        broker: CachingBrokerAdapter,
        buffer: RollingDataBuffer,
        observer: LiveObserver,
        tick_processor: TickProcessor,
        agent: Any,
        data_client: Any,
    ) -> None:
        self._instance_id = instance_id
        self._run_id = run_id
        self._runner = runner
        self._broker = broker
        self._buffer = buffer
        self._observer = observer
        self._tick_processor = tick_processor
        self._agent = agent
        self._data_client = data_client
        self._consecutive_failures = 0

    @classmethod
    async def bring_up(
        cls,
        *,
        agent: Any,
        instance_id: str,
        run_id: str,
        algorithm_id: str,
        algorithm_commit_sha: str,
        manifest: dict,
        config: dict,
        persisted_state: Optional[dict],
        broker_type: str,
        environment: str,
        credentials: dict,
        data_client: Any,
    ) -> "LiveInstanceRuntime":
        # 1. Ensure algorithm package is cached locally.
        pkg_dir = await package_cache.ensure(
            agent=agent, algorithm_id=algorithm_id, commit_sha=algorithm_commit_sha,
        )
        # 2. Import the algorithm class.
        algo_cls = package_cache.load_algorithm_class(
            pkg_dir=pkg_dir,
            entry_point=manifest["entry_point"],
            class_name=manifest["class_name"],
        )
        algo = algo_cls()
        # 3. Build the broker adapter and wrap with the TTL cache.
        raw_broker = make_broker_adapter(broker_type, environment, credentials)
        broker = CachingBrokerAdapter(raw_broker, account_state_ttl=30)
        # 4. Build rolling data buffer from manifest.requirements.data_dependencies.
        data_deps = (manifest.get("requirements") or {}).get("data_dependencies") or []
        buffer = RollingDataBuffer(data_deps)
        await buffer.backfill(data_client)
        # 5. Build the AlgorithmRunner (also wires the algo log shipper from M4.4).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        runner = AlgorithmRunner(
            instance_id=instance_id,
            algorithm=algo,
            config=config,
            restored_state=persisted_state,
            agent=agent,
            loop=loop,
        )
        runner.start()  # calls algo.on_start(config, restored_state)
        # 6. Build the LiveObserver.
        observer = LiveObserver(
            agent=agent, broker=broker,
            instance_id=instance_id, run_id=run_id,
        )
        # 7. Build the TickProcessor with the live_observer wired in.
        tick_processor = TickProcessor(
            runner=runner,
            broker=broker,
            data_client=data_client,
            coordinator_client=agent,
            live_observer=observer,
        )
        return cls(
            instance_id=instance_id, run_id=run_id,
            runner=runner, broker=broker, buffer=buffer,
            observer=observer, tick_processor=tick_processor,
            agent=agent, data_client=data_client,
        )

    def is_healthy(self) -> bool:
        return (
            self._runner.state == RunnerState.RUNNING
            and self._consecutive_failures < MAX_CONSECUTIVE_FAILURES
        )

    async def on_tick_batch_entry(self, entry: dict) -> None:
        # 1. Merge pushed delta into rolling buffer.
        data = entry.get("data") or {}
        if data:
            self._buffer.ingest(data)
        ts = _parse_iso(entry["timestamp"])
        # 2. Process the tick.
        try:
            await self._tick_processor.process_tick(ts)
            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            tb = _last_n_traceback_lines(20)
            await self._agent.send_activity_event(
                self._instance_id, "algo_exception", severity="error",
                payload={"error": str(e), "traceback_tail": tb},
            )
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                await self._agent.send_event(
                    "instance_error", self._instance_id,
                    payload={"reason": f"{MAX_CONSECUTIVE_FAILURES} consecutive tick failures"},
                )
                await self.shut_down()
                return
        # 3. Emit equity sample (LiveObserver fetches account state via broker).
        try:
            await self._observer.on_tick(timestamp=ts.isoformat())
        except Exception:
            logger.exception("Equity-sample emission failed for %s", self._instance_id)
        # 4. Checkpoint state after every tick (best-effort).
        try:
            state = self._runner.save_state()
            await self._agent.send_state_checkpoint(self._instance_id, state)
        except Exception:
            logger.exception("Checkpoint failed for %s", self._instance_id)

    async def shut_down(self) -> dict:
        try:
            return self._runner.stop()  # calls algo.on_stop()
        except Exception:
            logger.exception("Algorithm on_stop raised; using save_state fallback")
            try:
                return self._runner.save_state()
            except Exception:
                logger.exception("save_state also failed")
                return {}
