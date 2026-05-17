# Quilt CLI — Design Spec

**Date:** 2026-05-17
**Status:** Draft for implementation
**Scope:** A fully-functional `quilt` CLI that operators (and AI agents) use to manage the coordinator, dashboard, algorithms, accounts, workers, deployments, backtests, data, and settings. Replaces the small `quilt dev` set that exists today.

**Reference:** Builds on the live execution and running-algorithm UX specs. The CLI primarily wraps existing HTTP endpoints; a small set of diagnostic commands work directly with the filesystem and DB when the coordinator is down.

---

## 1. Motivation

Every operator action today goes through the React dashboard. Algorithm authors have a small `quilt dev` CLI (`validate`, `backtest`, `run`) that pre-dates most of the current system and uses a different backtest engine (Lumibot) than the coordinator. That divergence means local backtest results don't match what live deployments produce.

Operators need a CLI to:
- Start/stop the coordinator and dashboard as scriptable processes.
- Run backtests through the same engine the dashboard uses, so results are directly comparable to live deployments.
- Install algorithms (from GitHub or local directories), manage workers, accounts, and deployments.
- Control market data subscriptions and downloads.
- Inspect settings, follow activity streams, and diagnose breakage.

AI agents need every operator action to be scriptable: no interactive prompts, structured JSON output on demand, meaningful exit codes, and a clean way to follow long-running streams.

After this spec ships, an operator (or agent) can stand up the whole stack, install an algorithm, run a backtest, start a deployment, watch its activity, and tear it all down — all from the command line, without ever opening the dashboard.

---

## 2. Architecture

### 2.1 Two modes inside one binary

**Client mode (most commands).** The command builds an `httpx` client against the coordinator URL, makes the request, formats the response. The CLI never writes to the DB in this mode.

**Diagnostic mode (a small set).** Commands that need to work when the coordinator is down or that operate on the local filesystem/DB directly:

- `quilt coord {start | stop | restart | status | logs}` — coordinator process lifecycle.
- `quilt dashboard {build | dev}` — Vite build / dev server.
- `quilt up` / `quilt down` — aggregate lifecycle (coord starts, which serves the built dashboard from `dashboard/dist/`).
- `quilt init` — first-time config setup.
- `quilt doctor` — diagnoses common breakage (coord status falls back to filesystem when HTTP unreachable).
- `quilt db {migrate | status | revisions}` — Alembic wrapper.
- `quilt validate <path>` — pre-flight check for an algorithm package directory (no coord needed).

### 2.2 Single binary, single config

All commands live under one `quilt` entry point (the existing `pyproject.toml` console script). Config resolution order:

1. CLI flag (e.g. `--coord http://other-host:8000`)
2. Env var (`QUILT_COORDINATOR_URL`)
3. `~/.quilt/config.yaml` field
4. Default (`http://localhost:8000`)

`QUILT_CONFIG=/path/to/config.yaml` overrides the config file location.

### 2.3 Module layout

Keep `sdk/cli/main.py` as the entry point. Remove `sdk/cli/backtest.py` (Lumibot-based) and `sdk/cli/run.py` (local paper-trade stub). Promote `validate` to top-level. Add new top-level modules and a `commands/` subdir:

```
sdk/
├── cli/
│   ├── main.py              # entry point — extend command tree
│   ├── client.py            # NEW — httpx-based API client + error mapping
│   ├── output.py            # NEW — table/JSON rendering helpers
│   ├── config.py            # MODIFIED — add coordinator_url
│   ├── process.py           # NEW — PID file / log management
│   ├── follow.py            # NEW — websocket tail helper
│   ├── validate.py          # KEEP — refactor to call sdk/validation.py
│   ├── doctor.py            # NEW — runs the check battery
│   └── commands/            # NEW — one file per top-level group
│       ├── coord.py
│       ├── dashboard.py
│       ├── deployment.py
│       ├── algorithm.py
│       ├── worker.py
│       ├── account.py
│       ├── data.py
│       ├── backtest.py
│       ├── settings.py
│       ├── db.py
│       └── init.py
└── validation.py            # NEW — shared validation logic (CLI + install endpoint)
```

`sdk/cli/dev/`, `sdk/cli/backtest.py`, and `sdk/cli/run.py` are deleted.

---

## 3. Command Tree

Noun-verb structure (`kubectl`/`gh`/`docker` style). Aliases on long ones.

```
quilt
├── up                          # coord start (which serves built dashboard at /)
├── down                        # coord stop
├── init                        # first-time setup
├── doctor                      # diagnose breakage
├── validate <path>             # pre-flight algorithm package check
├── version
│
├── coord
│   ├── start [--foreground]    # daemonize uvicorn; log to ~/.quilt/log/coord.log
│   ├── stop
│   ├── restart
│   ├── status                  # running, pid, uptime, port, db_version
│   └── logs [--follow] [-n N]
│
├── dashboard
│   ├── build                   # npm run build
│   └── dev                     # npm run dev in foreground (HMR mode)
│
├── db
│   ├── migrate                 # alembic upgrade head
│   ├── status                  # alembic current
│   └── revisions
│
├── algorithm   (alias: algo)
│   ├── list
│   ├── show <id>
│   ├── install <path-or-url> [--as <name>] [--ref <sha|branch>]
│   │       # accepts GitHub URL OR local directory
│   ├── update <id>             # pull latest from repo (errors if local-installed)
│   └── uninstall <id> [--yes]
│
├── account
│   ├── list
│   ├── show <id>
│   ├── create --name --broker --env [--api-key ...] [--secret-key ...]
│   ├── update <id> [--api-key ...] [--secret-key ...]
│   ├── delete <id> [--yes]
│   └── unlock <id>             # clear locked_by if stuck
│
├── worker
│   ├── list
│   ├── show <id>
│   ├── add --name              # creates row + prints install one-liner
│   ├── install-command <id>    # re-print install one-liner
│   ├── regenerate-token <id>
│   └── delete <id> [--yes]
│
├── deployment   (alias: deploy)
│   ├── list [--algo <id>] [--worker <id>] [--status running]
│   ├── show <id>
│   ├── create --algo <id> --account <id> --worker <id> [--config '{}']
│   ├── start <id>
│   ├── stop <id>
│   ├── delete <id> [--yes]
│   ├── runs <id>
│   ├── report <id> [--run <run_id>]
│   ├── trades <id> [-n N]
│   └── activity <id> [--follow] [--severity info] [--kind event|log|all]
│
├── backtest
│   ├── run --algo <id> --start <date> --end <date>
│   │       [--cash 100000] [--config '{}'] [--wait]
│   ├── list [--algo <id>]
│   ├── show <id>
│   ├── report <id>                  # full report blob (use with --json)
│   ├── trades <id>
│   └── delete <id> [--yes]
│
├── data
│   ├── subscribe <broker> <symbol> [--retention-hours 24]
│   ├── unsubscribe <broker> <symbol>
│   ├── subscriptions
│   ├── download --symbol <s> --start <d> --end <d>
│   │           --provider polygon [--timeframe 1day]
│   ├── downloads
│   ├── available
│   ├── scrapers
│   ├── scraper-run <name>
│   └── scraper-logs <name> [--follow]
│
└── settings
    ├── get [<key>]
    ├── set <key> <value>            # auto-encrypted for known-sensitive keys
    ├── unset <key>
    └── list
```

Removed (vs. current `sdk/cli/` state): `dev/`, `dev/backtest.py`, `dev/run.py`. `dev/validate.py` becomes `sdk/cli/validate.py` (renamed at the top level).

---

## 4. Process Lifecycle

### 4.1 Runtime directory

`~/.quilt/` (override via `QUILT_HOME` env var):

- `~/.quilt/config.yaml` — single-file config (just `coordinator_url` for v1).
- `~/.quilt/run/coord.pid` — current coordinator PID.
- `~/.quilt/log/coord.log` — coordinator stdout/stderr, rotated daily (Python `TimedRotatingFileHandler`, keep 7).

### 4.2 `quilt coord start`

1. If `coord.pid` exists, read PID. If alive AND `GET /api/health` returns 200, print "already running (pid=N)" and exit 0.
2. If PID file exists but stale (process dead OR healthcheck fails), remove the file and continue.
3. Spawn `uvicorn coordinator.main:app --host 127.0.0.1 --port 8000` as a detached subprocess: `os.setsid`, stdout/stderr redirected to `coord.log`, parent returns.
4. Write child PID to `coord.pid`.
5. Poll `GET /api/health` for up to 30 seconds (200ms interval). If healthy → print `coord started (pid=N, port=8000)` and exit 0. If not healthy after timeout → kill the child, remove the PID file, exit 4.
6. `--foreground` flag: skip the daemonize step; replace the current process with `uvicorn` via `os.execvp`. PID file is NOT written. Use this for debugging.

### 4.3 `quilt coord stop`

1. If no PID file → print "not running" and exit 0 (idempotent).
2. Send SIGTERM to the PID. Wait up to 10s for exit.
3. If still alive, SIGKILL and warn.
4. Remove the PID file. Exit 0.

### 4.4 `quilt coord restart`

`stop` → wait for full exit → `start`.

### 4.5 `quilt coord status`

```
state:       running        # or stopped, unhealthy
pid:         12345          # or null
uptime:      2h 14m 32s     # or null
port:        8000
db_version:  abc1234        # alembic current revision
```

With `--json`: identical structure as a JSON object.

`unhealthy` state: PID is alive but `/api/health` doesn't 200 within 2s. Surfaces a degraded coord that hasn't crashed but isn't responsive.

### 4.6 `quilt coord logs [--follow] [-n N]`

`-n` defaults to 50. `--follow` does `tail -f` semantics (poll every 200ms for new bytes; handle log rotation by reopening on inode change).

### 4.7 `quilt up` and `quilt down`

`up`:
1. If `dashboard/dist/index.html` doesn't exist, print warning ("dashboard not built; run `quilt dashboard build`") but continue.
2. Run `quilt coord start`.
3. Exit with `coord start`'s exit code.

`down`: alias for `quilt coord stop`.

The coordinator must mount `dashboard/dist/` at `/` as static files when present. If this isn't already happening in `coordinator/main.py`, add it (use `fastapi.staticfiles.StaticFiles`, mount at `/`, serve `index.html` for unmatched paths so the React Router still works).

### 4.8 `quilt dashboard build`

`subprocess.run(["npm", "run", "build"], cwd="dashboard", check=True)`. Streams output. Exits with the npm exit code.

### 4.9 `quilt dashboard dev`

Foreground only — exec `npm run dev` in `dashboard/`. The dev server runs on port 3000 by default and proxies API calls to the coord. Use when iterating on `.tsx` files with HMR. Ctrl+C stops it.

---

## 5. Output Format

A single shared helper module `sdk/cli/output.py`:

```python
def print_table(rows: list[dict], columns: list[str]) -> None: ...
def print_json(payload: Any) -> None: ...
def print_status(message: str) -> None: ...  # to stderr; suppressed by -q
def fail(code: int, message: str) -> None: ...
```

The global flags `--json`, `-q`, and `--coord <url>` are attached to the root command and propagated to subcommands via Click context.

### 5.1 Default vs JSON

Default: `rich.table.Table` for lists, key/value rendering for single-item detail.

With `--json`: `json.dumps(payload, default=str, indent=2)`. Every command has a stable JSON schema. For commands that return arrays, `--json` emits a JSON array (NOT ndjson). For `--follow`, `--json` emits ndjson (one JSON object per line).

### 5.2 Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Internal / unexpected error |
| 2 | User error (bad args, not found) |
| 3 | Coordinator unreachable (connection refused, DNS failure, timeout) |
| 4 | Operation failed (server returned 5xx, or operation rejected by the business logic) |

### 5.3 Destructive commands

`delete`, `uninstall` require `--yes`. Without it: `fail(2, "Refusing to delete <thing> without --yes")`. No interactive `Are you sure?` prompts.

### 5.4 Error rendering

On HTTP error responses:
- Default: print `error: <detail from response body>` to stderr, exit with appropriate code.
- `--json`: emit `{"ok": false, "error": "<detail>", "code": "<error_code_if_known>", "status_code": <http_status>}` to stdout. Exit code still carries the same meaning.

For connection refused: stderr `error: coordinator unreachable at <url>`, exit 3. With `--json`: `{"ok": false, "error": "coordinator unreachable at <url>", "code": "COORD_UNREACHABLE"}`.

---

## 6. Follow / Tail

Three `--follow` commands; two backed by websockets, one file-based.

### 6.1 Websocket-backed (`deployment activity`, `data scraper-logs`)

Shared helper at `sdk/cli/follow.py`:

```python
async def follow_target(target: str, *, severity: str, kind: str, json_mode: bool) -> int:
    """Open ws to /ws/dashboard, subscribe to target, render events to stdout."""
```

Behavior:

1. Optionally fetch the last 50 rows via REST (`GET /api/deployments/:id/activity?limit=50`) and print them, so the user sees recent context. Skip via `--no-history`.
2. Open `ws://<coord>/ws/dashboard`. Send `{type: "subscribe", target: "deployment:<id>"}`.
3. For each incoming `activity_event` or `algo_log`, render a line. Default format:
   ```
   [HH:MM:SS] <severity-color>info</>  trade_executed     d-abc12345  BUY 10 AAPL @ 175.32
   ```
   `--json`: ndjson, one object per line.
4. On Ctrl+C: send `{type: "unsubscribe", target}`, close ws, exit 0.
5. On ws disconnect: print stderr warning `connection lost — reconnecting`, retry with exponential backoff (1s → 2s → 4s → ... capped at 30s). If reconnects fail for 5 minutes straight: exit 3.

### 6.2 File-based (`coord logs`)

Stdlib `tail -f`-equivalent:
1. Seek to end (or `-n N` lines from end).
2. Loop: read new bytes (poll every 200ms).
3. On `stat()` showing a different inode (log rotation): reopen.
4. Ctrl+C exits 0.

---

## 7. `init` and `doctor`

### 7.1 `quilt init`

Non-interactive. Steps:

1. Create `~/.quilt/`, `~/.quilt/run/`, `~/.quilt/log/`.
2. Write `~/.quilt/config.yaml`:
   ```yaml
   coordinator_url: http://localhost:8000
   data_dir: ./data
   db_url: sqlite+aiosqlite:///data/quilt_trader.db
   ```
3. Run `alembic upgrade head` against `db_url`.
4. Print "Quilt initialized. Next steps: `quilt worker add --name <name>`, `quilt algorithm install <repo-or-path>`, `quilt up`."

Flags: `--coord-url`, `--data-dir`, `--db-url`, `--force` (overwrite existing config), `--skip-migrate` (skip Alembic for cases where the DB is already populated), `--interactive` (Q&A wizard; defaults to non-interactive).

### 7.2 `quilt doctor`

Runs a battery of checks. Each check produces `{name, status: PASS|WARN|FAIL, message}`. Default output is a `rich.table.Table` with color-coded statuses; `--json` outputs `{ok: bool, checks: [...]}`.

Exit code: 0 if all checks PASS, 1 if any WARN-only, 2 if any FAIL.

| Check | What it verifies |
|---|---|
| `config_exists` | `~/.quilt/config.yaml` is present and parseable |
| `data_dir` | `data_dir` exists and is writable |
| `db_reachable` | DB connection opens; `Base.metadata`'s tables all exist |
| `db_migrations` | `alembic current` equals `alembic heads` |
| `coord_reachable` | `GET /api/health` returns 200 within 2s (WARN if not running, FAIL if PID file says running but unresponsive) |
| `coord_pid_match` | If `coord.pid` exists, the process is alive AND listening on the configured port |
| `dashboard_built` | `dashboard/dist/index.html` exists (WARN if not) |
| `workers_online` | Count Worker rows; print `N online, M offline` (WARN if 0 total) |
| `algorithms_installed` | Count installed algorithms (WARN if 0) |
| `live_subscriptions_running` | Every `LiveSubscription(status=running)` has an active task in `live_feed_aggregator` (requires coord — skips with WARN if unreachable) |
| `live_finalizer_running` | For every running deployment, `AlgorithmDeploymentReport.generated_at` is fresher than 5 minutes (requires coord — WARN if unreachable) |
| `settings_sanity` | Decrypt-and-verify sensitive settings (polygon_api_key, tailscale_authkey if present) |
| `disk_space` | `data_dir`'s filesystem has at least 1 GB free (WARN if not) |

The "requires coord" checks call a new endpoint `GET /api/diagnostics` that exposes the runtime status of the in-memory services. If the coord is unreachable, those checks return WARN with message `"coord not running — skipped"` instead of FAIL.

---

## 8. Shared Validation

`sdk/validation.py` exports `validate_algorithm_package(path: Path) -> list[ValidationError]` (or a structured result type — pick one shape and stick with it). Used by both `quilt validate` and `POST /api/algorithms/install`.

Checks performed (same as today's `sdk/cli/validate.py`):

1. `quilt.yaml` exists at `path`.
2. Parses as a valid `QuiltManifest` (runs all manifest validation including `trigger` regex and `history_bars`).
3. The entry-point module imports without raising.
4. The class named in the manifest exists in the module and extends `QuiltAlgorithm`.

The install endpoint currently duplicates checks 1-4 inline. As part of this work, refactor the install endpoint to call `validate_algorithm_package(...)` and reject the install if any errors are returned.

---

## 9. Install from Local Directory

The existing `POST /api/algorithms/install` accepts only GitHub URLs (`https://github.com/owner/repo`). Extend it to accept local directories:

- If the request body's `source` looks like a URL → existing GitHub clone flow.
- If the source is a local filesystem path → copy the directory to `data/packages/<derived_name>/`, set `Algorithm.commit_hash = "local:<sha256_of_dir>"` where the sha is computed from a recursive hash of the directory contents (excluding `__pycache__`, `.git`, etc.).

`quilt algorithm install ./my-algo --as my-algo-dev`:
- CLI POSTs `{"source": "/absolute/path/to/my-algo", "name_override": "my-algo-dev"}` to the install endpoint.
- `name_override` (when provided) sets the row's `name` and the on-disk package directory name. Without it, the directory's `quilt.yaml:name` is used.

`quilt algorithm update <id>`:
- For GitHub-installed: pull latest, re-run validation, update `commit_hash`.
- For local-installed: re-hash the source directory; if changed, re-copy and update `commit_hash`. Source path is stored on the Algorithm row (new column `source_path`, nullable, only set for local installs).

---

## 10. Config, Auth, and Backwards Compat

### 10.1 Config

`~/.quilt/config.yaml` v1 schema:

```yaml
coordinator_url: http://localhost:8000
data_dir: ./data
db_url: sqlite+aiosqlite:///data/quilt_trader.db
auth_token: null         # reserved; no effect in v1
```

Each command resolves `coordinator_url` via the priority chain in §2.2.

### 10.2 Auth

No auth in v1. The CLI always sends `Authorization: Bearer <token>` if `auth_token` is configured, so the wire format is forward-compatible for the day the coord gains a static-token middleware.

### 10.3 Backwards compatibility

Existing `sdk/cli/dev/backtest.py` and `sdk/cli/dev/run.py` are deleted. Anyone scripted against `quilt dev backtest` or `quilt dev run` gets a hard break. Acceptable because this is a personal/internal tool and the divergence from coord-engine results is a real bug. `quilt dev validate` continues to work as an alias for `quilt validate` for one release.

---

## 11. New Coordinator Endpoints

A small number of endpoints don't exist today and are needed by the CLI:

| Endpoint | Purpose |
|---|---|
| `GET /api/api/health` | Already exists at `coordinator/main.py:238`. Used by `coord start` poll loop. |
| `GET /api/diagnostics` | Powers `quilt doctor` checks that need runtime state: list of running tasks in `live_feed_aggregator`, finalizer last-tick timestamp per deployment, scheduler instance count, etc. |
| `POST /api/algorithms/install` | Extend to accept local paths (§9). |
| `GET /api/algorithms/{id}/source` | Returns whether the algorithm was installed from `github` or `local` plus its source path/url. Used by `quilt algorithm update` to decide which update path to take. |

---

## 12. Testing Strategy

- Unit tests for `sdk/cli/client.py` mock `httpx.AsyncClient` and verify request shapes + error mapping.
- Unit tests for `sdk/cli/output.py` verify table/JSON rendering.
- Unit tests for `sdk/cli/process.py` use `tmp_path` for the PID/log dirs and subprocess mocks for the spawn.
- Unit tests for `sdk/cli/follow.py` use a fake ws server (or mock the connect) and verify reconnect/backoff behavior.
- Integration tests for top-level commands (`quilt up`, `quilt deployment list`, etc.) spin up the test app via the existing `running_app` fixture and invoke the Click command via `click.testing.CliRunner`. Verifies the full path including the HTTP client.
- The doctor command gets its own integration test that hits the `/api/diagnostics` endpoint against a known-good test fixture.

---

## 13. Out of Scope (v1)

- Multi-coordinator profiles (`quilt config use-profile prod`).
- TLS/cert management.
- Bash/zsh completions as a managed command (Click's `_QUILT_COMPLETE` env var is documented in the README; no custom command).
- Remote dashboard hosting (Vercel/Netlify). Coord serves the built bundle.
- Plugin commands.
- Interactive shell / REPL mode.
- A `quilt watch <deployment_id>` that combines `report` + `activity --follow` into a live TUI dashboard. Maybe later; v1 just has the individual subcommands.

---

## 14. Implementation Order (suggested)

The plan will split into milestones; rough ordering:

1. **Foundation:** `sdk/cli/client.py`, `sdk/cli/output.py`, `sdk/cli/config.py` extensions, global flags wired into `main.py`. No new commands yet — just the plumbing.
2. **Validation refactor:** `sdk/validation.py` extracted, `sdk/cli/validate.py` rewired, install endpoint refactored to call it.
3. **Process lifecycle:** `sdk/cli/process.py`, `quilt coord {start,stop,restart,status,logs}`, `quilt up`, `quilt down`. Coordinator gains `StaticFiles` mount for `dashboard/dist/`.
4. **`init` + `doctor`:** `quilt init`, `quilt doctor`, the new `/api/diagnostics` endpoint, `quilt db {migrate,status}`.
5. **Algorithm + worker + account + settings:** wraps existing endpoints; install endpoint extended for local paths.
6. **Deployment + backtest + data:** wraps existing endpoints. `quilt deployment activity --follow` uses the follow helper.
7. **Follow infrastructure:** `sdk/cli/follow.py`, wire `--follow` into `deployment activity`, `coord logs`, `data scraper-logs`.
8. **Cleanup:** delete `sdk/cli/dev/`, `sdk/cli/backtest.py`, `sdk/cli/run.py`. Update README with new command reference.
