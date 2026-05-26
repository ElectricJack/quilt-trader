"""TickScheduler — per-instance tick orchestration with per-worker batching.

For each running AlgorithmInstance, subscribes to the live_feed_aggregator
according to the algorithm's manifest trigger (bar:tf, event, interval:Ns)
and enqueues a tick payload onto the per-worker outbound coalescer queue.
The coalescer drains every coalesce_ms (default 10ms), packing all pending
ticks for that worker into a single `tick_batch` ws message.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone


def _json_safe(obj):
    """Recursively convert datetime objects to ISO strings for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj
from typing import Any, Optional

logger = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(r"^interval:(\d+)([smh])$")
_INTERVAL_MULTS = {"s": 1, "m": 60, "h": 3600}


def _parse_interval_seconds(trigger: str) -> int:
    m = _INTERVAL_RE.match(trigger)
    if not m:
        raise ValueError(f"Not an interval trigger: {trigger!r}")
    return int(m.group(1)) * _INTERVAL_MULTS[m.group(2)]


class _WorkerOutbound:
    """Per-worker outbound queue + drain task."""

    def __init__(self, worker_id: str, ws_manager: Any, coalesce_ms: int) -> None:
        self.worker_id = worker_id
        self._ws_manager = ws_manager
        self._queue: asyncio.Queue = asyncio.Queue()
        self._coalesce_s = coalesce_ms / 1000.0
        self._drain_task: Optional[asyncio.Task] = None

    def ensure_running(self) -> None:
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def enqueue(self, tick: dict) -> None:
        await self._queue.put(tick)
        self.ensure_running()

    async def _drain_loop(self) -> None:
        try:
            while True:
                first = await self._queue.get()
                batch = [first]
                deadline = asyncio.get_running_loop().time() + self._coalesce_s
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        nxt = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                        batch.append(nxt)
                    except asyncio.TimeoutError:
                        break
                ws = self._ws_manager.worker_connections.get(self.worker_id)
                if ws is None:
                    logger.debug("Dropping batch for offline worker %s (%d ticks)",
                                self.worker_id, len(batch))
                    continue
                try:
                    await ws.send_json({"type": "tick_batch", "ticks": batch})
                except Exception:
                    logger.exception("Failed to send tick_batch to worker %s", self.worker_id[:8])
        except asyncio.CancelledError:
            return

    async def shutdown(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):
                pass


class _InstanceContext:
    def __init__(
        self,
        *,
        instance_id: str,
        run_id: str,
        worker_id: str,
        broker_type: str,
        asset_type: str,
        trigger: str,
        symbols: list[dict],
        scheduler: "TickScheduler",
    ) -> None:
        self.instance_id = instance_id
        self.run_id = run_id
        self.worker_id = worker_id
        self.broker_type = broker_type
        self.asset_type = asset_type
        self.trigger = trigger
        self.symbols = symbols
        self._scheduler = scheduler
        self._subscriptions: list = []
        self._interval_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self.trigger.startswith("bar:"):
            tf = self.trigger.split(":", 1)[1]
            for dep in self.symbols:
                sym = dep.get("symbol")
                if not sym:
                    continue
                cb = self._make_bar_callback(sym, tf)
                self._scheduler._aggregator.subscribe_bars(
                    self.broker_type, sym, tf, cb,
                )
                self._subscriptions.append(("bars", self.broker_type, sym, tf, cb))
        elif self.trigger == "event":
            for dep in self.symbols:
                sym = dep.get("symbol")
                if not sym:
                    continue
                cb = self._make_event_callback(sym)
                self._scheduler._aggregator.subscribe_events(
                    self.broker_type, sym, cb,
                )
                self._subscriptions.append(("events", self.broker_type, sym, cb))
        elif self.trigger.startswith("interval:"):
            secs = _parse_interval_seconds(self.trigger)
            self._interval_task = asyncio.create_task(self._interval_loop(secs))
        else:
            raise ValueError(f"Unknown trigger {self.trigger!r}")

    def _make_bar_callback(self, symbol: str, tf: str):
        async def cb(bar: dict) -> None:
            tick = _json_safe({
                "instance_id": self.instance_id,
                "run_id": self.run_id,
                "timestamp": bar.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "trigger_kind": "bar",
                "trigger_meta": {"timeframe": tf},
                "data": {symbol: {"timeframe": tf, "bars": [bar]}},
            })
            await self._scheduler._enqueue_tick(self.worker_id, tick)
        return cb

    def _make_event_callback(self, symbol: str):
        async def cb(event: dict) -> None:
            tick = _json_safe({
                "instance_id": self.instance_id,
                "run_id": self.run_id,
                "timestamp": event.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "trigger_kind": "event",
                "trigger_meta": {},
                "data": {symbol: {"event": event}},
            })
            await self._scheduler._enqueue_tick(self.worker_id, tick)
        return cb

    async def _interval_loop(self, interval_s: int) -> None:
        from coordinator.services.market_clock import is_market_open
        try:
            while True:
                now = datetime.now(timezone.utc)
                # Prefer a real symbol so registry dispatches accurately;
                # fall back to legacy asset_type for back-compat.
                probe = (self.symbols[0]["symbol"] if self.symbols else self.asset_type)
                if is_market_open(probe, now):
                    tick = {
                        "instance_id": self.instance_id,
                        "run_id": self.run_id,
                        "timestamp": now.isoformat(),
                        "trigger_kind": "interval",
                        "trigger_meta": {"seconds": interval_s},
                        "data": {},
                    }
                    await self._scheduler._enqueue_tick(self.worker_id, tick)
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        for sub in self._subscriptions:
            kind = sub[0]
            try:
                if kind == "bars":
                    _, broker, sym, tf, cb = sub
                    self._scheduler._aggregator.unsubscribe_bars(broker, sym, tf, cb)
                elif kind == "events":
                    _, broker, sym, cb = sub
                    self._scheduler._aggregator.unsubscribe_events(broker, sym, cb)
            except Exception:
                logger.exception("Failed to unsubscribe %r", sub)
        self._subscriptions.clear()
        if self._interval_task is not None:
            self._interval_task.cancel()
            try:
                await self._interval_task
            except (asyncio.CancelledError, Exception):
                pass
            self._interval_task = None


class TickScheduler:
    def __init__(self, *, aggregator: Any, ws_manager: Any, coalesce_ms: Optional[int] = None) -> None:
        self._aggregator = aggregator
        self._ws_manager = ws_manager
        self._coalesce_ms = coalesce_ms if coalesce_ms is not None else int(
            os.environ.get("QT_TICK_COALESCE_WINDOW_MS", "10")
        )
        self._instances: dict[str, _InstanceContext] = {}
        self._worker_outbound: dict[str, _WorkerOutbound] = {}

    async def start_instance(self, spec: dict) -> None:
        inst_id = spec["instance_id"]
        if inst_id in self._instances:
            await self.stop_instance(inst_id)
        ctx = _InstanceContext(
            instance_id=inst_id,
            run_id=spec["run_id"],
            worker_id=spec["worker_id"],
            broker_type=spec["broker_type"],
            asset_type=spec.get("asset_type", "equities"),
            trigger=spec["trigger"],
            symbols=spec.get("symbols") or [],
            scheduler=self,
        )
        await ctx.start()
        self._instances[inst_id] = ctx
        self._worker_outbound.setdefault(
            spec["worker_id"],
            _WorkerOutbound(spec["worker_id"], self._ws_manager, self._coalesce_ms),
        )

    async def stop_instance(self, instance_id: str) -> None:
        ctx = self._instances.pop(instance_id, None)
        if ctx is not None:
            await ctx.stop()

    async def drop_worker(self, worker_id: str) -> None:
        """Called when a worker disconnects. Cancels per-instance subs for
        all instances on that worker and shuts down the outbound queue."""
        to_stop = [iid for iid, ctx in self._instances.items() if ctx.worker_id == worker_id]
        for iid in to_stop:
            await self.stop_instance(iid)
        outbound = self._worker_outbound.pop(worker_id, None)
        if outbound is not None:
            await outbound.shutdown()

    async def _enqueue_tick(self, worker_id: str, tick: dict) -> None:
        outbound = self._worker_outbound.get(worker_id)
        if outbound is None:
            outbound = _WorkerOutbound(worker_id, self._ws_manager, self._coalesce_ms)
            self._worker_outbound[worker_id] = outbound
        await outbound.enqueue(tick)

    async def shutdown(self) -> None:
        for ctx in list(self._instances.values()):
            await ctx.stop()
        self._instances.clear()
        for outbound in list(self._worker_outbound.values()):
            await outbound.shutdown()
        self._worker_outbound.clear()
