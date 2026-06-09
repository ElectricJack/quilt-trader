# Backtest Accuracy

> Quilt's backtest engine prices options the way a market maker would
> have, not the way mid-price math wishes they would.

## What you'll learn

- Why naive options backtests lie, and what that costs you in live trading.
- How Quilt prices options across the run: live chain IV, carry-forward IV,
  constant-sigma fallback, and a direction-aware envelope on top.
- The `mtm_realism` knob — what it controls, what its endpoints mean, and
  when to move it off zero.
- How `quilt backtest run` differs from `quilt research session create`,
  and where parameter sweeps and walk-forward live.

## The problem this solves

A naive options backtester marks open positions to mid-price every bar.
That looks tidy on a chart and is wrong in three different ways.

First, real fills don't land at mid. Selling hits the bid, buying hits the
ask, and on illiquid contracts the spread is 10–30% wide. A strategy that
looks profitable when entries and exits both clear at mid often becomes a
loser the moment you penalize the cross.

Second, mid stops being meaningful when one side of the quote drifts. A
$0.05 / $0.50 quote has a "mid" of $0.275, which is not a price anyone
will pay or receive. The backtest believes the position is worth $0.275;
the broker will tell you otherwise.

Third — and this is the one that actually blew up a Quilt backtest in
December 2024 — sparse chain data lets stale quotes haunt the equity
curve indefinitely. The 2026-06-04 design spec
(`docs/superpowers/specs/2026-06-04-equity-curve-mtm-design.md`) tells
the story: an algorithm sold 17,950 SPY short calls into a portfolio
worth $50,000 and the equity curve sat flat at $50,000 for six months
because the engine fell back to "mark at cost" whenever it couldn't find
fresh chain data. The position settled at -$10.67M at expiry. Before
that, it was invisible.

Quilt's options MTM path was rewritten to remove every one of those
failure modes. The engine never marks at cost. It never trusts stale
chain mid as a stand-in for "current price." Instead, it prices every
option contract every bar through a layered Black-Scholes pipeline with
a direction-aware envelope, and exposes a single dial — `mtm_realism` —
so you can choose whether to bias the price-discovery model toward
"conservative" or "broker-like."

## How Quilt does it

### The pricing pipeline

Every open option position is re-priced on every bar by
`BacktestEngine._lookup_option_mtm_price`
(`coordinator/services/backtest_engine_v2.py:922`). The pipeline:

```
   open chain row available?
            │
            ├─ yes → harvest (mid, IV) into OptionsMTMHelper caches
            │        (do NOT use the cached mid as MTM — it may be stale)
            │
            └─ regardless, price via Black-Scholes:
                  1. look up underlying close at sim_time
                  2. resolve sigma via three-tier IV cache
                       (symbol → (underlying, expiry) → underlying ATM
                        → FALLBACK_SIGMA = 0.40)
                  3. bs_or_intrinsic = black_scholes_price(S, K, T,
                       r=0.045, sigma, option_type)
                       (T ≤ 0 → intrinsic; sigma ≤ 0 → discounted intrinsic)
                  4. apply direction-aware envelope, lerped by α = mtm_realism
                  5. return max(result, 0.0)
```

Two facts about this pipeline are worth pinning down.

**The chain mid is never the MTM.** When a chain row is available, the
engine harvests its bid/ask and `implied_volatility` into the helper's
caches (`backtest_engine_v2.py:964`), then prices via Black-Scholes
anyway. Chain entries from the option-chain cache may be hours stale by
the time the engine valuates a position — using them directly would
re-introduce the same "flat equity curve" pathology the rewrite was
built to kill.

**Every bar pays the BS cost.** This is intentional. A short option held
through a quiet week with no fresh chain reads still gets a fresh BS
price each bar, computed off the current underlying close and the
carry-forward IV. The equity curve moves day-to-day with the underlying,
not in a one-day cliff at expiry.

### Three-tier IV resolution

`OptionsMTMHelper._resolve_iv`
(`coordinator/services/options_mtm.py:117`) walks three caches in order
and falls back to a constant on full miss:

| Tier | Cache | Populated by |
|---|---|---|
| 1 | `_iv_by_symbol`: exact OCC symbol → most recent IV | Any chain read for this exact contract |
| 2 | `_iv_by_expiry`: `(underlying, expiration ISO date)` → most recent IV | Any chain read for any contract on this underlying with this expiration |
| 3 | `_iv_by_underlying`: underlying → most recent IV from any contract observed | Any chain read for any contract on this underlying |
| Fallback | constant `FALLBACK_SIGMA = 0.40` | No observations at all |

The cache is rebuilt every engine run — no cross-run persistence. It
fills naturally as the algorithm reads chain data during the run. The
constant-sigma fallback is deliberately high (40% annualized) so that
short positions cannot get fake relief from a "no data, assume the option
is cheap" failure mode.

### The direction-aware envelope

`OptionsMTMHelper._apply_envelope` (`options_mtm.py:133`) post-processes
the Black-Scholes price. The semantics are **asymmetric** and worth
reading carefully — they don't match what "direction-aware envelope"
sounds like at first.

**Long positions (`position_quantity > 0`)**: no envelope. The BS price
passes through unchanged. The comment in the source explains why:

> A LONG position's worst case is the option decaying to zero, which BS
> already captures correctly bar-by-bar. Capping LONG MTM at the
> entry-bar's mid would freeze the equity curve for the whole hold
> period; use unbiased BS instead.

**Short positions (`position_quantity < 0`)**: floor at the worst of
three candidates.

```
conservative = max(bs_or_intrinsic, intrinsic, last_known_mid or 0.0)
mtm = α × bs_or_intrinsic + (1 - α) × conservative
```

A short option's MTM is a liability. The floor says: never let the
liability shrink below the worst plausible estimate — the highest of
the Black-Scholes value, the intrinsic value, and the most recent
observed mid. With `α = 0`, the floor fully applies and the algorithm
sees the most conservative liability. With `α = 1`, the envelope is
disabled and the algorithm sees the raw BS price.

**Zero quantity (`position_quantity == 0`)**: envelope bypassed. Returns
unbiased BS regardless of α. This path is used when the helper is asked
for a price without a position context (e.g. during fill-price
resolution).

The asymmetry exists because the goal is to keep algorithms from
exploiting the MTM during chain-data gaps to size new positions. Longs
can't game the system by inflating their position value — BS already
prices a long fairly. Shorts can, by understating their liability — so
the floor catches them.

### The `mtm_realism` knob

`mtm_realism` is a single float in `[0.0, 1.0]`, validated at engine
entry (`backtest_engine_v2.py:139`):

| Value | What it does | When to use it |
|---|---|---|
| `0.0` (default) | Full envelope. Short MTM is floored at `max(BS, intrinsic, last_mid)`. Most conservative for the algorithm. | Default for any new strategy. Pre-registration runs. Anything that will later trade real money. |
| `1.0` | No envelope. Algorithm sees the unbiased BS price. Backtest matches what a broker would show in clean-data conditions. | Diagnosing a "works in backtest, breaks live" divergence. Reconciling against broker statements. |
| Intermediate (e.g. `0.5`) | Linear interpolation between conservative and unbiased. | Sensitivity studies. Establishing how much of a strategy's edge depends on optimistic MTM. |

Default is `0.0`. The argument for the default: backtests should be
biased against the algorithm, not toward it. A strategy that survives
`mtm_realism = 0.0` is one whose edge is not a chart-only illusion.

### Equities vs options

Equities don't go through any of this. `BacktestEngine._lookup_symbol_close`
(`backtest_engine_v2.py:877`) returns the cached close at or before
`sim_time` and the caller multiplies through. There is no envelope, no
IV resolution, no Black-Scholes — just the bar close. Equity slippage is
modeled separately, at fill time, via a configurable `SlippageModel`.

Options need the envelope because their quotes are sparse, their spreads
are wide, and their stale prices are wrong in directionally-exploitable
ways. Equities don't share any of those problems on the timeframes Quilt
backtests over.

### Sweeps, walk-forward, and parameter sets

There are two CLI entry points for backtests, and only one of them
exposes `mtm_realism`:

**`quilt backtest run`** (`sdk/cli/commands/backtest.py:35`) runs a
single configuration end-to-end. Flags: `--algo`, `--start`, `--end`,
`--cash`, `--config` (JSON overrides), `--wait`. **`--mtm-realism` is
not exposed here.** Single-run backtests use the engine default of `0.0`.

**`quilt research session create`**
(`sdk/cli/commands/research.py:64`) creates an `OptimizationSession` —
a pre-registered hypothesis-plus-parameter-space record that subsequent
sweeps and walk-forward jobs run under. This is where `--mtm-realism`
lives (`research.py:98`), validated server-side to `[0.0, 1.0]` and
persisted on the session row (`coordinator/database/models.py:485`). The
flag flows through to every backtest run under that session.

Once a session exists you launch:

- `quilt research sweep --session-id N --search {grid,random,latin,tpe} --max-trials M`
  (`research.py:190`) — sweeps the session's parameter space against the
  configured search strategy.
- `quilt research walk-forward --session-id N --train-years 4 --test-years 1 --step-months 6`
  (`research.py:234`) — anchored or rolling walk-forward with optional
  objective (`sharpe` / `calmar` / `sortino`).
- `quilt research report --session-id N` — builds the markdown + HTML
  report.

Every backtest spawned under a session inherits the session's
`mtm_realism` (`coordinator/services/research_job_manager.py:172`).
Sweeps and walk-forwards are how you produce statistically meaningful
backtest results — and the conservative default propagates through all
of them.

## Worked example

Imagine an options-heavy strategy on SPY that the chart says runs at
Sharpe 1.8, max drawdown 9%. You're tempted to put it live.

Run the same strategy through a session twice:

```bash
# Conservative (default) — what new strategies should be evaluated under
quilt research session create \
  --name "spy-strangle-strict" --hypothesis "..." \
  --algorithm-id spy-strangle --base-config '{}' \
  --parameter-space '{}' --criteria '{"oos_sharpe_lci": 0.5}' \
  --start 2022-01-01 --end 2024-12-31 \
  --mtm-realism 0.0

# Broker-like — matches what a clean-data live run would show
quilt research session create \
  --name "spy-strangle-broker" --hypothesis "..." \
  --algorithm-id spy-strangle --base-config '{}' \
  --parameter-space '{}' --criteria '{"oos_sharpe_lci": 0.5}' \
  --start 2022-01-01 --end 2024-12-31 \
  --mtm-realism 1.0
```

What you should expect to see — without fabricating specific numbers:

- For an options-heavy short-vol strategy with sparse chain data, the
  conservative session will report a lower Sharpe and a deeper max
  drawdown than the broker-like one. The gap is the strategy's exposure
  to the price-discovery model. A two-Sharpe gap means your edge mostly
  lives in mid-price wishful thinking; a 0.1-Sharpe gap means the
  strategy is robust to MTM treatment.
- The conservative curve will show drawdowns in the *bars when they
  actually happen* — when underlying moves against a short position.
  The broker-like curve will too, but may show smoother day-to-day
  changes when chain data is sparse.
- A strategy that passes pre-registered OOS criteria under
  `mtm_realism = 0.0` and fails under `mtm_realism = 1.0` does not
  exist (the conservative path is, by construction, no friendlier than
  the unbiased one for shorts and identical for longs). The interesting
  case is the reverse: a strategy that passes at `1.0` and fails at
  `0.0` is one whose edge lives in the gap-bar MTM optimism. **That
  strategy should not be deployed.**

The point of running both is not to pick the friendlier number. It's to
see how much of your reported edge is robust and how much is an artifact
of the price-discovery model.

## Limits and sharp edges

- **Constant-sigma fallback.** When no IV has been observed for an
  underlying yet, the helper uses `FALLBACK_SIGMA = 0.40`. This is fine
  for short-horizon backtests where the cache fills in within the first
  few bars, less fine for cold-start runs that touch a contract with no
  chain history. Expect drift in long-history backtests over underlyings
  whose realized vol is far from 40%.
- **European-style pricing for American options.** `black_scholes_price`
  (`options_mtm.py:32`) is the European formula. US equity options are
  American (early-exerciseable). The early-exercise premium is ignored.
  Acceptable for index options like SPX. Less acceptable for deep-ITM
  calls on dividend-paying single names — short positions can be
  underpriced by the early-exercise risk the model doesn't see.
- **No dividend yield.** The risk-free rate is hard-coded at
  `RISK_FREE_RATE = 0.045` and dividends are zero. For dividend-paying
  underlyings this slightly overprices calls and underprices puts. The
  intrinsic floor catches the cases that matter.
- **Equity slippage is fixed-bps.** The current slippage model is not
  adaptive to realized volatility or quote-book depth. A 2008-style spike
  through a thin book is not modeled with extra friction.
- **Walk-forward retrains at fixed cadence.** `--train-years`,
  `--test-years`, `--step-months` are static (`research.py:236-238`).
  There is no in-period adaptation; the train window does not slide
  with regime detection.
- **`mtm_realism` is not exposed on `quilt backtest run`.** Single-run
  backtests always use `0.0`. To experiment with the dial, create a
  research session — even a session with a trivial parameter space and
  one trial.

## See also

- [`writing-algorithms.md`](./writing-algorithms.md) — what an algorithm
  looks like that produces the signals the engine then prices.
- [`data-collection.md`](./data-collection.md) — where the engine's
  price history (including option chains and IV) comes from.
- [`../superpowers/specs/2026-06-04-equity-curve-mtm-design.md`](../superpowers/specs/2026-06-04-equity-curve-mtm-design.md)
  — the design rationale for the conservative envelope, including the
  bug that motivated it.
