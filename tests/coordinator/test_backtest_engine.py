import pytest
from coordinator.services.backtest_engine import BacktestComparator, ComparisonResult


def test_compare_matching_signals():
    live = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
        {"timestamp": "2025-01-01T09:32:00", "signals_produced": [{"legs": [{"symbol": "TSLA", "signal_type": "sell"}]}]},
    ]
    backtest = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
        {"timestamp": "2025-01-01T09:32:00", "signals_produced": [{"legs": [{"symbol": "TSLA", "signal_type": "sell"}]}]},
    ]
    result = BacktestComparator.compare(live, backtest)
    assert isinstance(result, ComparisonResult)
    assert result.total_ticks == 3
    assert result.matching_ticks == 3
    assert result.match_percentage == 100.0
    assert result.divergences == []


def test_compare_with_divergence():
    live = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
    ]
    backtest = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "sell"}]}]},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
    ]
    result = BacktestComparator.compare(live, backtest)
    assert result.total_ticks == 2
    assert result.matching_ticks == 1
    assert result.match_percentage == 50.0
    assert len(result.divergences) == 1
    assert result.divergences[0]["timestamp"] == "2025-01-01T09:30:00"


def test_compare_signal_present_vs_absent():
    live = [{"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]}]
    backtest = [{"timestamp": "2025-01-01T09:30:00", "signals_produced": []}]
    result = BacktestComparator.compare(live, backtest)
    assert result.matching_ticks == 0
    assert len(result.divergences) == 1


def test_compare_empty():
    result = BacktestComparator.compare([], [])
    assert result.total_ticks == 0
    assert result.match_percentage == 100.0


def test_compare_different_lengths():
    live = [{"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
            {"timestamp": "2025-01-01T09:31:00", "signals_produced": []}]
    backtest = [{"timestamp": "2025-01-01T09:30:00", "signals_produced": []}]
    result = BacktestComparator.compare(live, backtest)
    assert result.total_ticks == 2
    assert result.matching_ticks == 1
    assert len(result.divergences) == 1


def test_exceeds_threshold():
    live = [{"timestamp": f"2025-01-01T09:{i:02d}:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]} for i in range(10)]
    backtest = [{"timestamp": f"2025-01-01T09:{i:02d}:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "sell"}]}]} for i in range(10)]
    result = BacktestComparator.compare(live, backtest, threshold=5.0)
    assert result.exceeds_threshold is True


def test_below_threshold():
    live = [{"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
            {"timestamp": "2025-01-01T09:31:00", "signals_produced": []}]
    backtest = [{"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
                {"timestamp": "2025-01-01T09:31:00", "signals_produced": []}]
    result = BacktestComparator.compare(live, backtest, threshold=5.0)
    assert result.exceeds_threshold is False
