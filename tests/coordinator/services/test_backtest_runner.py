"""Tests for BacktestRunner — Spec D one-shot orchestrator."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, date, timezone

import pandas as pd

from coordinator.api.dependencies import get_container
from coordinator.services.backtest_runner import BacktestRunner


@pytest.mark.asyncio
async def test_runner_creates_row_and_advances_status(test_app, db_session):
    """End-to-end with mocked engine: queued -> downloading_data -> running -> completed."""
    from coordinator.database.models import Algorithm, BacktestRun
    # Use a GitHub-shaped URL so the runner's _package_dir_name helper parses it.
    algo = Algorithm(name="test-algo", repo_url="https://github.com/example/test-algo",
                     install_status="installed")
    db_session.add(algo); await db_session.flush()

    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        initial_cash=10_000.0,
    )
    db_session.add(run); await db_session.commit()

    # Mock everything that's NOT the runner itself.
    with patch("coordinator.services.backtest_runner._load_manifest") as load_manifest, \
         patch("coordinator.services.coverage_utils.ensure_coverage", new_callable=AsyncMock, return_value=[]) as mock_ensure, \
         patch("coordinator.services.backtest_runner._load_bar_series") as load_bars, \
         patch("coordinator.services.backtest_runner._load_algorithm_class") as load_class, \
         patch("coordinator.services.backtest_runner.BacktestEngine") as mock_engine_cls:
        load_manifest.return_value = MagicMock(
            requirements=MagicMock(data_dependencies=[
                {"symbol": "SPY", "timeframe": "1day", "source": "polygon"},
            ]),
        )
        # Engine immediately calls observer.on_complete
        def fake_engine_run(**kwargs):
            obs = kwargs["observer"]
            from coordinator.services.backtest_engine_v2 import EngineSummary
            obs.on_equity_point(datetime(2024, 1, 1, tzinfo=timezone.utc), 10_000.0, 10_000.0, [])
            obs.on_complete(EngineSummary(total_bars=10, total_signals=0, total_fills=0,
                                          final_cash=10_000.0, final_portfolio_value=10_000.0))
        mock_engine_cls.return_value.run = fake_engine_run
        load_bars.return_value = MagicMock(empty=False)
        load_class.return_value = MagicMock  # returns the class, instantiation happens inside runner

        container = get_container()
        mock_coverage_index = MagicMock()
        runner = BacktestRunner(
            session_factory=container.session_factory,
            download_manager=MagicMock(),
            data_service=MagicMock(),
            coverage_index=mock_coverage_index,
        )
        await runner.run(run.id)

    from sqlalchemy import select
    refreshed = (await db_session.execute(
        select(BacktestRun).where(BacktestRun.id == run.id)
    )).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "completed"


# ---- on_miss / auto-download helpers ----

def _make_runner_stubs():
    """Return a BacktestRunner with fully-mocked collaborators."""
    mock_dm = MagicMock()
    mock_ds = MagicMock()
    return BacktestRunner(
        session_factory=MagicMock(),
        download_manager=mock_dm,
        data_service=mock_ds,
    ), mock_dm, mock_ds


@pytest.mark.asyncio
async def test_make_on_miss_returns_disk_data_without_download():
    """If data already lives on disk, on_miss skips the download."""
    runner, mock_dm, mock_ds = _make_runner_stubs()

    disk_df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=5, freq="D"),
        "open": [1.0] * 5, "high": [1.0] * 5, "low": [1.0] * 5,
        "close": [1.0] * 5, "volume": [100] * 5,
    })
    mock_ds.load_market_data.return_value = disk_df

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, tzinfo=timezone.utc)
    # _make_on_miss must be called from an async context (it captures the running loop).
    on_miss = runner._make_on_miss(start, end)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, on_miss, "SPY", "1day", "polygon")

    # download_manager must NOT be touched — disk already had data
    mock_dm.create_download.assert_not_called()
    assert result is disk_df


@pytest.mark.asyncio
async def test_download_and_wait_creates_and_polls_download():
    """_download_and_wait should call create_download then _wait_for_download."""
    runner, mock_dm, mock_ds = _make_runner_stubs()

    mock_dm.create_download = AsyncMock(return_value={"id": "dl-abc"})

    with patch.object(runner, "_wait_for_download", new_callable=AsyncMock) as mock_wait:
        await runner._download_and_wait(
            "AAPL", "1day", "polygon",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 31, tzinfo=timezone.utc),
        )

    mock_dm.create_download.assert_awaited_once_with(
        symbols=["AAPL"],
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        provider="polygon",
        timeframe="1day",
    )
    mock_wait.assert_awaited_once_with("dl-abc")


@pytest.mark.asyncio
async def test_make_on_miss_downloads_when_not_on_disk():
    """on_miss should trigger _download_and_wait when disk returns None/empty,
    then re-read from disk and return the result."""
    runner, mock_dm, mock_ds = _make_runner_stubs()

    disk_df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=5, freq="D"),
        "open": [1.0] * 5, "high": [1.0] * 5, "low": [1.0] * 5,
        "close": [1.0] * 5, "volume": [100] * 5,
    })
    # First call returns None (not yet on disk), second call (after download) returns df
    mock_ds.load_market_data.side_effect = [None, disk_df]

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    on_miss = runner._make_on_miss(start, end)

    # The on_miss callback is sync but internally uses run_coroutine_threadsafe.
    # In the test environment we run it from inside an async context — set up a
    # running loop so run_coroutine_threadsafe works correctly.
    loop = asyncio.get_event_loop()

    downloaded = []

    async def fake_download_and_wait(symbol, timeframe, source, s, e):
        downloaded.append((symbol, timeframe, source))

    with patch.object(runner, "_download_and_wait", side_effect=fake_download_and_wait):
        # Run on_miss in a thread (mirrors real engine executor usage)
        result = await loop.run_in_executor(None, on_miss, "TSLA", "1day", "polygon")

    assert len(downloaded) == 1
    assert downloaded[0] == ("TSLA", "1day", "polygon")
    assert result is disk_df
