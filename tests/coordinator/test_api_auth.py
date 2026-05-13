import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from coordinator.api.auth import (
    generate_api_token,
    hash_token,
    verify_token,
    APIAuthMiddleware,
)
from coordinator.main import create_app


class TestTokenFunctions:
    def test_generate_token_length(self):
        token = generate_api_token()
        assert len(token) > 20

    def test_generate_unique_tokens(self):
        t1 = generate_api_token()
        t2 = generate_api_token()
        assert t1 != t2

    def test_hash_token_deterministic(self):
        token = "test-token"
        h1 = hash_token(token)
        h2 = hash_token(token)
        assert h1 == h2

    def test_verify_token_correct(self):
        token = "my-secret-token"
        hashed = hash_token(token)
        assert verify_token(token, hashed) is True

    def test_verify_token_incorrect(self):
        hashed = hash_token("correct-token")
        assert verify_token("wrong-token", hashed) is False


class TestAPIAuthMiddleware:
    @pytest_asyncio.fixture
    async def protected_app(self):
        app = create_app(database_url="sqlite+aiosqlite://", encryption_key="test-key-32-bytes-long!!!!!!!!")
        token = "test-api-token-12345"
        app.add_middleware(APIAuthMiddleware, token_hash=hash_token(token))
        async with app.router.lifespan_context(app):
            yield app, token

    @pytest_asyncio.fixture
    async def protected_client(self, protected_app):
        app, token = protected_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, token

    @pytest.mark.asyncio
    async def test_health_exempt(self, protected_client):
        client, _ = protected_client
        resp = await client.get("/api/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_api_requires_auth(self, protected_client):
        client, _ = protected_client
        resp = await client.get("/api/accounts")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_token_works(self, protected_client):
        client, token = protected_client
        resp = await client.get(
            "/api/accounts",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_query_param_works(self, protected_client):
        client, token = protected_client
        resp = await client.get(f"/api/accounts?api_key={token}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_token_rejected(self, protected_client):
        client, _ = protected_client
        resp = await client.get(
            "/api/accounts",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_when_disabled(self):
        app = create_app(database_url="sqlite+aiosqlite://", encryption_key="test-key-32-bytes-long!!!!!!!!")
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/accounts")
                assert resp.status_code == 200
