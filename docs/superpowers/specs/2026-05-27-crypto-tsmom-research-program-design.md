# Crypto TSMOM Research Program — Strategy Validation Lab + First Strategy

**Date:** 2026-05-27
**Status:** Approved (pending user review of written spec)
**Related research:** [2026-05-27-quant-edge-survey.md](../research/2026-05-27-quant-edge-survey.md)

## Problem

QuiltTrader has a working backtest engine, metrics, and cost model, but lacks the rigorous-validation infrastructure needed to determine whether a strategy has a real edge or a backtest artifact. Specifically missing:

- Walk-forward rolling refit (every existing run is single train+test)
- Hyperparameter sweep orchestration (parameters exist as ad-hoc `ParameterSet` rows)
- Multiple-testing correction (no awareness of "how many configs did we try")
- Bootstrap confidence intervals (point-estimate metrics only)
- Regime-conditional metrics (no regime detection)
- Per-asset/per-venue cost models (one fee config per backtest)
- Markdown/HTML research reports (JSON only)

Without these, the framework cannot distinguish real edges from p-hacked overfits. With $1000 of risk capital, deploying a noise-mining result is a meaningful waste.

This spec delivers (1) a reusable **Strategy Validation Lab** module that any future strategy uses, and (2) **crypto TSMOM on BTC/ETH** as the first strategy that consumes it. The TSMOM choice is justified in [the literature survey](../research/2026-05-27-quant-edge-survey.md) — Liu & Tsyvinski 2021 (RFS) provides the canonical evidence; the edge survives McLean & Pontiff's 58% post-publication haircut into a plausible-but-not-spectacular Sharpe.

## Goals

1. Add a reusable validation lab module that any strategy in the framework can use.
2. Deliver a first strategy (crypto TSMOM) end-to-end: ingestion → backtest → walk-forward → deployment-or-kill decision based on pre-registered criteria.
3. Produce a research report (markdown + HTML) for the TSMOM strategy that documents the methodology, results with confidence intervals, and the kill/deploy decision.

## Non-goals

- Crypto perpetual-futures broker integration (perp venues geoblock US persons; the funding-carry edge has decayed since 2024 anyway).
- Equity VRP / cross-sectional momentum strategies (Phase 2; uses the same lab).
- Live deployment automation (manual deploy after kill-criteria pass).
- Dashboard UI for `OptimizationSession` browsing (Phase 2).

## Architecture

### Two deliverables

```
┌─────────────────────────────────────────────────────────────────┐
│  Deliverable 1 — coordinator/services/validation/  (new)         │
│  Reusable infrastructure. All future strategies depend on it.    │
│                                                                  │
│  walk_forward.py    sweep.py    optimization_session.py          │
│  bootstrap.py       regime.py   multi_test.py                    │
│  cost_model.py      report.py                                    │
└──────────────────────────┬──────────────────────────────────────┘
                           │ consumed by
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Deliverable 2 — data/packages/crypto-tsmom/  (new)              │
│  First user of the lab. Proves the methodology end-to-end.       │
│                                                                  │
│  manifest.yaml      strategy.py     ingestion.py                 │
│  hyperparameters.yaml      tests/                                │
└─────────────────────────────────────────────────────────────────┘
```

## Deliverable 1: Strategy Validation Lab

New package: `coordinator/services/validation/`. Each module has a focused responsibility.

### 1.1 `walk_forward.py` — Rolling refit orchestrator

**Inputs:** a strategy manifest, a date range, train-window length, test-window length, step size, parameter space.

**Behavior:**
1. Slice the date range into rolling (train, test) folds.
2. For each fold: run parameter sweep on the train window, pick best by in-sample objective, run that single config on the test window. Persist both runs.
3. Aggregate fold-level test results into a continuous out-of-sample equity curve.
4. Report metrics computed on the concatenated OOS curve, never on individual test folds.

**Wraps:** `coordinator/services/backtest_runner.py:run()` for each underlying backtest.

**Public API:**
```python
async def run_walk_forward(
    session_id: int,
    manifest_path: Path,
    train_years: float,
    test_years: float,
    step_months: float,
    parameter_space: dict,
    objective: Literal["sharpe", "calmar", "sortino"],
) -> WalkForwardResult
```

### 1.2 `sweep.py` — Hyperparameter search

**Inputs:** parameter space (typed bounds), search type (grid | random | latin-hypercube), max trials, parallelism.

**Behavior:**
1. Generate parameter configurations.
2. For each config, spawn a `BacktestRun` row attached to the parent `OptimizationSession`.
3. Use `parallel_backtest_feeder.py` infrastructure for concurrent runs.
4. Return ranked results.

**Public API:**
```python
async def run_sweep(
    session_id: int,
    manifest_path: Path,
    parameter_space: dict,
    search: Literal["grid", "random", "latin"],
    max_trials: int,
    parallelism: int,
) -> SweepResult
```

### 1.3 `optimization_session.py` — Session grouping & multi-test tracking

**New SQLAlchemy model:** `OptimizationSession`
- `id`, `created_at`, `name`, `hypothesis` (text), `parameter_space` (JSON), `pre_registered_criteria` (JSON), `notes` (text)
- One-to-many → `BacktestRun.optimization_session_id` (new FK column)

**Pre-registration enforcement:**
- Session must be created with `pre_registered_criteria` before any runs attach to it.
- Multiple-testing count = `BacktestRun.count` where `optimization_session_id = self.id`.
- This count is fed to `multi_test.py` when computing corrected significance.

**Public API:**
```python
def create_session(
    name: str,
    hypothesis: str,
    parameter_space: dict,
    pre_registered_criteria: dict,
) -> OptimizationSession
```

### 1.4 `bootstrap.py` — Confidence intervals via block bootstrap

**Inputs:** an equity curve (parquet path or in-memory series), block size (default `max(20, len // 20)` to preserve autocorrelation), N resamples (default 1000), confidence level (default 95%).

**Outputs:** point estimate + CI bounds for Sharpe, Sortino, CAGR, MaxDD, Calmar.

**Why block bootstrap, not naïve:** daily returns have autocorrelation (especially in momentum regimes). Naïve resampling destroys it and inflates Sharpe CIs. Block bootstrap preserves local dependence.

**Public API:**
```python
def bootstrap_metrics(
    equity_curve: pd.Series,
    block_size: int | None = None,
    n_resamples: int = 1000,
    confidence: float = 0.95,
) -> dict[str, MetricCI]
```

### 1.5 `regime.py` — Regime tagging & conditional metrics

**Regime definitions (initial set, configurable later):**
- **Bull:** trailing 90-day BTC return > +15%
- **Bear:** trailing 90-day BTC return < -15%
- **Chop:** otherwise

**Per-regime metrics:** Sharpe, win rate, return, max drawdown — computed on the subset of dates tagged with each regime.

**Why BTC trailing return as the regime tag, even for non-crypto strategies:** BTC is the cleanest single proxy for risk-on/risk-off in this era. The lab can later support pluggable regime taggers (VIX-based for equity, custom user functions).

**Public API:**
```python
def tag_regimes(price_series: pd.Series, lookback_days: int = 90) -> pd.Series  # returns categorical
def regime_conditional_metrics(equity: pd.Series, regimes: pd.Series) -> dict[str, dict[str, float]]
```

### 1.6 `multi_test.py` — Significance correction

**Inputs:** N hypotheses tested (from `OptimizationSession`), raw p-values or Sharpe estimates.

**Methods:** Bonferroni (conservative), Benjamini-Hochberg (FDR), and a stub for SPA / White's Reality Check (deferred — implementation in v2).

**Public API:**
```python
def correct(
    raw_p_values: list[float],
    n_tested: int,
    method: Literal["bonferroni", "bh"],
    alpha: float = 0.05,
) -> list[CorrectedResult]
```

### 1.7 `cost_model.py` — Per-venue, per-asset costs

**Inputs:** YAML file declaring per-(venue, asset_type, symbol) fees and slippage.

**Extends current `TradingFee`/`SlippageModel`:** today they are single-config-per-backtest. New behavior: load a `CostModelProfile` keyed by `(venue, asset_type, symbol)` and dispatch at fill time.

**Initial profile file** at `coordinator/services/validation/cost_profiles/default.yaml`:
```yaml
profiles:
  alpaca_crypto:
    fees:
      maker_pct: 0.0015
      taker_pct: 0.0025
    slippage:
      market_bps: 15
      use_bar_range: true
  alpaca_equity:
    fees:
      flat: 0.0
      percent: 0.0
    slippage:
      market_bps: 2
  tradier_options:
    fees:
      per_contract: 0.65
      regulatory_per_contract: 0.02
    slippage:
      market_bps: 50
```

**Hook into engine:** `backtest_engine_v2.py:_try_fill()` reads the active `CostModelProfile` from the run's config rather than a flat `TradingFee`/`SlippageModel`.

### 1.8 `report.py` — Markdown + HTML research reports

**Inputs:** an `OptimizationSession.id`.

**Outputs:** two files in `data/research_reports/{session_id}/`:
- `report.md` — narrative report with embedded result tables and ASCII summary stats
- `report.html` — same content + interactive matplotlib/plotly charts (equity, drawdown, regime breakdown, parameter heatmaps, CI bands)

**Sections (auto-generated):**
1. Hypothesis & pre-registered criteria (from session)
2. Parameter space & search method
3. Walk-forward folds visualized
4. OOS equity curve with CI band
5. Regime-conditional metrics table
6. Multi-test-corrected significance
7. Kill/deploy decision against pre-registered criteria

## Deliverable 2: Crypto TSMOM strategy

New package: `data/packages/crypto-tsmom/`.

### 2.1 Strategy specification

**Universe:** BTC/USD, ETH/USD (Alpaca spot). No alts — capacity at $1000 is poor and Liu-Tsyvinski / Han-Kang-Ryu both find cross-sectional crypto momentum weaker than time-series.

**Bar frequency:** Daily. Liu-Tsyvinski tested 1-6 week horizons; daily refresh of the signal is well above the signal frequency and balances sample size vs noise.

**Signal — ensemble TSMOM with vol normalization:**

For each symbol *s* on day *t*, compute *k* lookback z-scores:

$$z_{s,t,k} = \frac{r_{s,t,k}}{\sigma_{s,t,k}}$$

where $r_{s,t,k}$ is the cumulative log return over the last *k* days and $\sigma_{s,t,k}$ is the rolling realized vol over the same window.

Default lookback set: $k \in \{7, 14, 28, 56\}$.

Ensemble signal: $\text{sig}_{s,t} = \text{mean}(\text{sign}(z_{s,t,k}) \cdot \min(|z_{s,t,k}|, 2))$ — sign captures direction, magnitude is clipped at 2σ to limit single-lookback dominance.

**Position sizing — vol-targeted, long-only spot:**

Compute the unconstrained vol-targeted weight:

$$w^{*}_{s,t} = \frac{\sigma_{\text{target}}}{\sigma_{s,t,28}} \cdot \text{sig}_{s,t}$$

Then apply spot long-only constraint:

$$w_{s,t} = \text{clip}(w^{*}_{s,t}, 0, 1)$$

with $\sigma_{\text{target}} = 0.15$ annualized. Spot crypto on Alpaca cannot be shorted, so negative signals deterministically map to a zero (cash) position. The upper bound of 1 caps gross exposure at 100% of capital per symbol.

**Rebalance:** Daily at 00:00 UTC. Only trades when target position changes by ≥ 5% to suppress noise-driven turnover.

**Long-only constraint:** Spot crypto on Alpaca cannot be shorted. Negative signals → flat. This is acknowledged asymmetric; the lit (Liu-Tsyvinski Table 3) shows the long-side captures most of the effect.

### 2.2 Manifest with parameter schema (new feature)

The framework's manifest format gains a `parameters` block declaring typed, bounded parameters. This is the foundation that `sweep.py` consumes.

```yaml
# data/packages/crypto-tsmom/quilt.yaml
name: crypto-tsmom
version: 0.1.0
class_name: CryptoTSMOMStrategy
entry_point: strategy.py
asset_types: [crypto]
data_dependencies:
  - symbol: BTC/USD
    source: alpaca
    timeframe: 1d
  - symbol: ETH/USD
    source: alpaca
    timeframe: 1d
trigger:
  bar: 1d
parameters:
  lookbacks:
    type: list[int]
    default: [7, 14, 28, 56]
    description: Lookback windows in days
  vol_target:
    type: float
    default: 0.15
    bounds: [0.05, 0.40]
    description: Annualized vol target
  rebalance_threshold:
    type: float
    default: 0.05
    bounds: [0.0, 0.20]
    description: Minimum position change to trigger trade
  z_clip:
    type: float
    default: 2.0
    bounds: [1.0, 4.0]
    description: Max abs z-score before clipping in ensemble
```

Sweep hyperparameter space (defined in `hyperparameters.yaml`, separate from the runtime `parameters` block to keep manifest stable across experiments):

```yaml
search:
  type: grid
  parameter_space:
    lookbacks:
      - [7, 14, 28]
      - [7, 14, 28, 56]
      - [14, 28, 56, 90]
    vol_target: [0.10, 0.15, 0.20, 0.25]
    rebalance_threshold: [0.05, 0.10]
    z_clip: [2.0]
# Total: 3 * 4 * 2 * 1 = 24 configs per fold
```

### 2.3 Ingestion

`ingestion.py` pulls daily OHLCV for BTC-USD and ETH-USD from yfinance (free, back to ~2014 for BTC and ~2017 for ETH) and writes to `data/market/yfinance/BTC-USD/1d.parquet` and `data/market/yfinance/ETH-USD/1d.parquet`. CLI command: `quilt data fetch yfinance BTC-USD ETH-USD --timeframe 1d --start 2014-01-01`.

The framework's `data_providers/` package gains a `yfinance.py` provider if one doesn't already exist; otherwise reuse.

## Methodology

### Walk-forward parameters

- **Train window:** 4 years (1460 days)
- **Test window:** 1 year (365 days)
- **Step:** 6 months (182 days)
- **First fold train:** 2015-01-01 → 2018-12-31; first test: 2019-01-01 → 2019-12-31
- **Last fold test:** ends 2026-05-01
- **Total folds:** approximately 13
- **OOS span:** 2019-01-01 → 2026-05-01 (continuous, ~7.3 years)

Each fold's test window contributes a chunk to the concatenated OOS equity curve. Metrics are computed on the **concatenated OOS series** spanning the full OOS span, not averaged across folds.

### Pre-registered hypothesis (in OptimizationSession)

> H1: Daily ensemble TSMOM on BTC/ETH spot, vol-scaled to 15% annualized target, with Alpaca crypto cost profile, produces walk-forward OOS Sharpe whose 95% bootstrap lower bound exceeds 0.5, and OOS max drawdown 95th-percentile bound does not exceed 35%, across the 2019-2026 OOS period.

### Deployment / kill criteria (pre-registered)

| Criterion | Threshold | Status if not met |
|---|---|---|
| OOS Sharpe lower-CI (95%) | > 0.5 | Kill |
| OOS MaxDD upper-CI (95%) | < 35% | Kill |
| Bull-regime Sharpe | > 0.3 | Kill |
| Chop-regime Sharpe | > 0.0 | Kill (negative chop kills the edge claim) |
| Bear-regime Sharpe | > -0.5 | Soft (acceptable; bear is long-only's weakness) |
| Bonferroni-corrected p-value on best config | < 0.05 | Kill |
| OOS turnover annualized | < 50× capital | Warn (high turnover = cost-sensitivity risk) |

A configuration must clear **all** hard-kill rows on its OOS walk-forward result, with Bonferroni correction applied at the sweep size (24 configs).

### Multi-testing budget

Per the pre-registered hypothesis, the session permits one parameter sweep of up to 24 configs across all folds. Each fold runs the same 24 configs (the sweep itself happens within each train window; the parameter-space declaration is fixed up-front, not per-fold). Total `OptimizationSession.runs` count: 24 configs × 13 folds ≈ 312 runs. Significance test applies a Bonferroni divisor of 24 (the number of distinct hypotheses tested in the sweep), not 312 (the runs are not independent hypotheses).

## Phasing

| Phase | What ships | Acceptance |
|---|---|---|
| Phase 1 — Cost model foundation | `cost_model.py` with YAML profiles + engine hook | Existing backtests run unchanged with `default` profile; new Alpaca-crypto profile measurably differs from old single-config |
| Phase 2 — Optimization session + sweep | DB model, `sweep.py`, `optimization_session.py` + CLI integration | A 24-config grid sweep on a dummy strategy completes; all runs grouped under one session |
| Phase 3 — Walk-forward orchestrator | `walk_forward.py` consuming sweep | A 13-fold walk-forward on the dummy strategy produces a concatenated OOS curve |
| Phase 4 — Bootstrap + regime + multi-test | `bootstrap.py`, `regime.py`, `multi_test.py` | CI bounds appear on metrics; regime tags exist on OOS dates; corrected p-values computed |
| Phase 5 — Reporter | `report.py` produces markdown + HTML | Reports render with all sections for the dummy session |
| Phase 6 — TSMOM strategy + ingestion | `data/packages/crypto-tsmom/` + yfinance ingestion | Strategy loads, runs single backtest, passes unit tests |
| Phase 7 — TSMOM full session | Run the pre-registered session | Report.md and report.html exist; deployment-or-kill decision recorded |

## Deferred work

Items below are deferred from this spec to keep scope tight. Added to `docs/superpowers/backlog.md`.

- **SPA / White's Reality Check** implementation (only Bonferroni + BH in v1)
- **Pluggable regime taggers** (BTC trailing-return only in v1; VIX-based and custom functions later)
- **Bayesian / TPE search** in `sweep.py` (grid + random + latin-hypercube only in v1)
- **Dashboard UI for OptimizationSession browsing**
- **Live deployment automation hook** after a session passes (manual deploy in v1)
- **Crypto perpetual futures venue integration** (lit shows decayed edge; revisit only if Phase 8+ wants funding carry)
- **Equity VRP defined-risk strategy** (Phase 2 of the broader research roadmap; uses the same lab)
- **Cross-sectional momentum via MTUM** (Phase 3 of the broader research roadmap)

## Risk and limitations

1. **Long-only spot capture loses some of the lit's effect size.** Liu-Tsyvinski's strongest result includes shorts; our spot-only target Sharpe should be discounted ~30-50% from their headline numbers before haircutting again for post-publication.
2. **yfinance data quality is "good enough for daily" but not tick-level.** Acceptable for daily TSMOM; would be inadequate for any intraday strategy.
3. **The 2014-2019 in-sample period coincides with crypto's wildest bull moves**, which inflates in-sample Sharpe. The walk-forward design specifically discounts this — only the concatenated OOS series counts toward the kill criteria.
4. **Crypto-trailing-return regime tagger may be circular for a crypto strategy** (the strategy directly trades the regime tagger's signal). Bull-regime Sharpe will be biased upward. The bear-regime threshold (-0.5) is set loose for this reason. Future strategies on non-crypto assets won't have this issue.
5. **Bootstrap CIs assume the OOS distribution is roughly stationary.** A regime change post-2026-05 (e.g., crypto winter, regulatory shock) is not captured by the historical CI.
