# Live Algorithm Execution — Design Spec

**Date:** 2026-05-16
**Status:** Draft for implementation
**Scope:** End-to-end live execution of trading algorithms on worker Pis: code distribution, tick scheduling, market data routing, order execution, lifecycle, and recovery.

**Reference:** Builds on the previous spec `2026-05-16-running-algorithm-ux-design.md` (which built the UX surface, status flow, activity stream, and live data report pipeline). The previous spec assumed workers would emit `equity_sample` and `trade_sample` events; this spec actually makes the worker run the algorithm so those events get emitted.

---

## 1. Motivation

The previous spec shipped the UX, status flow, and live-report finalizer end-to-end on the assumption that the worker is actually running the algorithm and emitting per-tick samples. It isn't. `worker/agent.py`'s `_handle_start_instance` is a stub: it stores the instance's config in a dict, sends `instance_started` back, and does nothing else. No algorithm code runs on the Pi. No samples emit. No log lines ship. The deployment page shows "Running" but nothing flows.

This spec fills that gap end-to-end:

- The worker loads the algorithm's code, builds a broker adapter, runs the algorithm's `on_tick` on a schedule, dispatches signals to the broker, and emits the per-tick samples / activity events the previous spec relies on.
- The coordinator becomes the brain for scheduling (decides when each algorithm should tick) and data routing (multiplexes one broker market-data stream to whichever workers need each symbol).
- The two communicate over the existing worker ↔ coordinator websocket using a new `tick_batch` push message and an updated `start_instance` payload.

After this spec ships, a deployment in "Running" state on the dashboard is actually running an algorithm on a Pi, reacting to live market data, placing real orders.

---

## 2. Architecture Overview

```
┌─── Coordinator (one process) ────────────────────────┐         ┌─── Worker (one process per Pi) ─────────┐
│                                                      │         │                                         │
│  data/packages/<repo>/      ── HTTP tarball ────────▶  ~/.quilt/packages/<algo_id>/<sha>/                │
│  Account.credentials (encrypted in DB)               │         │                                         │
│      ── decrypt + embed in start_instance ws ──────▶  In-memory: BrokerAdapter holds creds              │
│                                                      │         │                                         │
│  live_feed_aggregator                                │         │  LiveInstanceRuntime (per inst)         │
│    open one stream per (broker, symbol)              │         │    AlgorithmRunner (wraps algo class)   │
│    write parquet                                     │         │    RollingDataBuffer (per symbol/tf)    │
│    NEW: in-memory subscribers fan out callbacks      │         │    LiveObserver (emits samples)         │
│         to TickScheduler tasks                       │         │    CachingBrokerAdapter (30s TTL)       │
│                                                      │         │                                         │
│  TickScheduler                                       │         │                                         │
│    one task per running instance                     │         │                                         │
│    subscribe to live_feed_aggregator by trigger      │         │                                         │
│    enqueue ticks onto per-worker outbound queue      │         │                                         │
│                                                      │         │                                         │
│  per-worker coalescer                                │         │                                         │
│    drain queue every 10ms (or on enqueue)            │         │                                         │
│    pack into one tick_batch ws message ───────────▶ Agent dispatches each entry to its instance        │
│                                                                │  runtime → on_tick_batch_entry         │
│                                                                │    ingest data into buffer             │
│                                                                │    build LiveTickContext              │
│                                                                │    process_tick → runner.tick(ctx)     │
│                                                                │    dispatch signals via broker        │
│                                                                │    observer.on_tick (equity sample)   │
│                                                                │    send state_checkpoint              │
│                                                                │                                       │
│                                                                │  BrokerAdapter ── HTTPS ──▶ Broker    │
│                                                                │    (orders + account state)           │
└──────────────────────────────────────────────────────┘         └─────────────────────────────────────────┘
```

### Five principles

1. **Coordinator owns scheduling and data routing.** It knows which symbols each instance needs (from manifest), opens one shared broker stream per `(broker, symbol)` via `live_feed_aggregator`, and decides when each algorithm's `on_tick` should fire.

2. **Worker owns algorithm execution.** Loads the algorithm package, instantiates the class, holds in-memory state (indicator buffers, persisted_state), runs `on_tick`, dispatches signals.

3. **Streaming data flows coordinator → worker; orders flow worker → broker directly.** Coordinator multiplexes the broker stream to subscribed workers. Workers submit orders straight to the broker over HTTPS using credentials they received at start.

4. **One unified push channel per worker, carrying batched ticks.** Multiple instances' ticks fired at the same moment get coalesced into a single ws message over Tailscale (one packet instead of N).

5. **Restart is symmetric.** On either side reconnecting, coordinator re-sends `start_instance` (idempotent) with the last persisted state. Algorithms resume from their last checkpoint with at most one tick of state loss.

---

## 3. Manifest Additions

Algorithm authors declare what they need so the coordinator can schedule and route correctly.

### 3.1 New `trigger` field (under top-level)

```yaml
name: simple-ma-crossover
type: algorithm
entry_point: simple_ma_crossover.algorithm
class_name: SimpleMACrossover
version: 1.0.0
trigger: "bar:1min"     # NEW; default "bar:1min" if omitted
requirements:
  asset_types: [equities]
  brokers: [alpaca]
  data_dependencies:
    - { symbol: "AAPL", timeframe: "1min", history_bars: 200 }  # history_bars NEW; default 200
    - { symbol: "SPY",  timeframe: "1min", history_bars: 200 }
```

Accepted `trigger` values, validated by manifest parser via regex `^(bar:[a-z0-9]+|event|interval:\d+[smh])$`:

- `"bar:<tf>"` — fire `on_tick` when a new closed bar at timeframe `<tf>` arrives for *any* declared symbol. Tested values for v1: `"bar:1min"`, `"bar:5min"`, `"bar:1day"`. Other timeframes parse but require `live_feed_aggregator` to actually produce that timeframe.
- `"event"` — fire on every market data event (trade or quote) for any declared symbol.
- `"interval:<duration>"` — fire on a fixed cadence (`<duration>` like `30s`, `5m`, `1h`). **Gated on market hours** (Section 5.4); bar and event triggers don't need this gate because no data flows when market is closed.

### 3.2 Updated `data_dependencies` schema

The existing `data_dependencies` list already accepts dicts with `source`, `symbol`, etc. (handled by `coordinator/services/lifecycle.py:_split_data_deps`). We add an optional `history_bars` sub-field (positive integer, default 200) that tells the worker how many bars to backfill into the rolling data buffer on start.

### 3.3 Validation

The manifest parser at `sdk/manifest.py:QuiltManifest._parse` is extended:

- Adds `trigger: str` field on the dataclass with default `"bar:1min"`.
- Validates the regex; raises `ManifestError` on invalid format.
- For each `data_dependencies` entry, validates that `history_bars` (if present) is a positive int.
- Algorithm install (`/api/algorithms/install`) fails fast on validation errors.

### 3.4 Backwards compatibility

Algorithms already installed without a `trigger` field get `"bar:1min"` at parse time. No DB migration; the field lives in the manifest YAML on disk in `data/packages/<repo>/quilt.yaml`. No re-install required.

---

## 4. Coordinator-Side: TickScheduler

The new service at `coordinator/services/tick_scheduler.py`.

### 4.1 Per-instance scheduler tasks

`TickScheduler` owns one asyncio task per running `AlgorithmInstance`. The task's behavior depends on the algorithm's `trigger`:

**`bar:<tf>` instances:**

```python
# Pseudocode
for symbol in algorithm.data_dependencies.symbols:
    aggregator.subscribe_bars(broker, symbol, tf, callback=self._on_bar_close)

# self._on_bar_close fires once per (symbol, tf) close. We dedupe within a
# single bar boundary so the algorithm gets one tick when SPY and AAPL both
# close their :30 bar at the same wall-clock millisecond.
async def _on_bar_close(self, symbol, tf, bar):
    self._pending_symbols.add(symbol)
    self._pending_bars[symbol] = bar
    self._coalesce_timer.reset(50)  # wait up to 50ms for sibling bars
async def _fire_tick(self):
    payload = {
        "instance_id": ..., "run_id": ...,
        "timestamp": now_iso(),
        "trigger_kind": "bar",
        "trigger_meta": {"timeframe": tf},
        "data": {sym: {"bars": [bar], "timeframe": tf}
                 for sym, bar in self._pending_bars.items()},
    }
    self._pending_bars.clear()
    self._pending_symbols.clear()
    await outbound_queue_for_worker(self._worker_id).put(payload)
```

**`event` instances:** subscribe via `aggregator.subscribe_events(broker, symbol, callback)`. Each event enqueues a tick immediately (no coalescing — each event is its own tick).

**`interval:<duration>` instances:**

```python
async def run(self):
    interval_s = parse_duration(self._trigger)
    while True:
        if market_clock.is_market_open(self._asset_type, datetime.now(UTC)):
            payload = self._build_interval_tick_payload()
            await outbound_queue_for_worker(self._worker_id).put(payload)
        await asyncio.sleep(interval_s)
```

The `interval_tick` payload's `data` field is empty (or contains the latest bar per symbol if one has closed since the previous interval tick). The algorithm reads its current view from its worker-side buffer.

### 4.2 Per-worker outbound coalescer

One outbound queue per connected worker. A drain task per queue:

```python
async def drain_loop(self):
    while True:
        first = await self._queue.get()                  # blocks until first tick
        batch = [first]
        deadline = monotonic() + 0.010                   # 10ms window
        while monotonic() < deadline:
            try:
                batch.append(await asyncio.wait_for(self._queue.get(), timeout=deadline - monotonic()))
            except asyncio.TimeoutError:
                break
        await self._ws.send_json({"type": "tick_batch", "ticks": batch})
```

10ms is small enough to be imperceptible for bar-driven strategies (a 1-min bar fires once per 60s — no batching benefit anyway) and protects against the "everything ticks at :00 second" thundering herd. Configurable via env var `QT_TICK_COALESCE_WINDOW_MS` (default 10).

### 4.3 Extension to `live_feed_aggregator`

Today `live_feed_aggregator` opens broker streams, writes parquet, updates `LiveSubscription` metadata. We add an in-memory event bus alongside (purely additive — parquet writes don't change):

```python
class LiveFeedAggregator:
    def __init__(self, ...):
        ...
        self._bar_subscribers: dict[tuple[str, str, str], set[Callable]] = {}
        # key: (broker, symbol, timeframe); callback signature: (bar_dict) -> Coroutine
        self._event_subscribers: dict[tuple[str, str], set[Callable]] = {}
        # key: (broker, symbol); callback signature: (event_dict) -> Coroutine

    def subscribe_bars(self, broker, symbol, timeframe, callback): ...
    def unsubscribe_bars(self, broker, symbol, timeframe, callback): ...
    def subscribe_events(self, broker, symbol, callback): ...
    def unsubscribe_events(self, broker, symbol, callback): ...

    async def _dispatch_bar(self, broker, symbol, timeframe, bar):
        """Called internally when a 1-min (or other) bar finalizes."""
        for cb in list(self._bar_subscribers.get((broker, symbol, timeframe), ())):
            try:
                await cb(bar)
            except Exception:
                logger.exception("Bar subscriber failed for %s/%s/%s", broker, symbol, timeframe)
```

Subscribers register/unregister during the instance's lifecycle. Callbacks are async (the scheduler enqueues into the worker's outbound queue and returns immediately — no blocking the data feed). The aggregator must call `_dispatch_bar` after each successful bar flush; subscribers are invoked from the aggregator's own task, errors are logged but never propagate.

### 4.4 Market clock helper

New module `coordinator/services/market_clock.py`:

```python
def is_market_open(asset_type: str, ts: datetime) -> bool: ...
```

For v1:
- `equities`, `equity_options`: US market hours 9:30–16:00 ET, Mon–Fri, excluding US bank holidays via a hardcoded list (2024–2026 covered explicitly; clearly documented as needing annual refresh).
- All other asset types: returns `True`.

Used only by `interval:` trigger tasks. Bar and event triggers don't need it because no bars/events arrive outside market hours anyway.

### 4.5 Wiring into lifespan

A single `TickScheduler` instance is constructed in `coordinator/main.py` alongside `LiveFinalizer` and the other lifespan services. Handed `live_feed_aggregator` and `session_factory`. On startup it queries the DB for every `AlgorithmInstance` with `status="running"` and starts a per-instance task for each (recovery from coordinator restart). New endpoints call it:

- `POST /api/deployments/:id/start` → after the optimistic write that creates the run, calls `tick_scheduler.start_instance(inst)`.
- `POST /api/deployments/:id/stop` → calls `tick_scheduler.stop_instance(inst)` after the optimistic write that sets `status="stopping"`.

On shutdown, the scheduler cancels all per-instance tasks and unsubscribes from the aggregator.

### 4.6 Updated `start_instance` ws payload

The payload the coordinator sends to the worker grows substantially. The endpoint at `coordinator/api/routes/deployments.py:start_deployment` enriches it just before sending:

```json
{
  "type": "start_instance",
  "instance_id": "...",
  "run_id": "...",
  "algorithm_id": "...",
  "algorithm_commit_sha": "abc1234...",
  "manifest": { ... parsed manifest dict ... },
  "broker_type": "alpaca",
  "environment": "paper",
  "credentials": { "api_key": "...", "secret_key": "..." },
  "config": { ... config_values from instance row ... },
  "persisted_state": { ... or null ... }
}
```

The coordinator decrypts the credentials via the existing `EncryptionService` just before sending. The credentials cross the wire over Tailscale (WireGuard-encrypted), reach the worker, populate the `BrokerAdapter`'s in-memory fields, and never touch worker disk.

### 4.7 Push payload shape

The `tick_batch` ws message:

```json
{
  "type": "tick_batch",
  "ticks": [
    {
      "instance_id": "...",
      "run_id": "...",
      "timestamp": "2026-05-16T13:34:00Z",
      "trigger_kind": "bar",
      "trigger_meta": { "timeframe": "1min" },
      "data": {
        "AAPL": {
          "timeframe": "1min",
          "bars": [
            { "timestamp": "2026-05-16T13:34:00Z",
              "open": 185.20, "high": 185.30, "low": 185.10, "close": 185.25,
              "volume": 12345 }
          ]
        },
        "SPY": { "timeframe": "1min", "bars": [ ... ] }
      }
    }
  ]
}
```

Variations:

- `trigger_kind: "event"` — `data[symbol].bars` is omitted; instead `data[symbol].event` holds the raw quote/trade event.
- `trigger_kind: "interval"` — `data` typically empty; or contains the latest closed bar per symbol if one closed since the previous interval tick.

---

## 5. Worker-Side: LiveInstanceRuntime

The replacement for `_handle_start_instance`'s current stub behavior. Most of the new worker code lives here.

### 5.1 Slim `_handle_start_instance` in `worker/agent.py`

```python
async def _handle_start_instance(self, message: dict) -> None:
    inst_id = message["instance_id"]
    # Idempotent: if already running and healthy, no-op.
    existing = self._running_instances.get(inst_id)
    if existing is not None and existing.is_healthy():
        logger.info("Ignoring duplicate start_instance for %s (already healthy)", inst_id)
        return
    if existing is not None:
        await existing.shut_down()  # error/zombie state — tear down before re-bringing-up
    try:
        runtime = await LiveInstanceRuntime.bring_up(
            agent=self,
            instance_id=inst_id,
            run_id=message["run_id"],
            algorithm_id=message["algorithm_id"],
            algorithm_commit_sha=message["algorithm_commit_sha"],
            manifest=message["manifest"],
            config=message.get("config", {}),
            persisted_state=message.get("persisted_state"),
            broker_type=message["broker_type"],
            environment=message["environment"],
            credentials=message["credentials"],
            data_client=self._data_client,
        )
    except Exception as e:
        logger.exception("Failed to bring up instance %s", inst_id)
        await self.send_event("instance_error", inst_id, payload={"error": str(e)})
        await self.send_activity_event(inst_id, "instance_error", severity="error",
                                       payload={"error": str(e)})
        return
    self._running_instances[inst_id] = runtime
    await self.send_event("instance_started", inst_id)
    await self.send_activity_event(inst_id, "instance_started", severity="info")
```

The agent acquires a `DataClient` once in `worker/main.py` (already happens) and passes it to runtimes.

### 5.2 `LiveInstanceRuntime` class

New file `worker/live_instance_runtime.py`. One instance per running algorithm; owns its lifecycle:

```python
class LiveInstanceRuntime:
    def __init__(self, *, instance_id, run_id, runner, broker, buffer, observer,
                 tick_processor, agent, data_dependencies):
        self._instance_id = instance_id
        self._run_id = run_id
        self._runner = runner
        self._broker = broker
        self._buffer = buffer
        self._observer = observer
        self._tick_processor = tick_processor
        self._agent = agent
        self._data_deps = data_dependencies
        self._consecutive_failures = 0

    @classmethod
    async def bring_up(cls, *, agent, instance_id, run_id, algorithm_id,
                       algorithm_commit_sha, manifest, config, persisted_state,
                       broker_type, environment, credentials, data_client):
        # 1. Ensure algorithm package is locally cached.
        pkg_dir = await package_cache.ensure(
            agent=agent,
            algorithm_id=algorithm_id,
            commit_sha=algorithm_commit_sha,
        )
        # 2. Import the algorithm class via importlib.
        algo_cls = load_algorithm_class(
            pkg_dir, manifest["entry_point"], manifest["class_name"],
        )
        algo = algo_cls()
        # 3. Build the broker adapter via the existing factory.
        raw_broker = make_broker_adapter(broker_type, environment, credentials)
        broker = CachingBrokerAdapter(raw_broker, account_state_ttl=30)
        # 4. Build the rolling data buffer from manifest.data_dependencies.
        data_deps = manifest["requirements"].get("data_dependencies", []) or []
        buffer = RollingDataBuffer(data_deps)
        await buffer.backfill(data_client)
        # 5. Build the AlgorithmRunner (already wires log shipper from M4.4).
        runner = AlgorithmRunner(
            instance_id=instance_id, algorithm=algo, config=config,
            restored_state=persisted_state, agent=agent,
            loop=asyncio.get_running_loop(),
        )
        runner.start()  # calls algo.on_start(config, restored_state)
        # 6. Build the LiveObserver with the run_id.
        observer = LiveObserver(agent=agent, broker=broker,
                                instance_id=instance_id, run_id=run_id)
        # 7. Build the TickProcessor, passing the live_observer.
        tick_processor = TickProcessor(
            runner=runner, broker=broker,
            data_client=data_client, coordinator_client=agent,
            live_observer=observer,
        )
        return cls(
            instance_id=instance_id, run_id=run_id,
            runner=runner, broker=broker, buffer=buffer, observer=observer,
            tick_processor=tick_processor, agent=agent,
            data_dependencies=data_deps,
        )

    async def on_tick_batch_entry(self, entry: dict) -> None:
        """Called by the agent when a tick_batch carries an entry for this instance."""
        # 1. Merge the pushed delta into the rolling buffer.
        if entry.get("data"):
            self._buffer.ingest(entry["data"])
        ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
        # 2. Run the tick through TickProcessor (handles signal dispatch +
        #    activity event emission + observer.on_trade for filled trades).
        try:
            await self._tick_processor.process_tick(ts)
            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            await self._agent.send_activity_event(
                self._instance_id, "algo_exception", severity="error",
                payload={"error": str(e), "traceback_tail": _last_n_traceback_lines(20)},
            )
            if self._consecutive_failures >= 5:
                await self._agent.send_event("instance_error", self._instance_id,
                                             payload={"reason": "5 consecutive tick failures"})
                await self.shut_down()
                return
        # 3. Emit equity sample.
        await self._observer.on_tick(timestamp=ts.isoformat())
        # 4. Checkpoint state after every tick (cheap; durable).
        try:
            state = self._runner.save_state()
            await self._agent.send_state_checkpoint(self._instance_id, state)
        except Exception:
            logger.exception("Failed to checkpoint state for instance %s", self._instance_id)

    def is_healthy(self) -> bool:
        return self._runner.state == RunnerState.RUNNING and self._consecutive_failures < 5

    async def shut_down(self) -> dict:
        try:
            final = self._runner.stop()  # calls algo.on_stop()
        except Exception:
            logger.exception("Algorithm on_stop raised; using save_state fallback")
            final = self._runner.save_state()
        return final
```

### 5.3 `WorkerAgent` routing of `tick_batch`

The agent's message router gains a `tick_batch` handler:

```python
def register_handlers(self) -> None:
    self.router.register("start_instance", self._handle_start_instance)
    self.router.register("stop_instance", self._handle_stop_instance)
    self.router.register("heartbeat_ack", self._handle_heartbeat_ack)
    self.router.register("tick_batch", self._handle_tick_batch)        # NEW

async def _handle_tick_batch(self, message: dict) -> None:
    for entry in message.get("ticks", []):
        inst_id = entry.get("instance_id")
        runtime = self._running_instances.get(inst_id)
        if runtime is None:
            logger.warning("tick_batch entry for unknown instance %s; ignoring", inst_id)
            continue
        # Process each entry sequentially within the agent's task — algorithms
        # holding global state must not see concurrent ticks against the same
        # instance, but ticks for different instances are independent.
        asyncio.create_task(runtime.on_tick_batch_entry(entry))
```

(Per-instance task spawning keeps a slow algorithm from blocking faster siblings within the same batch.)

### 5.4 `package_cache` module

New file `worker/package_cache.py`:

```python
PACKAGE_CACHE_ROOT = Path(os.environ.get("QT_PACKAGE_CACHE_ROOT",
                                          str(Path.home() / ".quilt" / "packages")))

async def ensure(*, agent, algorithm_id: str, commit_sha: str) -> Path:
    """Return a local directory containing the algorithm package for (algorithm_id, commit_sha).
    Downloads via HTTP if not cached.
    """
    target = PACKAGE_CACHE_ROOT / algorithm_id / commit_sha
    if target.exists() and (target / "quilt.yaml").exists():
        return target
    # Fetch the tarball from the coordinator over HTTP (via the existing DataClient
    # base URL, with the worker's install token as auth).
    coord_http = agent.coordinator_http_url
    token = agent.worker_install_token  # propagated from WorkerConfig
    url = f"{coord_http}/api/algorithms/{algorithm_id}/package.tar.gz?sha={commit_sha}"
    target.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("GET", url, headers={"X-Worker-Install-Token": token}) as r:
            r.raise_for_status()
            tar_bytes = await r.aread()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        tar.extractall(target, filter="data")
    return target


def load_algorithm_class(pkg_dir: Path, entry_point: str, class_name: str) -> type:
    """Load the algorithm class without polluting global sys.path.

    `entry_point` is a module path like "simple_ma_crossover.algorithm".
    `class_name` is the class to instantiate.
    """
    module_relpath = entry_point.replace(".", "/") + ".py"
    module_path = pkg_dir / module_relpath
    if not module_path.exists():
        # Try package init form (entry_point/__init__.py)
        module_path = pkg_dir / entry_point.replace(".", "/") / "__init__.py"
    if not module_path.exists():
        raise FileNotFoundError(f"Algorithm entry_point not found: {entry_point} in {pkg_dir}")
    spec = importlib.util.spec_from_file_location(entry_point, module_path,
                                                  submodule_search_locations=[str(pkg_dir)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(f"Class {class_name!r} not found in {entry_point}")
    return cls
```

The worker install token is already on the worker (it was used during install). We surface it on `WorkerConfig` and the agent so `package_cache` can include it as a header.

### 5.5 `RollingDataBuffer` module

New file `worker/rolling_data_buffer.py`:

```python
class RollingDataBuffer:
    def __init__(self, data_dependencies: list[dict]):
        # Map (symbol, timeframe) -> deque[bar_dict] of max length history_bars.
        self._buffers: dict[tuple[str, str], deque] = {}
        self._max_bars: dict[tuple[str, str], int] = {}
        for d in data_dependencies:
            sym = d.get("symbol")
            tf = d.get("timeframe", "1min")
            if not sym:
                continue
            max_bars = int(d.get("history_bars", 200))
            key = (sym, tf)
            self._buffers[key] = deque(maxlen=max_bars)
            self._max_bars[key] = max_bars

    async def backfill(self, data_client: DataClient) -> None:
        for (sym, tf), buf in self._buffers.items():
            df = await data_client.get_market_data(sym, timeframe=tf, bars=self._max_bars[(sym, tf)])
            for _, row in df.iterrows():
                buf.append(row.to_dict())

    def ingest(self, push_data: dict) -> None:
        # push_data shape: { "AAPL": {"timeframe": "1min", "bars": [...]}, ... }
        for sym, payload in push_data.items():
            tf = payload.get("timeframe", "1min")
            key = (sym, tf)
            if key not in self._buffers:
                continue
            for bar in payload.get("bars", []):
                self._buffers[key].append(bar)

    def get(self, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        key = (symbol, timeframe)
        if key not in self._buffers:
            return pd.DataFrame()
        rows = list(self._buffers[key])[-bars:]
        return pd.DataFrame(rows)

    def has(self, symbol: str, timeframe: str) -> bool:
        return (symbol, timeframe) in self._buffers
```

### 5.6 Updated `LiveTickContext`

`worker/context.py` is extended so `market_data()` reads from the buffer when possible:

```python
class LiveTickContext:
    def __init__(self, timestamp, mode, broker, data_client, buffer=None):
        self._timestamp = timestamp
        self._mode = mode
        self._broker = broker
        self._data_client = data_client
        self._buffer = buffer

    # ... existing timestamp/positions/cash/account_value/buying_power unchanged ...

    async def market_data(self, symbol, timeframe="1min", bars=100):
        if self._buffer is not None and self._buffer.has(symbol, timeframe):
            return self._buffer.get(symbol, timeframe, bars)
        # Fallback to HTTP for symbols/timeframes the algorithm didn't declare.
        logger.warning("Symbol %s timeframe %s not declared in data_dependencies; "
                       "falling back to HTTP (slow path)", symbol, timeframe)
        return await self._data_client.get_market_data(symbol, timeframe=timeframe, bars=bars)
```

The `TickProcessor` is updated to build a `LiveTickContext` with the buffer attached. Constructed in `LiveInstanceRuntime.bring_up`.

### 5.7 `CachingBrokerAdapter` wrapper

New file `worker/caching_broker_adapter.py`:

```python
class CachingBrokerAdapter(BrokerAdapter):
    """Wraps a BrokerAdapter to cache get_account_info and get_positions.

    Account state is read every tick; without caching, each tick incurs 1–3
    HTTPS calls to the broker. With 30s TTL, multiple algos on the same
    account naturally share cached state.
    """
    def __init__(self, inner: BrokerAdapter, account_state_ttl: float = 30.0):
        self._inner = inner
        self._ttl = account_state_ttl
        self._cache_account: tuple[float, dict] | None = None
        self._cache_positions: tuple[float, dict] | None = None

    def get_account_info(self) -> dict:
        now = time.monotonic()
        if self._cache_account is not None and now - self._cache_account[0] < self._ttl:
            return self._cache_account[1]
        v = self._inner.get_account_info()
        self._cache_account = (now, v)
        return v

    def get_positions(self) -> dict[str, dict]:
        now = time.monotonic()
        if self._cache_positions is not None and now - self._cache_positions[0] < self._ttl:
            return self._cache_positions[1]
        v = self._inner.get_positions()
        self._cache_positions = (now, v)
        return v

    def invalidate(self) -> None:
        """Forces a refresh on next read. Call after order submission."""
        self._cache_account = None
        self._cache_positions = None

    # All other methods pass through to self._inner.
    # ... (mechanical delegation for submit_order, get_transactions, etc.)
```

`TickProcessor` calls `broker.invalidate()` after each successful `submit_order` so the next tick sees the updated state.

### 5.8 `_handle_stop_instance`

Updated to use the runtime:

```python
async def _handle_stop_instance(self, message: dict) -> None:
    inst_id = message["instance_id"]
    runtime = self._running_instances.pop(inst_id, None)
    if runtime is not None:
        try:
            final_state = await runtime.shut_down()
            await self.send_state_checkpoint(inst_id, final_state)
        except Exception:
            logger.exception("Error shutting down instance %s", inst_id)
    await self.send_event("instance_stopped", inst_id)
    await self.send_activity_event(inst_id, "instance_stopped", severity="info")
```

---

## 6. Coordinator Endpoints

### 6.1 `GET /api/algorithms/{algorithm_id}/package.tar.gz?sha={commit_sha}`

New route at `coordinator/api/routes/algorithms.py`. Streams a gzipped tarball of `data/packages/<repo_dir>/`. Mirrors the pattern of `worker_install_package` in `coordinator/api/routes/workers.py`:

```python
@router.get("/{algorithm_id}/package.tar.gz")
async def algorithm_package(
    algorithm_id: str,
    sha: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Auth: require X-Worker-Install-Token header matching an installed worker.
    token = request.headers.get("X-Worker-Install-Token")
    if not token or not await _is_valid_worker_token(token, db):
        raise HTTPException(status_code=401, detail="Invalid worker token")
    # Lookup and validate SHA.
    algo = (await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))).scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")
    if algo.commit_hash != sha:
        raise HTTPException(status_code=404, detail=f"Algorithm SHA mismatch: have {algo.commit_hash}, requested {sha}")
    # Build the tarball from data/packages/<repo_dir>/.
    pkg_dir_name = _package_dir_name(algo.repo_url)  # already exists in backtest_runner.py
    src_path = Path("data/packages") / pkg_dir_name
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="Algorithm package not on disk")
    data = _build_package_tarball(src_path)
    return Response(content=data, media_type="application/gzip",
                    headers={"Content-Disposition": f"attachment; filename={pkg_dir_name}.tar.gz"})
```

Worker token validation: any worker with a valid `install_token` in the `workers` table is allowed. We extend `Worker` so that even after `install_status="claimed"`, the install_token remains valid for package fetches. (Today it's set to None on claim; we change to leave it set, optionally rotated by an admin operation.)

### 6.2 Updated `start_instance` ws payload

Already covered in §4.6. The endpoint in `coordinator/api/routes/deployments.py:start_deployment` is extended:

```python
# Inside start_deployment, after the optimistic write + run row creation:
account = (await db.execute(
    select(Account).where(Account.id == inst.account_id)
)).scalar_one()
algorithm = (await db.execute(
    select(Algorithm).where(Algorithm.id == inst.algorithm_id)
)).scalar_one()
encryption = get_container().encryption
credentials = json.loads(encryption.decrypt(account.credentials))
manifest = _load_manifest_dict(algorithm)  # parses data/packages/<repo>/quilt.yaml
await worker_ws.send_json({
    "type": "start_instance",
    "instance_id": inst.id,
    "run_id": run.id,
    "algorithm_id": algorithm.id,
    "algorithm_commit_sha": algorithm.commit_hash,
    "manifest": manifest,
    "broker_type": account.broker_type,
    "environment": account.environment,
    "credentials": credentials,
    "config": inst.config_values or {},
    "persisted_state": inst.persisted_state,
})
```

---

## 7. Lifecycle Flows

### 7.1 Cold start (deployment never run before)

1. User clicks Start in dashboard → `POST /api/deployments/:id/start`.
2. Coordinator: validates worker is connected, creates `AlgorithmRun` row (run_number=1), writes `inst.status="starting"`, `inst.active_run_id=run.id`, broadcasts `deployment_status_changed`.
3. Coordinator: decrypts credentials, loads manifest YAML, sends `start_instance` ws payload to worker.
4. Coordinator: registers the instance with `tick_scheduler.start_instance(inst)`, which subscribes to the aggregator and starts its per-instance task.
5. Worker: receives `start_instance`. Bring-up: package cache fetch → algorithm load → broker adapter → buffer backfill (HTTP to coordinator data API) → `runner.start()` (calls `algo.on_start`). Sends `instance_started` + activity event. Total bring-up time: ~1–3s typical.
6. Coordinator: receives `instance_started`, writes `inst.status="running"`, broadcasts.
7. First tick fires whenever the trigger says so (typically the next `:00` boundary for `bar:1min`).

### 7.2 Subsequent ticks (steady state)

1. Aggregator finalizes a 1-min bar for AAPL. Calls registered subscribers.
2. Scheduler task for instance X enqueues a tick on worker W's outbound queue.
3. Coalescer drains within 10ms (combining any sibling ticks for other instances on W).
4. `tick_batch` ws message sent to worker.
5. Worker's `_handle_tick_batch` dispatches per-entry to each `LiveInstanceRuntime`.
6. Runtime: ingest delta into buffer, build `LiveTickContext`, call `tick_processor.process_tick(ts)`, emit `equity_sample`, send `state_checkpoint`.
7. Inside `process_tick`: `algorithm.on_tick(ctx)` runs; signals are dispatched via `broker.submit_order`; trades emit `trade_sample` and `activity_event(trade_executed)`; activity events flow back to coordinator → DB → ws broadcast to dashboard subscribers.

### 7.3 Stop

1. User clicks Stop → `POST /api/deployments/:id/stop`.
2. Coordinator: writes `status="stopping"`, broadcasts. Calls `tick_scheduler.stop_instance(inst)` (cancels per-instance task, unsubscribes from aggregator).
3. Coordinator: sends `stop_instance` ws message to worker.
4. Worker: `runtime.shut_down()` calls `algo.on_stop()`, sends final `state_checkpoint`, sends `instance_stopped`.
5. Coordinator: receives `instance_stopped`, writes `status="stopped"`, marks active run `status="stopped"`, `stopped_at=now`, clears `inst.active_run_id`, broadcasts.

### 7.4 Worker reconnect after disconnect

1. Worker crashed or network blip. Coordinator detected via heartbeat timeout (M1.4). Marked worker `offline`. Per §8.7, the `tick_scheduler` cancels per-instance tasks for instances on that worker and drops its outbound queue (no buffering).
2. Worker reconnects via the existing `worker/main.py` reconnect loop. Sends heartbeat. Coordinator marks worker `online`, broadcasts `worker_connected`.
3. Coordinator's heartbeat handler triggers a reconciliation: query DB for instances with `status="running"` AND `worker_id == W.id`. For each, re-send `start_instance` with the last `persisted_state`.
4. Worker brings them up. Existing instances (none, since the worker restarted) are absent; bring-up path runs fresh from the checkpoint. Algorithm sees `on_start(config, restored_state=<last_checkpoint>)`.
5. Each restart creates a NEW `AlgorithmRun` row (the previous run is marked `error` with `stopped_at` set on disconnect). The deployment page shows the prior run with its partial metrics + a new run #N+1 starting.

### 7.5 Coordinator reconnect after disconnect

1. Worker's ws drops. Worker's reconnect loop retries. Worker's running instances stay alive in memory but receive no `tick_batch` pushes (effectively paused).
2. Coordinator boots. Reconciles: marks all `status="running"` instances' active runs as `error` if `now() - heartbeat > 60s` (uses existing M1.4 sweeper logic, generalized).
3. Worker reconnects. Coordinator's heartbeat reconciliation (§7.4 step 3) re-sends `start_instance` for all known-running instances on the now-reconnected worker.
4. Worker is idempotent: sees existing healthy runtime for inst X, no-ops. For instances that died with the worker (none in this scenario), brings up fresh.
5. Tick scheduler resumes pushing.

### 7.6 Algorithm package update

1. User does `POST /api/algorithms/install` with a new commit. Coordinator fetches via existing flow, writes new `commit_hash` to the Algorithm row.
2. Deployments using the algorithm continue running on the OLD code (cached on workers under the old SHA).
3. User clicks Stop, then Start on a deployment. New `start_instance` payload includes the new `algorithm_commit_sha`. Worker's `package_cache.ensure()` sees the SHA is new, fetches the new tarball, extracts to `~/.quilt/packages/<algo_id>/<new_sha>/`. Old cached version stays for any other deployments still on the old SHA.
4. No hot reload. The user must stop+start to pick up an update. This is intentional and documented.

---

## 8. Failure Handling

### 8.1 Algorithm raises in `on_tick`

`LiveInstanceRuntime.on_tick_batch_entry` wraps the `tick_processor.process_tick(ts)` call in try/except. On exception:

- Emits `activity_event` with `event_type="algo_exception"`, `severity="error"`, payload `{error, traceback_tail}` (last 20 lines of traceback).
- Calls `runner.save_state()` in its own try/except as best-effort checkpoint.
- Increments `self._consecutive_failures`. After 5 consecutive tick failures, the runtime self-stops: emits `instance_error`, calls `self.shut_down()`. Coordinator's existing `instance_error` handler marks the deployment `status="error"`.
- A single failure does NOT stop the algorithm. Resets the counter on the next successful tick.

### 8.2 Broker order failure

`broker.submit_order` returns an `OrderResult` with `success=False` on failure. The existing `TickProcessor` already handles this via `runner.on_signal_rejected(signal, reason)`. We extend it to also emit:

- `activity_event` with `event_type="broker_error"`, `severity="warn"`, payload `{symbol, side, quantity, error}`.

The trade does not appear in `TradeLog` (which is the desired behavior — only successful fills are recorded). The algorithm sees the rejection and decides what to do next.

### 8.3 Worker disconnects mid-tick

Tick computation completes locally (synchronous Python + a few async-broker HTTPS calls). Subsequent `equity_sample` / `trade_sample` / `state_checkpoint` ws sends fail silently (caught in `_send`). On reconnect (§7.4), the worker brings the instance up from the last successful checkpoint — at most one tick of state is lost. Trades that successfully reached the broker before the disconnect are recorded broker-side and surface via the next account-state refresh.

### 8.4 Coordinator disconnects mid-tick

If a tick is currently being pushed when the ws drops, the worker may receive a truncated or no message. The send-side error is logged on the coordinator. The next reconnect+reconcile cycle resumes ticking. The algorithm sees a temporary gap in `on_tick` calls; intra-algorithm state remains intact.

### 8.5 Package fetch fails on start

`package_cache.ensure()` raises (404, network error, bad tarball). `bring_up` catches; `_handle_start_instance` emits `instance_error` with a descriptive payload. Coordinator marks the deployment `status="error"`. User sees the error in the dashboard's activity panel. They can re-try Start after fixing the cause.

### 8.6 Credentials missing or invalid

`make_broker_adapter` raises `CredentialError` for missing required fields. `bring_up` catches; same path as §8.5 — `instance_error` with `"Broker credentials for account X are missing fields: api_key, secret_key"` style message. User edits the account in the dashboard, retries.

### 8.7 Subscription gap when worker is offline

When a worker disconnects, the coordinator could either (a) drop ticks destined for that worker, or (b) buffer them indefinitely. We choose **(a)**: drop. Reasoning: a worker offline for more than a few seconds will fall behind the live market anyway; replaying stale ticks would feed the algorithm out-of-date data. On reconnect, the algorithm gets the next live tick fresh.

Implementation: when a worker's ws disconnects (caught in the existing `handle_worker_disconnect` from M1.3), the scheduler drops that worker's outbound queue and cancels per-instance tasks for instances on that worker. On reconnect, `tick_scheduler` re-starts per-instance tasks for `status="running"` instances on the now-online worker.

---

## 9. Open Manifest Migration Concern

Algorithms that pre-date this spec are installed without a `trigger` field in their manifest YAML. The parser defaults to `"bar:1min"` (§3.4) so install doesn't fail. BUT: the implicit default may not match the algorithm author's intent — a daily-rebalance algorithm with `"bar:1day"` semantics would still get a tick at every 1-min bar close and would have to ignore most of them. This is a soft compatibility issue, not a correctness one (the algorithm sees more ticks than it needs and presumably no-ops). Document in the install flow's UI: "Algorithms without a `trigger` field default to `bar:1min`. Edit your manifest to override."

No further migration tooling — re-installing the algorithm with the new field updates the manifest on disk.

---

## 10. Out of Scope (v1)

- Multiple parallel runs of the same algorithm on different accounts. `Account.locked_by` already prevents this internally.
- Algorithm hot reload (changing commit_sha while the algorithm runs). Today: stop, install new version, start. Package cache handles it cleanly.
- Coordinator HA / multi-coordinator scheduling. Single coordinator only.
- Sub-millisecond / nanosecond strategy support. `trigger: "event"` is the lowest-latency path; further optimization (binary ws framing, zero-copy, etc.) is deferred.
- Per-worker resource limits (memory, CPU enforcement). Workers are trusted; an OOMing algorithm OOMs its Pi.
- Algorithm signing or verification. Tarballs are downloaded from a trusted coordinator over a Tailscale-encrypted channel; no signature check.
- A dashboard view of the tick scheduler's internal state (queue depths, dropped ticks). Reserved for operational tooling later.

---

## 11. Implementation Order (suggested)

The implementation plan will split into milestones; rough ordering:

1. **Manifest changes + package endpoint**: extend `QuiltManifest` with `trigger` field, add `/api/algorithms/{id}/package.tar.gz`, update worker token validation. Independent; testable in isolation.
2. **Worker bring-up infrastructure**: `package_cache`, `RollingDataBuffer`, `CachingBrokerAdapter`, `LiveTickContext` extension. New modules, low coupling. Doesn't depend on coordinator-side changes.
3. **`LiveInstanceRuntime`**: ties together the bring-up flow. Refactor `_handle_start_instance` to use it. End-to-end manual test possible: hand-craft a `start_instance` payload, watch the worker load and run an algorithm.
4. **Coordinator scheduler core**: `TickScheduler`, per-instance task abstraction, integration with `live_feed_aggregator` subscribe/dispatch API. Add per-worker outbound coalescer.
5. **Trigger types**: `bar:`, `event:`, `interval:` paths. Market clock helper. Wire each into the scheduler.
6. **`start_instance` payload enrichment**: update `start_deployment` endpoint to include all the new fields. End-to-end smoke test: click Start in dashboard, watch tick_batch flow.
7. **Reconnect reconciliation**: extend the heartbeat handler to re-send `start_instance` for orphaned instances on worker reconnect.
8. **Failure handling polish**: consecutive-failure tracking, broker_error events, traceback truncation.
9. **End-to-end browser test**: install `simple-ma-crossover` against an Alpaca paper account, start a deployment, watch the dashboard's deployment page fill in.
