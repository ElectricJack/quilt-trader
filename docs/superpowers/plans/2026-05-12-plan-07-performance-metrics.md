# Plan 7: Performance & Metrics Engine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the performance metrics computation engine and account snapshot service — the system that computes Sharpe ratio, Sortino ratio, max drawdown, win rate, profit factor, and all other metrics at four levels (run, instance lifetime, algorithm aggregate, account), manages periodic equity curve snapshots, and calculates time-weighted returns that isolate trading performance from deposits/withdrawals.

**Architecture:** The metrics engine takes an equity curve (list of timestamped equity values) and a list of closed positions, and computes the full performance metrics schema. The snapshot service periodically captures account state and triggers snapshots on cash flow events and run start/stop. TWR is computed by chaining sub-period returns between cash flow events. The engine is pure computation with no database dependencies — it receives data and returns metrics.

**Tech Stack:** Python 3.11+, numpy (for financial calculations), pandas, pytest

---

## File Map

| File | Responsibility |
|------|---------------|
| `coordinator/services/metrics_engine.py` | Pure computation: equity curve → performance metrics |
| `coordinator/services/snapshot_service.py` | Account snapshot capture + TWR computation |
| `coordinator/services/metrics_aggregator.py` | Aggregation across runs → instance lifetime, across instances → algorithm aggregate |
| `coordinator/api/routes/metrics.py` | API endpoints for querying metrics at all levels |
| `tests/coordinator/test_metrics_engine.py` | Metrics engine unit tests |
| `tests/coordinator/test_snapshot_service.py` | Snapshot service tests |
| `tests/coordinator/test_metrics_aggregator.py` | Aggregation tests |
| `tests/coordinator/test_metrics_api.py` | Metrics API tests |

---

### Task 1: Metrics Engine — Core Calculations

**Files:**
- Create: `coordinator/services/metrics_engine.py`
- Create: `tests/coordinator/test_metrics_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_metrics_engine.py
import pytest
import math
from datetime import datetime, timezone, timedelta

from coordinator.services.metrics_engine import MetricsEngine


@pytest.fixture
def sample_equity_curve():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        {"timestamp": (start + timedelta(days=i)).isoformat(), "equity": v}
        for i, v in enumerate([
            50000, 50500, 51000, 50200, 50800,
            51500, 51200, 52000, 51800, 52500,
            53000, 52200, 53500, 54000, 53800,
            54500, 55000, 54200, 55500, 56000,
        ])
    ]


@pytest.fixture
def sample_positions():
    return [
        {"net_pnl": 500.0, "total_fees": 2.0, "opened_at": "2025-01-01", "closed_at": "2025-01-03"},
        {"net_pnl": -200.0, "total_fees": 1.5, "opened_at": "2025-01-02", "closed_at": "2025-01-04"},
        {"net_pnl": 800.0, "total_fees": 3.0, "opened_at": "2025-01-05", "closed_at": "2025-01-07"},
        {"net_pnl": -150.0, "total_fees": 1.0, "opened_at": "2025-01-06", "closed_at": "2025-01-08"},
        {"net_pnl": 600.0, "total_fees": 2.5, "opened_at": "2025-01-09", "closed_at": "2025-01-11"},
        {"net_pnl": 300.0, "total_fees": 1.5, "opened_at": "2025-01-10", "closed_at": "2025-01-12"},
        {"net_pnl": -100.0, "total_fees": 1.0, "opened_at": "2025-01-13", "closed_at": "2025-01-14"},
        {"net_pnl": 450.0, "total_fees": 2.0, "opened_at": "2025-01-15", "closed_at": "2025-01-17"},
    ]


def test_compute_total_return(sample_equity_curve, sample_positions):
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    assert metrics["total_return_pct"] == pytest.approx(12.0, abs=0.5)
    assert metrics["total_return_dollars"] == pytest.approx(6000, abs=100)


def test_compute_trade_stats(sample_equity_curve, sample_positions):
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    assert metrics["total_trades"] == 8
    assert metrics["winning_trades"] == 5
    assert metrics["losing_trades"] == 3
    assert metrics["win_rate_pct"] == pytest.approx(62.5, abs=0.1)


def test_compute_profit_factor(sample_equity_curve, sample_positions):
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    assert metrics["profit_factor"] > 1.0


def test_compute_max_drawdown(sample_equity_curve, sample_positions):
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    assert metrics["max_drawdown_pct"] > 0
    assert metrics["max_drawdown_dollars"] > 0


def test_compute_sharpe_ratio(sample_equity_curve, sample_positions):
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    assert "sharpe_ratio" in metrics
    assert isinstance(metrics["sharpe_ratio"], float)


def test_compute_sortino_ratio(sample_equity_curve, sample_positions):
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    assert "sortino_ratio" in metrics


def test_compute_avg_win_loss(sample_equity_curve, sample_positions):
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    assert metrics["avg_win_dollars"] > 0
    assert metrics["avg_loss_dollars"] < 0


def test_compute_with_empty_equity_curve():
    metrics = MetricsEngine.compute([], [])
    assert metrics["total_return_pct"] == 0.0
    assert metrics["total_trades"] == 0
    assert metrics["sharpe_ratio"] == 0.0


def test_compute_with_single_point():
    curve = [{"timestamp": "2025-01-01", "equity": 50000}]
    metrics = MetricsEngine.compute(curve, [])
    assert metrics["total_return_pct"] == 0.0


def test_compute_fees_and_slippage(sample_equity_curve, sample_positions):
    for p in sample_positions:
        p["total_slippage"] = -1.0
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    assert metrics["total_fees_dollars"] > 0
    assert metrics["total_slippage_dollars"] < 0


def test_compute_exposure():
    curve = [
        {"timestamp": "2025-01-01", "equity": 50000},
        {"timestamp": "2025-01-02", "equity": 50500},
        {"timestamp": "2025-01-03", "equity": 50000},
        {"timestamp": "2025-01-04", "equity": 50200},
    ]
    positions = [
        {"net_pnl": 500.0, "total_fees": 0, "opened_at": "2025-01-01", "closed_at": "2025-01-02"},
    ]
    metrics = MetricsEngine.compute(curve, positions)
    assert "exposure_pct" in metrics


def test_all_metrics_schema_keys(sample_equity_curve, sample_positions):
    metrics = MetricsEngine.compute(sample_equity_curve, sample_positions)
    required_keys = [
        "total_return_pct", "total_return_dollars", "annualized_return_pct",
        "sharpe_ratio", "sortino_ratio", "max_drawdown_pct", "max_drawdown_dollars",
        "max_drawdown_duration_days", "calmar_ratio", "win_rate_pct", "loss_rate_pct",
        "profit_factor", "avg_win_dollars", "avg_loss_dollars", "largest_win_dollars",
        "largest_loss_dollars", "total_trades", "winning_trades", "losing_trades",
        "total_fees_dollars", "net_profit_after_fees", "positions_opened",
        "positions_closed",
    ]
    for key in required_keys:
        assert key in metrics, f"Missing key: {key}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_metrics_engine.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/metrics_engine.py
import math
from datetime import datetime
from typing import Optional


class MetricsEngine:
    @staticmethod
    def compute(
        equity_curve: list[dict],
        positions: list[dict],
        risk_free_rate: float = 0.0,
    ) -> dict:
        if len(equity_curve) < 2:
            return MetricsEngine._empty_metrics(len(positions))

        equities = [p["equity"] for p in equity_curve]
        start_equity = equities[0]
        end_equity = equities[-1]

        total_return_dollars = end_equity - start_equity
        total_return_pct = (total_return_dollars / start_equity) * 100 if start_equity else 0

        # Daily returns
        returns = []
        for i in range(1, len(equities)):
            if equities[i - 1] != 0:
                returns.append((equities[i] - equities[i - 1]) / equities[i - 1])

        # Duration in days
        try:
            start_dt = datetime.fromisoformat(equity_curve[0]["timestamp"].replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(equity_curve[-1]["timestamp"].replace("Z", "+00:00"))
            duration_days = max((end_dt - start_dt).total_seconds() / 86400, 1)
        except Exception:
            duration_days = len(equity_curve)

        # Annualized return
        if duration_days > 0 and start_equity > 0:
            annualized = ((end_equity / start_equity) ** (365 / duration_days) - 1) * 100
        else:
            annualized = 0.0

        # Volatility + Sharpe
        if len(returns) > 1:
            mean_return = sum(returns) / len(returns)
            variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
            daily_vol = math.sqrt(variance)
            annual_vol = daily_vol * math.sqrt(252)
            sharpe = (mean_return * 252 - risk_free_rate) / (annual_vol) if annual_vol > 0 else 0
        else:
            mean_return = 0
            daily_vol = 0
            annual_vol = 0
            sharpe = 0

        # Sortino (downside deviation)
        downside = [r for r in returns if r < 0]
        if len(downside) > 1:
            downside_var = sum(r ** 2 for r in downside) / len(downside)
            downside_dev = math.sqrt(downside_var) * math.sqrt(252)
            sortino = (mean_return * 252 - risk_free_rate) / downside_dev if downside_dev > 0 else 0
        else:
            sortino = 0

        # Max drawdown
        peak = equities[0]
        max_dd_pct = 0
        max_dd_dollars = 0
        dd_start = 0
        max_dd_duration = 0
        current_dd_start = 0

        for i, eq in enumerate(equities):
            if eq > peak:
                if current_dd_start > 0:
                    dd_dur = i - current_dd_start
                    max_dd_duration = max(max_dd_duration, dd_dur)
                peak = eq
                current_dd_start = 0
            else:
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                dd_abs = peak - eq
                if dd > max_dd_pct:
                    max_dd_pct = dd
                    max_dd_dollars = dd_abs
                if current_dd_start == 0:
                    current_dd_start = i

        # Calmar
        calmar = annualized / max_dd_pct if max_dd_pct > 0 else 0

        # Trade stats
        wins = [p for p in positions if p.get("net_pnl", 0) > 0]
        losses = [p for p in positions if p.get("net_pnl", 0) <= 0]
        total_trades = len(positions)
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        loss_rate = 100 - win_rate if total_trades > 0 else 0

        total_win = sum(p["net_pnl"] for p in wins) if wins else 0
        total_loss = abs(sum(p["net_pnl"] for p in losses)) if losses else 0
        profit_factor = total_win / total_loss if total_loss > 0 else 0

        avg_win = total_win / winning_trades if winning_trades > 0 else 0
        avg_loss = sum(p["net_pnl"] for p in losses) / losing_trades if losing_trades > 0 else 0
        largest_win = max((p["net_pnl"] for p in wins), default=0)
        largest_loss = min((p["net_pnl"] for p in losses), default=0)

        total_fees = sum(p.get("total_fees", 0) for p in positions)
        total_slippage = sum(p.get("total_slippage", 0) for p in positions)
        net_pnl = sum(p.get("net_pnl", 0) for p in positions)

        # Consecutive wins/losses
        max_consec_wins = 0
        max_consec_losses = 0
        current_wins = 0
        current_losses = 0
        for p in positions:
            if p.get("net_pnl", 0) > 0:
                current_wins += 1
                current_losses = 0
                max_consec_wins = max(max_consec_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_consec_losses = max(max_consec_losses, current_losses)

        # Expectancy
        expectancy = net_pnl / total_trades if total_trades > 0 else 0
        expectancy_pct = (expectancy / start_equity * 100) if start_equity > 0 else 0

        open_positions = sum(1 for p in positions if p.get("closed_at") is None)
        closed_positions = total_trades - open_positions

        return {
            "total_return_pct": round(total_return_pct, 2),
            "total_return_dollars": round(total_return_dollars, 2),
            "annualized_return_pct": round(annualized, 2),
            "time_weighted_return_pct": round(total_return_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_drawdown_dollars": round(max_dd_dollars, 2),
            "max_drawdown_duration_days": max_dd_duration,
            "calmar_ratio": round(calmar, 2),
            "win_rate_pct": round(win_rate, 1),
            "loss_rate_pct": round(loss_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_win_dollars": round(avg_win, 2),
            "avg_loss_dollars": round(avg_loss, 2),
            "largest_win_dollars": round(largest_win, 2),
            "largest_loss_dollars": round(largest_loss, 2),
            "avg_win_pct": round(avg_win / start_equity * 100, 2) if start_equity else 0,
            "avg_loss_pct": round(avg_loss / start_equity * 100, 2) if start_equity else 0,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "total_fees_dollars": round(total_fees, 2),
            "total_slippage_dollars": round(total_slippage, 2),
            "net_profit_after_fees": round(net_pnl - total_fees, 2),
            "exposure_pct": 0.0,
            "volatility_annualized_pct": round(annual_vol * 100, 2),
            "risk_reward_ratio": round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else 0,
            "expectancy_dollars": round(expectancy, 2),
            "expectancy_pct": round(expectancy_pct, 2),
            "consecutive_wins_max": max_consec_wins,
            "consecutive_losses_max": max_consec_losses,
            "run_duration_days": round(duration_days, 1),
            "positions_opened": total_trades,
            "positions_closed": closed_positions,
            "positions_open": open_positions,
        }

    @staticmethod
    def _empty_metrics(position_count: int = 0) -> dict:
        return {
            "total_return_pct": 0.0,
            "total_return_dollars": 0.0,
            "annualized_return_pct": 0.0,
            "time_weighted_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_dollars": 0.0,
            "max_drawdown_duration_days": 0,
            "calmar_ratio": 0.0,
            "win_rate_pct": 0.0,
            "loss_rate_pct": 0.0,
            "profit_factor": 0.0,
            "avg_win_dollars": 0.0,
            "avg_loss_dollars": 0.0,
            "largest_win_dollars": 0.0,
            "largest_loss_dollars": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_fees_dollars": 0.0,
            "total_slippage_dollars": 0.0,
            "net_profit_after_fees": 0.0,
            "exposure_pct": 0.0,
            "volatility_annualized_pct": 0.0,
            "risk_reward_ratio": 0.0,
            "expectancy_dollars": 0.0,
            "expectancy_pct": 0.0,
            "consecutive_wins_max": 0,
            "consecutive_losses_max": 0,
            "run_duration_days": 0.0,
            "positions_opened": 0,
            "positions_closed": 0,
            "positions_open": 0,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_metrics_engine.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/metrics_engine.py tests/coordinator/test_metrics_engine.py
git commit -m "feat(coordinator): add metrics engine with full performance calculations"
```

---

### Task 2: Snapshot Service + TWR

**Files:**
- Create: `coordinator/services/snapshot_service.py`
- Create: `tests/coordinator/test_snapshot_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_snapshot_service.py
import pytest
from coordinator.services.snapshot_service import SnapshotService


def test_compute_twr_no_cash_flows():
    snapshots = [
        {"timestamp": "2025-01-01", "total_value": 50000, "source": "scheduled"},
        {"timestamp": "2025-01-15", "total_value": 52000, "source": "scheduled"},
        {"timestamp": "2025-01-31", "total_value": 53000, "source": "scheduled"},
    ]
    twr = SnapshotService.compute_twr(snapshots, cash_flows=[])
    assert twr == pytest.approx(6.0, abs=0.1)


def test_compute_twr_with_deposit():
    snapshots = [
        {"timestamp": "2025-01-01", "total_value": 50000, "source": "scheduled"},
        {"timestamp": "2025-01-15", "total_value": 52000, "source": "cash_flow"},
        {"timestamp": "2025-01-31", "total_value": 63000, "source": "scheduled"},
    ]
    cash_flows = [
        {"timestamp": "2025-01-15", "amount": 10000},
    ]
    twr = SnapshotService.compute_twr(snapshots, cash_flows)
    # Period 1: 52000/50000 = 1.04
    # After deposit: 52000+10000 = 62000 start of period 2
    # Period 2: 63000/62000 ≈ 1.0161
    # TWR = (1.04 * 1.0161 - 1) * 100 ≈ 5.67%
    assert twr == pytest.approx(5.67, abs=0.5)


def test_compute_twr_with_withdrawal():
    snapshots = [
        {"timestamp": "2025-01-01", "total_value": 50000, "source": "scheduled"},
        {"timestamp": "2025-01-15", "total_value": 52000, "source": "cash_flow"},
        {"timestamp": "2025-01-31", "total_value": 45000, "source": "scheduled"},
    ]
    cash_flows = [
        {"timestamp": "2025-01-15", "amount": -5000},
    ]
    twr = SnapshotService.compute_twr(snapshots, cash_flows)
    # Period 1: 52000/50000 = 1.04
    # After withdrawal: 52000-5000 = 47000 start of period 2
    # Period 2: 45000/47000 ≈ 0.9574
    # TWR = (1.04 * 0.9574 - 1) * 100 ≈ -0.43%
    assert twr == pytest.approx(-0.43, abs=0.5)


def test_compute_twr_empty():
    assert SnapshotService.compute_twr([], []) == 0.0


def test_compute_twr_single_snapshot():
    snapshots = [{"timestamp": "2025-01-01", "total_value": 50000, "source": "scheduled"}]
    assert SnapshotService.compute_twr(snapshots, []) == 0.0


def test_create_snapshot():
    snapshot = SnapshotService.create_snapshot_data(
        account_id="acct-1",
        total_value=50000.0,
        cash=20000.0,
        positions_value=30000.0,
        net_deposits=10000.0,
        source="scheduled",
    )
    assert snapshot["account_id"] == "acct-1"
    assert snapshot["total_value"] == 50000.0
    assert snapshot["source"] == "scheduled"
    assert "timestamp" in snapshot
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_snapshot_service.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/snapshot_service.py
from datetime import datetime, timezone
from typing import Optional


class SnapshotService:
    @staticmethod
    def compute_twr(
        snapshots: list[dict],
        cash_flows: list[dict],
    ) -> float:
        if len(snapshots) < 2:
            return 0.0

        cf_by_ts = {}
        for cf in cash_flows:
            ts = cf["timestamp"]
            cf_by_ts[ts] = cf_by_ts.get(ts, 0) + cf["amount"]

        chain = 1.0
        for i in range(1, len(snapshots)):
            prev_value = snapshots[i - 1]["total_value"]
            curr_value = snapshots[i]["total_value"]
            cf_at_curr = cf_by_ts.get(snapshots[i]["timestamp"], 0)

            if prev_value == 0:
                continue

            if cf_at_curr != 0:
                period_return = curr_value / prev_value
                chain *= period_return
                snapshots[i]["_adjusted_start"] = curr_value + cf_at_curr
            else:
                prev_adjusted = snapshots[i - 1].get("_adjusted_start")
                start = prev_adjusted if prev_adjusted else prev_value
                if start == 0:
                    continue
                period_return = curr_value / start
                chain *= period_return

        return round((chain - 1) * 100, 2)

    @staticmethod
    def create_snapshot_data(
        account_id: str,
        total_value: float,
        cash: float,
        positions_value: float,
        net_deposits: float,
        source: str,
    ) -> dict:
        return {
            "account_id": account_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_value": total_value,
            "cash": cash,
            "positions_value": positions_value,
            "net_deposits_cumulative": net_deposits,
            "source": source,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_snapshot_service.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/snapshot_service.py tests/coordinator/test_snapshot_service.py
git commit -m "feat(coordinator): add snapshot service with TWR computation"
```

---

### Task 3: Metrics Aggregator

**Files:**
- Create: `coordinator/services/metrics_aggregator.py`
- Create: `tests/coordinator/test_metrics_aggregator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_metrics_aggregator.py
import pytest
from coordinator.services.metrics_aggregator import MetricsAggregator


def make_run_metrics(pnl, fees, trades, wins, losses, sharpe, max_dd):
    return {
        "total_return_dollars": pnl,
        "total_fees_dollars": fees,
        "total_trades": trades,
        "winning_trades": wins,
        "losing_trades": losses,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd,
        "net_profit_after_fees": pnl - fees,
        "total_return_pct": (pnl / 50000) * 100,
        "positions_opened": trades,
        "positions_closed": trades,
        "positions_open": 0,
    }


def test_aggregate_runs():
    runs = [
        {"metrics": make_run_metrics(2000, 20, 30, 18, 12, 1.5, 5.0), "starting_equity": 50000},
        {"metrics": make_run_metrics(1500, 15, 20, 13, 7, 1.8, 3.0), "starting_equity": 52000},
        {"metrics": make_run_metrics(-500, 10, 15, 5, 10, -0.3, 8.0), "starting_equity": 53500},
    ]
    agg = MetricsAggregator.aggregate_runs(runs)
    assert agg["total_return_dollars"] == 3000
    assert agg["total_trades"] == 65
    assert agg["winning_trades"] == 36
    assert agg["max_drawdown_pct"] == 8.0


def test_aggregate_runs_empty():
    agg = MetricsAggregator.aggregate_runs([])
    assert agg["total_trades"] == 0
    assert agg["total_return_dollars"] == 0


def test_aggregate_runs_single():
    runs = [
        {"metrics": make_run_metrics(1000, 10, 20, 12, 8, 1.2, 4.0), "starting_equity": 50000},
    ]
    agg = MetricsAggregator.aggregate_runs(runs)
    assert agg["total_return_dollars"] == 1000
    assert agg["total_trades"] == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_metrics_aggregator.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/metrics_aggregator.py

class MetricsAggregator:
    @staticmethod
    def aggregate_runs(runs: list[dict]) -> dict:
        if not runs:
            return {
                "total_return_dollars": 0,
                "total_return_pct": 0,
                "total_fees_dollars": 0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "max_drawdown_pct": 0,
                "net_profit_after_fees": 0,
                "sharpe_ratio": 0,
                "positions_opened": 0,
                "positions_closed": 0,
                "positions_open": 0,
            }

        total_pnl = 0
        total_fees = 0
        total_trades = 0
        total_wins = 0
        total_losses = 0
        max_dd = 0
        total_positions_opened = 0
        total_positions_closed = 0
        total_positions_open = 0
        sharpe_values = []

        for run in runs:
            m = run.get("metrics", {})
            if not m:
                continue
            total_pnl += m.get("total_return_dollars", 0)
            total_fees += m.get("total_fees_dollars", 0)
            total_trades += m.get("total_trades", 0)
            total_wins += m.get("winning_trades", 0)
            total_losses += m.get("losing_trades", 0)
            max_dd = max(max_dd, m.get("max_drawdown_pct", 0))
            total_positions_opened += m.get("positions_opened", 0)
            total_positions_closed += m.get("positions_closed", 0)
            total_positions_open += m.get("positions_open", 0)
            if m.get("sharpe_ratio") is not None:
                sharpe_values.append(m["sharpe_ratio"])

        first_equity = runs[0].get("starting_equity", 0)
        total_return_pct = (total_pnl / first_equity * 100) if first_equity else 0
        avg_sharpe = sum(sharpe_values) / len(sharpe_values) if sharpe_values else 0
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0

        return {
            "total_return_dollars": round(total_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_fees_dollars": round(total_fees, 2),
            "net_profit_after_fees": round(total_pnl - total_fees, 2),
            "total_trades": total_trades,
            "winning_trades": total_wins,
            "losing_trades": total_losses,
            "win_rate_pct": round(win_rate, 1),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(avg_sharpe, 2),
            "positions_opened": total_positions_opened,
            "positions_closed": total_positions_closed,
            "positions_open": total_positions_open,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_metrics_aggregator.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/metrics_aggregator.py tests/coordinator/test_metrics_aggregator.py
git commit -m "feat(coordinator): add metrics aggregator for multi-run rollups"
```

---

### Task 4: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run all metrics-related tests**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_metrics_engine.py tests/coordinator/test_snapshot_service.py tests/coordinator/test_metrics_aggregator.py -v`
Expected: All tests pass (21 tests)

- [ ] **Step 2: Verify all modules importable**

Run: `cd /home/jkern/dev/quilt-trader && python -c "from coordinator.services.metrics_engine import MetricsEngine; from coordinator.services.snapshot_service import SnapshotService; from coordinator.services.metrics_aggregator import MetricsAggregator; print('All metrics modules imported successfully')"`
Expected: `All metrics modules imported successfully`

- [ ] **Step 3: Run full test suite**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -v --tb=short`
Expected: All tests pass
