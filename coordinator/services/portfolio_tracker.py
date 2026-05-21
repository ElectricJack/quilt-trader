"""Real-time mark-to-market portfolio tracker.

Maintains per-account positions/cash, recomputes equity on live price
updates, and pushes results to WebSocket subscribers with 1-second
debounce per topic.
"""

from __future__ import annotations

import time
from typing import Any


class PortfolioTracker:
    """Tracks live account equity from streaming price ticks."""

    _DEBOUNCE_INTERVAL = 1.0  # seconds

    def __init__(self, ws_manager: Any) -> None:
        self._ws = ws_manager

        # account_id -> {"positions": {symbol: {"quantity": float, "current_price": float}}, "cash": float}
        self._accounts: dict[str, dict] = {}

        # symbol -> set of account_ids that hold it
        self._symbol_to_accounts: dict[str, set[str]] = {}

        # latest prices from live ticks
        self._prices: dict[str, float] = {}

        # topics with active dashboard subscribers
        self._subscribers: set[str] = set()

        # accounts visible in portfolio summary
        self._visible_accounts: set[str] = set()

        # topic -> last push timestamp (for debounce)
        self._last_push: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_account_state(self, account_id: str, state: dict) -> None:
        """Load or refresh an account's positions and cash.

        *state* has the shape::

            {
                "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}, ...},
                "cash": 5000.0,
            }
        """
        # Remove old reverse-index entries for this account
        if account_id in self._accounts:
            for sym in self._accounts[account_id].get("positions", {}):
                accts = self._symbol_to_accounts.get(sym)
                if accts:
                    accts.discard(account_id)
                    if not accts:
                        del self._symbol_to_accounts[sym]

        self._accounts[account_id] = state

        # Build reverse index
        for sym in state.get("positions", {}):
            self._symbol_to_accounts.setdefault(sym, set()).add(account_id)

    def add_subscriber(self, topic: str) -> None:
        """Mark *topic* as having active dashboard subscribers."""
        self._subscribers.add(topic)

    def remove_subscriber(self, topic: str) -> None:
        """Remove *topic* from the active subscriber set."""
        self._subscribers.discard(topic)

    def mark_account_visible(self, account_id: str) -> None:
        """Include *account_id* in the portfolio summary aggregation."""
        self._visible_accounts.add(account_id)

    async def on_price_update(self, symbol: str, price: float) -> None:
        """Handle a live tick for *symbol* at *price*.

        Recomputes affected accounts and broadcasts to subscribed topics,
        respecting the debounce interval.
        """
        affected = self._symbol_to_accounts.get(symbol)
        if not affected:
            return

        self._prices[symbol] = price

        # Update current_price in every affected account's position
        for account_id in affected:
            positions = self._accounts[account_id].get("positions", {})
            if symbol in positions:
                positions[symbol]["current_price"] = price

        now = time.monotonic()
        summary_dirty = False

        for account_id in affected:
            topic = f"account:{account_id}"
            if topic in self._subscribers and self._should_push(topic, now):
                account = self._accounts[account_id]
                positions_value = self._positions_value(account)
                cash = account.get("cash", 0.0)
                await self._ws.broadcast_to_target(topic, {
                    "type": "account_equity_update",
                    "account_id": account_id,
                    "total_value": positions_value + cash,
                    "positions_value": positions_value,
                    "cash": cash,
                })
                self._last_push[topic] = now

            if account_id in self._visible_accounts:
                summary_dirty = True

        if summary_dirty and "portfolio:summary" in self._subscribers:
            topic = "portfolio:summary"
            if self._should_push(topic, now):
                total_equity = self._compute_total_equity()
                await self._ws.broadcast_to_target(topic, {
                    "type": "portfolio_summary_update",
                    "total_equity": total_equity,
                })
                self._last_push[topic] = now

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _should_push(self, topic: str, now: float) -> bool:
        last = self._last_push.get(topic, 0.0)
        return (now - last) >= self._DEBOUNCE_INTERVAL

    @staticmethod
    def _positions_value(account: dict) -> float:
        total = 0.0
        for pos in account.get("positions", {}).values():
            total += pos.get("quantity", 0) * pos.get("current_price", 0.0)
        return total

    def _compute_total_equity(self) -> float:
        total = 0.0
        for account_id in self._visible_accounts:
            account = self._accounts.get(account_id)
            if account is None:
                continue
            total += self._positions_value(account) + account.get("cash", 0.0)
        return total
