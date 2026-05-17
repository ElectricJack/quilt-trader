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

EQUITIES_TYPES = {"equities", "equity_options"}


def _to_et(ts_utc: datetime) -> datetime:
    """Convert UTC to America/New_York wall clock."""
    try:
        from zoneinfo import ZoneInfo
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=timezone.utc)
        return ts_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # Last-resort fallback assumes EST (no DST). Bounded but acceptable.
        return (ts_utc - timedelta(hours=5)).replace(tzinfo=None)


def is_market_open(asset_type: str, ts: datetime) -> bool:
    if asset_type not in EQUITIES_TYPES:
        return True
    et = _to_et(ts)
    if et.weekday() >= 5:
        return False
    if et.date() in US_EQUITIES_HOLIDAYS:
        return False
    open_t = time(9, 30)
    close_t = time(16, 0)
    return open_t <= et.time() <= close_t
