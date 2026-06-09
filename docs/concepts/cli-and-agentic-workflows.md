# CLI and Agentic Workflows

> Every Quilt operation is a CLI command. That makes Quilt one of the few algo trading platforms an AI agent can drive end-to-end without scraping a UI.

## What you'll learn

- The CLI design principles: one verb, one exit code, one JSON shape.
- The `--json` contract and which commands honour it.
- The five-code exit convention agents can branch on.
- A worked recipe for a Claude-Code-style agent driving the build → backtest → deploy loop.
- Where the agent story still leaks — and how to work around it.

## The problem this solves

Most trading platforms are GUI-first. Their CLI, when one exists, is a thin admin tool for the human operator: start the daemon, dump some logs, maybe run a one-shot script. Anything important — wiring a strategy to an account, watching a backtest, promoting a paper run to live — happens through a web dashboard with HTML forms and confirmation modals. That is a fine product surface when a person is in the loop. It is a dead end for AI workflows, where an agent has to:

- query state programmatically (no HTML scraping, no XPath),
- take actions deterministically (every flag named, every input typed),
- parse responses without guessing the shape (machine output, stable keys),
- distinguish "the operation failed" from "the tool failed" cleanly (so the agent knows whether to retry, escalate, or stop).

Quilt was built CLI-first. Every state read and every state change has a `quilt` subcommand, every read command honours `--json`, and every failure mode maps to a documented exit code. The dashboard is a thin Vite app over the same `/api/*` endpoints the CLI hits — when you run `quilt deployment list`, you are calling the exact REST handler the dashboard uses (`coordinator/api/routes/deployments.py:77`).

The intended primary user, then, is not the human at a keyboard. It is the agent in the loop, with the human reading the transcript.

## How Quilt does it

### Global flags

Defined on the root group in `sdk/cli/main.py:5-10`:

| Flag | Effect |
|---|---|
| `--coord <url>` | Override coordinator URL (also `QUILT_COORDINATOR_URL`, then `~/.quilt/config.yaml`, then `http://localhost:8000`). Resolution order is in `sdk/cli/config.py:63-72`. |
| `--json` | Emit machine-readable JSON to stdout where applicable. Status lines stay on stderr. |
| `-q, --quiet` | Suppress non-essential output. Honoured per-command; many commands ignore it because they only emit essentials anyway. |

There is **no** `--verbose` flag, and there is no global `--no-color`. Status messages are routed to stderr (`sdk/cli/output.py:36-38`), so piping stdout into `jq` works regardless of TTY.

### Exit codes

Five codes, set centrally in `sdk/cli/client.py:39-54` and `sdk/cli/output.py:41-44`:

| Code | Meaning | Source |
|---|---|---|
| `0` | Success | normal completion |
| `1` | Internal / unexpected | unknown HTTP status, uncaught exception |
| `2` | User error | 4xx from API, bad arguments, missing `--yes` on destructive ops |
| `3` | Coordinator unreachable | `httpx.ConnectError` / `ConnectTimeout` |
| `4` | Operation failed | 5xx from API, backtest reached `failed`/`cancelled` terminal state, subprocess (alembic, npm) returned non-zero |

The HTTP-to-exit mapping is the load-bearing piece for agents: a `404` on `quilt deployment show some-id` exits `2` (user error — bad id), but the coordinator being down exits `3` (infra problem — retry later, or boot the coord). An agent can branch on the integer without parsing English from stderr.

**Known wart:** `quilt doctor` returns `1` if any check is `WARN` and `2` if any is `FAIL` (`sdk/cli/commands/doctor.py:109-112`). That overloads the meaning of `1` and `2` from the conventions above. Agents driving `doctor` should treat any non-zero exit as "look at the JSON output" rather than mapping the code literally.

### Command surface

Subcommands live in `sdk/cli/commands/`. Grouped by domain:

| Command | Purpose | Most useful flags |
|---|---|---|
| `quilt init` | Write `~/.quilt/config.yaml`, create `./data`, run migrations | `--coord-url`, `--data-dir`, `--force`, `--skip-migrate` |
| `quilt doctor` | Run health checks against config, dashboard build, coord process, DB, disk | (no flags) |
| `quilt validate` | Validate a `quilt.yaml` package before install | `--path` |
| `quilt up` / `quilt down` | Aliases for `coord start` / `coord stop` | (delegate to coord) |
| `quilt coord {start,stop,restart,status,logs}` | Coordinator process lifecycle | `--port`, `--host`, `--foreground`, `--follow` |
| `quilt dashboard {build,dev}` | Vite build / HMR dev server | (none) |
| `quilt db {migrate,status,revisions}` | Alembic wrappers | (none) |
| `quilt account {list,show,create,update,unlock,delete}` | Broker credentials | `--broker`, `--env`, `--api-key`, `--secret-key`, `--yes` |
| `quilt algorithm \| algo {list,show,install,uninstall,update}` | Installed algorithm packages | `--as`, `--ref`, `--yes` |
| `quilt deployment \| deploy {list,show,create,start,stop,delete,runs,report,trades,activity}` | Algorithm-on-account-on-worker bindings | `--algo`, `--account`, `--worker`, `--config`, `--follow`, `--yes` |
| `quilt worker {list,show,add,install-command,regenerate-token,update,delete}` | Registered Pi workers | `--name`, `--tailscale-ip`, `--max-algorithms`, `--no-wait` |
| `quilt backtest {run,list,show,report,trades,delete}` | Coordinator-side backtests | `--algo`, `--start`, `--end`, `--cash`, `--config`, `--wait` |
| `quilt research session {create,list,show}`, `quilt research {sweep,walk-forward,report}` | Strategy Validation Lab | `--session-id`, `--search`, `--max-trials`, `--no-wait` |
| `quilt data {subscribe,unsubscribe,subscriptions,download,downloads,available,scrapers,scraper-run}` | Live ticks, historical downloads, custom scrapers | `--symbol`, `--start`, `--end`, `--provider` |
| `quilt data datasets {list,show,download,downloads,quota}` | Time-series datasets (FMP and beyond) | `--symbol`, `--from`, `--to`, `--param` |
| `quilt settings {list,get,set,unset}` | Encrypted coordinator settings (API keys, tokens) | (positional `key value`, plus `--username/--password` for theta-data) |

Both `algo` and `algorithm` work; same for `deploy` and `deployment` (wired in `sdk/cli/main.py:57,62`).

### Where credentials live

Agents driving Quilt typically receive broker keys via env vars in their sandbox and pass them through on `account create`:

    quilt account create --name "Alpaca Paper" --broker alpaca --env paper \
      --api-key "$ALPACA_KEY" --secret-key "$ALPACA_SECRET"

The coordinator writes them to its encrypted settings store; subsequent runs reference the account by name or id, never by raw key. Broker identifiers accepted today include `alpaca` and `tradier` (Tradier uses `--access-token` and `--account-id` instead of `--secret-key`; see `sdk/cli/commands/account.py:78-107`). There is no CLI verb that reveals a stored secret — `quilt settings get` returns which keys are *set*, not their values (`sdk/cli/commands/settings.py:73-86`). For provider-level credentials (data vendors, etc.) use `quilt settings set <key> <value>`.

### Machine-readable everywhere

Every read command honours `--json`. The shape is the raw response from the coordinator API, serialised with `json.dumps(..., default=str, indent=2)` (`sdk/cli/output.py:15-19`). No envelope, no `{"data": ...}` wrapping, no pagination metadata for endpoints that return arrays.

`quilt deployment list --json` returns a list of objects with this shape (built in `coordinator/api/routes/deployments.py:49-70,192-195`):

```json
[
  {
    "id": "a3f2c1d4-...",
    "algorithm_id": "8b91...",
    "account_id": "2c44...",
    "worker_id": "61d7...",
    "algorithm_name": "momentum-v1",
    "account_name": "Alpaca Paper",
    "worker_name": "pi-1",
    "status": "running",
    "active_run_id": "fa01...",
    "config_values": {"lookback": 20},
    "lifetime_metrics": {
      "trade_count": 142,
      "win_count": 9,
      "loss_count": 5,
      "win_rate": 64.28,
      "lifetime_pnl": 312.44,
      "realized_pnl": 220.10,
      "unrealized_pnl": 92.34
    },
    "created_at": "2026-05-01T14:22:18+00:00",
    "updated_at": "2026-06-08T09:01:03+00:00"
  }
]
```

> Agents typically extract `id` for follow-up calls, `status` for state checks, and `lifetime_metrics` for decision logic. Treat any unlisted key as opaque.

The `id`, `algorithm_id`, `account_id`, `worker_id` fields are full UUIDs in `--json` mode; the human table shows the first 8 chars (`sdk/cli/commands/deployment.py:55-58`).

Mutation commands also emit JSON when `--json` is set — `quilt deployment create --json` returns the new deployment object; `quilt deployment start --json` returns `{"active_run_id": "..."}` (the body of the `/start` endpoint).

There is no JSON schema versioning yet. New fields are added in additive, non-breaking patches. Agents should treat unknown keys as opaque rather than asserting on object shape.

### Stable command names

Quilt is too young to make a formal backwards-compatibility promise. The current policy:

- The five "domain" groups (`algorithm`, `account`, `worker`, `deployment`, `backtest`) and their `list / show / create / delete` verbs are stable. They map 1:1 to REST resources and renaming them would break the API too.
- Short aliases (`algo`, `deploy`) are kept registered in `main.py` so existing scripts don't have to update on rename.
- Flag names occasionally evolve (e.g. `--account` resolves a name OR id; older `--account-id` is folded in). Removed flags will get a release of deprecation warnings before they go.

If you're scripting against the CLI, pin to a Quilt version in your venv (`pip install quilt-trader==X.Y`) the same way you would for any internal API.

### Idempotency, and where it isn't

The spec for this doc claimed several commands are idempotent. Walking the actual code, most of them aren't. The honest picture:

- **`quilt init`** is **not** idempotent. If `~/.quilt/config.yaml` exists it exits `2` ("config already exists; use --force to overwrite", `sdk/cli/commands/init.py:27-28`). Re-running it on a fresh box is safe; re-running on a configured box requires `--force` (which **overwrites** the file).
- **`quilt coord start`** **is** idempotent. If the PID file points to a live process serving `/api/health`, it prints `already running` and returns `0` (`sdk/cli/commands/coord.py:34-37`).
- **`quilt algorithm install`** creates a new row on each call. Names are not unique in the `algorithms` table (`coordinator/database/models.py:65-70`); re-running with the same name yields a second row rather than updating the first. Expect duplicates in `quilt algorithm list` if you re-install. Use `quilt algorithm update <name>` to pull the latest commit for a GitHub-sourced install.
- **`quilt deployment create`** creates a new UUID row on every call, even with identical `(algo, account, worker, config)` — there is no upsert. Deployments have no human name. Agents should `list --json`, filter on the tuple, and only `create` if no match.
- **`quilt deployment start` / `stop`** are guarded by status (409 if the deployment is in the wrong state — `coordinator/api/routes/deployments.py:214-215`), which maps to exit `2`. Repeating a `start` on an already-running deployment is a user error, not a no-op.
- **Destructive commands require `--yes`**: `algorithm uninstall`, `account delete`, `worker delete`, `deployment delete`, `backtest delete`. Without it they exit `2`. Agents should always pass `--yes` explicitly.

## Worked example: agent-driven algorithm development loop

This is what a Claude-Code-style agent's tool calls look like for "build, backtest, and deploy a momentum strategy on TSLA":

```bash
# 1. The agent has written ./generated-algo/quilt.yaml + algorithm.py.
#    Validate the package before touching the coordinator.
quilt validate --path ./generated-algo
#   stdout: PASS: momentum-v1 (algorithm) v0.1.0 is valid
#   exit 0  → proceed
```

```bash
# 2. Install into the local coordinator.
quilt algorithm install ./generated-algo --as momentum-v1 --json
#   stdout: {"id": "8b91...", "name": "momentum-v1", "install_status": "installed", ...}
#   agent extracts: ALGO_ID = body["id"]
```

```bash
# 3. Run a backtest synchronously, parse the metrics.
quilt backtest run \
  --algo "$ALGO_ID" --start 2024-01-01 --end 2024-12-31 \
  --cash 100000 --wait --json
#   stderr:  [queued] ...
#            [running] preparing data...
#            [completed]
#   stdout:  {"id": "...", "status": "completed", "total_return": 0.234,
#             "sharpe_ratio": 1.8, "max_drawdown": -0.12, "trade_count": 42,
#             "win_rate": 0.61, ...}
#   exit 0   on completed
#   exit 4   on failed/cancelled (--wait blocks until terminal)
```

The agent now branches on the integer: exit `4` → bail out, write the failure back to the user; exit `0` → parse the JSON and check Sharpe against a threshold.

```bash
# 4. Sharpe > 1.0 — create the paper-trading deployment.
quilt deployment create \
  --algo momentum-v1 --account "Alpaca Paper" --worker pi-1 \
  --config '{"lookback": 20}' --json
#   stdout: {"id": "a3f2c1d4-...", "status": "stopped", "active_run_id": null, ...}
#   agent extracts: DEPLOY_ID = body["id"]
```

`--algo`, `--account`, `--worker` accept names, short-ID prefixes, or full UUIDs — the resolver in `sdk/cli/resolve.py` does the lookup and exits `2` with an "ambiguous prefix" or "no match" message if it can't pick one.

```bash
# 5. Start the deployment.
quilt deployment start "$DEPLOY_ID"
#   stdout: started, active_run_id=fa01...
#   exit 0
```

```bash
# 6. Watch live activity. --follow emits NDJSON (one JSON object per line)
#    when --json is set, so the agent can stream-parse and react to events.
quilt deployment activity "$DEPLOY_ID" --follow --severity warn --json
#   stdout: {"timestamp": "...", "severity": "warn", "kind": "log", ...}
#           {"timestamp": "...", "severity": "error", "kind": "event", ...}
#   exit on Ctrl+C: 0
#   exit on persistent reconnect failure: 3
```

The `--follow` JSON-lines behaviour is implemented in `sdk/cli/follow.py:113-117`. (An earlier version of this doc claimed streaming commands don't emit JSON line-by-line. They do, today.)

The agent has driven the entire loop — write code, validate, install, backtest, evaluate, deploy, observe — without ever opening a browser.

## Limits and sharp edges

- **No schema version on `--json` output.** Future additions are additive (new keys, not renamed ones) but you should diff against fixtures if you depend on the shape. There is no `--json-schema` discovery command yet.
- **No machine-readable error catalog.** Failures are `(exit_code, stderr_string)`. Most coordinator errors carry the FastAPI `detail` text on stderr; agents that need to branch on a *specific* failure (e.g. "broker auth rejected") have to substring-match the message. A stable error-code field is on the wishlist.
- **`quilt doctor` overloads exit codes 1 and 2.** See above. Treat doctor's non-zero as "read the JSON `checks` array" rather than mapping the integer.
- **Some flows still require the dashboard.** Broker credential entry uses the encrypted setting store via the standard API (`quilt account create --api-key ... --secret-key ...` works), but if you bootstrapped the box through the dashboard and your secrets are sitting in the encrypted store under a key the CLI can't `get`, your only recourse is the dashboard's settings panel. `quilt settings get` returns a status dict (which secrets are *set*), not the values (`sdk/cli/commands/settings.py:73-86`).
- **Worker install needs an out-of-band step.** `quilt worker add` returns an install token; the actual Pi bring-up runs `quilt worker install-command <name>` (which prints a curl one-liner) on the worker box itself. That command isn't agent-driven from the coordinator side.
- **`init` is destructive on re-run with `--force`.** Agents bringing up a fresh box should check `~/.quilt/config.yaml` exists before deciding to re-init.
- **Names are not unique.** `algorithm install`, `worker add`, `account create` will happily create duplicates. The resolver (`sdk/cli/resolve.py:40`) exits `2` with "ambiguous" when it can't disambiguate. Agents should always check `list --json` before creating.
- **No batch / transactional commands.** Each subcommand is one REST call. "Install algo + create deployment + start" is three independent operations; the agent has to handle partial-success rollback itself.

## See also

- [`writing-algorithms.md`](./writing-algorithms.md) — what an agent is actually generating before the install step
- [`backtest-accuracy.md`](./backtest-accuracy.md) — what an agent should look at in the backtest report JSON, and what to trust
- [`distributed-execution.md`](./distributed-execution.md) — the worker / deployment lifecycle the agent is driving over the wire
