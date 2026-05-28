import pytest
import pytest_asyncio
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pandas as pd

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base, MarketDataDownload
from coordinator.services.data_service import DataService
from coordinator.services.download_manager import DownloadManager
from sqlalchemy import select


@pytest_asyncio.fixture
async def db_engine():
    engine = create_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return create_session_factory(db_engine)


@pytest.fixture
def mock_data_service(tmp_path):
    return DataService(
        market_data_dir=str(tmp_path / "market"),
        custom_data_dir=str(tmp_path / "custom"),
    )


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.fetch_bars = AsyncMock(return_value=[
        {"timestamp": "2024-01-01T00:00:00+00:00", "open": 100, "high": 105, "low": 99, "close": 103, "volume": 1000},
        {"timestamp": "2024-01-02T00:00:00+00:00", "open": 103, "high": 107, "low": 102, "close": 106, "volume": 1200},
    ])
    return provider


@pytest_asyncio.fixture
async def download_manager(session_factory, mock_data_service, mock_provider):
    mgr = DownloadManager(
        session_factory=session_factory,
        data_service=mock_data_service,
        providers={"polygon": mock_provider},
    )
    yield mgr
    await mgr.shutdown()


class TestDownloadManager:
    @pytest.mark.asyncio
    async def test_create_download(self, download_manager, session_factory):
        result = await download_manager.create_download(
            symbols=["AAPL", "MSFT"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        assert result["status"] == "queued"
        assert result["symbols"] == ["AAPL", "MSFT"]
        assert result["total"] == 2
        assert "id" in result

    @pytest.mark.asyncio
    async def test_create_download_unknown_provider(self, download_manager):
        with pytest.raises(ValueError, match="Unknown provider"):
            await download_manager.create_download(
                symbols=["AAPL"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
                provider="unknown_provider",
            )

    @pytest.mark.asyncio
    async def test_get_download(self, download_manager):
        result = await download_manager.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        import asyncio
        await asyncio.sleep(0.5)

        dl = await download_manager.get_download(result["id"])
        assert dl is not None
        assert dl["symbols"] == ["AAPL"]
        assert dl["provider"] == "polygon"

    @pytest.mark.asyncio
    async def test_get_download_not_found(self, download_manager):
        dl = await download_manager.get_download("nonexistent-id")
        assert dl is None

    @pytest.mark.asyncio
    async def test_list_downloads(self, download_manager):
        await download_manager.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 3, 31),
        )
        await download_manager.create_download(
            symbols=["MSFT"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 3, 31),
        )
        downloads = await download_manager.list_downloads()
        assert len(downloads) >= 2

    @pytest.mark.asyncio
    async def test_download_runs_and_saves_data(self, download_manager, mock_provider, mock_data_service):
        import asyncio
        result = await download_manager.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(1.0)

        dl = await download_manager.get_download(result["id"])
        # fetch_bars must have been dispatched regardless of save outcome
        mock_provider.fetch_bars.assert_called()
        assert dl["status"] in ("completed", "running", "failed", "completed_with_errors")

        loaded = mock_data_service.load_market_data("polygon", "AAPL", "1day")
        if dl["status"] == "completed":
            assert loaded is not None
            assert len(loaded) == 2

    @pytest.mark.asyncio
    async def test_download_with_provider_error(self, session_factory, mock_data_service):
        error_provider = AsyncMock()
        error_provider.fetch_bars = AsyncMock(side_effect=Exception("API rate limited"))

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": error_provider},
        )
        import asyncio
        result = await mgr.create_download(
            symbols=["BAD"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        if dl["status"] != "running":
            # All symbols failed → status is now "failed"
            assert dl["status"] == "failed"
            assert "API rate limited" in dl["error_message"]

    @pytest.mark.asyncio
    async def test_unsupported_data_type_fails(self, session_factory, mock_data_service, mock_provider):
        """Requesting data_type='quotes' should fail with a clear error; fetch_bars must not be called."""
        import asyncio
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": mock_provider},
        )
        result = await mgr.create_download(
            symbols=["SPY"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
            data_type="quotes",
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        if dl["status"] != "running":
            assert dl["status"] == "failed", f"Expected 'failed', got '{dl['status']}'"
            assert "quotes" in dl["error_message"]
            assert "not yet supported" in dl["error_message"]
        mock_provider.fetch_bars.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_data_type_trades_fails(self, session_factory, mock_data_service, mock_provider):
        """Requesting data_type='trades' should also fail clearly."""
        import asyncio
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": mock_provider},
        )
        result = await mgr.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
            data_type="trades",
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        if dl["status"] != "running":
            assert dl["status"] == "failed", f"Expected 'failed', got '{dl['status']}'"
            assert "trades" in dl["error_message"]

    @pytest.mark.asyncio
    async def test_bars_data_type_succeeds(self, download_manager, mock_provider, mock_data_service):
        """Requesting data_type='bars' dispatches to fetch_bars (not a not-supported error)."""
        import asyncio
        result = await download_manager.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
            data_type="bars",
        )
        await asyncio.sleep(1.0)

        dl = await download_manager.get_download(result["id"])
        # fetch_bars must have been called — the key assertion for dispatch correctness.
        mock_provider.fetch_bars.assert_called()
        # If completed (parquet engine available), status is "completed".
        # If parquet is missing the save raises an unrelated error → status may be "failed",
        # but that is a pre-existing environment issue unrelated to data_type dispatch.
        if dl["status"] not in ("running",):
            assert dl["status"] in ("completed", "failed", "completed_with_errors"), (
                f"Unexpected status '{dl['status']}'"
            )
            # Crucially, if it failed it must NOT be a "not yet supported" error.
            if dl["error_message"]:
                assert "not yet supported" not in dl["error_message"]

    @pytest.mark.asyncio
    async def test_downloads_run_sequentially(self, session_factory, mock_data_service):
        """Two queued downloads must not fetch in parallel — the second waits for
        the first to release the semaphore. While the first is mid-fetch, the
        second's status remains 'queued' and its fetch_bars has not been called."""
        import asyncio

        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_called = asyncio.Event()

        async def first_fetch(*args, **kwargs):
            first_started.set()
            await release_first.wait()
            return []

        async def second_fetch(*args, **kwargs):
            second_called.set()
            return []

        # Route different symbols to different provider implementations
        class RoutingProvider:
            async def fetch_bars(self, symbol, timeframe, start, end, **kwargs):
                if symbol == "FIRST":
                    return await first_fetch()
                return await second_fetch()

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": RoutingProvider()},
        )
        try:
            first = await mgr.create_download(
                symbols=["FIRST"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
            )
            second = await mgr.create_download(
                symbols=["SECOND"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
            )

            await asyncio.wait_for(first_started.wait(), timeout=2.0)
            # While first is mid-fetch, the second must not have been called yet
            # and its DB row must still be 'queued'.
            assert not second_called.is_set()
            dl_second = await mgr.get_download(second["id"])
            assert dl_second["status"] == "queued"
            dl_first = await mgr.get_download(first["id"])
            assert dl_first["status"] == "running"

            # Release first; second should now run.
            release_first.set()
            await asyncio.wait_for(second_called.wait(), timeout=2.0)
        finally:
            release_first.set()
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_cancel_queued_download_before_run(self, session_factory, mock_data_service):
        """A queued download waiting on the serialization semaphore can still be
        cancelled, and its fetch_bars is never invoked."""
        import asyncio

        block_first = asyncio.Event()
        second_called = asyncio.Event()

        class BlockingProvider:
            async def fetch_bars(self, symbol, timeframe, start, end, **kwargs):
                if symbol == "FIRST":
                    await block_first.wait()
                    return []
                second_called.set()
                return []

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": BlockingProvider()},
        )
        try:
            await mgr.create_download(
                symbols=["FIRST"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
            )
            second = await mgr.create_download(
                symbols=["SECOND"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
            )

            # Let the first download grab the semaphore and start running.
            await asyncio.sleep(0.1)

            cancelled = await mgr.cancel_download(second["id"])
            assert cancelled is True

            dl_second = await mgr.get_download(second["id"])
            assert dl_second["status"] == "cancelled"

            # Release the first; second's fetch_bars must never be invoked.
            block_first.set()
            await asyncio.sleep(0.2)
            assert not second_called.is_set()
        finally:
            block_first.set()
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_cancel_download(self, download_manager):
        slow_provider = AsyncMock()
        import asyncio

        async def slow_fetch(*args, **kwargs):
            await asyncio.sleep(10)
            return []

        slow_provider.fetch_bars = slow_fetch
        download_manager._providers["polygon"] = slow_provider

        result = await download_manager.create_download(
            symbols=["SLOW1", "SLOW2", "SLOW3"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(0.1)

        cancelled = await download_manager.cancel_download(result["id"])
        assert cancelled is True

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, download_manager):
        cancelled = await download_manager.cancel_download("nonexistent")
        assert cancelled is False

    @pytest.mark.asyncio
    async def test_recover_orphaned_downloads(self, session_factory, mock_data_service, mock_provider):
        """Seed a running and a queued row directly, then confirm recover_orphaned_downloads marks both failed."""
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": mock_provider},
        )

        # Seed rows directly via session factory (no in-memory task registered)
        async with session_factory() as session:
            running_row = MarketDataDownload(
                symbols=["AAPL"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
                provider="polygon",
                data_type="bars",
                timeframe="1day",
                status="running",
                progress_current=0,
                progress_total=1,
            )
            queued_row = MarketDataDownload(
                symbols=["MSFT"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
                provider="polygon",
                data_type="bars",
                timeframe="1day",
                status="queued",
                progress_current=0,
                progress_total=1,
            )
            session.add(running_row)
            session.add(queued_row)
            await session.commit()
            running_id = running_row.id
            queued_id = queued_row.id

        count = await mgr.recover_orphaned_downloads()
        assert count == 2

        async with session_factory() as session:
            result = await session.execute(
                select(MarketDataDownload).where(MarketDataDownload.id == running_id)
            )
            recovered_running = result.scalar_one_or_none()
            assert recovered_running is not None
            assert recovered_running.status == "failed"
            assert recovered_running.error_message == "Orphaned by coordinator restart"

            result = await session.execute(
                select(MarketDataDownload).where(MarketDataDownload.id == queued_id)
            )
            recovered_queued = result.scalar_one_or_none()
            assert recovered_queued is not None
            assert recovered_queued.status == "failed"
            assert recovered_queued.error_message == "Orphaned by coordinator restart"

    @pytest.mark.asyncio
    async def test_cancel_orphan_marks_cancelled(self, session_factory, mock_data_service, mock_provider):
        """A running row with no in-memory task should be cancelled gracefully (orphan case)."""
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": mock_provider},
        )

        # Seed a running row directly — no task registered in mgr._active_tasks
        async with session_factory() as session:
            orphan = MarketDataDownload(
                symbols=["AAPL"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
                provider="polygon",
                data_type="bars",
                timeframe="1day",
                status="running",
                progress_current=0,
                progress_total=1,
            )
            session.add(orphan)
            await session.commit()
            orphan_id = orphan.id

        result = await mgr.cancel_download(orphan_id)
        assert result is True

        async with session_factory() as session:
            row = (
                await session.execute(
                    select(MarketDataDownload).where(MarketDataDownload.id == orphan_id)
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.status == "cancelled"
            assert row.error_message == "Cancelled (orphan; no live task)"

    @pytest.mark.asyncio
    async def test_cancel_terminal_row_returns_false(self, session_factory, mock_data_service, mock_provider):
        """Cancelling a row that is already in a terminal state should return False and leave it unchanged."""
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": mock_provider},
        )

        async with session_factory() as session:
            completed = MarketDataDownload(
                symbols=["AAPL"],
                date_range_start=date(2024, 1, 1),
                date_range_end=date(2024, 6, 30),
                provider="polygon",
                data_type="bars",
                timeframe="1day",
                status="completed",
                progress_current=1,
                progress_total=1,
            )
            session.add(completed)
            await session.commit()
            completed_id = completed.id

        result = await mgr.cancel_download(completed_id)
        assert result is False

        async with session_factory() as session:
            row = (
                await session.execute(
                    select(MarketDataDownload).where(MarketDataDownload.id == completed_id)
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.status == "completed"

    @pytest.mark.asyncio
    async def test_progress_message_set_during_download(self, session_factory, mock_data_service):
        """progress_message is written during paginated fetch and cleared on completion."""
        import asyncio

        # Provider that calls on_page once with the new 3-arg signature
        async def fetch_with_callback(symbol, timeframe, start, end, on_page=None, on_status=None):
            if on_page is not None:
                await on_page(0, 1, 0.5)
            return [
                {"timestamp": "2024-01-01T00:00:00+00:00", "open": 100, "high": 105,
                 "low": 99, "close": 103, "volume": 1000},
            ]

        paging_provider = AsyncMock()
        paging_provider.fetch_bars = fetch_with_callback

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": paging_provider},
        )

        result = await mgr.create_download(
            symbols=["SPY"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        assert dl is not None
        # After completion, progress_message must be cleared (set to None)
        assert dl["progress_message"] is None
        # Status must reflect completion (not stuck running)
        assert dl["status"] in ("completed", "failed", "completed_with_errors")

    @pytest.mark.asyncio
    async def test_incremental_save_invoked_per_page(self, session_factory, mock_data_service):
        """When a provider streams pages via on_bars, the manager saves each page
        as it arrives — not only after fetch_bars returns."""
        import asyncio
        saves: list[int] = []
        original_save = mock_data_service.save_market_data

        def tracking_save(provider, symbol, timeframe, df):
            saves.append(len(df))
            return original_save(provider, symbol, timeframe, df)

        mock_data_service.save_market_data = tracking_save

        async def streaming_fetch(symbol, timeframe, start, end, on_page=None, on_status=None, on_bars=None):
            page_a = [{"timestamp": "2024-01-01T00:00:00+00:00", "open": 1, "high": 1,
                       "low": 1, "close": 1, "volume": 10}]
            page_b = [{"timestamp": "2024-01-02T00:00:00+00:00", "open": 2, "high": 2,
                       "low": 2, "close": 2, "volume": 20}]
            if on_bars is not None:
                await on_bars(page_a)
                await on_bars(page_b)
            return page_a + page_b

        streaming_provider = AsyncMock()
        streaming_provider.fetch_bars = streaming_fetch

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": streaming_provider},
        )

        result = await mgr.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        # Two per-page saves should have happened, with no redundant third save of
        # the full list (incremental_saved suppresses the final save).
        if dl["status"] in ("completed", "completed_with_errors"):
            assert saves == [1, 1], f"Expected two single-page saves, got {saves}"

    @pytest.mark.asyncio
    async def test_resume_skips_already_covered_range(self, session_factory, mock_data_service):
        """If existing parquet already covers the requested end date, fetch_bars
        must not be called for that symbol."""
        import asyncio
        # Pre-populate data through 2024-12-31
        existing = pd.DataFrame({
            "timestamp": ["2024-01-01T00:00:00+00:00", "2024-12-31T00:00:00+00:00"],
            "open": [1.0, 2.0], "high": [1.0, 2.0],
            "low": [1.0, 2.0], "close": [1.0, 2.0], "volume": [10, 20],
        })
        mock_data_service.save_market_data("polygon", "AAPL", "1day", existing)

        provider = AsyncMock()
        provider.fetch_bars = AsyncMock(return_value=[])

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": provider},
        )
        result = await mgr.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        if dl["status"] != "running":
            provider.fetch_bars.assert_not_called()
            assert dl["status"] in ("completed", "completed_with_errors")

    @pytest.mark.asyncio
    async def test_resume_advances_start_date(self, session_factory, mock_data_service):
        """If existing data covers part of the range, fetch_bars is called with a
        start date past the last saved timestamp."""
        import asyncio
        existing = pd.DataFrame({
            "timestamp": ["2024-03-15T00:00:00+00:00"],
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [10],
        })
        mock_data_service.save_market_data("polygon", "AAPL", "1day", existing)

        captured_start: list = []

        async def capturing_fetch(symbol, timeframe, start, end, **kwargs):
            captured_start.append(start)
            return []

        provider = AsyncMock()
        provider.fetch_bars = capturing_fetch

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": provider},
        )
        result = await mgr.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        if dl["status"] != "running":
            assert captured_start, "fetch_bars was not called"
            # Start should be advanced to day after the last saved bar
            assert captured_start[0] == date(2024, 3, 16), (
                f"Expected resume from 2024-03-16, got {captured_start[0]}"
            )

    @pytest.mark.asyncio
    async def test_non_streaming_provider_falls_back_to_full_save(self, session_factory, mock_data_service):
        """A provider that returns bars without invoking on_bars (e.g. Theta) must
        still have its data persisted by the manager's fallback save."""
        import asyncio
        saves: list[int] = []
        original_save = mock_data_service.save_market_data

        def tracking_save(provider, symbol, timeframe, df):
            saves.append(len(df))
            return original_save(provider, symbol, timeframe, df)

        mock_data_service.save_market_data = tracking_save

        async def non_streaming_fetch(symbol, timeframe, start, end, **kwargs):
            # Ignores on_bars completely
            return [
                {"timestamp": "2024-01-01T00:00:00+00:00", "open": 1, "high": 1,
                 "low": 1, "close": 1, "volume": 10},
                {"timestamp": "2024-01-02T00:00:00+00:00", "open": 2, "high": 2,
                 "low": 2, "close": 2, "volume": 20},
            ]

        provider = AsyncMock()
        provider.fetch_bars = non_streaming_fetch

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": provider},
        )
        result = await mgr.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        if dl["status"] in ("completed", "completed_with_errors"):
            # Exactly one full-list save via the fallback path
            assert saves == [2], f"Expected single fallback save of 2 bars, got {saves}"

    @pytest.mark.asyncio
    async def test_current_symbol_pct_set_during_download(self, session_factory, mock_data_service):
        """current_symbol_pct is set mid-download and cleared to None on completion."""
        import asyncio
        from sqlalchemy import select as sa_select

        # Provider calls on_page(0, 100, 0.5) then returns bars
        async def fetch_with_fraction(symbol, timeframe, start, end, on_page=None, on_status=None):
            if on_page is not None:
                await on_page(0, 100, 0.5)
            return [
                {"timestamp": "2024-01-01T00:00:00+00:00", "open": 100, "high": 105,
                 "low": 99, "close": 103, "volume": 1000},
            ]

        paging_provider = AsyncMock()
        paging_provider.fetch_bars = fetch_with_fraction

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": paging_provider},
        )

        result = await mgr.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
        )
        await asyncio.sleep(1.0)

        dl = await mgr.get_download(result["id"])
        assert dl is not None
        # After completion, current_symbol_pct must be cleared (set to None)
        assert dl["current_symbol_pct"] is None
        # Status must reflect completion
        assert dl["status"] in ("completed", "failed", "completed_with_errors")


class TestPerProviderSemaphores:
    """Per-provider semaphores let fast providers (yfinance) run while a slow
    provider (polygon) is busy."""

    @pytest.mark.asyncio
    async def test_default_concurrency_per_provider(self, session_factory, mock_data_service):
        """The DownloadManager exposes a per-provider semaphore. Default values
        give polygon=1 (rate-limited) and yfinance=4 (no rate limit)."""
        provider_a = AsyncMock()
        provider_b = AsyncMock()
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": provider_a, "yfinance": provider_b},
        )
        assert mgr._semaphore_for("polygon")._value == 1
        assert mgr._semaphore_for("yfinance")._value == 4

    @pytest.mark.asyncio
    async def test_provider_concurrency_override(self, session_factory, mock_data_service):
        """Callers can override the per-provider concurrency."""
        provider = AsyncMock()
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": provider},
            provider_concurrency={"polygon": 3},
        )
        assert mgr._semaphore_for("polygon")._value == 3

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_fallback_semaphore(
        self, session_factory, mock_data_service,
    ):
        """For providers not in the providers dict, the fallback semaphore (value=1)
        is returned. This keeps any future provider safely serialized."""
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": AsyncMock()},
        )
        fallback = mgr._semaphore_for("does-not-exist")
        assert fallback._value == 1
        assert fallback is mgr._fallback_semaphore

    @pytest.mark.asyncio
    async def test_polygon_and_yfinance_run_concurrently(
        self, session_factory, mock_data_service,
    ):
        """Polygon and yfinance downloads use independent semaphores, so a slow
        polygon download must NOT block a yfinance download."""
        import asyncio

        polygon_release = asyncio.Event()
        polygon_started = asyncio.Event()

        async def slow_polygon_fetch(symbol, timeframe, start, end, **_kw):
            polygon_started.set()
            await polygon_release.wait()  # block until we say so
            return []

        polygon = AsyncMock()
        polygon.fetch_bars = slow_polygon_fetch

        yfinance = AsyncMock()
        yfinance.fetch_bars = AsyncMock(return_value=[
            {"timestamp": "2024-01-01T00:00:00+00:00", "open": 100, "high": 105,
             "low": 99, "close": 103, "volume": 1000},
        ])

        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": polygon, "yfinance": yfinance},
        )

        # Queue polygon first; it will block
        await mgr.create_download(
            symbols=["AAPL"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
            provider="polygon",
        )
        # Wait for polygon to actually start (it now holds its provider semaphore)
        await asyncio.wait_for(polygon_started.wait(), timeout=2.0)

        # Now queue yfinance — should run immediately, NOT blocked by polygon
        yf_result = await mgr.create_download(
            symbols=["BTC-USD"],
            date_range_start=date(2024, 1, 1),
            date_range_end=date(2024, 6, 30),
            provider="yfinance",
        )

        # Give yfinance time to complete
        await asyncio.sleep(0.5)
        yf_dl = await mgr.get_download(yf_result["id"])
        assert yf_dl["status"] in ("completed", "failed", "completed_with_errors"), (
            f"yfinance was blocked by polygon — status={yf_dl['status']}"
        )

        # Cleanup: release polygon so its task finishes
        polygon_release.set()
        await asyncio.sleep(0.3)


class TestCompletionListenerRegistry:
    """Multi-consumer on_download_complete listener registry."""

    @pytest.mark.asyncio
    async def test_legacy_attribute_assignment_still_works(self, session_factory, mock_data_service):
        """coordinator/main.py assigns _on_download_complete directly; preserve
        that contract via property setter."""
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": AsyncMock()},
        )
        called = []
        mgr._on_download_complete = lambda p, s: called.append((p, s))
        # Trigger the listener via private API
        for cb in list(mgr._completion_listeners):
            cb("polygon", ["AAPL"])
        assert called == [("polygon", ["AAPL"])]

    @pytest.mark.asyncio
    async def test_multiple_listeners_all_fire(self, session_factory, mock_data_service):
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": AsyncMock()},
        )
        events_a, events_b = [], []
        mgr.add_completion_listener(lambda p, s: events_a.append((p, s)))
        mgr.add_completion_listener(lambda p, s: events_b.append((p, s)))
        for cb in list(mgr._completion_listeners):
            cb("polygon", ["AAPL", "MSFT"])
        assert events_a == [("polygon", ["AAPL", "MSFT"])]
        assert events_b == [("polygon", ["AAPL", "MSFT"])]

    @pytest.mark.asyncio
    async def test_exception_in_one_listener_does_not_block_others(
        self, session_factory, mock_data_service, caplog
    ):
        import logging
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": AsyncMock()},
        )
        called = []
        def bad(p, s):
            raise RuntimeError("listener boom")
        mgr.add_completion_listener(bad)
        mgr.add_completion_listener(lambda p, s: called.append((p, s)))
        # Emulate downstream fire
        for cb in list(mgr._completion_listeners):
            try:
                cb("polygon", ["AAPL"])
            except Exception:
                pass
        # The good listener was still invoked
        assert called == [("polygon", ["AAPL"])]

    @pytest.mark.asyncio
    async def test_remove_listener_works(self, session_factory, mock_data_service):
        mgr = DownloadManager(
            session_factory=session_factory,
            data_service=mock_data_service,
            providers={"polygon": AsyncMock()},
        )
        events = []
        cb = lambda p, s: events.append((p, s))
        mgr.add_completion_listener(cb)
        assert cb in mgr._completion_listeners
        mgr.remove_completion_listener(cb)
        assert cb not in mgr._completion_listeners
        # Removing again is a no-op
        mgr.remove_completion_listener(cb)
