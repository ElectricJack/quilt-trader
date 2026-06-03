"""Tests for CoverageIndex."""
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from coordinator.services.coverage_index import CoverageIndex


def _make_ds(*date_ranges: tuple[date, date]) -> MagicMock:
    """Build a mock DataService whose load_market_data returns bars for the given ranges."""
    rows = []
    for d_start, d_end in date_ranges:
        for d in pd.bdate_range(d_start, d_end):
            rows.append({"timestamp": pd.Timestamp(d, tz="UTC"), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0})
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["timestamp"])

    ds = MagicMock()
    ds.load_market_data.return_value = df
    return ds


def _make_ds_none() -> MagicMock:
    ds = MagicMock()
    ds.load_market_data.return_value = None
    return ds


def _make_ds_empty() -> MagicMock:
    ds = MagicMock()
    ds.load_market_data.return_value = pd.DataFrame(columns=["timestamp"])
    return ds


# ─── get_ranges ───────────────────────────────────────────────────────────────

def test_ranges_single_contiguous_block():
    ds = _make_ds((date(2026, 1, 2), date(2026, 1, 9)))
    idx = CoverageIndex(ds)
    ranges = idx.get_ranges("polygon", "SPY")
    assert len(ranges) == 1
    assert ranges[0][0] == date(2026, 1, 2)
    assert ranges[0][1] == date(2026, 1, 9)


def test_ranges_two_separate_blocks():
    """Two date ranges separated by a gap of several business days → two ranges."""
    ds = _make_ds(
        (date(2026, 1, 2), date(2026, 1, 9)),    # week 1
        (date(2026, 1, 20), date(2026, 1, 23)),  # week 3 (gap: Jan 12–16)
    )
    idx = CoverageIndex(ds)
    ranges = idx.get_ranges("polygon", "SPY")
    assert len(ranges) == 2
    assert ranges[0][0] == date(2026, 1, 2)
    assert ranges[0][1] == date(2026, 1, 9)
    assert ranges[1][0] == date(2026, 1, 20)
    assert ranges[1][1] == date(2026, 1, 23)


def test_ranges_cached_on_second_call():
    ds = _make_ds((date(2026, 1, 2), date(2026, 1, 5)))
    idx = CoverageIndex(ds)
    idx.get_ranges("polygon", "SPY")
    idx.get_ranges("polygon", "SPY")
    # load_market_data called only once (first call; second uses cache)
    assert ds.load_market_data.call_count == 1


def test_ranges_empty_data():
    idx = CoverageIndex(_make_ds_none())
    assert idx.get_ranges("polygon", "SPY") == []


def test_ranges_empty_dataframe():
    idx = CoverageIndex(_make_ds_empty())
    assert idx.get_ranges("polygon", "SPY") == []


# ─── get_gaps ─────────────────────────────────────────────────────────────────

def test_gaps_fully_covered():
    ds = _make_ds((date(2026, 1, 2), date(2026, 1, 30)))
    idx = CoverageIndex(ds)
    gaps = idx.get_gaps("polygon", "SPY", date(2026, 1, 5), date(2026, 1, 20))
    assert gaps == []


def test_gaps_no_data_at_all():
    idx = CoverageIndex(_make_ds_none())
    gaps = idx.get_gaps("polygon", "SPY", date(2026, 1, 2), date(2026, 1, 9))
    assert gaps == [(date(2026, 1, 2), date(2026, 1, 9))]


def test_gaps_leading_gap():
    """Cached data starts AFTER requested start → leading gap."""
    ds = _make_ds((date(2026, 1, 20), date(2026, 1, 30)))
    idx = CoverageIndex(ds)
    gaps = idx.get_gaps("polygon", "SPY", date(2026, 1, 2), date(2026, 1, 30))
    # Gap from Jan 2 up to Jan 19
    assert len(gaps) == 1
    assert gaps[0][0] == date(2026, 1, 2)
    assert gaps[0][1] == date(2026, 1, 19)


def test_gaps_trailing_gap():
    """Cached data ends BEFORE requested end → trailing gap."""
    ds = _make_ds((date(2026, 1, 2), date(2026, 1, 15)))
    idx = CoverageIndex(ds)
    gaps = idx.get_gaps("polygon", "SPY", date(2026, 1, 2), date(2026, 1, 30))
    assert len(gaps) == 1
    assert gaps[0][0] == date(2026, 1, 16)
    assert gaps[0][1] == date(2026, 1, 30)


def test_gaps_middle_gap():
    """Gap between two cached ranges falls inside the requested window."""
    ds = _make_ds(
        (date(2026, 1, 2), date(2026, 1, 9)),
        (date(2026, 1, 20), date(2026, 1, 30)),
    )
    idx = CoverageIndex(ds)
    gaps = idx.get_gaps("polygon", "SPY", date(2026, 1, 2), date(2026, 1, 30))
    assert len(gaps) == 1
    assert gaps[0][0] == date(2026, 1, 10)
    assert gaps[0][1] == date(2026, 1, 19)


def test_gaps_leading_and_trailing():
    """Cache covers the middle; both edges are missing."""
    ds = _make_ds((date(2026, 1, 12), date(2026, 1, 16)))
    idx = CoverageIndex(ds)
    gaps = idx.get_gaps("polygon", "SPY", date(2026, 1, 2), date(2026, 1, 30))
    assert len(gaps) == 2
    assert gaps[0][1] < date(2026, 1, 12)  # leading
    assert gaps[1][0] > date(2026, 1, 16)  # trailing


# ─── invalidate ───────────────────────────────────────────────────────────────

def test_invalidate_clears_cache():
    ds = _make_ds((date(2026, 1, 2), date(2026, 1, 5)))
    idx = CoverageIndex(ds)
    idx.get_ranges("polygon", "SPY")          # populates cache
    idx.invalidate("polygon", "SPY")           # clears it
    idx.get_ranges("polygon", "SPY")          # should re-scan
    assert ds.load_market_data.call_count == 2


def test_invalidate_other_key_unaffected():
    ds = _make_ds((date(2026, 1, 2), date(2026, 1, 5)))
    idx = CoverageIndex(ds)
    idx.get_ranges("polygon", "SPY")
    idx.get_ranges("polygon", "QQQ")
    idx.invalidate("polygon", "SPY")
    # QQQ cache still present — no extra call for QQQ
    call_count_before = ds.load_market_data.call_count
    idx.get_ranges("polygon", "QQQ")
    assert ds.load_market_data.call_count == call_count_before


def test_ranges_returns_empty_when_load_raises_validation_error():
    """Orphan non-canonical symbol directories (left over from the canonical-symbol
    migration) cause data_service.load_market_data to raise ValueError on validation.
    CoverageIndex must skip those entries instead of aborting the whole scan."""
    ds = MagicMock()
    ds.load_market_data.side_effect = ValueError(
        "'ETH' is not a canonical symbol. Crypto canonical form is e.g. 'BTCUSD'."
    )
    idx = CoverageIndex(ds)
    assert idx.get_ranges("polygon", "ETH") == []
    assert idx.get_gaps("polygon", "ETH", date(2026, 1, 1), date(2026, 1, 31)) == [
        (date(2026, 1, 1), date(2026, 1, 31))
    ]
