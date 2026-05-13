import logging
from dataclasses import dataclass, field
from typing import Any

from coordinator.services.event_bus import EventBus
from coordinator.services.scraper_manager import ScraperManager

logger = logging.getLogger(__name__)


class CompatibilityError(Exception):
    pass


@dataclass
class CompatibilityResult:
    compatible: bool
    mismatches: list[str] = field(default_factory=list)


class LifecycleManager:
    def __init__(
        self,
        worker_manager: Any,
        scraper_manager: ScraperManager,
        event_bus: EventBus,
    ) -> None:
        self._worker_manager = worker_manager
        self._scraper_manager = scraper_manager
        self._event_bus = event_bus

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
        if deps:
            for dep in deps:
                name = dep.get("name") if isinstance(dep, dict) else dep
                if self._scraper_manager.is_registered(name):
                    self._scraper_manager.ensure_running(name, instance.id)
