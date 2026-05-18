import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from coordinator.main import create_app


@pytest_asyncio.fixture
async def app():
    app = create_app(
        database_url="sqlite+aiosqlite://",
        encryption_key="test-key-32-bytes-long!!!!!!!!",
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_lifespan_runs_without_error():
    """App starts and shuts down cleanly when lifespan is used."""
    app = create_app(
        database_url="sqlite+aiosqlite://",
        encryption_key="test-key-32-bytes-long!!!!!!!!",
    )
    async with app.router.lifespan_context(app):
        pass  # lifespan startup and shutdown should not raise


@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_scheduler_in_container(app):
    from coordinator.api.dependencies import get_container

    container = get_container()
    assert container.scheduler is not None
    # Scheduler should expose list_jobs
    jobs = container.scheduler.list_jobs()
    assert isinstance(jobs, list)


@pytest.mark.asyncio
async def test_container_holds_session_factory(app):
    from coordinator.api.dependencies import get_container

    container = get_container()
    assert container.session_factory is not None


@pytest.mark.asyncio
async def test_container_holds_event_bus(app):
    from coordinator.api.dependencies import get_container

    container = get_container()
    assert container.event_bus is not None


@pytest.mark.asyncio
async def test_container_holds_encryption(app):
    from coordinator.api.dependencies import get_container

    container = get_container()
    assert container.encryption is not None


@pytest.mark.asyncio
async def test_live_feed_aggregator_wired_with_encryption(app):
    """Regression: aggregator must receive the EncryptionService, otherwise
    account credentials cannot be decrypted and broker adapters fail to build.
    """
    from coordinator.api.dependencies import get_container

    container = get_container()
    assert container.live_feed_aggregator is not None
    assert container.live_feed_aggregator._encryption is container.encryption


@pytest.mark.asyncio
async def test_live_feed_aggregator_has_coord_worker_id(app):
    """Lifespan must upsert a coord Worker row and pin its id onto the
    aggregator so that _emit_stream_event can write worker_activity rows.
    """
    from coordinator.api.dependencies import get_container

    container = get_container()
    assert container.live_feed_aggregator is not None
    assert container.live_feed_aggregator._coord_worker_id is not None
