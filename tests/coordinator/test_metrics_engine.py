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
    positions = [{"net_pnl": 500.0, "total_fees": 0, "opened_at": "2025-01-01", "closed_at": "2025-01-02"}]
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
        "total_fees_dollars", "net_profit_after_fees", "positions_opened", "positions_closed",
    ]
    for key in required_keys:
        assert key in metrics, f"Missing key: {key}"
