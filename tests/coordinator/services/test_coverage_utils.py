"""Tests for ensure_coverage."""
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from coordinator.services.coverage_utils import ensure_coverage
from coordinator.services.coverage_index import CoverageIndex


def _make_coverage_index(*date_ranges: tuple[date, date]) -> CoverageIndex:
    """Build a CoverageIndex whose _scan returns fixed ranges."""
    from coordinator.services.coverage_index import _find_contiguous_ranges
    rows = []
    for d_start, d_end in date_ranges:
        for d in pd.bdate_range(d_start, d_end):
            rows.append({"timestamp": pd.Timestamp(d, tz="UTC")})
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["timestamp"])

    ds = MagicMock()
    ds.load_market_data.return_value = df
    return CoverageIndex(ds)


def _make_download_manager(download_id: str = "dl-001") -> AsyncMock:
    mgr = MagicMock()
    mgr.create_download = AsyncMock(return_value={"id": download_id})
    return mgr


# ─── Full coverage — no downloads ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fully_covered_no_downloads():
    idx = _make_coverage_index((date(2026, 1, 2), date(2026, 1, 30)))
    mgr = _make_download_manager()
    ids = await ensure_coverage(
        "polygon", "SPY",
        date(2026, 1, 5), date(2026, 1, 20),
        mgr, idx,
    )
    assert ids == []
    mgr.create_download.assert_not_called()


# ─── No data at all — one download spanning the whole range ───────────────────

@pytest.mark.asyncio
async def test_no_data_downloads_full_range():
    ds = MagicMock()
    ds.load_market_data.return_value = None
    idx = CoverageIndex(ds)
    mgr = _make_download_manager("dl-001")

    ids = await ensure_coverage(
        "polygon", "SPY",
        date(2026, 1, 5), date(2026, 1, 10),
        mgr, idx,
    )
    assert ids == ["dl-001"]
    mgr.create_download.assert_called_once()
    call_kwargs = mgr.create_download.call_args.kwargs
    # Edges expanded by 1 day
    assert call_kwargs["date_range_start"] == date(2026, 1, 4)
    assert call_kwargs["date_range_end"] == date(2026, 1, 11)
    assert call_kwargs["symbols"] == ["SPY"]
    assert call_kwargs["provider"] == "polygon"
    assert call_kwargs["timeframe"] == "1min"


# ─── Middle gap — one download with correct edge overlap ──────────────────────

@pytest.mark.asyncio
async def test_middle_gap_one_download_with_overlap():
    idx = _make_coverage_index(
        (date(2026, 1, 2), date(2026, 1, 9)),
        (date(2026, 1, 20), date(2026, 1, 30)),
    )
    mgr = _make_download_manager("dl-middle")

    ids = await ensure_coverage(
        "polygon", "SPY",
        date(2026, 1, 2), date(2026, 1, 30),
        mgr, idx,
    )
    assert ids == ["dl-middle"]
    call_kwargs = mgr.create_download.call_args.kwargs
    # Gap is Jan 10 – Jan 19; expanded ±1 day
    assert call_kwargs["date_range_start"] == date(2026, 1, 9)
    assert call_kwargs["date_range_end"] == date(2026, 1, 20)


# ─── Multiple gaps — multiple downloads ───────────────────────────────────────

@pytest.mark.asyncio
async def test_multiple_gaps_multiple_downloads():
    idx = _make_coverage_index(
        (date(2026, 1, 12), date(2026, 1, 16)),  # only middle covered
    )
    mgr = MagicMock()
    mgr.create_download = AsyncMock(side_effect=[
        {"id": "dl-leading"},
        {"id": "dl-trailing"},
    ])

    ids = await ensure_coverage(
        "polygon", "SPY",
        date(2026, 1, 2), date(2026, 1, 30),
        mgr, idx,
    )
    assert len(ids) == 2
    assert "dl-leading" in ids
    assert "dl-trailing" in ids
    assert mgr.create_download.call_count == 2


# ─── Cache invalidated after ensure_coverage ──────────────────────────────────

@pytest.mark.asyncio
async def test_cache_invalidated_after_call():
    ds = MagicMock()
    ds.load_market_data.return_value = None
    idx = CoverageIndex(ds)
    mgr = _make_download_manager()

    # Pre-warm the cache
    idx.get_ranges("polygon", "SPY")
    calls_after_warmup = ds.load_market_data.call_count

    await ensure_coverage(
        "polygon", "SPY",
        date(2026, 1, 2), date(2026, 1, 5),
        mgr, idx,
    )
    # Cache should be invalidated — next call must re-scan (more load_market_data calls)
    idx.get_ranges("polygon", "SPY")
    assert ds.load_market_data.call_count > calls_after_warmup


# ─── Custom timeframe forwarded correctly ─────────────────────────────────────

@pytest.mark.asyncio
async def test_custom_timeframe_forwarded():
    ds = MagicMock()
    ds.load_market_data.return_value = None
    idx = CoverageIndex(ds)
    mgr = _make_download_manager()

    await ensure_coverage(
        "theta", "AAPL",
        date(2026, 1, 2), date(2026, 1, 5),
        mgr, idx,
        timeframe="1day",
    )
    call_kwargs = mgr.create_download.call_args.kwargs
    assert call_kwargs["timeframe"] == "1day"
    assert call_kwargs["provider"] == "theta"
    assert call_kwargs["symbols"] == ["AAPL"]
