"""Two-phase goal processor.

Phase 1 (discovering): Scan all expirations, call discover_option_contracts
for each, accumulate the full contract list. No downloads. Updates
discovery_progress ("45/105 expirations") each tick.

Phase 2 (downloading): Keep at most (provider concurrency + 1) downloads
in flight per goal, driven by completion events. See
docs/superpowers/specs/2026-05-27-options-goal-incremental-download-design.md.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import DataGoal, MarketDataDownload

logger = logging.getLogger(__name__)

DISCOVERY_BATCH = 20
DOWNLOAD_BATCH = 50  # legacy: still used by _process_bars_goal
DISK_CACHE_TTL_SECONDS = 60


def _utcnow() -> datetime:
    """Indirection point for tests to pin time."""
    return datetime.now(timezone.utc)


def _backoff_seconds(failure_count: int) -> int:
    """Exponential backoff capped at 24h. 1 failure → 60s, 2 → 120s, ... cap at 86_400s."""
    if failure_count <= 0:
        return 0
    return min(60 * (2 ** (failure_count - 1)), 86_400)


def _is_terminal_failure(error_message: str | None) -> bool:
    """A failure is *terminal* when the upstream provider has authoritatively
    answered "no data" for the requested query. Re-asking won't help — the
    contract didn't trade in the requested window and never will. Distinct
    from transient errors (rate limit, network) which should retry.

    Today the only signal is the download manager's "no data returned by ..."
    error string. If we add more providers or error categorizations, extend
    here.
    """
    if not error_message:
        return False
    return "no data returned" in error_message


class GoalProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        download_manager: Any,
        data_service: Any,
        providers: dict[str, Any],
        market_dir: str = "data/market",
    ) -> None:
        self._sf = session_factory
        self._dm = download_manager
        self._ds = data_service
        self._providers = providers
        self._market_dir = market_dir
        # Per-provider cache of symbols with <provider>/<sym>/1day.parquet
        # on disk. Refreshed via full scandir at most every DISK_CACHE_TTL_SECONDS
        # and updated incrementally by on_download_complete events.
        self._disk_cache: dict[str, set[str]] = {}
        self._disk_cache_ts: dict[str, datetime] = {}

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
            except Exception as exc:
                logger.exception("GoalProcessor failed for goal %s", goal.id)
                async with self._sf() as session:
                    g = (await session.execute(
                        select(DataGoal).where(DataGoal.id == goal.id)
                    )).scalar_one()
                    g.error_message = f"{type(exc).__name__}: {exc} — will retry next tick"
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
                    ticker = c.get("ticker")
                    if not isinstance(ticker, str) or not ticker:
                        continue
                    sym = ticker.removeprefix("O:")
                    new_contracts.append({"symbol": sym, "expiration": exp.isoformat()})

            discovered_exps.add(exp.isoformat())
            discovered_this_tick += 1

            # Save progress after each expiration so the UI updates live
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
                # Check if goal was paused while we were running
                if g.status != "active":
                    await session.commit()
                    return
                await session.commit()

        all_discovered = len(discovered_exps) >= len(expirations)
        if all_discovered:
            async with self._sf() as session:
                g = (await session.execute(
                    select(DataGoal).where(DataGoal.id == goal.id)
                )).scalar_one()
                g.phase = "downloading"
                g.discovery_progress = f"{len(expirations)}/{len(expirations)} expirations (done)"
                await session.commit()

    async def _download_options(self, goal: DataGoal) -> None:
        """Reconcile and enqueue up to (concurrency + 1) downloads for this goal.

        Per-tick: refresh disk cache if TTL expired, compute (on_disk, in_flight,
        recently_failed) sets restricted to discovered contracts, enqueue up to
        the cap from eligible pending, update goal counters, transition phase
        if everything's on disk.
        """
        config = goal.config
        provider_name = config.get("provider", "polygon")
        contracts = goal.discovered_contracts or []

        await self._refresh_disk_cache_if_stale(provider_name)
        await self._enqueue_for_goal(goal, contracts, provider_name)

        # Recount after enqueue (disk cache is already current).
        on_disk = self._disk_cache.get(provider_name, set())
        discovered = {c.get("symbol") for c in contracts if c.get("symbol")}
        completed = len(discovered & on_disk)
        in_flight = await self._count_in_flight(provider_name, discovered)
        terminal = discovered & set(goal.terminal_symbols or [])

        # Done criterion: every discovered symbol is either on disk or has
        # been authoritatively answered "no data" by the provider, and
        # nothing is in flight.
        done = (len(discovered) - len(on_disk) - len(terminal)) <= 0

        async with self._sf() as session:
            g = (await session.execute(
                select(DataGoal).where(DataGoal.id == goal.id)
            )).scalar_one()
            # Keep total_items in sync with the discovered list. The PUT
            # /goals/{id} route resets total_items to 0 on edit — without
            # this, the dashboard shows 0% forever after a goal edit even
            # though completed_items climbs.
            g.total_items = len(discovered)
            g.completed_items = completed
            g.failed_items = len(terminal)
            g.error_message = None
            g.last_processed_at = _utcnow()
            if done and in_flight == 0:
                g.phase = "completed"
                g.status = "completed"
            await session.commit()

    async def on_download_complete(
        self,
        provider: str,
        symbols: list[str],
        status: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Called by DownloadManager when any download finishes (success OR
        failure). Updates the disk cache incrementally if the parquet now
        exists; records terminal "no data" failures on each owning goal so the
        state survives a cleanup of the transient market_data_downloads log;
        then tops up the in-flight queue for any active options goal that
        owns this symbol."""
        cache = self._disk_cache.setdefault(provider, set())
        for sym in symbols:
            if isinstance(sym, str) and sym and self._has_parquet(provider, sym):
                cache.add(sym)

        is_terminal_failure = status == "failed" and _is_terminal_failure(error_message)

        # Top up each affected goal, and persist terminal-symbol state where
        # the failure was a "no data returned" answer from the provider.
        async with self._sf() as session:
            goals = (await session.execute(
                select(DataGoal).where(
                    DataGoal.status.in_(("active", "paused")),
                    DataGoal.phase == "downloading",
                    DataGoal.goal_type == "options",
                )
            )).scalars().all()
            owned_per_goal: list[tuple[Any, list[dict], list[str]]] = []
            for goal in goals:
                if (goal.config or {}).get("provider", "polygon") != provider:
                    continue
                cs = goal.discovered_contracts or []
                owned = [s for s in symbols if any(c.get("symbol") == s for c in cs)]
                if not owned:
                    continue
                if is_terminal_failure:
                    existing = set(goal.terminal_symbols or [])
                    updated = existing | set(owned)
                    if updated != existing:
                        goal.terminal_symbols = sorted(updated)
                owned_per_goal.append((goal, cs, owned))
            if is_terminal_failure:
                await session.commit()
            relevant: list[tuple[Any, list[dict]]] = []
            for goal, cs, _owned in owned_per_goal:
                if goal.status != "active":
                    continue
                session.expunge(goal)
                relevant.append((goal, cs))
        for goal, cs in relevant:
            try:
                await self._enqueue_for_goal(goal, cs, provider)
            except Exception:
                logger.exception("on_download_complete enqueue failed for goal %s", goal.id)

    # ─── internals ───────────────────────────────────────────────────────────

    async def _enqueue_for_goal(self, goal: Any, contracts: list[dict], provider: str) -> None:
        """Enqueue (cap - in_flight) eligible pending contracts for one goal."""
        cap = self._dm.concurrency_for(provider) + 1
        discovered = {c.get("symbol") for c in contracts if c.get("symbol")}
        if not discovered:
            return

        on_disk = self._disk_cache.get(provider, set())
        in_flight = await self._in_flight_symbols(provider, discovered)
        slot = cap - len(in_flight)
        if slot <= 0:
            return

        backed_off = await self._backed_off_symbols(provider, discovered)
        terminal = discovered & set(getattr(goal, "terminal_symbols", None) or [])
        eligible = discovered - on_disk - in_flight - backed_off - terminal
        if not eligible:
            return

        # Preserve discovery order so progress is human-legible.
        sym_to_contract = {c["symbol"]: c for c in contracts if c.get("symbol") in eligible}
        ordered = [c for c in contracts if c.get("symbol") in sym_to_contract]

        config = goal.config or {}
        start = date.fromisoformat(config["date_start"])
        end = date.fromisoformat(config["date_end"])

        for c in ordered[:slot]:
            sym = c["symbol"]
            try:
                exp = date.fromisoformat(c["expiration"])
            except Exception:
                continue
            dl_start = max(start, exp - timedelta(days=90))
            dl_end = min(end, exp)
            await self._dm.create_download(
                symbols=[sym],
                date_range_start=dl_start,
                date_range_end=dl_end,
                provider=provider,
                timeframe="1day",
            )

    def _has_parquet(self, provider: str, symbol: str) -> bool:
        path = os.path.join(self._market_dir, provider, symbol, "1day.parquet")
        return os.path.exists(path)

    async def _refresh_disk_cache_if_stale(self, provider: str) -> None:
        last = self._disk_cache_ts.get(provider)
        now = _utcnow()
        if last is not None and now - last < timedelta(seconds=DISK_CACHE_TTL_SECONDS):
            return
        provider_dir = os.path.join(self._market_dir, provider)
        found: set[str] = set()
        try:
            with os.scandir(provider_dir) as it:
                for entry in it:
                    if not entry.is_dir():
                        continue
                    if os.path.exists(os.path.join(entry.path, "1day.parquet")):
                        found.add(entry.name)
        except FileNotFoundError:
            pass
        self._disk_cache[provider] = found
        self._disk_cache_ts[provider] = now

    async def _in_flight_symbols(self, provider: str, candidates: set[str]) -> set[str]:
        """Symbols belonging to `candidates` that have a queued/running row
        in market_data_downloads for this provider."""
        if not candidates:
            return set()
        async with self._sf() as session:
            stmt = (
                select(MarketDataDownload.symbols)
                .where(
                    MarketDataDownload.provider == provider,
                    MarketDataDownload.timeframe == "1day",
                    MarketDataDownload.status.in_(("queued", "running")),
                )
            )
            rows = (await session.execute(stmt)).all()
        out: set[str] = set()
        for (syms,) in rows:
            for s in syms or []:
                if s in candidates:
                    out.add(s)
        return out

    async def _count_in_flight(self, provider: str, candidates: set[str]) -> int:
        return len(await self._in_flight_symbols(provider, candidates))

    async def _backed_off_symbols(
        self, provider: str, candidates: set[str],
    ) -> set[str]:
        """Symbols whose transient failures are still inside their exponential
        backoff window.

        Terminal "no data returned" answers are tracked on the goal itself
        (``DataGoal.terminal_symbols``) so they survive a cleanup of the
        transient ``market_data_downloads`` log. Transient backoff is
        derived from the log on purpose — if the log is cleared we only
        ever retry sooner, never re-do work the provider already answered.
        """
        if not candidates:
            return set()
        async with self._sf() as session:
            stmt = (
                select(MarketDataDownload)
                .where(
                    MarketDataDownload.provider == provider,
                    MarketDataDownload.timeframe == "1day",
                    MarketDataDownload.status.in_(("failed", "completed")),
                )
                .order_by(MarketDataDownload.completed_at.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()

        from collections import defaultdict
        fail_count: dict[str, int] = defaultdict(int)
        last_failed_at: dict[str, datetime] = {}
        latest_was_terminal: dict[str, bool] = {}
        for row in rows:
            for s in row.symbols or []:
                if s not in candidates:
                    continue
                if row.status == "completed":
                    fail_count[s] = 0
                    last_failed_at.pop(s, None)
                    latest_was_terminal[s] = False
                elif row.status == "failed":
                    fail_count[s] += 1
                    if row.completed_at is not None:
                        last_failed_at[s] = row.completed_at
                    latest_was_terminal[s] = _is_terminal_failure(row.error_message)

        backed_off: set[str] = set()
        now = _utcnow()
        for sym, n in fail_count.items():
            if n <= 0 or latest_was_terminal.get(sym):
                continue
            last = last_failed_at.get(sym)
            if last is None:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            delay = _backoff_seconds(n)
            if (now - last).total_seconds() < delay:
                backed_off.add(sym)
        return backed_off

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
