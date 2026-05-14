import pytest
from coordinator.services.metrics_aggregator import MetricsAggregator

def make_run_metrics(pnl, fees, trades, wins, losses, sharpe, max_dd):
    return {
        "total_return_dollars": pnl, "total_fees_dollars": fees,
        "total_trades": trades, "winning_trades": wins, "losing_trades": losses,
        "sharpe_ratio": sharpe, "max_drawdown_pct": max_dd,
        "net_profit_after_fees": pnl - fees,
        "total_return_pct": (pnl / 50000) * 100,
        "positions_opened": trades, "positions_closed": trades, "positions_open": 0,
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
    runs = [{"metrics": make_run_metrics(1000, 10, 20, 12, 8, 1.2, 4.0), "starting_equity": 50000}]
    agg = MetricsAggregator.aggregate_runs(runs)
    assert agg["total_return_dollars"] == 1000
    assert agg["total_trades"] == 20
