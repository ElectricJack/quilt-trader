# Dashboard-Driven Backtest Execution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the dashboard-driven backtest engine described in Spec D — pick an algorithm, configure date range / cash / fees / slippage / benchmark, auto-download missing data, run a conservative-fill simulation with framework-level no-look-ahead, surface results (metrics, equity curve, trades, optional quantstats HTML tearsheet) on a dedicated page. The same engine also feeds the existing `BacktestComparison` divergence checker.

**Architecture:** Five sequential phases with parallel work units inside each. Phase 0 lands foundations (model, context, config, metrics, dep). Phase 1 is the engine itself (single work unit — largest piece). Phase 2 wires consumers (Spec D runner, parallel-comparison feeder, REST API). Phase 3 builds UI. Phase 4 ties tearsheet generation + end-to-end smoke.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 / Pydantic v2 / pytest-asyncio (backend); React + TypeScript + Vite + TanStack Query + lightweight-charts (frontend); `quantstats` (new dep for HTML tearsheet).

**Spec reference:** `docs/superpowers/specs/2026-05-14-backtest-execution-design.md`.

---

## Orchestration

### Phase ordering

```
Phase 0 — Foundations (parallel: F1..F5)
        |
        v
Phase 1 — Engine (serial: E1)
        |
        v
Phase 2 — Consumers + API (parallel: C1, C2, C3)
        |
        v
Phase 3 — UI (parallel: U1, U2, U3)
        |
        v
Phase 4 — Tearsheet + smoke (sequential: T1, S1)
```

A phase ends only when all its parallel work units have merged.

### File ownership map

| File | Owner |
|---|---|
| `coordinator/database/models.py` (add `BacktestRun`) + alembic migration | F1 |
| `coordinator/services/backtest_tick_context.py` | F2 |
| `coordinator/services/backtest_config.py` (TradingFee + SlippageModel) | F3 |
| `coordinator/services/backtest_metrics.py` | F4 |
| `pyproject.toml` (add quantstats dep) | F5 |
| `coordinator/services/backtest_engine.py` (REPLACES the existing comparator-only module; engine + comparator coexist) | E1 |
| `coordinator/services/backtest_runner.py` | C1 |
| `coordinator/services/parallel_backtest_feeder.py` + `coordinator/services/backtest_scheduler.py` (update) | C2 |
| `coordinator/api/routes/backtest_runs.py` + `coordinator/main.py` (mount) + `coordinator/api/dependencies.py` (container) | C3 |
| `dashboard/src/components/RunBacktestModal.tsx` + additive hooks/client | U1 |
| `dashboard/src/pages/BacktestRunDetail.tsx` + additive hooks/client | U2 |
| `dashboard/src/pages/Backtests.tsx` (split into tabs) + `App.tsx` route + nav | U3 |
| `coordinator/services/backtest_tearsheet.py` (quantstats wrapper) + integration into `BacktestRunner` | T1 |
| `tests/smoke/test_e2e_backtest.py` + manual smoke against `quilt-trader-test-algo` | S1 |

### Conventions

- **TDD per task:** write failing test → run → implement → run → commit.
- **Commit style:** `feat(scope): subject`, `fix(scope): subject`, `test(scope): subject`.
- **Python:** type hints; `from __future__ import annotations` on new files; `Optional[X]` for nullable; Pydantic v2 (`model_config`, `field_validator`).
- **Tests:** existing `tests/coordinator/conftest.py` provides `client`, `test_app`, `db_session`. Mirror that.
- **Cross-spec rule:** the existing `BacktestComparison` table, its router, and its scheduler stay. We're ADDING a new model + new endpoints + a new engine; the comparator's `_compare_instance` continues to read `DecisionLog` rows — just now produced by the new engine via `ParallelBacktestFeeder`.

---

## Phase 0 — Foundations

Five independent work units. None depend on each other.

### Work unit F1: `BacktestRun` model + migration

**Branch:** `plan/D-F1-backtest-run-model`

**Files:**
- Modify: `coordinator/database/models.py`
- Create: `coordinator/database/migrations/versions/<timestamp>_backtest_runs.py`
- Test: extend `tests/coordinator/test_models.py` with one round-trip test.

- [ ] **F1.1 — Add `BacktestRun` model**

In `coordinator/database/models.py`, append BEFORE `Setting`:

```python
class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    algorithm_id: Mapped[str] = mapped_column(String, ForeignKey("algorithms.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    # queued | downloading_data | running | completed | failed | cancelled

    # Inputs
    date_range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_cash: Mapped[float] = mapped_column(Float, nullable=False, default=100_000.0)
    config_overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    buy_trading_fees: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    sell_trading_fees: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    slippage_model: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    benchmark_symbol: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    benchmark_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Progress
    progress_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Results
    total_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cagr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volatility: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    romad: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_slippage_dollars: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trade_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expectancy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longest_drawdown_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_winning_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_losing_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Large blobs
    equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    trades: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    drawdown_periods: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Side artifacts
    tearsheet_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    download_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **F1.2 — Generate alembic migration**

```bash
cd /home/jkern/dev/quilt-trader && alembic revision --autogenerate -m "backtest_runs"
```

Inspect the generated file. Expected: `op.create_table("backtest_runs", ...)`. The SQLite + SQLAlchemy 2 combo plus the F5 commit's `render_as_batch=True` should make autogenerate work cleanly.

- [ ] **F1.3 — Apply and verify**

```bash
alembic upgrade head
sqlite3 data/quilt_trader.db ".schema backtest_runs"
```

Confirm the table exists with all columns.

- [ ] **F1.4 — Round-trip test**

In `tests/coordinator/test_models.py` (append):

```python
@pytest.mark.asyncio
async def test_backtest_run_round_trip(db_session):
    from coordinator.database.models import Algorithm, BacktestRun
    from datetime import datetime, timezone, timedelta

    algo = Algorithm(name="test-algo", repo_url="https://example/x",
                     install_status="installed")
    db_session.add(algo)
    await db_session.flush()

    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 6, 1, tzinfo=timezone.utc),
        initial_cash=50000.0,
        buy_trading_fees=[{"flat_fee": 0.0, "percent_fee": 0.001, "maker": True, "taker": True}],
        slippage_model={"market_bps": 5.0, "limit_bps": 0.0, "use_bar_range": False, "volume_impact_bps_per_pct": 0.0},
        benchmark_symbol="SPY",
        benchmark_source="polygon",
    )
    db_session.add(run)
    await db_session.flush()

    fetched = (await db_session.execute(
        select(BacktestRun).where(BacktestRun.id == run.id)
    )).scalar_one()
    assert fetched.algorithm_id == algo.id
    assert fetched.initial_cash == 50000.0
    assert fetched.status == "queued"
    assert fetched.buy_trading_fees[0]["percent_fee"] == 0.001
```

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/test_models.py -v -k backtest_run`. Expected: PASS.

- [ ] **F1.5 — Commit**

```bash
git add coordinator/database/models.py coordinator/database/migrations/versions/ tests/coordinator/test_models.py
git commit -m "feat(schema): add backtest_runs table

Spec D §1. Single new table; no other schema changes."
```

### Work unit F2: `BacktestTickContext` + look-ahead enforcement

**Branch:** `plan/D-F2-tick-context`

**Files:**
- Create: `coordinator/services/backtest_tick_context.py`
- Test: `tests/coordinator/services/test_backtest_tick_context.py`

The look-ahead-prevention logic — Spec D's most correctness-critical piece. F2 ships ONLY the context + its tests. The engine that uses it lands in E1.

- [ ] **F2.1 — Failing test: filters future bars**

```python
# tests/coordinator/services/test_backtest_tick_context.py
import pytest
import pandas as pd
from datetime import datetime, timezone
from coordinator.services.backtest_tick_context import BacktestTickContext, timeframe_to_seconds


def _make_daily(start, days):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=days, freq="D", tz="UTC"),
        "open": [100.0 + i for i in range(days)],
        "high": [101.0 + i for i in range(days)],
        "low":  [ 99.0 + i for i in range(days)],
        "close":[100.5 + i for i in range(days)],
        "volume": [1_000_000] * days,
    })


def test_market_data_filters_future_bars():
    daily = _make_daily("2026-01-01", 30)
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): daily},
        positions={},
        cash=100_000.0,
    )
    ctx.set_sim_time(datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc))
    out = ctx.market_data("SPY", timeframe="1day", bars=100, source="polygon")
    # Most recent fully-closed daily bar is 2026-01-14 (close = 2026-01-15 00:00).
    assert out["timestamp"].max() == pd.Timestamp("2026-01-14", tz="UTC")
    # In-progress 2026-01-15 must NOT appear.
    assert pd.Timestamp("2026-01-15", tz="UTC") not in out["timestamp"].values


def test_market_data_returns_tail():
    daily = _make_daily("2026-01-01", 30)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): daily}, positions={}, cash=0)
    ctx.set_sim_time(datetime(2026, 1, 31, tzinfo=timezone.utc))
    out = ctx.market_data("SPY", timeframe="1day", bars=5, source="polygon")
    assert len(out) == 5
    # Tail = last 5 bars before sim_time
    assert out["timestamp"].max() == pd.Timestamp("2026-01-30", tz="UTC")


def test_multi_timeframe_no_lookahead():
    daily = _make_daily("2026-01-01", 30)
    minute = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-15 09:30", periods=200, freq="min", tz="UTC"),
        "open": [100.0] * 200,
        "high": [101.0] * 200,
        "low":  [ 99.0] * 200,
        "close":[100.5] * 200,
        "volume": [10_000] * 200,
    })
    ctx = BacktestTickContext(
        bars={
            ("polygon", "SPY", "1day"): daily,
            ("polygon", "SPY", "1min"): minute,
        },
        positions={}, cash=0,
    )
    # Sim time mid-day on Jan 15
    ctx.set_sim_time(datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc))
    # Daily for SPY must NOT include Jan 15 (in progress)
    daily_out = ctx.market_data("SPY", "1day", 100, source="polygon")
    assert daily_out["timestamp"].max() == pd.Timestamp("2026-01-14", tz="UTC")
    # Minute bars before sim_time are accessible
    minute_out = ctx.market_data("SPY", "1min", 100, source="polygon")
    assert minute_out["timestamp"].max() < pd.Timestamp("2026-01-15 12:30", tz="UTC") + pd.Timedelta(seconds=1)


def test_tick_timeframe_zero_duration_strict():
    """A '1tick' bar is available the instant its timestamp <= sim_time_now."""
    ticks = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-15 09:30:00", periods=5, freq="100ms", tz="UTC"),
        "open":   [100.00, 100.01, 100.00, 100.02, 100.03],
        "high":   [100.00, 100.01, 100.00, 100.02, 100.03],
        "low":    [100.00, 100.01, 100.00, 100.02, 100.03],
        "close":  [100.00, 100.01, 100.00, 100.02, 100.03],
        "volume": [100, 200, 50, 300, 150],
    })
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1tick"): ticks}, positions={}, cash=0)
    sim = pd.Timestamp("2026-01-15 09:30:00.2", tz="UTC").to_pydatetime()
    ctx.set_sim_time(sim)
    out = ctx.market_data("SPY", "1tick", 10, source="polygon")
    # Ticks at 09:30:00.0, .1, .2 are all available (zero-duration; timestamp <= sim_time)
    assert len(out) == 3


def test_timeframe_to_seconds():
    assert timeframe_to_seconds("1min") == 60
    assert timeframe_to_seconds("5min") == 300
    assert timeframe_to_seconds("15min") == 900
    assert timeframe_to_seconds("1hour") == 3600
    assert timeframe_to_seconds("1day") == 86400
    assert timeframe_to_seconds("1tick") == 0
    with pytest.raises(ValueError):
        timeframe_to_seconds("invalid")
```

- [ ] **F2.2 — Confirm tests fail**

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/services/test_backtest_tick_context.py -v`. Expected: ImportError (module doesn't exist yet).

- [ ] **F2.3 — Implement `backtest_tick_context.py`**

```python
# coordinator/services/backtest_tick_context.py
"""Tick context used by BacktestEngine.

Implements sdk.context.TickContext with framework-level no-look-ahead
enforcement: market_data() returns only bars whose close time <=
sim_time_now. Same rule for all timeframes (a 1day bar has duration
86400s; a 1tick bar has duration 0s).

Options chain is declared but NotImplementedError-raising in v1 —
options backtesting is a follow-up spec (Spec D §12).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from sdk.context import TickContext
from sdk.models import OptionChain, Position


_TIMEFRAME_TO_SECONDS = {
    "1tick":  0,
    "1min":   60,
    "5min":   300,
    "15min":  900,
    "1hour":  3600,
    "1day":   86400,
}


def timeframe_to_seconds(tf: str) -> int:
    if tf not in _TIMEFRAME_TO_SECONDS:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return _TIMEFRAME_TO_SECONDS[tf]


class BacktestTickContext(TickContext):
    """Backtest-time TickContext.

    The engine calls `set_sim_time()` before each `on_tick` and the engine
    maintains positions/cash state. `bars` is a dict keyed by
    (source, symbol, timeframe) -> pre-loaded DataFrame.
    """

    def __init__(
        self,
        bars: dict[tuple[str, str, str], pd.DataFrame],
        positions: dict[str, Position],
        cash: float,
        account_value: Optional[float] = None,
        buying_power: Optional[float] = None,
        default_source: Optional[str] = None,
    ) -> None:
        self._bars = bars
        self._positions = positions
        self._cash = cash
        self._account_value = account_value if account_value is not None else cash
        self._buying_power = buying_power if buying_power is not None else cash
        self._default_source = default_source
        self._sim_time_now: Optional[datetime] = None

    # ---- mutation hooks called by the engine ----

    def set_sim_time(self, t: datetime) -> None:
        self._sim_time_now = t

    def update_account(
        self, *, cash: float, account_value: float, buying_power: float,
        positions: dict[str, Position],
    ) -> None:
        self._cash = cash
        self._account_value = account_value
        self._buying_power = buying_power
        self._positions = positions

    # ---- TickContext interface ----

    @property
    def timestamp(self) -> datetime:
        if self._sim_time_now is None:
            raise RuntimeError("BacktestTickContext.set_sim_time must be called before timestamp access")
        return self._sim_time_now

    @property
    def mode(self) -> str:
        return "backtest"

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def account_value(self) -> float:
        return self._account_value

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def buying_power(self) -> float:
        return self._buying_power

    def market_data(
        self, symbol: str, timeframe: str = "1min", bars: int = 100,
        source: Optional[str] = None,
    ) -> pd.DataFrame:
        if self._sim_time_now is None:
            raise RuntimeError("set_sim_time must be called before market_data")
        src = source or self._default_source
        if src is None:
            # Fallback: pick the first available source for the symbol+timeframe
            for (s, sym, tf), _df in self._bars.items():
                if sym == symbol and tf == timeframe:
                    src = s
                    break
        key = (src, symbol, timeframe)
        df = self._bars.get(key)
        if df is None or df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        duration_s = timeframe_to_seconds(timeframe)
        cutoff = self._sim_time_now
        # A bar is "fully closed" if its close time (start + duration) is <= cutoff.
        # For 1tick (duration=0), this collapses to timestamp <= cutoff.
        delta = pd.Timedelta(seconds=duration_s)
        visible = df[df["timestamp"] + delta <= pd.Timestamp(cutoff)]
        return visible.tail(bars).reset_index(drop=True)

    def data(self, source_name: str) -> pd.DataFrame:
        # Custom (scraper) data sources — backtest treats these as not available in v1.
        raise NotImplementedError(
            f"Custom data source '{source_name}' not available in backtest contexts; "
            f"tracked as a follow-up."
        )

    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        raise NotImplementedError(
            "option_chain not yet available in backtest contexts; tracked as a follow-up. "
            "Options backtest support is documented in Spec D §12."
        )
```

- [ ] **F2.4 — Confirm tests pass**

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/services/test_backtest_tick_context.py -v`. Expected: 5 tests PASS.

- [ ] **F2.5 — Commit**

```bash
git add coordinator/services/backtest_tick_context.py tests/coordinator/services/test_backtest_tick_context.py
git commit -m "feat(backtest): tick context with framework-level no-look-ahead

Spec D §3. Implements sdk.context.TickContext with bar-close-time
filtering. Treats 1tick as a degenerate zero-duration bar so the
same filter works at any frequency. Engine lands in E1."
```

### Work unit F3: `TradingFee` + `SlippageModel` Pydantic models

**Branch:** `plan/D-F3-config-models`

**Files:**
- Create: `coordinator/services/backtest_config.py`
- Test: `tests/coordinator/services/test_backtest_config.py`

- [ ] **F3.1 — Failing test**

```python
# tests/coordinator/services/test_backtest_config.py
import pytest
from pydantic import ValidationError
from coordinator.services.backtest_config import TradingFee, SlippageModel


def test_trading_fee_defaults():
    tf = TradingFee()
    assert tf.flat_fee == 0.0
    assert tf.percent_fee == 0.0
    assert tf.maker is True
    assert tf.taker is True


def test_trading_fee_negative_rejected():
    with pytest.raises(ValidationError):
        TradingFee(flat_fee=-1.0)
    with pytest.raises(ValidationError):
        TradingFee(percent_fee=-0.001)


def test_slippage_model_defaults_are_conservative():
    sm = SlippageModel()
    assert sm.market_bps == 5.0  # Conservative default
    assert sm.limit_bps == 0.0
    assert sm.use_bar_range is False
    assert sm.volume_impact_bps_per_pct == 0.0


def test_slippage_model_validation():
    with pytest.raises(ValidationError):
        SlippageModel(market_bps=-1.0)
    with pytest.raises(ValidationError):
        SlippageModel(volume_impact_bps_per_pct=-5.0)
```

- [ ] **F3.2 — Implement**

```python
# coordinator/services/backtest_config.py
"""Pydantic config models for backtest runs.

TradingFee mirrors Lumibot's API (flat + percent + maker/taker).
SlippageModel is our own (Lumibot doesn't simulate slippage in the
backtest broker). Default market_bps=5.0 — conservative by design.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TradingFee(BaseModel):
    flat_fee: float = Field(default=0.0, ge=0)
    percent_fee: float = Field(default=0.0, ge=0)   # decimal: 0.001 = 0.1%
    maker: bool = True   # applies to limit / stop_limit
    taker: bool = True   # applies to market / stop


class SlippageModel(BaseModel):
    market_bps: float = Field(default=5.0, ge=0)
    limit_bps: float = Field(default=0.0, ge=0)
    use_bar_range: bool = False
    volume_impact_bps_per_pct: float = Field(default=0.0, ge=0)
```

- [ ] **F3.3 — Run + pass**

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/services/test_backtest_config.py -v`. Expected: 4 tests PASS.

- [ ] **F3.4 — Commit**

```bash
git add coordinator/services/backtest_config.py tests/coordinator/services/test_backtest_config.py
git commit -m "feat(backtest): TradingFee + SlippageModel config

Pydantic v2 models. SlippageModel.market_bps defaults to 5.0
(conservative). Spec D §2."
```

### Work unit F4: `backtest_metrics.py`

**Branch:** `plan/D-F4-metrics`

**Files:**
- Create: `coordinator/services/backtest_metrics.py`
- Test: `tests/coordinator/services/test_backtest_metrics.py`

Pure math functions. Self-contained (no engine, no DB). Largest unit in Phase 0 in terms of math correctness.

- [ ] **F4.1 — Failing tests for each metric**

```python
# tests/coordinator/services/test_backtest_metrics.py
import pytest
import math
import pandas as pd
from datetime import datetime, timezone
from coordinator.services.backtest_metrics import (
    cagr, volatility, sharpe_ratio, sortino_ratio, calmar_ratio,
    max_drawdown, romad, total_return, win_rate, profit_factor,
    avg_win, avg_loss, expectancy, round_trip_trades, longest_streak,
    longest_drawdown_days, top_n_drawdowns, compute_all,
)


def _daily_returns(values):
    """Build a daily-indexed dataframe with portfolio_value series."""
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D", tz="UTC")
    df = pd.DataFrame({"portfolio_value": values}, index=idx)
    df["return"] = df["portfolio_value"].pct_change().fillna(0)
    return df


def test_total_return_simple():
    df = _daily_returns([100, 110, 121])  # +10%, +10%
    assert total_return(df, initial_cash=100) == pytest.approx(0.21, abs=1e-6)


def test_cagr_one_year():
    df = _daily_returns([100, 110] + [110] * 365)  # 10% gain, held one full year
    # CAGR ≈ 10% / 366 days * 365 ~ 9.97%; approx check
    result = cagr(df)
    assert 0.08 < result < 0.12


def test_volatility_zero_when_no_variation():
    df = _daily_returns([100] * 100)
    assert volatility(df) == pytest.approx(0.0, abs=1e-9)


def test_sharpe_uses_cagr_minus_rf_over_vol():
    df = _daily_returns([100, 101, 102, 103, 104, 105])  # steady gains
    s = sharpe_ratio(df, risk_free_rate=0.0)
    # Returns are positive with low vol, so sharpe should be large positive
    assert s > 0


def test_sortino_penalizes_downside_only():
    # Two series with same total return; one has downside vol, one doesn't.
    smooth = _daily_returns([100, 110, 120, 130, 140])
    volatile = _daily_returns([100, 110, 100, 120, 140])
    s_smooth = sortino_ratio(smooth, risk_free_rate=0.0)
    s_volatile = sortino_ratio(volatile, risk_free_rate=0.0)
    assert s_smooth > s_volatile  # Smooth has no downside


def test_max_drawdown_finds_peak_to_trough():
    df = _daily_returns([100, 110, 120, 90, 100, 80, 130])  # peak 120, trough 80 → -33.3%
    md = max_drawdown(df)
    assert md["drawdown"] == pytest.approx((120 - 80) / 120, abs=1e-4)


def test_romad():
    df = _daily_returns([100, 110, 90, 105])
    r = romad(df)
    assert isinstance(r, float)


def test_calmar_cagr_over_max_drawdown():
    df = _daily_returns([100, 110, 90, 105, 120])
    c = calmar_ratio(df)
    assert isinstance(c, float)


# ---- Trade-based metrics ----

def _make_trades(realized_pnls):
    """Each pnl creates one round-trip trade (open + close at same price+pnl)."""
    return [{"realized_pnl": p, "timestamp": f"2024-01-{i+1:02d}T00:00:00+00:00"}
            for i, p in enumerate(realized_pnls)]


def test_win_rate():
    trades = _make_trades([10, 20, -5, 15, -10])  # 3 wins / 5
    assert win_rate(trades) == pytest.approx(0.6, abs=1e-6)


def test_profit_factor():
    trades = _make_trades([10, 20, -5, 15, -10])  # gross profit 45, gross loss 15
    assert profit_factor(trades) == pytest.approx(45/15, abs=1e-6)


def test_avg_win_and_loss():
    trades = _make_trades([10, 20, -5, 15, -10])
    assert avg_win(trades) == pytest.approx((10+20+15)/3, abs=1e-6)
    assert avg_loss(trades) == pytest.approx((-5-10)/2, abs=1e-6)


def test_expectancy():
    trades = _make_trades([10, 20, -5, 15, -10])
    # E = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    expected = 0.6 * 15.0 + 0.4 * -7.5
    assert expectancy(trades) == pytest.approx(expected, abs=1e-6)


def test_longest_streak():
    trades = _make_trades([10, 20, -5, 15, 30, 5, -10, -20, 5])
    assert longest_streak(trades, win=True) == 3  # 15, 30, 5
    assert longest_streak(trades, win=False) == 2  # -10, -20


def test_compute_all_returns_dict():
    df = _daily_returns([100, 102, 105, 100, 110])
    trades = _make_trades([5, -2, 10, -3])
    out = compute_all(df, trades, initial_cash=100, risk_free_rate=0.04)
    assert "total_return" in out
    assert "sharpe_ratio" in out
    assert "win_rate" in out
    assert "total_fees_paid" not in out  # fees come from trade dicts, separate sum
```

- [ ] **F4.2 — Run + confirm fail**

Run: `pytest tests/coordinator/services/test_backtest_metrics.py -v`. Expected: ImportError.

- [ ] **F4.3 — Implement `backtest_metrics.py`**

```python
# coordinator/services/backtest_metrics.py
"""Backtest performance metrics.

Functions accept a daily-resampled DataFrame with a 'return' column
and/or a list of trade dicts with 'realized_pnl'. Pure math; no I/O,
no DB. Matches Lumibot's metric set (lumibot/tools/indicators.py) plus
Sortino, Calmar, win-rate, profit-factor, expectancy, streak/drawdown
period analytics.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

import pandas as pd


# ---- Equity-curve metrics ----

def total_return(df: pd.DataFrame, initial_cash: float) -> float:
    if df.empty:
        return 0.0
    final = df["portfolio_value"].iloc[-1]
    return (final / initial_cash) - 1.0


def cagr(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    df_sorted = df.sort_index()
    cum = (1 + df_sorted["return"]).cumprod()
    total = float(cum.iloc[-1])
    start = df_sorted.index[0]
    end = df_sorted.index[-1]
    days = (end - start).days
    if days == 0:
        return 0.0
    period_years = days / 365.25
    if total <= 0:
        return -1.0
    return total ** (1 / period_years) - 1


def volatility(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    df_sorted = df.sort_index()
    start = df_sorted.index[0]
    end = df_sorted.index[-1]
    days = (end - start).days
    if days == 0:
        return 0.0
    period_years = days / 365.25
    ratio = df_sorted["return"].count() / period_years
    return float(df_sorted["return"].std() * math.sqrt(ratio))


def sharpe_ratio(df: pd.DataFrame, risk_free_rate: float = 0.04) -> float:
    vol = volatility(df)
    if vol == 0:
        return 0.0
    return (cagr(df) - risk_free_rate) / vol


def sortino_ratio(df: pd.DataFrame, risk_free_rate: float = 0.04) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    downside = df["return"][df["return"] < 0]
    if downside.empty:
        return 0.0
    df_sorted = df.sort_index()
    days = (df_sorted.index[-1] - df_sorted.index[0]).days
    period_years = max(days / 365.25, 1e-9)
    downside_vol = float(downside.std() * math.sqrt(df_sorted["return"].count() / period_years))
    if downside_vol == 0:
        return 0.0
    return (cagr(df) - risk_free_rate) / downside_vol


def max_drawdown(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"drawdown": 0.0, "date": None}
    df_sorted = df.sort_index().copy()
    cum = (1 + df_sorted["return"]).cumprod()
    cum_max = cum.cummax()
    drawdown = (cum_max - cum) / cum_max
    dd_max = float(drawdown.max())
    if math.isnan(dd_max):
        return {"drawdown": 0.0, "date": None}
    return {"drawdown": dd_max, "date": drawdown.idxmax()}


def romad(df: pd.DataFrame) -> float:
    md = max_drawdown(df)
    if md["drawdown"] == 0:
        return 0.0
    return cagr(df) / md["drawdown"]


def calmar_ratio(df: pd.DataFrame) -> float:
    """Same as RoMaD but with explicit naming."""
    return romad(df)


def longest_drawdown_days(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df_sorted = df.sort_index()
    cum = (1 + df_sorted["return"]).cumprod()
    cum_max = cum.cummax()
    underwater = cum < cum_max
    if not underwater.any():
        return 0
    # Group consecutive runs of underwater periods
    longest = 0
    current = 0
    for u in underwater:
        if u:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def top_n_drawdowns(df: pd.DataFrame, n: int = 5) -> list[dict]:
    """Return top-n drawdown periods sorted by depth, with start/end/recovery."""
    if df.empty:
        return []
    df_sorted = df.sort_index()
    cum = (1 + df_sorted["return"]).cumprod()
    cum_max = cum.cummax()
    drawdown_pct = (cum_max - cum) / cum_max

    periods = []
    in_dd = False
    start_idx = None
    peak_idx = None
    trough_idx = None
    trough_dd = 0.0
    for ts, dd in drawdown_pct.items():
        if dd > 0 and not in_dd:
            in_dd = True
            start_idx = ts
            peak_idx = ts
            trough_idx = ts
            trough_dd = dd
        elif dd > 0 and in_dd:
            if dd > trough_dd:
                trough_idx = ts
                trough_dd = dd
        elif dd == 0 and in_dd:
            in_dd = False
            periods.append({
                "start": start_idx.isoformat(),
                "trough": trough_idx.isoformat(),
                "recovered": ts.isoformat(),
                "depth": float(trough_dd),
            })
            start_idx = peak_idx = trough_idx = None
            trough_dd = 0.0
    if in_dd:  # Ongoing drawdown at end of series
        periods.append({
            "start": start_idx.isoformat(),
            "trough": trough_idx.isoformat(),
            "recovered": None,
            "depth": float(trough_dd),
        })
    periods.sort(key=lambda p: p["depth"], reverse=True)
    return periods[:n]


# ---- Trade-based metrics ----

def round_trip_trades(trades: list[dict]) -> list[dict]:
    """Return trades that have a non-null realized_pnl (i.e., closed positions).

    The engine writes one trade dict per fill, but only the closing fills
    carry realized_pnl. v1 keeps it simple: any trade with realized_pnl
    counts as one round-trip.
    """
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
        "drawdown_periods": top_n_drawdowns(df, n=5),
    }
```

- [ ] **F4.4 — Run + pass**

Run: `pytest tests/coordinator/services/test_backtest_metrics.py -v`. Expected: 14 PASS.

- [ ] **F4.5 — Commit**

```bash
git add coordinator/services/backtest_metrics.py tests/coordinator/services/test_backtest_metrics.py
git commit -m "feat(backtest): metrics — Lumibot-grade + Sortino/Calmar/streaks

Spec D §5. Pure functions accepting equity-curve DataFrame and trade
dict list. Computes total return, CAGR, volatility, Sharpe, Sortino,
Calmar, max drawdown, RoMaD, longest drawdown days, top-N drawdown
periods, win rate, profit factor, avg win/loss, expectancy,
winning/losing streaks. compute_all() bundles everything."
```

### Work unit F5: Add `quantstats` dependency

**Branch:** `plan/D-F5-quantstats-dep`

**Files:**
- Modify: `pyproject.toml`
- Optional smoke: a tiny test that `import quantstats` works.

- [ ] **F5.1 — Add to pyproject.toml**

Read `pyproject.toml`, locate the dependencies section, append:

```
quantstats>=0.0.62
```

Per Spec D, this is for the optional HTML tearsheet. quantstats pulls in matplotlib + statsmodels + seaborn + tabulate.

- [ ] **F5.2 — Install + smoke**

```bash
cd /home/jkern/dev/quilt-trader && pip3 install --user 'quantstats>=0.0.62'
python3 -c "import quantstats as qs; print(qs.__version__)"
```

- [ ] **F5.3 — Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): add quantstats for HTML tearsheet

Spec D §6. quantstats>=0.0.62 powers the Lumibot-style HTML report
generated post-backtest. Pulls in matplotlib/statsmodels/seaborn —
accepted tradeoff for the tearsheet payoff."
```

---

**End of Phase 0.** All five units merge before Phase 1.

---

## Phase 1 — Engine

One work unit. Largest single piece of code in the plan. Depends on F2 (TickContext), F3 (config), F1 (BacktestRun model — for the trade dict shape, since trades land on the row).

### Work unit E1: `BacktestEngine`

**Branch:** `plan/D-E1-engine`

**Files:**
- Create: `coordinator/services/backtest_engine_v2.py` (named v2 because the existing `backtest_engine.py` holds the comparator — we leave it untouched; Phase 2's C2 unifies via observer)
- Test: `tests/coordinator/services/test_backtest_engine.py`

Implements the observer-driven step loop + fill simulation per Spec D §3.

- [ ] **E1.1 — Failing tests covering conservative fill rules**

```python
# tests/coordinator/services/test_backtest_engine.py
"""Tests for BacktestEngine. These are correctness-critical — if any
regress, the engine is broken at its core. See Spec D §9."""
import pytest
import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timezone
from coordinator.services.backtest_engine_v2 import (
    BacktestEngine, EngineObserver, FillRecord, EngineSummary, CancelToken,
)
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import TradingFee, SlippageModel


class RecordingObserver:
    def __init__(self):
        self.signals = []
        self.fills = []
        self.equity = []
        self.complete = False
        self.error = None
        self.rejected = []

    def on_tick(self, sim_time, ctx_snapshot): pass
    def on_signals_emitted(self, sim_time, signals): self.signals.append((sim_time, signals))
    def on_fill(self, fill): self.fills.append(fill)
    def on_signal_rejected(self, sim_time, signal, reason): self.rejected.append((sim_time, signal, reason))
    def on_equity_point(self, sim_time, portfolio_value, cash, positions):
        self.equity.append({"sim_time": sim_time, "pv": portfolio_value, "cash": cash, "positions": positions})
    def on_complete(self, summary): self.complete = True
    def on_error(self, exc): self.error = exc


def _bars(start, n, opens=None, highs=None, lows=None, closes=None, vols=None):
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq="D", tz="UTC"),
        "open":  opens or [100.0]*n,
        "high":  highs or [101.0]*n,
        "low":   lows or [99.0]*n,
        "close": closes or [100.5]*n,
        "volume": vols or [1_000_000]*n,
    })


class _BuyOnceAlgo:
    """Algorithm that emits a single market BUY on its first tick, then nothing."""
    def __init__(self):
        self._fired = False
    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx):
        from sdk.signals import Signal, SignalLeg, SignalType, OrderType
        if self._fired:
            return []
        self._fired = True
        return [Signal(legs=[SignalLeg(
            symbol="SPY", signal_type=SignalType.BUY, quantity=1,
            asset_type="equities", order_type=OrderType.MARKET,
        )])]
    def on_stop(self): return {}
    def save_state(self): return {}


def test_market_order_fills_at_next_bar_open_with_slippage_never_signal_bar():
    """Spec D §9.3. A signal on bar T fills at bar T+1's open + slippage. Never bar T."""
    clock = _bars("2024-01-01", 5, opens=[100, 102, 105, 110, 115])
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={}, cash=10_000.0,
    )
    obs = RecordingObserver()
    engine = BacktestEngine()
    engine.run(
        algorithm=_BuyOnceAlgo(),
        ctx=ctx,
        clock_series=clock,
        clock_timeframe="1day",
        clock_source="polygon",
        clock_symbol="SPY",
        slippage=SlippageModel(market_bps=5.0),
        buy_fees=[], sell_fees=[],
        initial_cash=10_000.0,
        observer=obs,
        cancel_token=CancelToken(),
    )
    # Signal emitted at end of bar 0 (sim_time = bar 0 close = 2024-01-02 00:00 UTC).
    # Fill MUST be at bar 1's open = 102.0 + 5bps = 102.051.
    assert len(obs.fills) == 1
    fill = obs.fills[0]
    assert fill.symbol == "SPY"
    assert fill.timestamp == pd.Timestamp("2024-01-02", tz="UTC")  # bar 1's timestamp
    expected_price = 102.0 * (1 + 5/10000)
    assert fill.fill_price == pytest.approx(expected_price, abs=1e-6)
    assert fill.requested_price == pytest.approx(102.0, abs=1e-6)


def test_limit_order_requires_strict_cross():
    """Spec D §9.5. Buy limit at $100: low=100 → no fill; low=99.99 → fill; low=100.01 → no fill."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType

    class LimitAlgo:
        def __init__(self, limit_price): self.limit_price = limit_price; self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                asset_type="equities", order_type=OrderType.LIMIT, limit_price=self.limit_price,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    def _run(next_bar_low, limit=100.0):
        clock = _bars("2024-01-01", 3, lows=[99.0, next_bar_low, 99.0],
                      highs=[101.0]*3, opens=[100.0]*3, closes=[100.5]*3)
        ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
        obs = RecordingObserver()
        BacktestEngine().run(
            algorithm=LimitAlgo(limit_price=limit), ctx=ctx, clock_series=clock,
            clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
            slippage=SlippageModel(), buy_fees=[], sell_fees=[],
            initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
        )
        return obs

    obs_exact = _run(next_bar_low=100.0)
    assert len(obs_exact.fills) == 0  # Exact touch — no fill

    obs_cross = _run(next_bar_low=99.99)
    assert len(obs_cross.fills) == 1  # Strict cross — fills
    assert obs_cross.fills[0].fill_price == pytest.approx(100.0, abs=1e-6)

    obs_no_cross = _run(next_bar_low=100.01)
    assert len(obs_no_cross.fills) == 0  # Didn't cross


def test_fee_recorded_with_breakdown():
    clock = _bars("2024-01-01", 3, opens=[100.0]*3)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=_BuyOnceAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0),  # No slippage so fees easy to verify
        buy_fees=[TradingFee(flat_fee=1.0, percent_fee=0.001, taker=True)],
        sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    fill = obs.fills[0]
    # Fee = flat_fee(1.0) + percent_fee(0.001) * 100.0 * 1.0 = 1.1
    assert fill.fees == pytest.approx(1.1, abs=1e-6)
    assert len(fill.fee_breakdown) == 1


def test_no_path_to_same_bar_fill():
    """Spec D §9.4. Regression guard — pending orders are processed AFTER on_tick returns and AGAINST the next iteration."""
    clock = _bars("2024-01-01", 5, opens=[100, 102, 105, 110, 115])
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=_BuyOnceAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=5.0), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    # Signal emitted while sim_time = end of bar 0. Fill MUST NOT have a timestamp <= bar 0.
    assert obs.fills[0].timestamp > pd.Timestamp("2024-01-01 23:59:59", tz="UTC")
    assert obs.fills[0].timestamp >= pd.Timestamp("2024-01-02", tz="UTC")


def test_options_leg_fails_run_cleanly():
    """Spec D §9.11. A signal with asset_type=options halts the run cleanly."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType

    class OptionsAlgo:
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            return [Signal(legs=[SignalLeg(
                symbol="SPY240620C00500000", signal_type=SignalType.BUY, quantity=1,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 3, opens=[100.0]*3)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=OptionsAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is not None
    assert "options" in str(obs.error).lower()


def test_cancel_token_stops_engine_cleanly():
    cancel = CancelToken()
    cancel.set()  # Pre-cancelled
    clock = _bars("2024-01-01", 100)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=_BuyOnceAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=cancel,
    )
    # Engine should exit early with no fills (cancel was set before run started)
    assert not obs.complete
    assert len(obs.fills) == 0
```

- [ ] **E1.2 — Confirm tests fail (module doesn't exist)**

Run: `pytest tests/coordinator/services/test_backtest_engine.py -v`. Expected: ImportError.

- [ ] **E1.3 — Implement `backtest_engine_v2.py`**

```python
# coordinator/services/backtest_engine_v2.py
"""BacktestEngine — observer-driven, persistence-free simulation.

Spec D §3. Conservative-by-default fill model:
- No same-bar fills (signal at bar T → fill at T+1 at earliest).
- Market: next-bar open + slippage.
- Limit: strict cross required (price strictly past limit, not touch).
- Stop / stop-limit: trigger then market/limit on +2 bars.
- Multi-leg: per-leg independent fill timeline.

The engine is intentionally simple-but-pessimistic. See Spec D for full
discussion. The persistence-free design lets two callers consume it:
BacktestRunner (one-shot, persists to BacktestRun) and
ParallelBacktestFeeder (DecisionLog producer for BacktestComparison).
"""
from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

import pandas as pd

from coordinator.services.backtest_tick_context import BacktestTickContext, timeframe_to_seconds
from coordinator.services.backtest_config import SlippageModel, TradingFee
from sdk.signals import Signal, SignalLeg, SignalType, OrderType

logger = logging.getLogger(__name__)


class UnsupportedAssetTypeError(Exception):
    """Raised when the engine encounters an asset class it doesn't support yet."""


@dataclass
class CancelToken:
    _set: bool = False
    def set(self): self._set = True
    def is_set(self) -> bool: return self._set


@dataclass
class FillRecord:
    timestamp: datetime
    symbol: str
    asset_type: str
    side: str
    quantity: float
    requested_price: float
    fill_price: float
    slippage_dollars: float
    slippage_bps_applied: float
    fees: float
    fee_breakdown: list[dict]
    signal_id: str
    realized_pnl: Optional[float] = None  # Set on closing fills (round-trip)


@dataclass
class EngineSummary:
    total_bars: int
    total_signals: int
    total_fills: int
    final_cash: float
    final_portfolio_value: float


class EngineObserver(Protocol):
    def on_tick(self, sim_time: datetime, ctx_snapshot: dict) -> None: ...
    def on_signals_emitted(self, sim_time: datetime, signals: list[Signal]) -> None: ...
    def on_fill(self, fill: FillRecord) -> None: ...
    def on_signal_rejected(self, sim_time: datetime, signal: Signal, reason: str) -> None: ...
    def on_equity_point(self, sim_time: datetime, portfolio_value: float, cash: float, positions: list[dict]) -> None: ...
    def on_complete(self, summary: EngineSummary) -> None: ...
    def on_error(self, exc: Exception) -> None: ...


@dataclass
class _PendingOrder:
    signal_id: str
    leg: SignalLeg
    scheduled_for_bar_index: int   # Index in clock_series; fill attempted at this bar (and possibly later for stops)
    is_stop_triggered: bool = False  # Stop-to-market two-stage tracking


@dataclass
class _PositionState:
    quantity: float = 0.0
    avg_price: float = 0.0
    asset_type: str = "equities"


class BacktestEngine:
    def run(
        self,
        *,
        algorithm,                  # QuiltAlgorithm-like
        ctx: BacktestTickContext,
        clock_series: pd.DataFrame,
        clock_timeframe: str,
        clock_source: str,
        clock_symbol: str,
        slippage: SlippageModel,
        buy_fees: list[TradingFee],
        sell_fees: list[TradingFee],
        initial_cash: float,
        observer: EngineObserver,
        cancel_token: CancelToken,
        progress_callback: Optional[Callable[[float], None]] = None,
        rng_seed: int = 12345,
    ) -> None:
        try:
            self._run_internal(
                algorithm=algorithm, ctx=ctx, clock=clock_series,
                clock_tf=clock_timeframe, clock_source=clock_source, clock_symbol=clock_symbol,
                slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                initial_cash=initial_cash, observer=observer, cancel=cancel_token,
                progress=progress_callback, rng_seed=rng_seed,
            )
        except Exception as exc:
            logger.exception("BacktestEngine.run failed")
            observer.on_error(exc)

    def _run_internal(
        self, *, algorithm, ctx, clock, clock_tf, clock_source, clock_symbol,
        slippage, buy_fees, sell_fees, initial_cash, observer, cancel,
        progress, rng_seed,
    ):
        cash = initial_cash
        positions: dict[tuple, _PositionState] = {}
        pending: list[_PendingOrder] = []
        all_fills: list[FillRecord] = []
        all_signals_count = 0
        tf_duration = timeframe_to_seconds(clock_tf)
        rng = random.Random(rng_seed)

        # Wrap algorithm lifecycle in try/except so errors propagate via observer
        algorithm.on_start({}, None)

        for bar_idx in range(len(clock)):
            if cancel.is_set():
                logger.info("BacktestEngine cancelled at bar %d", bar_idx)
                return

            bar = clock.iloc[bar_idx]
            sim_time = (bar["timestamp"].to_pydatetime() +
                        pd.Timedelta(seconds=tf_duration).to_pytimedelta())
            ctx.set_sim_time(sim_time)
            # Update context with current account state for the algorithm's `ctx.positions/cash` reads
            ctx_positions = self._positions_for_context(positions)
            ctx.update_account(
                cash=cash,
                account_value=cash + self._positions_market_value(positions, bar),
                buying_power=cash,
                positions=ctx_positions,
            )

            # ---- 1. Tick the algorithm ----
            observer.on_tick(sim_time, {"cash": cash})
            try:
                signals = algorithm.on_tick(ctx) or []
            except Exception as exc:
                raise

            if signals:
                # Validate options asset_type — fast fail per Spec D §12
                for sig in signals:
                    for leg in sig.legs:
                        if leg.asset_type == "options":
                            raise UnsupportedAssetTypeError(
                                f"Options backtest not yet supported (leg: {leg.symbol}). "
                                f"Tracked as a follow-up; see Spec D §12."
                            )
                all_signals_count += len(signals)
                observer.on_signals_emitted(sim_time, signals)
                # Schedule pending orders for the NEXT bar
                for sig in signals:
                    sig_id = str(uuid.uuid4())
                    for leg in sig.legs:
                        pending.append(_PendingOrder(
                            signal_id=sig_id, leg=leg,
                            scheduled_for_bar_index=bar_idx + 1,
                        ))

            # ---- 2. Process pending orders that target THIS bar ----
            still_pending: list[_PendingOrder] = []
            for po in pending:
                if po.scheduled_for_bar_index > bar_idx:
                    still_pending.append(po)
                    continue
                # Try to fill against THIS bar
                fill, advance_for_stop = self._try_fill(
                    po, bar=bar, slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                    cash=cash, positions=positions, rng=rng, sim_time=bar["timestamp"].to_pydatetime(),
                )
                if fill is not None:
                    cash = self._apply_fill(cash, positions, fill)
                    all_fills.append(fill)
                    observer.on_fill(fill)
                elif advance_for_stop:
                    # Stop triggered this bar — re-schedule for next bar as a market order
                    po.is_stop_triggered = True
                    po.scheduled_for_bar_index = bar_idx + 1
                    still_pending.append(po)
                else:
                    # Not filled, not stop-trigger — apply expiry (v1 = 1 bar)
                    observer.on_signal_rejected(
                        sim_time, Signal(legs=[po.leg]), "no_fill_within_timeout"
                    )
            pending = still_pending

            # ---- 3. Mark-to-market equity point ----
            mtm_value = cash + self._positions_market_value(positions, bar)
            observer.on_equity_point(
                sim_time, mtm_value, cash, self._positions_snapshot(positions, bar),
            )

            if progress is not None and bar_idx % 100 == 0:
                progress(bar_idx / max(len(clock), 1))

        algorithm.on_stop()

        observer.on_complete(EngineSummary(
            total_bars=len(clock),
            total_signals=all_signals_count,
            total_fills=len(all_fills),
            final_cash=cash,
            final_portfolio_value=cash + self._positions_market_value(positions, clock.iloc[-1]),
        ))

    # ---- Fill simulation ----

    def _try_fill(
        self, po: _PendingOrder, *, bar, slippage: SlippageModel,
        buy_fees, sell_fees, cash, positions, rng, sim_time,
    ) -> tuple[Optional[FillRecord], bool]:
        """Returns (fill_or_none, stop_triggered).

        stop_triggered=True means this is a stop order that triggered this bar
        and should be re-scheduled as a market order for the next bar.
        """
        leg = po.leg
        ot = leg.order_type
        side = "buy" if leg.signal_type in (SignalType.BUY, SignalType.BUY_TO_COVER) else "sell"
        fees_list = buy_fees if side == "buy" else sell_fees

        if ot == OrderType.MARKET or po.is_stop_triggered:
            return self._fill_market(po, bar, side, slippage, fees_list, rng, sim_time), False

        if ot == OrderType.LIMIT:
            return self._fill_limit(po, bar, side, slippage, fees_list, sim_time), False

        if ot == OrderType.STOP:
            triggered = self._stop_triggered(po, bar, side)
            if triggered:
                return None, True
            return None, False

        if ot == OrderType.STOP_LIMIT:
            triggered = self._stop_triggered(po, bar, side)
            if triggered:
                # Convert to a pending limit at limit_price for the next bar
                # Engine reschedules; mark by setting order_type to limit via leg replacement.
                # Trick: caller advances via stop_triggered=True path, but we want a LIMIT next bar
                # not a market. Special handling: we set is_stop_triggered=True but the engine's
                # rescheduling will hit MARKET in the next iteration. To preserve limit semantics,
                # we replace the leg's order_type to LIMIT (Python dataclass mutation).
                po.leg = SignalLeg(
                    symbol=leg.symbol, signal_type=leg.signal_type, quantity=leg.quantity,
                    asset_type=leg.asset_type, order_type=OrderType.LIMIT,
                    limit_price=leg.limit_price, stop_price=None,
                )
                return None, True
            return None, False

        raise ValueError(f"Unsupported order_type: {ot}")

    def _fill_market(self, po, bar, side, slippage, fees_list, rng, sim_time) -> FillRecord:
        leg = po.leg
        if slippage.use_bar_range:
            fill_price = rng.uniform(float(bar["low"]), float(bar["high"]))
            slip_bps = abs(fill_price - float(bar["open"])) / float(bar["open"]) * 10000
        else:
            sign = 1 if side == "buy" else -1
            slip = float(bar["open"]) * (slippage.market_bps / 10000) * sign
            fill_price = float(bar["open"]) + slip
            slip_bps = slippage.market_bps

        # Volume impact, optionally additive
        if slippage.volume_impact_bps_per_pct > 0 and float(bar["volume"]) > 0:
            pct_consumed = (leg.quantity / float(bar["volume"])) * 100
            extra_bps = pct_consumed * slippage.volume_impact_bps_per_pct
            extra_sign = 1 if side == "buy" else -1
            fill_price += float(bar["open"]) * (extra_bps / 10000) * extra_sign
            slip_bps += extra_bps

        requested = float(bar["open"])
        fees, breakdown = self._compute_fees(leg, fill_price, fees_list, order_type=OrderType.MARKET)
        return FillRecord(
            timestamp=bar["timestamp"].to_pydatetime(), symbol=leg.symbol,
            asset_type=leg.asset_type, side=side, quantity=leg.quantity,
            requested_price=requested, fill_price=fill_price,
            slippage_dollars=abs(fill_price - requested) * leg.quantity,
            slippage_bps_applied=slip_bps, fees=fees, fee_breakdown=breakdown,
            signal_id=po.signal_id,
        )

    def _fill_limit(self, po, bar, side, slippage, fees_list, sim_time) -> Optional[FillRecord]:
        leg = po.leg
        limit = leg.limit_price
        if limit is None:
            return None
        low, high = float(bar["low"]), float(bar["high"])
        # STRICT cross only — see Spec D §3 conservative-by-default rule
        if side == "buy":
            if not (low < limit):
                return None
        else:
            if not (high > limit):
                return None
        fill_price = limit  # Limits never fill worse than the limit
        if slippage.limit_bps > 0:
            # Edge case: model brokers that take a small bps even on limits
            sign = 1 if side == "buy" else -1
            fill_price += limit * (slippage.limit_bps / 10000) * sign
        fees, breakdown = self._compute_fees(leg, fill_price, fees_list, order_type=OrderType.LIMIT)
        return FillRecord(
            timestamp=bar["timestamp"].to_pydatetime(), symbol=leg.symbol,
            asset_type=leg.asset_type, side=side, quantity=leg.quantity,
            requested_price=limit, fill_price=fill_price,
            slippage_dollars=abs(fill_price - limit) * leg.quantity,
            slippage_bps_applied=slippage.limit_bps, fees=fees, fee_breakdown=breakdown,
            signal_id=po.signal_id,
        )

    def _stop_triggered(self, po, bar, side) -> bool:
        leg = po.leg
        stop = leg.stop_price
        if stop is None:
            return False
        low, high = float(bar["low"]), float(bar["high"])
        return low <= stop <= high

    def _compute_fees(self, leg: SignalLeg, fill_price: float, fees_list: list[TradingFee],
                      order_type: OrderType) -> tuple[float, list[dict]]:
        is_maker = order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
        is_taker = not is_maker
        total = 0.0
        breakdown = []
        for tf in fees_list:
            applies = (tf.maker and is_maker) or (tf.taker and is_taker)
            if not applies:
                continue
            f = tf.flat_fee + fill_price * leg.quantity * tf.percent_fee
            total += f
            breakdown.append({
                "flat_fee": tf.flat_fee, "percent_fee": tf.percent_fee,
                "maker": tf.maker, "taker": tf.taker, "computed": f,
            })
        return total, breakdown

    # ---- Position tracking ----

    def _apply_fill(self, cash: float, positions: dict, fill: FillRecord) -> float:
        key = (fill.symbol,)  # Equities/crypto key for v1
        ps = positions.get(key) or _PositionState(asset_type=fill.asset_type)
        notional = fill.fill_price * fill.quantity
        if fill.side == "buy":
            # Weighted average price update
            total_qty = ps.quantity + fill.quantity
            if total_qty == 0:
                ps.avg_price = 0.0
            else:
                ps.avg_price = (ps.avg_price * ps.quantity + fill.fill_price * fill.quantity) / total_qty
            ps.quantity = total_qty
            cash -= notional + fill.fees
        else:  # sell
            # Realized PnL on the sold portion
            realized = (fill.fill_price - ps.avg_price) * fill.quantity - fill.fees
            fill.realized_pnl = realized
            ps.quantity -= fill.quantity
            if ps.quantity == 0:
                ps.avg_price = 0.0
            cash += notional - fill.fees
        positions[key] = ps
        if ps.quantity == 0:
            del positions[key]
        return cash

    def _positions_market_value(self, positions: dict, bar) -> float:
        # v1 simplification: use the clock bar's close as the price proxy for ALL held positions.
        # Multi-symbol with different clocks would need per-symbol lookup — out of scope for v1.
        close = float(bar["close"])
        return sum(ps.quantity * close for ps in positions.values())

    def _positions_snapshot(self, positions: dict, bar) -> list[dict]:
        close = float(bar["close"])
        return [
            {"symbol": k[0], "quantity": ps.quantity, "avg_price": ps.avg_price,
             "current_price": close, "market_value": ps.quantity * close,
             "asset_type": ps.asset_type}
            for k, ps in positions.items()
        ]

    def _positions_for_context(self, positions: dict) -> dict:
        # Convert internal state to sdk.models.Position dict the algorithm reads via ctx.positions
        from sdk.models import Position
        out = {}
        for (sym,), ps in positions.items():
            # Use a minimal Position dataclass; SDK may have more fields — fill defaults
            try:
                out[sym] = Position(symbol=sym, quantity=ps.quantity, avg_price=ps.avg_price)
            except TypeError:
                # If sdk.models.Position has different signature, fall back to bare dict
                out[sym] = {"symbol": sym, "quantity": ps.quantity, "avg_price": ps.avg_price}
        return out
```

- [ ] **E1.4 — Run engine tests, confirm pass**

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/services/test_backtest_engine.py -v`. Expected: 6 tests PASS.

- [ ] **E1.5 — Commit**

```bash
git add coordinator/services/backtest_engine_v2.py tests/coordinator/services/test_backtest_engine.py
git commit -m "feat(backtest): observer-driven engine with conservative fills

Spec D §3. Persistence-free, callback-driven simulation. Market fills
at next-bar open + slippage; limits require strict cross; stops
trigger then market-fill at +2 bars; options legs fail fast. The
existing comparator-only module backtest_engine.py is untouched —
this is backtest_engine_v2.py; Phase 2's C2 unifies via the observer
protocol."
```

---

**End of Phase 1.** E1 merges before Phase 2.

---

## Phase 2 — Consumers + API

Three parallel work units.

### Work unit C1: `BacktestRunner`

**Branch:** `plan/D-C1-runner`

**Files:**
- Create: `coordinator/services/backtest_runner.py`
- Test: `tests/coordinator/services/test_backtest_runner.py`

Owns persistence to the `BacktestRun` row. Wires the engine, the download manager (for missing data), and the metrics module.

- [ ] **C1.1 — Failing test (orchestration with mocked engine + downloads)**

```python
# tests/coordinator/services/test_backtest_runner.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from coordinator.services.backtest_runner import BacktestRunner


@pytest.mark.asyncio
async def test_runner_creates_row_and_advances_status(test_app, db_session):
    """End-to-end with mocked engine: queued → downloading_data → running → completed."""
    from coordinator.database.models import Algorithm, BacktestRun
    algo = Algorithm(name="test-algo", repo_url="https://example/x", install_status="installed")
    db_session.add(algo); await db_session.flush()

    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        initial_cash=10_000.0,
    )
    db_session.add(run); await db_session.commit()

    # Mock everything that's NOT the runner itself.
    with patch("coordinator.services.backtest_runner._load_manifest") as load_manifest, \
         patch("coordinator.services.backtest_runner._has_coverage", return_value=True) as has_cov, \
         patch("coordinator.services.backtest_runner._load_bar_series") as load_bars, \
         patch("coordinator.services.backtest_runner._load_algorithm_class") as load_class, \
         patch("coordinator.services.backtest_runner.BacktestEngine") as mock_engine_cls:
        load_manifest.return_value = MagicMock(
            requirements=MagicMock(data_dependencies=[
                {"symbol": "SPY", "timeframe": "1day", "source": "polygon"},
            ]),
        )
        # Engine immediately calls observer.on_complete
        def fake_engine_run(**kwargs):
            obs = kwargs["observer"]
            from coordinator.services.backtest_engine_v2 import EngineSummary
            obs.on_equity_point(datetime(2024, 1, 1, tzinfo=timezone.utc), 10_000.0, 10_000.0, [])
            obs.on_complete(EngineSummary(total_bars=10, total_signals=0, total_fills=0,
                                          final_cash=10_000.0, final_portfolio_value=10_000.0))
        mock_engine_cls.return_value.run = fake_engine_run
        load_bars.return_value = MagicMock(empty=False)
        load_class.return_value = MagicMock  # returns the class, instantiation happens inside runner

        runner = BacktestRunner(session_factory=None, download_manager=MagicMock(), data_service=MagicMock())
        await runner.run(run.id)

    from sqlalchemy import select
    refreshed = (await db_session.execute(
        select(BacktestRun).where(BacktestRun.id == run.id)
    )).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "completed"
```

- [ ] **C1.2 — Implement `BacktestRunner`**

```python
# coordinator/services/backtest_runner.py
"""BacktestRunner — orchestrates a Spec D one-shot backtest.

1. Loads BacktestRun + Algorithm.
2. Parses manifest data_dependencies.
3. Checks each (source, symbol, timeframe) has parquet coverage; downloads missing.
4. Builds BacktestTickContext, loads algorithm class.
5. Runs BacktestEngine with a persistence-aware observer.
6. Computes metrics, persists everything to the BacktestRun row.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from sqlalchemy import select

from coordinator.services.backtest_config import SlippageModel, TradingFee
from coordinator.services.backtest_engine_v2 import (
    BacktestEngine, CancelToken, EngineObserver, EngineSummary, FillRecord,
)
from coordinator.services.backtest_metrics import compute_all
from coordinator.services.backtest_tick_context import BacktestTickContext

logger = logging.getLogger(__name__)


def _load_manifest(algo_name: str):
    from sdk.manifest import QuiltManifest
    return QuiltManifest.from_file(Path("data/packages") / algo_name / "quilt.yaml")


def _has_coverage(data_service, source, symbol, timeframe, start, end) -> bool:
    df = data_service.load_market_data(source, symbol, timeframe)
    if df is None or df.empty:
        return False
    df_min = pd.to_datetime(df["timestamp"]).min()
    df_max = pd.to_datetime(df["timestamp"]).max()
    return df_min <= pd.Timestamp(start) and df_max >= pd.Timestamp(end)


def _load_bar_series(data_service, source, symbol, timeframe) -> pd.DataFrame:
    return data_service.load_market_data(source, symbol, timeframe)


def _load_algorithm_class(algo_name: str, manifest) -> type:
    import importlib.util, sys
    pkg_dir = Path("data/packages") / algo_name
    entry = pkg_dir / manifest.entry_point
    mod_name = f"_qt_backtest_{algo_name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, entry)
    mod = importlib.util.module_from_spec(spec)
    old = sys.path.copy()
    sys.path.insert(0, str(pkg_dir))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path = old
    return getattr(mod, manifest.class_name)


class _RunObserver:
    def __init__(self):
        self.equity_curve = []
        self.trades: list[dict] = []
        self.error: Optional[Exception] = None
        self.summary: Optional[EngineSummary] = None
        self.progress = 0.0

    def on_tick(self, sim_time, ctx_snapshot): pass
    def on_signals_emitted(self, sim_time, signals): pass
    def on_equity_point(self, sim_time, portfolio_value, cash, positions):
        self.equity_curve.append({
            "timestamp": sim_time.isoformat(),
            "portfolio_value": portfolio_value,
            "cash": cash,
            "positions": positions,
        })
    def on_fill(self, fill: FillRecord):
        self.trades.append({
            "timestamp": fill.timestamp.isoformat(),
            "symbol": fill.symbol,
            "asset_type": fill.asset_type,
            "side": fill.side,
            "quantity": fill.quantity,
            "requested_price": fill.requested_price,
            "fill_price": fill.fill_price,
            "slippage_dollars": fill.slippage_dollars,
            "slippage_bps_applied": fill.slippage_bps_applied,
            "fees": fill.fees,
            "fee_breakdown": fill.fee_breakdown,
            "signal_id": fill.signal_id,
            "realized_pnl": fill.realized_pnl,
        })
    def on_signal_rejected(self, sim_time, signal, reason): pass
    def on_complete(self, summary): self.summary = summary
    def on_error(self, exc): self.error = exc


class BacktestRunner:
    def __init__(self, session_factory, download_manager, data_service):
        self._sf = session_factory
        self._dm = download_manager
        self._ds = data_service

    async def run(self, run_id: str) -> None:
        from coordinator.database.models import Algorithm, BacktestRun

        async with self._sf() as session:
            run = (await session.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one()
            algo = (await session.execute(select(Algorithm).where(Algorithm.id == run.algorithm_id))).scalar_one()
            run.status = "downloading_data"
            run.started_at = datetime.now(timezone.utc)
            await session.commit()

        try:
            manifest = _load_manifest(algo.name)
            deps = manifest.requirements.data_dependencies or []

            # Stage 1: data coverage
            download_ids = []
            for dep in deps:
                source = dep.get("source") or "polygon"
                symbol = dep["symbol"]
                timeframe = dep["timeframe"]
                if not _has_coverage(self._ds, source, symbol, timeframe,
                                     run.date_range_start, run.date_range_end):
                    msg = f"Downloading {symbol} {timeframe} from {source}"
                    async with self._sf() as session:
                        r = (await session.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one()
                        r.progress_message = msg; await session.commit()
                    dl = await self._dm.create_download(
                        symbols=[symbol], date_range_start=run.date_range_start.date(),
                        date_range_end=run.date_range_end.date(),
                        provider=source, timeframe=timeframe,
                    )
                    download_ids.append(dl["id"])
                    # Wait for completion
                    await self._wait_for_download(dl["id"])

            # Stage 2: run engine
            async with self._sf() as session:
                r = (await session.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one()
                r.status = "running"
                r.progress_message = "Running backtest…"
                r.download_ids = download_ids
                await session.commit()

            # Build context
            bars = {}
            for dep in deps:
                source = dep.get("source") or "polygon"
                df = _load_bar_series(self._ds, source, dep["symbol"], dep["timeframe"])
                if df is None or df.empty:
                    raise RuntimeError(f"Missing data for {dep['symbol']} {dep['timeframe']} {source}")
                # Filter to the run's date range
                df = df[(df["timestamp"] >= pd.Timestamp(run.date_range_start)) &
                        (df["timestamp"] <= pd.Timestamp(run.date_range_end))].reset_index(drop=True)
                bars[(source, dep["symbol"], dep["timeframe"])] = df

            # Pick the smallest-timeframe series for the clock
            clock_key = self._smallest_timeframe_key(bars)
            clock_series = bars[clock_key]
            clock_source, clock_symbol, clock_tf = clock_key

            ctx = BacktestTickContext(
                bars=bars, positions={}, cash=run.initial_cash,
                default_source=clock_source,
            )

            AlgoClass = _load_algorithm_class(algo.name, manifest)
            algorithm = AlgoClass()

            slippage = SlippageModel(**(run.slippage_model or {}))
            buy_fees = [TradingFee(**f) for f in (run.buy_trading_fees or [])]
            sell_fees = [TradingFee(**f) for f in (run.sell_trading_fees or [])]

            observer = _RunObserver()
            cancel = CancelToken()
            BacktestEngine().run(
                algorithm=algorithm, ctx=ctx, clock_series=clock_series,
                clock_timeframe=clock_tf, clock_source=clock_source, clock_symbol=clock_symbol,
                slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                initial_cash=run.initial_cash, observer=observer, cancel_token=cancel,
            )
            if observer.error:
                raise observer.error

            # Stage 3: compute metrics, persist
            df = pd.DataFrame(observer.equity_curve)
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")
                df["return"] = df["portfolio_value"].pct_change().fillna(0)
                # Resample to daily for metric computation
                daily = df.resample("D").last().dropna()
                daily["return"] = daily["portfolio_value"].pct_change().fillna(0)
                metrics = compute_all(daily, observer.trades,
                                      initial_cash=run.initial_cash, risk_free_rate=0.04)
            else:
                metrics = {}

            async with self._sf() as session:
                r = (await session.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one()
                r.equity_curve = observer.equity_curve
                r.trades = observer.trades
                r.total_fees_paid = sum(t["fees"] for t in observer.trades)
                r.total_slippage_dollars = sum(t["slippage_dollars"] for t in observer.trades)
                # Apply metrics
                for k, v in metrics.items():
                    if k == "max_drawdown_date" and v is not None:
                        v = pd.Timestamp(v).to_pydatetime() if not isinstance(v, datetime) else v
                    if hasattr(r, k):
                        setattr(r, k, v)
                r.status = "completed"
                r.completed_at = datetime.now(timezone.utc)
                r.progress_message = "Backtest complete"
                r.progress_pct = 1.0
                await session.commit()
        except Exception as exc:
            logger.exception("BacktestRunner failed for %s", run_id)
            async with self._sf() as session:
                r = (await session.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one()
                r.status = "failed"
                r.error_message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                r.completed_at = datetime.now(timezone.utc)
                await session.commit()

    async def _wait_for_download(self, download_id: str, poll_s: float = 1.0) -> None:
        while True:
            status = await self._dm.get_download(download_id)
            if status and status.get("status") in ("completed", "failed", "cancelled"):
                if status.get("status") != "completed":
                    raise RuntimeError(f"Download {download_id} ended with status {status.get('status')}")
                return
            await asyncio.sleep(poll_s)

    def _smallest_timeframe_key(self, bars: dict) -> tuple:
        from coordinator.services.backtest_tick_context import timeframe_to_seconds
        return min(bars.keys(), key=lambda k: timeframe_to_seconds(k[2]) or 1e18)
```

- [ ] **C1.3 — Run + pass**

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/services/test_backtest_runner.py -v`. Expected: PASS.

- [ ] **C1.4 — Commit**

```bash
git add coordinator/services/backtest_runner.py tests/coordinator/services/test_backtest_runner.py
git commit -m "feat(backtest): runner — Spec D one-shot orchestrator

Walks manifest data_dependencies, downloads missing data via
DownloadManager, runs BacktestEngine, computes metrics, persists
to the BacktestRun row. Updates status / progress as it advances."
```

### Work unit C2: `ParallelBacktestFeeder` + scheduler update

**Branch:** `plan/D-C2-feeder`

**Files:**
- Create: `coordinator/services/parallel_backtest_feeder.py`
- Modify: `coordinator/services/backtest_scheduler.py` (the existing one)
- Test: `tests/coordinator/services/test_parallel_backtest_feeder.py`

- [ ] **C2.1 — Failing test for the feeder observer**

```python
# tests/coordinator/services/test_parallel_backtest_feeder.py
import pytest
from datetime import datetime, timezone
from coordinator.services.parallel_backtest_feeder import ParallelBacktestFeeder

@pytest.mark.asyncio
async def test_feeder_writes_decision_log_per_signal(test_app, db_session):
    from coordinator.database.models import Algorithm, AlgorithmInstance, Account, Worker, DecisionLog
    algo = Algorithm(name="x", repo_url="https://e/x", install_status="installed")
    account = Account(name="a", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["equities"], pdt_mode="off")
    worker = Worker(name="w")
    db_session.add_all([algo, account, worker]); await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=account.id, worker_id=worker.id, status="running")
    db_session.add(inst); await db_session.commit()

    from coordinator.api.dependencies import get_container
    feeder = ParallelBacktestFeeder(instance_id=inst.id, session_factory=get_container().session_factory)

    from sdk.signals import Signal, SignalLeg, SignalType
    sig = Signal(legs=[SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                                  asset_type="equities")])
    await feeder.on_signals_emitted_async(datetime(2024, 1, 15, tzinfo=timezone.utc), [sig])

    from sqlalchemy import select
    rows = (await db_session.execute(
        select(DecisionLog).where(DecisionLog.instance_id == inst.id)
                          .where(DecisionLog.mode == "backtest")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].signals_produced[0]["legs"][0]["symbol"] == "SPY"
```

- [ ] **C2.2 — Implement feeder**

```python
# coordinator/services/parallel_backtest_feeder.py
"""ParallelBacktestFeeder — feeds DecisionLog(mode=backtest) rows for BacktestComparison.

Implements EngineObserver. Only `on_signals_emitted` does meaningful work
(writes a DecisionLog row); other callbacks are no-ops because the
comparison feature only cares about signal-emission divergence vs live.

Spec D §11.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from coordinator.database.models import DecisionLog

logger = logging.getLogger(__name__)


class ParallelBacktestFeeder:
    def __init__(self, instance_id: str, session_factory):
        self._instance_id = instance_id
        self._sf = session_factory

    # ---- EngineObserver protocol ----

    def on_tick(self, sim_time, ctx_snapshot): pass
    def on_signal_rejected(self, sim_time, signal, reason): pass
    def on_fill(self, fill): pass
    def on_equity_point(self, sim_time, portfolio_value, cash, positions): pass
    def on_complete(self, summary): pass
    def on_error(self, exc):
        logger.warning("ParallelBacktestFeeder for instance %s: engine error %s",
                       self._instance_id, exc)

    def on_signals_emitted(self, sim_time, signals):
        # Engine calls this synchronously; we schedule the DB write on the loop.
        loop = asyncio.get_event_loop()
        loop.create_task(self.on_signals_emitted_async(sim_time, signals))

    async def on_signals_emitted_async(self, sim_time: datetime, signals: list):
        async with self._sf() as session:
            for sig in signals:
                session.add(DecisionLog(
                    instance_id=self._instance_id,
                    timestamp=sim_time,
                    mode="backtest",
                    signals_produced=[{
                        "legs": [{
                            "symbol": l.symbol,
                            "signal_type": l.signal_type.value if hasattr(l.signal_type, "value") else str(l.signal_type),
                            "quantity": l.quantity,
                            "asset_type": l.asset_type,
                        } for l in sig.legs],
                        "strategy_type": getattr(sig, "strategy_type", "single"),
                    }],
                ))
            await session.commit()
```

- [ ] **C2.3 — Update `backtest_scheduler.py` to use the engine**

The existing `BacktestSchedulerJob` reads from `DecisionLog` and computes a comparison; it doesn't run a parallel backtest. Extend it to invoke the engine through the feeder before running the comparison.

Read `coordinator/services/backtest_scheduler.py`. In `run()`, BEFORE the existing `_compare_instance` loop, add a stage that:

1. For each running instance: build a `BacktestTickContext` over the last `lookback_hours` of bar data.
2. Construct a `ParallelBacktestFeeder(instance_id, session_factory)`.
3. Call `BacktestEngine().run(...)` with the feeder as observer, with default `SlippageModel()` + empty fee lists.
4. After the engine returns, the existing `_compare_instance` flow runs — and now `DecisionLog(mode="backtest")` rows exist for the comparator to read.

Use the SAME helpers from C1 (`_load_manifest`, `_load_bar_series`, `_load_algorithm_class`).

- [ ] **C2.4 — Run + pass**

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/services/test_parallel_backtest_feeder.py tests/coordinator/test_backtest_scheduler.py -v`. Expected: PASS.

- [ ] **C2.5 — Commit**

```bash
git add coordinator/services/parallel_backtest_feeder.py coordinator/services/backtest_scheduler.py \
        tests/coordinator/services/test_parallel_backtest_feeder.py
git commit -m "feat(backtest): parallel feeder + scheduler integration

Spec D §11. ParallelBacktestFeeder writes DecisionLog(mode=backtest)
rows; the BacktestSchedulerJob now runs the engine through this
feeder, finally producing the backtest decision stream that the
existing BacktestComparator has always assumed exists."
```

### Work unit C3: API routes

**Branch:** `plan/D-C3-api`

**Files:**
- Create: `coordinator/api/routes/backtest_runs.py`
- Modify: `coordinator/main.py` (mount), `coordinator/api/dependencies.py` (container)
- Test: `tests/coordinator/test_backtest_runs_api.py`

- [ ] **C3.1 — Failing API tests**

```python
# tests/coordinator/test_backtest_runs_api.py
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_create_backtest_run_starts_task(client, db_session, monkeypatch):
    from coordinator.database.models import Algorithm
    algo = Algorithm(name="t", repo_url="https://e/t", install_status="installed")
    db_session.add(algo); await db_session.commit()

    # Stub runner.run to avoid actually running it
    from coordinator.api.routes import backtest_runs as routes
    async def fake_run(run_id): pass
    monkeypatch.setattr(routes, "_dispatch_runner", lambda app, run_id: None)

    body = {
        "algorithm_id": algo.id,
        "date_range_start": "2024-01-01T00:00:00+00:00",
        "date_range_end": "2024-02-01T00:00:00+00:00",
        "initial_cash": 25_000.0,
        "slippage_model": {"market_bps": 5.0},
        "benchmark_symbol": "SPY",
        "benchmark_source": "polygon",
    }
    r = await client.post("/api/backtest-runs", json=body)
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "queued"
    assert data["initial_cash"] == 25_000.0
    assert data["algorithm_id"] == algo.id


@pytest.mark.asyncio
async def test_list_backtest_runs(client, db_session):
    r = await client.get("/api/backtest-runs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_get_404(client):
    r = await client.get("/api/backtest-runs/missing")
    assert r.status_code == 404
```

- [ ] **C3.2 — Implement routes**

```python
# coordinator/api/routes/backtest_runs.py
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db, get_container
from coordinator.database.models import BacktestRun, Algorithm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest-runs", tags=["backtest-runs"])


class TradingFeeIn(BaseModel):
    flat_fee: float = 0.0
    percent_fee: float = 0.0
    maker: bool = True
    taker: bool = True


class SlippageModelIn(BaseModel):
    market_bps: float = 5.0
    limit_bps: float = 0.0
    use_bar_range: bool = False
    volume_impact_bps_per_pct: float = 0.0


class BacktestRunCreate(BaseModel):
    algorithm_id: str
    date_range_start: datetime
    date_range_end: datetime
    initial_cash: float = 100_000.0
    config_overrides: Optional[dict] = None
    buy_trading_fees: Optional[list[TradingFeeIn]] = None
    sell_trading_fees: Optional[list[TradingFeeIn]] = None
    slippage_model: Optional[SlippageModelIn] = None
    benchmark_symbol: Optional[str] = None
    benchmark_source: Optional[str] = None


def _to_response(r: BacktestRun) -> dict:
    return {
        "id": r.id, "algorithm_id": r.algorithm_id, "status": r.status,
        "date_range_start": r.date_range_start.isoformat() if r.date_range_start else None,
        "date_range_end": r.date_range_end.isoformat() if r.date_range_end else None,
        "initial_cash": r.initial_cash,
        "config_overrides": r.config_overrides,
        "buy_trading_fees": r.buy_trading_fees, "sell_trading_fees": r.sell_trading_fees,
        "slippage_model": r.slippage_model,
        "benchmark_symbol": r.benchmark_symbol, "benchmark_source": r.benchmark_source,
        "progress_message": r.progress_message, "progress_pct": r.progress_pct,
        "error_message": r.error_message,
        "total_return": r.total_return, "cagr": r.cagr, "volatility": r.volatility,
        "sharpe_ratio": r.sharpe_ratio, "sortino_ratio": r.sortino_ratio,
        "calmar_ratio": r.calmar_ratio, "max_drawdown": r.max_drawdown,
        "max_drawdown_date": r.max_drawdown_date.isoformat() if r.max_drawdown_date else None,
        "romad": r.romad, "total_fees_paid": r.total_fees_paid,
        "total_slippage_dollars": r.total_slippage_dollars,
        "trade_count": r.trade_count, "win_rate": r.win_rate,
        "profit_factor": r.profit_factor, "avg_win": r.avg_win, "avg_loss": r.avg_loss,
        "expectancy": r.expectancy,
        "longest_drawdown_days": r.longest_drawdown_days,
        "longest_winning_streak": r.longest_winning_streak,
        "longest_losing_streak": r.longest_losing_streak,
        "drawdown_periods": r.drawdown_periods,
        "tearsheet_path": r.tearsheet_path,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _dispatch_runner(container, run_id: str):
    """Spawn the runner as a background asyncio task."""
    runner = container.backtest_runner
    asyncio.create_task(runner.run(run_id))


@router.post("", status_code=201)
async def create_run(body: BacktestRunCreate, db: AsyncSession = Depends(get_db)):
    algo = (await db.execute(select(Algorithm).where(Algorithm.id == body.algorithm_id))).scalar_one_or_none()
    if algo is None:
        raise HTTPException(404, detail=f"Algorithm not found: {body.algorithm_id}")
    run = BacktestRun(
        algorithm_id=body.algorithm_id,
        date_range_start=body.date_range_start,
        date_range_end=body.date_range_end,
        initial_cash=body.initial_cash,
        config_overrides=body.config_overrides,
        buy_trading_fees=[f.dict() for f in body.buy_trading_fees] if body.buy_trading_fees else None,
        sell_trading_fees=[f.dict() for f in body.sell_trading_fees] if body.sell_trading_fees else None,
        slippage_model=body.slippage_model.dict() if body.slippage_model else None,
        benchmark_symbol=body.benchmark_symbol,
        benchmark_source=body.benchmark_source,
    )
    db.add(run); await db.flush()
    _dispatch_runner(get_container(), run.id)
    return _to_response(run)


@router.get("")
async def list_runs(
    algorithm_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    q = select(BacktestRun)
    if algorithm_id:
        q = q.where(BacktestRun.algorithm_id == algorithm_id)
    q = q.order_by(desc(BacktestRun.created_at)).limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [_to_response(r) for r in rows]


@router.get("/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    return _to_response(r)


@router.get("/{run_id}/equity-curve")
async def get_equity_curve(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    return {"items": r.equity_curve or []}


@router.get("/{run_id}/trades")
async def get_trades(run_id: str, limit: int = Query(500, ge=1, le=5000),
                     offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    trades = r.trades or []
    return {"total": len(trades), "items": trades[offset:offset + limit]}


@router.get("/{run_id}/tearsheet")
async def get_tearsheet(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None or not r.tearsheet_path or not Path(r.tearsheet_path).exists():
        raise HTTPException(404, detail="Tearsheet not available")
    return FileResponse(r.tearsheet_path, media_type="text/html",
                        filename=f"backtest_{run_id}_tearsheet.html")


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, detail="Backtest run not found")
    # If in-flight, the runner exposes a per-run CancelToken via the container — set it
    container = get_container()
    if hasattr(container, "cancel_backtest"):
        container.cancel_backtest(run_id)
    # Best-effort cleanup of tearsheet file
    if r.tearsheet_path:
        try:
            Path(r.tearsheet_path).unlink(missing_ok=True)
        except OSError:
            pass
    await db.delete(r)
```

- [ ] **C3.3 — Mount in `coordinator/main.py` BEFORE the static-files catch-all**

Read `coordinator/main.py` to find the static mount. Add:

```python
from coordinator.api.routes import backtest_runs as backtest_runs_routes
app.include_router(backtest_runs_routes.router)
```

before the static files mount.

- [ ] **C3.4 — Wire `BacktestRunner` into the container**

Read `coordinator/api/dependencies.py`. Add to the container:

```python
backtest_runner: Optional["BacktestRunner"] = None
```

In `coordinator/main.py`'s lifespan startup:

```python
from coordinator.services.backtest_runner import BacktestRunner
container.backtest_runner = BacktestRunner(
    session_factory=container.session_factory,
    download_manager=container.download_manager,
    data_service=container.data_service,
)
```

- [ ] **C3.5 — Run + pass**

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/test_backtest_runs_api.py -v`. Expected: PASS.

- [ ] **C3.6 — Commit**

```bash
git add coordinator/api/routes/backtest_runs.py coordinator/main.py coordinator/api/dependencies.py \
        tests/coordinator/test_backtest_runs_api.py
git commit -m "feat(backtest): REST API + container wiring

POST/GET/DELETE /api/backtest-runs with sub-endpoints for equity_curve,
trades, tearsheet. Runner constructed in app lifespan."
```

---

**End of Phase 2.** All three units merge before Phase 3.

---

## Phase 3 — UI

Three parallel work units. Same conflict-management rule as the previous plan: APPEND to shared `hooks.ts`/`client.ts`/`types.ts` under banner comments; never reorder.

### Work unit U1: `RunBacktestModal`

**Branch:** `plan/D-U1-modal`

**Files:**
- Create: `dashboard/src/components/RunBacktestModal.tsx`
- Modify: `dashboard/src/pages/AlgorithmDetail.tsx` (add Run Backtest button)
- Modify (additive): `dashboard/src/api/hooks.ts`, `dashboard/src/api/client.ts`, `dashboard/src/types.ts`

- [ ] **U1.1 — Add client method + hook**

Append to `dashboard/src/api/client.ts`:

```typescript
// ── Spec D: backtest runs ──
async createBacktestRun(body: {
  algorithm_id: string;
  date_range_start: string;
  date_range_end: string;
  initial_cash: number;
  config_overrides?: Record<string, unknown>;
  buy_trading_fees?: Array<{flat_fee: number; percent_fee: number; maker: boolean; taker: boolean}>;
  sell_trading_fees?: Array<typeof this extends never ? never : any>;
  slippage_model?: {market_bps: number; limit_bps: number; use_bar_range: boolean; volume_impact_bps_per_pct: number};
  benchmark_symbol?: string;
  benchmark_source?: string;
}) {
  return (await this.http.post("/api/backtest-runs", body)).data;
}
async listBacktestRuns(params?: { algorithm_id?: string; limit?: number; offset?: number }) {
  return (await this.http.get("/api/backtest-runs", { params })).data;
}
async getBacktestRun(id: string) { return (await this.http.get(`/api/backtest-runs/${id}`)).data; }
async getBacktestEquityCurve(id: string) { return (await this.http.get(`/api/backtest-runs/${id}/equity-curve`)).data; }
async getBacktestTrades(id: string, params?: { limit?: number; offset?: number }) {
  return (await this.http.get(`/api/backtest-runs/${id}/trades`, { params })).data;
}
async deleteBacktestRun(id: string) { await this.http.delete(`/api/backtest-runs/${id}`); }
```

Append to `dashboard/src/api/hooks.ts`:

```typescript
// ── Spec D: backtest runs ──
export function useCreateBacktestRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createBacktestRun.bind(api),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtest-runs"] }),
  });
}
export function useBacktestRuns(algorithm_id?: string) {
  return useQuery({
    queryKey: ["backtest-runs", algorithm_id],
    queryFn: () => api.listBacktestRuns(algorithm_id ? { algorithm_id } : undefined),
  });
}
export function useBacktestRun(id: string, opts?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ["backtest-run", id],
    queryFn: () => api.getBacktestRun(id),
    enabled: !!id,
    refetchInterval: opts?.refetchInterval,
  });
}
export function useBacktestEquityCurve(id: string) {
  return useQuery({
    queryKey: ["backtest-equity", id],
    queryFn: () => api.getBacktestEquityCurve(id),
    enabled: !!id,
  });
}
export function useBacktestTrades(id: string, limit = 500, offset = 0) {
  return useQuery({
    queryKey: ["backtest-trades", id, limit, offset],
    queryFn: () => api.getBacktestTrades(id, { limit, offset }),
    enabled: !!id,
  });
}
export function useDeleteBacktestRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteBacktestRun.bind(api),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtest-runs"] }),
  });
}
```

- [ ] **U1.2 — Implement `RunBacktestModal`**

```tsx
// dashboard/src/components/RunBacktestModal.tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateBacktestRun } from "../api/hooks";
import { useUIStore } from "../stores/ui";

const FEE_PRESETS = {
  none: { buy: [], sell: [] },
  "alpaca-equities": { buy: [], sell: [] },
  "tradier-options": {
    buy:  [{ flat_fee: 0.35, percent_fee: 0.0, maker: true, taker: true }],
    sell: [{ flat_fee: 0.35, percent_fee: 0.0, maker: true, taker: true }],
  },
};

interface Props {
  open: boolean;
  onClose: () => void;
  algorithmId: string;
  manifestConfig?: Array<{name: string; type: string; default?: any}>;
}

export function RunBacktestModal({ open, onClose, algorithmId, manifestConfig = [] }: Props) {
  const navigate = useNavigate();
  const addAlert = useUIStore(s => s.addAlert);
  const create = useCreateBacktestRun();

  const today = new Date();
  const oneYearAgo = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate());

  const [start, setStart] = useState(oneYearAgo.toISOString().slice(0, 10));
  const [end, setEnd] = useState(today.toISOString().slice(0, 10));
  const [cash, setCash] = useState(100_000);
  const [preset, setPreset] = useState<keyof typeof FEE_PRESETS>("none");
  const [marketBps, setMarketBps] = useState(5.0);
  const [useBarRange, setUseBarRange] = useState(false);
  const [benchmarkSymbol, setBenchmarkSymbol] = useState("SPY");
  const [benchmarkSource, setBenchmarkSource] = useState("polygon");
  const [configOverrides, setConfigOverrides] = useState<Record<string, any>>(
    Object.fromEntries(manifestConfig.map(p => [p.name, p.default]))
  );

  if (!open) return null;

  async function submit() {
    try {
      const fees = FEE_PRESETS[preset];
      const result = await create.mutateAsync({
        algorithm_id: algorithmId,
        date_range_start: new Date(start).toISOString(),
        date_range_end: new Date(end).toISOString(),
        initial_cash: cash,
        config_overrides: configOverrides,
        buy_trading_fees: fees.buy.length ? fees.buy : undefined,
        sell_trading_fees: fees.sell.length ? fees.sell : undefined,
        slippage_model: {
          market_bps: marketBps, limit_bps: 0, use_bar_range: useBarRange,
          volume_impact_bps_per_pct: 0,
        },
        benchmark_symbol: benchmarkSymbol || undefined,
        benchmark_source: benchmarkSource || undefined,
      });
      addAlert({ message: `Backtest queued: ${result.id.slice(0,8)}…`, severity: "info" });
      onClose();
      navigate(`/backtest-runs/${result.id}`);
    } catch (e) {
      addAlert({ message: `Failed to start backtest: ${(e as Error).message}`, severity: "error" });
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 w-full max-w-2xl space-y-3">
        <h2 className="text-lg font-semibold">Run Backtest</h2>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">Start date
            <input type="date" value={start} onChange={e => setStart(e.target.value)}
                   className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1" />
          </label>
          <label className="text-sm">End date
            <input type="date" value={end} onChange={e => setEnd(e.target.value)}
                   className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1" />
          </label>
        </div>

        <label className="text-sm block">Initial cash
          <input type="number" value={cash} onChange={e => setCash(Number(e.target.value))}
                 className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1" />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">Fee preset
            <select value={preset} onChange={e => setPreset(e.target.value as any)}
                    className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1">
              <option value="none">None (no fees)</option>
              <option value="alpaca-equities">Alpaca equities ($0)</option>
              <option value="tradier-options">Tradier options ($0.35/contract)</option>
            </select>
          </label>
          <label className="text-sm">Market slippage (bps)
            <input type="number" step="0.5" value={marketBps} onChange={e => setMarketBps(Number(e.target.value))}
                   className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1" />
          </label>
        </div>

        <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={useBarRange} onChange={e => setUseBarRange(e.target.checked)} />
          Use bar range for slippage (random fill within next bar's [low, high])
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">Benchmark symbol
            <input value={benchmarkSymbol} onChange={e => setBenchmarkSymbol(e.target.value.toUpperCase())}
                   className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1" />
          </label>
          <label className="text-sm">Benchmark source
            <select value={benchmarkSource} onChange={e => setBenchmarkSource(e.target.value)}
                    className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1">
              <option value="polygon">polygon</option>
              <option value="theta">theta</option>
            </select>
          </label>
        </div>

        {manifestConfig.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-gray-400">Algorithm config overrides</summary>
            <div className="space-y-2 mt-2">
              {manifestConfig.map(p => (
                <label key={p.name} className="block text-xs">{p.name} ({p.type})
                  <input
                    value={String(configOverrides[p.name] ?? "")}
                    onChange={e => setConfigOverrides({ ...configOverrides, [p.name]: e.target.value })}
                    className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
                  />
                </label>
              ))}
            </div>
          </details>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600">
            Cancel
          </button>
          <button onClick={submit} disabled={create.isPending}
                  className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50">
            {create.isPending ? "Starting…" : "Run Backtest"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **U1.3 — Wire into AlgorithmDetail.tsx**

Add to `dashboard/src/pages/AlgorithmDetail.tsx`:

```tsx
import { useState } from "react";
import { RunBacktestModal } from "../components/RunBacktestModal";
// ... existing imports

// Inside component:
const [backtestOpen, setBacktestOpen] = useState(false);

// In the header buttons row, add before Update/Delete:
<button onClick={() => setBacktestOpen(true)}
        className="flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500">
  Run Backtest
</button>

// At end of JSX:
<RunBacktestModal
  open={backtestOpen}
  onClose={() => setBacktestOpen(false)}
  algorithmId={algorithm?.id ?? ""}
  manifestConfig={(algorithm?.config_schema?.parameters as any[]) ?? []}
/>
```

- [ ] **U1.4 — Commit**

```bash
git add dashboard/src/components/RunBacktestModal.tsx dashboard/src/pages/AlgorithmDetail.tsx \
        dashboard/src/api/hooks.ts dashboard/src/api/client.ts
git commit -m "feat(backtest-ui): Run Backtest modal + AlgorithmDetail button"
```

### Work unit U2: `BacktestRunDetail.tsx`

**Branch:** `plan/D-U2-detail-page`

**Files:**
- Create: `dashboard/src/pages/BacktestRunDetail.tsx`
- Modify: `dashboard/src/App.tsx` (add route)

- [ ] **U2.1 — Implement the detail page**

```tsx
// dashboard/src/pages/BacktestRunDetail.tsx
import { useParams, Link } from "react-router-dom";
import { useBacktestRun, useBacktestEquityCurve, useBacktestTrades, useDeleteBacktestRun } from "../api/hooks";
import { ChevronLeft, Download, Trash2 } from "lucide-react";
import { StatusBadge } from "../components/StatusBadge";
import { EquityCurve } from "../components/EquityCurve";

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}%`;
}
function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}
function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function BacktestRunDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const isInflight = (status: string) => ["queued", "downloading_data", "running"].includes(status);
  const { data: run } = useBacktestRun(id, { refetchInterval: 2000 });
  const inflight = run && isInflight(run.status);
  const { data: equity } = useBacktestEquityCurve(id);
  const { data: tradesData } = useBacktestTrades(id, 500);
  const del = useDeleteBacktestRun();

  if (!run) return <div className="p-4 text-gray-400">Loading…</div>;

  const trades = tradesData?.items ?? [];
  const equityPoints = (equity?.items ?? []).map((p: any) => ({
    timestamp: p.timestamp, equity: p.portfolio_value,
  }));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/backtests" className="text-gray-400 hover:text-white"><ChevronLeft size={20} /></Link>
          <h1 className="text-xl font-bold">Backtest Run</h1>
          <StatusBadge status={run.status} />
        </div>
        <div className="flex gap-2">
          {run.tearsheet_path && (
            <a href={`/api/backtest-runs/${id}/tearsheet`} target="_blank" rel="noreferrer"
               className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600">
              <Download size={14} /> Download tearsheet
            </a>
          )}
          <button onClick={() => del.mutate(id)}
                  className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-red-300 bg-red-900/40 border border-red-800 hover:bg-red-900/60">
            <Trash2 size={14} /> Delete
          </button>
        </div>
      </div>

      {inflight && (
        <div className="bg-gray-900 border border-gray-800 rounded p-3">
          <div className="text-sm text-gray-300 mb-2">{run.progress_message ?? run.status}</div>
          <div className="bg-gray-700 rounded-full h-2 overflow-hidden">
            <div className="bg-indigo-600 h-2 transition-all duration-300"
                 style={{ width: `${(run.progress_pct ?? 0) * 100}%` }} />
          </div>
        </div>
      )}

      {run.error_message && (
        <div className="bg-red-900/30 border border-red-800 rounded p-3 text-sm text-red-200 whitespace-pre-wrap">
          {run.error_message}
        </div>
      )}

      {/* Metrics grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ["Total return", fmtPct(run.total_return), run.total_return],
          ["CAGR", fmtPct(run.cagr), run.cagr],
          ["Sharpe", fmtNum(run.sharpe_ratio), run.sharpe_ratio],
          ["Sortino", fmtNum(run.sortino_ratio), run.sortino_ratio],
          ["Calmar", fmtNum(run.calmar_ratio), run.calmar_ratio],
          ["Max drawdown", fmtPct(run.max_drawdown), -1],
          ["Volatility", fmtPct(run.volatility), null],
          ["RoMaD", fmtNum(run.romad), run.romad],
          ["Trade count", fmtNum(run.trade_count, 0), null],
          ["Win rate", fmtPct(run.win_rate), null],
          ["Profit factor", fmtNum(run.profit_factor), null],
          ["Expectancy", fmtUsd(run.expectancy), run.expectancy],
          ["Total fees", fmtUsd(run.total_fees_paid), -1],
          ["Total slippage", fmtUsd(run.total_slippage_dollars), -1],
          ["Longest win streak", fmtNum(run.longest_winning_streak, 0), null],
          ["Longest loss streak", fmtNum(run.longest_losing_streak, 0), null],
        ].map(([label, value, signed]) => (
          <div key={label as string} className="bg-gray-900 border border-gray-800 rounded p-3">
            <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
            <div className={`text-lg font-semibold ${
              typeof signed === "number" && signed > 0 ? "text-green-400" :
              typeof signed === "number" && signed < 0 ? "text-red-400" : "text-gray-200"
            }`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <div className="bg-gray-900 border border-gray-800 rounded p-3">
        <h3 className="text-sm font-semibold text-gray-300 mb-2">Equity curve</h3>
        {equityPoints.length > 0 ? <EquityCurve data={equityPoints} height={300} /> :
         <div className="text-gray-500 text-sm py-8 text-center">No equity data yet</div>}
      </div>

      {/* Trades */}
      <div className="bg-gray-900 border border-gray-800 rounded">
        <div className="px-3 py-2 border-b border-gray-800 text-sm font-semibold text-gray-300">
          Trades ({tradesData?.total ?? 0})
        </div>
        <div className="overflow-auto max-h-96">
          <table className="w-full text-sm">
            <thead className="bg-gray-800 text-xs uppercase text-gray-400 sticky top-0">
              <tr>
                <th className="text-left p-2">Timestamp</th>
                <th className="text-left p-2">Symbol</th>
                <th className="text-left p-2">Side</th>
                <th className="text-right p-2">Qty</th>
                <th className="text-right p-2">Requested</th>
                <th className="text-right p-2">Fill</th>
                <th className="text-right p-2">Slippage $</th>
                <th className="text-right p-2">Fees</th>
                <th className="text-right p-2">Realized P&L</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t: any, i: number) => (
                <tr key={i} className="border-t border-gray-800">
                  <td className="p-2 text-xs text-gray-400">{new Date(t.timestamp).toLocaleString()}</td>
                  <td className="p-2 font-mono">{t.symbol}</td>
                  <td className={`p-2 ${t.side === "buy" ? "text-green-400" : "text-red-400"}`}>{t.side}</td>
                  <td className="p-2 text-right">{fmtNum(t.quantity, 4)}</td>
                  <td className="p-2 text-right">{fmtUsd(t.requested_price)}</td>
                  <td className="p-2 text-right font-semibold">{fmtUsd(t.fill_price)}</td>
                  <td className="p-2 text-right text-gray-400">{fmtUsd(t.slippage_dollars)}</td>
                  <td className="p-2 text-right text-gray-400">{fmtUsd(t.fees)}</td>
                  <td className={`p-2 text-right ${
                    t.realized_pnl == null ? "text-gray-500" :
                    t.realized_pnl > 0 ? "text-green-400" : "text-red-400"
                  }`}>{t.realized_pnl == null ? "—" : fmtUsd(t.realized_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **U2.2 — Add route in `App.tsx`**

```tsx
import { BacktestRunDetail } from "./pages/BacktestRunDetail";
// inside <Routes>:
<Route path="/backtest-runs/:id" element={<BacktestRunDetail />} />
```

- [ ] **U2.3 — Commit**

```bash
git add dashboard/src/pages/BacktestRunDetail.tsx dashboard/src/App.tsx
git commit -m "feat(backtest-ui): backtest run detail page"
```

### Work unit U3: `Backtests.tsx` tabs split

**Branch:** `plan/D-U3-tabs`

**Files:**
- Modify: `dashboard/src/pages/Backtests.tsx`

Splits the existing Backtests page into two tabs: "Runs" (new — lists `BacktestRun` rows) and "Comparisons" (existing — lists `BacktestComparison` rows).

- [ ] **U3.1 — Modify Backtests.tsx**

Read the existing file. Wrap its current content into a "Comparisons" tab. Add a new "Runs" tab that lists `BacktestRun` rows via `useBacktestRuns()` with columns: created_at, algorithm name, status, date range, total_return, sharpe, trade_count. Row click navigates to `/backtest-runs/:id`.

Skeleton (apply to the existing file's structure):

```tsx
import { useState } from "react";
import { useBacktestRuns, useBacktests, useAlgorithms } from "../api/hooks";
// ... existing imports

export function Backtests() {
  const [tab, setTab] = useState<"runs" | "comparisons">("runs");
  const navigate = useNavigate();
  const { data: algos = [] } = useAlgorithms();
  const algoById = new Map(algos.map(a => [a.id, a.name]));

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Backtests</h1>
      <div className="flex gap-2 border-b border-gray-800">
        {(["runs", "comparisons"] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
                  className={`px-3 py-2 text-sm ${tab === t
                    ? "border-b-2 border-indigo-500 text-white" : "text-gray-400 hover:text-gray-200"}`}>
            {t === "runs" ? "Runs" : "Comparisons"}
          </button>
        ))}
      </div>
      {tab === "runs" && <RunsTab algoById={algoById} navigate={navigate} />}
      {tab === "comparisons" && <ComparisonsTab />}
    </div>
  );
}

function RunsTab({ algoById, navigate }) {
  const { data: runs = [], isLoading } = useBacktestRuns();
  // Build a DataTable with columns: created_at, algo name (via algoById), status,
  // date_range, total_return, sharpe_ratio, trade_count.
  // onRowClick: navigate(`/backtest-runs/${row.id}`).
  // (Implementation mirrors the existing ComparisonsTab pattern — use the existing DataTable component.)
}

function ComparisonsTab() {
  // existing content from current Backtests.tsx — wrap as-is.
}
```

- [ ] **U3.2 — Commit**

```bash
git add dashboard/src/pages/Backtests.tsx
git commit -m "feat(backtest-ui): Backtests page tabs split (Runs / Comparisons)"
```

---

**End of Phase 3.** Three units merge before Phase 4.

---

## Phase 4 — Tearsheet + smoke

### Work unit T1: quantstats tearsheet generation

**Branch:** `plan/D-T1-tearsheet`

**Files:**
- Create: `coordinator/services/backtest_tearsheet.py`
- Modify: `coordinator/services/backtest_runner.py` (call tearsheet generation at end of run)
- Test: `tests/coordinator/services/test_backtest_tearsheet.py`

- [ ] **T1.1 — Failing test**

```python
# tests/coordinator/services/test_backtest_tearsheet.py
import pytest
import pandas as pd
from pathlib import Path
from coordinator.services.backtest_tearsheet import generate_tearsheet


def test_generate_tearsheet_creates_html(tmp_path):
    # Synthetic daily returns over 60 days
    idx = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    strategy_pv = pd.Series([100_000 + i * 100 for i in range(60)], index=idx, name="strategy")
    bench_pv = pd.Series([100_000 + i * 80 for i in range(60)], index=idx, name="benchmark")

    out_path = tmp_path / "tearsheet.html"
    generate_tearsheet(strategy_pv, bench_pv, title="Test", output=str(out_path), risk_free_rate=0.04)
    assert out_path.exists()
    content = out_path.read_text()
    assert "<html" in content.lower() or "<!doctype" in content.lower()


def test_generate_tearsheet_handles_missing_benchmark(tmp_path):
    idx = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    strategy_pv = pd.Series([100_000 + i * 50 for i in range(10)], index=idx, name="strategy")
    out_path = tmp_path / "tearsheet.html"
    generate_tearsheet(strategy_pv, None, title="Test", output=str(out_path))
    # quantstats can produce a no-benchmark report
    assert out_path.exists() or out_path.with_suffix(".html").exists()
```

- [ ] **T1.2 — Implement**

```python
# coordinator/services/backtest_tearsheet.py
"""quantstats HTML tearsheet generation for completed backtest runs.

Mirrors Lumibot's create_tearsheet pattern (lumibot/tools/indicators.py:944)
but stripped to the essentials: strategy returns + optional benchmark returns
→ HTML file. Spec D §6.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def generate_tearsheet(
    strategy_pv: pd.Series,
    benchmark_pv: Optional[pd.Series],
    *,
    title: str,
    output: str,
    risk_free_rate: float = 0.04,
) -> Optional[str]:
    """Generate an HTML tearsheet at `output`. Returns the path on success, None on failure.

    `strategy_pv` and `benchmark_pv` are portfolio-value series (NOT returns).
    """
    try:
        import quantstats as qs
        import contextlib
        import warnings

        strategy_returns = strategy_pv.pct_change().fillna(0)
        strategy_returns.name = "strategy"
        bench_returns = None
        if benchmark_pv is not None and not benchmark_pv.empty:
            bench_returns = benchmark_pv.pct_change().fillna(0)
            bench_returns.name = benchmark_pv.name or "benchmark"

        os.makedirs(os.path.dirname(output), exist_ok=True)

        with open(os.devnull, "w") as devnull, \
             contextlib.redirect_stdout(devnull), \
             contextlib.redirect_stderr(devnull), \
             warnings.catch_warnings():
            warnings.simplefilter("ignore")
            qs.reports.html(
                strategy_returns,
                benchmark=bench_returns,
                title=title,
                output=output,
                rf=risk_free_rate,
            )
        return output
    except Exception as exc:
        logger.warning("Tearsheet generation failed for %s: %s", title, exc)
        return None
```

- [ ] **T1.3 — Wire into `BacktestRunner`**

In `coordinator/services/backtest_runner.py`, after computing metrics but before final commit:

```python
# Generate tearsheet
from coordinator.services.backtest_tearsheet import generate_tearsheet
try:
    pv_series = pd.Series(
        [p["portfolio_value"] for p in observer.equity_curve],
        index=pd.to_datetime([p["timestamp"] for p in observer.equity_curve]),
    )
    bench_pv = None
    if run.benchmark_symbol and run.benchmark_source:
        bench_df = self._ds.load_market_data(run.benchmark_source, run.benchmark_symbol, "1day")
        if bench_df is not None and not bench_df.empty:
            mask = ((bench_df["timestamp"] >= pd.Timestamp(run.date_range_start)) &
                    (bench_df["timestamp"] <= pd.Timestamp(run.date_range_end)))
            bench = bench_df[mask].copy()
            bench["pv_proxy"] = bench["close"] / bench["close"].iloc[0] * run.initial_cash
            bench_pv = pd.Series(bench["pv_proxy"].values, index=bench["timestamp"].values,
                                 name=run.benchmark_symbol)
    out_path = f"data/backtests/{run.id}/tearsheet.html"
    result_path = generate_tearsheet(
        pv_series, bench_pv, title=f"{algo.name} backtest",
        output=out_path, risk_free_rate=0.04,
    )
    if result_path:
        r.tearsheet_path = result_path
except Exception as exc:
    logger.warning("Tearsheet step failed; backtest result is still valid: %s", exc)
```

- [ ] **T1.4 — Run + pass**

Run: `cd /home/jkern/dev/quilt-trader && python3 -m pytest tests/coordinator/services/test_backtest_tearsheet.py -v`. Expected: PASS.

- [ ] **T1.5 — Commit**

```bash
git add coordinator/services/backtest_tearsheet.py coordinator/services/backtest_runner.py \
        tests/coordinator/services/test_backtest_tearsheet.py
git commit -m "feat(backtest): quantstats HTML tearsheet generation

Generates a Lumibot-style HTML report at data/backtests/{run_id}/
tearsheet.html on run completion. Wired into the runner; failures
log a warning but don't fail the run."
```

### Work unit S1: End-to-end smoke

**Branch:** `plan/D-S1-smoke`

Not strictly code-writing — a manual smoke run. The plan-execution should leave this as a documented checklist for the user.

- [ ] **S1.1 — Spec D smoke against `ElectricJack/quilt-trader-test-algo`**

1. Open the dashboard. Confirm the existing test algorithm is installed (if not, install via the URL flow).
2. Open AlgorithmDetail for the test algorithm. Click "Run Backtest".
3. In the modal: pick date range `2024-01-01` → `2024-12-31`. Initial cash `100000`. Fee preset `none`. Market slippage `5 bps`. Benchmark `SPY`/`polygon`.
4. Submit. Navigate to `/backtest-runs/{id}`.
5. Observe: status flips through `queued → downloading_data → running → completed`. If polygon data for SPY 1day 2024 isn't already on disk, the download triggers (visible as progress messages).
6. On completion: verify metrics populated (total_return, CAGR, Sharpe, max_drawdown). Verify equity curve renders. Trades table shows entries from SMA-crossover signals.
7. Click "Download tearsheet" — confirm HTML opens in a new tab with quantstats's standard layout.

- [ ] **S1.2 — Commit (record-only)**

```bash
git commit --allow-empty -m "test: Spec D manual smoke complete"
```

---

## Self-review

Spec coverage check:

- **Spec D §1 (DB)**: F1.
- **Spec D §2 (config)**: F3.
- **Spec D §3 (engine, look-ahead, conservative fills, tick-as-bar)**: F2 (context) + E1 (engine).
- **Spec D §4 (orchestration)**: C1.
- **Spec D §5 (metrics)**: F4.
- **Spec D §6 (tearsheet)**: F5 (dep) + T1 (impl).
- **Spec D §7 (API)**: C3.
- **Spec D §8 (UI)**: U1, U2, U3.
- **Spec D §9 (correctness tests)**: Tests embedded in F2 (look-ahead, tick-as-bar) and E1 (conservative fills, fee accounting, options forward-compat).
- **Spec D §10 (cross-cutting)**: distributed across all units; quantstats dep in F5; container wiring in C3.
- **Spec D §11 (shared engine with BacktestComparison)**: C2.
- **Spec D §12 (options forward-compat)**: tests in E1 + the `UnsupportedAssetTypeError` raise paths in the engine.

No spec sections without a task.

Placeholder scan: no TBDs, no "implement later". The U2 trades table uses an inline `<table>` rather than the existing `DataTable` component — pragmatic choice given the long column count; agents may refactor to DataTable if they prefer matching project style.

Type-consistency check: `FillRecord` fields used by C1's `_RunObserver.on_fill` match those defined in E1.3. `EngineSummary` fields match. `TradingFee` fields used by U1 modal (camelCase via JSON) match Pydantic snake_case via FastAPI serialization (no transformation needed since Pydantic v2's `model_dump` preserves field names).

---

## Recommended execution mode

Use `superpowers:subagent-driven-development` with parallel dispatch per phase, mirroring the three-specs plan:

- **Phase 0** (5 parallel): F1 (Sonnet), F2 (Opus), F3 (Sonnet), F4 (Opus), F5 (Sonnet).
- **Phase 1** (1 unit): E1 (Opus, largest unit).
- **Phase 2** (3 parallel): C1 (Opus), C2 (Sonnet), C3 (Sonnet).
- **Phase 3** (3 parallel): U1 (Sonnet), U2 (Opus — complex page), U3 (Sonnet).
- **Phase 4** (sequential): T1 (Sonnet), S1 (manual with user).

Estimated wall-clock with full parallelism: ~30-45 minutes (smaller than the three-specs plan because there are fewer units and the engine is the only truly large piece).
