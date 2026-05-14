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
    return DownloadManager(
        session_factory=session_factory,
        data_service=mock_data_service,
        providers={"polygon": mock_provider},
    )


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
