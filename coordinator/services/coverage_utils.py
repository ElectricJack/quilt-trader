"""coverage_utils — unified gap-fill helper used by the API and backtest runner."""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coordinator.services.coverage_index import CoverageIndex
    from coordinator.services.download_manager import DownloadManager


async def ensure_coverage(
    provider: str,
    symbol: str,
    start: date,
    end: date,
    download_manager: "DownloadManager",
    coverage_index: "CoverageIndex",
    timeframe: str = "1min",
) -> list[str]:
    """Download only what's missing for (provider, symbol) in [start, end].

    For each gap found by the coverage index, expands the download window by
    one day on each edge (overlap for reconciliation with adjacent bars), then
    submits a DownloadManager job.

    Returns the list of download IDs that were created (empty when fully covered).
    Invalidates the coverage cache after submitting so the next call re-scans disk.
    """
    gaps = coverage_index.get_gaps(provider, symbol, start, end)
    download_ids: list[str] = []

    for gap_start, gap_end in gaps:
        dl_start = gap_start - timedelta(days=1)
        dl_end = gap_end + timedelta(days=1)
        dl = await download_manager.create_download(
            symbols=[symbol],
            date_range_start=dl_start,
            date_range_end=dl_end,
            provider=provider,
            timeframe=timeframe,
        )
        download_ids.append(dl["id"])

    # Invalidate so the next get_ranges re-scans disk after downloads land.
    coverage_index.invalidate(provider, symbol)
    return download_ids
