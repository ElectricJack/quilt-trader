# coordinator/services/goal_processor.py
"""Processes active DataGoals — discovers missing data and creates downloads.

Runs periodically via the scheduler. For each active goal:
1. Determine what data is needed (based on goal_type + config)
2. Check what's already on disk
3. Create individual downloads for missing items
4. Update goal progress counters
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import DataGoal

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


class GoalProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        download_manager: Any,
        data_service: Any,
        providers: dict[str, Any],
    ) -> None:
        self._sf = session_factory
        self._dm = download_manager
        self._ds = data_service
        self._providers = providers

    async def tick(self) -> None:
        async with self._sf() as session:
            goals = (await session.execute(
                select(DataGoal).where(DataGoal.status == "active")
            )).scalars().all()

        for goal in goals:
            try:
                if goal.goal_type == "options":
                    await self._process_options_goal(goal)
                elif goal.goal_type == "bars":
                    await self._process_bars_goal(goal)
            except Exception:
                logger.exception("GoalProcessor failed for goal %s", goal.id)
                async with self._sf() as session:
                    g = (await session.execute(
                        select(DataGoal).where(DataGoal.id == goal.id)
                    )).scalar_one()
                    g.error_message = "Processing error — will retry next tick"
                    await session.commit()

    async def _process_options_goal(self, goal: DataGoal) -> None:
        config = goal.config
        underlying = config["underlying"]
        provider_name = config.get("provider", "polygon")
        provider = self._providers.get(provider_name)
        if provider is None or not hasattr(provider, "discover_option_contracts"):
            return

        start = date.fromisoformat(config["date_start"])
        end = date.fromisoformat(config["date_end"])
        frequency = config.get("frequency", "monthly")
        strike_range = config.get("strike_range", "atm5")
        max_contracts = config.get("max_contracts_per_exp", 60)

        strike_pct = {"atm5": 0.05, "atm15": 0.15, "all": 1.0}.get(strike_range, 0.05)

        expirations = self._generate_expirations(start, end, frequency)

        total = 0
        completed = 0
        queued = 0

        for exp in expirations:
            existing = self._ds.list_option_contracts(provider_name, underlying, exp)
            has_history = sum(1 for sym in existing
                             if (df := self._ds.load_market_data(provider_name, sym, "1day")) is not None and len(df) > 1)

            if existing and has_history == len(existing):
                total += len(existing)
                completed += len(existing)
                continue

            if not existing and queued < BATCH_SIZE:
                contracts = await provider.discover_option_contracts(
                    underlying, exp, strike_range_pct=strike_pct, max_contracts=max_contracts,
                )
                if not contracts:
                    continue
                symbols = [c["ticker"].removeprefix("O:") for c in contracts]
                total += len(symbols)

                dl_start = max(start, exp - timedelta(days=90))
                dl_end = min(end, exp)

                for sym in symbols:
                    if queued >= BATCH_SIZE:
                        break
                    await self._dm.create_download(
                        symbols=[sym],
                        date_range_start=dl_start,
                        date_range_end=dl_end,
                        provider=provider_name,
                        timeframe="1day",
                    )
                    queued += 1
            elif existing:
                total += len(existing)
                completed += has_history
                for sym in existing:
                    df = self._ds.load_market_data(provider_name, sym, "1day")
                    if (df is None or len(df) <= 1) and queued < BATCH_SIZE:
                        dl_start = max(start, exp - timedelta(days=90))
                        dl_end = min(end, exp)
                        await self._dm.create_download(
                            symbols=[sym],
                            date_range_start=dl_start,
                            date_range_end=dl_end,
                            provider=provider_name,
                            timeframe="1day",
                        )
                        queued += 1

        async with self._sf() as session:
            g = (await session.execute(
                select(DataGoal).where(DataGoal.id == goal.id)
            )).scalar_one()
            g.total_items = total
            g.completed_items = completed
            g.error_message = None
            g.last_processed_at = datetime.now(timezone.utc)
            if total > 0 and completed >= total and queued == 0:
                g.status = "completed"
            await session.commit()

    async def _process_bars_goal(self, goal: DataGoal) -> None:
        config = goal.config
        symbols = config["symbols"]
        provider_name = config.get("provider", "polygon")
        start = date.fromisoformat(config["date_start"])
        end = date.fromisoformat(config["date_end"])
        timeframes = config.get("timeframes", ["1day"])

        total = len(symbols) * len(timeframes)
        completed = 0
        queued = 0

        for sym in symbols:
            for tf in timeframes:
                df = self._ds.load_market_data(provider_name, sym, tf)
                if df is not None and not df.empty:
                    completed += 1
                    continue
                if queued < BATCH_SIZE:
                    await self._dm.create_download(
                        symbols=[sym],
                        date_range_start=start,
                        date_range_end=end,
                        provider=provider_name,
                        timeframe=tf,
                    )
                    queued += 1

        async with self._sf() as session:
            g = (await session.execute(
                select(DataGoal).where(DataGoal.id == goal.id)
            )).scalar_one()
            g.total_items = total
            g.completed_items = completed
            g.error_message = None
            g.last_processed_at = datetime.now(timezone.utc)
            if completed >= total and queued == 0:
                g.status = "completed"
            await session.commit()

    @staticmethod
    def _generate_expirations(start: date, end: date, frequency: str) -> list[date]:
        if frequency == "monthly":
            return GoalProcessor._monthly_fridays(start, end)
        elif frequency == "weekly":
            return GoalProcessor._weekly_fridays(start, end)
        elif frequency == "daily":
            return GoalProcessor._trading_days(start, end)
        return GoalProcessor._monthly_fridays(start, end)

    @staticmethod
    def _monthly_fridays(start: date, end: date) -> list[date]:
        from coordinator.services.backtest_runner import BacktestRunner
        return BacktestRunner._monthly_expirations(start, end)

    @staticmethod
    def _weekly_fridays(start: date, end: date) -> list[date]:
        result = []
        d = start
        while d <= end:
            if d.weekday() == 4:
                result.append(d)
            d += timedelta(days=1)
        return result

    @staticmethod
    def _trading_days(start: date, end: date) -> list[date]:
        result = []
        d = start
        while d <= end:
            if d.weekday() < 5:
                result.append(d)
            d += timedelta(days=1)
        return result
