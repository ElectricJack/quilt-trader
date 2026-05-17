import pytest
from pathlib import Path


@pytest.fixture
def _valid_pkg(tmp_path):
    """Create a minimal valid algorithm package on disk."""
    pkg = tmp_path / "my-algo"
    pkg.mkdir()
    (pkg / "quilt.yaml").write_text(
        "name: my-algo\ntype: algorithm\nversion: 1.0.0\n"
        "entry_point: my_algo\nclass_name: MyAlgo\n"
        "requirements:\n  asset_types: [equities]\n"
    )
    (pkg / "my_algo.py").write_text(
        "from sdk.algorithm import QuiltAlgorithm\n"
        "class MyAlgo(QuiltAlgorithm):\n"
        "  def on_start(self, c, s): pass\n"
        "  def on_tick(self, ctx): return []\n"
        "  def on_stop(self): return {}\n"
        "  def save_state(self): return {}\n"
    )
    return pkg


@pytest.mark.asyncio
async def test_install_from_local_directory(client, _valid_pkg):
    r = await client.post("/api/algorithms/install", json={
        "source": str(_valid_pkg),
    })
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["commit_hash"].startswith("local:")


@pytest.mark.asyncio
async def test_install_with_name_override(client, _valid_pkg):
    r = await client.post("/api/algorithms/install", json={
        "source": str(_valid_pkg),
        "name_override": "my-algo-dev",
    })
    assert r.status_code in (200, 201)
    body = r.json()
    assert body["name"] == "my-algo-dev"


@pytest.mark.asyncio
async def test_install_local_path_persists_source_path(client, _valid_pkg, db_session):
    from sqlalchemy import select
    from coordinator.database.models import Algorithm
    r = await client.post("/api/algorithms/install", json={
        "source": str(_valid_pkg),
    })
    assert r.status_code in (200, 201)
    algo_id = r.json()["id"]
    algo = (await db_session.execute(
        select(Algorithm).where(Algorithm.id == algo_id)
    )).scalar_one()
    assert algo.source_path == str(_valid_pkg)
