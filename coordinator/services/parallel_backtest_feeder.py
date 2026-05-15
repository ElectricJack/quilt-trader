"""ParallelBacktestFeeder — feeds DecisionLog(mode=backtest) rows for BacktestComparison.

Implements EngineObserver. Only `on_signals_emitted` does meaningful work
(writes a DecisionLog row); other callbacks are no-ops because the
comparison feature only cares about signal-emission divergence vs live.

Spec D §11.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from coordinator.database.models import DecisionLog

logger = logging.getLogger(__name__)


class ParallelBacktestFeeder:
    def __init__(self, instance_id: str, session_factory):
        self._instance_id = instance_id
        self._sf = session_factory

    # ---- EngineObserver protocol ----

    def on_tick(self, sim_time, ctx_snapshot): pass
    def on_signal_rejected(self, sim_time, signal, reason): pass
    def on_fill(self, fill): pass
    def on_equity_point(self, sim_time, portfolio_value, cash, positions): pass
    def on_complete(self, summary): pass

    def on_error(self, exc):
        logger.warning("ParallelBacktestFeeder for instance %s: engine error %s",
                       self._instance_id, exc)

    def on_signals_emitted(self, sim_time, signals):
        # Engine calls this synchronously; we schedule the DB write on the loop.
        loop = asyncio.get_event_loop()
        loop.create_task(self.on_signals_emitted_async(sim_time, signals))

    async def on_signals_emitted_async(self, sim_time: datetime, signals: list):
        async with self._sf() as session:
            for sig in signals:
                session.add(DecisionLog(
                    instance_id=self._instance_id,
                    timestamp=sim_time,
                    mode="backtest",
                    signals_produced=[{
                        "legs": [{
                            "symbol": l.symbol,
                            "signal_type": l.signal_type.value if hasattr(l.signal_type, "value") else str(l.signal_type),
                            "quantity": l.quantity,
                            "asset_type": l.asset_type,
                        } for l in sig.legs],
                        "strategy_type": getattr(sig, "strategy_type", "single"),
                    }],
                ))
            await session.commit()
