"""Two-phase goal processor.

Phase 1 (discovering): Scan all expirations, call discover_option_contracts
for each, accumulate the full contract list. No downloads. Updates
discovery_progress ("45/105 expirations") each tick.

Phase 2 (downloading): Iterate discovered contracts, check what's on disk,
queue downloads for what's missing. Updates completed_items each tick.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import DataGoal

logger = logging.getLogger(__name__)

DISCOVERY_BATCH = 20
DOWNLOAD_BATCH = 50


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
                    phase = getattr(goal, "phase", None) or "discovering"
                    if phase == "discovering":
                        await self._discover_options(goal)
                    elif phase == "downloading":
                        await self._download_options(goal)
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

    async def _discover_options(self, goal: DataGoal) -> None:
        config = goal.config
        underlying = config["underlying"]
        provider_name = config.get("provider", "polygon")
        provider = self._providers.get(provider_name)
        if provider is None or not hasattr(provider, "discover_option_contracts"):
            return

        start = date.fromisoformat(config["date_start"])
        end = date.fromisoformat(config["date_end"])
        frequencies = config.get("frequencies", config.get("frequency", ["monthly"]))
        if isinstance(frequencies, str):
            frequencies = [frequencies]
        strike_range = config.get("strike_range", "atm5")
        max_contracts = config.get("max_contracts_per_exp", 60)
        strike_pct = {"atm5": 0.05, "atm15": 0.15, "all": 1.0}.get(strike_range, 0.05)

        all_expirations: set[date] = set()
        for freq in frequencies:
            all_expirations.update(self._generate_expirations(start, end, freq))
        expirations = sorted(all_expirations)

        existing_contracts = goal.discovered_contracts or []
        discovered_exps = {c["expiration"] for c in existing_contracts}

        new_contracts = list(existing_contracts)
        discovered_this_tick = 0

        # Get underlying price once via yfinance (free, no rate limit)
        # to avoid burning a Polygon API call per expiration
        underlying_price = None
        if strike_pct < 1.0:
            try:
                yf_provider = self._providers.get("yfinance")
                if yf_provider:
                    import asyncio
                    bars = await yf_provider.fetch_bars(underlying, "1day", end - timedelta(days=7), end)
                    if bars:
                        underlying_price = bars[-1].get("close")
                        logger.info("Got %s price %.2f from yfinance for discovery", underlying, underlying_price)
            except Exception:
                logger.debug("Could not get underlying price from yfinance, will use Polygon fallback")

        for exp in expirations:
            if exp.isoformat() in discovered_exps:
                continue
            if discovered_this_tick >= DISCOVERY_BATCH:
                break

            existing_on_disk = self._ds.list_option_contracts(provider_name, underlying, exp)
            if existing_on_disk:
                for sym in existing_on_disk:
                    new_contracts.append({"symbol": sym, "expiration": exp.isoformat()})
            else:
                contracts = await provider.discover_option_contracts(
                    underlying, exp, strike_range_pct=strike_pct,
                    max_contracts=max_contracts, underlying_price=underlying_price,
                )
                for c in contracts:
                    sym = c["ticker"].removeprefix("O:")
                    new_contracts.append({"symbol": sym, "expiration": exp.isoformat()})

            discovered_exps.add(exp.isoformat())
            discovered_this_tick += 1

        all_discovered = len(discovered_exps) >= len(expirations)
        progress = f"{len(discovered_exps)}/{len(expirations)} expirations"

        async with self._sf() as session:
            g = (await session.execute(
                select(DataGoal).where(DataGoal.id == goal.id)
            )).scalar_one()
            g.discovered_contracts = new_contracts
            g.discovery_progress = progress
            g.total_items = len(new_contracts)
            g.last_processed_at = datetime.now(timezone.utc)
            g.error_message = None
            if all_discovered:
                g.phase = "downloading"
                g.discovery_progress = f"{len(expirations)}/{len(expirations)} expirations (done)"
            await session.commit()

    async def _download_options(self, goal: DataGoal) -> None:
        config = goal.config
        provider_name = config.get("provider", "polygon")
        start = date.fromisoformat(config["date_start"])
        end = date.fromisoformat(config["date_end"])
        contracts = goal.discovered_contracts or []

        completed = 0
        queued = 0

        for c in contracts:
            sym = c["symbol"]
            df = self._ds.load_market_data(provider_name, sym, "1day")
            if df is not None and len(df) > 1:
                completed += 1
                continue
            if queued >= DOWNLOAD_BATCH:
                continue
            exp = date.fromisoformat(c["expiration"])
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
            g.completed_items = completed
            g.error_message = None
            g.last_processed_at = datetime.now(timezone.utc)
            if completed >= len(contracts) and queued == 0:
                g.phase = "completed"
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
                if queued < DOWNLOAD_BATCH:
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
            g.phase = "downloading"
            g.error_message = None
            g.last_processed_at = datetime.now(timezone.utc)
            if completed >= total and queued == 0:
                g.phase = "completed"
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
