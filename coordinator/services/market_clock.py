"""US equity market clock — used by interval-trigger algorithms.

v1 only handles US equities and equity_options (same hours). All other
asset types return True (always open) — algorithms for futures/crypto/forex
are responsible for their own time gating until we add proper calendars.

Holidays cover 2024-2026 explicitly. Annual maintenance required.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

US_EQUITIES_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19),
    date(2024, 3, 29), date(2024, 5, 27), date(2024, 6, 19),
    date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28), date(2024, 12, 25),
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
}

def _to_et(ts_utc: datetime) -> datetime:
    """Convert UTC to America/New_York wall clock."""
    try:
        from zoneinfo import ZoneInfo
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=timezone.utc)
        return ts_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return (ts_utc - timedelta(hours=5)).replace(tzinfo=None)


def is_market_open(symbol_or_asset_type: str, ts: datetime) -> bool:
    """Whether the market is open at ``ts``.

    Accepts either a symbol (preferred — routes through registry) or
    a legacy asset_type string like "equities" / "crypto" / "options".

    Unrecognized asset_type strings (e.g. "futures", "fx") return True,
    preserving the legacy "unknown asset types trade 24/7" semantic.
    Holiday checks remain here (registry services don't track holidays).
    """
    from coordinator.services.asset_services import (
        AssetType,
        get_default_registry,
    )
    registry = get_default_registry()
    # Legacy alias: "equity_options" → AssetType.OPTIONS
    if symbol_or_asset_type == "equity_options":
        symbol_or_asset_type = AssetType.OPTIONS.value

    try:
        at = AssetType(symbol_or_asset_type)
        svc = registry.get_service_by_type(at)
    except ValueError:
        # Lowercase strings that aren't valid AssetType values are
        # treated as legacy unknown asset types → 24/7.
        if symbol_or_asset_type.islower():
            return True
        svc = registry.get_service(symbol_or_asset_type)

    if svc.asset_type == AssetType.CRYPTO:
        return True
    et = _to_et(ts)
    if et.date() in US_EQUITIES_HOLIDAYS:
        return False
    return svc.is_market_open(ts)
