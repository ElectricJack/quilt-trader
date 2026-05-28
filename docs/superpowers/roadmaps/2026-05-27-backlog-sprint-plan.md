# Backlog Sprint Plan

**Date:** 2026-05-27
**Purpose:** Organize the 27 open backlog items into execution tiers — quick wins first, planning-required work later. Recommended path-of-least-resistance for clearing accumulated debt without losing focus on bigger features.

The numbered IDs match `docs/superpowers/backlog.md` cross-references for tracing.

---

## Tier 1 — Quick wins (≤ ½ day each, no spec needed)

Ship in one or two focused sessions. ~3 days cumulative.

| # | Item | Estimate | Why low-risk |
|---|---|---|---|
| 1 | Push `simple-ma-crossover` `quilt.yaml` upstream | 15 min | One file, one PR (skip if upstream repo write-access not configured) |
| 2 | Orphan backtest cleanup on coordinator startup | 1 hr | Mirror existing `recover_orphaned_downloads()` |
| 3 | Default cost profile auto-applied (`None` → `"default"`) | 2 hr | One config default change + smoke test |
| 4 | Validate `Algorithm.assets` shape at install time | 3 hr | Pydantic validator on existing field |
| 5 | Algorithm install: handle existing package dir | 3 hr | One conditional in `PackageManager.clone_repo` |
| 6 | `on_disconnect` callback wired into broker stream handles | 4 hr | Callback param + two adapter wires |
| 7 | Realistic Alpaca crypto slippage profile (YAML data) | 4 hr | New YAML + per-symbol overrides + smoke test |
| 8 | Persist trade-aggregate metrics (win_rate, PF, etc.) on `BacktestRun` | 4 hr | Read parquet in finalizer, fill columns |

**Execution discipline:** one commit per item, one test per item, no scope creep.

---

## Tier 2 — Day-scale focused (½ – 1 day, light design)

Self-contained, no architectural decisions. ~5 days cumulative.

| # | Item | Estimate |
|---|---|---|
| 9 | Multi-consumer `on_download_complete` listener registry | ½ day |
| 10 | SPA / White's Reality Check significance test | ½ day |
| 11 | Pluggable regime taggers (VIX-based + user-supplied) | ½ day |
| 12 | Bayesian / TPE search via Optuna in sweep | 1 day |
| 13 | `add_symbols` / `remove_symbols` on stream handles | 1 day |
| 14 | Paid-tier polygon concurrency setting | 1 day |

---

## Tier 3 — Multi-day, needs a short spec

Real design decisions. Each gets its own dated spec → plan → implementation. ~2 weeks each.

| # | Item | Decision points |
|---|---|---|
| 15 | Async-job model for `quilt research walk-forward` | New endpoint pattern, job-id schema, dashboard ties-in |
| 16 | Strategy-side stop-loss / circuit breaker | Algorithm vs framework feature; A/B test methodology |
| 17 | Manifest `data:` block for custom deps | Schema migration; install-time validation |
| 18 | Replace synthetic backtest clock with union-of-symbol-timelines | Two-pass execution; lazy bar discovery |
| 19 | Timezone-aware backtest engine | Manifest `timezone` field; ctx-level tz handling |
| 20 | Per-attempt scraper run history | DB schema; UI surface |
| 21 | Bulk "close all" positions action | UX, partial-failure aggregation |

---

## Tier 4 — Roadmap-level (multi-week, full design doc)

Each warrants brainstorming → spec → plan from scratch. **Do not** start these as part of clearing backlog — they're features in their own right.

| # | Item | Why big |
|---|---|---|
| 22 | **Validation Lab dashboard UI** | Multiple new pages (sessions, sweeps, walk-forward viewer, report renderer), real-time progress, parameter-space editor, deploy button |
| 23 | Live deployment automation after session passes | Trust model, paper→live cutover UX, kill-switch |
| 24 | Crypto perpetual futures venue integration | New broker adapter (Hyperliquid DEX or CME micros), funding-rate plumbing, margin model |
| 25 | Equity VRP defined-risk strategy (Phase 2 research roadmap) | Strategy spec + walk-forward over SPX options data |
| 26 | MTUM XS-momentum strategy (Phase 3 research roadmap) | Strategy spec + factor research |
| 27 | Daily/weekly option expiration data (Polygon paid tier) | ~20× download volume; cost decision |

---

## Recommended execution order

**Sprint 1 (this session, in progress):** Clear Tier 1 items 2-8. Item 1 (upstream push) deferred — depends on external repo write access. Items 7 and 8 are highest immediate value because they directly improve dashboard backtest readability after today's debugging session.

**Sprint 2 (next):** Tier 2, prioritize the validation-lab items (#10 SPA, #11 regime taggers, #12 Optuna) so the lab grows while it's still in active context.

**Sprint 3+:** Pick one Tier 3 OR one Tier 4 item. Recommend **#22 (Validation Lab dashboard UI)** as the highest-leverage Tier 4 — until it exists, the validation lab is invisible to humans and future agents.

---

## Status tracking

After each item ships, update `docs/superpowers/backlog.md`:
- Add `**RESOLVED** (YYYY-MM-DD)` under "Why deferred"
- Briefly describe what was shipped and where

Cross-reference back to this roadmap from individual specs when they get written for Tier 3 / Tier 4 items.
