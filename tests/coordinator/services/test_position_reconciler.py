import pytest
from coordinator.services.position_reconciler import PositionReconciler


def test_reconcile_detects_orphaned_broker_position():
    broker_positions = {
        "SPY": {"symbol": "SPY", "quantity": 10, "side": "long", "avg_entry_price": 520.0, "current_price": 525.0},
    }
    db_positions = []
    result = PositionReconciler.reconcile(broker_positions, db_positions)
    assert len(result.untracked) == 1
    assert result.untracked[0]["symbol"] == "SPY"
    assert result.matched == []
    assert result.stale == []


def test_reconcile_detects_stale_db_position():
    broker_positions = {}
    db_positions = [
        {"id": "pos-1", "legs": [{"symbol": "AAPL", "side": "buy", "quantity": 5}], "status": "open", "account_id": "acct-1"},
    ]
    result = PositionReconciler.reconcile(broker_positions, db_positions)
    assert len(result.stale) == 1
    assert result.stale[0]["id"] == "pos-1"
    assert result.untracked == []


def test_reconcile_matches_known_position():
    broker_positions = {
        "SPY": {"symbol": "SPY", "quantity": 10, "side": "long", "avg_entry_price": 520.0, "current_price": 525.0},
    }
    db_positions = [
        {"id": "pos-1", "legs": [{"symbol": "SPY", "side": "buy", "quantity": 10}], "status": "open", "account_id": "acct-1"},
    ]
    result = PositionReconciler.reconcile(broker_positions, db_positions)
    assert len(result.matched) == 1
    assert result.matched[0]["db_id"] == "pos-1"
    assert result.matched[0]["broker_symbol"] == "SPY"


def test_reconcile_detects_quantity_mismatch():
    broker_positions = {
        "SPY": {"symbol": "SPY", "quantity": 10, "side": "long", "avg_entry_price": 520.0, "current_price": 525.0},
    }
    db_positions = [
        {"id": "pos-1", "legs": [{"symbol": "SPY", "side": "buy", "quantity": 5}], "status": "open", "account_id": "acct-1"},
    ]
    result = PositionReconciler.reconcile(broker_positions, db_positions)
    assert len(result.mismatched) == 1
    assert result.mismatched[0]["broker_qty"] == 10
    assert result.mismatched[0]["db_qty"] == 5
