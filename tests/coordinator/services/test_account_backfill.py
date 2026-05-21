import pytest
from datetime import date


def test_replay_transactions_builds_position_ledger():
    from coordinator.services.account_backfill import replay_transactions

    transactions = [
        {"type": "fill", "timestamp": "2025-01-02T10:00:00Z", "symbol": "AAPL", "side": "buy", "quantity": 10, "price": 150.0},
        {"type": "fill", "timestamp": "2025-01-03T10:00:00Z", "symbol": "GOOG", "side": "buy", "quantity": 5, "price": 100.0},
        {"type": "fill", "timestamp": "2025-01-05T10:00:00Z", "symbol": "AAPL", "side": "sell", "quantity": 3, "price": 155.0},
    ]
    ledger, cash_by_date = replay_transactions(transactions, starting_cash=10000.0)

    assert ledger[date(2025, 1, 2)]["AAPL"]["quantity"] == 10
    assert ledger[date(2025, 1, 3)]["GOOG"]["quantity"] == 5
    assert ledger[date(2025, 1, 5)]["AAPL"]["quantity"] == 7
    assert cash_by_date[date(2025, 1, 5)] == pytest.approx(8465.0)


def test_replay_transactions_handles_cash_flows():
    from coordinator.services.account_backfill import replay_transactions

    transactions = [
        {"type": "deposit", "timestamp": "2025-01-02T10:00:00Z", "amount": 5000.0},
        {"type": "fill", "timestamp": "2025-01-03T10:00:00Z", "symbol": "AAPL", "side": "buy", "quantity": 10, "price": 150.0},
        {"type": "dividend", "timestamp": "2025-01-05T10:00:00Z", "amount": 25.0},
    ]
    ledger, cash_by_date = replay_transactions(transactions, starting_cash=0.0)

    assert cash_by_date[date(2025, 1, 2)] == pytest.approx(5000.0)
    assert cash_by_date[date(2025, 1, 3)] == pytest.approx(3500.0)
    assert cash_by_date[date(2025, 1, 5)] == pytest.approx(3525.0)


def test_replay_removes_zero_positions():
    from coordinator.services.account_backfill import replay_transactions

    transactions = [
        {"type": "fill", "timestamp": "2025-01-02T10:00:00Z", "symbol": "AAPL", "side": "buy", "quantity": 10, "price": 150.0},
        {"type": "fill", "timestamp": "2025-01-03T10:00:00Z", "symbol": "AAPL", "side": "sell", "quantity": 10, "price": 155.0},
    ]
    ledger, _ = replay_transactions(transactions, starting_cash=10000.0)
    assert "AAPL" not in ledger[date(2025, 1, 3)]


def test_forward_fill_fills_weekday_gaps():
    from coordinator.services.account_backfill import forward_fill_ledger

    ledger = {
        date(2025, 1, 6): {"AAPL": {"quantity": 10, "avg_cost": 150.0}},  # Monday
    }
    cash = {date(2025, 1, 6): 5000.0}

    filled_ledger, filled_cash = forward_fill_ledger(ledger, cash, date(2025, 1, 6), date(2025, 1, 10))

    # Should have Mon-Fri
    assert date(2025, 1, 6) in filled_ledger  # Mon
    assert date(2025, 1, 7) in filled_ledger  # Tue
    assert date(2025, 1, 8) in filled_ledger  # Wed
    assert date(2025, 1, 9) in filled_ledger  # Thu
    assert date(2025, 1, 10) in filled_ledger  # Fri
    # Weekend not included
    assert date(2025, 1, 11) not in filled_ledger  # Sat
    # All carry forward same values
    assert filled_ledger[date(2025, 1, 10)]["AAPL"]["quantity"] == 10
    assert filled_cash[date(2025, 1, 10)] == 5000.0


def test_materialize_equity_computes_values():
    from coordinator.services.account_backfill import materialize_equity

    ledger = {
        date(2025, 1, 2): {"AAPL": {"quantity": 10, "avg_cost": 150.0}},
        date(2025, 1, 3): {"AAPL": {"quantity": 10, "avg_cost": 150.0}, "GOOG": {"quantity": 5, "avg_cost": 100.0}},
    }
    cash_by_date = {date(2025, 1, 2): 8500.0, date(2025, 1, 3): 8000.0}
    prices = {
        ("AAPL", date(2025, 1, 2)): 152.0,
        ("AAPL", date(2025, 1, 3)): 155.0,
        ("GOOG", date(2025, 1, 3)): 102.0,
    }

    rows = materialize_equity(ledger, cash_by_date, prices)

    assert len(rows) == 2
    assert rows[0]["total_value"] == pytest.approx(10020.0)  # 10*152 + 8500
    assert rows[1]["total_value"] == pytest.approx(10060.0)  # 10*155 + 5*102 + 8000
    assert rows[0]["estimated"] is False
    assert rows[1]["estimated"] is False


def test_materialize_equity_forward_fills_missing_prices():
    from coordinator.services.account_backfill import materialize_equity

    ledger = {
        date(2025, 1, 2): {"AAPL": {"quantity": 10, "avg_cost": 150.0}},
        date(2025, 1, 3): {"AAPL": {"quantity": 10, "avg_cost": 150.0}},
    }
    cash_by_date = {date(2025, 1, 2): 5000.0, date(2025, 1, 3): 5000.0}
    prices = {
        ("AAPL", date(2025, 1, 2)): 152.0,
        # No price for Jan 3 — should forward-fill from Jan 2
    }

    rows = materialize_equity(ledger, cash_by_date, prices)

    assert rows[1]["total_value"] == pytest.approx(6520.0)  # 10*152 + 5000
    assert rows[1]["estimated"] is True  # forward-filled
