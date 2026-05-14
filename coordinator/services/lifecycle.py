import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import LiveSubscription
from coordinator.services.event_bus import EventBus
from coordinator.services.live_feed_manager import LiveFeedManager
from coordinator.services.scraper_manager import ScraperManager

logger = logging.getLogger(__name__)


class CompatibilityError(Exception):
    pass


class StartError(Exception):
    """Raised when an instance cannot start due to a missing precondition.

    Distinct from ``CompatibilityError`` (which is about account/algo
    feature mismatch) — ``StartError`` covers external preconditions like
    a missing live-data subscription.
    """


@dataclass
class CompatibilityResult:
    compatible: bool
    mismatches: list[str] = field(default_factory=list)


def _split_data_deps(deps: Any) -> tuple[list[dict], list[dict]]:
    """Return (broker_live, historical) lists. ``deps`` may be None, list of
    dicts, or list of strings (legacy scraper names).
    """
    broker_live: list[dict] = []
    historical: list[dict] = []
    if not deps:
        return broker_live, historical
    for d in deps:
        if not isinstance(d, dict):
            # Legacy: a bare string is the name of a scraper-style dep.
            continue
        # `source` defaults to broker_live per spec §"Backwards compat".
        # But: an entry with no `symbol` (e.g. a scraper) cannot be a
        # live-data dep regardless of declared source.
        src = d.get("source", "broker_live")
        if src == "broker_live" and d.get("symbol"):
            broker_live.append(d)
        else:
            historical.append(d)
    return broker_live, historical


class LifecycleManager:
    def __init__(
        self,
        worker_manager: Any,
        scraper_manager: ScraperManager,
        event_bus: EventBus,
        live_feed_manager: Optional[LiveFeedManager] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    ) -> None:
        self._worker_manager = worker_manager
        self._scraper_manager = scraper_manager
        self._event_bus = event_bus
        self._live_feed_manager = live_feed_manager
        self._session_factory = session_factory

    @staticmethod
    def check_compatibility(account: dict, algorithm: dict) -> CompatibilityResult:
        mismatches = []
        for t in (algorithm.get("required_asset_types") or []):
            if t not in (account.get("supported_asset_types") or []):
                mismatches.append(f"Missing asset type: {t}")
        req_opt = algorithm.get("required_options_level")
        acct_opt = account.get("options_level")
        if req_opt is not None and (acct_opt is None or acct_opt < req_opt):
            mismatches.append(
                f"Insufficient options level: requires {req_opt}, account has {acct_opt}"
            )
        for f in (algorithm.get("required_account_features") or []):
            if f not in (account.get("account_features") or []):
                mismatches.append(f"Missing account feature: {f}")
        brokers = algorithm.get("supported_brokers")
        if brokers is not None and account.get("broker_type") not in brokers:
            mismatches.append(
                f"Unsupported broker: {account.get('broker_type')} (algorithm supports: {brokers})"
            )
        return CompatibilityResult(compatible=len(mismatches) == 0, mismatches=mismatches)

    async def pre_start_checks(self, account: Any, algorithm: Any, instance: Any) -> None:
        if account.locked_by is not None and account.locked_by != instance.id:
            raise CompatibilityError(f"Account is locked by instance {account.locked_by}")
        result = self.check_compatibility(
            {
                "supported_asset_types": account.supported_asset_types or [],
                "options_level": account.options_level,
                "account_features": account.account_features or [],
                "broker_type": account.broker_type,
            },
            {
                "required_asset_types": algorithm.required_asset_types or [],
                "required_options_level": algorithm.required_options_level,
                "required_account_features": algorithm.required_account_features or [],
                "supported_brokers": algorithm.supported_brokers,
            },
        )
        if not result.compatible:
            raise CompatibilityError(
                f"Compatibility check failed: {'; '.join(result.mismatches)}"
            )

        deps = algorithm.data_dependencies
        broker_live, historical = _split_data_deps(deps)

        # Historical scraper deps: keep the original behavior.
        for dep in (historical or []):
            name = dep.get("name") if isinstance(dep, dict) else dep
            if name and self._scraper_manager.is_registered(name):
                self._scraper_manager.ensure_running(name, instance.id)

        # Broker-live deps: gate against LiveSubscription rows and auto-add the
        # instance as a dependent. Skipped when no live-feed wiring is provided
        # (early phases / unit tests that don't care).
        if not broker_live or self._live_feed_manager is None or self._session_factory is None:
            return

        broker = account.broker_type
        async with self._session_factory() as session:
            for dep in broker_live:
                symbol = dep["symbol"]
                sub = (
                    await session.execute(
                        select(LiveSubscription).where(
                            LiveSubscription.broker == broker,
                            LiveSubscription.symbol == symbol,
                        )
                    )
                ).scalar_one_or_none()
                if sub is None or sub.status != "running":
                    raise StartError(
                        f"Cannot start: no live subscription for {symbol} on {broker}. "
                        f"Subscribe on the Data page first."
                    )
            # All gates passed — register dependents and bump counters.
            for dep in broker_live:
                symbol = dep["symbol"]
                self._live_feed_manager.ensure_running(broker, symbol, instance.id)
                sub = (
                    await session.execute(
                        select(LiveSubscription).where(
                            LiveSubscription.broker == broker,
                            LiveSubscription.symbol == symbol,
                        )
                    )
                ).scalar_one()
                sub.dependent_count = (sub.dependent_count or 0) + 1
            await session.commit()

    async def post_stop_actions(
        self, account: Any, algorithm: Any, instance: Any
    ) -> None:
        """Release any broker_live dependents this instance held.

        Counterpart of ``pre_start_checks``. Safe to call even if startup
        never bumped a counter — release() is idempotent.
        """
        deps = algorithm.data_dependencies
        broker_live, _ = _split_data_deps(deps)
        if not broker_live or self._live_feed_manager is None or self._session_factory is None:
            return

        broker = account.broker_type
        async with self._session_factory() as session:
            for dep in broker_live:
                symbol = dep["symbol"]
                # In-memory release
                self._live_feed_manager.release(broker, symbol, instance.id)
                # DB mirror
                sub = (
                    await session.execute(
                        select(LiveSubscription).where(
                            LiveSubscription.broker == broker,
                            LiveSubscription.symbol == symbol,
                        )
                    )
                ).scalar_one_or_none()
                if sub is not None:
                    sub.dependent_count = max(0, (sub.dependent_count or 0) - 1)
            await session.commit()
