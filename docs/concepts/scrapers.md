# Scrapers

> Scrapers turn external data sources (web pages, third-party APIs, CSVs you find on the internet) into typed columns your algorithms can subscribe to.

## What you'll learn

- The `QuiltScraper` SDK contract — three methods, one DataFrame.
- How scraper output reaches the data layer and how algorithms read it via `ctx.data()`.
- How the coordinator schedules scrapers from manifest cron expressions.
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

Setup details — how to pre-log-in the Chromium profile, how to re-auth when the session expires, what each `AuthExpiredError` / `ParseError` failure mode means — live in [`../../packages/alpha-picks-scraper/README.md`](../../packages/alpha-picks-scraper/README.md).

## Limits & sharp edges

- **Output is full-overwrite; no history snapshots.** The framework keeps exactly one version of each scraper's CSV — the most recent successful run. If you need point-in-time queries, snapshot it yourself (a daily `cp` into a dated subdirectory works, or pipe the DataFrame into the bitemporal datasets framework instead).
- **CSV writes are in-place, not atomic.** See the atomicity caveat under "Execution model." For tick-frequency scrapers, write to a temp path inside `on_run` and `os.replace` onto the final filename before returning.
- **One scraper, one CSV.** The output filename is derived from the scraper's `name` field. Multi-output scrapers must split into multiple scraper packages, each with its own manifest and schedule.
- **Cookie-based scrapers need manual re-auth when sessions expire.** Playwright-profile scrapers like alpha-picks fail with a recognizable error when the upstream login wall reappears; you re-log-in to the profile by hand. There is no automated credential rotation in the framework.
- **Playwright ships a ~150MB Chromium per venv.** Each scraper package gets its own venv, so each Playwright-based scraper costs another ~150MB of disk. Worth knowing if you plan to run a dozen of them on a coordinator with thin storage.
- **Catch-up is bounded at 3 attempts per UTC day.** A scraper that fails three times in a day will stop being retried until the next UTC midnight, even if you bounce the coordinator.

## See also

- [`writing-algorithms.md`](writing-algorithms.md) — how `ctx.data()` fits into the tick context an algorithm consumes.
- [`data-collection.md`](data-collection.md) — where `data/custom/` sits in the broader data layer.
- [`../../packages/alpha-picks-scraper/README.md`](../../packages/alpha-picks-scraper/README.md) — full setup walk-through for the reference scraper.
