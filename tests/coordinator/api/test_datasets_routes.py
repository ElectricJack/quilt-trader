"""Integration tests for /api/datasets/* endpoints.

Uses the ``test_app`` fixture from conftest.py (an ASGI FastAPI app backed by a
temporary SQLite database) and an in-process httpx AsyncClient.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# /api/datasets  — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_datasets(test_app):
    """GET /api/datasets returns a list of registered DatasetSpecs."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets")
    assert r.status_code == 200
    body = r.json()
    names = {d["name"] for d in body}
    assert "fmp.house_disclosures" in names


# ---------------------------------------------------------------------------
# /api/datasets/{name}  — one spec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dataset_detail(test_app):
    """GET /api/datasets/{name} returns the spec for a known dataset."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets/fmp.house_disclosures")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "fmp"
    assert body["pagination"] == "page"
    assert body["event_date_column"] == "transactionDate"


@pytest.mark.asyncio
async def test_get_dataset_unknown_returns_404(test_app):
    """GET /api/datasets/{name} returns 404 for an unknown dataset."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets/nope.nada")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/datasets/providers  — availability matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dataset_providers_returns_availability_matrix(test_app):
    """GET /api/datasets/providers returns provider entries with availability flags."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets/providers")
    assert r.status_code == 200
    body = r.json()
    assert any(p["name"] == "fmp" for p in body)
    # In a test environment fmp_api_key is not configured — should be unavailable
    fmp = next(p for p in body if p["name"] == "fmp")
    assert "available" in fmp
    assert "reason" in fmp


# ---------------------------------------------------------------------------
# /api/datasets/coverage  — index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_index(test_app):
    """GET /api/datasets/coverage returns an index entry per registered spec."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets/coverage")
    assert r.status_code == 200
    body = r.json()
    names = {entry["name"] for entry in body}
    assert "fmp.house_disclosures" in names
    # Each entry should have a detail_url
    entry = next(e for e in body if e["name"] == "fmp.house_disclosures")
    assert entry["detail_url"] == "/api/datasets/fmp.house_disclosures/coverage"


# ---------------------------------------------------------------------------
# /api/datasets/{name}/rows  — paginated row preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rows_endpoint_applies_as_of_filter(test_app, tmp_path):
    """GET /{name}/rows respects the as_of bitemporal filter."""
    from coordinator.services.datasets.registry import get
    from coordinator.services.datasets.storage import DatasetService, set_default_service

    spec = get("fmp.house_disclosures")

    # Wire a temp-path service so the test does not touch production data.
    svc = DatasetService(data_root=tmp_path)
    set_default_service(svc)

    await svc.upsert(spec, [
        {
            "transactionDate": "2024-01-01",
            "disclosureDate": "2024-02-01",
            "symbol": "A",
            "name": "X",
            "amount": "$1",
        },
        {
            "transactionDate": "2024-03-01",
            "disclosureDate": "2024-04-01",
            "symbol": "B",
            "name": "Y",
            "amount": "$2",
        },
    ])

    async with _client(test_app) as ac:
        r = await ac.get(
            "/api/datasets/fmp.house_disclosures/rows",
            params={"as_of": "2024-02-15"},
        )
    assert r.status_code == 200
    body = r.json()
    symbols = {row["symbol"] for row in body["rows"]}
    # Only the row known as of 2024-02-15 (disclosureDate=2024-02-01) should appear
    assert symbols == {"A"}
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# /api/datasets/downloads  — queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_download(test_app):
    """POST /api/datasets/downloads creates a queued row when adapter is present."""
    # Inject a stub adapter so the route does not reject the request.
    test_app.state.dataset_adapters = {"fmp": object()}

    async with _client(test_app) as ac:
        r = await ac.post(
            "/api/datasets/downloads",
            json={"name": "fmp.house_disclosures", "params": {}},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["dataset_name"] == "fmp.house_disclosures"
    assert body["id"] is not None


@pytest.mark.asyncio
async def test_queue_download_unknown_dataset_returns_404(test_app):
    """POST /api/datasets/downloads with unknown dataset name returns 404."""
    test_app.state.dataset_adapters = {"fmp": object()}

    async with _client(test_app) as ac:
        r = await ac.post(
            "/api/datasets/downloads",
            json={"name": "nope.nada", "params": {}},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_queue_download_unconfigured_provider_returns_400(test_app):
    """POST /api/datasets/downloads returns 400 when adapter is not configured."""
    test_app.state.dataset_adapters = {}  # fmp not configured

    async with _client(test_app) as ac:
        r = await ac.post(
            "/api/datasets/downloads",
            json={"name": "fmp.house_disclosures", "params": {}},
        )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/datasets/downloads  — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_downloads(test_app):
    """GET /api/datasets/downloads returns a list (may be empty)."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets/downloads")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# /api/datasets/downloads/{id}  — one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_download_not_found(test_app):
    """GET /api/datasets/downloads/{id} returns 404 for unknown id."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets/downloads/999999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/datasets/quota  — all providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_endpoint_returns_list(test_app):
    """GET /api/datasets/quota returns a list (may be empty when no quota rows exist)."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets/quota")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)


# ---------------------------------------------------------------------------
# /api/datasets/quota/{provider}  — one provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_provider_missing_returns_zeros(test_app):
    """GET /api/datasets/quota/{provider} returns zero-usage sentinel for missing provider."""
    async with _client(test_app) as ac:
        r = await ac.get("/api/datasets/quota/fmp")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "fmp"
    assert body["calls_used"] == 0
    assert body["exhausted"] is False
