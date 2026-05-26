"""Broker -> supported asset types lookup.

Derives the per-broker capability list by querying each service's
supports_provider() method. Adding a new asset type to the registry
automatically extends every broker's capability list (if supported).
"""
from __future__ import annotations

from coordinator.services.asset_services import (
    AssetType,
    get_default_registry,
)

_KNOWN_BROKERS = {"alpaca", "tradier"}


def asset_types_for_broker(broker_type: str) -> list[str]:
    if broker_type not in _KNOWN_BROKERS:
        raise ValueError(f"Unknown broker: {broker_type}")
    registry = get_default_registry()
    supported: list[str] = []
    for at in AssetType:
        svc = registry.get_service_by_type(at)
        if svc.supports_provider(broker_type):
            supported.append(at.value)
    return supported


# Back-compat shim for any callers still importing BROKER_ASSET_TYPES.
BROKER_ASSET_TYPES: dict[str, list[str]] = {
    b: asset_types_for_broker(b) for b in _KNOWN_BROKERS
}
