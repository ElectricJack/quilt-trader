import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import LiveSubscription
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


def _check_compatibility(account: dict, algorithm: dict) -> CompatibilityResult:
    """Return a CompatibilityResult for the given account/algorithm dicts."""
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


def _parse_assets(assets: Any) -> list[dict]:
    """Return list of {symbol, asset_class} dicts.

    Asset class is determined by the registry from the symbol — any
    incoming asset_class field is ignored (registry is authoritative).
    """
    from coordinator.services.asset_services import get_default_registry
    registry = get_default_registry()
    out: list[dict] = []
    if not assets:
        return out
    for a in assets:
        if not isinstance(a, dict):
            continue
        symbol = a.get("symbol")
        if symbol:
            out.append({
                "symbol": symbol,
                "asset_class": registry.classify(symbol).value,
            })
    return out


class LifecycleService:
    """Lightweight lifecycle service that reads from ``algorithm.assets``
    (the new structured format) to auto-create LiveSubscription + consumer
    rows on deploy and tear them down on stop."""

    def __init__(
        self,
        scraper_manager: ScraperManager,
        live_feed_manager: Optional[LiveFeedManager] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        live_feed_aggregator: Optional[Any] = None,
    ) -> None:
        self._scraper_manager = scraper_manager
        self._live_feed_manager = live_feed_manager
        self._session_factory = session_factory
        # When set, pre_start_checks calls .start_subscription so the broker
        # WS actually opens at deploy time (instead of waiting for a coord
        # restart). post_stop_actions calls .stop_subscription on orphan delete.
        self._live_feed_aggregator = live_feed_aggregator

    @staticmethod
    def check_compatibility(account: dict, algorithm: dict) -> CompatibilityResult:
        return _check_compatibility(account, algorithm)

    async def pre_start_checks(self, account: Any, algorithm: Any, instance: Any) -> None:
        if account.locked_by is not None and account.locked_by != instance.id:
            raise CompatibilityError(f"Account is locked by instance {account.locked_by}")
        result = _check_compatibility(
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

        assets = _parse_assets(algorithm.assets)
        if not assets or self._live_feed_manager is None or self._session_factory is None:
            return

        # Asset-class compatibility: every declared asset class must be in the
        # account's supported_asset_types.
        supported = set(account.supported_asset_types or [])
        declared = {a["asset_class"] for a in assets}
        missing = declared - supported
        if missing:
            raise CompatibilityError(
                f"Account does not support asset class(es): {', '.join(sorted(missing))}"
            )

        from coordinator.database.models import LiveSubscription, SubscriptionConsumer
        from sqlalchemy.orm import selectinload

        async with self._session_factory() as session:
            for asset in assets:
                symbol = asset["symbol"]
                asset_class = asset["asset_class"]
                sub = (await session.execute(
                    select(LiveSubscription)
                    .where(
                        LiveSubscription.account_id == account.id,
                        LiveSubscription.symbol == symbol,
                    )
                    .options(selectinload(LiveSubscription.consumers))
                )).scalar_one_or_none()
                if sub is None:
                    sub = LiveSubscription(
                        account_id=account.id,
                        broker=account.broker_type,
                        symbol=symbol,
                        asset_class=asset_class,
                        status="running",
                    )
                    session.add(sub)
                    await session.flush()
                already = (await session.execute(
                    select(SubscriptionConsumer).where(
                        SubscriptionConsumer.subscription_id == sub.id,
                        SubscriptionConsumer.consumer_type == "algo",
                        SubscriptionConsumer.consumer_id == instance.id,
                    )
                )).scalar_one_or_none()
                if already is None:
                    session.add(SubscriptionConsumer(
                        subscription_id=sub.id, consumer_type="algo",
                        consumer_id=instance.id,
                    ))
                self._live_feed_manager.ensure_running(
                    account.broker_type, symbol, instance.id,
                )
            await session.commit()

        if self._live_feed_aggregator is not None:
            for asset in assets:
                try:
                    await self._live_feed_aggregator.start_subscription(
                        account.id, account.broker_type,
                        asset["symbol"], asset["asset_class"],
                    )
                except TypeError:
                    # Aggregator still on old 3-arg form; Task 5 fixes.
                    try:
                        await self._live_feed_aggregator.start_subscription(
                            account.broker_type, asset["symbol"], asset["asset_class"],
                        )
                    except Exception:
                        import logging
                        logging.getLogger(__name__).exception(
                            "Failed to start_subscription for %s/%s",
                            account.broker_type, asset["symbol"],
                        )
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "Failed to start_subscription for %s/%s",
                        account.broker_type, asset["symbol"],
                    )

    async def post_stop_actions(self, account, algorithm, instance) -> None:
        """Release any algo consumers this instance held. If a subscription has
        no remaining consumers, delete the row (symmetric auto-delete)."""
        assets = _parse_assets(algorithm.assets)
        if not assets or self._live_feed_manager is None or self._session_factory is None:
            return

        from coordinator.database.models import LiveSubscription, SubscriptionConsumer
        from sqlalchemy.orm import selectinload

        orphaned: list[tuple[str, str]] = []
        async with self._session_factory() as session:
            for asset in assets:
                symbol = asset["symbol"]
                self._live_feed_manager.release(account.broker_type, symbol, instance.id)

                sub = (await session.execute(
                    select(LiveSubscription)
                    .where(
                        LiveSubscription.account_id == account.id,
                        LiveSubscription.symbol == symbol,
                    )
                    .options(selectinload(LiveSubscription.consumers))
                )).scalar_one_or_none()
                if sub is None:
                    continue
                for c in list(sub.consumers):
                    if c.consumer_type == "algo" and c.consumer_id == instance.id:
                        await session.delete(c)
                await session.flush()
                await session.refresh(sub, ["consumers"])
                if not sub.consumers:
                    await session.delete(sub)
                    orphaned.append((account.id, symbol))
            await session.commit()

        if self._live_feed_aggregator is not None:
            for account_id, symbol in orphaned:
                try:
                    await self._live_feed_aggregator.stop_subscription(account_id, symbol)
                except TypeError:
                    # Old (broker, symbol) form
                    try:
                        await self._live_feed_aggregator.stop_subscription(
                            account.broker_type, symbol,
                        )
                    except Exception:
                        import logging
                        logging.getLogger(__name__).exception(
                            "Failed to stop_subscription for %s/%s", account_id, symbol,
                        )
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "Failed to stop_subscription for %s/%s", account_id, symbol,
                    )
