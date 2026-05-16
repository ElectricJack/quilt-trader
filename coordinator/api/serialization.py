"""Timestamp serialization helpers for API responses.

Why this exists: SQLite returns naive datetimes even when columns are declared
DateTime(timezone=True), so .isoformat() emits offset-less strings that the
browser interprets as local time. This produces wildly wrong "ago" math —
e.g. -25187s ago for a UTC-7 user. Always route timestamps through this
helper before serializing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def to_iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    return iso.replace("+00:00", "Z")
