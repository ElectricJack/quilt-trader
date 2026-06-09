# QuiltTrader

A distributed algorithmic trading framework built around honest data
and honest backtests, driven by a CLI an AI agent can use end-to-end.

- **Own your data** — every quote, bar, and scraped row lives in a
  local Parquet store you control.
- **Honest options backtests** — a layered MTM model with three-tier
  IV resolution, not a wishful mid-price.
- **CLI-first, agent-friendly** — every operation has a `quilt`
  command with `--json` output and documented exit codes.

## What goes wrong with most algo trading setups

**Data you can't trust or replay.** Free feeds are full of gaps,
late prints, and silent symbol changes. Paid feeds are locked behind
vendor APIs that won't let you go back and re-run a strategy against
the bytes the algorithm actually saw last quarter.

**Backtests that mark options to mid.** A clean fill at the midpoint
of a 30-cent-wide options spread is a fantasy. Real fills land 10-30%
worse, and a backtest that prints clean equity curves on bad
assumptions is worse than no backtest at all.

**Frameworks that assume one machine.** Running half a dozen
strategies on a Pi cluster — or splitting paper and live across hosts —
means rolling your own deployment, log shipping, and restart logic on
top of a single-process framework.

**CLIs built for humans, not agents.** An interactive REPL or a
TUI is fine for a person typing. An LLM iterating on a strategy needs
stable JSON, predictable exit codes, and streamable logs. Most tools
ship none of that.

## Quilt-trader's bet

Own your data, locally, in an open format. Make options backtests
honest by default, with the realism knob exposed rather than hidden in
defaults. Build the CLI first, so an agent can drive the full loop —
fetch data, run a backtest, read the result, iterate — without scraping
human-formatted output. Treat distributed execution as the default
shape, not a bolt-on, so adding the third Pi looks the same as adding
the first.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 Your PC / Server                 │
│                                                  │
│  ┌────────────┐  ┌───────────┐  ┌────────────┐  │
│  │Coordinator │  │ Dashboard │  │  Market     │  │
│  │  (FastAPI) │──│  (React)  │  │  Data Store │  │
│  │  port 8000 │  │  served   │  │  (Parquet)  │  │
│  └─────┬──────┘  │  at /     │  └────────────┘  │
│        │         └───────────┘                   │
└────────┼─────────────────────────────────────────┘
         │ Tailscale VPN (WebSocket)
    ┌────┴─────┐    ┌──────────┐    ┌──────────┐
    │ Worker 1 │    │ Worker 2 │    │ Worker N │
    │ (Raspi)  │    │ (Raspi)  │    │  (any)   │
    │ algo-a   │    │ algo-b   │    │ algo-c   │
    └──────────┘    └──────────┘    └──────────┘
```

Hub-and-spoke. A single coordinator on your PC owns config, data, and
the dashboard; stateless workers join over Tailscale and run one
algorithm each. The coordinator orchestrates, the workers execute, and
either side can be restarted without losing the system's mind.

Learn more → [docs/concepts/architecture.md](docs/concepts/architecture.md)

## What you get

### Own your data

Free feeds are incomplete and paid feeds are unreplayable — Quilt
stores everything you pull, locally, in an open format.

Every subscription tick and every historical download lands in the
same Parquet store under `data/market/{provider}/{symbol}/{timeframe}.parquet`.
Switch providers, query from a notebook, or rsync the whole tree to
another box — it's just files. Backtests read from this store, so what
you see is what your algorithm saw.

Five providers wired in today: Polygon, Tradier, Alpaca, ThetaData,
and yfinance.

Learn more → [docs/concepts/data-collection.md](docs/concepts/data-collection.md)

### Honest options backtests

Mid-price fills lie about how your strategy would have actually done —
Quilt's pricing pipeline is built to stop lying.

Each historical option price runs through a three-tier IV resolution
(exact quote, surface fit, model fallback) and a direction-aware
envelope that asks how the trade would have crossed the spread, not
just where the midpoint sat. The realism level is a knob, not a
default — `mtm_realism` is a float from 0.0 to 1.0 you set per
session.

Set it on `quilt research session create` and the same number flows
through every backtest in that session.

Learn more → [docs/concepts/backtest-accuracy.md](docs/concepts/backtest-accuracy.md)

### Agent-friendly CLI

Most CLIs are built for humans typing — Quilt's is built so an LLM can
drive the whole research loop.

Every command takes `--json` and emits stable, parseable output.
Exit codes are documented and consistent: `0` success, `1` internal
error, `2` user error, `3` coordinator unreachable, `4` operation
failed. Long-running commands stream NDJSON under `--follow --json`,
so an agent can tail a backtest or a deployment without parsing
human-formatted logs.

Learn more → [cli-and-agentic-workflows.md](docs/concepts/cli-and-agentic-workflows.md).
To see the surface an agent works against, also see
[writing-algorithms.md](docs/concepts/writing-algorithms.md).

### Custom data via scrapers

Markets aren't the whole picture — Quilt makes "the other data" a
first-class input alongside price.

Subclass `QuiltScraper`, register the class, and the coordinator runs
it on a POSIX cron schedule (UTC) at startup via `ScraperRegistry`.
Output goes to `data/custom/<name>.csv` with atomic-ish writes, and
algorithms read it through `ctx.data(source_name)` the same way they
read prices.

Cron strings, headers, and idempotency are yours to define — Quilt
handles the schedule and the storage.

Learn more → [docs/concepts/scrapers.md](docs/concepts/scrapers.md)

### Distributed by default

Most frameworks pretend one machine is enough — Quilt assumes you'll
want a small cluster from day one.

Workers are stateless processes that connect to the coordinator over
Tailscale with a documented WebSocket protocol. `WORKER_ID` is baked
in at install time, so a Pi can reboot, lose its disk, or be replaced
wholesale, and the coordinator picks it back up by identity.

Add a worker with `quilt worker add` and the one-liner installer
handles Tailscale enrollment, systemd, and the secret handoff.

Learn more → [docs/concepts/distributed-execution.md](docs/concepts/distributed-execution.md)

## Get started

Spin up the coordinator, connect a paper broker, run a toy algorithm —
about 30 minutes.

→ [docs/onboarding/getting-started.md](docs/onboarding/getting-started.md)

## What QuiltTrader isn't

- **Not a hosted service.** You run it on your hardware.
- **Not HFT.** Sub-second latency is not a design goal.
- **Not multi-user.** One operator, one Tailnet.
- **Not cloud-native.** Single coordinator with stateless workers.
  You can deploy components to cloud VMs if you want; nothing about
  the design assumes a cloud control plane.

## Status

Private — not yet open source. Issues and pull requests welcome from
invited collaborators.
