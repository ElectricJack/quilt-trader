import io
import tarfile
import pytest
from coordinator.database.models import Algorithm, Worker


@pytest.fixture
def install_token():
    return "test-worker-install-token-32-chars-long"


@pytest.mark.asyncio
async def test_package_endpoint_requires_worker_install_token(client, db_session):
    algo = Algorithm(
        repo_url="https://github.com/test-org/test-algo",
        name="test-algo",
        commit_hash="abc1234",
    )
    db_session.add(algo)
    await db_session.commit()

    r = await client.get(f"/api/algorithms/{algo.id}/package.tar.gz?sha=abc1234")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_package_endpoint_404_when_sha_mismatch(client, db_session, install_token):
    algo = Algorithm(
        repo_url="https://github.com/test-org/test-algo",
        name="test-algo",
        commit_hash="abc1234",
    )
    w = Worker(name="w", status="online", install_token=install_token,
               install_status="claimed")
    db_session.add_all([algo, w])
    await db_session.commit()

    r = await client.get(
        f"/api/algorithms/{algo.id}/package.tar.gz?sha=different",
        headers={"X-Worker-Install-Token": install_token},
    )
    assert r.status_code == 404
    assert "SHA mismatch" in r.json()["detail"]


@pytest.mark.asyncio
async def test_package_endpoint_404_when_dir_missing_on_disk(client, db_session, install_token):
    algo = Algorithm(
        repo_url="https://github.com/test-org/nonexistent-pkg",
        name="nonexistent-pkg",
        commit_hash="abc1234",
    )
    w = Worker(name="w", status="online", install_token=install_token,
               install_status="claimed")
    db_session.add_all([algo, w])
    await db_session.commit()

    r = await client.get(
        f"/api/algorithms/{algo.id}/package.tar.gz?sha=abc1234",
        headers={"X-Worker-Install-Token": install_token},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_package_endpoint_streams_tarball(client, db_session, install_token, tmp_path, monkeypatch):
    pkg_dir = tmp_path / "test-algo"
    pkg_dir.mkdir()
    (pkg_dir / "quilt.yaml").write_text("name: test\n")
    (pkg_dir / "algorithm.py").write_text("class TestAlgo: pass\n")

    import coordinator.api.routes.algorithms as algos_mod
    monkeypatch.setattr(algos_mod, "PACKAGE_ROOT", tmp_path)

    algo = Algorithm(
        repo_url="https://github.com/test-org/test-algo",
        name="test-algo",
        commit_hash="abc1234",
    )
    w = Worker(name="w", status="online", install_token=install_token,
               install_status="claimed")
    db_session.add_all([algo, w])
    await db_session.commit()

    r = await client.get(
        f"/api/algorithms/{algo.id}/package.tar.gz?sha=abc1234",
        headers={"X-Worker-Install-Token": install_token},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/gzip"
    buf = io.BytesIO(r.content)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = tar.getnames()
        assert any(n.endswith("quilt.yaml") for n in names)
        assert any(n.endswith("algorithm.py") for n in names)
