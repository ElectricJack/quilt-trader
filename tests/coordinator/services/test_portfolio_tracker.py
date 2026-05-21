import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_price_update_recomputes_account_value():
    from coordinator.services.portfolio_tracker import PortfolioTracker
    ws = MagicMock()
    ws.broadcast_to_target = AsyncMock()
    tracker = PortfolioTracker(ws_manager=ws)
    tracker.set_account_state("acct-1", {
        "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}},
        "cash": 5000.0,
    })
    tracker.add_subscriber("account:acct-1")
    await tracker.on_price_update("AAPL", 155.0)
    ws.broadcast_to_target.assert_called()
    call_args = ws.broadcast_to_target.call_args
    assert call_args[0][0] == "account:acct-1"
    msg = call_args[0][1]
    assert msg["total_value"] == pytest.approx(6550.0)  # 10*155 + 5000
    assert msg["positions_value"] == pytest.approx(1550.0)


@pytest.mark.asyncio
async def test_no_broadcast_without_subscribers():
    from coordinator.services.portfolio_tracker import PortfolioTracker
    ws = MagicMock()
    ws.broadcast_to_target = AsyncMock()
    tracker = PortfolioTracker(ws_manager=ws)
    tracker.set_account_state("acct-1", {
        "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}},
        "cash": 5000.0,
    })
    await tracker.on_price_update("AAPL", 155.0)
    ws.broadcast_to_target.assert_not_called()


@pytest.mark.asyncio
async def test_portfolio_summary_aggregates():
    from coordinator.services.portfolio_tracker import PortfolioTracker
    ws = MagicMock()
    ws.broadcast_to_target = AsyncMock()
    tracker = PortfolioTracker(ws_manager=ws)
    tracker.set_account_state("acct-1", {
        "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}},
        "cash": 5000.0,
    })
    tracker.set_account_state("acct-2", {
        "positions": {"GOOG": {"quantity": 5, "current_price": 100.0}},
        "cash": 3000.0,
    })
    tracker.add_subscriber("portfolio:summary")
    tracker.mark_account_visible("acct-1")
    tracker.mark_account_visible("acct-2")
    await tracker.on_price_update("AAPL", 155.0)
    calls = [c for c in ws.broadcast_to_target.call_args_list if c[0][0] == "portfolio:summary"]
    assert len(calls) >= 1
    msg = calls[-1][0][1]
    assert msg["total_equity"] == pytest.approx(10050.0)  # (10*155+5000) + (5*100+3000)


@pytest.mark.asyncio
async def test_untracked_symbol_is_ignored():
    from coordinator.services.portfolio_tracker import PortfolioTracker
    ws = MagicMock()
    ws.broadcast_to_target = AsyncMock()
    tracker = PortfolioTracker(ws_manager=ws)
    tracker.set_account_state("acct-1", {
        "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}},
        "cash": 5000.0,
    })
    tracker.add_subscriber("account:acct-1")
    await tracker.on_price_update("GOOG", 100.0)  # not held
    ws.broadcast_to_target.assert_not_called()


@pytest.mark.asyncio
async def test_remove_subscriber_stops_broadcasts():
    from coordinator.services.portfolio_tracker import PortfolioTracker
    ws = MagicMock()
    ws.broadcast_to_target = AsyncMock()
    tracker = PortfolioTracker(ws_manager=ws)
    tracker.set_account_state("acct-1", {
        "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}},
        "cash": 5000.0,
    })
    tracker.add_subscriber("account:acct-1")
    tracker.remove_subscriber("account:acct-1")
    await tracker.on_price_update("AAPL", 155.0)
    ws.broadcast_to_target.assert_not_called()
