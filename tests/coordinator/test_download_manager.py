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
        assert dl["status"] in ("completed", "running")
        mock_provider.fetch_bars.assert_called()

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
            assert dl["status"] == "completed_with_errors"
            assert "API rate limited" in dl["error_message"]

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
