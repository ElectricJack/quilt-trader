from datetime import datetime, timezone


def test_equities_open_during_regular_hours():
    from coordinator.services.market_clock import is_market_open
    # 2026-05-15 (Fri), 14:00 UTC == 10:00 EDT == during 09:30-16:00.
    ts = datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc)
    assert is_market_open("equities", ts)


def test_equities_closed_at_night():
    from coordinator.services.market_clock import is_market_open
    ts = datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc)  # 19:00 EDT
    assert not is_market_open("equities", ts)


def test_equities_closed_on_weekend():
    from coordinator.services.market_clock import is_market_open
    ts = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)  # Saturday
    assert not is_market_open("equities", ts)


def test_equities_closed_on_holiday():
    from coordinator.services.market_clock import is_market_open
    # 2026-01-01 New Year's Day (Thu) — should be closed.
    ts = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
    assert not is_market_open("equities", ts)


def test_unknown_asset_type_returns_true():
    from coordinator.services.market_clock import is_market_open
    ts = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)  # Saturday
    assert is_market_open("crypto", ts)
    assert is_market_open("futures", ts)


def test_equity_options_uses_same_calendar_as_equities():
    from coordinator.services.market_clock import is_market_open
    ts_weekday = datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc)
    ts_weekend = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)
    assert is_market_open("equity_options", ts_weekday)
    assert not is_market_open("equity_options", ts_weekend)
