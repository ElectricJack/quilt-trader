# README and Onboarding Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the README as a slim pitch (Approach 2: problem → quilt's answer) and produce a coherent set of onboarding + concept docs under `docs/onboarding/` and `docs/concepts/`. Audit and update the two existing `docs/notes/` files. Verify every claim against current code before writing.

**Architecture:** A single root `README.md` becomes the entry point and pitch. `docs/onboarding/getting-started.md` is the first-30-minutes walkthrough. `docs/concepts/` holds seven per-subsystem deep-dive docs that the README links into. All cross-links use relative paths. No fabricated CLI flags, file paths, or code references — every claim is verified against the code first.

**Tech Stack:** Markdown only. ASCII diagrams. No build step, no generated content. Verification commands: `rg`, `Read`, `quilt --help`.

**Spec reference:** `docs/superpowers/specs/2026-06-08-readme-and-onboarding-design.md`

---

## Conventions used throughout this plan

Each task creates or modifies one file. Steps inside each task follow this pattern:

1. **Verify** — read source files, run CLI `--help`, grep for symbols. Capture what is actually true.
2. **Draft** — write the doc using only verified facts.
3. **Link-check** — `rg` for every relative link, every `path:line` reference; confirm each target exists.
4. **Commit** — one commit per task.

Each task is self-contained. A subagent picking up Task 7 should be able to do it with only the task's content + the spec.

The `commit message` format is `docs(<scope>): <subject>`. All commits include the trailer:
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## Task 1: Audit `docs/notes/wsl-tailscale-setup.md`

**Files:**
- Read: `docs/notes/wsl-tailscale-setup.md`
- Read (for verification): `README.md` (current WSL2 reference at line 84), `scripts/install-worker.sh` (if exists), `coordinator/main.py` (port bindings)
- Modify (if drifted): `docs/notes/wsl-tailscale-setup.md`

- [ ] **Step 1: Read the existing note end-to-end**

Run: `Read docs/notes/wsl-tailscale-setup.md`
Capture the claims it makes (commands, ports, IPs, WSL versions, distro assumptions).

- [ ] **Step 2: Verify each command runs as documented**

For each shell command in the note:
- Confirm the executable still exists with the documented flags (`tailscale up --help`, `netsh interface --help`, etc.).
- Confirm port numbers match what the coordinator binds (`grep -rn "8000" coordinator/main.py coordinator/api/`).
- Confirm any referenced systemd unit names match installer reality.

Capture findings in a working note.

- [ ] **Step 3: Update the doc if drifted**

If any command, port, or path has changed: edit inline, preserving voice. If nothing has changed: leave the file untouched and note "no changes" in the commit message body.

- [ ] **Step 4: Link-check**

Run: `rg "docs/notes/wsl-tailscale-setup" .` — confirm any inbound links still resolve. The current `README.md:84` references it; that line will be removed in Task 12 (README rewrite) but the file itself remains linked from new concept docs.

- [ ] **Step 5: Commit**

```bash
git add docs/notes/wsl-tailscale-setup.md
git commit -m "$(cat <<'EOF'
docs(notes): audit WSL2 + Tailscale setup against current code

<one-sentence summary of what changed, or "no drift found">.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If no edits were made, skip the commit and note "no drift" instead.

---

## Task 2: Audit `docs/notes/polygon-endpoints.md`

**Files:**
- Read: `docs/notes/polygon-endpoints.md`
- Read (for verification): `coordinator/services/data_providers/polygon.py`
- Modify (if drifted): `docs/notes/polygon-endpoints.md`

- [ ] **Step 1: Read the existing note end-to-end**

Capture every Polygon endpoint URL, query parameter, and response shape it documents.

- [ ] **Step 2: Verify against current provider implementation**

Run: `Read coordinator/services/data_providers/polygon.py` in full.

Compare each endpoint and parameter the doc references against what the provider actually calls. Note any drift (renamed endpoints, deprecated params, new params we now require).

- [ ] **Step 3: Update the doc if drifted**

Edit inline. Preserve voice.

- [ ] **Step 4: Commit**

```bash
git add docs/notes/polygon-endpoints.md
git commit -m "$(cat <<'EOF'
docs(notes): audit Polygon endpoints against current provider code

<one-sentence summary>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Skip if no drift.

---

## Task 3: Audit `packages/alpha-picks-scraper/README.md` scheduling caveat

**Files:**
- Read: `packages/alpha-picks-scraper/README.md`
- Read (for verification): `coordinator/services/scraper_engine.py`, `coordinator/services/scraper_manager.py`, `coordinator/services/scheduler.py`
- Modify (if drifted): `packages/alpha-picks-scraper/README.md`

The current `packages/alpha-picks-scraper/README.md` says: "Quilt's coordinator does not automatically schedule scrapers; trigger manually via the verification script in your Quilt repo (`scripts/run_scraper_once.py`) until scheduler wiring lands." This claim needs to be re-verified — scheduler wiring may have landed.

- [ ] **Step 1: Determine current scraper scheduling state**

Run: `rg -n "cron|schedule" coordinator/services/scraper_engine.py coordinator/services/scraper_manager.py coordinator/services/scheduler.py`

If scheduled scraper execution is now wired, capture how (manifest field, CLI command, scheduler entry).

- [ ] **Step 2: Update the README's "Schedule" section if drifted**

If scrapers are now scheduled automatically: update wording, add the `schedule:` manifest field semantics, and replace the "until scheduler wiring lands" caveat with what's actually true.

If still manual: leave as-is.

- [ ] **Step 3: Commit**

```bash
git add packages/alpha-picks-scraper/README.md
git commit -m "$(cat <<'EOF'
docs(alpha-picks): update scheduling caveat to match current state

<one-sentence summary>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Skip if no drift.

---

## Task 4: Write `docs/concepts/architecture.md`

**Files:**
- Create: `docs/concepts/architecture.md`
- Read (for verification): `coordinator/main.py`, `coordinator/api/`, `worker/main.py`, `worker/agent.py`, `dashboard/src/App.tsx`
- Read (for spec reference): `docs/superpowers/specs/2026-05-12-quilt-trader-design.md` (sections 2.1–2.3)

- [ ] **Step 1: Create the docs/concepts directory**

```bash
mkdir -p docs/concepts
```

- [ ] **Step 2: Verify component responsibilities against current code**

For each component named in the doc (coordinator, worker, dashboard, scheduler, scraper engine, market data store, Discord bot):
- Confirm the file/module that owns it exists.
- Confirm it does what the doc will claim it does.

Specifically:
- Coordinator owns SQLite, serves dashboard, runs scheduler, owns data — verify in `coordinator/main.py`.
- Workers hold broker credentials in memory only — verify in `worker/agent.py` (credentials passed on `start_algorithm`).
- WebSocket is the worker↔coordinator channel — verify in `worker/agent.py`.

- [ ] **Step 3: Write the doc following the spec §5 template**

Required sections, in order:

1. **Heading + 1-line framing.** "Quilt-trader is a hub-and-spoke distributed system: a single coordinator orchestrates many stateless workers over Tailscale."

2. **What you'll learn** — 4 bullets:
   - The split between coordinator and workers, and why it's drawn there.
   - How components communicate (REST, WebSocket, Tailscale).
   - Where each piece of state lives.
   - Why hub-and-spoke instead of peer-to-peer.

3. **The problem this solves** (~200 words). Most algo frameworks assume one machine: your laptop is the broker, the data store, the scheduler, and the execution host. That works until your laptop sleeps. Or until you want to run six strategies and the GIL blocks them. Or until you want to put live execution on a low-cost always-on box (Pi) without giving it your data archive. Quilt splits these roles.

4. **How Quilt does it.** Subsections:
   - **Coordinator** — central control plane. List responsibilities, reference `coordinator/main.py` and `coordinator/services/` directories.
   - **Workers** — stateless execution hosts. List responsibilities, reference `worker/main.py:<line>`, `worker/agent.py:<line>`, `worker/tick_loop.py:<line>`. Mention broker credentials live only in worker memory.
   - **Dashboard** — React frontend served by the coordinator. Reference `dashboard/src/App.tsx`.
   - **Discord bot** — optional notification + remote management. Reference `coordinator/services/discord_bot.py`.
   - **Communication channels** — REST for data fetches, WebSocket for control-plane events, Tailscale for transport security. Reference the design spec's §2.3 message types.

5. **Worked example.** A single algorithm running through the full system: coordinator stores config → tells worker to start → worker fetches data from coordinator REST API → worker calls broker for fills → worker streams events back over WebSocket → coordinator writes to SQLite + dashboard pushes to browser. ASCII sequence diagram.

6. **Why hub-and-spoke (not peer-to-peer or cloud).** Three short paragraphs:
   - Single source of truth simplifies state recovery after power loss.
   - Workers can be cheap/disposable (Pi, old laptop) because they hold no durable state.
   - Avoids the operational cost of running a distributed database.

7. **Limits & sharp edges.**
   - Coordinator is a SPOF — if it's down, no algorithms run. Workers can stay connected but won't get data or commit trades that require approval.
   - State persistence is SQLite — not designed for >1 coordinator instance.
   - No multi-user auth.

8. **See also.**
   - `distributed-execution.md` — how the WebSocket protocol actually works
   - `data-collection.md` — what the coordinator's data layer looks like
   - `../superpowers/specs/2026-05-12-quilt-trader-design.md` — original design

- [ ] **Step 4: Link-check**

For every relative link in the doc:
```bash
rg -n "\[.*\]\(\..*\)" docs/concepts/architecture.md
```
For each match, confirm the target file exists (`ls <target>`).

For every `path:line` reference in the doc, confirm:
```bash
sed -n "<line>p" <path>
```
shows what the doc claims.

- [ ] **Step 5: Commit**

```bash
git add docs/concepts/architecture.md
git commit -m "$(cat <<'EOF'
docs(concepts): add architecture deep dive

Hub-and-spoke topology, component responsibilities, communication
channels, and the trade-offs of the coordinator-as-SPOF design.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Write `docs/concepts/writing-algorithms.md`

**Files:**
- Create: `docs/concepts/writing-algorithms.md`
- Read (for verification): `sdk/algorithm.py`, `sdk/context.py`, `sdk/signals.py`, `sdk/manifest.py`, `sdk/models.py`
- Read (for examples): `packages/alpha-picks-scraper/` (for manifest format reference)

- [ ] **Step 1: Verify SDK surface**

Run:
```
Read sdk/algorithm.py
Read sdk/context.py
Read sdk/signals.py
Read sdk/manifest.py
```

Capture:
- The `QuiltAlgorithm` base class methods (`on_start`, `on_tick`, `on_stop`, `save_state`, `on_signal_rejected`, `on_trade_executed`, `on_position_closed`, `notify`).
- The `TickContext` interface (`ctx.positions`, `ctx.cash`, `ctx.account_value`, `ctx.market_data`, `ctx.data`, `ctx.market_time`, `ctx.is_market_open`).
- `Signal`, `SignalLeg`, `SignalType`, `OrderType` enumerations.
- Manifest fields (`name`, `type`, `version`, `entry_point`, `class_name`, `trigger`, `requirements`, `market_timezone`).
- Supported trigger formats — confirm `interval:60s`, `bar:1min`, `event` are real.

- [ ] **Step 2: Write the doc following the spec §5 template**

Required sections:

1. **Heading + 1-line framing.** "A Quilt algorithm is a Python class that decides what to trade on each tick. The framework handles data, execution, state, and the boring parts."

2. **What you'll learn.** 5 bullets — lifecycle, triggers, context API, signals, state persistence.

3. **The problem this solves.** ~200 words. Naive algo frameworks make you re-invent: data plumbing, broker adapters, state recovery, scheduling. Quilt's algorithm SDK is a tiny surface (3 required methods, a context object, a signal type) and a manifest. Everything else is the framework's job.

4. **How Quilt does it.** Subsections:
   - **The manifest** (`quilt.yaml`) — show real fields, point at `sdk/manifest.py:<line>`. Include the `market_timezone` field and its smart default per asset_types (referenced in commit `a1392c5`).
   - **The class** — `QuiltAlgorithm` lifecycle. For each method (`on_start`, `on_tick`, `on_stop`, `save_state`, `on_signal_rejected`, `on_trade_executed`, `on_position_closed`), one paragraph: when it's called, what to put there, gotchas. Reference `sdk/algorithm.py:<line>`.
   - **The tick context** — table of `ctx.*` attributes/methods with one-line descriptions. Reference `sdk/context.py:<line>`. Include `ctx.market_time` and `ctx.is_market_open` helpers (commit `0807a3a`).
   - **Triggers** — table: `interval:60s`, `bar:1min`, `event`. What each fires on. When to use which.
   - **Signals** — `Signal` + `SignalLeg` + `OrderType`. Show a small example signal for "buy 100 AAPL at market." Reference `sdk/signals.py:<line>`.
   - **State persistence** — the `save_state()` → restart → `on_start(restored_state=...)` flow. Why this matters (power loss, deployment updates).

5. **Worked example.** A complete ~40-line momentum algorithm in one code block. EMA cross. Real imports, real signal construction, real config keys. Comment the code lightly.

6. **Limits & sharp edges.**
   - `on_tick` runs in the worker subprocess; long-running work blocks the next tick.
   - No async support — algorithms are synchronous.
   - `ctx.market_data` returns recent bars only; for historical research use the backtest engine.

7. **See also.**
   - `data-collection.md` — what `ctx.market_data` and `ctx.data` are pulling from
   - `backtest-accuracy.md` — running the algorithm against history
   - `cli-and-agentic-workflows.md` — installing, deploying, and iterating from the CLI

- [ ] **Step 3: Link-check** (same pattern as Task 4 step 4)

- [ ] **Step 4: Commit**

```bash
git add docs/concepts/writing-algorithms.md
git commit -m "$(cat <<'EOF'
docs(concepts): add algorithm authoring guide

QuiltAlgorithm lifecycle, triggers, context API, signals, manifest,
and a worked EMA-cross example.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Write `docs/concepts/data-collection.md`

**Files:**
- Create: `docs/concepts/data-collection.md`
- Read (for verification): `coordinator/services/data_providers/`, `coordinator/services/data_service.py`, `coordinator/services/download_manager.py`, `coordinator/services/download_job.py`, `coordinator/services/coverage_index.py`, `coordinator/services/datasets/`, `sdk/cli/commands/data.py`

- [ ] **Step 1: Verify provider list and capabilities**

Run:
```
ls coordinator/services/data_providers/
Read coordinator/services/data_providers/polygon.py     (head 60 lines, look for free-tier note)
Read coordinator/services/data_providers/tradier.py     (history depth)
Read coordinator/services/data_providers/alpaca.py
Read coordinator/services/data_providers/theta.py
Read coordinator/services/data_providers/yfinance_provider.py
```

Capture each provider's: asset coverage (equities, options, crypto, index), history depth, paid vs free tier, live streaming support.

- [ ] **Step 2: Verify storage layout**

Confirm `data/market/{provider}/{symbol}/{timeframe}.parquet` is current. Run:
```
ls data/market/
```
and adapt the doc to what's actually on disk.

- [ ] **Step 3: Verify CLI data commands**

Run: `Read sdk/cli/commands/data.py`. List the subcommands and their flags. Confirm `subscribe`, `download`, `scrapers` exist and that the README's flag examples match (`--retention-hours`, `--symbol`, `--start`, `--end`).

- [ ] **Step 4: Verify datasets framework**

Run: `Read coordinator/services/datasets/__init__.py` and `coordinator/services/datasets/registry.py`. Capture what a dataset is, the registry shape, and what providers it currently supports (`fmp` is in the tree).

- [ ] **Step 5: Write the doc following the spec §5 template**

Required sections:

1. **Heading + 1-line framing.** "Quilt manages market data and custom datasets so your algorithms have one source of truth for both live trading and backtests."

2. **What you'll learn.** Storage layout, provider differences, subscriptions vs downloads, coverage tracking, the datasets framework, custom data via scrapers.

3. **The problem this solves.** ~250 words. Free data APIs are incomplete (truncated history, missing premarket, no options). Paid APIs let you pull data only at runtime — you can't backtest five years ago against the data the API would have returned to you. Quilt persists everything it touches into a local Parquet store so backtests use the same data your live algo will use. Coverage indexing means you don't re-download what you already have.

4. **How Quilt does it.** Subsections:
   - **Storage layout.** ASCII tree of `data/market/{provider}/{symbol}/{timeframe}.parquet`. Why Parquet (compressed, queryable, language-agnostic). Mention `data/custom/` for scraper output and `data/datasets/` for the datasets framework.
   - **Provider comparison.** Markdown table: provider | asset classes | history depth | live stream | paid?
   - **Subscriptions vs downloads.** Subscriptions are live streams that accumulate into Parquet over time. Downloads pull historical ranges all at once. Both write into the same store.
   - **Coverage index.** Reference `coordinator/services/coverage_index.py`. Explain why it exists (avoid re-downloading what's already on disk).
   - **The datasets framework.** Beyond per-symbol price bars, you can register a "dataset" (e.g., FMP fundamentals). Reference `coordinator/services/datasets/registry.py`. Briefly describe the FMP provider as the current example.
   - **Custom data via scrapers.** Brief note: see `scrapers.md`.

5. **Worked example.** Three CLI commands the reader can run end-to-end:
   - `quilt data subscribe alpaca AAPL --retention-hours 720` — start streaming.
   - Wait a minute, then `ls data/market/alpaca/AAPL/`.
   - `quilt data download --symbol AAPL --start 2024-01-01 --end 2024-12-31` — backfill history.

6. **Limits & sharp edges.**
   - Polygon free-tier history is 2 years; older data needs a paid plan.
   - Tradier history requires a brokerage account (free).
   - Alpaca historical requires a paid data plan.
   - No automatic data backup — `data/` is on the coordinator host's disk.
   - Storage grows linearly with subscriptions; archival not auto-managed.

7. **See also.**
   - `backtest-accuracy.md` — how stored data feeds the backtest engine
   - `scrapers.md` — adding custom non-price data
   - `../notes/polygon-endpoints.md` — Polygon endpoint quirks

- [ ] **Step 6: Link-check** (same pattern as Task 4)

- [ ] **Step 7: Commit**

```bash
git add docs/concepts/data-collection.md
git commit -m "$(cat <<'EOF'
docs(concepts): add data collection deep dive

Providers, Parquet storage layout, subscriptions vs downloads,
coverage indexing, and the datasets framework.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Write `docs/concepts/backtest-accuracy.md`

**Files:**
- Create: `docs/concepts/backtest-accuracy.md`
- Read (for verification): `coordinator/services/backtest_engine.py`, `coordinator/services/backtest_engine_v2.py`, `coordinator/services/options_mtm.py`, `coordinator/services/backtest_runner.py`, `coordinator/services/options_math.py`, `coordinator/database/models.py` (search for `mtm_realism`)
- Read (for spec reference): `docs/superpowers/specs/2026-06-04-equity-curve-mtm-design.md`

- [ ] **Step 1: Verify `mtm_realism` modes**

Run: `rg -n "mtm_realism" coordinator/services/options_mtm.py coordinator/services/backtest_engine.py coordinator/services/backtest_engine_v2.py`

Enumerate every mode value (e.g., `intrinsic`, `mid`, `envelope`, `black_scholes`, or whatever the implementation actually uses). Capture how each is computed.

- [ ] **Step 2: Verify three-tier IV resolution and envelope logic**

Run: `Read coordinator/services/options_mtm.py` end-to-end.

Capture:
- The three tiers of IV resolution (cache → vendor → constant fallback).
- The direction-aware envelope with alpha lerp (commit `3fe9298`).
- The Black-Scholes pricing helper (commit `c0b1978`).

- [ ] **Step 3: Verify backtest CLI surface**

Run: `Read sdk/cli/commands/backtest.py` and `sdk/cli/commands/research.py`. Capture: `backtest run` flags (`--algo`, `--start`, `--end`, `--wait`, `--mtm-realism`?), `research session create` flags, parameter sweep / walk-forward command names.

- [ ] **Step 4: Write the doc following the spec §5 template**

Required sections:

1. **Heading + 1-line framing.** "Quilt's backtest engine prices options the way a market maker would have, not the way mid-price math wishes they would."

2. **What you'll learn.** Why naive backtests lie about options. How Quilt prices options. The `mtm_realism` knob. Parameter sweeps and walk-forward.

3. **The problem this solves.** ~300 words. A naive options backtester marks open positions to mid-price at each bar. Real fills happen at the bid (selling) or ask (buying), and spreads on illiquid options can be 10–30% wide. Backtests show smooth equity curves; live runs blow them up on entry alone. Worse, IV moves between bars and old-strike options get stale quotes — naive engines often use the *last seen quote* indefinitely, which inflates marks during volatility spikes. Quilt addresses both: a layered pricing model (Black-Scholes when IV is fresh, intrinsic value as the floor, a direction-aware envelope to bound the realism) and a configurable `mtm_realism` mode so you can pick how aggressive the price-discovery model is.

4. **How Quilt does it.** Subsections:
   - **The pricing pipeline.** ASCII flow: candidate price → IV resolution → BS price → envelope clamp → intrinsic floor → final mark. Reference `coordinator/services/options_mtm.py:<line>` for each stage.
   - **Three-tier IV resolution.** (a) recent observed IV cache, (b) vendor IV from data feed, (c) constant-sigma fallback. When each kicks in.
   - **The direction-aware envelope.** What "alpha lerp" means in plain English: smooth interpolation between optimistic (mid) and conservative (worst-of-bid/ask) marks. Why direction matters: long positions get marked toward the conservative side; short positions toward the optimistic side. (Or whatever the code actually does — verify before describing.)
   - **The `mtm_realism` knob.** Table: mode | what it does | when to use it. Default mode and recommended starting point.
   - **Equities vs options.** Equities use a simpler mid-or-trade model — only options need the envelope.
   - **Sweeps, walk-forward, and parameter sets.** Brief: `quilt backtest run` for a single config; `quilt research session create` for sweeping configs across symbols; walk-forward via the research API. Reference `sdk/cli/commands/research.py:<line>`.

5. **Worked example.** Two backtest runs of the same algorithm side by side: one with `--mtm-realism naive` (or whatever the lax mode is), one with `--mtm-realism strict`. Show the diff in final equity curve / Sharpe / max drawdown that this produces *in principle* (don't fabricate numbers — say "expect Sharpe to drop by X-Y on average for options-heavy strategies; the divergence is the whole point of running both").

6. **Limits & sharp edges.**
   - Constant-sigma fallback is a coarse approximation; expect drift in long-history backtests.
   - American options are priced with European Black-Scholes; the early-exercise premium is ignored. Acceptable for index options, less so for equity options with high dividends.
   - Slippage on equities is currently modeled as a fixed bps — not adaptive to volatility.
   - Walk-forward currently re-trains parameters at fixed cadence; no in-period adaptation.

7. **See also.**
   - `writing-algorithms.md` — what an algorithm looks like that produces signals
   - `data-collection.md` — where the engine's price history comes from
   - `../superpowers/specs/2026-06-04-equity-curve-mtm-design.md` — design rationale

- [ ] **Step 5: Link-check** (same pattern)

- [ ] **Step 6: Commit**

```bash
git add docs/concepts/backtest-accuracy.md
git commit -m "$(cat <<'EOF'
docs(concepts): add backtest accuracy deep dive

Options MTM pipeline, three-tier IV resolution, direction-aware
envelope, mtm_realism modes, and the parameter-sweep / walk-forward
research surface.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Write `docs/concepts/scrapers.md`

**Files:**
- Create: `docs/concepts/scrapers.md`
- Read (for verification): `sdk/scraper.py`, `coordinator/services/scraper_engine.py`, `coordinator/services/scraper_manager.py`, `packages/alpha-picks-scraper/quilt.yaml`, `packages/alpha-picks-scraper/scraper.py` (just the class signature)
- Read (for cross-link target): `packages/alpha-picks-scraper/README.md`

- [ ] **Step 1: Verify the scraper SDK surface**

Run: `Read sdk/scraper.py`.

Confirm `QuiltScraper` base class has `on_start`, `on_run`, `on_stop`. Confirm `on_run` returns a `pd.DataFrame`. Confirm the docstring matches what we'll claim.

- [ ] **Step 2: Verify the scraper engine flow**

Run: `Read coordinator/services/scraper_engine.py` and `coordinator/services/scraper_manager.py`.

Capture:
- How a scraper is invoked (subprocess? in-process?).
- Where the output CSV is written (`data/custom/<name>.csv`).
- Whether writes are atomic (temp file + rename).
- Whether scheduling is wired (cross-reference Task 3 audit).

- [ ] **Step 3: Verify manifest format**

Run: `Read packages/alpha-picks-scraper/quilt.yaml`.

Confirm: `type: scraper`, `entry_point`, `class_name`, `schedule`, `requirements`.

- [ ] **Step 4: Write the doc following the spec §5 template**

Required sections:

1. **Heading + 1-line framing.** "Scrapers turn external data sources (web pages, third-party APIs, CSVs you find on the internet) into typed columns your algorithms can subscribe to."

2. **What you'll learn.** The scraper SDK contract. The atomic CSV swap. How scraped data reaches algorithms via `ctx.data()`. How to package and schedule one.

3. **The problem this solves.** ~200 words. Algo trading lives or dies on the data you pull in beyond price bars: analyst picks, social sentiment, supply chain signals, fundamentals. Most frameworks make you bolt these in ad-hoc — a cron job that drops a CSV in a known location, a fragile parser, no contract. Quilt gives this a first-class API: a `QuiltScraper` base class, an atomic output swap, scheduling via the coordinator, and `ctx.data("my-scraper")` inside algorithms.

4. **How Quilt does it.** Subsections:
   - **The SDK contract.** `QuiltScraper` lifecycle: `on_start(config)` → `on_run()` → returns `pd.DataFrame` → engine writes to `data/custom/<name>.csv` atomically → `on_stop()`. Show the base class source (it's 17 lines — show all of it).
   - **The manifest.** Same `quilt.yaml` format as algorithms, but `type: scraper`. Show the alpha-picks manifest as the template.
   - **How algorithms read scraper output.** Inside `on_tick`, `ctx.data("alpha-picks-scraper")` returns the current CSV as a DataFrame. The freshness is "as of the last successful scrape." No history snapshots; algorithms diff successive frames if they care.
   - **Scheduling.** Reflect whatever Task 3's audit produced. If wired: how the `schedule:` cron expression works, how to trigger a one-off run. If not wired: how to invoke manually and the path to scheduling.
   - **Packaging.** Scrapers are separate Python packages installable via `quilt algorithm install <path>` (same command handles both types).

5. **Worked example.** Walk through the alpha-picks scraper at a high level — what it does, the cookies-based auth, the output schema. Link to its README for full setup.

6. **Limits & sharp edges.**
   - Output is full-overwrite; no history snapshots in the framework. Build your own snapshotter if you need point-in-time queries.
   - One scraper = one CSV; multi-output scrapers must split into multiple scrapers.
   - Cookie-based scrapers need manual re-auth when sessions expire (alpha-picks documents this).
   - Playwright-based scrapers ship a ~150MB Chromium per venv.

7. **See also.**
   - `writing-algorithms.md` — how `ctx.data()` is consumed
   - `data-collection.md` — where scraper output sits in the data layer
   - `../../packages/alpha-picks-scraper/README.md` — full worked example

- [ ] **Step 5: Link-check** (same pattern)

- [ ] **Step 6: Commit**

```bash
git add docs/concepts/scrapers.md
git commit -m "$(cat <<'EOF'
docs(concepts): add scrapers deep dive

QuiltScraper SDK contract, atomic CSV output, ctx.data() consumption,
packaging, and the alpha-picks example as a template.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Write `docs/concepts/distributed-execution.md`

**Files:**
- Create: `docs/concepts/distributed-execution.md`
- Read (for verification): `worker/agent.py`, `worker/main.py`, `worker/tick_loop.py`, `coordinator/api/` (search for WebSocket route), `sdk/cli/commands/worker.py`
- Read (for spec reference): `docs/superpowers/specs/2026-05-12-quilt-trader-design.md` §2.3

- [ ] **Step 1: Verify WebSocket message types**

Run: `rg -n "start_algorithm|stop_algorithm|heartbeat|signal_request|trade_executed|state_checkpoint|decision_log" worker/agent.py coordinator/api/`

Capture: which message types still exist, which have been renamed, which are new since the 2026-05-12 spec.

- [ ] **Step 2: Verify worker installation path**

Run: `Read sdk/cli/commands/worker.py`. Capture the `worker add`, `worker update`, `worker list` subcommands and their flags.

If a `scripts/install-worker.sh` or equivalent exists, read it to confirm the one-liner install flow.

- [ ] **Step 3: Verify Tailscale is still the assumed transport**

Run: `rg -n "tailscale" sdk/cli/ coordinator/ worker/ --type py`. Confirm Tailscale URLs / IPs are the documented connection mechanism (vs. anything more sophisticated like Lighthouse or self-signed cert TLS).

- [ ] **Step 4: Write the doc following the spec §5 template**

Required sections:

1. **Heading + 1-line framing.** "Quilt distributes algorithm execution across as many machines as you want, joined by Tailscale, controlled by one coordinator."

2. **What you'll learn.** The coordinator/worker contract. Why Tailscale. How to add and update workers. The WebSocket message types at a high level.

3. **The problem this solves.** ~200 words. Running ten algorithms on one laptop blocks the GIL, leaks credentials across strategies, and dies when the laptop sleeps. Putting them on remote VPSes means dealing with VPC, ingress firewalls, credential distribution, secure update channels. Quilt's answer is dumb: every worker is a stateless Linux box on your Tailnet. The coordinator pushes config + credentials over an authenticated mesh. Workers can be Raspberry Pis at home, a spot VM in another cloud, an old MacBook — same install one-liner.

4. **How Quilt does it.** Subsections:
   - **Why Tailscale.** Identity-baked-in, no port forwarding, encrypted by default, free for personal use. Quilt is opinionated about this — Tailnet is the trust boundary.
   - **Worker is stateless.** No DB, no algo code on disk longer than the deployment lifetime. Credentials live in worker memory only. Reboot the worker, the coordinator can re-deploy from scratch.
   - **The WebSocket protocol.** Two tables: (a) coordinator → worker messages, (b) worker → coordinator messages. Each row: message type | when it fires | payload summary. Reflect what `rg` actually found.
   - **Adding a worker.** Walk through `quilt worker add --name pi-1` → install one-liner → joins Tailnet → connects to coordinator → appears in dashboard. Reference `sdk/cli/commands/worker.py:<line>`.
   - **Updating a worker.** `quilt worker update pi-1` semantics: tarball push from coordinator; git pull if the worker is a git clone. Why this design (workers without git tooling can still update).

5. **Worked example.** A two-worker setup: one running an equities algo, one running an options algo. Walk through how the coordinator routes signals, how PDT checks happen on the coordinator before signal approval, what happens if one worker disconnects.

6. **Limits & sharp edges.**
   - Tailscale is not strictly required at the protocol level, but is the only documented transport. Self-hosted Headscale should work; not tested.
   - Workers can't talk to each other — only to the coordinator.
   - No automatic failover if a worker dies mid-deployment; the coordinator marks it stale and the user redeploys.
   - WebSocket reconnect uses exponential backoff; algorithms freeze (don't trade) during disconnects.

7. **See also.**
   - `architecture.md` — system topology
   - `cli-and-agentic-workflows.md` — driving the worker lifecycle from the CLI
   - `../notes/wsl-tailscale-setup.md` — WSL2 networking caveats

- [ ] **Step 5: Link-check** (same pattern)

- [ ] **Step 6: Commit**

```bash
git add docs/concepts/distributed-execution.md
git commit -m "$(cat <<'EOF'
docs(concepts): add distributed execution deep dive

Why Tailscale, the worker-as-stateless design, WebSocket message
types, and the add/update worker lifecycle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Write `docs/concepts/cli-and-agentic-workflows.md`

**Files:**
- Create: `docs/concepts/cli-and-agentic-workflows.md`
- Read (for verification): `sdk/cli/main.py`, `sdk/cli/output.py`, `sdk/cli/commands/*.py`, `sdk/cli/config.py`

- [ ] **Step 1: Verify the global CLI flags and exit code surface**

Run:
```
Read sdk/cli/main.py
Read sdk/cli/output.py
```

Confirm: `--json` (machine output), `--coord <url>`, `-q` (quiet) global flags. Confirm exit codes: `0` success, `1` internal error, `2` user error, `3` coordinator unreachable, `4` operation failed. These appear in the current README; verify they're real.

- [ ] **Step 2: Enumerate the command surface**

Run: `ls sdk/cli/commands/`. For each, capture: subcommand name, what it does, key flags. Don't transcribe every flag; the doc is conceptual, not a reference.

- [ ] **Step 3: Verify JSON output shape on one canonical command**

Pick `quilt algorithm list --json` or `quilt deployment list --json`. Read the relevant command file. Capture the JSON schema (top-level keys, item structure). This becomes the "agent-friendly output" example.

- [ ] **Step 4: Write the doc following the spec §5 template**

Required sections:

1. **Heading + 1-line framing.** "Every Quilt operation is a CLI command. That makes Quilt one of the few algo trading platforms an AI agent can drive end-to-end without scraping a UI."

2. **What you'll learn.** The CLI design principles. The `--json` contract. Exit codes. Recipes for building/backtesting/deploying via an agent.

3. **The problem this solves.** ~250 words. Most trading platforms are GUI-first: their CLI (if any) is a thin admin tool. That's fine when a human is in the loop, but it's a dead end for AI-driven workflows where an agent needs to: query state programmatically, take actions deterministically, parse responses without HTML scraping, distinguish "operation failed" from "tool failed" cleanly. Quilt was built CLI-first. The dashboard exists, but every state read and state change has a `quilt` command with `--json` output and a documented exit code. An agent can install a new algorithm, run a backtest sweep, examine the report, and deploy the winner to a worker — all from the same shell session.

4. **How Quilt does it.** Subsections:
   - **CLI surface map.** Markdown table grouping commands by domain (lifecycle, accounts, algorithms, deployments, workers, data, backtests, ops). Each row: command | purpose | most useful flags. Don't enumerate every flag; refer to `quilt <cmd> --help`.
   - **Machine-readable everywhere.** `--json` on every read command. Show a real example: `quilt deployment list --json` output (use the actual schema captured in Step 3).
   - **Exit codes.** The five-code system (`0/1/2/3/4`). Why agents can branch on these without parsing stderr.
   - **Stable command names.** Commitment to backwards compatibility (or, if the project is too young for that, the current renaming policy).
   - **Idempotent where it matters.** `quilt init` is safe to re-run. `quilt deployment create` errors on duplicate name. (Verify what's actually idempotent — don't claim more than is true.)

5. **Worked example: agent-driven algorithm development loop.** A narrative of an AI agent (e.g., Claude Code) handling a request like "build, backtest, and deploy a momentum strategy on TSLA." The agent's tool calls:
   ```
   quilt algorithm install ./generated-algo --as momentum-v1
   quilt backtest run --algo momentum-v1 --start 2024-01-01 --end 2024-12-31 --wait --json
   # parse JSON, evaluate metrics
   quilt deployment create --algo momentum-v1 --account "Alpaca Paper" --worker pi-1 --json
   quilt deployment start <id>
   quilt deployment activity <id> --follow
   ```
   Show what each command outputs (or what the agent needs to extract from JSON to make the next decision).

6. **Limits & sharp edges.**
   - JSON output is documented per-command but not yet schema-versioned; future flag additions are additive, but you should diff before relying on field order.
   - Streaming commands (`activity --follow`) don't emit JSON line-by-line yet; agents needing live data should poll instead.
   - No machine-readable error catalog yet; errors are exit code + stderr string.
   - Some CLI flows still require dashboard interaction (e.g., initial broker key entry for the encrypted store). Documented per-command.

7. **See also.**
   - `writing-algorithms.md` — what an agent is actually generating
   - `backtest-accuracy.md` — what the agent should look at in the report JSON
   - `distributed-execution.md` — the worker lifecycle the agent will drive

- [ ] **Step 5: Link-check** (same pattern)

- [ ] **Step 6: Commit**

```bash
git add docs/concepts/cli-and-agentic-workflows.md
git commit -m "$(cat <<'EOF'
docs(concepts): add CLI and agentic workflows deep dive

The CLI-first design, --json contract, exit codes, and a worked
example of an AI agent driving the full build → backtest → deploy
loop.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Verify localhost-worker support and create the toy algorithm

**Files:**
- Read (for verification): `worker/main.py`, `worker/config.py`, `sdk/cli/commands/worker.py`, `scripts/` (any worker install scripts)
- Create (if needed): a toy algorithm at a stable inline location (do not commit standalone — it lives inside the doc)

This is a pure verification task. Output is two answers used by Task 12.

- [ ] **Step 1: Determine if a worker can run on the coordinator host**

A "localhost worker" means: the worker process runs on the same machine as the coordinator, connecting via `localhost:8000` rather than over Tailscale.

Run:
```
Read worker/config.py
Read worker/main.py
```

Look for: hard requirement on Tailscale being up, hard requirement on the coordinator URL being non-localhost, or a documented `--local` flag.

Run: `rg -n "localhost|127.0.0.1" worker/ sdk/cli/commands/worker.py`. Capture findings.

Outcome A: localhost worker is supported and documented. Record the exact command.
Outcome B: localhost worker works but is undocumented. Record the command path that does work; flag as "supported but undocumented" for the getting-started doc.
Outcome C: localhost worker is not supported. Step 3 of getting-started will require a real second machine.

- [ ] **Step 2: Draft the toy algorithm**

Build a self-contained ~25-line momentum algorithm that:
- Subclasses `QuiltAlgorithm` from `sdk/algorithm.py`.
- Uses a single config key (`threshold`, default 0.02).
- On `on_tick`, fetches 20 bars of 1-minute data for a configured symbol (defaulting to `SPY`).
- Computes a simple short/long EMA cross.
- Emits a single-share market-order signal on cross.
- Implements `on_stop` returning an empty dict (so state persistence works without effort).

Include a matching `quilt.yaml` manifest (name `toy-momentum`, `trigger: interval:60s`, `requirements: { asset_types: [equities] }`).

This algorithm will be embedded inline in `getting-started.md` in Task 12 — don't commit it as a standalone package.

- [ ] **Step 3: Smoke-test the toy algorithm shape**

Don't run it — just confirm the imports resolve and the manifest validates. Run:
```
python -c "from sdk.algorithm import QuiltAlgorithm; from sdk.signals import Signal, SignalLeg, SignalType, OrderType; print('imports ok')"
```

If imports succeed, the algorithm code is structurally sound for inclusion. Capture the exact text of the algorithm and manifest for Task 12.

- [ ] **Step 4: No commit**

This task produces drafted content used by Task 12; nothing is committed standalone.

---

## Task 12: Write `docs/onboarding/getting-started.md`

**Files:**
- Create: `docs/onboarding/getting-started.md`
- Inputs from Task 11: localhost-worker decision (A/B/C), toy algorithm + manifest text
- Read (for verification): `pyproject.toml` (confirm `[coordinator,dev]` extras), `sdk/cli/commands/init.py`, `sdk/cli/commands/coord.py`

- [ ] **Step 1: Create the docs/onboarding directory**

```bash
mkdir -p docs/onboarding
```

- [ ] **Step 2: Verify install commands against current code**

Run:
```
Read pyproject.toml
```
Confirm `coordinator` and `dev` extras are still defined. If they've been renamed, use the current names.

Confirm `quilt init`, `quilt up`, `quilt down` are real commands by reading `sdk/cli/commands/init.py` and `sdk/cli/commands/coord.py`.

- [ ] **Step 3: Write the doc**

Structure per spec §6:

1. **Heading + outcome statement.** "By the end you'll have: coordinator running, a paper broker account connected, a worker, and one toy algorithm running."

2. **Before you start.** Python 3.11+, Node 18+, Tailscale account, Alpaca paper account (free at app.alpaca.markets). Optional: a second Linux machine or Pi.

3. **Step 1 — Install the coordinator.** Clone, `pip install -e ".[coordinator,dev]"`, `quilt init`, dashboard build (`cd dashboard && npm install && npm run build && cd ..`), `quilt up`. Note coordinator is now at `http://localhost:8000`.

4. **Step 2 — Connect a paper broker account.** Dashboard → Accounts → Add → Alpaca Paper. Where to get keys.

5. **Step 3 — Add a worker.** Based on Task 11 outcome:
   - **Outcome A or B:** Document the localhost worker path as Option A (recommended for first run). Document the Raspberry Pi one-liner as Option B. For Outcome B, add a one-line note that the localhost path is "supported but informal — see [`distributed-execution.md`](../concepts/distributed-execution.md) for the production setup."
   - **Outcome C:** Document only the Raspberry Pi path. Add a "if you don't have a second machine yet" note pointing at a future enhancement.

6. **Step 4 — Install and run the toy algorithm.** Embed the algorithm and manifest from Task 11 inline. Tell the reader to save them locally:
   ```bash
   mkdir -p ./toy-momentum
   # paste the algorithm.py and quilt.yaml below
   quilt algorithm install ./toy-momentum --as toy-momentum-v1
   quilt deployment create --algo toy-momentum-v1 --account "Alpaca Paper" --worker <name>
   quilt deployment start <deployment-id>
   ```

   Label clearly: **toy example, do not trade real money against this.**

7. **Step 5 — Watch it work.**
   ```bash
   quilt deployment activity <deployment-id> --follow
   ```
   Plus dashboard view. Then stop with `quilt deployment stop <id>`.

8. **Step 6 — Run a backtest.**
   ```bash
   quilt backtest run --algo toy-momentum-v1 --start 2024-01-01 --end 2024-12-31 --wait
   ```
   View the report in the dashboard's Backtests tab.

9. **What to read next.** Per spec §4 self-review fix, all seven concept docs grouped by intent:
   - Big picture → [`architecture.md`](../concepts/architecture.md)
   - Write algorithms → [`writing-algorithms.md`](../concepts/writing-algorithms.md)
   - Honest backtests → [`backtest-accuracy.md`](../concepts/backtest-accuracy.md)
   - Data sources → [`data-collection.md`](../concepts/data-collection.md)
   - Deploy to a fleet → [`distributed-execution.md`](../concepts/distributed-execution.md)
   - Custom scrapers → [`scrapers.md`](../concepts/scrapers.md)
   - AI agent integration → [`cli-and-agentic-workflows.md`](../concepts/cli-and-agentic-workflows.md)

10. **Troubleshooting first-run failures.** Markdown table, 5–6 entries:
    | Symptom | Likely cause | Fix |
    |---|---|---|
    | `quilt up` exits with "port 8000 in use" | Another service is on 8000 | Kill it or `quilt up --port 8081` (verify flag exists) |
    | Worker stuck on "connecting" | Tailscale not up | `tailscale status`; `tailscale up` |
    | "Broker keys rejected" | Wrong key/secret pair | Re-paste from Alpaca dashboard |
    | `npm run build` fails | Node < 18 | Upgrade Node |
    | Worker can't reach coordinator from another machine | WSL2 networking | See [`../notes/wsl-tailscale-setup.md`](../notes/wsl-tailscale-setup.md) |
    | Pip install fails with `editable install requires setuptools` | Old pip | `pip install -U pip` |

- [ ] **Step 4: Link-check**

```bash
rg -n "\[.*\]\((\.\.|/).*\)" docs/onboarding/getting-started.md
```
Confirm every relative link target exists. The concept docs were created in Tasks 4–10, so they should resolve.

- [ ] **Step 5: Commit**

```bash
git add docs/onboarding/getting-started.md
git commit -m "$(cat <<'EOF'
docs(onboarding): add first-30-minutes getting started guide

Coordinator install, paper broker connect, worker add, toy algorithm
install + deploy + backtest, with a first-run troubleshooting table.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Rewrite `README.md`

**Files:**
- Modify: `README.md` (full rewrite)
- Read (for the architecture diagram): the current `README.md` diagram (lines 7–24)

- [ ] **Step 1: Backup the current README content out-of-tree (mental note)**

Skim the existing README. Identify content that moved out (Writing Algorithms section → `writing-algorithms.md`; CLI Reference → `cli-and-agentic-workflows.md`; Project Structure → `architecture.md`; Shell Completion → keep as a footer note or drop).

- [ ] **Step 2: Write the new README per spec §4**

Eight sections in order:

**§1 Hero (~80 words).**

```markdown
# QuiltTrader

A distributed algorithmic trading framework built around honest data
and honest backtests, driven by a CLI an AI agent can use end-to-end.

- **Own your data** — every quote, bar, and scraped row lives in a
  local Parquet store you control.
- **Honest options backtests** — a layered MTM model with three-tier
  IV resolution, not a wishful mid-price.
- **CLI-first, agent-friendly** — every operation has a `quilt`
  command with `--json` output and documented exit codes.
```

**§2 What goes wrong with most algo trading setups (~150 words).**

Four short paragraphs:
- Free data is incomplete and locked behind vendor APIs you can't time-travel against.
- Backtests mark options to mid-price; live fills diverge 10–30% on real spreads.
- Most frameworks assume one machine; running multiple algos on a Pi cluster means rolling your own orchestration.
- CLIs are afterthoughts — built for humans typing, not agents iterating.

**§3 Quilt-trader's bet (~80 words).**

One paragraph stating the project's opinions: own your data, make backtests honest, CLI-first so agents can drive the loop, distribute execution as a default not a bolt-on.

**§4 Architecture diagram + caption (~60 words + ASCII).**

Re-use the existing diagram from current `README.md` lines 7–24. Update the caption to a single tight paragraph explaining the hub-and-spoke arrangement. End with: `Learn more → [docs/concepts/architecture.md](docs/concepts/architecture.md)`.

**§5 Five value-prop sections (~80 words each).**

Order: Data → Backtests → Agentic-CLI → Scrapers → Distributed.

Each follows this shape:

```markdown
### <Value Prop>

<1-sentence headline that ties back to a §2 problem>.

<2-3 sentences on how Quilt addresses it>.

<One concrete detail: a number, a name, an example>.

Learn more → [docs/concepts/<doc>.md](docs/concepts/<doc>.md)
```

For the Agentic-CLI section, the "Learn more" line should reference both docs:
```
Learn more → [cli-and-agentic-workflows.md](docs/concepts/cli-and-agentic-workflows.md). To see the surface an agent works against, also see [writing-algorithms.md](docs/concepts/writing-algorithms.md).
```

**§6 Get started (~30 words).**

```markdown
## Get started

Spin up the coordinator, connect a paper broker, run a toy algorithm —
about 30 minutes.

→ [docs/onboarding/getting-started.md](docs/onboarding/getting-started.md)
```

**§7 What it isn't (~60 words).**

```markdown
## What QuiltTrader isn't

- **Not a hosted service.** You run it on your hardware.
- **Not HFT.** Sub-second latency is not a design goal.
- **Not multi-user.** One operator, one Tailnet.
- **Not cloud-native.** It runs in a single coordinator with stateless workers, not in a Kubernetes cluster. (You can deploy components to cloud VMs if you want; nothing about the design assumes a cloud control plane.)
```

**§8 License + status (~20 words).**

```markdown
## Status

Private — not yet open source. Issues and pull requests welcome from
invited collaborators.
```

- [ ] **Step 3: Link-check**

```bash
rg -n "\[.*\]\(docs/.*\)" README.md
```
Confirm every linked file exists.

```bash
ls docs/onboarding/getting-started.md docs/concepts/architecture.md docs/concepts/data-collection.md docs/concepts/backtest-accuracy.md docs/concepts/cli-and-agentic-workflows.md docs/concepts/writing-algorithms.md docs/concepts/scrapers.md docs/concepts/distributed-execution.md
```
All must resolve.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): rewrite as slim pitch with problem -> answer arc

Lead with the two primary objectives (honest data, honest backtests)
and the agent-friendly CLI. Move deep content into docs/concepts/
and docs/onboarding/getting-started.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Final cross-link audit

**Files:**
- Read: all files created or modified in Tasks 1–13.

A single pass to confirm the doc set is internally consistent.

- [ ] **Step 1: Enumerate every relative link in the new docs**

```bash
rg -n "\[[^\]]+\]\(([^h)][^)]*)\)" README.md docs/onboarding/ docs/concepts/
```

- [ ] **Step 2: Verify each link target exists**

For each match, run `ls <target>` (relative to the linking file's directory). Note any broken links.

- [ ] **Step 3: Verify every `path:line` reference**

```bash
rg -n "[a-zA-Z_/]+\.py:[0-9]+" docs/concepts/ docs/onboarding/
```

For each match, run `sed -n "<line>p" <path>` to confirm the line still contains what the doc cites.

- [ ] **Step 4: Fix any drift inline**

For each broken link or stale line reference, update the citing doc. If many references in one doc are stale, fix them all in one commit.

- [ ] **Step 5: Verify CLI commands cited in new docs**

```bash
rg -n "quilt [a-z]+ [a-z]+" README.md docs/concepts/ docs/onboarding/
```

For each unique command, confirm it appears in `sdk/cli/commands/<file>.py`. Note any drift.

- [ ] **Step 6: Commit fixes (if any)**

```bash
git add <fixed-files>
git commit -m "$(cat <<'EOF'
docs: final cross-link and code-reference audit

<summary of what drifted, or "no drift found">.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Skip if no fixes needed.

---

## Self-Review (post-write)

**Spec coverage:**
- Spec §3 file layout — Tasks 4–13 produce every listed file. ✓
- Spec §4 README structure — Task 13. ✓
- Spec §5 concept doc template — Tasks 4–10 all follow it. ✓
- Spec §6 getting-started structure — Task 12. ✓
- Spec §7 tone / conventions — applied throughout. ✓
- Spec §8 verification work (10 items) — Tasks 1–3 (notes + alpha-picks), 6 (data + providers), 7 (mtm_realism + options pricing), 9 (WebSocket protocol), 5 + 8 (SDK + scraper), 11 (localhost worker), 14 (final audit). All 10 items covered. ✓
- Spec §9 success criteria — Task 14 closes the loop. ✓

**Placeholder scan:** No "TBD" / "TODO" / "fill in later." Every task has concrete file paths, concrete commands, concrete content outlines.

**Type consistency:** SDK names referenced consistently across tasks: `QuiltAlgorithm` (Tasks 5, 11, 12, 13), `QuiltScraper` (Tasks 3, 8), `TickContext` (Task 5), `Signal`/`SignalLeg`/`OrderType` (Tasks 5, 11), `mtm_realism` (Tasks 7, 13). CLI command names match the actual `sdk/cli/commands/` file inventory.

Plan complete.
