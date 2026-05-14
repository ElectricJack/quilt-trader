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
    cash_flows = [{"timestamp": "2025-01-15", "amount": 10000}]
    twr = SnapshotService.compute_twr(snapshots, cash_flows)
    assert twr == pytest.approx(5.67, abs=0.5)

def test_compute_twr_with_withdrawal():
    snapshots = [
        {"timestamp": "2025-01-01", "total_value": 50000, "source": "scheduled"},
        {"timestamp": "2025-01-15", "total_value": 52000, "source": "cash_flow"},
        {"timestamp": "2025-01-31", "total_value": 45000, "source": "scheduled"},
    ]
    cash_flows = [{"timestamp": "2025-01-15", "amount": -5000}]
    twr = SnapshotService.compute_twr(snapshots, cash_flows)
    assert twr == pytest.approx(-0.43, abs=0.5)

def test_compute_twr_empty():
    assert SnapshotService.compute_twr([], []) == 0.0

def test_compute_twr_single_snapshot():
    snapshots = [{"timestamp": "2025-01-01", "total_value": 50000, "source": "scheduled"}]
    assert SnapshotService.compute_twr(snapshots, []) == 0.0

def test_create_snapshot():
    snapshot = SnapshotService.create_snapshot_data(
        account_id="acct-1", total_value=50000.0, cash=20000.0,
        positions_value=30000.0, net_deposits=10000.0, source="scheduled",
    )
    assert snapshot["account_id"] == "acct-1"
    assert snapshot["total_value"] == 50000.0
    assert snapshot["source"] == "scheduled"
    assert "timestamp" in snapshot
