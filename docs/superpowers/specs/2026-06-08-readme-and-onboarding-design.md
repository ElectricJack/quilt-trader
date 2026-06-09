# README and Onboarding Documentation — Design Specification

**Date:** 2026-06-08
**Status:** Approved
**Audience for the work:** aspirational open-source users (people who haven't heard of QuiltTrader and need to be convinced and then onboarded)

## 1. Goals

Rewrite the project README and produce a coherent set of onboarding and concept documents that:

1. Make a stranger reading the README on GitHub want to try the project in 60 seconds.
2. Lead with QuiltTrader's two primary objectives — **honest data collection** and **honest backtesting accuracy** — rather than burying them under install instructions.
3. Position the **CLI-first, agent-friendly** interface as a peer differentiator, not a footnote.
4. Cover the remaining capabilities (scrapers, distributed execution over Tailscale) as supporting evidence.
5. Provide a first-30-minutes walkthrough that gets a new user from clone to a running algorithm.
6. Provide one in-depth concept doc per major subsystem so an interested reader can dive deep without trawling source code.
7. Verify and update existing operational notes (`docs/notes/*`) that the new docs link into.

## 2. Non-goals

- Auto-generated API reference (OpenAPI dump, CLI `--help` rendering). Out of scope — concept docs link to `--help` for command-level detail.
- Per-broker setup tutorials beyond Alpaca paper.
- Per-data-provider deep dives. The data-collection concept doc compares providers in a table; no per-provider docs.
- Discord bot user guide. Mentioned in `architecture.md`; no dedicated doc.
- Dashboard user guide or screenshots.
- Contributor / development guide (test layout, CI, contributing).
- Marketing copy, hosted website, or any non-Markdown artifact.

## 3. Deliverable file layout

```
README.md                                    ← rewrite (slim pitch, ~250 lines)

docs/
├── onboarding/
│   └── getting-started.md                   ← new (first 30 minutes)
│
├── concepts/                                ← new directory
│   ├── architecture.md
│   ├── data-collection.md
│   ├── backtest-accuracy.md
│   ├── writing-algorithms.md
│   ├── scrapers.md
│   ├── distributed-execution.md
│   └── cli-and-agentic-workflows.md
│
└── notes/                                   ← existing, audit + update
    ├── wsl-tailscale-setup.md               ← verify accuracy, update if drifted
    └── polygon-endpoints.md                 ← verify accuracy, update if drifted
```

**Navigation rules**

- README's five value-prop sections each end with a `Learn more →` link to the matching concept doc.
- Each concept doc starts with a `What you'll learn` bullet list and ends with a `See also` section linking to 2–3 related concept docs and any relevant `docs/notes/*`.
- `getting-started.md` cross-links to concept docs at moments where the reader will want depth.
- All cross-links use relative paths (works in a local checkout, on GitHub, and in any Markdown viewer).

## 4. README structure (Approach 2: problem → answer)

Total target: ~250 lines, scannable in 60 seconds. Sections, in order, with rough word counts:

| § | Section | Words | Purpose |
|---|---------|-------|---------|
| 1 | Hero | ~80 | Name, one-line pitch, three-bullet "what makes it different." |
| 2 | What goes wrong with most algo trading setups | ~150 | Names concrete failure modes in free data, options backtests, single-machine assumptions, human-only CLIs. |
| 3 | Quilt-trader's bet | ~80 | The project's opinions: own your data, honest backtests, agent-first, distributed by default. |
| 4 | Architecture diagram + caption | ~60 + ASCII | The existing diagram, lightly refreshed. Sets up the rest. |
| 5 | Five value-prop sections | ~80 each | Data → Backtests → Agentic-CLI → Scrapers → Distributed. Each ties back to a §2 problem, names a concrete detail, ends with `Learn more →`. |
| 6 | Get started | ~30 | Two lines, link to `docs/onboarding/getting-started.md`. |
| 7 | What it isn't | ~60 | Honest non-goals (not HFT, not multi-user, not cloud-native, not hosted). |
| 8 | License + status | ~20 | Current status line. |

**Why this order for the five pillars:** Data and backtests come first because they're the thesis. Agentic-CLI third because it's the natural "and here's how you actually use it" pivot. Scrapers fourth because they extend the data story. Distributed execution last because it's the operational payoff.

## 5. Concept doc template

Every file in `docs/concepts/` follows the same shape so the set reads as a series, not seven different essays. Target length: 200–400 lines per doc.

```markdown
# <Topic>

> One-sentence framing of why this exists.

## What you'll learn
- 3-5 bullets, each a concrete takeaway.

## The problem this solves
~150-300 words. Names the failure mode in existing tools or naive
implementations. Ties back to a "what goes wrong" item from README §2.

## How Quilt does it
Subsections covering the actual mechanism. Mix prose, ASCII diagrams,
small real code excerpts. Reference real file paths where useful
(e.g., sdk/algorithm.py:11).

## Worked example
A small end-to-end illustration the reader can mentally run, or
literally copy-paste.

## Limits & sharp edges
Honest section: what it doesn't do, where it's still rough, known
caveats.

## See also
- 2-3 cross-links to other concept docs.
- Links to relevant `docs/notes/*`.
- Links to the matching design spec under `docs/superpowers/specs/`.
```

**Per-doc distinctive angle:**

| Doc | Angle |
|---|---|
| `architecture.md` | Why hub-and-spoke (not peer-to-peer). Component responsibilities table. The big-picture mental model. |
| `data-collection.md` | Storage layout (`data/market/{provider}/{symbol}/{timeframe}.parquet`). Provider comparison table (Polygon / Tradier / Alpaca / Theta / FMP). Subscriptions, downloads, gap-filling / coverage index, datasets framework. |
| `backtest-accuracy.md` | Lead with the **options MTM problem** — naive backtesters mark to mid; real fills diverge by 10–30%. Show `mtm_realism` modes side-by-side. Mention three-tier IV resolution, Black-Scholes layer, direction-aware envelope. Sweeps, walk-forward, parameter sets. |
| `writing-algorithms.md` | `QuiltAlgorithm` lifecycle (`on_start` / `on_tick` / `on_stop`). Triggers (`interval:60s`, `bar:1min`, `event`). `Signal` / `SignalLeg` / `OrderType`. State persistence across restarts. Market clock helpers (`market_time`, `is_market_open`). |
| `scrapers.md` | `QuiltScraper` SDK. Atomic CSV swap on each run. Output flows into algorithms via `ctx.data(source_name)`. Alpha-picks as a template. Scheduling caveats (verify and update current state). |
| `distributed-execution.md` | WebSocket protocol message types. Why Tailscale (zero-config mesh, identity baked in). Adding and updating workers. Stateless-worker design. |
| `cli-and-agentic-workflows.md` | **The agentic angle.** Stable `--json` output. Documented exit codes (0/1/2/3/4 from current README). Idempotent commands. Recipes: an agent building, backtesting, and deploying a new algorithm using only `quilt` commands. Why this matters vs GUI-driven competitors. |

## 6. `getting-started.md` structure

Linear, copy-paste-able, no decision points unless necessary. Target: ~200 lines.

| Step | Content |
|------|---------|
| Hero | "By the end you'll have: coordinator running, paper broker account, a worker, one algorithm running." |
| Before you start | Python 3.11+, Node 18+, Tailscale account, Alpaca paper account. Optional: a Pi or second machine. |
| 1. Install the coordinator | Clone, `pip install -e ".[coordinator,dev]"`, `quilt init`, dashboard build, `quilt up`. Verified against current `pyproject.toml`. |
| 2. Connect a paper broker | Dashboard → Accounts → Add → Alpaca Paper. |
| 3. Add a worker | **Option A (recommended for first run):** localhost worker. **Option B:** Raspberry Pi via the one-liner. *Whether Option A is supported must be verified during execution; if not, this step requires a real worker machine.* |
| 4. Install and run a toy algorithm | Embed a tiny (~20 line) momentum algorithm inline, clearly labeled "toy example, do not trade." `quilt algorithm install`, `quilt deployment create`, `quilt deployment start`. |
| 5. Watch it work | `quilt deployment activity <id> --follow`, dashboard view, then stop it. |
| 6. Run a backtest | `quilt backtest run --algo <name> --start 2024-01-01 --end 2024-12-31 --wait`. View the report. |
| What to read next | Cross-links to all five concept doc value props. |
| Troubleshooting first-run failures | Short table of 5–6 most common first-run errors (coordinator port in use, Tailscale not up, broker keys rejected, dashboard build failed, worker can't reach coordinator, WSL2 networking). |

## 7. Tone, style, and conventions

- **Direct, no hype.** "QuiltTrader does X" not "QuiltTrader empowers you to X." Match existing README voice.
- **Concrete over abstract.** Numbers, file paths, command names. "Polygon offers 2 years of free history" beats "extensive historical coverage."
- **Honest about limits.** Every concept doc has a `Limits & sharp edges` section. README has a `What it isn't` section.
- **No emoji** in docs.
- **Code samples are verified, not invented.** When a doc shows a CLI command or YAML manifest, it matches current code.
- **Code references use `path:line` format** (e.g., `sdk/algorithm.py:11`) so a reader can jump to source.
- **ASCII diagrams over external images.** Renders in `cat`, GitHub, and any Markdown viewer; no image assets to maintain.
- **Cross-links are relative paths**, not absolute URLs.
- **No frontmatter** on new docs.

## 8. Verification work folded into execution

The implementation plan must include explicit verification steps. None of these are optional; doc drift is the failure mode this design exists to prevent.

1. Verify `docs/notes/wsl-tailscale-setup.md` against current code; update if drifted.
2. Verify `docs/notes/polygon-endpoints.md` against current code; update if drifted.
3. Verify whether running a worker on the coordinator host (localhost worker) is supported; restructure `getting-started.md` step 3 if not.
4. Verify all CLI commands in README and `getting-started.md` against `sdk/cli/`.
5. Verify all file paths referenced in concept docs exist.
6. Verify `pyproject.toml` extras (`[coordinator,dev]`) still match.
7. Verify the scraper scheduling caveat in `packages/alpha-picks-scraper/README.md` is still accurate; update if not.
8. Verify the `mtm_realism` modes named in `backtest-accuracy.md` against the current implementation (`coordinator/services/options_mtm.py`, `coordinator/services/backtest_engine.py`).
9. Verify the WebSocket protocol message types in `distributed-execution.md` against the current `worker/agent.py` and coordinator-side handlers.
10. Verify the `QuiltAlgorithm` lifecycle, `QuiltScraper` lifecycle, trigger types, and signal types in their concept docs against `sdk/algorithm.py`, `sdk/scraper.py`, `sdk/signals.py`, `sdk/manifest.py`.

## 9. Success criteria

Done means:

1. `README.md` is rewritten and renders cleanly on GitHub.
2. `docs/onboarding/getting-started.md` exists, all commands verified.
3. All seven concept docs exist under `docs/concepts/`, each following the template, each verified against current code.
4. `docs/notes/wsl-tailscale-setup.md` and `docs/notes/polygon-endpoints.md` audited and updated if needed.
5. A fresh reader can go from the README to a running algorithm using only the documentation produced by this work — no other source needed.
6. No fabricated CLI flags, file paths, or code references anywhere in the new docs.
