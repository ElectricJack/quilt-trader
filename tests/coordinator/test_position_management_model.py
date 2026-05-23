import pytest
from coordinator.database.models import Position


def test_position_has_new_columns():
    pos = Position(
        account_id="acct-1",
        strategy_type="vertical_spread",
        legs=[
            {"symbol": "SPY260620C00560000", "asset_type": "options",
             "side": "buy", "quantity": 2, "avg_price": 5.00},
        ],
        status="open",
        net_cost=10.00,
        remaining_quantity=2,
        owner_instance_id="inst-123",
        cost_basis_lots=[
            {"fill_price": 5.00, "quantity": 2, "timestamp": "2026-05-23T10:00:00Z"}
        ],
    )
    assert pos.remaining_quantity == 2
    assert pos.owner_instance_id == "inst-123"
    assert pos.cost_basis_lots[0]["fill_price"] == 5.00
    assert pos.status == "open"
