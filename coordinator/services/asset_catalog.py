"""Catalog of asset types supported per broker.

Drives the account-creation checkbox UI (Spec A §1) and the order-ticket
asset-type filter. Adding a broker = adding a key here + implementing the
adapter side.
"""
from __future__ import annotations

BROKER_ASSET_TYPES: dict[str, list[str]] = {
    "alpaca":  ["equities", "options", "crypto"],
    "tradier": ["equities", "options"],
}


def asset_types_for_broker(broker_type: str) -> list[str]:
    if broker_type not in BROKER_ASSET_TYPES:
        raise ValueError(f"Unknown broker: {broker_type}")
    return list(BROKER_ASSET_TYPES[broker_type])
