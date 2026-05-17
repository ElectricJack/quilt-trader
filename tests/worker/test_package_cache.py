import io
import tarfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


def _build_test_tarball(file_contents: dict) -> bytes:
    """Build a gzipped tar of {path_in_tar: content_str}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in file_contents.items():
            data = content.encode()
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_ensure_downloads_and_extracts_when_cache_miss(tmp_path, monkeypatch):
    from worker import package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)

    tar_bytes = _build_test_tarball({
        "test-algo/quilt.yaml": "name: test\n",
        "test-algo/algorithm.py": "class TestAlgo: pass\n",
    })

    class FakeResp:
        status_code = 200
        async def aread(self): return tar_bytes
        def raise_for_status(self): pass

    class FakeStream:
        async def __aenter__(self_inner): return FakeResp()
        async def __aexit__(self_inner, *args): pass

    fake_client = MagicMock()
    fake_client.stream = MagicMock(return_value=FakeStream())
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(package_cache.httpx, "AsyncClient",
                        lambda **kw: fake_client)

    agent = MagicMock()
    agent.coordinator_http_url = "http://fake-coord:8000"
    agent.worker_install_token = "tok"

    result_path = await package_cache.ensure(
        agent=agent, algorithm_id="algo-1", commit_sha="sha-abc",
    )
    assert result_path == tmp_path / "algo-1" / "sha-abc"
    assert (result_path / "test-algo" / "quilt.yaml").exists()


@pytest.mark.asyncio
async def test_ensure_cache_hit_skips_download(tmp_path, monkeypatch):
    from worker import package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)

    cached = tmp_path / "algo-1" / "sha-abc"
    cached.mkdir(parents=True)
    (cached / "quilt.yaml").write_text("cached")

    def boom(**kw):
        raise RuntimeError("should not download")
    monkeypatch.setattr(package_cache.httpx, "AsyncClient", boom)

    agent = MagicMock()
    agent.coordinator_http_url = "http://fake-coord:8000"
    agent.worker_install_token = "tok"

    result_path = await package_cache.ensure(
        agent=agent, algorithm_id="algo-1", commit_sha="sha-abc",
    )
    assert result_path == cached


def test_load_algorithm_class_imports_module_and_returns_class(tmp_path):
    from worker import package_cache
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "test_module.py").write_text(
        "class MyAlgo:\n    def hello(self): return 'hi'\n"
    )
    cls = package_cache.load_algorithm_class(
        pkg_dir=pkg_dir, entry_point="test_module", class_name="MyAlgo",
    )
    assert cls().hello() == "hi"


def test_load_algorithm_class_raises_when_entry_point_missing(tmp_path):
    from worker import package_cache
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        package_cache.load_algorithm_class(
            pkg_dir=pkg_dir, entry_point="nope", class_name="Anything",
        )


def test_load_algorithm_class_raises_when_class_missing(tmp_path):
    from worker import package_cache
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "mod.py").write_text("# empty\n")
    with pytest.raises(AttributeError):
        package_cache.load_algorithm_class(
            pkg_dir=pkg_dir, entry_point="mod", class_name="MissingClass",
        )


def test_load_algorithm_class_walks_one_level_for_nested_package(tmp_path):
    """Tarballs extracted from coordinator have an outer dir named after the
    repo. load_algorithm_class should look one level deeper for the entry point."""
    from worker import package_cache
    pkg_dir = tmp_path / "extracted"
    pkg_dir.mkdir()
    (pkg_dir / "test-algo").mkdir()
    (pkg_dir / "test-algo" / "my_module.py").write_text(
        "class MyAlgo:\n    pass\n"
    )
    cls = package_cache.load_algorithm_class(
        pkg_dir=pkg_dir, entry_point="my_module", class_name="MyAlgo",
    )
    assert cls.__name__ == "MyAlgo"
