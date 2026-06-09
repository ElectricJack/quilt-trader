# Architecture

> Quilt-trader is a hub-and-spoke distributed system: a single coordinator orchestrates many stateless workers over Tailscale.

## What you'll learn

- The split between coordinator and workers, and why it's drawn there.
- How components communicate (REST, WebSocket, Tailscale).
- Where each piece of state lives.
- Why hub-and-spoke instead of peer-to-peer.

## The problem this solves

Most algo-trading frameworks assume one machine. Your laptop is the
broker, the data store, the scheduler, and the execution host. That
works until your laptop sleeps. Or until you want to run six strategies
in parallel and the Python GIL serializes them. Or until you want live
execution on a low-cost always-on box (a Raspberry Pi in a closet)
without copying years of historical parquet onto it.

The single-machine model also conflates concerns that have very
different durability requirements. The trade log must survive a power
cut. The connection to your broker must not. Holding both on the same
process means every change to one risks the other.

Quilt splits these roles. One process — the **coordinator** — owns
everything that has to be durable: the SQLite database, the parquet
cache, the scheduler. The processes that touch a broker — the
**workers** — hold no persistent state at all. They can be wiped,
moved, or replaced without losing a row of trade history. The
coordinator can be backed up with a single file copy. The workers can
be the cheapest hardware you own.

## How Quilt does it

### Coordinator

A single FastAPI app at `coordinator/main.py:22` (`create_app`).
It owns:

- **The database.** SQLite via SQLAlchemy. Engine is built at the top
  of `create_app` and exposed through a `session_factory` passed to
  every service.
- **The dashboard.** The compiled React bundle at `dashboard/dist/` is
  mounted as static files by the same FastAPI app
  (`coordinator/main.py:737`). One process serves the API and the UI;
  one URL to remember.
- **The scheduler.** `SchedulerService` is started in the app lifespan
  (`coordinator/main.py:91`). It holds cron jobs for nightly
  archival, account sync, the data-goal processor, and scraper runs.
- **The data layer.** `DataService` (`coordinator/services/data_service.py`)
  reads and writes parquet under `data/market/` and `data/custom/`.
  `DownloadManager` (`coordinator/services/download_manager.py`)
  fans out fetch jobs to provider adapters (Polygon, Tradier, Alpaca,
  Theta, yfinance, FMP).
- **The scraper engine.** `ScraperEngine`
  (`coordinator/services/scraper_engine.py`) runs scraper packages
  from `packages/` on a cron and atomically swaps their output CSVs.
- **The WebSocket hub.** `ConnectionManager`
  (`coordinator/api/websocket.py:13`) tracks every connected worker
  and dashboard, routes worker events to subscribed dashboards, and
  fans coordinator commands out to workers.

The full list of services lives under
`coordinator/services/`; the full list of REST routes lives under
`coordinator/api/routes/`.

### Workers

A worker is one process started by `python -m worker.main`
(`worker/main.py:69`). It opens a WebSocket to the coordinator
(`worker/main.py:38`) and stays connected, reconnecting on drop.

The entrypoint is `WorkerAgent` (`worker/agent.py:30`). It is
deliberately small:

- A `MessageRouter` dispatches inbound messages by `type` to
  per-message-type handlers.
- Outbound, the agent has thin senders for heartbeats, activity
  events, algo logs, signal approval requests, state checkpoints,
  and decision logs.
- Per-instance work happens in `LiveInstanceRuntime` (one per
  running algorithm), and inside each runtime a `TickProcessor`
  (`worker/tick_loop.py:31`) drives the algorithm on every tick.

**Workers hold no durable state.** Broker credentials arrive inside
the `start_instance` message and are passed to
`LiveInstanceRuntime.bring_up` as a `credentials=...` kwarg
(`worker/agent.py:167`). They are never written to disk. If the
worker dies and restarts, the coordinator re-sends the credentials
with the next `start_instance`. The same is true for algorithm
config and persisted state — both arrive in the message, both live
only in worker memory.

This is the load-bearing design choice. It is what lets a worker be
a $50 Raspberry Pi that you don't trust with secrets at rest.

### Dashboard

A React + Vite app rooted at `dashboard/src/App.tsx:44`. It's a
single-page app with React Router. State is fetched over the REST
API via TanStack Query and kept fresh via a WebSocket subscription
(`useWebSocketSync` in `dashboard/src/hooks/`).

The dashboard is served by the coordinator. The build output
(`dashboard/dist/`) is mounted at `/` by `SPAStaticFiles` in
`coordinator/main.py:737`, with a fallback to `index.html` so that
client-side routes survive a page refresh. API and WebSocket routes
are registered first and take precedence.

### Discord bot

Optional. `coordinator/services/discord_bot.py` defines a
`DiscordNotifier` with per-event-type channel routing and a minimum
severity threshold. When configured, the coordinator routes
worker events (`trade_executed`, status changes, PDT warnings) to
the configured channels. Workers never talk to Discord directly —
all notifications fan out from the coordinator.

This is a notification surface, not a remote-control plane. Slash
commands are out of scope.

### Communication channels

Three transports, each with a clear job:

- **WebSocket (worker ↔ coordinator).** Persistent, bidirectional.
  Used for control-plane events: heartbeats, start/stop commands,
  signal approval round-trips, activity events, log streaming. The
  worker opens it at `worker/main.py:36` (`/ws/worker`); the
  coordinator side is `ConnectionManager` at
  `coordinator/api/websocket.py:13`.
- **WebSocket (dashboard ↔ coordinator).** Same `ConnectionManager`,
  different endpoint. Dashboards subscribe to targets
  (`account:<id>`, `live_data:<broker>:<symbol>`, etc.) and the
  coordinator broadcasts updates.
- **REST (worker → coordinator).** Used for bulk data fetches. A
  worker calls `DataClient.get_bars(...)` and the coordinator
  responds out of its parquet cache. REST, not WebSocket, because
  the payloads are large and the call pattern is request/response.
- **Tailscale.** The transport beneath all of the above. Workers,
  coordinator, and dashboard browsers all sit on the same Tailscale
  tailnet. There is no public-internet exposure and no hand-rolled
  auth between coordinator and worker — Tailscale's WireGuard mesh
  is the identity boundary. `worker/main.py:14` discovers the local
  Tailscale IP and sends it on every heartbeat.

The full enumeration of message types lives in the design spec
(see "See also" below, §2.3).

## Worked example

A single algorithm running end to end:

```
 Dashboard         Coordinator                    Worker            Broker
    |                  |                            |                 |
    | POST /api/       |                            |                 |
    | deployments      |                            |                 |
    |----------------->|  (writes row to SQLite)    |                 |
    |                  |                            |                 |
    |                  |  ws: start_instance        |                 |
    |                  |  {instance_id, manifest,   |                 |
    |                  |   config, credentials,     |                 |
    |                  |   persisted_state}         |                 |
    |                  |--------------------------->|                 |
    |                  |                            | (load algo,     |
    |                  |                            |  open broker    |
    |                  |                            |  connection)    |
    |                  |                            |                 |
    |                  |                            | REST: GET       |
    |                  |                            | /api/data/bars  |
    |                  |                            |<----------------|
    |                  |  parquet read              |                 |
    |                  |--------------------------->|                 |
    |                  |                            |                 |
    |                  |                            | (algo emits     |
    |                  |                            |  Signal on tick)|
    |                  |  ws: signal_request        |                 |
    |                  |<---------------------------|                 |
    |                  | (PDT check, approval)      |                 |
    |                  |  ws: signal_response       |                 |
    |                  |--------------------------->|                 |
    |                  |                            |                 |
    |                  |                            | place_order --->|
    |                  |                            |<--- fill -------|
    |                  |                            |                 |
    |                  |  ws: trade_executed        |                 |
    |                  |<---------------------------|                 |
    |                  |  (writes Trade row,        |                 |
    |                  |   updates Position)        |                 |
    |                  |                            |                 |
    | ws: broadcast    |                            |                 |
    | trade event      |                            |                 |
    |<-----------------|                            |                 |
```

Every durable side effect (config, trade, position) is a coordinator
write. The worker is a pure pipeline from market data and broker
fills back to coordinator events.

## Why hub-and-spoke (not peer-to-peer or cloud)

**Single source of truth simplifies state recovery.** When the
coordinator restarts after a power cut, SQLite is the truth.
Workers reconnect, the coordinator re-sends `start_instance` for
each algorithm that was running, and the system rehydrates. There
is no consensus protocol, no split-brain to reason about.

**Workers are disposable.** A worker holds broker credentials and a
running Python subprocess and nothing else. Re-image the SD card,
re-run the install script, the new worker takes over. This is the
property that lets you put live execution on a $50 Pi without
losing sleep over its failure modes.

**No distributed database.** SQLite is one file. Backup is `cp`.
Migration is Alembic. Querying is `sqlite3 data/quilt_trader.db`.
A distributed setup would buy nothing for a personal-scale algo
trading system and would cost a permanent operational tax. The
coordinator is sized to be the single instance for one person's
trading stack; that constraint is load-bearing for the rest of the
design.

## Limits & sharp edges

**Coordinator is a single point of failure.** If the coordinator
process is down, no new algorithms can start, no trades that
require signal approval can fire, and the dashboard is unreachable.
Workers can stay connected to a restarted coordinator and resume,
but they cannot operate autonomously during the outage. There is
no failover coordinator.

**SQLite means one writer.** The whole persistence layer assumes a
single coordinator instance. Running two coordinators against the
same database file is not supported and will corrupt state. Scaling
"up" (faster machine, more SSD) is the only path; scaling "out"
(more coordinators) is not.

**No multi-user auth.** There is no login, no per-user permissions,
no audit trail of who started what. The security model is "the
Tailscale tailnet is the boundary." Anyone on the tailnet can drive
the dashboard and the API. If you share your tailnet, you share
your trading stack.

**Tailscale is required for multi-host setups.** A single-Pi
deployment can run the worker against `localhost` and skip
Tailscale entirely (see design spec §2.4). Anything more than one
host assumes a working tailnet.

## See also

- [distributed-execution.md](distributed-execution.md) — how the WebSocket protocol actually works
- [data-collection.md](data-collection.md) — what the coordinator's data layer looks like
- [../superpowers/specs/2026-05-12-quilt-trader-design.md](../superpowers/specs/2026-05-12-quilt-trader-design.md) — original design
