# Backtest Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the QuantStats HTML tearsheet with a native dashboard report page, backed by a producer-consumer ingestion pipeline that streams backtest output into a multi-resolution parquet pyramid for instant first-paint plus zoom drill-down.

**Architecture:** QuantStats stays only as a math library (`qs.stats.*`). The runner spawns a writer thread that drains chunks from the engine via a bounded queue and appends to parquet using `pyarrow.parquet.ParquetWriter`. At completion a finalizer reads the parquet, resamples to daily / hourly, computes ~28 curated metrics + rolling series + heatmap matrix, and persists everything to the `BacktestRun` row. The frontend renders a 4-slot chart grid (each slot toggleable), a Strategy-vs-Benchmark metrics table, and three side tables, all dark-themed and using `lightweight-charts` (existing dependency).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, alembic, pyarrow, quantstats, pytest+pytest-asyncio. React 18, TypeScript, react-query, lightweight-charts, Tailwind CSS, vitest+react-testing-library.

**Spec:** `docs/superpowers/specs/2026-05-15-backtest-report-design.md`

---

## File structure

**Backend (new):**
- `coordinator/services/backtest_metrics_qs.py` — qs-backed wrapper with the same function signatures as today's `backtest_metrics.py`.
- `coordinator/services/backtest_writer.py` — `ChunkingObserver` (engine-side producer) + `ParquetWriterThread` (consumer).
- `coordinator/services/backtest_finalizer.py` — read native parquet, resample, compute everything, write the row.
- `coordinator/database/migrations/versions/<rev>_backtest_report_columns.py` — alembic migration adding 6 JSON columns and dropping `tearsheet_path`.

**Backend (modified):**
- `coordinator/database/models.py` — add new columns; drop `tearsheet_path`.
- `coordinator/services/backtest_runner.py` — wire ChunkingObserver + writer thread + finalizer; drop inline metrics computation + `generate_tearsheet` call.
- `coordinator/api/routes/backtest_runs.py` — add `GET /report`, add `GET /equity`, remove `GET /tearsheet`, remove `GET /equity-curve`, extend `DELETE` to clean up the run directory.

**Backend (deleted):**
- `coordinator/services/backtest_tearsheet.py`
- `coordinator/services/backtest_metrics.py` (only after the runner switches to `backtest_metrics_qs`).

**Frontend (new in `dashboard/src/components/report/`):**
- `KpiCard.tsx`, `MetricsTable.tsx`, `EquitySlot.tsx`, `DrawdownSlot.tsx`, `ReturnsDistributionSlot.tsx`, `RollingMetricsSlot.tsx`, `MonthlyHeatmap.tsx`, `DrawdownsTable.tsx`, `EoyTable.tsx`, `ParametersTable.tsx`.

**Frontend (modified):**
- `dashboard/src/api/client.ts` — add `getBacktestReport`, `getBacktestEquityWindow`; remove `getBacktestEquityCurve`.
- `dashboard/src/api/hooks.ts` — add `useBacktestReport`, `useBacktestEquityWindow`; remove `useBacktestEquityCurve`.
- `dashboard/src/types/index.ts` — add report payload types.
- `dashboard/src/pages/BacktestRunDetail.tsx` — rewrite body to render the new component tree; remove "Download tearsheet" anchor.

**Tests (new):**
- `tests/coordinator/services/test_backtest_metrics_qs.py`
- `tests/coordinator/services/test_backtest_writer.py`
- `tests/coordinator/services/test_backtest_finalizer.py`
- `tests/coordinator/test_backtest_report_api.py`

**Tests (modified):**
- `tests/coordinator/services/test_backtest_metrics.py` — loosen tolerance to `rel=1e-3` to accommodate qs differences.
- `tests/coordinator/services/test_backtest_runner.py` — verify the runner uses the new pipeline.

---

## Conventions

- After each task, run the local test suite for the area touched (`pytest tests/coordinator/...` for backend, `npm run typecheck && npm test` for frontend).
- Each task ends in a single commit. Commit message style follows the repo: `feat(backtest): ...`, `fix(backtest): ...`, `chore(backtest): ...`, `docs(backtest): ...`.
- For backend changes, write failing test → run to confirm fail → minimal impl → run to confirm pass → commit.
- For frontend chart components (slots), tests are limited to "renders without crashing with mock data" since canvas-based charts don't unit-test meaningfully. KpiCard / MetricsTable / small tables get behavioral tests.
- Project root for all `pytest` commands: `/home/jkern/dev/quilt-trader`. Project root for all `npm` commands: `/home/jkern/dev/quilt-trader/dashboard`.

---

## Task 1: Add `qs.stats` wrapper module with parity tests

**Files:**
- Create: `coordinator/services/backtest_metrics_qs.py`
- Create: `tests/coordinator/services/test_backtest_metrics_qs.py`

QuantStats is already a dependency. This wrapper provides the metrics the runner needs — Sharpe, Sortino, CAGR, drawdown stats, etc — implemented as one-line passthroughs to `qs.stats.*` so the runner doesn't have to know the qs API.

- [ ] **Step 1: Write failing test for `compute_all` shape**

`tests/coordinator/services/test_backtest_metrics_qs.py`:

```python
"""Tests for the QuantStats-backed metrics wrapper."""
import math
import pandas as pd
import pytest

from coordinator.services.backtest_metrics_qs import compute_all


def _daily_df(values: list[float]) -> pd.DataFrame:
    """Build a daily portfolio_value frame with a 'return' column."""
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D")
    df = pd.DataFrame({"portfolio_value": values}, index=idx)
    df["return"] = df["portfolio_value"].pct_change().fillna(0)
    return df


def test_compute_all_returns_canonical_keys():
    df = _daily_df([100.0, 101.0, 99.0, 102.0, 100.0, 103.0, 105.0])
    result = compute_all(df, trades=[], initial_cash=100.0, risk_free_rate=0.04)
    expected_keys = {
        "total_return", "cagr", "volatility", "sharpe_ratio", "sortino_ratio",
        "calmar_ratio", "max_drawdown", "max_drawdown_date", "romad",
        "trade_count", "win_rate", "profit_factor", "avg_win", "avg_loss",
        "expectancy", "longest_drawdown_days",
        "longest_winning_streak", "longest_losing_streak",
        "drawdown_periods",
    }
    assert expected_keys.issubset(result.keys())
    assert isinstance(result["sharpe_ratio"], float)
    assert isinstance(result["drawdown_periods"], list)


def test_total_return_matches_simple_calc():
    df = _daily_df([100.0, 110.0, 120.0])
    result = compute_all(df, trades=[], initial_cash=100.0)
    assert result["total_return"] == pytest.approx(0.20, rel=1e-3)


def test_max_drawdown_positive_value():
    df = _daily_df([100.0, 90.0, 80.0, 95.0])
    result = compute_all(df, trades=[], initial_cash=100.0)
    # qs returns drawdown as negative; we normalize to positive magnitude
    assert result["max_drawdown"] == pytest.approx(0.20, rel=1e-2)
    assert result["max_drawdown_date"] is not None


def test_empty_df_returns_safe_zeros():
    df = pd.DataFrame(columns=["portfolio_value", "return"])
    result = compute_all(df, trades=[], initial_cash=100.0)
    assert result["total_return"] == 0.0
    assert result["sharpe_ratio"] == 0.0
    assert result["drawdown_periods"] == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/coordinator/services/test_backtest_metrics_qs.py -v
```

Expected: `ModuleNotFoundError: No module named 'coordinator.services.backtest_metrics_qs'`.

- [ ] **Step 3: Implement the wrapper**

`coordinator/services/backtest_metrics_qs.py`:

```python
"""QuantStats-backed metrics wrapper.

Same surface as `backtest_metrics.compute_all` so the runner can swap
the import without code changes downstream. We keep our trade-based
metrics (win_rate, profit_factor, expectancy, streaks) since qs doesn't
model round-trip trades.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd
import quantstats as qs


# ---- Equity-curve metrics (qs-backed) ----

def _returns_series(df: pd.DataFrame) -> pd.Series:
    if df.empty or "return" not in df.columns:
        return pd.Series(dtype=float)
    s = df["return"].copy()
    if hasattr(s.index, "tz") and s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    return s


def total_return(df: pd.DataFrame, initial_cash: float) -> float:
    if df.empty:
        return 0.0
    final = float(df["portfolio_value"].iloc[-1])
    return (final / initial_cash) - 1.0


def cagr(df: pd.DataFrame) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    return float(qs.stats.cagr(s))


def volatility(df: pd.DataFrame) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    return float(qs.stats.volatility(s, annualize=True))


def sharpe_ratio(df: pd.DataFrame, risk_free_rate: float = 0.04) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    return float(qs.stats.sharpe(s, rf=risk_free_rate))


def sortino_ratio(df: pd.DataFrame, risk_free_rate: float = 0.04) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    return float(qs.stats.sortino(s, rf=risk_free_rate))


def calmar_ratio(df: pd.DataFrame) -> float:
    s = _returns_series(df)
    if s.empty or len(s) < 2:
        return 0.0
    return float(qs.stats.calmar(s))


def max_drawdown(df: pd.DataFrame) -> dict:
    s = _returns_series(df)
    if s.empty:
        return {"drawdown": 0.0, "date": None}
    dd_series = qs.stats.to_drawdown_series(s)
    dd = float(abs(dd_series.min()))
    if math.isnan(dd):
        return {"drawdown": 0.0, "date": None}
    return {"drawdown": dd, "date": dd_series.idxmin()}


def romad(df: pd.DataFrame) -> float:
    md = max_drawdown(df)
    if md["drawdown"] == 0:
        return 0.0
    return cagr(df) / md["drawdown"]


def longest_drawdown_days(df: pd.DataFrame) -> int:
    s = _returns_series(df)
    if s.empty:
        return 0
    details = qs.stats.drawdown_details(qs.stats.to_drawdown_series(s))
    if details is None or len(details) == 0:
        return 0
    return int(details["days"].max())


def top_n_drawdowns(df: pd.DataFrame, n: int = 10) -> list[dict]:
    s = _returns_series(df)
    if s.empty:
        return []
    details = qs.stats.drawdown_details(qs.stats.to_drawdown_series(s))
    if details is None or len(details) == 0:
        return []
    sorted_dd = details.reindex(details["max drawdown"].abs().sort_values(ascending=False).index)
    out = []
    for _, row in sorted_dd.head(n).iterrows():
        out.append({
            "start": pd.Timestamp(row["start"]).isoformat(),
            "trough": pd.Timestamp(row.get("valley", row["start"])).isoformat(),
            "recovered": pd.Timestamp(row["end"]).isoformat() if pd.notna(row.get("end")) else None,
            "depth": float(abs(row["max drawdown"]) / 100.0),  # qs returns percent
            "days": int(row["days"]),
        })
    return out


# ---- Trade-based metrics (kept from original module) ----

def round_trip_trades(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t.get("realized_pnl") is not None]


def win_rate(trades: list[dict]) -> float:
    rts = round_trip_trades(trades)
    if not rts:
        return 0.0
    wins = sum(1 for t in rts if t["realized_pnl"] > 0)
    return wins / len(rts)


def profit_factor(trades: list[dict]) -> float:
    rts = round_trip_trades(trades)
    gross_profit = sum(t["realized_pnl"] for t in rts if t["realized_pnl"] > 0)
    gross_loss = abs(sum(t["realized_pnl"] for t in rts if t["realized_pnl"] < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def avg_win(trades: list[dict]) -> float:
    wins = [t["realized_pnl"] for t in round_trip_trades(trades) if t["realized_pnl"] > 0]
    return sum(wins) / len(wins) if wins else 0.0


def avg_loss(trades: list[dict]) -> float:
    losses = [t["realized_pnl"] for t in round_trip_trades(trades) if t["realized_pnl"] < 0]
    return sum(losses) / len(losses) if losses else 0.0


def expectancy(trades: list[dict]) -> float:
    wr = win_rate(trades)
    return wr * avg_win(trades) + (1 - wr) * avg_loss(trades)


def longest_streak(trades: list[dict], *, win: bool) -> int:
    rts = round_trip_trades(trades)
    longest = 0
    current = 0
    for t in rts:
        is_win = t["realized_pnl"] > 0
        if (win and is_win) or (not win and not is_win):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def compute_all(
    df: pd.DataFrame, trades: list[dict], *,
    initial_cash: float, risk_free_rate: float = 0.04,
) -> dict[str, Any]:
    md = max_drawdown(df)
    return {
        "total_return": total_return(df, initial_cash),
        "cagr": cagr(df),
        "volatility": volatility(df),
        "sharpe_ratio": sharpe_ratio(df, risk_free_rate),
        "sortino_ratio": sortino_ratio(df, risk_free_rate),
        "calmar_ratio": calmar_ratio(df),
        "max_drawdown": md["drawdown"],
        "max_drawdown_date": md["date"],
        "romad": romad(df),
        "trade_count": len(round_trip_trades(trades)),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "avg_win": avg_win(trades),
        "avg_loss": avg_loss(trades),
        "expectancy": expectancy(trades),
        "longest_drawdown_days": longest_drawdown_days(df),
        "longest_winning_streak": longest_streak(trades, win=True),
        "longest_losing_streak": longest_streak(trades, win=False),
        "drawdown_periods": top_n_drawdowns(df, n=10),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/coordinator/services/test_backtest_metrics_qs.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_metrics_qs.py tests/coordinator/services/test_backtest_metrics_qs.py
git commit -m "feat(backtest): qs-backed metrics wrapper as drop-in for backtest_metrics"
```

---

## Task 2: Switch the runner to the qs metrics wrapper

**Files:**
- Modify: `coordinator/services/backtest_runner.py:26` — change `from coordinator.services.backtest_metrics import compute_all` to import from `backtest_metrics_qs`.
- Modify: `tests/coordinator/services/test_backtest_metrics.py` — loosen tolerance.

The old hand-rolled metrics module stays on disk for one more task; we cut the runner over first to flush out any qs-vs-bespoke discrepancies in CI before deleting the old code.

- [ ] **Step 1: Loosen tolerances in the existing metrics test**

Open `tests/coordinator/services/test_backtest_metrics.py`. Find every `pytest.approx(<value>)` and add `rel=1e-3`. For example:

Before:
```python
assert sharpe_ratio(df) == pytest.approx(0.5)
```

After:
```python
assert sharpe_ratio(df) == pytest.approx(0.5, rel=1e-3)
```

If the file imports `from coordinator.services.backtest_metrics import ...`, change the import to `backtest_metrics_qs`.

- [ ] **Step 2: Run the metrics tests to confirm parity**

```bash
pytest tests/coordinator/services/test_backtest_metrics.py -v
```

Expected: all pass with the looser tolerance. If any test fails, the qs implementation differs more than 0.1% — investigate before continuing (the most likely culprits are CAGR's day-count convention and Sortino's downside-deviation formula, both of which the spec accepts).

- [ ] **Step 3: Switch the runner's import**

In `coordinator/services/backtest_runner.py`, change:

```python
from coordinator.services.backtest_metrics import compute_all
```

to:

```python
from coordinator.services.backtest_metrics_qs import compute_all
```

- [ ] **Step 4: Run the runner test**

```bash
pytest tests/coordinator/services/test_backtest_runner.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_runner.py tests/coordinator/services/test_backtest_metrics.py
git commit -m "chore(backtest): switch runner to qs-backed metrics; loosen test tolerances"
```

---

## Task 3: Delete the old hand-rolled metrics module

**Files:**
- Delete: `coordinator/services/backtest_metrics.py`
- Delete: `tests/coordinator/services/test_backtest_metrics.py` (replaced by `test_backtest_metrics_qs.py`)

The runner already uses `backtest_metrics_qs`. Nothing else imports the old module.

- [ ] **Step 1: Verify nothing imports the old module**

```bash
grep -rn "from coordinator.services.backtest_metrics import\|backtest_metrics\." coordinator/ tests/ scripts/ packages/ 2>/dev/null | grep -v backtest_metrics_qs
```

Expected: no output.

- [ ] **Step 2: Delete the files**

```bash
rm coordinator/services/backtest_metrics.py tests/coordinator/services/test_backtest_metrics.py
```

- [ ] **Step 3: Run the full backtest test suite to confirm green**

```bash
pytest tests/coordinator/services/test_backtest_runner.py tests/coordinator/services/test_backtest_metrics_qs.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add -A coordinator/services/backtest_metrics.py tests/coordinator/services/test_backtest_metrics.py
git commit -m "chore(backtest): remove hand-rolled metrics module (replaced by qs wrapper)"
```

---

## Task 4: Alembic migration — add report columns, drop tearsheet_path

**Files:**
- Create: `coordinator/database/migrations/versions/<auto>_backtest_report_columns.py`
- Modify: `coordinator/database/models.py:339-396` (the `BacktestRun` class) — add new column declarations, remove `tearsheet_path`.

- [ ] **Step 1: Generate an alembic revision**

```bash
cd /home/jkern/dev/quilt-trader && alembic revision -m "backtest_report_columns"
```

Note the generated filename, e.g. `coordinator/database/migrations/versions/abc123_backtest_report_columns.py`.

- [ ] **Step 2: Fill in the migration**

Replace the auto-generated `upgrade()` and `downgrade()` bodies with:

```python
def upgrade() -> None:
    op.add_column('backtest_runs', sa.Column('key_metrics', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('rolling_metrics', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('monthly_returns_matrix', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('eoy_returns', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('benchmark_equity_curve', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('drawdown_curve', sa.JSON(), nullable=True))
    with op.batch_alter_table('backtest_runs') as batch:
        batch.drop_column('tearsheet_path')


def downgrade() -> None:
    op.add_column('backtest_runs', sa.Column('tearsheet_path', sa.String(), nullable=True))
    with op.batch_alter_table('backtest_runs') as batch:
        batch.drop_column('drawdown_curve')
        batch.drop_column('benchmark_equity_curve')
        batch.drop_column('eoy_returns')
        batch.drop_column('monthly_returns_matrix')
        batch.drop_column('rolling_metrics')
        batch.drop_column('key_metrics')
```

The `batch_alter_table` block is required for SQLite (and harmless on Postgres); SQLite doesn't support raw `DROP COLUMN`.

- [ ] **Step 3: Update the model**

In `coordinator/database/models.py`, locate the `BacktestRun` class (around line 339).

Remove the `tearsheet_path` line:
```python
    tearsheet_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
```

Add (anywhere in the class, after `drawdown_periods`):
```python
    key_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rolling_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    monthly_returns_matrix: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    eoy_returns: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    benchmark_equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    drawdown_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Run the migration up + down + up to verify reversibility**

```bash
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

Expected: each command exits 0 with no errors.

- [ ] **Step 5: Run the model test**

```bash
pytest tests/coordinator/test_models.py -v
```

Expected: pass.

- [ ] **Step 6: Remove `tearsheet_path` from the route response**

In `coordinator/api/routes/backtest_runs.py`, delete the `"tearsheet_path": r.tearsheet_path,` line in `_to_response()` (around line 74).

- [ ] **Step 7: Run the API test**

```bash
pytest tests/coordinator/test_backtest_runs_api.py -v
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add coordinator/database/migrations/versions/*backtest_report_columns.py coordinator/database/models.py coordinator/api/routes/backtest_runs.py
git commit -m "feat(schema): backtest_runs report columns; drop tearsheet_path"
```

---

## Task 5: `ChunkingObserver` — emit chunks on day boundaries with adaptive sizing

**Files:**
- Create: `coordinator/services/backtest_writer.py` (start with just `ChunkingObserver` here; writer thread added in Task 6)
- Create: `tests/coordinator/services/test_backtest_writer.py`

The observer implements the engine's `EngineObserver` protocol (`on_tick`, `on_equity_point`, `on_fill`, `on_signals_emitted`, `on_signal_rejected`, `on_complete`, `on_error`). It buffers per-tick events and pushes a chunk dict to a `queue.Queue` when `days_per_chunk` simulated days have elapsed.

- [ ] **Step 1: Write failing tests**

`tests/coordinator/services/test_backtest_writer.py`:

```python
"""Tests for ChunkingObserver."""
from datetime import datetime
from queue import Queue

import pandas as pd
import pytest

from coordinator.services.backtest_writer import ChunkingObserver


def _clock(start: str, periods: int, freq: str) -> pd.DataFrame:
    """Build a clock_series-shaped DataFrame for the observer."""
    return pd.DataFrame({"timestamp": pd.date_range(start, periods=periods, freq=freq)})


def test_days_per_chunk_with_minute_bars_nyse():
    # ~390 ticks/day for NYSE minute bars
    clock = _clock("2024-01-02 09:30", periods=390 * 5, freq="1min")
    obs = ChunkingObserver(queue=Queue(), clock_series=clock)
    # ceil(5000/390) = 13 days
    assert obs.days_per_chunk == 13


def test_days_per_chunk_with_daily_bars_capped_at_max():
    clock = _clock("2024-01-02", periods=365, freq="1D")
    obs = ChunkingObserver(queue=Queue(), clock_series=clock)
    # ceil(5000/1) = 5000 → capped at MAX_DAYS_PER_CHUNK (30)
    assert obs.days_per_chunk == 30


def test_days_per_chunk_with_24h_minute_bars():
    clock = _clock("2024-01-02", periods=1440 * 5, freq="1min")
    obs = ChunkingObserver(queue=Queue(), clock_series=clock)
    # ceil(5000/1440) = 4 days
    assert obs.days_per_chunk == 4


def test_chunk_emitted_on_day_boundary():
    clock = _clock("2024-01-02 00:00", periods=2880, freq="1min")  # 2 days at 1min
    q: Queue = Queue()
    obs = ChunkingObserver(queue=q, clock_series=clock, days_per_chunk_override=1)
    # Simulate the engine calling on_equity_point per tick
    for ts in clock["timestamp"]:
        obs.on_equity_point(ts.to_pydatetime(), 100.0, 100.0, [])
    obs.flush()
    # Drain queue
    chunks = []
    while not q.empty():
        chunks.append(q.get())
    # 2 days, 1 day per chunk → 2 chunks
    assert len(chunks) == 2
    assert chunks[0]["equity"][0]["timestamp"].date() == datetime(2024, 1, 2).date()
    assert chunks[1]["equity"][0]["timestamp"].date() == datetime(2024, 1, 3).date()


def test_chunk_includes_trades_for_window():
    clock = _clock("2024-01-02 00:00", periods=1440, freq="1min")
    q: Queue = Queue()
    obs = ChunkingObserver(queue=q, clock_series=clock, days_per_chunk_override=1)
    obs.on_equity_point(datetime(2024, 1, 2, 10, 0), 100.0, 100.0, [])
    # Mock fill object with the FillRecord shape
    class _F:
        def __init__(self, ts):
            self.timestamp = ts
            self.symbol = "SPY"; self.asset_type = "stock"; self.side = "buy"
            self.quantity = 1.0; self.requested_price = 1.0; self.fill_price = 1.0
            self.slippage_dollars = 0.0; self.slippage_bps_applied = 0.0
            self.fees = 0.0; self.fee_breakdown = {}; self.signal_id = "x"
            self.realized_pnl = None
    obs.on_fill(_F(datetime(2024, 1, 2, 10, 1)))
    obs.flush()
    chunk = q.get()
    assert len(chunk["trades"]) == 1
    assert chunk["trades"][0]["symbol"] == "SPY"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/coordinator/services/test_backtest_writer.py -v
```

Expected: `ModuleNotFoundError: No module named 'coordinator.services.backtest_writer'`.

- [ ] **Step 3: Implement `ChunkingObserver`**

`coordinator/services/backtest_writer.py`:

```python
"""Backtest streaming pipeline.

ChunkingObserver: implements EngineObserver, buffers per-tick events
and pushes chunks to a queue on simulated-day boundaries with adaptive
sizing (target ticks per chunk, clamped to a day range).

ParquetWriterThread (added in Task 6): consumes chunks from the queue
and appends to parquet files using pyarrow.parquet.ParquetWriter.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, date
from queue import Queue
from typing import Any, Optional

import pandas as pd

TARGET_TICKS_PER_CHUNK = 5_000
MIN_DAYS_PER_CHUNK = 1
MAX_DAYS_PER_CHUNK = 30


def compute_days_per_chunk(clock_series: pd.DataFrame) -> int:
    """Adaptive: target ~5k ticks per chunk, clamped to [1, 30] days."""
    if clock_series is None or len(clock_series) == 0:
        return MIN_DAYS_PER_CHUNK
    total_days = max(1, (clock_series["timestamp"].iloc[-1] - clock_series["timestamp"].iloc[0]).days + 1)
    avg_ticks_per_day = max(1.0, len(clock_series) / total_days)
    days = math.ceil(TARGET_TICKS_PER_CHUNK / avg_ticks_per_day)
    return max(MIN_DAYS_PER_CHUNK, min(MAX_DAYS_PER_CHUNK, days))


class ChunkingObserver:
    """EngineObserver that emits chunks every N simulated days to a queue."""

    def __init__(
        self,
        *, queue: Queue, clock_series: pd.DataFrame,
        days_per_chunk_override: Optional[int] = None,
    ) -> None:
        self._q = queue
        self.days_per_chunk: int = (
            days_per_chunk_override
            if days_per_chunk_override is not None
            else compute_days_per_chunk(clock_series)
        )
        self._buf_equity: list[dict] = []
        self._buf_trades: list[dict] = []
        self._chunk_start_date: Optional[date] = None
        self._chunk_window_days: int = 0
        self._lock = threading.Lock()
        self._daily_aggregate: dict[date, float] = {}  # date -> last portfolio_value
        self.writer_error: Optional[BaseException] = None

    # ---- EngineObserver protocol ----

    def on_tick(self, sim_time: datetime, ctx_snapshot: dict) -> None:
        pass

    def on_signals_emitted(self, sim_time: datetime, signals) -> None:
        pass

    def on_signal_rejected(self, sim_time: datetime, signal, reason: str) -> None:
        pass

    def on_equity_point(
        self, sim_time: datetime, portfolio_value: float, cash: float, positions
    ) -> None:
        d = sim_time.date()
        if self._chunk_start_date is None:
            self._chunk_start_date = d
            self._chunk_window_days = 1
        elif d != self._chunk_start_date and d not in (self._chunk_start_date,):
            # New day. Detect rollover by counting unique dates in the buffer.
            if self._chunk_window_days >= self.days_per_chunk:
                self._flush_chunk()
                self._chunk_start_date = d
                self._chunk_window_days = 1
            else:
                # Same chunk, advance day window
                if not self._buf_equity or self._buf_equity[-1]["timestamp"].date() != d:
                    self._chunk_window_days += 1
        self._buf_equity.append({
            "timestamp": sim_time,
            "portfolio_value": float(portfolio_value),
            "cash": float(cash),
        })
        with self._lock:
            self._daily_aggregate[d] = float(portfolio_value)

    def on_fill(self, fill) -> None:
        self._buf_trades.append({
            "timestamp": fill.timestamp,
            "symbol": fill.symbol,
            "asset_type": fill.asset_type,
            "side": fill.side,
            "quantity": float(fill.quantity),
            "requested_price": fill.requested_price,
            "fill_price": fill.fill_price,
            "slippage_dollars": fill.slippage_dollars,
            "slippage_bps_applied": fill.slippage_bps_applied,
            "fees": fill.fees,
            "fee_breakdown": fill.fee_breakdown,
            "signal_id": fill.signal_id,
            "realized_pnl": fill.realized_pnl,
        })

    def on_complete(self, summary) -> None:
        self.flush()

    def on_error(self, exc) -> None:
        self.flush()

    # ---- Public ----

    def flush(self) -> None:
        if self._buf_equity or self._buf_trades:
            self._flush_chunk()

    def daily_aggregate_snapshot(self) -> list[dict]:
        """Return a thread-safe snapshot of the running daily curve."""
        with self._lock:
            return [
                {"timestamp": d.isoformat(), "portfolio_value": v}
                for d, v in sorted(self._daily_aggregate.items())
            ]

    # ---- Internal ----

    def _flush_chunk(self) -> None:
        chunk = {
            "equity": self._buf_equity,
            "trades": self._buf_trades,
            "window_start": self._buf_equity[0]["timestamp"] if self._buf_equity else None,
            "window_end": self._buf_equity[-1]["timestamp"] if self._buf_equity else None,
        }
        self._buf_equity = []
        self._buf_trades = []
        self._chunk_start_date = None
        self._chunk_window_days = 0
        self._q.put(chunk)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/coordinator/services/test_backtest_writer.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_writer.py tests/coordinator/services/test_backtest_writer.py
git commit -m "feat(backtest): ChunkingObserver — adaptive day-boundary chunks for the writer pipeline"
```

---

## Task 6: `ParquetWriterThread` — drain queue, append to parquet

**Files:**
- Modify: `coordinator/services/backtest_writer.py` — add `ParquetWriterThread`.
- Modify: `tests/coordinator/services/test_backtest_writer.py` — add writer-thread tests.

Single long-running thread holding two open `pyarrow.parquet.ParquetWriter` instances (equity + trades). Drains the queue until it sees a `None` sentinel, then closes both writers.

- [ ] **Step 1: Write failing tests**

Append to `tests/coordinator/services/test_backtest_writer.py`:

```python
import time
from pathlib import Path
import pyarrow.parquet as pq

from coordinator.services.backtest_writer import ParquetWriterThread


def test_writer_thread_appends_chunks_to_parquet(tmp_path):
    q: Queue = Queue()
    eq_path = tmp_path / "equity_native.parquet"
    tr_path = tmp_path / "trades.parquet"
    t = ParquetWriterThread(queue=q, equity_path=eq_path, trades_path=tr_path)
    t.start()
    # Push 2 chunks
    q.put({
        "equity": [
            {"timestamp": datetime(2024, 1, 2, 10, 0), "portfolio_value": 100.0, "cash": 100.0},
            {"timestamp": datetime(2024, 1, 2, 10, 1), "portfolio_value": 101.0, "cash": 99.0},
        ],
        "trades": [],
        "window_start": datetime(2024, 1, 2, 10, 0),
        "window_end": datetime(2024, 1, 2, 10, 1),
    })
    q.put({
        "equity": [
            {"timestamp": datetime(2024, 1, 3, 10, 0), "portfolio_value": 102.0, "cash": 98.0},
        ],
        "trades": [],
        "window_start": datetime(2024, 1, 3, 10, 0),
        "window_end": datetime(2024, 1, 3, 10, 0),
    })
    q.put(None)  # sentinel
    t.join(timeout=5)
    assert not t.is_alive()
    table = pq.read_table(eq_path)
    assert table.num_rows == 3
    assert "portfolio_value" in table.column_names


def test_writer_thread_records_error_on_bad_chunk(tmp_path):
    q: Queue = Queue()
    t = ParquetWriterThread(
        queue=q,
        equity_path=tmp_path / "equity_native.parquet",
        trades_path=tmp_path / "trades.parquet",
    )
    t.start()
    # Send a malformed chunk (string instead of dict for equity rows)
    q.put({"equity": "not-a-list", "trades": [], "window_start": None, "window_end": None})
    q.put(None)
    t.join(timeout=5)
    assert t.error is not None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/coordinator/services/test_backtest_writer.py -v
```

Expected: 2 new tests fail with `ImportError: cannot import name 'ParquetWriterThread'`.

- [ ] **Step 3: Implement the writer thread**

Append to `coordinator/services/backtest_writer.py`:

```python
import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


_EQUITY_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns")),
    ("portfolio_value", pa.float64()),
    ("cash", pa.float64()),
])

_TRADE_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns")),
    ("symbol", pa.string()),
    ("asset_type", pa.string()),
    ("side", pa.string()),
    ("quantity", pa.float64()),
    ("requested_price", pa.float64()),
    ("fill_price", pa.float64()),
    ("slippage_dollars", pa.float64()),
    ("slippage_bps_applied", pa.float64()),
    ("fees", pa.float64()),
    ("fee_breakdown", pa.string()),  # JSON-serialized
    ("signal_id", pa.string()),
    ("realized_pnl", pa.float64()),
])


class ParquetWriterThread(threading.Thread):
    """Drain (queue → parquet) until a None sentinel, then close writers."""

    def __init__(
        self, *, queue: Queue, equity_path: Path, trades_path: Path,
    ) -> None:
        super().__init__(daemon=True, name="backtest-writer")
        self._q = queue
        self._eq_path = Path(equity_path)
        self._tr_path = Path(trades_path)
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        eq_writer: Optional[pq.ParquetWriter] = None
        tr_writer: Optional[pq.ParquetWriter] = None
        try:
            self._eq_path.parent.mkdir(parents=True, exist_ok=True)
            eq_writer = pq.ParquetWriter(self._eq_path, _EQUITY_SCHEMA, compression="snappy")
            tr_writer = pq.ParquetWriter(self._tr_path, _TRADE_SCHEMA, compression="snappy")
            while True:
                chunk = self._q.get()
                if chunk is None:
                    return
                self._write_chunk(eq_writer, tr_writer, chunk)
        except Exception as exc:
            logger.exception("ParquetWriterThread failed")
            self.error = exc
            # Drain remaining items so the producer doesn't block forever
            while True:
                try:
                    item = self._q.get_nowait()
                except Exception:
                    break
                if item is None:
                    break
        finally:
            if eq_writer is not None:
                try: eq_writer.close()
                except Exception: pass
            if tr_writer is not None:
                try: tr_writer.close()
                except Exception: pass

    @staticmethod
    def _write_chunk(eq_writer, tr_writer, chunk: dict) -> None:
        import json
        eq_rows = chunk["equity"]
        if not isinstance(eq_rows, list):
            raise TypeError(f"chunk['equity'] must be a list, got {type(eq_rows).__name__}")
        if eq_rows:
            table = pa.Table.from_pylist(eq_rows, schema=_EQUITY_SCHEMA)
            eq_writer.write_table(table)
        tr_rows = chunk.get("trades") or []
        if tr_rows:
            normalized = [
                {**t, "fee_breakdown": json.dumps(t.get("fee_breakdown") or {})}
                for t in tr_rows
            ]
            table = pa.Table.from_pylist(normalized, schema=_TRADE_SCHEMA)
            tr_writer.write_table(table)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/coordinator/services/test_backtest_writer.py -v
```

Expected: 7 passed total.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_writer.py tests/coordinator/services/test_backtest_writer.py
git commit -m "feat(backtest): ParquetWriterThread — streaming append for the writer pipeline"
```

---

## Task 7: `backtest_finalizer` — read parquet, compute everything, write row

**Files:**
- Create: `coordinator/services/backtest_finalizer.py`
- Create: `tests/coordinator/services/test_backtest_finalizer.py`

Pure function: takes a `run_id`, the on-disk parquet paths, the benchmark bar DataFrame (or None), the run config, and a session factory; reads native parquet, resamples, computes all metrics + matrices, writes the row.

- [ ] **Step 1: Write failing tests**

`tests/coordinator/services/test_backtest_finalizer.py`:

```python
"""Tests for backtest_finalizer."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from coordinator.services.backtest_finalizer import (
    resample_to_daily, build_eoy_returns, build_monthly_matrix, finalize_run,
)


def _write_native_equity(path: Path, days: int = 252, start_value: float = 100.0):
    """Write a fake daily equity_native.parquet with `days` rows."""
    idx = pd.date_range("2023-01-02", periods=days, freq="D")
    values = [start_value * (1 + 0.001 * i) for i in range(days)]
    df = pd.DataFrame({
        "timestamp": idx,
        "portfolio_value": values,
        "cash": values,
    })
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path)


def test_resample_to_daily_keeps_last_value_per_day(tmp_path):
    p = tmp_path / "eq.parquet"
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-02 09:30", "2024-01-02 16:00",
                                     "2024-01-03 09:30", "2024-01-03 16:00"]),
        "portfolio_value": [100.0, 101.0, 102.0, 103.0],
        "cash": [100.0, 101.0, 102.0, 103.0],
    })
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), p)
    daily = resample_to_daily(p)
    assert len(daily) == 2
    assert daily.iloc[0]["portfolio_value"] == 101.0
    assert daily.iloc[1]["portfolio_value"] == 103.0


def test_build_eoy_returns_per_year():
    idx = pd.date_range("2022-01-03", "2024-12-31", freq="D")
    pv = [100.0 * (1 + 0.0005 * i) for i in range(len(idx))]
    df = pd.DataFrame({"timestamp": idx, "portfolio_value": pv})
    bench = pd.Series([100.0 * (1 + 0.0003 * i) for i in range(len(idx))], index=idx)
    eoy = build_eoy_returns(df, bench)
    years = {row["year"] for row in eoy}
    assert years == {2022, 2023, 2024}


def test_build_monthly_matrix_shape():
    idx = pd.date_range("2023-01-01", "2024-12-31", freq="D")
    pv = [100.0 * (1 + 0.0005 * i) for i in range(len(idx))]
    df = pd.DataFrame({"timestamp": idx, "portfolio_value": pv})
    matrix = build_monthly_matrix(df)
    assert sorted(matrix["years"]) == [2023, 2024]
    assert all(len(c) == 3 for c in matrix["cells"])  # [year, month, ret_pct]


@pytest.mark.asyncio
async def test_finalize_run_populates_all_columns(tmp_path, test_app, db_session):
    """End-to-end: write native parquet, run finalizer, check row fields."""
    from coordinator.database.models import Algorithm, BacktestRun
    algo = Algorithm(name="t", repo_url="https://github.com/x/y", install_status="installed")
    db_session.add(algo); await db_session.flush()
    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2023, 1, 2, tzinfo=timezone.utc),
        date_range_end=datetime(2023, 12, 29, tzinfo=timezone.utc),
        initial_cash=100.0,
    )
    db_session.add(run); await db_session.commit()

    run_dir = tmp_path / run.id
    run_dir.mkdir()
    _write_native_equity(run_dir / "equity_native.parquet", days=252)
    # Empty trades file
    pq.write_table(pa.table({
        "timestamp": pa.array([], type=pa.timestamp("ns")),
        "symbol": pa.array([], type=pa.string()),
        "side": pa.array([], type=pa.string()),
        "quantity": pa.array([], type=pa.float64()),
        "realized_pnl": pa.array([], type=pa.float64()),
    }), run_dir / "trades.parquet")

    from coordinator.api.dependencies import get_container
    container = get_container()

    await finalize_run(
        run_id=run.id, run_dir=run_dir, session_factory=container.session_factory,
        benchmark_bar_df=None,
    )

    from sqlalchemy import select
    refreshed = (await db_session.execute(
        select(BacktestRun).where(BacktestRun.id == run.id)
    )).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.key_metrics is not None
    assert "strategy" in refreshed.key_metrics
    assert refreshed.equity_curve is not None
    assert len(refreshed.equity_curve) > 0
    assert refreshed.monthly_returns_matrix is not None
    assert refreshed.drawdown_curve is not None
    assert refreshed.eoy_returns is not None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/coordinator/services/test_backtest_finalizer.py -v
```

Expected: `ModuleNotFoundError: No module named 'coordinator.services.backtest_finalizer'`.

- [ ] **Step 3: Implement the finalizer**

`coordinator/services/backtest_finalizer.py`:

```python
"""Backtest finalizer.

Reads the on-disk parquet pyramid produced by ParquetWriterThread,
computes the report payload (metrics, rolling series, monthly matrix,
EOY, drawdown curve, top-N drawdowns, daily equity mirror), and persists
to the BacktestRun row in a single transaction.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import quantstats as qs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.services.backtest_metrics_qs import (
    compute_all,
)

logger = logging.getLogger(__name__)

ROLLING_WINDOW_DAYS = 90


# ---- Resample / read helpers ----

def resample_to_daily(equity_native_path: Path) -> pd.DataFrame:
    table = pq.read_table(equity_native_path)
    df = table.to_pandas()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    df = df.set_index("timestamp")
    daily = df.resample("D").last().dropna()
    daily = daily.reset_index()
    return daily


def write_daily_parquet(daily_df: pd.DataFrame, out_path: Path) -> None:
    import pyarrow as pa
    table = pa.Table.from_pandas(daily_df, preserve_index=False)
    pq.write_table(table, out_path, compression="snappy")


# ---- Compute helpers ----

def _returns_from_pv(daily_df: pd.DataFrame) -> pd.Series:
    s = daily_df.set_index("timestamp")["portfolio_value"].pct_change().fillna(0)
    if hasattr(s.index, "tz") and s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    return s


def build_drawdown_curve(daily_df: pd.DataFrame) -> list[dict]:
    s = _returns_from_pv(daily_df)
    if s.empty:
        return []
    dd = qs.stats.to_drawdown_series(s)
    return [
        {"timestamp": ts.isoformat(), "drawdown_pct": float(v)}
        for ts, v in dd.items()
    ]


def build_monthly_matrix(daily_df: pd.DataFrame) -> dict:
    s = _returns_from_pv(daily_df)
    if s.empty:
        return {"years": [], "cells": []}
    monthly = qs.stats.monthly_returns(s)  # DataFrame: rows=years, cols=Jan..Dec
    years = [int(y) for y in monthly.index.tolist()]
    cells: list[list] = []
    month_cols = list(monthly.columns)
    for y in monthly.index:
        for m_idx, col in enumerate(month_cols, start=1):
            v = monthly.loc[y, col]
            if pd.notna(v):
                cells.append([int(y), int(m_idx), float(v)])
    return {"years": years, "cells": cells}


def build_eoy_returns(
    daily_df: pd.DataFrame, benchmark_pv: Optional[pd.Series],
) -> list[dict]:
    pv = daily_df.set_index("timestamp")["portfolio_value"]
    yearly = pv.groupby(pv.index.year).agg(["first", "last"])
    out: list[dict] = []
    for year, row in yearly.iterrows():
        strat_pct = float(row["last"] / row["first"] - 1.0) * 100.0
        bench_pct: Optional[float] = None
        if benchmark_pv is not None and not benchmark_pv.empty:
            yr_mask = (benchmark_pv.index.year == year)
            if yr_mask.any():
                yr_pv = benchmark_pv.loc[yr_mask]
                bench_pct = float(yr_pv.iloc[-1] / yr_pv.iloc[0] - 1.0) * 100.0
        multiplier = (strat_pct / bench_pct) if (bench_pct not in (None, 0.0)) else None
        out.append({
            "year": int(year),
            "strategy_pct": strat_pct,
            "benchmark_pct": bench_pct,
            "multiplier": multiplier,
            "won": (bench_pct is not None and strat_pct > bench_pct),
        })
    return out


def build_rolling_metrics(
    strat_returns: pd.Series,
    bench_returns: Optional[pd.Series],
    window: int = ROLLING_WINDOW_DAYS,
) -> dict:
    if strat_returns.empty:
        return {"window_days": window, "points": []}
    points: list[dict] = []
    rolling_sharpe = qs.stats.rolling_sharpe(strat_returns, rolling_period=window)
    rolling_sortino = qs.stats.rolling_sortino(strat_returns, rolling_period=window)
    rolling_vol = qs.stats.rolling_volatility(strat_returns, rolling_period=window)
    rolling_beta: Optional[pd.Series] = None
    if bench_returns is not None and not bench_returns.empty:
        joined = pd.concat([strat_returns, bench_returns], axis=1, join="inner").dropna()
        joined.columns = ["s", "b"]
        if len(joined) >= window:
            rolling_beta = (
                joined["s"].rolling(window).cov(joined["b"]) /
                joined["b"].rolling(window).var()
            )
    for ts in strat_returns.index:
        pt = {
            "timestamp": ts.isoformat(),
            "sharpe": _safe(rolling_sharpe, ts),
            "sortino": _safe(rolling_sortino, ts),
            "vol": _safe(rolling_vol, ts),
            "beta": _safe(rolling_beta, ts) if rolling_beta is not None else None,
        }
        points.append(pt)
    return {"window_days": window, "points": points}


def _safe(series: Optional[pd.Series], idx) -> Optional[float]:
    if series is None or idx not in series.index:
        return None
    v = series.loc[idx]
    if pd.isna(v) or np.isinf(v):
        return None
    return float(v)


def build_key_metrics(
    daily_df: pd.DataFrame,
    benchmark_daily_df: Optional[pd.DataFrame],
    initial_cash: float,
    risk_free_rate: float = 0.04,
) -> dict:
    strat = compute_all(_with_return(daily_df), trades=[], initial_cash=initial_cash, risk_free_rate=risk_free_rate)
    bench = {}
    if benchmark_daily_df is not None and not benchmark_daily_df.empty:
        bench_pv = benchmark_daily_df.set_index("timestamp")["portfolio_value"]
        bench_init = float(bench_pv.iloc[0])
        bench = compute_all(_with_return(benchmark_daily_df), trades=[], initial_cash=bench_init, risk_free_rate=risk_free_rate)
    # Drop date objects; JSON-safe
    strat = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in strat.items() if k != "drawdown_periods"}
    bench = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in bench.items() if k != "drawdown_periods"}
    return {"strategy": strat, "benchmark": bench}


def _with_return(daily_df: pd.DataFrame) -> pd.DataFrame:
    df = daily_df.copy().set_index("timestamp")
    df["return"] = df["portfolio_value"].pct_change().fillna(0)
    return df


# ---- Main entrypoint ----

async def finalize_run(
    *, run_id: str, run_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    benchmark_bar_df: Optional[pd.DataFrame],
) -> None:
    """Read native parquet, build all report payloads, persist to row."""
    from coordinator.database.models import BacktestRun

    eq_native = run_dir / "equity_native.parquet"
    if not eq_native.exists():
        raise FileNotFoundError(f"Missing native equity parquet at {eq_native}")

    daily_df = resample_to_daily(eq_native)
    write_daily_parquet(daily_df, run_dir / "equity_1day.parquet")

    bench_daily_df: Optional[pd.DataFrame] = None
    if benchmark_bar_df is not None and not benchmark_bar_df.empty:
        bench_pv_normalized = _normalize_benchmark(benchmark_bar_df, daily_df)
        bench_daily_df = pd.DataFrame({
            "timestamp": bench_pv_normalized.index,
            "portfolio_value": bench_pv_normalized.values,
        })
        write_daily_parquet(bench_daily_df, run_dir / "benchmark_1day.parquet")

    # Compute all payloads
    strat_returns = _returns_from_pv(daily_df)
    bench_returns = (
        _returns_from_pv(bench_daily_df) if bench_daily_df is not None else None
    )

    async with session_factory() as session:
        r = (await session.execute(
            select(BacktestRun).where(BacktestRun.id == run_id)
        )).scalar_one()

        r.equity_curve = [
            {"timestamp": ts.isoformat(), "portfolio_value": float(pv), "cash": float(c)}
            for ts, pv, c in zip(daily_df["timestamp"], daily_df["portfolio_value"], daily_df["cash"])
        ]
        r.benchmark_equity_curve = (
            [{"timestamp": ts.isoformat(), "value": float(v)}
             for ts, v in zip(bench_daily_df["timestamp"], bench_daily_df["portfolio_value"])]
            if bench_daily_df is not None else None
        )
        r.drawdown_curve = build_drawdown_curve(daily_df)
        r.monthly_returns_matrix = build_monthly_matrix(daily_df)
        r.eoy_returns = build_eoy_returns(
            daily_df,
            (bench_daily_df.set_index("timestamp")["portfolio_value"] if bench_daily_df is not None else None),
        )
        r.rolling_metrics = build_rolling_metrics(strat_returns, bench_returns)
        r.key_metrics = build_key_metrics(daily_df, bench_daily_df, initial_cash=r.initial_cash)
        # Top-10 drawdowns
        from coordinator.services.backtest_metrics_qs import top_n_drawdowns
        r.drawdown_periods = top_n_drawdowns(_with_return(daily_df), n=10)

        await session.commit()


def _normalize_benchmark(bench_bars: pd.DataFrame, daily_df: pd.DataFrame) -> pd.Series:
    """Normalize benchmark close to start at the strategy's initial portfolio value."""
    df = bench_bars.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    df = df.sort_values("timestamp").set_index("timestamp")
    initial = float(daily_df["portfolio_value"].iloc[0])
    first_close = float(df["close"].iloc[0])
    if first_close == 0:
        return pd.Series(dtype=float)
    return (df["close"] / first_close) * initial
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/coordinator/services/test_backtest_finalizer.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_finalizer.py tests/coordinator/services/test_backtest_finalizer.py
git commit -m "feat(backtest): finalizer — resample, compute report payloads, persist row"
```

---

## Task 8: Wire the runner to the new pipeline

**Files:**
- Modify: `coordinator/services/backtest_runner.py` — replace `_RunObserver` with `ChunkingObserver`, spawn `ParquetWriterThread`, call `finalize_run` instead of inline metrics, drop the `generate_tearsheet` call.
- Modify: `tests/coordinator/services/test_backtest_runner.py` — update the mock to match the new flow.
- Delete: `coordinator/services/backtest_tearsheet.py`.

This is the load-bearing change. After this task, runs use the streaming pipeline end-to-end.

- [ ] **Step 1: Read the current runner.run, then rewrite the engine-run + finalize block**

In `coordinator/services/backtest_runner.py`, locate the `_RunObserver` class (lines ~102-140) — leave it alone for now (deletion is in Step 5).

Locate the engine-run section that today looks like:

```python
            observer = _RunObserver()
            cancel = CancelToken()

            loop = asyncio.get_running_loop()
            pump = asyncio.create_task(self._progress_pump(run_id, observer))
            try:
                await loop.run_in_executor(
                    None,
                    functools.partial(
                        BacktestEngine().run,
                        algorithm=algorithm, ctx=ctx, clock_series=clock_series,
                        ...
                        progress_callback=lambda p: setattr(observer, "progress", p),
                    ),
                )
            finally:
                pump.cancel()
                ...
```

Replace this entire block plus the metrics + tearsheet + final-DB-write below it (i.e. everything from `observer = _RunObserver()` through `r.progress_pct = 1.0` / `await session.commit()`) with:

```python
            from queue import Queue
            from coordinator.services.backtest_writer import (
                ChunkingObserver, ParquetWriterThread,
            )
            from coordinator.services.backtest_finalizer import finalize_run

            run_dir = Path("data/backtests") / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            equity_native_path = run_dir / "equity_native.parquet"
            trades_path = run_dir / "trades.parquet"

            chunk_queue: Queue = Queue(maxsize=8)
            observer = ChunkingObserver(queue=chunk_queue, clock_series=clock_series)
            writer = ParquetWriterThread(
                queue=chunk_queue, equity_path=equity_native_path, trades_path=trades_path,
            )
            writer.start()

            cancel = CancelToken()
            loop = asyncio.get_running_loop()
            pump = asyncio.create_task(self._progress_pump(run_id, observer))
            try:
                await loop.run_in_executor(
                    None,
                    functools.partial(
                        BacktestEngine().run,
                        algorithm=algorithm, ctx=ctx, clock_series=clock_series,
                        clock_timeframe=clock_tf, clock_source=clock_source,
                        clock_symbol=clock_symbol,
                        slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                        initial_cash=initial_cash, observer=observer,
                        cancel_token=cancel,
                        progress_callback=lambda p: setattr(observer, "progress", p),
                    ),
                )
            finally:
                pump.cancel()
                try:
                    await pump
                except asyncio.CancelledError:
                    pass
                # Signal writer to drain & exit
                chunk_queue.put(None)
                writer.join(timeout=30)

            if observer.writer_error or writer.error:
                raise (writer.error or observer.writer_error)

            # Load benchmark bars for finalize (if configured)
            benchmark_bar_df = None
            async with self._sf() as session:
                r = (await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )).scalar_one()
                bench_symbol = r.benchmark_symbol
                bench_source = r.benchmark_source
            if bench_symbol and bench_source:
                bdf = self._ds.load_market_data(bench_source, bench_symbol, "1day")
                if bdf is not None and not bdf.empty:
                    benchmark_bar_df = bdf

            # Finalize: resample, compute metrics, persist row
            await finalize_run(
                run_id=run_id, run_dir=run_dir,
                session_factory=self._sf, benchmark_bar_df=benchmark_bar_df,
            )

            # Mark complete + clear progress fields
            async with self._sf() as session:
                r = (await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )).scalar_one()
                r.status = "completed"
                r.completed_at = datetime.now(timezone.utc)
                r.progress_message = "Backtest complete"
                r.progress_pct = 1.0
                r.download_ids = download_ids
                await session.commit()
```

Update the `_progress_pump` to also write the daily aggregate from the observer. Find:

```python
                    r.progress_pct = float(observer.progress)
```

Replace with:

```python
                    r.progress_pct = float(observer.progress)
                    if hasattr(observer, "daily_aggregate_snapshot"):
                        snap = observer.daily_aggregate_snapshot()
                        if snap:
                            r.equity_curve = snap
```

Remove the import of `from coordinator.services.backtest_tearsheet import generate_tearsheet` if present.

- [ ] **Step 2: Delete the old `_RunObserver` class**

Now safe to remove. In the runner, delete the `class _RunObserver:` block (lines ~102-140) entirely.

- [ ] **Step 3: Run the runner test**

```bash
pytest tests/coordinator/services/test_backtest_runner.py -v
```

Expected: pass. The test mocks `BacktestEngine`, so the writer thread will see no chunks and exit cleanly when the sentinel is pushed.

- [ ] **Step 4: Delete `backtest_tearsheet.py` and any test for it**

```bash
rm coordinator/services/backtest_tearsheet.py
[ -f tests/coordinator/services/test_backtest_tearsheet.py ] && rm tests/coordinator/services/test_backtest_tearsheet.py
```

- [ ] **Step 5: Run the full backtest suite**

```bash
pytest tests/coordinator/services/ tests/coordinator/test_backtest_runs_api.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/backtest_runner.py tests/coordinator/services/test_backtest_runner.py
git rm coordinator/services/backtest_tearsheet.py 2>/dev/null
git rm tests/coordinator/services/test_backtest_tearsheet.py 2>/dev/null
git commit -m "feat(backtest): runner uses ChunkingObserver + writer thread + finalizer; drop tearsheet step"
```

---

## Task 9: Extend `DELETE /api/backtest-runs/{id}` to clean the run directory

**Files:**
- Modify: `coordinator/api/routes/backtest_runs.py:212-227` — the `delete_run` handler.
- Modify: `tests/coordinator/test_backtest_runs_api.py` — add cleanup test.

The handler today only unlinks the single tearsheet HTML. The new layout has multiple parquet files; switch to `shutil.rmtree`.

- [ ] **Step 1: Write failing test**

Append to `tests/coordinator/test_backtest_runs_api.py`:

```python
@pytest.mark.asyncio
async def test_delete_run_removes_run_directory(test_client, db_session, tmp_path, monkeypatch):
    """DELETE should rmtree data/backtests/{run_id}/."""
    from coordinator.database.models import Algorithm, BacktestRun
    monkeypatch.chdir(tmp_path)
    algo = Algorithm(name="t", repo_url="https://github.com/x/y", install_status="installed")
    db_session.add(algo); await db_session.flush()
    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        initial_cash=100.0,
    )
    db_session.add(run); await db_session.commit()

    run_dir = tmp_path / "data" / "backtests" / run.id
    run_dir.mkdir(parents=True)
    (run_dir / "equity_native.parquet").write_bytes(b"stub")

    resp = await test_client.delete(f"/api/backtest-runs/{run.id}")
    assert resp.status_code == 204
    assert not run_dir.exists()
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/coordinator/test_backtest_runs_api.py::test_delete_run_removes_run_directory -v
```

Expected: fail (directory still exists).

- [ ] **Step 3: Update the handler**

In `coordinator/api/routes/backtest_runs.py`, replace the `delete_run` body:

```python
@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str, db: AsyncSession = Depends(get_db)):
    import shutil
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    container = get_container()
    if hasattr(container, "cancel_backtest"):
        container.cancel_backtest(run_id)
    # Remove the entire run output directory
    run_dir = Path("data/backtests") / run_id
    try:
        shutil.rmtree(run_dir, ignore_errors=True)
    except Exception:
        logger.exception("Failed to remove run dir %s", run_dir)
    await db.delete(r)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/coordinator/test_backtest_runs_api.py -v
```

Expected: all pass including the new cleanup test.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/backtest_runs.py tests/coordinator/test_backtest_runs_api.py
git commit -m "feat(backtest): DELETE cleans up the run output directory (rmtree)"
```

---

## Task 10: `GET /api/backtest-runs/{id}/report`

**Files:**
- Modify: `coordinator/api/routes/backtest_runs.py` — add the route.
- Create: `tests/coordinator/test_backtest_report_api.py` — test the report shape.

Returns the row plus the new fields. No file I/O.

- [ ] **Step 1: Write failing test**

`tests/coordinator/test_backtest_report_api.py`:

```python
"""Tests for the /report and /equity endpoints."""
from datetime import datetime, timezone
import pytest


@pytest.mark.asyncio
async def test_get_report_returns_all_payload_fields(test_client, db_session):
    from coordinator.database.models import Algorithm, BacktestRun
    algo = Algorithm(name="t", repo_url="https://github.com/x/y", install_status="installed")
    db_session.add(algo); await db_session.flush()
    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_cash=100.0,
        config_overrides={"x": 1},
        key_metrics={"strategy": {"sharpe_ratio": 1.4}, "benchmark": {"sharpe_ratio": 0.7}},
        equity_curve=[{"timestamp": "2024-01-01T00:00:00", "portfolio_value": 100.0, "cash": 100.0}],
        benchmark_equity_curve=[{"timestamp": "2024-01-01T00:00:00", "value": 100.0}],
        drawdown_curve=[{"timestamp": "2024-01-01T00:00:00", "drawdown_pct": 0.0}],
        rolling_metrics={"window_days": 90, "points": []},
        monthly_returns_matrix={"years": [2024], "cells": []},
        eoy_returns=[{"year": 2024, "strategy_pct": 0.0, "benchmark_pct": 0.0, "multiplier": None, "won": False}],
        drawdown_periods=[],
    )
    db_session.add(run); await db_session.commit()

    resp = await test_client.get(f"/api/backtest-runs/{run.id}/report")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "id", "status", "config_overrides", "key_metrics", "equity_curve",
        "benchmark_equity_curve", "drawdown_curve", "rolling_metrics",
        "monthly_returns_matrix", "eoy_returns", "drawdown_periods",
    ):
        assert key in body, f"missing key: {key}"


@pytest.mark.asyncio
async def test_get_report_404_for_missing_run(test_client):
    resp = await test_client.get("/api/backtest-runs/nope/report")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/coordinator/test_backtest_report_api.py -v
```

Expected: 404 returned for both because the route doesn't exist.

- [ ] **Step 3: Add the route**

In `coordinator/api/routes/backtest_runs.py`, add (right after the `get_run` function around line 130):

```python
@router.get("/{run_id}/report")
async def get_report(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    return {
        "id": r.id,
        "algorithm_id": r.algorithm_id,
        "status": r.status,
        "date_range_start": r.date_range_start.isoformat() if r.date_range_start else None,
        "date_range_end": r.date_range_end.isoformat() if r.date_range_end else None,
        "initial_cash": r.initial_cash,
        "config_overrides": r.config_overrides,
        "benchmark_symbol": r.benchmark_symbol,
        "benchmark_source": r.benchmark_source,
        "key_metrics": r.key_metrics,
        "equity_curve": r.equity_curve,
        "benchmark_equity_curve": r.benchmark_equity_curve,
        "drawdown_curve": r.drawdown_curve,
        "rolling_metrics": r.rolling_metrics,
        "monthly_returns_matrix": r.monthly_returns_matrix,
        "eoy_returns": r.eoy_returns,
        "drawdown_periods": r.drawdown_periods,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/coordinator/test_backtest_report_api.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/backtest_runs.py tests/coordinator/test_backtest_report_api.py
git commit -m "feat(backtest): GET /api/backtest-runs/{id}/report — full report payload"
```

---

## Task 11: `GET /api/backtest-runs/{id}/equity` — windowed parquet read

**Files:**
- Modify: `coordinator/api/routes/backtest_runs.py` — add the route.
- Modify: `tests/coordinator/test_backtest_report_api.py` — add equity-window tests.

Reads the per-resolution parquet file, slices to the window, returns ≤5000 points.

- [ ] **Step 1: Write failing test**

Append to `tests/coordinator/test_backtest_report_api.py`:

```python
import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd
from pathlib import Path


@pytest.mark.asyncio
async def test_equity_endpoint_returns_window_at_requested_resolution(
    test_client, db_session, tmp_path, monkeypatch,
):
    from coordinator.database.models import Algorithm, BacktestRun
    monkeypatch.chdir(tmp_path)
    algo = Algorithm(name="t", repo_url="https://github.com/x/y", install_status="installed")
    db_session.add(algo); await db_session.flush()
    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 1, 31, tzinfo=timezone.utc),
        initial_cash=100.0,
    )
    db_session.add(run); await db_session.commit()

    run_dir = tmp_path / "data" / "backtests" / run.id
    run_dir.mkdir(parents=True)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-02", periods=10, freq="D"),
        "portfolio_value": [100.0 + i for i in range(10)],
        "cash": [100.0] * 10,
    })
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), run_dir / "equity_1day.parquet")

    resp = await test_client.get(
        f"/api/backtest-runs/{run.id}/equity"
        "?from=2024-01-04T00:00:00&to=2024-01-07T00:00:00&resolution=1day"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "1day"
    # 4 days inclusive
    assert len(body["items"]) == 4
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/coordinator/test_backtest_report_api.py -v
```

Expected: new test fails with 404.

- [ ] **Step 3: Add the route**

Append to `coordinator/api/routes/backtest_runs.py`:

```python
@router.get("/{run_id}/equity")
async def get_equity_window(
    run_id: str,
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    resolution: str = Query("auto", regex="^(1min|1hour|1day|auto)$"),
    db: AsyncSession = Depends(get_db),
):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")

    run_dir = Path("data/backtests") / run_id
    chosen = resolution
    if resolution == "auto":
        # Pick the highest resolution available that produces ≤5000 points in the window.
        days = max(1, (to - from_).days)
        if days < 3 and (run_dir / "equity_1min.parquet").exists():
            chosen = "1min"
        elif days < 60 and (run_dir / "equity_1hour.parquet").exists():
            chosen = "1hour"
        else:
            chosen = "1day"

    pq_path = run_dir / f"equity_{chosen}.parquet"
    if not pq_path.exists():
        raise HTTPException(404, detail=f"No {chosen} parquet for this run")
    import pyarrow.parquet as _pq
    df = _pq.read_table(pq_path).to_pandas()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    from_naive = pd.Timestamp(from_).tz_localize(None) if pd.Timestamp(from_).tz is not None else pd.Timestamp(from_)
    to_naive = pd.Timestamp(to).tz_localize(None) if pd.Timestamp(to).tz is not None else pd.Timestamp(to)
    mask = (df["timestamp"] >= from_naive) & (df["timestamp"] <= to_naive)
    sliced = df.loc[mask]
    return {
        "resolution": chosen,
        "items": [
            {"ts": row["timestamp"].isoformat(), "portfolio_value": float(row["portfolio_value"]), "cash": float(row.get("cash", 0.0))}
            for _, row in sliced.iterrows()
        ],
    }
```

Add `import pandas as pd` to the imports if not already present.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/coordinator/test_backtest_report_api.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/backtest_runs.py tests/coordinator/test_backtest_report_api.py
git commit -m "feat(backtest): GET /api/backtest-runs/{id}/equity — windowed parquet read"
```

---

## Task 12: Remove `/tearsheet` and `/equity-curve` routes

**Files:**
- Modify: `coordinator/api/routes/backtest_runs.py` — delete `get_tearsheet` and `get_equity_curve` route handlers.

- [ ] **Step 1: Delete the routes**

In `coordinator/api/routes/backtest_runs.py`, delete the entire `@router.get("/{run_id}/tearsheet")` handler (around lines 200-209) and the entire `@router.get("/{run_id}/equity-curve")` handler (around lines 133-183).

Also remove the `from fastapi.responses import FileResponse` import if no longer used.

- [ ] **Step 2: Run the API test suite to verify the deletes are non-breaking**

```bash
pytest tests/coordinator/test_backtest_runs_api.py tests/coordinator/test_backtest_report_api.py -v
```

Expected: all pass. (Any test that hit the old routes is already gone or passing on the new shape.)

- [ ] **Step 3: Commit**

```bash
git add coordinator/api/routes/backtest_runs.py
git commit -m "chore(backtest): remove /tearsheet and /equity-curve routes (replaced by /report and /equity)"
```

---

## Task 13: Frontend types, API client methods, react-query hooks

**Files:**
- Modify: `dashboard/src/types/index.ts` — add report payload types.
- Modify: `dashboard/src/api/client.ts` — add `getBacktestReport`, `getBacktestEquityWindow`; remove `getBacktestEquityCurve`.
- Modify: `dashboard/src/api/hooks.ts` — add `useBacktestReport`, `useBacktestEquityWindow`; remove `useBacktestEquityCurve`.

These three changes are tightly coupled (types referenced by client, client used by hooks); committed as one task.

- [ ] **Step 1: Add types**

In `dashboard/src/types/index.ts`, append:

```typescript
// ── Backtest Report ──

export interface BacktestKeyMetrics {
  total_return: number;
  cagr: number;
  volatility: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown: number;
  romad: number;
  longest_drawdown_days: number;
  // Tail risk
  daily_var?: number;
  daily_cvar?: number;
  skew?: number;
  kurtosis?: number;
  // Period returns
  ytd?: number; "1y"?: number; "3y"?: number;
  // Distribution
  best_day?: number; worst_day?: number;
  best_month?: number; worst_month?: number;
  // Win rates
  time_in_market?: number; win_days?: number; win_month?: number;
  // vs benchmark (strategy only)
  beta?: number; alpha?: number; correlation?: number;
  [key: string]: number | undefined;
}

export interface BacktestRollingPoint {
  timestamp: string;
  sharpe: number | null;
  sortino: number | null;
  vol: number | null;
  beta: number | null;
}

export interface BacktestReport {
  id: string;
  algorithm_id: string;
  status: string;
  date_range_start: string | null;
  date_range_end: string | null;
  initial_cash: number;
  config_overrides: Record<string, unknown> | null;
  benchmark_symbol: string | null;
  benchmark_source: string | null;
  key_metrics: { strategy: BacktestKeyMetrics; benchmark: BacktestKeyMetrics } | null;
  equity_curve: { timestamp: string; portfolio_value: number; cash?: number }[] | null;
  benchmark_equity_curve: { timestamp: string; value: number }[] | null;
  drawdown_curve: { timestamp: string; drawdown_pct: number }[] | null;
  rolling_metrics: { window_days: number; points: BacktestRollingPoint[] } | null;
  monthly_returns_matrix: { years: number[]; cells: [number, number, number][] } | null;
  eoy_returns: {
    year: number; strategy_pct: number; benchmark_pct: number | null;
    multiplier: number | null; won: boolean;
  }[] | null;
  drawdown_periods: {
    start: string; trough: string; recovered: string | null;
    depth: number; days: number;
  }[] | null;
}

export interface BacktestEquityWindow {
  resolution: "1min" | "1hour" | "1day";
  items: { ts: string; portfolio_value: number; cash: number }[];
}
```

- [ ] **Step 2: Add client methods**

In `dashboard/src/api/client.ts`, around line 696, replace the `deleteBacktestRun` method block with:

```typescript
  getBacktestReport(id: string): Promise<BacktestReport> {
    return request<BacktestReport>(`/api/backtest-runs/${id}/report`);
  },
  getBacktestEquityWindow(
    id: string, params: { from: string; to: string; resolution?: "1min" | "1hour" | "1day" | "auto" },
  ): Promise<BacktestEquityWindow> {
    const qs = new URLSearchParams();
    qs.set("from", params.from);
    qs.set("to", params.to);
    qs.set("resolution", params.resolution ?? "auto");
    return request<BacktestEquityWindow>(`/api/backtest-runs/${id}/equity?${qs.toString()}`);
  },
  deleteBacktestRun(id: string): Promise<void> {
    return request<void>(`/api/backtest-runs/${id}`, { method: "DELETE" });
  },
```

Add `BacktestReport, BacktestEquityWindow` to the type imports at the top of the file.

Delete the existing `getBacktestEquityCurve` method.

- [ ] **Step 3: Add hooks; remove the old equity-curve hook**

In `dashboard/src/api/hooks.ts`:

Delete the `useBacktestEquityCurve` function entirely.

Add (near the other backtest hooks):

```typescript
export function useBacktestReport(
  id: string,
  opts?: { refetchInterval?: number },
) {
  return useQuery({
    queryKey: ["backtest-report", id] as const,
    queryFn: () => api.getBacktestReport(id),
    enabled: !!id,
    refetchInterval: opts?.refetchInterval,
  });
}

export function useBacktestEquityWindow(
  id: string,
  params: { from: string; to: string; resolution?: "1min" | "1hour" | "1day" | "auto" } | null,
) {
  return useQuery({
    queryKey: ["backtest-equity-window", id, params] as const,
    queryFn: () => api.getBacktestEquityWindow(id, params!),
    enabled: !!id && params != null,
    staleTime: 60_000,
  });
}
```

- [ ] **Step 4: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok` with no errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/types/index.ts dashboard/src/api/client.ts dashboard/src/api/hooks.ts
git commit -m "feat(backtest-ui): types + client + hooks for /report and /equity endpoints"
```

---

## Task 14: `KpiCard` component

**Files:**
- Create: `dashboard/src/components/report/KpiCard.tsx`
- Create: `dashboard/src/components/report/KpiCard.test.tsx`

Two variants: hero (large) and sub (smaller, in a row). Both render `{label, value, hint?}`.

- [ ] **Step 1: Write failing test**

`dashboard/src/components/report/KpiCard.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { KpiCard } from "./KpiCard";

describe("KpiCard", () => {
  it("renders label and value", () => {
    render(<KpiCard label="CAGR" value="32.4%" />);
    expect(screen.getByText("CAGR")).toBeInTheDocument();
    expect(screen.getByText("32.4%")).toBeInTheDocument();
  });

  it("renders hero variant with larger styling", () => {
    const { container } = render(<KpiCard label="CAGR" value="32.4%" variant="hero" />);
    expect(container.querySelector(".text-3xl, .text-4xl")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify failure**

```bash
cd dashboard && npm test -- src/components/report/KpiCard.test.tsx
```

Expected: fails because the file doesn't exist.

- [ ] **Step 3: Implement the component**

`dashboard/src/components/report/KpiCard.tsx`:

```typescript
interface KpiCardProps {
  label: string;
  value: string;
  hint?: string;
  variant?: "hero" | "sub";
}

export function KpiCard({ label, value, hint, variant = "sub" }: KpiCardProps) {
  const valueClass = variant === "hero" ? "text-4xl font-bold text-white" : "text-xl font-semibold text-white";
  const wrapClass = variant === "hero"
    ? "bg-gray-900 border border-gray-800 rounded-lg p-5"
    : "bg-gray-900 border border-gray-800 rounded p-3";
  return (
    <div className={wrapClass}>
      <div className="text-[10px] uppercase tracking-wide text-gray-500" title={hint}>{label}</div>
      <div className={valueClass}>{value}</div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd dashboard && npm test -- src/components/report/KpiCard.test.tsx
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/report/KpiCard.tsx dashboard/src/components/report/KpiCard.test.tsx
git commit -m "feat(backtest-ui): KpiCard component (hero + sub variants)"
```

---

## Task 15: Side tables — `ParametersTable`, `EoyTable`, `DrawdownsTable`

**Files:**
- Create: `dashboard/src/components/report/ParametersTable.tsx`
- Create: `dashboard/src/components/report/EoyTable.tsx`
- Create: `dashboard/src/components/report/DrawdownsTable.tsx`

Three small read-only tables. Pure renderers from the report payload.

- [ ] **Step 1: Implement `ParametersTable`**

`dashboard/src/components/report/ParametersTable.tsx`:

```typescript
interface Props {
  params: Record<string, unknown> | null;
}

export function ParametersTable({ params }: Props) {
  const entries = params ? Object.entries(params) : [];
  if (entries.length === 0) return null;
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <h3 className="text-sm font-semibold text-gray-300 mb-2">Parameters Used</h3>
      <table className="w-full text-xs">
        <thead className="text-gray-500">
          <tr><th className="text-left py-1">Parameter</th><th className="text-right py-1">Value</th></tr>
        </thead>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k} className="border-t border-gray-800">
              <td className="py-1 text-gray-300 font-mono">{k}</td>
              <td className="py-1 text-right text-gray-300 font-mono">
                {v === null || v === undefined ? "—" : String(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Implement `EoyTable`**

`dashboard/src/components/report/EoyTable.tsx`:

```typescript
interface EoyRow {
  year: number;
  strategy_pct: number;
  benchmark_pct: number | null;
  multiplier: number | null;
  won: boolean;
}

interface Props { rows: EoyRow[] | null; }

export function EoyTable({ rows }: Props) {
  if (!rows || rows.length === 0) return null;
  const fmt = (v: number | null) => (v === null ? "—" : `${v.toFixed(2)}%`);
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <h3 className="text-sm font-semibold text-gray-300 mb-2">EOY Returns vs Benchmark</h3>
      <table className="w-full text-xs">
        <thead className="text-gray-500">
          <tr>
            <th className="text-left py-1">Year</th>
            <th className="text-right py-1">Strategy</th>
            <th className="text-right py-1">Benchmark</th>
            <th className="text-right py-1">×</th>
            <th className="text-right py-1">Won</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.year} className="border-t border-gray-800">
              <td className="py-1 text-gray-300">{r.year}</td>
              <td className="py-1 text-right text-gray-300">{fmt(r.strategy_pct)}</td>
              <td className="py-1 text-right text-gray-400">{fmt(r.benchmark_pct)}</td>
              <td className="py-1 text-right text-gray-400">{r.multiplier === null ? "—" : r.multiplier.toFixed(2)}</td>
              <td className={`py-1 text-right ${r.won ? "text-green-400" : "text-red-400"}`}>
                {r.won ? "+" : "−"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Implement `DrawdownsTable`**

`dashboard/src/components/report/DrawdownsTable.tsx`:

```typescript
interface DrawdownRow {
  start: string; trough: string; recovered: string | null;
  depth: number; days: number;
}

interface Props { rows: DrawdownRow[] | null; }

export function DrawdownsTable({ rows }: Props) {
  if (!rows || rows.length === 0) return null;
  const fmtDate = (s: string | null) => (s ? s.split("T")[0] : "ongoing");
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <h3 className="text-sm font-semibold text-gray-300 mb-2">Worst-{rows.length} Drawdowns</h3>
      <table className="w-full text-xs">
        <thead className="text-gray-500">
          <tr>
            <th className="text-left py-1">Started</th>
            <th className="text-left py-1">Recovered</th>
            <th className="text-right py-1">Depth</th>
            <th className="text-right py-1">Days</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-gray-800">
              <td className="py-1 text-gray-300 font-mono">{fmtDate(r.start)}</td>
              <td className="py-1 text-gray-400 font-mono">{fmtDate(r.recovered)}</td>
              <td className="py-1 text-right text-red-400">{(r.depth * 100).toFixed(2)}%</td>
              <td className="py-1 text-right text-gray-400">{r.days}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/report/ParametersTable.tsx dashboard/src/components/report/EoyTable.tsx dashboard/src/components/report/DrawdownsTable.tsx
git commit -m "feat(backtest-ui): side tables — Parameters / EOY / Worst Drawdowns"
```

---

## Task 16: `MetricsTable` — Strategy vs Benchmark grouped table

**Files:**
- Create: `dashboard/src/components/report/MetricsTable.tsx`

8 grouped sections, ~28 metrics. Strategy and Benchmark side-by-side. The vs-Benchmark group has em-dash in the benchmark column.

- [ ] **Step 1: Implement the component**

`dashboard/src/components/report/MetricsTable.tsx`:

```typescript
import type { BacktestKeyMetrics } from "../../types";

type Fmt = "pct" | "num" | "int";

interface Row {
  label: string;
  key: keyof BacktestKeyMetrics | string;
  fmt: Fmt;
  strategyOnly?: boolean;
}

interface Group {
  title: string;
  rows: Row[];
}

const GROUPS: Group[] = [
  { title: "Returns", rows: [
    { label: "Total Return", key: "total_return", fmt: "pct" },
    { label: "CAGR (Annual)", key: "cagr", fmt: "pct" },
    { label: "Volatility (ann.)", key: "volatility", fmt: "pct" },
  ]},
  { title: "Risk-adjusted", rows: [
    { label: "Sharpe", key: "sharpe_ratio", fmt: "num" },
    { label: "Sortino", key: "sortino_ratio", fmt: "num" },
    { label: "Omega", key: "omega", fmt: "num" },
  ]},
  { title: "Drawdown", rows: [
    { label: "Max Drawdown", key: "max_drawdown", fmt: "pct" },
    { label: "Longest DD Days", key: "longest_drawdown_days", fmt: "int" },
    { label: "Avg Drawdown", key: "avg_drawdown", fmt: "pct" },
    { label: "Avg DD Days", key: "avg_drawdown_days", fmt: "int" },
    { label: "Ulcer Index", key: "ulcer_index", fmt: "num" },
  ]},
  { title: "Tail risk", rows: [
    { label: "Daily VaR (95%)", key: "daily_var", fmt: "pct" },
    { label: "Daily cVaR", key: "daily_cvar", fmt: "pct" },
    { label: "Skew", key: "skew", fmt: "num" },
    { label: "Kurtosis", key: "kurtosis", fmt: "num" },
  ]},
  { title: "Period returns", rows: [
    { label: "YTD", key: "ytd", fmt: "pct" },
    { label: "1Y", key: "1y", fmt: "pct" },
    { label: "3Y (annualized)", key: "3y", fmt: "pct" },
  ]},
  { title: "Distribution", rows: [
    { label: "Best Day", key: "best_day", fmt: "pct" },
    { label: "Worst Day", key: "worst_day", fmt: "pct" },
    { label: "Best Month", key: "best_month", fmt: "pct" },
    { label: "Worst Month", key: "worst_month", fmt: "pct" },
  ]},
  { title: "Win rates", rows: [
    { label: "Time in Market", key: "time_in_market", fmt: "pct" },
    { label: "Win Days %", key: "win_days", fmt: "pct" },
    { label: "Win Month %", key: "win_month", fmt: "pct" },
  ]},
  { title: "vs Benchmark", rows: [
    { label: "Beta", key: "beta", fmt: "num", strategyOnly: true },
    { label: "Alpha", key: "alpha", fmt: "num", strategyOnly: true },
    { label: "Correlation", key: "correlation", fmt: "num", strategyOnly: true },
  ]},
];

function fmtValue(v: number | undefined | null, fmt: Fmt): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  if (fmt === "pct") return `${(v * 100).toFixed(2)}%`;
  if (fmt === "int") return Math.round(v).toString();
  return v.toFixed(2);
}

interface Props {
  strategy: BacktestKeyMetrics | undefined;
  benchmark: BacktestKeyMetrics | undefined;
}

export function MetricsTable({ strategy, benchmark }: Props) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded">
      <div className="px-3 py-2 border-b border-gray-800 text-sm font-semibold text-gray-300">
        Key Performance Metrics
      </div>
      <table className="w-full text-sm">
        <thead className="bg-gray-800 text-xs uppercase text-gray-400">
          <tr>
            <th className="text-left p-2">Metric</th>
            <th className="text-right p-2">Strategy</th>
            <th className="text-right p-2">Benchmark</th>
          </tr>
        </thead>
        <tbody>
          {GROUPS.map((g) => (
            <>
              <tr key={`g-${g.title}`} className="bg-gray-800/40">
                <td colSpan={3} className="px-2 py-1 text-[10px] uppercase text-gray-500">
                  {g.title}
                </td>
              </tr>
              {g.rows.map((row) => {
                const sv = strategy ? strategy[row.key as keyof BacktestKeyMetrics] : undefined;
                const bv = benchmark ? benchmark[row.key as keyof BacktestKeyMetrics] : undefined;
                return (
                  <tr key={row.label} className="border-t border-gray-800">
                    <td className="p-2 text-gray-300">{row.label}</td>
                    <td className="p-2 text-right text-gray-200">{fmtValue(sv, row.fmt)}</td>
                    <td className="p-2 text-right text-gray-400">
                      {row.strategyOnly ? "—" : fmtValue(bv, row.fmt)}
                    </td>
                  </tr>
                );
              })}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/report/MetricsTable.tsx
git commit -m "feat(backtest-ui): MetricsTable — Strategy vs Benchmark, 8 grouped sections"
```

---

## Task 17: `MonthlyHeatmap` component

**Files:**
- Create: `dashboard/src/components/report/MonthlyHeatmap.tsx`

CSS-grid year × month grid, colored by return. Tooltip with exact value.

- [ ] **Step 1: Implement the component**

`dashboard/src/components/report/MonthlyHeatmap.tsx`:

```typescript
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

interface Props {
  matrix: { years: number[]; cells: [number, number, number][] } | null;
}

function colorFor(pct: number): string {
  // pct is fraction, e.g. 0.05 = +5%
  const p = Math.max(-0.10, Math.min(0.10, pct));
  if (p > 0) {
    const intensity = Math.round((p / 0.10) * 200) + 30;
    return `rgb(0, ${intensity}, 0)`;
  }
  if (p < 0) {
    const intensity = Math.round((-p / 0.10) * 200) + 30;
    return `rgb(${intensity}, 0, 0)`;
  }
  return "rgb(40, 40, 40)";
}

export function MonthlyHeatmap({ matrix }: Props) {
  if (!matrix || matrix.years.length === 0) {
    return <div className="text-xs text-gray-500 p-4">No monthly data.</div>;
  }
  const lookup = new Map<string, number>();
  for (const [y, m, v] of matrix.cells) lookup.set(`${y}-${m}`, v);
  return (
    <div className="overflow-auto">
      <table className="text-[10px] border-separate" style={{ borderSpacing: 1 }}>
        <thead>
          <tr>
            <th className="text-gray-500 px-1 text-left">Year</th>
            {MONTHS.map((m) => (
              <th key={m} className="text-gray-500 px-1">{m}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.years.map((y) => (
            <tr key={y}>
              <td className="text-gray-400 px-1">{y}</td>
              {MONTHS.map((_, idx) => {
                const v = lookup.get(`${y}-${idx + 1}`);
                if (v === undefined) {
                  return <td key={idx} className="w-8 h-6 bg-gray-800/30" />;
                }
                return (
                  <td
                    key={idx}
                    className="w-8 h-6 text-center text-white"
                    style={{ background: colorFor(v) }}
                    title={`${y}-${String(idx + 1).padStart(2, "0")}: ${(v * 100).toFixed(2)}%`}
                  >
                    {(v * 100).toFixed(0)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/report/MonthlyHeatmap.tsx
git commit -m "feat(backtest-ui): MonthlyHeatmap — year × month CSS-grid component"
```

---

## Task 18: `ReturnsDistributionSlot` — view toggle (heatmap / EOY bar / histogram / scatter)

**Files:**
- Create: `dashboard/src/components/report/ReturnsDistributionSlot.tsx`

Wraps `MonthlyHeatmap` plus three other views. The EOY bar and histogram and scatter views use `lightweight-charts`.

- [ ] **Step 1: Implement the component**

`dashboard/src/components/report/ReturnsDistributionSlot.tsx`:

```typescript
import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, ColorType, type IChartApi } from "lightweight-charts";
import { MonthlyHeatmap } from "./MonthlyHeatmap";
import type { BacktestReport } from "../../types";

type View = "heatmap" | "eoy" | "histogram" | "scatter";

interface Props {
  report: BacktestReport;
}

export function ReturnsDistributionSlot({ report }: Props) {
  const [view, setView] = useState<View>("heatmap");
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-300">Returns distribution</h3>
        <div className="flex gap-1 text-xs">
          {(["heatmap", "eoy", "histogram", "scatter"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2 py-1 rounded ${view === v ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}
            >{v}</button>
          ))}
        </div>
      </div>
      {view === "heatmap" && <MonthlyHeatmap matrix={report.monthly_returns_matrix} />}
      {view === "eoy" && <EoyBar rows={report.eoy_returns} />}
      {view === "histogram" && <Histogram equity={report.equity_curve} />}
      {view === "scatter" && <Scatter equity={report.equity_curve} />}
    </div>
  );
}

function dailyReturnsFromEquity(equity: { timestamp: string; portfolio_value: number }[] | null): { ts: string; ret: number }[] {
  if (!equity || equity.length < 2) return [];
  const out: { ts: string; ret: number }[] = [];
  for (let i = 1; i < equity.length; i++) {
    const prev = equity[i - 1].portfolio_value;
    if (prev === 0) continue;
    out.push({ ts: equity[i].timestamp, ret: equity[i].portfolio_value / prev - 1 });
  }
  return out;
}

function EoyBar({ rows }: { rows: BacktestReport["eoy_returns"] }) {
  if (!rows || rows.length === 0) return <div className="text-xs text-gray-500 p-4">No EOY data.</div>;
  return (
    <div className="space-y-1">
      {rows.map((r) => (
        <div key={r.year} className="flex items-center gap-2">
          <span className="w-12 text-xs text-gray-400">{r.year}</span>
          <div className="flex-1 h-4 bg-gray-800 rounded overflow-hidden flex">
            <div
              className="h-full bg-indigo-500"
              style={{ width: `${Math.min(50, Math.abs(r.strategy_pct))}%` }}
              title={`Strategy: ${r.strategy_pct.toFixed(2)}%`}
            />
            {r.benchmark_pct !== null && (
              <div
                className="h-full bg-gray-500 ml-1"
                style={{ width: `${Math.min(50, Math.abs(r.benchmark_pct))}%` }}
                title={`Benchmark: ${r.benchmark_pct.toFixed(2)}%`}
              />
            )}
          </div>
          <span className="w-16 text-right text-xs text-gray-300">{r.strategy_pct.toFixed(1)}%</span>
        </div>
      ))}
    </div>
  );
}

function Histogram({ equity }: { equity: BacktestReport["equity_curve"] }) {
  const data = useMemo(() => dailyReturnsFromEquity(equity).map((d) => d.ret), [equity]);
  const bins = useMemo(() => {
    const N = 30;
    if (data.length === 0) return [];
    const min = Math.min(...data); const max = Math.max(...data);
    const step = (max - min) / N || 1;
    const counts = new Array(N).fill(0);
    for (const v of data) {
      const idx = Math.min(N - 1, Math.max(0, Math.floor((v - min) / step)));
      counts[idx]++;
    }
    return counts.map((c, i) => ({ bin_start: min + i * step, count: c }));
  }, [data]);
  if (bins.length === 0) return <div className="text-xs text-gray-500 p-4">No data.</div>;
  const max = Math.max(...bins.map((b) => b.count));
  return (
    <div className="flex items-end gap-px h-40">
      {bins.map((b, i) => (
        <div
          key={i}
          className="flex-1 bg-indigo-500"
          style={{ height: `${(b.count / max) * 100}%` }}
          title={`${(b.bin_start * 100).toFixed(2)}% — count ${b.count}`}
        />
      ))}
    </div>
  );
}

function Scatter({ equity }: { equity: BacktestReport["equity_curve"] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart: IChartApi = createChart(ref.current, {
      height: 220,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
    });
    const series = chart.addHistogramSeries({ color: "#6366f1" });
    const points = dailyReturnsFromEquity(equity).map((p) => ({
      time: (Date.parse(p.ts) / 1000) as any,
      value: p.ret * 100,
      color: p.ret >= 0 ? "#22c55e" : "#ef4444",
    }));
    series.setData(points);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [equity]);
  return <div ref={ref} className="w-full" />;
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/report/ReturnsDistributionSlot.tsx
git commit -m "feat(backtest-ui): ReturnsDistributionSlot — heatmap / EOY bar / histogram / scatter"
```

---

## Task 19: `DrawdownSlot` — underwater plot ↔ top-N bars

**Files:**
- Create: `dashboard/src/components/report/DrawdownSlot.tsx`

- [ ] **Step 1: Implement the component**

`dashboard/src/components/report/DrawdownSlot.tsx`:

```typescript
import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, type IChartApi } from "lightweight-charts";
import type { BacktestReport } from "../../types";

interface Props { report: BacktestReport; }

export function DrawdownSlot({ report }: Props) {
  const [view, setView] = useState<"underwater" | "topN">("underwater");
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-300">Drawdown</h3>
        <div className="flex gap-1 text-xs">
          {(["underwater", "topN"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2 py-1 rounded ${view === v ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}
            >{v === "underwater" ? "Underwater" : "Top periods"}</button>
          ))}
        </div>
      </div>
      {view === "underwater" ? <Underwater curve={report.drawdown_curve} /> : <TopN periods={report.drawdown_periods} />}
    </div>
  );
}

function Underwater({ curve }: { curve: BacktestReport["drawdown_curve"] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !curve) return;
    const chart: IChartApi = createChart(ref.current, {
      height: 220,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
    });
    const series = chart.addAreaSeries({
      lineColor: "#ef4444", topColor: "rgba(239,68,68,0.3)", bottomColor: "rgba(239,68,68,0.0)",
    });
    series.setData(curve.map((p) => ({
      time: (Date.parse(p.timestamp) / 1000) as any,
      value: p.drawdown_pct * 100,
    })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [curve]);
  return <div ref={ref} className="w-full" />;
}

function TopN({ periods }: { periods: BacktestReport["drawdown_periods"] }) {
  if (!periods || periods.length === 0) return <div className="text-xs text-gray-500 p-4">No drawdown periods.</div>;
  const max = Math.max(...periods.map((p) => p.depth));
  return (
    <div className="space-y-1">
      {periods.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <span className="w-32 text-gray-400 font-mono truncate">
            {p.start.split("T")[0]} → {p.recovered ? p.recovered.split("T")[0] : "ongoing"}
          </span>
          <div className="flex-1 h-3 bg-gray-800 rounded overflow-hidden">
            <div
              className="h-full bg-red-500"
              style={{ width: `${(p.depth / max) * 100}%` }}
            />
          </div>
          <span className="w-16 text-right text-red-400">{(p.depth * 100).toFixed(1)}%</span>
          <span className="w-12 text-right text-gray-400">{p.days}d</span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/report/DrawdownSlot.tsx
git commit -m "feat(backtest-ui): DrawdownSlot — underwater plot + top-N periods view"
```

---

## Task 20: `RollingMetricsSlot` — multi-line toggleable series

**Files:**
- Create: `dashboard/src/components/report/RollingMetricsSlot.tsx`

- [ ] **Step 1: Implement the component**

`dashboard/src/components/report/RollingMetricsSlot.tsx`:

```typescript
import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, type IChartApi, type ISeriesApi } from "lightweight-charts";
import type { BacktestReport, BacktestRollingPoint } from "../../types";

type Series = "sharpe" | "sortino" | "vol" | "beta";
const SERIES_META: Record<Series, { color: string; label: string }> = {
  sharpe: { color: "#6366f1", label: "Rolling Sharpe" },
  sortino: { color: "#22c55e", label: "Rolling Sortino" },
  vol: { color: "#facc15", label: "Rolling Volatility" },
  beta: { color: "#ef4444", label: "Rolling Beta" },
};

interface Props { report: BacktestReport; }

export function RollingMetricsSlot({ report }: Props) {
  const [enabled, setEnabled] = useState<Record<Series, boolean>>({
    sharpe: true, sortino: false, vol: false, beta: false,
  });
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Partial<Record<Series, ISeriesApi<"Line">>>>({});

  useEffect(() => {
    if (!ref.current || !report.rolling_metrics) return;
    const chart = createChart(ref.current, {
      height: 220,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
    });
    chartRef.current = chart;
    chart.timeScale().fitContent();
    return () => { chart.remove(); chartRef.current = null; seriesRef.current = {}; };
  }, [report.rolling_metrics]);

  useEffect(() => {
    if (!chartRef.current || !report.rolling_metrics) return;
    const chart = chartRef.current;
    const points = report.rolling_metrics.points;
    (Object.keys(SERIES_META) as Series[]).forEach((k) => {
      const has = !!seriesRef.current[k];
      if (enabled[k] && !has) {
        const s = chart.addLineSeries({ color: SERIES_META[k].color, lineWidth: 2 });
        s.setData(points
          .filter((p: BacktestRollingPoint) => p[k] !== null && p[k] !== undefined)
          .map((p: BacktestRollingPoint) => ({
            time: (Date.parse(p.timestamp) / 1000) as any,
            value: p[k] as number,
          })));
        seriesRef.current[k] = s;
      } else if (!enabled[k] && has) {
        chart.removeSeries(seriesRef.current[k]!);
        delete seriesRef.current[k];
      }
    });
  }, [enabled, report.rolling_metrics]);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-300">
          Rolling metrics ({report.rolling_metrics?.window_days ?? 90}d window)
        </h3>
        <div className="flex gap-1 text-xs">
          {(Object.keys(SERIES_META) as Series[]).map((k) => (
            <button
              key={k}
              onClick={() => setEnabled((e) => ({ ...e, [k]: !e[k] }))}
              className={`px-2 py-1 rounded ${enabled[k] ? "text-white" : "text-gray-400 bg-gray-800 hover:bg-gray-700"}`}
              style={enabled[k] ? { background: SERIES_META[k].color } : undefined}
            >{SERIES_META[k].label}</button>
          ))}
        </div>
      </div>
      <div ref={ref} className="w-full" />
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/report/RollingMetricsSlot.tsx
git commit -m "feat(backtest-ui): RollingMetricsSlot — toggleable Sharpe/Sortino/Vol/Beta series"
```

---

## Task 21: `EquitySlot` — wraps existing BacktestChart with toggles + progressive zoom

**Files:**
- Create: `dashboard/src/components/report/EquitySlot.tsx`

Reuses the existing `BacktestChart` component (which already supports benchmark/cash/markers toggles). Adds: log-scale toggle, vol-matched-curve toggle, progressive-zoom data fetcher.

The vol-matched curve is computed client-side from the benchmark series scaled to match strategy volatility over the window.

- [ ] **Step 1: Implement the component**

`dashboard/src/components/report/EquitySlot.tsx`:

```typescript
import { useMemo, useState } from "react";
import { BacktestChart, type BacktestEquityPoint, type BacktestBenchmarkPoint, type BacktestTradeMarker } from "../BacktestChart";
import type { BacktestReport } from "../../types";
import { useBacktestEquityWindow } from "../../api/hooks";

interface Props {
  report: BacktestReport;
  trades: { timestamp: string; symbol: string; side: string; quantity: number; fill_price: number | null }[];
}

interface VisibleRange { from: string | null; to: string | null; }

export function EquitySlot({ report, trades }: Props) {
  const [logScale, setLogScale] = useState(false);
  const [showVolMatched, setShowVolMatched] = useState(false);
  const [visible, setVisible] = useState<VisibleRange>({ from: null, to: null });

  // Decide what resolution to fetch based on the visible range.
  const zoomParams = useMemo(() => {
    if (!visible.from || !visible.to) return null;
    const days = (Date.parse(visible.to) - Date.parse(visible.from)) / 86_400_000;
    if (days > 60) return null;  // daily already in report
    const resolution: "1min" | "1hour" = days < 3 ? "1min" : "1hour";
    return { from: visible.from, to: visible.to, resolution };
  }, [visible]);

  const { data: zoomedEquity } = useBacktestEquityWindow(report.id, zoomParams);

  const baseEquity: BacktestEquityPoint[] = (report.equity_curve ?? []).map((p) => ({
    timestamp: p.timestamp,
    portfolio_value: p.portfolio_value,
    cash: p.cash,
  }));

  const equityPoints: BacktestEquityPoint[] = useMemo(() => {
    if (!zoomedEquity || !zoomParams) return baseEquity;
    // Splice the zoomed range into the base series
    const before = baseEquity.filter((p) => p.timestamp < zoomParams.from);
    const after = baseEquity.filter((p) => p.timestamp > zoomParams.to);
    const inside = zoomedEquity.items.map((it) => ({
      timestamp: it.ts, portfolio_value: it.portfolio_value, cash: it.cash,
    }));
    return [...before, ...inside, ...after];
  }, [baseEquity, zoomedEquity, zoomParams]);

  const benchmarkPoints: BacktestBenchmarkPoint[] = useMemo(() => {
    const raw = (report.benchmark_equity_curve ?? []).map((p) => ({
      timestamp: p.timestamp, value: p.value,
    }));
    if (!showVolMatched || raw.length < 2 || baseEquity.length < 2) return raw;
    // Vol-match: scale benchmark daily returns by (strategy_std / benchmark_std), recompound
    const stratRets: number[] = [];
    for (let i = 1; i < baseEquity.length; i++) {
      const prev = baseEquity[i - 1].portfolio_value;
      if (prev > 0) stratRets.push(baseEquity[i].portfolio_value / prev - 1);
    }
    const benchRets: number[] = [];
    for (let i = 1; i < raw.length; i++) {
      const prev = raw[i - 1].value;
      if (prev > 0) benchRets.push(raw[i].value / prev - 1);
    }
    const std = (xs: number[]) => {
      if (xs.length < 2) return 0;
      const m = xs.reduce((a, b) => a + b, 0) / xs.length;
      return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1));
    };
    const sStd = std(stratRets);
    const bStd = std(benchRets);
    if (bStd === 0) return raw;
    const scale = sStd / bStd;
    const out: BacktestBenchmarkPoint[] = [{ timestamp: raw[0].timestamp, value: raw[0].value }];
    let cum = raw[0].value;
    for (const r of benchRets) {
      cum = cum * (1 + r * scale);
      out.push({ timestamp: raw[out.length].timestamp, value: cum });
    }
    return out;
  }, [report.benchmark_equity_curve, baseEquity, showVolMatched]);

  const tradeMarkers: BacktestTradeMarker[] = trades
    .filter((t) => t.fill_price !== null && (t.side === "buy" || t.side === "sell"))
    .map((t) => ({
      timestamp: t.timestamp,
      side: t.side as "buy" | "sell",
      symbol: t.symbol,
      quantity: t.quantity,
      fill_price: t.fill_price as number,
    }));

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-300">Equity</h3>
        <div className="flex gap-2 text-xs">
          <label className="flex items-center gap-1 text-gray-400">
            <input type="checkbox" checked={logScale} onChange={(e) => setLogScale(e.target.checked)} />
            Log scale
          </label>
          <label className="flex items-center gap-1 text-gray-400">
            <input type="checkbox" checked={showVolMatched} onChange={(e) => setShowVolMatched(e.target.checked)} />
            Vol-matched
          </label>
        </div>
      </div>
      <BacktestChart
        equity={equityPoints}
        benchmark={benchmarkPoints}
        trades={tradeMarkers}
        benchmarkLabel={
          report.benchmark_symbol
            ? `Benchmark${showVolMatched ? " (vol-matched)" : ""} (${report.benchmark_symbol})`
            : "Benchmark"
        }
        height={300}
        logScale={logScale}
        onVisibleRangeChange={(from, to) => setVisible({ from, to })}
      />
    </div>
  );
}
```

- [ ] **Step 2: Add the new BacktestChart props (logScale, onVisibleRangeChange)**

`dashboard/src/components/BacktestChart.tsx` currently accepts `equity`, `benchmark`, `trades`, `benchmarkLabel`, `height`. Add two optional props.

Find the props interface and add:

```typescript
  logScale?: boolean;
  onVisibleRangeChange?: (from: string | null, to: string | null) => void;
```

In the chart-creation effect, pass `mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal` to the price-scale options. Import `PriceScaleMode` from `lightweight-charts`.

In the same effect, after `chart` is created, register the visible-range callback:

```typescript
if (onVisibleRangeChange) {
  chart.timeScale().subscribeVisibleTimeRangeChange((range) => {
    if (range && typeof range.from === "number" && typeof range.to === "number") {
      onVisibleRangeChange(
        new Date(range.from * 1000).toISOString(),
        new Date(range.to * 1000).toISOString(),
      );
    } else {
      onVisibleRangeChange(null, null);
    }
  });
}
```

- [ ] **Step 3: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/components/report/EquitySlot.tsx dashboard/src/components/BacktestChart.tsx
git commit -m "feat(backtest-ui): EquitySlot — toggles + progressive zoom on visible range"
```

---

## Task 22: Rewrite `BacktestRunDetail` body, remove tearsheet button

**Files:**
- Modify: `dashboard/src/pages/BacktestRunDetail.tsx`

Replace the metrics-grid + equity-card sections with the new component tree. Keep the header (title, status, Delete), the in-flight progress bar, the trades table, and the Delete confirm dialog.

- [ ] **Step 1: Rewrite the page**

Replace the current file with:

```typescript
// ── Spec D U2: backtest run detail ──
import { useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { ChevronLeft, Trash2 } from "lucide-react";
import {
  useBacktestReport,
  useBacktestTrades,
  useDeleteBacktestRun,
} from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/ui";
import { KpiCard } from "../components/report/KpiCard";
import { ParametersTable } from "../components/report/ParametersTable";
import { EoyTable } from "../components/report/EoyTable";
import { DrawdownsTable } from "../components/report/DrawdownsTable";
import { MetricsTable } from "../components/report/MetricsTable";
import { EquitySlot } from "../components/report/EquitySlot";
import { DrawdownSlot } from "../components/report/DrawdownSlot";
import { ReturnsDistributionSlot } from "../components/report/ReturnsDistributionSlot";
import { RollingMetricsSlot } from "../components/report/RollingMetricsSlot";

const INFLIGHT_STATUSES = ["queued", "downloading_data", "running"];
function inflight(status: string | undefined | null): boolean {
  return !!status && INFLIGHT_STATUSES.includes(status);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

function fmtInt(v: number | null | undefined): string {
  if (v == null) return "—";
  return Math.round(v).toString();
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}

interface BacktestTradeRow {
  timestamp: string; symbol: string; side: string; quantity: number;
  requested_price: number | null; fill_price: number | null;
  slippage_dollars: number | null; fees: number | null; realized_pnl: number | null;
}

export function BacktestRunDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);
  const { data: report } = useBacktestReport(id, { refetchInterval: 2000 });
  const isRunInflight = inflight(report?.status);
  const liveRefetch = isRunInflight ? 2000 : undefined;
  const { data: tradesData } = useBacktestTrades(id, 500, 0, { refetchInterval: liveRefetch });
  const del = useDeleteBacktestRun();
  const [deleteOpen, setDeleteOpen] = useState(false);

  async function handleDelete() {
    try {
      await del.mutateAsync(id);
      addAlert({ message: "Deleted backtest run.", severity: "success" });
      navigate("/backtests");
    } catch {
      addAlert({ message: "Failed to delete backtest run.", severity: "error" });
      setDeleteOpen(false);
    }
  }

  if (!report) {
    return <div className="p-4 text-gray-400">Loading…</div>;
  }

  const trades = ((tradesData?.items ?? []) as BacktestTradeRow[]) ?? [];
  const totalTrades = (tradesData as { total?: number } | undefined)?.total ?? trades.length;
  const km = report.key_metrics?.strategy;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/backtests" className="text-gray-400 hover:text-white">
            <ChevronLeft size={20} />
          </Link>
          <h1 className="text-xl font-bold">Backtest Run</h1>
          <StatusBadge status={report.status} />
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setDeleteOpen(true)}
            disabled={del.isPending}
            className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-red-300 bg-red-900/40 border border-red-800 hover:bg-red-900/60 disabled:opacity-50"
          >
            <Trash2 size={14} /> Delete
          </button>
        </div>
      </div>

      {/* In-flight progress */}
      {isRunInflight && (
        <div className="bg-gray-900 border border-gray-800 rounded p-3">
          <div className="text-sm text-gray-300 mb-2">{(report as any).progress_message ?? report.status}</div>
          <div className="bg-gray-700 rounded-full h-2 overflow-hidden">
            <div
              className="bg-indigo-600 h-2 transition-[width] ease-linear duration-[2000ms]"
              style={{ width: `${(((report as any).progress_pct ?? 0) as number) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* Incomplete-data banner for legacy rows */}
      {report.status === "completed" && !report.key_metrics && (
        <div className="bg-yellow-900/30 border border-yellow-800 rounded p-3 text-sm text-yellow-200">
          This backtest pre-dates the report system. Re-run it to populate the new metrics.
        </div>
      )}

      {/* KPI row */}
      {km && (
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <KpiCard variant="hero" label="Annual Return" value={fmtPct(km.cagr)} hint="CAGR" />
          <KpiCard label="Total Return" value={fmtPct(km.total_return)} />
          <KpiCard label="Max Drawdown" value={fmtPct(km.max_drawdown)} />
          <KpiCard label="RoMaD" value={fmtNum(km.romad)} hint="CAGR / Max Drawdown" />
          <KpiCard label="Longest DD Days" value={fmtInt(km.longest_drawdown_days)} />
        </div>
      )}

      {/* 4 chart slots — 2x2 grid at wide widths */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <EquitySlot report={report} trades={trades} />
        <DrawdownSlot report={report} />
        <ReturnsDistributionSlot report={report} />
        <RollingMetricsSlot report={report} />
      </div>

      {/* Side tables — 3-col at wide widths */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <ParametersTable params={report.config_overrides} />
        <EoyTable rows={report.eoy_returns} />
        <DrawdownsTable rows={report.drawdown_periods} />
      </div>

      {/* Strategy vs Benchmark metrics */}
      <MetricsTable
        strategy={report.key_metrics?.strategy}
        benchmark={report.key_metrics?.benchmark}
      />

      {/* Trades */}
      <div className="bg-gray-900 border border-gray-800 rounded">
        <div className="px-3 py-2 border-b border-gray-800 text-sm font-semibold text-gray-300">
          Trades ({totalTrades})
        </div>
        <div className="overflow-auto max-h-96">
          <table className="w-full text-sm">
            <thead className="bg-gray-800 text-xs uppercase text-gray-400 sticky top-0">
              <tr>
                <th className="text-left p-2">Timestamp</th>
                <th className="text-left p-2">Symbol</th>
                <th className="text-left p-2">Side</th>
                <th className="text-right p-2">Qty</th>
                <th className="text-right p-2">Fill</th>
                <th className="text-right p-2">Realized P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i} className="border-t border-gray-800">
                  <td className="p-2 text-xs text-gray-400">{new Date(t.timestamp).toLocaleString()}</td>
                  <td className="p-2 font-mono">{t.symbol}</td>
                  <td className={`p-2 ${t.side === "buy" ? "text-green-400" : "text-red-400"}`}>{t.side}</td>
                  <td className="p-2 text-right">{fmtNum(t.quantity, 4)}</td>
                  <td className="p-2 text-right font-semibold">
                    {t.fill_price === null ? "—" : t.fill_price.toLocaleString("en-US", { style: "currency", currency: "USD" })}
                  </td>
                  <td className={`p-2 text-right ${
                    t.realized_pnl == null ? "text-gray-500" : t.realized_pnl > 0 ? "text-green-400" : "text-red-400"
                  }`}>
                    {t.realized_pnl == null ? "—" :
                      t.realized_pnl.toLocaleString("en-US", { style: "currency", currency: "USD" })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete backtest run"
        message="Are you sure you want to delete this backtest run? This cannot be undone."
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

Expected: `ok`.

- [ ] **Step 3: Run frontend test suite**

```bash
cd dashboard && npm test
```

Expected: all pass (KpiCard tests + any pre-existing tests).

- [ ] **Step 4: Smoke test in dev server**

```bash
cd dashboard && npm run dev
```

Visit `http://localhost:5173/backtest-runs/<id>` for any existing completed run. Expected: legacy rows render the "Re-run to populate" banner; new rows (created after this PR) render the full report.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/BacktestRunDetail.tsx
git commit -m "feat(backtest-ui): rewrite BacktestRunDetail with native report layout"
```

---

## Self-review notes

After all 22 tasks are complete:

- The old quantstats HTML output is gone (file artifact, route, button, service file, schema column).
- The runner uses the producer-consumer pipeline. Memory is bounded by `5_000 ticks × 8 chunks × ~200 bytes ≈ 8 MB` regardless of backtest length.
- A 10-year 1-minute backtest produces parquet files totaling ~5–10 MB on disk; the `/report` payload is ~300 KB.
- The frontend renders a 4-slot chart grid + KPI cards + 3 side tables + Strategy-vs-Benchmark metrics table + the existing Trades table.
- Legacy backtest rows show a "re-run to populate" banner instead of breaking.

Followups (deferred per spec):

- Streaming-write resilience on coordinator crash mid-run.
- Auto-scaling heatmap color thresholds.
- Configurable rolling window (currently fixed at 90 days).
- Multi-run side-by-side comparison page.
