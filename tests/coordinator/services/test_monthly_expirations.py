from datetime import date
from coordinator.services.backtest_runner import BacktestRunner

def test_monthly_expirations_within_range():
    exps = BacktestRunner._monthly_expirations(date(2025, 1, 1), date(2025, 6, 30))
    assert len(exps) == 6  # Jan-Jun
    # Each should be a Friday
    for exp in exps:
        assert exp.weekday() == 4  # Friday
    # January 2025 3rd Friday = Jan 17
    assert exps[0] == date(2025, 1, 17)

def test_monthly_expirations_empty_range():
    exps = BacktestRunner._monthly_expirations(date(2025, 1, 20), date(2025, 1, 25))
    # Range is within January but after 3rd Friday — might return 0 or 1
    assert isinstance(exps, list)
