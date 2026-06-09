# Distributed execution

> Quilt distributes algorithm execution across as many machines as you want, joined by Tailscale, controlled by one coordinator.

## What you'll learn

- The coordinator/worker contract: who owns what, what crosses the wire.
- Why Tailscale is the assumed transport and what that buys.
- How to add a worker, how to update its code, and what semantics those commands have.
- The full WebSocket message catalog — both directions — at a level you can grep against.

## The problem this solves

Running ten algorithms on one laptop blocks the GIL, leaks credentials
across strategies, and dies when the laptop sleeps. Putting them on
remote VPSes means you now own a VPC: ingress firewalls, NAT, IAM, a
credential distribution mechanism, a secure update channel, and a
monitoring stack to tell you when any of it breaks. For a personal
trading system that is wildly disproportionate.

Quilt's answer: every worker is a stateless Linux box on your Tailnet.
The coordinator pushes config and broker credentials to the worker over
an authenticated WireGuard mesh that Tailscale sets up for you. The
worker holds the credentials in memory, runs the algorithm, and writes
nothing durable to disk. If the worker dies, you re-image the SD card
or spin up a new VM, run one install command, and the coordinator
re-sends every running instance from its SQLite database. Workers can
be Raspberry Pis, a $5/month spot VM, an old MacBook in a closet — the
install one-liner is the same. There is no broker-credential file to
copy, no SSH key to rotate, no per-host firewall rule to write.

## How Quilt does it

### Why Tailscale

Tailscale gives Quilt four things that would otherwise be four separate
problems:

- **Identity baked in.** Every device on the tailnet has a stable IP
  and a hostname assigned by Tailscale itself. The coordinator doesn't
  need its own auth handshake — if a worker can open a WebSocket to the
  coordinator's tailnet IP, it's already authenticated by WireGuard.
- **No port forwarding.** The coordinator binds on its tailnet
  interface, port 8000 by default (`coordinator/config.py:9`, override
  with `QT_PORT`). Nothing is exposed to the public internet. If you're
  running the coordinator inside WSL2, see
  [`../notes/wsl-tailscale-setup.md`](../notes/wsl-tailscale-setup.md)
  for the networking caveat.
- **Encrypted by default.** WireGuard handles transport security. The
  coordinator → worker channel carries broker credentials in plaintext
  JSON because the channel itself is the encryption layer.
- **Free for personal use.** Up to 100 devices on the personal plan,
  which is roughly 100× what one trader needs.

Quilt is opinionated about this. The Tailnet is the trust boundary;
there is no second auth layer behind it. If you let an untrusted device
onto your tailnet, you have given it access to your trading stack. (See
`docs/concepts/architecture.md` "Limits & sharp edges" for the matching
caveat.)

The worker discovers its own tailnet IP by shelling out to
`tailscale ip -4` at startup (`worker/main.py:14-24`) and includes it on
every heartbeat (`worker/agent.py:76-78`) so the dashboard can show it.

### Worker is stateless

A worker process holds three things in RAM and nothing else of value:

1. The WebSocket connection to the coordinator.
2. A dict of running `LiveInstanceRuntime` objects, keyed by instance
   ID (`worker/agent.py:44`).
3. Broker credentials, which arrived inside the `start_instance`
   message that brought each runtime up.

There is no worker-side database. There is no algorithm code on disk
beyond what's needed for the current deployment lifetime. There is no
credentials file. Reboot a worker and it has nothing to restore — it
opens a new WebSocket, sends a heartbeat, and the coordinator
re-issues a `start_instance` for every instance that was running. This
reconcile path lives at `coordinator/api/websocket.py:284-289` and
fires from the heartbeat handler whenever a worker transitions from
offline to online.

**How the coordinator knows it's still the same worker.** Identity is
the worker's UUID, minted by the coordinator at `quilt worker add` time
and baked into the install one-liner as `WORKER_ID`. The install script
writes that into `/etc/quilt-trader-worker.env` as `QTW_WORKER_ID`
(`scripts/install-worker.sh:117-127`), and the worker process sends it
on every heartbeat (`worker/agent.py:68-78`). If you re-image the host
and re-run the same install one-liner (or otherwise preserve the same
`QTW_WORKER_ID`), the new process re-uses the UUID and the coordinator
treats it as the same worker. If you instead run `quilt worker add`
again to mint a fresh UUID, you get a new worker row in the dashboard.
Beyond the UUID, the tailnet is the auth boundary — there is no
per-worker certificate. A device that's on your tailnet and knows a
valid `QTW_WORKER_ID` can speak as that worker, which is why the
"Limits" section names the tailnet as the trust boundary.

The two consequences worth internalizing:

- **A worker is disposable.** Re-image the SD card, re-run the install
  one-liner, the new worker takes over. No state migration.
- **Workers don't get backed up.** The coordinator's SQLite file is the
  only thing on the stack that needs backup.

### The WebSocket protocol

Two endpoints, one per direction of the system:

- `/ws/worker` — every worker opens exactly one of these
  (`coordinator/api/websocket.py:655-665`, `worker/main.py:36-38`).
- `/ws/dashboard` — every browser tab opens one
  (`coordinator/api/websocket.py:233-241`).

All messages are JSON with a top-level `"type"` string. Dispatch
on the worker is done by `MessageRouter` at `worker/agent.py:14-27`;
dispatch on the coordinator is the `if/elif msg_type ==` ladder in
`handle_worker_message` at `coordinator/api/websocket.py:244`.

**Coordinator → worker:**

| type               | when it fires                                                       | payload summary                                                                                                  |
| ------------------ | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `start_instance`   | Deployment created, or reconcile after worker reconnect             | `instance_id`, `run_id`, `algorithm_id`, `algorithm_commit_sha`, `manifest`, `broker_type`, `environment`, `credentials`, `config`, `persisted_state` |
| `stop_instance`    | Operator stopped the deployment, or coordinator is redeploying it   | `instance_id`                                                                                                    |
| `heartbeat_ack`    | Reply to every worker heartbeat                                     | (empty)                                                                                                          |
| `signal_response`  | Reply to a worker's `signal_request` (PDT check passed/failed)      | `approved` (bool), `instance_id`, `signal`, optional `reason`                                                    |
| `tick_batch`       | TickScheduler fans out market data to subscribed instances          | `ticks`: list of per-instance tick entries                                                                       |
| `update_worker`    | Operator ran `quilt worker update <name>`                           | (empty)                                                                                                          |
| `position_closed`  | A position the algo owns was manually closed in the dashboard       | `instance_id`, `position_id`, `symbol`, `reason`                                                                 |

Source: handler table at `worker/agent.py:131-138`.

**Worker → coordinator:**

| type                | when it fires                                                  | payload summary                                                                  |
| ------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `heartbeat`         | Every `QTW_HEARTBEAT_INTERVAL` seconds (default 30)            | `worker_id`, `worker_name`, `timestamp`, `version` (git SHA), `tailscale_ip`     |
| `signal_request`    | Algorithm emitted a `Signal`; waiting for PDT/approval         | `instance_id`, `signal` (serialized), `timestamp`                                |
| `state_checkpoint`  | Algorithm called `save_state()`; also on graceful shutdown     | `instance_id`, `state` (JSON), `timestamp`                                       |
| `decision_log`      | Tick-level decision data for the audit log                     | `instance_id`, `log_entry`, `timestamp`                                          |
| `instance_started`  | `LiveInstanceRuntime.bring_up` succeeded                       | `instance_id`, empty `payload`                                                   |
| `instance_stopped`  | Instance shut down cleanly                                     | `instance_id`, empty `payload`                                                   |
| `instance_error`    | `LiveInstanceRuntime.bring_up` raised                          | `instance_id`, `payload.error` (string)                                          |
| `activity_event`    | Granular event for the dashboard activity log                  | `worker_id`, `instance_id`, `event_type`, `severity`, `payload`                  |
| `algo_log`          | A log line from the algorithm subprocess                       | `worker_id`, `instance_id`, `logger_name`, `level`, `message`                    |
| `equity_sample`     | Per-tick equity snapshot for the live equity curve             | `instance_id`, `run_id`, `timestamp`, `portfolio_value`, `cash`                  |
| `trade_sample`      | Per-fill sample for the live trade log                         | `instance_id`, `run_id`, `symbol`, `side`, `quantity`, `fill_price`, `fees`, …   |
| `update_complete`   | Worker finished applying `update_worker` (or failed)           | `payload.success` (bool), `payload.method`, optional `payload.error`             |

Source: `_send` and `send_*` methods at `worker/agent.py:48-130`; handler
ladder at `coordinator/api/websocket.py:251-534`.

The two enumerations above are the canonical ones; the design spec's
older list (§2.3) used `start_algorithm`/`stop_algorithm`/
`trade_executed`/`algo_event`/`algo_error`/`algo_stopped`. Those names
have been refactored — current code uses `start_instance`/`stop_instance`
and folds trade/error/stop events through `activity_event` and the
`instance_started`/`instance_stopped`/`instance_error` lifecycle
messages. Read the tables above, not the spec.

Note that `signal_response` is sent from coordinator → worker, and is
auto-approved today (`coordinator/api/websocket.py:338-347`) — the PDT
gating logic is a planned hook, not a current enforcement point.

### Adding a worker

1. **Register the worker on the coordinator.**

   ```
   quilt worker add --name pi-1
   ```

   This is `worker_add` at `sdk/cli/commands/worker.py:78-101`. It
   POSTs to `/api/workers`, gets back a new worker UUID and a
   single-use `install_token`, and prints both.

   Flags: `--name` (required), `--tailscale-ip` (optional, the
   coordinator will learn it from heartbeats anyway), and
   `--max-algorithms` (default 2).

2. **Get the install one-liner.**

   ```
   quilt worker install-command pi-1
   ```

   This is `worker_install_command` at
   `sdk/cli/commands/worker.py:104-125`. It hits
   `/api/workers/{id}/install-command` on the coordinator, which
   renders a `curl … | sudo -E bash` line with `TAILSCALE_AUTHKEY`,
   `COORDINATOR_URL`, `WORKER_ID`, `WORKER_NAME`, and `WORKER_TOKEN`
   baked in (`coordinator/api/routes/workers.py:189-225`). The Tailscale
   authkey is pulled from the encrypted `tailscale_authkey` setting if
   you configured one; otherwise the placeholder `tskey-CHANGE-ME` is
   substituted and you fill it in by hand. If you don't yet have a
   Tailscale account, sign up at [tailscale.com](https://tailscale.com)
   and generate an authkey from the admin console (Settings → Keys →
   Generate auth key). A reusable, non-ephemeral key with no expiry is
   the simplest choice for personal use; tighten as needed.

3. **Paste it into a fresh shell on the new host.** That's
   `scripts/install-worker.sh`. It:

   - Installs Tailscale and brings the host up on your tailnet
     (`scripts/install-worker.sh:58-69`).
   - Verifies it can reach `$COORDINATOR_URL/api/health` over the
     tailnet (`scripts/install-worker.sh:71-78`).
   - Downloads the worker tarball, token-gated, from
     `$COORDINATOR_URL/api/workers/install/package.tar.gz`
     (`scripts/install-worker.sh:93-101`).
   - Creates a venv under `/opt/quilt-trader-worker/.venv`, installs
     `-e .[worker]` (`scripts/install-worker.sh:103-110`).
   - Writes `/etc/quilt-trader-worker.env` and a systemd unit, enables
     and starts it (`scripts/install-worker.sh:112-153`).
   - POSTs to `/api/workers/install/claim/<id>` to invalidate the
     install token (`scripts/install-worker.sh:164-171`).

4. **It appears in the dashboard.** The first heartbeat (every 30s by
   default) flips the worker row to `status="online"` and broadcasts a
   `worker_connected` event to all dashboard subscribers
   (`coordinator/api/websocket.py:254-289`). The dashboard's worker
   list reflects this without a refresh.

`quilt worker list` shows what the coordinator knows
(`sdk/cli/commands/worker.py:36-55`). `quilt worker show <name>` is the
detail view (`sdk/cli/commands/worker.py:58-75`).

### Updating a worker

```
quilt worker update pi-1
```

`worker_update` at `sdk/cli/commands/worker.py:147-227` POSTs to
`/api/workers/{id}/update`, which sends `{"type": "update_worker"}` over
the worker's open WebSocket (`coordinator/api/routes/workers.py:238-262`).

The worker's `_handle_update_worker` (`worker/agent.py:263-325`) then
does:

1. **Try `git pull origin main`** if `.git/` exists in the install
   directory (`worker/agent.py:230-243`). This preserves history for
   workers installed by `git clone` (a developer workflow).
2. **Fall back to a tarball download** from
   `$COORDINATOR_HTTP_URL/api/workers/install/package.tar.gz?token=...`
   if the git pull fails or there is no `.git/`
   (`worker/agent.py:245-261`). This is the path the standard install
   script takes, since the tarball install ships no `.git/`.
3. **Re-install dependencies** with `pip install -e .[worker]`
   (`worker/agent.py:301-306`).
4. **Send `update_complete`** with the method used and success/error
   (`worker/agent.py:309-311`).
5. **Exit with `os._exit(0)`** (`worker/agent.py:314`). systemd's
   `Restart=always` brings it back, this time on the new code.

The CLI then polls `/api/workers/{id}` until the worker goes offline
and comes back online (`sdk/cli/commands/worker.py:175-227`). Pass
`--no-wait` to skip polling and return immediately.

## Worked example: two workers, two strategies

Setup:

- `pi-1` — Raspberry Pi 4 in a closet, running an equities momentum algo
  against your Alpaca paper account.
- `vm-options` — $5/month spot VM, running an options spreads algo
  against your Tradier live account.

Both workers are registered with `quilt worker add`, both are on the
same tailnet, both have an open `/ws/worker` connection to the
coordinator.

**Tick from the equities algo.** `pi-1`'s `TickProcessor` runs the
algo, gets back a `Signal`, and sends `signal_request` over the
WebSocket (`worker/tick_loop.py:98-99`). The coordinator's
`handle_worker_message` receives it, checks PDT state for the
associated account (currently auto-approves;
`coordinator/api/websocket.py:338-347`), and sends `signal_response`
back. `pi-1` submits the order to Alpaca, then emits `trade_sample`,
`activity_event`, and (when the algo calls `save_state()`)
`state_checkpoint`. The coordinator persists the checkpoint to
SQLite so that a restart of `pi-1` doesn't lose the algorithm's
internal state.

**Tick from the options algo, simultaneously.** `vm-options` follows
the exact same protocol. The coordinator handles both message streams
on the same `ConnectionManager`. Neither worker is aware of the other —
they share no state, no broker connection, no algorithm code.

**PDT check on the coordinator.** When the equities algo emits a
day-trade-eligible signal, the rule check happens server-side, in the
coordinator's `signal_request` handler, before it sends back
`signal_response`. The worker is not trusted to enforce PDT — by
design. Even an algorithm that ignored a rejection couldn't day-trade,
because the worker only submits the order after `approved: True` comes
back.

**`vm-options` disconnects.** Network blip; its WebSocket drops. The
coordinator's `handle_worker_disconnect`
(`coordinator/api/websocket.py:619-652`) flips the worker row to
`status="offline"`, broadcasts `worker_disconnected` to dashboards, and
removes it from the `TickScheduler`. The options algo *freezes* —
ticks no longer flow, the algorithm is not running anywhere. When the
worker reconnects, the heartbeat handler reconciles by re-sending
`start_instance` for every still-`running` instance assigned to it
(`coordinator/api/websocket.py:537-600`). The algorithm rehydrates
from `persisted_state` and continues. The equities algo on `pi-1` is
untouched throughout.

## Limits & sharp edges

- **Tailscale is not strictly required at the protocol level.** The
  WebSocket only needs a route between the worker and the coordinator.
  A single-Pi setup can run the worker against `localhost` and skip
  Tailscale entirely (spec §2.4). But Tailscale is the only documented,
  install-supported transport. The install script assumes it
  (`scripts/install-worker.sh:58-69`), and the security model assumes
  it (no second auth layer).
- **Workers can't talk to each other.** All worker-to-worker
  communication routes through the coordinator. There is no peer
  discovery, no shared state. If you want two algorithms on different
  workers to coordinate, that coordination lives on the coordinator
  (typically as Position rows or a shared algorithm config), not as a
  side channel between workers.
- **No automatic failover if a worker dies mid-deployment.** If a
  worker goes offline while running an algorithm, the algorithm
  freezes. The coordinator does *not* reassign it to a healthy worker.
  Reassignment is a deliberate operator action (delete the instance,
  recreate it on a different worker, or wait for the original to come
  back). This is intentional — silent reassignment with broker
  credentials in flight is a category of bug worth avoiding.
- **WebSocket reconnect is naive.** `worker/main.py:38` uses
  `websockets.connect` as an async iterator, which retries with
  exponential backoff between attempts. There is no jitter, no upper
  bound on the backoff window in the worker code itself. While
  disconnected, the algorithm cannot trade.
- **Worker updates have no rollback.** If `update_worker` lands a
  broken commit, the worker `pip install`s it, exits, systemd brings
  it back, and the new (broken) code is what runs. There is no canary,
  no "roll back to previous SHA" command. Test updates against a
  scratch worker before pushing to your live one.
- **Install token is single-use but not short-lived.** Once minted,
  the token stays valid until the worker claims it (or you call
  `quilt worker regenerate-token`). If the token leaks before
  install, the leaker can claim your worker slot.

## See also

- [`architecture.md`](architecture.md) — system topology and where state lives.
- [`cli-and-agentic-workflows.md`](cli-and-agentic-workflows.md) — driving the worker lifecycle from the CLI.
- [`../notes/wsl-tailscale-setup.md`](../notes/wsl-tailscale-setup.md) — WSL2 networking caveats when the coordinator is inside WSL2.
- [`../superpowers/specs/2026-05-12-quilt-trader-design.md`](../superpowers/specs/2026-05-12-quilt-trader-design.md) §2.3 — original protocol spec (message names are stale; see notes above).
