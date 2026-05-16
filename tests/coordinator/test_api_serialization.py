from datetime import datetime, timezone, timedelta
from coordinator.api.serialization import to_iso_utc


def test_to_iso_utc_handles_none():
    assert to_iso_utc(None) is None


def test_to_iso_utc_assumes_utc_for_naive_datetimes():
    dt = datetime(2026, 5, 16, 12, 34, 56)  # naive
    assert to_iso_utc(dt) == "2026-05-16T12:34:56Z"


def test_to_iso_utc_converts_aware_datetimes_to_utc():
    dt = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone(timedelta(hours=-7)))
    assert to_iso_utc(dt) == "2026-05-16T19:00:00Z"


def test_to_iso_utc_preserves_utc_datetimes():
    dt = datetime(2026, 5, 16, 12, 34, 56, tzinfo=timezone.utc)
    assert to_iso_utc(dt) == "2026-05-16T12:34:56Z"
