import pytest
import pytest_asyncio
from datetime import date
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from coordinator.main import create_app
from coordinator.database.models import MarketDataDownload
from coordinator.api.dependencies import get_container


@pytest_asyncio.fixture
async def app():
    app = create_app(database_url="sqlite+aiosqlite://", encryption_key="test-key-32-bytes-long!!!!!!!!")
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db_session(app):
    container = get_container()
    async with container.session_factory() as session:
        yield session
        await session.rollback()


def _make_download(status: str, symbols: list[str] | None = None) -> MarketDataDownload:
    return MarketDataDownload(
        symbols=symbols or ["AAPL"],
        date_range_start=date(2024, 1, 1),
        date_range_end=date(2024, 6, 30),
        provider="polygon",
        data_type="bars",
        timeframe="1day",
        status=status,
        progress_current=0,
        progress_total=1,
    )


class TestDataAvailableEndpoint:
    @pytest.mark.asyncio
    async def test_list_available_data(self, client):
        resp = await client.get("/api/data/available")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestDownloadEndpoints:
    @pytest.mark.asyncio
    async def test_list_downloads_empty(self, client):
        resp = await client.get("/api/data/downloads")
        assert resp.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_get_download_not_found(self, client):
        resp = await client.get("/api/data/downloads/nonexistent")
        assert resp.status_code in (404, 503)


class TestDeleteDownload:
    @pytest.mark.asyncio
    async def test_delete_completed_download(self, client, db_session):
        """Seed a completed row, DELETE it, assert 204 and it's gone."""
        dl = _make_download("completed")
        db_session.add(dl)
        await db_session.commit()
        download_id = dl.id

        resp = await client.delete(f"/api/data/downloads/{download_id}")
        assert resp.status_code == 204

        # Confirm row is gone
        get_resp = await client.get(f"/api/data/downloads/{download_id}")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_active_download_returns_409(self, client, db_session):
        """Seed a running row, DELETE it, assert 409 and the row still exists."""
        dl = _make_download("running")
        db_session.add(dl)
        await db_session.commit()
        download_id = dl.id

        resp = await client.delete(f"/api/data/downloads/{download_id}")
        assert resp.status_code == 409
        body = resp.json()
        assert "Cancel" in body["detail"] or "cancel" in body["detail"]

        # Row must still exist
        get_resp = await client.get(f"/api/data/downloads/{download_id}")
        assert get_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_missing_returns_404(self, client):
        """DELETE on a nonexistent download_id returns 404."""
        resp = await client.delete("/api/data/downloads/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_queued_download_returns_409(self, client, db_session):
        """Queued downloads are also active and cannot be deleted."""
        dl = _make_download("queued")
        db_session.add(dl)
        await db_session.commit()
        download_id = dl.id

        resp = await client.delete(f"/api/data/downloads/{download_id}")
        assert resp.status_code == 409


class TestClearDownloads:
    @pytest.mark.asyncio
    async def test_clear_all_preserves_active_rows(self, client, db_session):
        """Seed 1 running + 2 completed + 1 failed; clear-all deletes 3, keeps running."""
        running_dl = _make_download("running", ["MSFT"])
        comp1 = _make_download("completed", ["AAPL"])
        comp2 = _make_download("completed", ["GOOG"])
        failed = _make_download("failed", ["TSLA"])
        for row in [running_dl, comp1, comp2, failed]:
            db_session.add(row)
        await db_session.commit()
        running_id = running_dl.id

        resp = await client.delete("/api/data/downloads")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 3

        # Running row must still be there
        get_resp = await client.get(f"/api/data/downloads/{running_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "running"

    @pytest.mark.asyncio
    async def test_clear_by_status_only_deletes_matching(self, client, db_session):
        """DELETE /downloads?status=failed only removes failed rows."""
        completed_dl = _make_download("completed", ["AAPL"])
        failed_dl = _make_download("failed", ["TSLA"])
        cancelled_dl = _make_download("cancelled", ["GOOG"])
        for row in [completed_dl, failed_dl, cancelled_dl]:
            db_session.add(row)
        await db_session.commit()
        completed_id = completed_dl.id
        failed_id = failed_dl.id
        cancelled_id = cancelled_dl.id

        resp = await client.delete("/api/data/downloads?status=failed")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

        # completed and cancelled must survive
        assert (await client.get(f"/api/data/downloads/{completed_id}")).status_code == 200
        assert (await client.get(f"/api/data/downloads/{cancelled_id}")).status_code == 200

        # failed must be gone
        assert (await client.get(f"/api/data/downloads/{failed_id}")).status_code == 404

    @pytest.mark.asyncio
    async def test_clear_by_multiple_statuses(self, client, db_session):
        """DELETE /downloads?status=failed,cancelled removes both."""
        completed_dl = _make_download("completed", ["AAPL"])
        failed_dl = _make_download("failed", ["TSLA"])
        cancelled_dl = _make_download("cancelled", ["GOOG"])
        for row in [completed_dl, failed_dl, cancelled_dl]:
            db_session.add(row)
        await db_session.commit()
        completed_id = completed_dl.id

        resp = await client.delete("/api/data/downloads?status=failed,cancelled")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2

        # completed must survive
        assert (await client.get(f"/api/data/downloads/{completed_id}")).status_code == 200

    @pytest.mark.asyncio
    async def test_clear_does_not_delete_active_even_if_status_param_matches(self, client, db_session):
        """Passing status=running to clear-all still skips active rows (safety guard)."""
        running_dl = _make_download("running", ["MSFT"])
        db_session.add(running_dl)
        await db_session.commit()
        running_id = running_dl.id

        resp = await client.delete("/api/data/downloads?status=running")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0

        # Row must still be there
        assert (await client.get(f"/api/data/downloads/{running_id}")).status_code == 200
