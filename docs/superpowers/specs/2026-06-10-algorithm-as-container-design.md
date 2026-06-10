# Algorithm-as-Container UX Restructure

**Status:** Design
**Date:** 2026-06-10
**Owner:** dashboard
**Supersedes (partially):** [2026-05-30-research-lab-dashboard-design.md](2026-05-30-research-lab-dashboard-design.md) — repositions Phase 3/4/5 surfaces inside the per-algorithm shell.

## Problem

The dashboard sidebar has three top-level entries — Algorithms, Backtests, Research — that present strategy work as three unrelated lanes. In the user's mental model, an algorithm is the unit of strategy work; its backtests, research sessions, and live deployments are *children* of that algorithm.

Today's separation forces context switching: to evaluate `crypto-tsmom`, the user opens the algorithm page for deployments and parameter sets, the Backtests page filtered by `algorithm_id` for historical runs, and the Research page filtered by `algorithm_id` for sweeps and walk-forwards. Cross-references between these surfaces are link-only, not structural.

The data model already supports an algorithm-centric grouping:

- `BacktestRun.algorithm_id` (FK, required)
- `OptimizationSession.algorithm_id` (FK, recently added in migration `07acd8b`)
- `AlgorithmInstance.algorithm_id` (FK, required)

The seam is purely a frontend organization problem.

## Goal

Make the algorithm the only top-level entry point for all strategy work, with backtests, research sessions, and deployments living as nested sub-routes under the algorithm. Each algorithm gets a hub page that surfaces the most recent activity across all three child types, plus dedicated sub-pages for the full lists.

## Non-goals

- **No new Research Lab dashboard features.** Phase 3/4/5 of the prior research-lab spec (walk-forward submission modal, sweep results matrix, OOS equity chart, in-browser report viewer) are out of scope here. They get unblocked by this restructure and reappear as follow-up specs.
- **No data model changes.** All FKs already exist. No migrations.
- **No change to the Overview page** beyond updating any internal links that pointed at `/backtests` or `/research`.
- **No change to algorithm install / package management.**
- **No change to live trading / worker behavior.**

## Information architecture

### Sidebar

Today (8 entries):

```
Overview · Accounts · Data · Workers · Algorithms · Backtests · Research · Settings
```

After (6 entries):

```
Overview · Accounts · Data · Workers · Algorithms · Settings
```

`Layout.tsx` removes the `Backtests` and `Research` items. Algorithms becomes the only path to backtest runs, research sessions, and live deployments.

### URL structure

```
/algorithms                                    list — card grid
/algorithms/:id                                hub — single-page dashboard
/algorithms/:id/backtests                      full backtest list (filterable)
/algorithms/:id/backtests/:run_id              single-run report (was /backtest-runs/:id)
/algorithms/:id/backtests/compare?runs=...     multi-run comparison (was /backtests/:id)
/algorithms/:id/research                       research sessions list
/algorithms/:id/research/:session_id           session detail (was /research/sessions/:id)
/algorithms/:id/deployments                    instances list
/algorithms/:id/deployments/:instance_id       live instance detail (was /deployments/:id)
/algorithms/:id/config                         manifest + parameter sets + repo info
```

### Redirects

Old URLs continue to work via server-side or router redirects. The redirect resolver fetches the entity, reads its `algorithm_id`, and 302s to the new nested URL:

| Old URL                    | Resolves to                                          |
| -------------------------- | ---------------------------------------------------- |
| `/backtests`               | `/algorithms` (gateway redirect)                     |
| `/backtests/:id`           | `/algorithms/:algo_id/backtests/compare?runs=:id`    |
| `/backtest-runs/:id`       | `/algorithms/:algo_id/backtests/:id`                 |
| `/research`                | `/algorithms`                                        |
| `/research/sessions/:id`   | `/algorithms/:algo_id/research/:id`                  |
| `/deployments/:id`         | `/algorithms/:algo_id/deployments/:id`               |

If the underlying entity is deleted, the redirect 404s — same behavior as today.

## Pages

### `/algorithms` — card grid

2–3 column responsive grid. Each card represents one algorithm:

```
┌─────────────────────────────────────────┐
│ crypto-tsmom                ● live      │
│ v1.4.2                      Sharpe 1.4  │
│ ▁▂▃▅▆▇▆▅▄▃▂▃▄▅▆ (60d equity sparkline) │
│ 3 deployments · 47 backtests · 2 research │
│ Last run 2h ago                         │
└─────────────────────────────────────────┘
```

Card fields:

- **Status badge**: rolled up from deployments. `● live` (green) if any live deployment, else `● paper` (orange) if any paper, else `● idle` (gray).
- **Headline KPI**: trailing-30d Sharpe from the most-recent live deployment if one exists, else from the most recent completed backtest. Label clarifies which source ("Sharpe 1.4 · live" vs. "Sharpe 1.4 · last backtest").
- **Sparkline**: 60-day equity curve, same source priority as headline KPI. ~60 points; rendered as a compact inline SVG.
- **Counts**: deployments, backtests, research sessions for this algorithm.
- **Last run**: most recent activity timestamp across backtests, sweeps, walk-forwards, or live ticks.

Page header:

- Search bar (filters cards by name, fuzzy).
- "New algorithm" button (reuses existing install-from-URL flow, unchanged).
- Sort dropdown: Last activity (default) · Name · Sharpe · # backtests.

Empty state (no algorithms installed): replace grid with a centered "Install your first algorithm" CTA.

### `/algorithms/:id` — hub page

Single page, scrolling top to bottom. Five regions:

**1. Header band**

```
crypto-tsmom · v1.4.2                  [● live]  [Run Backtest ▾]  [New Sweep]
3 deployments · 47 backtests · 2 research sessions
```

- "Run Backtest ▾" splits: "Quick backtest" (current ad-hoc modal) and "From parameter set".
- "New Sweep" opens `NewSweepModal` pre-bound to this algorithm; the modal's algorithm picker is hidden.

**2. KPI row** — four cards horizontally:

- Live PnL (today / 30d / YTD picker).
- Sharpe — live deployment if any, else last-backtest. Source labeled.
- Max drawdown — same source as Sharpe.
- Open positions across deployments.

If no live deployments exist, Live PnL is replaced with "Last backtest Sharpe / CAGR / DD" until a deployment goes live.

**3. Recent Backtests preview**

Compact table, last 5 rows: run name, status, Sharpe, CAGR, completed_at. Each row links to `/algorithms/:id/backtests/:run_id`. "View all 47 →" link to the full list.

**4. Active Research preview**

Cards for currently running and recently completed sessions. Each shows session name, kind (sweep / walk-forward), progress bar (if running) or summary metric (if complete). "View all →" link to the sessions list.

**5. Live Deployments preview**

Compact table: account, status, today's PnL, # positions. "View all →" link to the deployments list. If zero deployments, replace table with a CTA: "Deploy this algorithm to start trading".

**6. Parameter Sets** — collapsed-by-default expander listing saved parameter sets with a "Use in backtest" action per row.

**7. Manifest + repo info** — collapsed expander. Repo URL, git SHA, manifest YAML preview, "Open in GitHub" link, Reinstall / Uninstall actions.

### `/algorithms/:id/backtests` — full list

The existing `BacktestsPage` (or its equivalent), repackaged inside `<AlgorithmShell>` and pre-filtered to `algorithm_id=:id`.

- Filters: status, date range, completed-only, has-trades, cost_profile, parameter set used, tag.
- Columns: name, status, Sharpe, CAGR, max DD, # trades, completed_at, optionally a "from research session" badge.
- Multi-select rows → "Compare" button → `/algorithms/:id/backtests/compare?runs=...` rendering the existing comparison page.

### `/algorithms/:id/backtests/:run_id` — single run report

Existing `BacktestRunDetail.tsx`, rendered inside `<AlgorithmShell>`. Adds the algorithm header band on top and a "← Back to crypto-tsmom" breadcrumb.

### `/algorithms/:id/research` — sessions list

Existing `ResearchSessionsPage` content, repackaged inside `<AlgorithmShell>` and pre-filtered to `algorithm_id=:id`.

- "New Session" button at the top, pre-bound to this algorithm.
- Columns: name, hypothesis, kind, status, # backtests, created_at.
- Status filter: All / Running / Completed / Pre-registered.

### `/algorithms/:id/research/:session_id` — session detail

Existing `ResearchSessionDetail.tsx`, rendered inside `<AlgorithmShell>`. This is where the deferred Research Lab Phase 3/4/5 features land (walk-forward modal, sweep results matrix, stitched OOS equity chart, in-browser report viewer) when those follow-up specs ship.

### `/algorithms/:id/deployments` — instances list

A list of `AlgorithmInstance` rows. Today this is shown only as a section on `AlgorithmDetail`; here it gets its own page with filterable columns: account, status (live / paper / stopped), today's PnL, total PnL, # open positions, worker, created_at.

### `/algorithms/:id/deployments/:instance_id` — live instance detail

Existing `DeploymentDetail.tsx`, rendered inside `<AlgorithmShell>`.

### `/algorithms/:id/config`

Three sections, all currently surfaced on `AlgorithmDetail`:

- Manifest viewer (YAML, syntax-highlighted, read-only).
- Parameter sets table with CRUD.
- Repo info: URL, git SHA, version, branch, last install timestamp, Reinstall / Uninstall actions.

## Components

### `<AlgorithmShell>`

A layout wrapper used by every page under `/algorithms/:id/*`. Responsibilities:

1. Fetch the algorithm via `GET /api/algorithms/:id` (TanStack Query, stale time several minutes).
2. Render the algorithm header band: name, version, status badge, action buttons.
3. Render a breadcrumb / back link.
4. Render `<Outlet />` for the nested route content.

Existing detail components (`BacktestRunDetail`, `ResearchSessionDetail`, `DeploymentDetail`) are rendered inside `<Outlet />` unchanged except for stripping any algorithm-name headers they currently render (now handled by the shell).

### `<AlgorithmHubPage>` (new)

The `/algorithms/:id` page. Composes the existing modals (`RunBacktestModal`, `NewSweepModal`) plus new preview sections.

### `<AlgorithmCard>` (new)

Single card on the `/algorithms` grid. Receives summary data from the extended `GET /api/algorithms` endpoint.

### `<AlgorithmsGridPage>` (replaces existing `AlgorithmsPage`)

The card grid + search + sort + empty state.

## API changes

### `GET /api/algorithms` — extend with rollup fields

Each algorithm in the list response gains:

```json
{
  "id": "...",
  "name": "crypto-tsmom",
  "version": "1.4.2",
  // existing fields...
  "summary": {
    "status": "live",                              // "live" | "paper" | "idle"
    "status_source": "deployment_abc123",          // which instance set this
    "headline_sharpe": 1.42,
    "headline_sharpe_source": "live_30d",          // "live_30d" | "last_backtest"
    "equity_sparkline": [100.0, 100.4, 100.2, ...],   // ~60 points
    "equity_sparkline_source": "live" | "backtest",
    "counts": {
      "deployments": 3,
      "backtests": 47,
      "research_sessions": 2
    },
    "last_activity_at": "2026-06-10T13:42:00Z"
  }
}
```

Implementation: a new internal `AlgorithmSummaryService` aggregates from the existing `AlgorithmInstance`, `BacktestRun`, `OptimizationSession` tables. Cached per-algorithm with a short TTL (60s) since the summary is read on every list page load.

Returning the summary inline (always) keeps the API simple; the payload increase per algorithm is small (~200 bytes plus the sparkline ≈ 500 bytes). If list size becomes a concern in future, add `?include=summary` to gate.

### `GET /api/algorithms/:id` — extend with same rollup

The hub page consumes this. Same shape as the list-item summary, plus the existing detail fields.

### New convenience endpoints — none required

The hub's preview sections fetch using existing endpoints with query parameters:

- `GET /api/backtest-runs?algorithm_id=:id&limit=5&order=-completed_at`
- `GET /api/research/sessions?algorithm_id=:id&limit=5&status=running,completed_recent`
- `GET /api/algorithms/:id/instances` (already exists)

If `GET /api/research/sessions` doesn't already support `algorithm_id` filtering and `status` multi-value, add those.

## Migration plan

### Phase 1 — Routing + redirects, no UI change

- Add nested routes in `App.tsx` for `/algorithms/:id/{backtests,research,deployments,config}` and their detail sub-routes.
- Implement `<AlgorithmShell>` and mount it as the parent route.
- Add redirect handlers for old URLs: `/backtests`, `/backtests/:id`, `/backtest-runs/:id`, `/research`, `/research/sessions/:id`, `/deployments/:id`.
- Old pages still reachable via the redirects; sidebar still has 8 entries; no visual change yet.

### Phase 2 — Sidebar shrink + list page rewrites

- Remove "Backtests" and "Research" entries from `Layout.tsx`.
- Rewrite `/algorithms` as the card grid (`<AlgorithmsGridPage>`).
- Rewrite `/algorithms/:id` as the hub (`<AlgorithmHubPage>`).

### Phase 3 — API summary endpoint

- Implement `AlgorithmSummaryService`.
- Extend `GET /api/algorithms` and `GET /api/algorithms/:id` with the `summary` field.
- Cache layer (60s TTL).

### Phase 4 — Backlog reconciliation

Update `docs/superpowers/backlog.md`: mark the 9 Research Lab dashboard items as "shifted to per-algo location" with pointers to their new home under `/algorithms/:id/research` and `/algorithms/:id/research/:session_id`. The items themselves remain open.

## Risks

- **Extra fetch on every nested page.** `<AlgorithmShell>` fetches algorithm metadata before rendering the child. Mitigated by caching algo metadata in TanStack Query with a stale time of several minutes — the metadata barely changes — and by pre-fetching when navigating from the list page.
- **Redirect resolver requires an entity lookup.** `/backtest-runs/:id` → `/algorithms/:algo_id/backtests/:id` needs the run's `algorithm_id`. The router can't compute this client-side without a fetch. Acceptable: the redirect handler is a small server-side endpoint (`GET /api/redirects/backtest-runs/:id` → `307` with the new location) or a client-side handler that fetches `/api/backtest-runs/:id` and replaces the URL on response. Either adds ~50–200ms to the first navigation from a legacy bookmark.
- **Summary cache staleness on the list page.** A user starts a backtest then returns to `/algorithms` — counts and "last activity" may be 60s stale. Acceptable; the algorithm hub page is the authoritative view for fresh state.
- **`<AlgorithmShell>` and the hub page both fetch the algorithm.** Deduplicated by the TanStack Query cache key `["algorithm", id]`.

## Testing

- E2E test: navigating to a legacy URL (`/backtest-runs/:id`) ends on the new nested URL with the same content visible.
- E2E test: clicking through `/algorithms` → card → hub → "view all backtests" → single run → breadcrumb back; the algorithm context (header band) is visible at every level after the card.
- E2E test: sidebar has no Backtests or Research entries.
- Component test: `<AlgorithmCard>` renders the right status badge for each combination of live/paper/idle deployments + backtest history.
- API test: `GET /api/algorithms` returns the `summary` field; counts match the underlying tables.
- API test: redirect for legacy URLs returns `307` with the correct new location.

## Open questions

- Should the legacy `/backtests/:id` (multi-run comparison) URL redirect work when the runs span multiple algorithms? Today the comparison page accepts heterogeneous runs. Proposal: if all runs share an `algorithm_id`, redirect to `/algorithms/:id/backtests/compare?runs=...`; otherwise redirect to a top-level `/compare?runs=...` page that lives outside the algorithm shell. Deferred decision — handle when the comparison page itself is touched.
- Should the empty state on `/algorithms` show a curated list of recommended algorithms to install? Out of scope for this spec; logged as a future enhancement.
