import uuid, json
from datetime import date
import pytest, pytest_asyncio
from httpx import ASGITransport, AsyncClient

from coordinator.database.models import Algorithm, OptimizationSession


@pytest_asyncio.fixture
async def test_client(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session_factory(test_app):
    from coordinator.api.dependencies import get_container
    return get_container().session_factory


@pytest_asyncio.fixture
async def seeded_session(db_session_factory):
    aid = f"a-{uuid.uuid4().hex[:6]}"
    async with db_session_factory() as s:
        s.add(Algorithm(id=aid, repo_url="https://x.com", name="x", source_path="/tmp/x", install_status="installed"))
        sess = OptimizationSession(
            name=f"sess-{uuid.uuid4().hex[:6]}", hypothesis="h", algorithm_id=aid,
            base_config={}, parameter_space=json.dumps({}), pre_registered_criteria=json.dumps({}),
            status="open",
            date_range_start=date(2024, 1, 1), date_range_end=date(2024, 12, 31),
        )
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
        return sess


@pytest.mark.asyncio
async def test_post_cpcv_mode_fixed_returns_202(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/cpcv",
        json={"mode": "fixed", "n_groups": 6, "test_groups_per_split": 2, "embargo": 5},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["kind"] == "cpcv"
    assert body["status"] == "queued"
    assert body["job_id"]
    assert body["projected_backtest_count"] == 6


@pytest.mark.asyncio
async def test_post_cpcv_mode_select_returns_202(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/cpcv",
        json={
            "mode": "select", "n_groups": 6, "test_groups_per_split": 2, "embargo": 5,
            "parameter_space": {"lookback": [10, 20]}, "search": "grid",
            "max_trials_per_split": 20,
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # C(6, 2) * (20 + 2) = 15 * 22 = 330
    assert body["projected_backtest_count"] == 330


@pytest.mark.asyncio
async def test_post_cpcv_rejects_mode_select_without_parameter_space(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/cpcv",
        json={"mode": "select", "n_groups": 6, "test_groups_per_split": 2, "embargo": 5},
    )
    assert resp.status_code == 400
    assert "parameter_space" in resp.text


@pytest.mark.asyncio
async def test_post_cpcv_rejects_n_groups_lt_4(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/cpcv",
        json={"mode": "fixed", "n_groups": 3, "test_groups_per_split": 1, "embargo": 0},
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_post_cpcv_rejects_k_too_large(test_client, seeded_session):
    resp = await test_client.post(
        f"/api/research/sessions/{seeded_session.id}/cpcv",
        json={"mode": "fixed", "n_groups": 6, "test_groups_per_split": 4, "embargo": 0},
    )
    assert resp.status_code in (400, 422)
