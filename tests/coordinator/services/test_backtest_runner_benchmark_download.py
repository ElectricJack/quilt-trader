import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest


@pytest.mark.asyncio
async def test_runner_triggers_download_when_benchmark_missing(monkeypatch):
    """When the benchmark parquet is missing, the runner should call
    _download_and_wait and re-read before falling back to no-benchmark."""
    from coordinator.services.backtest_runner import BacktestRunner

    ds = MagicMock()
    # First call returns empty df (missing); after "download", returns real bars.
    bars = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=5),
                         "open": [1]*5, "high": [1]*5, "low": [1]*5,
                         "close": [1]*5, "volume": [1]*5})
    ds.load_market_data.side_effect = [pd.DataFrame(), bars]

    dm = MagicMock()
    dm.create_download = AsyncMock(return_value={"id": "dl-1"})

    runner = BacktestRunner(session_factory=MagicMock(), download_manager=dm,
                            data_service=ds)
    runner._wait_for_download = AsyncMock()

    await runner._download_and_wait(
        symbol="SPY", timeframe="1day", source="yfinance",
        start=date(2024, 1, 1), end=date(2024, 1, 10),
    )

    dm.create_download.assert_called_once()
    args = dm.create_download.call_args.kwargs
    assert args["symbols"] == ["SPY"]
    assert args["provider"] == "yfinance"
    assert args["timeframe"] == "1day"


@pytest.mark.asyncio
async def test_runner_benchmark_load_uses_download_and_retry(monkeypatch):
    """The benchmark block inside BacktestRunner.run should: try load,
    on empty/None, call _download_and_wait, retry the load, and use the result."""
    from coordinator.services import backtest_runner as br_mod

    ds = MagicMock()
    bars = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=3),
                         "open": [1]*3, "high": [1]*3, "low": [1]*3,
                         "close": [1]*3, "volume": [1]*3})
    ds.load_market_data.side_effect = [pd.DataFrame(), bars]
    downloader = AsyncMock()

    bdf = await br_mod._load_benchmark_with_download(
        ds=ds, source="yfinance", symbol="SPY",
        date_range_start=pd.Timestamp("2024-01-01"),
        date_range_end=pd.Timestamp("2024-01-10"),
        downloader=downloader,
    )
    downloader.assert_called_once()
    assert bdf is not None
    assert len(bdf) == 3


@pytest.mark.asyncio
async def test_runner_benchmark_load_returns_none_when_download_fails():
    from coordinator.services import backtest_runner as br_mod

    ds = MagicMock()
    ds.load_market_data.return_value = pd.DataFrame()  # always empty
    downloader = AsyncMock()

    bdf = await br_mod._load_benchmark_with_download(
        ds=ds, source="yfinance", symbol="SPY",
        date_range_start=pd.Timestamp("2024-01-01"),
        date_range_end=pd.Timestamp("2024-01-10"),
        downloader=downloader,
    )
    downloader.assert_called_once()
    assert bdf is None
