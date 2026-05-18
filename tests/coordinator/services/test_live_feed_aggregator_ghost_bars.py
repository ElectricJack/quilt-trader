from datetime import datetime, timezone
from coordinator.services.live_feed_aggregator import _BarBuilder


def test_take_closed_skips_bar_with_zero_volume_and_no_range():
    """A bar where vol==0 AND high==low is quote-only / no-activity noise."""
    bb = _BarBuilder()
    bb.minute_start = datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc)
    bb.open_ = 500.0
    bb.high = 500.0
    bb.low = 500.0
    bb.close = 500.0
    bb.volume = 0.0
    later = datetime(2026, 5, 18, 14, 31, tzinfo=timezone.utc)
    row = bb.take_closed(later)
    assert row is None, "ghost bar (vol=0, high==low) must be suppressed"


def test_take_closed_keeps_bar_with_real_trade():
    bb = _BarBuilder()
    bb.minute_start = datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc)
    bb.open_ = 500.0
    bb.high = 500.5
    bb.low = 499.5
    bb.close = 500.25
    bb.volume = 100.0
    later = datetime(2026, 5, 18, 14, 31, tzinfo=timezone.utc)
    row = bb.take_closed(later)
    assert row is not None
    assert row["volume"] == 100.0
    assert row["close"] == 500.25


def test_take_closed_keeps_bar_with_volume_but_flat_price():
    """A real trade where every fill happened to be at the same price still
    matters — keep it. Only vol==0 AND high==low are ghost."""
    bb = _BarBuilder()
    bb.minute_start = datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc)
    bb.open_ = 500.0
    bb.high = 500.0
    bb.low = 500.0
    bb.close = 500.0
    bb.volume = 50.0
    later = datetime(2026, 5, 18, 14, 31, tzinfo=timezone.utc)
    row = bb.take_closed(later)
    assert row is not None
    assert row["volume"] == 50.0
