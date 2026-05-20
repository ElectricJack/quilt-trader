"""CoverageIndex — lightweight in-memory index of cached market data date ranges.

Rebuilt from disk on first access; invalidated when new data is written.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from coordinator.services.data_service import DataService

# Number of consecutive missing business days that splits one range into two.
_GAP_THRESHOLD_BDAYS = 3


def _find_contiguous_ranges(dates: list[date]) -> list[tuple[date, date]]:
    """Given a sorted list of unique dates, find contiguous business-day runs.

    Two dates are in the same run when there are fewer than _GAP_THRESHOLD_BDAYS
    missing business days between them.  Weekend days (Sat/Sun) do not count as
    missing for equity data.
    """
    if not dates:
        return []

    ranges: list[tuple[date, date]] = []
    run_start = dates[0]
    prev = dates[0]

    for d in dates[1:]:
        # Count missing business days between prev and d (exclusive both ends)
        bdays_between = len(pd.bdate_range(prev, d, inclusive="neither"))
        if bdays_between >= _GAP_THRESHOLD_BDAYS:
            ranges.append((run_start, prev))
            run_start = d
        prev = d

    ranges.append((run_start, prev))
    return ranges


class CoverageIndex:
    """Track which date ranges are cached on disk per (provider, symbol) pair."""

    def __init__(self, data_service: "DataService") -> None:
        self._ds = data_service
        self._cache: dict[tuple[str, str], list[tuple[date, date]]] = {}

    # ------------------------------------------------------------------ public

    def get_ranges(self, provider: str, symbol: str) -> list[tuple[date, date]]:
        """Return sorted list of contiguous date ranges on disk."""
        key = (provider, symbol)
        if key not in self._cache:
            self._cache[key] = self._scan(provider, symbol)
        return self._cache[key]

    def get_gaps(
        self, provider: str, symbol: str, start: date, end: date
    ) -> list[tuple[date, date]]:
        """Return date ranges within [start, end] NOT covered by cached data."""
        ranges = self.get_ranges(provider, symbol)
        gaps: list[tuple[date, date]] = []

        cursor = start
        for r_start, r_end in ranges:
            if cursor > end:
                break
            if r_end < cursor:
                # This cached range is entirely before our window — skip.
                continue
            if r_start > end:
                # This cached range is entirely after our window — done.
                break
            if r_start > cursor:
                # Gap from cursor up to the start of this range.
                gap_end = min(r_start - pd.Timedelta(days=1), end)
                if cursor <= gap_end:
                    gaps.append((cursor, gap_end.date() if hasattr(gap_end, "date") else gap_end))
            # Advance cursor past this range.
            cursor = r_end + pd.Timedelta(days=1)
            cursor = cursor.date() if hasattr(cursor, "date") else cursor

        # Trailing gap after all ranges.
        if cursor <= end:
            gaps.append((cursor, end))

        return gaps

    def invalidate(self, provider: str, symbol: str) -> None:
        """Clear cached ranges so the next call re-scans disk."""
        self._cache.pop((provider, symbol), None)

    # ----------------------------------------------------------------- private

    def _scan(self, provider: str, symbol: str) -> list[tuple[date, date]]:
        """Load the 1-min parquet (or fallback timeframe), return contiguous ranges."""
        df = self._ds.load_market_data(provider, symbol, "1min")

        if df is None or df.empty:
            for tf in ("1day", "5min", "15min", "1hour"):
                df = self._ds.load_market_data(provider, symbol, tf)
                if df is not None and not df.empty:
                    break

        if df is None or df.empty:
            return []

        if "timestamp" not in df.columns:
            return []

        ts = pd.to_datetime(df["timestamp"], utc=True)
        unique_dates = sorted({t.date() for t in ts})
        return _find_contiguous_ranges(unique_dates)
