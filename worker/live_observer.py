"""LiveObserver — emits per-tick equity samples and per-trade samples to the coordinator.

Mirrors the role of the backtest's ChunkingObserver but works against a real
broker over a websocket-backed agent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class LiveObserver:
    def __init__(self, *, agent: Any, broker: Any, instance_id: str, run_id: str) -> None:
        self._agent = agent
        self._broker = broker
        self._dep = instance_id
        self._run = run_id

    async def on_tick(self, *, timestamp: Optional[str] = None) -> None:
        try:
            state = self._broker.get_account_info()
        except Exception:
            logger.exception("Failed to read account state from broker")
            return
        cash = float(state.get("cash") or 0.0)
        # Prefer portfolio_value if the broker already aggregates it, else compute.
        if "portfolio_value" in state:
            portfolio_value = float(state["portfolio_value"])
        else:
            positions_value = float(state.get("positions_value") or 0.0)
            portfolio_value = cash + positions_value

        await self._agent._send({
            "type": "equity_sample",
            "worker_id": self._agent.worker_id,
            "instance_id": self._dep,
            "run_id": self._run,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "portfolio_value": portfolio_value,
            "cash": cash,
        })

    async def on_trade(self, *, trade: dict) -> None:
        await self._agent._send({
            "type": "trade_sample",
            "worker_id": self._agent.worker_id,
            "instance_id": self._dep,
            "run_id": self._run,
            **trade,
        })
