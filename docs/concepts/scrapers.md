# Scrapers

> Scrapers turn external data sources (web pages, third-party APIs, CSVs you find on the internet) into typed columns your algorithms can subscribe to.

## What you'll learn

- The `QuiltScraper` SDK contract — three methods, one DataFrame.
- How scraper output reaches the data layer and how algorithms read it via `ctx.data()`.
- How the coordinator schedules scrapers from manifest cron expressions.
- What happens when a scraper's login expires, and how to sign it in again from the dashboard.
- How to package and install a scraper.

## The problem this solves

Algo trading lives or dies on the data you pull in beyond price bars: analyst picks, social sentiment, supply chain signals, fundamentals. Most frameworks make you bolt these in ad-hoc — a cron job that drops a CSV in a known location, a fragile parser, no contract with the trading engine, no observability when the scrape silently fails, no story for what "the data as of this tick" actually means.

Quilt gives custom data a first-class API. A scraper is a Python class that subclasses `QuiltScraper` and returns a `pandas.DataFrame` from `on_run()`. The coordinator owns the lifecycle: it discovers installed scrapers on startup, runs them on the cron schedule declared in their manifest, persists the result to a known location under `data/custom/`, records every attempt in SQLite, and exposes the result to algorithms via the same `ctx.data("my-scraper")` call regardless of whether they're running live or in a backtest. The CSV at `data/custom/<name>.csv` is the single source of truth for that scraper's current state.

## How Quilt does it

### The SDK contract

`sdk/scraper.py` is 17 lines, and that is the entire surface a scraper author has to implement:

```python
from __future__ import annotations

import pandas as pd


class QuiltScraper:
    """Base class that all data scrapers must implement."""

    def on_start(self, config: dict) -> None:
        pass

    def on_run(self) -> pd.DataFrame:
        raise NotImplementedError

    def on_stop(self) -> None:
        pass
```

The lifecycle is straight-line, not event-driven:

1. `on_start(config)` — called once per invocation. `config` is the merged dict of manifest defaults plus any per-instance overrides loaded from `data/scraper_configs/<name>.json`. Stash anything `on_run` will need on `self`.
2. `on_run()` — does the actual work and returns a `pd.DataFrame`. The engine takes the DataFrame and writes it to disk as CSV; the column names of the returned frame become the column names downstream consumers see.
3. `on_stop()` — called once after `on_run` returns. Use it to close handles, log out of sessions, clean up temp files.

There is no async, no retry hook, no streaming output. If `on_run` raises, the engine captures the exception text as the failure reason; the previous CSV (if any) is left untouched.

### Execution model

`ScraperEngine.run_scraper` in `coordinator/services/scraper_engine.py:34` invokes each scraper in its own subprocess. The engine launches the scraper package's venv-local Python interpreter (or falls back to the coordinator's interpreter if no venv is present), executes an inline runner that loads the manifest's `entry_point` + `class_name`, instantiates the class, calls `on_start` → `on_run` → `df.to_csv(...)` → `on_stop`, and waits for exit. Subprocess isolation means a scraper that segfaults Playwright, leaks file descriptors, or imports an incompatible numpy build cannot take the coordinator with it.

Output goes to `data/custom/<name>.csv` (`scraper_engine.py:31-32`). The path layout is fixed; algorithms look up custom data by scraper name only.

**Atomicity caveat.** The current engine writes the CSV in place via `df.to_csv(out_path, index=False)` (`scraper_engine.py:55`) — not via a temp-file-plus-rename. A concurrent reader can in principle see a partial file. This is a known gap, not a design choice; the fix (write to `<name>.csv.tmp`, then `os.replace`) is small and contributions are welcome. In practice the cron cadence is coarse and tick reads cluster on the same UTC clock, so collisions are rare today.

### The manifest

A scraper's `quilt.yaml` uses the same schema as an algorithm's, with `type: scraper` and two scraper-specific fields. Here is the alpha-picks manifest, used as the template (`packages/alpha-picks-scraper/quilt.yaml`):

```yaml
name: alpha-picks-scraper
type: scraper
version: 1.0.0
description: Scrapes Seeking Alpha's "Alpha Picks" current portfolio.
entry_point: scraper.py
class_name: AlphaPicksScraper
schedule: "0 14 * * 1-5"
jitter_seconds: 3600
config:
  parameters:
    - name: profile_dir
      type: string
      default: /var/lib/quilt/alpha-picks-profile
    - name: headless
      type: bool
      default: true
```

Required fields:

| Field           | Purpose                                                                 |
|-----------------|-------------------------------------------------------------------------|
| `name`          | Both the on-disk package directory and the key algorithms use in `ctx.data()`. |
| `type`          | Must be `scraper`. The discovery walk skips anything else.              |
| `entry_point`   | Path (relative to package root) to the Python module containing the scraper class. |
| `class_name`    | Class name to instantiate inside `entry_point`.                        |
| `schedule`      | POSIX cron expression (5 fields). Interpreted in UTC.                  |
| `jitter_seconds`| Optional integer. Randomizes the actual fire time by up to N seconds.  |

The `config.parameters` block declares the keys passed to `on_start`. Per-instance overrides go in `data/scraper_configs/<name>.json` and are merged on top of manifest defaults at run time.

### How algorithms read scraper output

Inside `on_tick`, an algorithm calls `ctx.data("alpha-picks-scraper")` and gets back the current CSV as a `pandas.DataFrame`. In live mode the worker pre-fetches custom data sources before each tick (`worker/context.py:188`); in backtests the backtest context (`coordinator/services/backtest_tick_context.py:282`) does the same lookup.

Freshness is "as of the last successful scrape." There is no point-in-time history in the framework — the CSV at `data/custom/<name>.csv` is the only version that exists, and it gets overwritten on every successful run. Algorithms that need to detect changes (e.g. "Alpha Picks added TSLA today") diff successive frames themselves, typically by stashing the last-seen frame on `self` in `on_tick`:

```python
def on_tick(self, ctx):
    picks = ctx.data("alpha-picks-scraper")
    prev = getattr(self, "_last_picks", None)
    if prev is not None:
        added = set(picks["symbol"]) - set(prev["symbol"])
        # ... act on `added`
    self._last_picks = picks
```

### Scheduling

The coordinator owns scraper scheduling end-to-end. `ScraperRegistry.discover_and_register()` (`coordinator/services/scraper_registry.py:68`) runs at coordinator startup (`coordinator/main.py:116`), walks `packages/`, parses every `quilt.yaml` with `type: scraper`, and registers a cron job per scraper via `SchedulerService.add_cron_job` (`coordinator/services/scheduler.py:41`). The scheduler is APScheduler's `AsyncIOScheduler`, pinned to UTC.

A few details worth knowing:

- **Cron syntax is POSIX.** Day-of-week 0 means Sunday in your manifest, not Monday. `SchedulerService._convert_dow` (`scheduler.py:22`) maps it to APScheduler's 0=Monday convention so you can write the familiar form.
- **Catch-up on startup.** If today's base cron time has already passed when the coordinator starts and there has been no successful run since, the registry fires a catch-up run immediately. Catch-up is bounded by `MAX_ATTEMPTS_PER_DAY = 3` (`scraper_registry.py:29`) so a chronically failing scraper can't burn the upstream API on every restart.
- **Persistence.** Every attempt is recorded in the `scrapers` SQLite table — `last_attempt_at`, `last_success`, `last_error`, `attempts_today`. This is what the `quilt data scrapers` CLI and the dashboard read.
- **Manual trigger.** `POST /api/scrapers/<name>/run` runs a scraper immediately; `quilt data scraper-run <name>` is the CLI wrapper (`sdk/cli/commands/data.py:193`).
- **Overlap.** The scheduler registers jobs with `coalesce=True` and APScheduler's default `max_instances=1` (`scheduler.py:58-61`), so if `on_run` is still executing when the next cron tick fires, the new run is blocked and any further missed firings collapse into a single catch-up run (bounded by the 600s `misfire_grace_time`). You won't get two copies of the same scraper racing on the CSV.
- **Per-run timeout.** There is no engine-level timeout today. A scraper that hangs on a network call will hold its slot until the coordinator restarts. Set your own HTTP client timeouts inside `on_run`.

### Auth errors and re-login

Scrapers that read a site through a signed-in browser profile (alpha-picks is the reference) stop working when the site expires the session or its bot check stops trusting the profile. Quilt notices this, stops hammering the site, and lets you sign in again from the dashboard. It never sees, stores or types a password: you sign in yourself, in the real page.

**Declaring it.** A scraper opts in with an `auth:` block in `quilt.yaml`, validated by `sdk.scraper_auth.parse_auth` (so `quilt validate` and `POST /api/scrapers` reject a bad block; discovery logs a warning and registers the scraper without auth):

```yaml
auth:
  kind: browser_profile              # the only kind today
  engine: patchright                 # module providing <engine>.async_api; default playwright
  profile_dir_param: profile_dir     # the config parameter holding the Chromium user-data-dir
  login_url: https://seekingalpha.com/alpha-picks/picks/current
  verify:                            # at least one of url / selector
    url: https://seekingalpha.com/alpha-picks/picks/current
    selector: ["table[data-test-id='alpha-picks-table']"]
    timeout_s: 30                    # for "Check now"; default 30
  session_timeout_s: 1200            # longest login session; default 1200, 60..3600
```

Launch options (user agent, viewport, args) stay in code: override `QuiltScraper.browser_launch_options()` and use the same dict in your own fetch code, because the site trusts a session only for the fingerprint that created it. The login browser runs headed exactly when the scrape does (`headless` resolves false).

**Detecting it.** Raise `sdk.scraper.AuthRequired` for a login wall or expired session, and `sdk.scraper.BotBlocked` for an anti-bot block page (subclass them, as alpha-picks does with `AuthExpiredError` and `BotBlockedError`). The runner (`sdk/scraper_runner.py`) reports these as `error_kind` `auth_required` / `bot_blocked`. Any other exception stays an ordinary failure, so a package that hasn't adopted the typed errors behaves exactly as before and is never paused.

**Back-off.** On a typed error the registry marks the scraper `needs_login`. The state lives in the `scrapers` table (`auth_state`, `auth_reason`, `auth_message`, `auth_changed_at`), so it survives restarts. Each transition writes one `Event` (`scraper_needs_login`, warning; `scraper_login_restored`, info) and broadcasts `scraper_auth_changed` to dashboards. While `needs_login`:

- scheduled cron runs and startup catch-up are skipped: no process, no attempt counted (`error_kind` `paused`);
- **Run now** still runs, since it's an explicit human action;
- scheduling resumes after a verified login, or after any successful run.

`GET /api/scrapers` shows it as `auth_state`, `auth_reason`, `auth_message`, `schedule_paused` and `login_session`.

**One browser per profile.** Every process that opens the profile takes `fcntl.flock` on `<profile>/.quilt-profile.lock` (`sdk.scraper_auth.profile_lock`): scrapes as role `scrape`, dashboard logins as `login`, the terminal fallback as `login-local`. A second contender gets a readable refusal naming the holder (`profile_busy`), instead of a second Chromium dying on the same user-data-dir. The lock goes away with the process, so it can't go stale.

**Re-login from the dashboard.** The Re-login button calls `POST /api/scrapers/<name>/login`. `LoginSessionManager` (`coordinator/services/scraper_login.py`) starts the login helper (`sdk/scraper_login.py`) in the scraper's venv, in its own process group. The helper opens the profile at `login_url` on the coordinator host's display, so on WSLg a normal Chromium window appears too, and streams the page to the dashboard as a CDP screencast. Mouse, keyboard, paste and tab switches go back over the same websocket. It finishes by itself:

1. Every 2 s the helper checks, without navigating, whether the page matches `verify` (same scheme, host and path as `verify.url`, and a visible `verify.selector`). **Check now** navigates to `verify.url` and waits up to `verify.timeout_s`.
2. Once verified, it waits 2 s for late cookies, closes the browser so Chromium flushes the profile, and exits.
3. The coordinator marks the scraper ok, then runs **one** confirmation scrape (`trigger="login_confirm"`). The session ends `succeeded` ("Signed in and scraped N rows") or `confirm_failed` with the scrape's error. If that scrape still hits a login wall, the scraper is back in `needs_login`.

Closing the browser window yourself counts as "done", as the old `setup_profile.py` habit did: the session goes to `browser_closed` and runs the same single confirmation scrape, without marking the scraper ok first. Nothing depends on noticing the close, though: if the close event never arrives, **Check now**, **Cancel** and the session timeout still end the session.

Session states: `starting → waiting_for_user ⇄ checking → verified → confirming → succeeded | confirm_failed`, plus `browser_closed` (goes on to `confirming`), `cancelled`, `timed_out` and `failed`. Each change is broadcast to dashboards as `scraper_login_state`.

| Route | Result |
|---|---|
| `POST /api/scrapers/<name>/login` | 201 new session `{id, state, message, started_at, expires_at, can_check}`; 200 with the session already open. 404 unknown scraper; 422 no `browser_profile` auth block; 409 a scrape is running, or another process holds the profile (`detail.holder` has pid, role, started_at); 429 two sessions already open; 503 a headed scraper with no X display |
| `GET /api/scrapers/<name>/login` | the active session, or one that ended in the last 10 minutes; else 404 |
| `POST /api/scrapers/<name>/login/check` | 202; asks the helper for an active check |
| `POST /api/scrapers/<name>/login/cancel` | 202; starts the stop sequence, the session ends `cancelled` |
| `WS /ws/scrapers/<name>/login?session=<id>` | the viewer: `session`, `frame`, `pages`, `ended` out; validated input in; 4404 for an unknown or ended session, 1000 after `ended` |

While a session is open, every run of that scraper except the confirmation scrape returns `login session in progress`; a login can't start while a scrape runs.

Guard rails around the helper:

- **Watchdog.** No heartbeat for 30 s kills the helper (`failed`); passing `session_timeout_s` cancels it (`timed_out`).
- **Stop sequence.** Send `cancel` and wait 10 s; SIGTERM the helper's process group, wait 5 s; SIGKILL the group. Playwright starts Chromium in a group of its own, so after every helper exit the manager also scans `/proc` for processes with `--user-data-dir=<profile>` and SIGKILLs any still there 5 s later.
- **Coordinator shutdown** runs the stop sequence for every session with shorter waits. If the coordinator dies outright, the helper sees EOF on stdin and closes the browser itself; the auth state is persisted, so nothing is lost.
- **Viewers.** Several devices may watch and drive one session. Each viewer keeps only the latest frame, so a slow one skips frames instead of queueing them. Leaving the page doesn't end the session; Re-login reattaches.
- **Privacy.** Viewer input is validated (unknown commands, out-of-range numbers and text over 256 characters are dropped) and never logged. Frames and the helper's stderr stay in memory and aren't logged either. The coordinator API has no authentication yet, so the 128-bit session id in the websocket URL and the session timeout are what limit access to the live page.

**Terminal fallback.** When the coordinator is down, `python scripts/scraper_login.py <name>` on the coordinator host runs the same helper in local mode: a headed window, status lines in the terminal, Enter to check, `q` + Enter to cancel. It takes the same profile lock. Afterwards, `quilt data scraper-run <name>` confirms the login and clears the dashboard banner.

### Packaging and installation

A scraper is a separate Python package living under `packages/<name>/` with its own venv. The expected layout:

```
packages/alpha-picks-scraper/
  quilt.yaml          # manifest
  scraper.py          # entry_point — defines the QuiltScraper subclass
  requirements.txt    # pip-installed into the package's venv
  .venv/              # package-local virtualenv (created at install time)
```

Two install paths exist today:

1. **Manual clone.** `git clone <repo> packages/<name>`, then create the venv and install requirements as the package's README documents. The coordinator picks it up on next restart via `discover_and_register`.
2. **HTTP install.** `POST /api/scrapers` with `{ "repo_url": "<git url>" }` clones the repo into `packages/`, creates the venv, installs `requirements.txt`, validates the manifest, and registers the scraper without a coordinator restart (`scraper_registry.py:228`).

Note that `quilt algorithm install` is algorithm-only — the algorithm install endpoint validates `type == "algorithm"` and rejects scraper manifests (`coordinator/api/routes/algorithms.py:724`). There is no `quilt scraper install` CLI today; use the manual clone path or `curl` the HTTP endpoint above. A symmetric CLI command is a reasonable contribution — the HTTP endpoint already does the real work.

To list installed scrapers: `quilt data scrapers` (`sdk/cli/commands/data.py:175`).

## Worked example: alpha-picks-scraper

The alpha-picks scraper (`packages/alpha-picks-scraper/`) is the reference implementation. What it does:

- Fires weekdays at 14:00 UTC with up to 60 minutes of jitter so the fire time looks human, not robotic.
- Uses Playwright with a persistent Chromium user-data-dir (`profile_dir`) that has been pre-logged-in to Seeking Alpha. This is the auth model — a real browser profile, not API keys.
- Fetches the "Alpha Picks current portfolio" page, parses the picks table, and returns a 7-column DataFrame: `symbol`, `company`, `date_picked`, `return_pct`, `sector`, `rating`, `holding_pct`.
- The engine writes the result to `data/custom/alpha-picks-scraper.csv`.

A consuming algorithm calls `ctx.data("alpha-picks-scraper")` in `on_tick` and gets the current portfolio. It can then build a target-weights vector, compare against `ctx.positions`, and emit rebalance signals.

Setup details — how to pre-log-in the Chromium profile and what each `AuthExpiredError` / `ParseError` failure mode means — live in [`../../packages/alpha-picks-scraper/README.md`](../../packages/alpha-picks-scraper/README.md). When the session expires, sign in again with Re-login on the Data page (see "Auth errors and re-login" above).

## Limits & sharp edges

- **Output is full-overwrite; no history snapshots.** The framework keeps exactly one version of each scraper's CSV — the most recent successful run. If you need point-in-time queries, snapshot it yourself (a daily `cp` into a dated subdirectory works, or pipe the DataFrame into the bitemporal datasets framework instead).
- **CSV writes are in-place, not atomic.** See the atomicity caveat under "Execution model." For tick-frequency scrapers, write to a temp path inside `on_run` and `os.replace` onto the final filename before returning.
- **One scraper, one CSV.** The output filename is derived from the scraper's `name` field. Multi-output scrapers must split into multiple scraper packages, each with its own manifest and schedule.
- **Cookie-based scrapers still need a person to sign in again when sessions expire.** A scraper that raises `AuthRequired` / `BotBlocked` is paused (`needs_login`) and shown in a dashboard banner; you sign in yourself with Re-login, which opens the profile's browser on the coordinator host and streams it to the dashboard (or `scripts/scraper_login.py <name>` in a terminal). Nothing is automatic: Quilt stores no credentials, solves no captchas, and does not retry a paused scraper on a timer, so scheduled data stays stale until you act (Run now still works). A package that hasn't adopted the typed errors fails as an ordinary error and is never paused. The login view sits on the same unauthenticated API as everything else; only the per-session id and the session timeout guard it.
- **Playwright ships a ~150MB Chromium per venv.** Each scraper package gets its own venv, so each Playwright-based scraper costs another ~150MB of disk. Worth knowing if you plan to run a dozen of them on a coordinator with thin storage.
- **Catch-up is bounded at 3 attempts per UTC day.** A scraper that fails three times in a day will stop being retried until the next UTC midnight, even if you bounce the coordinator.

## See also

- [`writing-algorithms.md`](writing-algorithms.md) — how `ctx.data()` fits into the tick context an algorithm consumes.
- [`data-collection.md`](data-collection.md) — where `data/custom/` sits in the broader data layer.
- [`../../packages/alpha-picks-scraper/README.md`](../../packages/alpha-picks-scraper/README.md) — full setup walk-through for the reference scraper.
