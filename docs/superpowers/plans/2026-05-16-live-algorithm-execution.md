# Live Algorithm Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a "Running" deployment actually run an algorithm on a Pi worker, reacting to live market data and emitting the per-tick samples and activity events the previous spec's pipeline expects.

**Architecture:** Coordinator owns scheduling and data routing — it knows each algorithm's `trigger` (declared in manifest) and pushes `tick_batch` ws messages to workers whenever an algorithm should react. Workers load algorithm code from coordinator-served tarballs, build broker adapters from coordinator-shipped credentials, run `AlgorithmRunner` + `LiveObserver`, dispatch orders directly to the broker over HTTPS. Streaming data is shared via the existing `live_feed_aggregator` (extended with a callback API); orders flow worker → broker directly.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, websockets, httpx, pandas, pyarrow, asyncio, importlib, tarfile. Pytest + pytest-asyncio for tests.

**Reference spec:** `docs/superpowers/specs/2026-05-16-live-algorithm-execution-design.md`.

---

## Conventions

- Backend test command: `pytest tests/coordinator/<path>.py -v` or `pytest tests/worker/<path>.py -v`
- Atomic commits per task; message style `<type>(<area>): <subject>`.
- Use exact `git add <files>` (never directory-wide) to avoid sweeping the user's uncommitted WIP.
- Stay on `main` branch (project's normal workflow).
- All datetime serialization uses the existing `coordinator.api.serialization.to_iso_utc` helper.

---

## Milestone 1 — Manifest + Algorithm Package Endpoint

Foundation. Adds the `trigger` field that downstream tasks key off, and the HTTP endpoint workers will pull algorithm code from.

### Task 1.1: Extend QuiltManifest with `trigger` and `history_bars`

**Files:**
- Modify: `sdk/manifest.py`
- Test: `tests/sdk/test_manifest.py` (extend if exists, else create)

- [ ] **Step 1: Read the current `sdk/manifest.py`**

Run: `Read sdk/manifest.py`. Note: `QuiltManifest` is a dataclass with fields like `name`, `type`, `version`, `entry_point`, `class_name`, `requirements`, `schedule`, `jitter_seconds`. There's a `_parse` classmethod that validates.

- [ ] **Step 2: Write the failing tests**

Create or extend `tests/sdk/test_manifest.py`:

```python
import pytest
from sdk.manifest import QuiltManifest, ManifestError


def _base_yaml(extra: str = "") -> str:
    return f"""
name: test-algo
type: algorithm
version: 1.0.0
entry_point: my_algo.algorithm
class_name: MyAlgo
requirements:
  asset_types: [equities]
  brokers: [alpaca]
  data_dependencies:
    - {{ symbol: "AAPL", timeframe: "1min" }}
{extra}
"""


def test_trigger_defaults_to_bar_1min_when_omitted():
    m = QuiltManifest.from_string(_base_yaml())
    assert m.trigger == "bar:1min"


def test_trigger_accepts_bar_timeframe():
    m = QuiltManifest.from_string(_base_yaml("trigger: bar:5min"))
    assert m.trigger == "bar:5min"


def test_trigger_accepts_event():
    m = QuiltManifest.from_string(_base_yaml("trigger: event"))
    assert m.trigger == "event"


def test_trigger_accepts_interval_with_duration_suffix():
    m = QuiltManifest.from_string(_base_yaml("trigger: interval:30s"))
    assert m.trigger == "interval:30s"
    m2 = QuiltManifest.from_string(_base_yaml("trigger: interval:5m"))
    assert m2.trigger == "interval:5m"


def test_trigger_rejects_unknown_format():
    with pytest.raises(ManifestError):
        QuiltManifest.from_string(_base_yaml("trigger: not_a_trigger"))


def test_trigger_rejects_invalid_interval():
    with pytest.raises(ManifestError):
        QuiltManifest.from_string(_base_yaml("trigger: interval:abc"))


def test_data_dependencies_history_bars_default():
    m = QuiltManifest.from_string(_base_yaml())
    deps = m.requirements.data_dependencies
    assert deps[0].get("history_bars", 200) == 200


def test_data_dependencies_history_bars_explicit():
    extra = """  data_dependencies:
    - {{ symbol: "AAPL", timeframe: "1min", history_bars: 500 }}"""
    # Slight rebuild: replace data_dependencies in base
    yaml_text = f"""
name: test-algo
type: algorithm
version: 1.0.0
entry_point: my_algo.algorithm
class_name: MyAlgo
requirements:
  asset_types: [equities]
  brokers: [alpaca]
  data_dependencies:
    - {{ symbol: "AAPL", timeframe: "1min", history_bars: 500 }}
"""
    m = QuiltManifest.from_string(yaml_text)
    assert m.requirements.data_dependencies[0]["history_bars"] == 500


def test_history_bars_rejects_non_positive():
    yaml_text = f"""
name: test-algo
type: algorithm
version: 1.0.0
entry_point: my_algo.algorithm
class_name: MyAlgo
requirements:
  asset_types: [equities]
  brokers: [alpaca]
  data_dependencies:
    - {{ symbol: "AAPL", timeframe: "1min", history_bars: -5 }}
"""
    with pytest.raises(ManifestError):
        QuiltManifest.from_string(yaml_text)
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `pytest tests/sdk/test_manifest.py -v -k "trigger or history_bars"`
Expected: tests fail (trigger field doesn't exist; history_bars validation doesn't exist).

- [ ] **Step 4: Implement the changes in `sdk/manifest.py`**

Add `import re` at the top.

Add the regex constant near the top of the module:
```python
TRIGGER_REGEX = re.compile(r"^(bar:[a-z0-9]+|event|interval:\d+[smh])$")
```

In the `QuiltManifest` dataclass, add the field (after `jitter_seconds`):
```python
trigger: str = "bar:1min"
```

In `_parse`, after the existing validation, add:
```python
trigger = data.get("trigger", "bar:1min")
if not TRIGGER_REGEX.match(trigger):
    raise ManifestError(
        f"trigger must match {TRIGGER_REGEX.pattern!r}, got {trigger!r}"
    )

# Validate history_bars on each data_dependency entry
for dep in (reqs_data.get("data_dependencies") or []):
    if not isinstance(dep, dict):
        continue
    hb = dep.get("history_bars")
    if hb is None:
        continue
    if not isinstance(hb, int) or hb <= 0:
        raise ManifestError(
            f"data_dependencies entry history_bars must be a positive integer, got {hb!r}"
        )
```

Pass `trigger=trigger` into the `QuiltManifest(...)` constructor at the end of `_parse`.

- [ ] **Step 5: Run the tests, verify they pass**

Run: `pytest tests/sdk/test_manifest.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add sdk/manifest.py tests/sdk/test_manifest.py
git commit -m "feat(sdk): manifest trigger field + data_dependencies.history_bars validation"
```

### Task 1.2: Algorithm package download endpoint

**Files:**
- Modify: `coordinator/api/routes/algorithms.py` (add new GET route)
- Modify: `coordinator/api/routes/workers.py` (do NOT clear install_token on claim — keep it for package fetches)
- Test: `tests/coordinator/test_algorithm_package_api.py` (new)

- [ ] **Step 1: Read the existing patterns**

Run: `Read coordinator/api/routes/workers.py` and look at `worker_install_package` (around lines 116–125) and `_build_worker_package_tarball` (around lines 83–102). Note: it streams a gzipped tarball of selected source directories, authenticated by a token.

Run: `Read coordinator/services/backtest_runner.py` and find `_package_dir_name` (top of file). Note: this derives the on-disk dir name from a GitHub repo_url.

- [ ] **Step 2: Write the failing test**

Create `tests/coordinator/test_algorithm_package_api.py`:

```python
import io
import tarfile
import pytest
from pathlib import Path

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
    # Use a repo_url whose derived dir name won't exist under data/packages.
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
    # Create a fake package directory.
    pkg_dir = tmp_path / "test-algo"
    pkg_dir.mkdir()
    (pkg_dir / "quilt.yaml").write_text("name: test\n")
    (pkg_dir / "algorithm.py").write_text("class TestAlgo: pass\n")

    # Patch the package directory root to tmp_path (the route uses Path("data/packages")).
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
    # Open the tarball and verify our files are inside.
    buf = io.BytesIO(r.content)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = tar.getnames()
        assert any(n.endswith("quilt.yaml") for n in names)
        assert any(n.endswith("algorithm.py") for n in names)
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `pytest tests/coordinator/test_algorithm_package_api.py -v`
Expected: all fail with 404 (route doesn't exist).

- [ ] **Step 4: Implement the endpoint in `coordinator/api/routes/algorithms.py`**

Read the file to find a good insertion point. Add at the top of the file (alongside existing imports):

```python
import io
import tarfile
from pathlib import Path
from fastapi.responses import Response
from sqlalchemy import select
from coordinator.database.models import Algorithm, Worker
```

Add this module-level constant (override-able for testing):
```python
PACKAGE_ROOT = Path("data/packages")
```

Add a helper near the top:
```python
def _derive_package_dir_name(repo_url: str) -> str:
    """Match the convention used by the install flow: last path segment of the GitHub repo URL."""
    import re
    m = re.match(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url or "")
    if not m:
        raise ValueError(f"Cannot derive package directory from repo_url: {repo_url!r}")
    return m.group(1).split("/", 1)[1]


def _build_algorithm_tarball(pkg_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(
            pkg_dir,
            arcname=pkg_dir.name,
            filter=lambda ti: None if (
                "__pycache__" in ti.name
                or ti.name.endswith(".pyc")
                or ti.name.endswith(".pyo")
            ) else ti,
        )
    return buf.getvalue()
```

Add the route:
```python
@router.get("/{algorithm_id}/package.tar.gz")
async def algorithm_package(
    algorithm_id: str,
    sha: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    token = request.headers.get("X-Worker-Install-Token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing X-Worker-Install-Token")
    worker = (await db.execute(
        select(Worker).where(Worker.install_token == token)
    )).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=401, detail="Invalid worker token")
    algo = (await db.execute(
        select(Algorithm).where(Algorithm.id == algorithm_id)
    )).scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")
    if algo.commit_hash != sha:
        raise HTTPException(
            status_code=404,
            detail=f"Algorithm SHA mismatch: have {algo.commit_hash}, requested {sha}",
        )
    try:
        pkg_dir_name = _derive_package_dir_name(algo.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    src_path = PACKAGE_ROOT / pkg_dir_name
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="Algorithm package not on disk")
    data = _build_algorithm_tarball(src_path)
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename={pkg_dir_name}.tar.gz"},
    )
```

Ensure `Request` is imported from `fastapi` (might already be).

- [ ] **Step 5: Modify worker claim flow to retain install_token**

Find `claim_worker` in `coordinator/api/routes/workers.py`. It currently sets `worker.install_token = None` on claim. Remove that line so the token persists for use as a worker auth token. Replace:
```python
if worker.install_status == "claimed":
    return {"ok": True, "already_claimed": True}
worker.install_status = "claimed"
worker.install_token = None
await db.flush()
```
with:
```python
if worker.install_status == "claimed":
    return {"ok": True, "already_claimed": True}
worker.install_status = "claimed"
# Note: install_token is kept after claim so the worker can authenticate
# subsequent requests (e.g. /api/algorithms/.../package.tar.gz).
await db.flush()
```

- [ ] **Step 6: Run tests, verify pass**

Run: `pytest tests/coordinator/test_algorithm_package_api.py -v`
Run also: `pytest tests/coordinator/test_workers_api*.py -v` (regression — make sure the claim behavior change didn't break existing tests).
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add coordinator/api/routes/algorithms.py coordinator/api/routes/workers.py tests/coordinator/test_algorithm_package_api.py
git commit -m "feat(api): /api/algorithms/{id}/package.tar.gz endpoint + retain worker install_token after claim"
```

---

## Milestone 2 — Worker Bring-up Infrastructure

Stateless building blocks the runtime composes.

### Task 2.1: `package_cache` module

**Files:**
- Create: `worker/package_cache.py`
- Create: `tests/worker/test_package_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_package_cache.py
import io
import tarfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


def _build_test_tarball(file_contents: dict[str, str]) -> bytes:
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
    # Build a fake httpx response.
    class FakeResp:
        status_code = 200
        async def aread(self): return tar_bytes
        def raise_for_status(self): pass
    class FakeStream:
        async def __aenter__(self_inner): return FakeResp()
        async def __aexit__(self_inner, *args): pass
    fake_client = MagicMock()
    fake_client.stream = MagicMock(return_value=FakeStream())
    fake_aclose = AsyncMock()
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

    # If ensure calls httpx, the test will blow up.
    monkeypatch.setattr(package_cache.httpx, "AsyncClient",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("should not download")))

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
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/worker/test_package_cache.py -v`
Expected: ImportError or AttributeError.

- [ ] **Step 3: Implement `worker/package_cache.py`**

```python
"""Local algorithm package cache.

Resolves (algorithm_id, commit_sha) → local directory. Downloads via HTTP
from the coordinator's /api/algorithms/{id}/package.tar.gz endpoint when
the cache is cold; otherwise returns the cached path. Cache key includes
commit_sha so updates don't invalidate older runs.
"""
from __future__ import annotations

import importlib.util
import io
import logging
import os
import tarfile
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PACKAGE_CACHE_ROOT = Path(
    os.environ.get("QT_PACKAGE_CACHE_ROOT",
                    str(Path.home() / ".quilt" / "packages"))
)


async def ensure(*, agent: Any, algorithm_id: str, commit_sha: str) -> Path:
    """Return a local directory containing the algorithm package.

    Downloads from the coordinator if not already cached locally.
    """
    target = PACKAGE_CACHE_ROOT / algorithm_id / commit_sha
    if target.exists() and any(target.iterdir()):
        return target
    target.mkdir(parents=True, exist_ok=True)

    url = f"{agent.coordinator_http_url}/api/algorithms/{algorithm_id}/package.tar.gz"
    params = {"sha": commit_sha}
    headers = {"X-Worker-Install-Token": agent.worker_install_token}

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("GET", url, params=params, headers=headers) as resp:
            resp.raise_for_status()
            tar_bytes = await resp.aread()

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        tar.extractall(target)  # extractall is fine — content comes from trusted coordinator

    logger.info("Cached algorithm %s @ %s to %s", algorithm_id, commit_sha, target)
    return target


def load_algorithm_class(*, pkg_dir: Path, entry_point: str, class_name: str) -> type:
    """Load `class_name` from `entry_point` (e.g. "my_pkg.algorithm") within `pkg_dir`.

    Uses importlib.util.spec_from_file_location so we don't pollute global sys.path
    or risk colliding with other algorithms loaded into the same worker process.
    """
    # First try as a module file: pkg_dir/<entry_point_dotted>.py
    module_relpath = Path(entry_point.replace(".", "/") + ".py")
    module_path = pkg_dir / module_relpath
    if not module_path.exists():
        # Walk one level deeper: maybe the tarball extracted into pkg_dir/<repo-name>/
        # Look for any single subdirectory that contains the entry point.
        for sub in pkg_dir.iterdir():
            if sub.is_dir():
                candidate = sub / module_relpath
                if candidate.exists():
                    module_path = candidate
                    pkg_dir = sub  # so submodule search starts here
                    break
    if not module_path.exists():
        # Try package init form: <entry_point>/__init__.py
        init_path = pkg_dir / entry_point.replace(".", "/") / "__init__.py"
        if init_path.exists():
            module_path = init_path
        else:
            for sub in pkg_dir.iterdir():
                if sub.is_dir():
                    candidate = sub / entry_point.replace(".", "/") / "__init__.py"
                    if candidate.exists():
                        module_path = candidate
                        pkg_dir = sub
                        break
    if not module_path.exists():
        raise FileNotFoundError(
            f"Algorithm entry_point {entry_point!r} not found in {pkg_dir}"
        )

    spec = importlib.util.spec_from_file_location(
        entry_point, module_path,
        submodule_search_locations=[str(pkg_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(
            f"Class {class_name!r} not found in {entry_point!r} ({module_path})"
        )
    return cls
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/worker/test_package_cache.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add worker/package_cache.py tests/worker/test_package_cache.py
git commit -m "feat(worker): package_cache module — download + import algorithm packages"
```

### Task 2.2: `RollingDataBuffer` module

**Files:**
- Create: `worker/rolling_data_buffer.py`
- Create: `tests/worker/test_rolling_data_buffer.py`

- [ ] **Step 1: Failing tests**

```python
# tests/worker/test_rolling_data_buffer.py
import pytest
import pandas as pd
from unittest.mock import AsyncMock


def test_init_creates_buffers_per_dependency():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 100},
        {"symbol": "SPY", "timeframe": "1min"},  # default history_bars
    ])
    assert buf.has("AAPL", "1min")
    assert buf.has("SPY", "1min")
    assert not buf.has("MSFT", "1min")


def test_init_skips_entries_without_symbol():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"timeframe": "1min", "name": "some_scraper"},  # no symbol → skipped
        {"symbol": "AAPL"},
    ])
    assert buf.has("AAPL", "1min")  # default timeframe


@pytest.mark.asyncio
async def test_backfill_populates_each_buffer():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 50},
    ])
    sample_df = pd.DataFrame([
        {"timestamp": "2026-05-16T12:00:00Z", "open": 100.0, "high": 101.0,
         "low": 99.5, "close": 100.5, "volume": 1000.0},
        {"timestamp": "2026-05-16T12:01:00Z", "open": 100.5, "high": 101.5,
         "low": 100.0, "close": 101.0, "volume": 1500.0},
    ])
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=sample_df)
    await buf.backfill(data_client)
    out = buf.get("AAPL", "1min", 10)
    assert len(out) == 2
    assert out.iloc[0]["close"] == 100.5


def test_ingest_appends_new_bars():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 50},
    ])
    buf.ingest({
        "AAPL": {
            "timeframe": "1min",
            "bars": [
                {"timestamp": "2026-05-16T12:02:00Z", "close": 102.0},
                {"timestamp": "2026-05-16T12:03:00Z", "close": 103.0},
            ],
        },
    })
    out = buf.get("AAPL", "1min", 10)
    assert len(out) == 2
    assert out.iloc[-1]["close"] == 103.0


def test_ingest_ignores_unknown_symbols():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min"},
    ])
    buf.ingest({
        "MSFT": {"timeframe": "1min", "bars": [{"close": 99}]},
    })
    assert buf.get("MSFT", "1min", 10).empty


def test_get_returns_last_n_bars():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 50},
    ])
    for i in range(20):
        buf.ingest({"AAPL": {"timeframe": "1min", "bars": [{"i": i}]}})
    out = buf.get("AAPL", "1min", 5)
    assert len(out) == 5
    assert list(out["i"]) == [15, 16, 17, 18, 19]


def test_get_unknown_returns_empty_df():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL"}])
    assert buf.get("MSFT", "1min", 10).empty


def test_maxlen_enforced():
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([
        {"symbol": "AAPL", "timeframe": "1min", "history_bars": 3},
    ])
    for i in range(10):
        buf.ingest({"AAPL": {"timeframe": "1min", "bars": [{"i": i}]}})
    out = buf.get("AAPL", "1min", 100)
    assert len(out) == 3
    assert list(out["i"]) == [7, 8, 9]
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/worker/test_rolling_data_buffer.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `worker/rolling_data_buffer.py`**

```python
"""In-memory per-instance rolling buffer for market data bars.

Each (symbol, timeframe) pair declared in the algorithm's data_dependencies
gets its own deque, sized to history_bars. Backfill once from coordinator
HTTP at start; ingest deltas pushed by coordinator on each tick; serve
ctx.market_data(...) calls from memory without HTTP.
"""
from __future__ import annotations

from collections import deque
from typing import Any
import logging
import pandas as pd

logger = logging.getLogger(__name__)


class RollingDataBuffer:
    def __init__(self, data_dependencies: list[dict]) -> None:
        self._buffers: dict[tuple[str, str], deque] = {}
        self._max_bars: dict[tuple[str, str], int] = {}
        for d in data_dependencies or []:
            if not isinstance(d, dict):
                continue
            sym = d.get("symbol")
            if not sym:
                continue
            tf = d.get("timeframe", "1min")
            max_bars = int(d.get("history_bars", 200))
            key = (sym, tf)
            self._buffers[key] = deque(maxlen=max_bars)
            self._max_bars[key] = max_bars

    async def backfill(self, data_client: Any) -> None:
        for (sym, tf), buf in self._buffers.items():
            try:
                df = await data_client.get_market_data(
                    sym, timeframe=tf, bars=self._max_bars[(sym, tf)],
                )
            except Exception:
                logger.exception("Backfill failed for %s/%s", sym, tf)
                continue
            for _, row in df.iterrows():
                buf.append(row.to_dict())

    def ingest(self, push_data: dict) -> None:
        for sym, payload in (push_data or {}).items():
            if not isinstance(payload, dict):
                continue
            tf = payload.get("timeframe", "1min")
            key = (sym, tf)
            if key not in self._buffers:
                continue
            for bar in payload.get("bars", []) or []:
                self._buffers[key].append(bar)

    def get(self, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        key = (symbol, timeframe)
        if key not in self._buffers:
            return pd.DataFrame()
        rows = list(self._buffers[key])[-bars:]
        return pd.DataFrame(rows)

    def has(self, symbol: str, timeframe: str) -> bool:
        return (symbol, timeframe) in self._buffers
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/worker/test_rolling_data_buffer.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add worker/rolling_data_buffer.py tests/worker/test_rolling_data_buffer.py
git commit -m "feat(worker): RollingDataBuffer — per-instance bar cache with backfill + delta ingest"
```

### Task 2.3: `CachingBrokerAdapter` wrapper

**Files:**
- Create: `worker/caching_broker_adapter.py`
- Create: `tests/worker/test_caching_broker_adapter.py`

- [ ] **Step 1: Failing tests**

```python
# tests/worker/test_caching_broker_adapter.py
import time
import pytest
from unittest.mock import MagicMock


def test_get_account_info_caches_within_ttl():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.get_account_info.return_value = {"cash": 100, "portfolio_value": 200, "buying_power": 100}
    wrapper = CachingBrokerAdapter(inner, account_state_ttl=60)
    wrapper.get_account_info()
    wrapper.get_account_info()
    assert inner.get_account_info.call_count == 1


def test_get_account_info_refreshes_after_ttl(monkeypatch):
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.get_account_info.return_value = {"cash": 1}
    wrapper = CachingBrokerAdapter(inner, account_state_ttl=10)
    fake_time = [1000.0]
    monkeypatch.setattr("worker.caching_broker_adapter.time.monotonic", lambda: fake_time[0])
    wrapper.get_account_info()
    fake_time[0] += 11
    wrapper.get_account_info()
    assert inner.get_account_info.call_count == 2


def test_invalidate_forces_refresh():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.get_account_info.return_value = {"cash": 1}
    wrapper = CachingBrokerAdapter(inner, account_state_ttl=60)
    wrapper.get_account_info()
    wrapper.invalidate()
    wrapper.get_account_info()
    assert inner.get_account_info.call_count == 2


def test_get_positions_cached_independently():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.get_account_info.return_value = {"cash": 1}
    inner.get_positions.return_value = {"AAPL": {"qty": 10}}
    wrapper = CachingBrokerAdapter(inner, account_state_ttl=60)
    wrapper.get_account_info()
    wrapper.get_positions()
    wrapper.get_positions()
    assert inner.get_account_info.call_count == 1
    assert inner.get_positions.call_count == 1


def test_submit_order_delegates_to_inner():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.submit_order.return_value = {"ok": True}
    wrapper = CachingBrokerAdapter(inner)
    result = wrapper.submit_order(
        symbol="AAPL", side="buy", quantity=10, order_type="market",
    )
    assert result == {"ok": True}
    inner.submit_order.assert_called_once()


def test_unknown_attribute_passthrough():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.something_custom.return_value = 42
    wrapper = CachingBrokerAdapter(inner)
    assert wrapper.something_custom() == 42
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/worker/test_caching_broker_adapter.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# worker/caching_broker_adapter.py
"""Thin TTL-caching wrapper around BrokerAdapter for hot-path account state reads.

get_account_info and get_positions are called every tick. Without caching,
each tick incurs 1-2 HTTPS round-trips to the broker. With a 30s TTL,
multiple algorithms (rare due to account locking, but possible across
different timeframes) naturally share state, and broker rate limits stay safe.

submit_order and other write paths pass through unchanged, and call sites
should invoke .invalidate() after an order succeeds so the next tick reads
fresh positions.
"""
from __future__ import annotations

import time
from typing import Any, Optional


class CachingBrokerAdapter:
    def __init__(self, inner: Any, account_state_ttl: float = 30.0) -> None:
        self._inner = inner
        self._ttl = account_state_ttl
        self._cache_account: Optional[tuple[float, dict]] = None
        self._cache_positions: Optional[tuple[float, dict]] = None

    def get_account_info(self) -> dict:
        now = time.monotonic()
        if self._cache_account is not None and now - self._cache_account[0] < self._ttl:
            return self._cache_account[1]
        v = self._inner.get_account_info()
        self._cache_account = (now, v)
        return v

    def get_positions(self) -> dict[str, dict]:
        now = time.monotonic()
        if self._cache_positions is not None and now - self._cache_positions[0] < self._ttl:
            return self._cache_positions[1]
        v = self._inner.get_positions()
        self._cache_positions = (now, v)
        return v

    def invalidate(self) -> None:
        self._cache_account = None
        self._cache_positions = None

    def submit_order(self, *args, **kwargs):
        return self._inner.submit_order(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Anything not explicitly overridden (transactions, multileg, etc.)
        # passes through to the inner adapter.
        return getattr(self._inner, name)
```

- [ ] **Step 4: Run + commit**

Run: `pytest tests/worker/test_caching_broker_adapter.py -v` → 6 passed.

```bash
git add worker/caching_broker_adapter.py tests/worker/test_caching_broker_adapter.py
git commit -m "feat(worker): CachingBrokerAdapter — 30s TTL cache for account state reads"
```

### Task 2.4: Extend `LiveTickContext` to read from buffer

**Files:**
- Modify: `worker/context.py`
- Test: `tests/worker/test_live_tick_context.py` (create if absent)

- [ ] **Step 1: Failing tests**

```python
# tests/worker/test_live_tick_context.py
import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_market_data_reads_from_buffer_when_available():
    from worker.context import LiveTickContext
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL", "timeframe": "1min", "history_bars": 10}])
    buf.ingest({"AAPL": {"timeframe": "1min", "bars": [{"close": 100.0}, {"close": 101.0}]}})
    broker = MagicMock()
    data_client = AsyncMock()
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client, buffer=buf,
    )
    df = await ctx.market_data("AAPL", "1min", 5)
    assert len(df) == 2
    data_client.get_market_data.assert_not_called()


@pytest.mark.asyncio
async def test_market_data_falls_back_to_http_when_symbol_not_in_buffer():
    from worker.context import LiveTickContext
    from worker.rolling_data_buffer import RollingDataBuffer
    buf = RollingDataBuffer([{"symbol": "AAPL", "timeframe": "1min"}])
    broker = MagicMock()
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=pd.DataFrame([{"close": 999.0}]))
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client, buffer=buf,
    )
    df = await ctx.market_data("MSFT", "1min", 5)
    assert len(df) == 1
    data_client.get_market_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_market_data_falls_back_to_http_when_buffer_is_none():
    """Backwards compat: existing callers may not pass a buffer."""
    from worker.context import LiveTickContext
    broker = MagicMock()
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=pd.DataFrame([{"x": 1}]))
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live", broker=broker, data_client=data_client,
    )
    await ctx.market_data("AAPL", "1min", 5)
    data_client.get_market_data.assert_awaited_once()
```

- [ ] **Step 2: Run, fail**

Run: `pytest tests/worker/test_live_tick_context.py -v`
Expected: test 1 fails (no buffer param), tests 2/3 might also fail.

- [ ] **Step 3: Modify `worker/context.py`**

Replace the existing `LiveTickContext.__init__` and `market_data` method:

```python
class LiveTickContext:
    def __init__(
        self,
        timestamp: datetime,
        mode: str,
        broker: BrokerAdapter,
        data_client: DataClient,
        buffer: Any = None,
    ) -> None:
        self._timestamp = timestamp
        self._mode = mode
        self._broker = broker
        self._data_client = data_client
        self._buffer = buffer

    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def positions(self) -> dict:
        return self._broker.get_positions()

    @property
    def account_value(self) -> float:
        return self._broker.get_account_info()["portfolio_value"]

    @property
    def cash(self) -> float:
        return self._broker.get_account_info()["cash"]

    @property
    def buying_power(self) -> float:
        return self._broker.get_account_info()["buying_power"]

    async def market_data(self, symbol: str, timeframe: str = "1min", bars: int = 100) -> pd.DataFrame:
        if self._buffer is not None and self._buffer.has(symbol, timeframe):
            return self._buffer.get(symbol, timeframe, bars)
        # Slow path: fetch over HTTP. Algorithms should declare what they need
        # in data_dependencies to avoid this.
        import logging
        logging.getLogger(__name__).warning(
            "market_data(%s, %s) not in buffer; HTTP fallback",
            symbol, timeframe,
        )
        return await self._data_client.get_market_data(symbol, timeframe=timeframe, bars=bars)

    async def data(self, source_name: str) -> pd.DataFrame:
        return await self._data_client.get_custom_data(source_name)
```

Add `from typing import Any` to imports if absent.

- [ ] **Step 4: Run + commit**

Run: `pytest tests/worker/test_live_tick_context.py tests/worker/ -v`
Expected: all pass.

```bash
git add worker/context.py tests/worker/test_live_tick_context.py
git commit -m "feat(worker): LiveTickContext reads from RollingDataBuffer with HTTP fallback"
```

---

## Milestone 3 — LiveInstanceRuntime + Worker Tick Handler

The integration layer that ties M2's pieces together and replaces the `_handle_start_instance` stub.

### Task 3.1: `LiveInstanceRuntime` class

**Files:**
- Create: `worker/live_instance_runtime.py`
- Create: `tests/worker/test_live_instance_runtime.py`

- [ ] **Step 1: Failing tests**

```python
# tests/worker/test_live_instance_runtime.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_agent():
    agent = MagicMock()
    agent.worker_id = "w1"
    agent.worker_install_token = "tok"
    agent.coordinator_http_url = "http://fake-coord:8000"
    agent._send = AsyncMock()
    agent.send_event = AsyncMock()
    agent.send_activity_event = AsyncMock()
    agent.send_state_checkpoint = AsyncMock()
    return agent


def _make_manifest():
    return {
        "name": "test-algo",
        "entry_point": "test_algo.algorithm",
        "class_name": "TestAlgo",
        "trigger": "bar:1min",
        "requirements": {
            "data_dependencies": [
                {"symbol": "AAPL", "timeframe": "1min", "history_bars": 50},
            ],
        },
    }


class FakeAlgo:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.tick_count = 0
        self.state = {}
    def on_start(self, config, restored_state):
        self.started = True
        if restored_state:
            self.state = restored_state
    def on_tick(self, ctx):
        self.tick_count += 1
        return []
    def on_stop(self):
        self.stopped = True
        return self.state
    def save_state(self):
        return {"ticks": self.tick_count}
    def on_signal_rejected(self, signal, reason): pass
    def on_trade_executed(self, signal, fill): pass


@pytest.mark.asyncio
async def test_bring_up_loads_algorithm_and_starts_runner(tmp_path, monkeypatch):
    from worker import live_instance_runtime, package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)

    monkeypatch.setattr(
        live_instance_runtime.package_cache, "ensure",
        AsyncMock(return_value=tmp_path / "fake"),
    )
    monkeypatch.setattr(
        live_instance_runtime.package_cache, "load_algorithm_class",
        MagicMock(return_value=FakeAlgo),
    )
    monkeypatch.setattr(
        live_instance_runtime, "make_broker_adapter",
        MagicMock(return_value=MagicMock(get_account_info=MagicMock(return_value={"cash": 100, "portfolio_value": 150, "buying_power": 100}))),
    )

    agent = _make_agent()
    runtime = await live_instance_runtime.LiveInstanceRuntime.bring_up(
        agent=agent, instance_id="d1", run_id="r1",
        algorithm_id="algo-1", algorithm_commit_sha="sha-abc",
        manifest=_make_manifest(),
        config={"foo": "bar"}, persisted_state=None,
        broker_type="alpaca", environment="paper",
        credentials={"api_key": "k", "secret_key": "s"},
        data_client=AsyncMock(),
    )
    assert runtime.is_healthy()
    assert runtime._runner._algorithm.started
    assert runtime._runner._algorithm.state == {}


@pytest.mark.asyncio
async def test_bring_up_passes_persisted_state_to_algorithm(tmp_path, monkeypatch):
    from worker import live_instance_runtime, package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(live_instance_runtime.package_cache, "ensure",
                        AsyncMock(return_value=tmp_path / "fake"))
    monkeypatch.setattr(live_instance_runtime.package_cache, "load_algorithm_class",
                        MagicMock(return_value=FakeAlgo))
    monkeypatch.setattr(live_instance_runtime, "make_broker_adapter",
                        MagicMock(return_value=MagicMock()))
    runtime = await live_instance_runtime.LiveInstanceRuntime.bring_up(
        agent=_make_agent(), instance_id="d1", run_id="r1",
        algorithm_id="algo-1", algorithm_commit_sha="sha-abc",
        manifest=_make_manifest(),
        config={}, persisted_state={"last_signal": "buy"},
        broker_type="alpaca", environment="paper",
        credentials={"api_key": "k", "secret_key": "s"},
        data_client=AsyncMock(),
    )
    assert runtime._runner._algorithm.state == {"last_signal": "buy"}


@pytest.mark.asyncio
async def test_shut_down_calls_algorithm_on_stop(tmp_path, monkeypatch):
    from worker import live_instance_runtime, package_cache
    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(live_instance_runtime.package_cache, "ensure",
                        AsyncMock(return_value=tmp_path / "fake"))
    monkeypatch.setattr(live_instance_runtime.package_cache, "load_algorithm_class",
                        MagicMock(return_value=FakeAlgo))
    monkeypatch.setattr(live_instance_runtime, "make_broker_adapter",
                        MagicMock(return_value=MagicMock()))
    runtime = await live_instance_runtime.LiveInstanceRuntime.bring_up(
        agent=_make_agent(), instance_id="d1", run_id="r1",
        algorithm_id="algo-1", algorithm_commit_sha="sha-abc",
        manifest=_make_manifest(), config={}, persisted_state=None,
        broker_type="alpaca", environment="paper",
        credentials={"api_key": "k", "secret_key": "s"},
        data_client=AsyncMock(),
    )
    final = await runtime.shut_down()
    assert runtime._runner._algorithm.stopped
    assert isinstance(final, dict)
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/worker/test_live_instance_runtime.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `worker/live_instance_runtime.py`**

```python
"""LiveInstanceRuntime — the runtime hosting one running algorithm instance.

Composes M2's building blocks (package_cache, RollingDataBuffer,
CachingBrokerAdapter, AlgorithmRunner, LiveObserver, TickProcessor) into a
single object the worker holds in self._running_instances[inst_id]. Owns the
instance's lifecycle (bring_up, on_tick_batch_entry, shut_down).
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime
from typing import Any, Optional

from worker import package_cache
from worker.adapter_factory import make_broker_adapter
from worker.caching_broker_adapter import CachingBrokerAdapter
from worker.context import LiveTickContext
from worker.live_observer import LiveObserver
from worker.rolling_data_buffer import RollingDataBuffer
from worker.runner import AlgorithmRunner, RunnerState
from worker.tick_loop import TickProcessor

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 5


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _last_n_traceback_lines(n: int) -> str:
    return "\n".join(traceback.format_exc().splitlines()[-n:])


class LiveInstanceRuntime:
    def __init__(
        self,
        *,
        instance_id: str,
        run_id: str,
        runner: AlgorithmRunner,
        broker: CachingBrokerAdapter,
        buffer: RollingDataBuffer,
        observer: LiveObserver,
        tick_processor: TickProcessor,
        agent: Any,
        data_client: Any,
    ) -> None:
        self._instance_id = instance_id
        self._run_id = run_id
        self._runner = runner
        self._broker = broker
        self._buffer = buffer
        self._observer = observer
        self._tick_processor = tick_processor
        self._agent = agent
        self._data_client = data_client
        self._consecutive_failures = 0

    @classmethod
    async def bring_up(
        cls,
        *,
        agent: Any,
        instance_id: str,
        run_id: str,
        algorithm_id: str,
        algorithm_commit_sha: str,
        manifest: dict,
        config: dict,
        persisted_state: Optional[dict],
        broker_type: str,
        environment: str,
        credentials: dict,
        data_client: Any,
    ) -> "LiveInstanceRuntime":
        # 1. Ensure algorithm package is cached locally.
        pkg_dir = await package_cache.ensure(
            agent=agent, algorithm_id=algorithm_id, commit_sha=algorithm_commit_sha,
        )
        # 2. Import the algorithm class.
        algo_cls = package_cache.load_algorithm_class(
            pkg_dir=pkg_dir,
            entry_point=manifest["entry_point"],
            class_name=manifest["class_name"],
        )
        algo = algo_cls()
        # 3. Build the broker adapter and wrap with the TTL cache.
        raw_broker = make_broker_adapter(broker_type, environment, credentials)
        broker = CachingBrokerAdapter(raw_broker, account_state_ttl=30)
        # 4. Build rolling data buffer from manifest.requirements.data_dependencies.
        data_deps = (manifest.get("requirements") or {}).get("data_dependencies") or []
        buffer = RollingDataBuffer(data_deps)
        await buffer.backfill(data_client)
        # 5. Build the AlgorithmRunner (also wires the algo log shipper from M4.4).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        runner = AlgorithmRunner(
            instance_id=instance_id,
            algorithm=algo,
            config=config,
            restored_state=persisted_state,
            agent=agent,
            loop=loop,
        )
        runner.start()  # calls algo.on_start(config, restored_state)
        # 6. Build the LiveObserver.
        observer = LiveObserver(
            agent=agent, broker=broker,
            instance_id=instance_id, run_id=run_id,
        )
        # 7. Build the TickProcessor with the live_observer wired in.
        tick_processor = TickProcessor(
            runner=runner,
            broker=broker,
            data_client=data_client,
            coordinator_client=agent,
            live_observer=observer,
        )
        return cls(
            instance_id=instance_id, run_id=run_id,
            runner=runner, broker=broker, buffer=buffer,
            observer=observer, tick_processor=tick_processor,
            agent=agent, data_client=data_client,
        )

    def is_healthy(self) -> bool:
        return (
            self._runner.state == RunnerState.RUNNING
            and self._consecutive_failures < MAX_CONSECUTIVE_FAILURES
        )

    async def on_tick_batch_entry(self, entry: dict) -> None:
        # 1. Merge pushed delta into rolling buffer.
        data = entry.get("data") or {}
        if data:
            self._buffer.ingest(data)
        ts = _parse_iso(entry["timestamp"])
        # 2. Process the tick.
        try:
            await self._tick_processor.process_tick(ts)
            self._consecutive_failures = 0
        except Exception as e:
            self._consecutive_failures += 1
            tb = _last_n_traceback_lines(20)
            await self._agent.send_activity_event(
                self._instance_id, "algo_exception", severity="error",
                payload={"error": str(e), "traceback_tail": tb},
            )
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                await self._agent.send_event(
                    "instance_error", self._instance_id,
                    payload={"reason": f"{MAX_CONSECUTIVE_FAILURES} consecutive tick failures"},
                )
                await self.shut_down()
                return
        # 3. Emit equity sample (LiveObserver fetches account state via broker).
        try:
            await self._observer.on_tick(timestamp=ts.isoformat())
        except Exception:
            logger.exception("Equity-sample emission failed for %s", self._instance_id)
        # 4. Checkpoint state after every tick (best-effort).
        try:
            state = self._runner.save_state()
            await self._agent.send_state_checkpoint(self._instance_id, state)
        except Exception:
            logger.exception("Checkpoint failed for %s", self._instance_id)

    async def shut_down(self) -> dict:
        try:
            return self._runner.stop()  # calls algo.on_stop()
        except Exception:
            logger.exception("Algorithm on_stop raised; using save_state fallback")
            try:
                return self._runner.save_state()
            except Exception:
                logger.exception("save_state also failed")
                return {}
```

- [ ] **Step 4: Run + commit**

Run: `pytest tests/worker/test_live_instance_runtime.py tests/worker/ -v`
Expected: all pass.

```bash
git add worker/live_instance_runtime.py tests/worker/test_live_instance_runtime.py
git commit -m "feat(worker): LiveInstanceRuntime composes bring-up, tick, shutdown lifecycle"
```

### Task 3.2: Replace `_handle_start_instance` + `_handle_stop_instance` + add `_handle_tick_batch`

**Files:**
- Modify: `worker/agent.py`
- Modify: `worker/main.py` (surface `coordinator_http_url` and `worker_install_token` on the agent)
- Modify: `worker/config.py` (add `worker_install_token` if absent)
- Modify: `tests/worker/test_agent_handlers.py` (extend)

- [ ] **Step 1: Read `worker/config.py` and `worker/main.py`**

Note: `WorkerConfig` already has `worker_id`, `worker_name`, `coordinator_url`, `coordinator_http_url`, `heartbeat_interval`, `data_cache_ttl`. Add `worker_install_token` if missing.

`worker/main.py` already builds `WorkerAgent(...)` with `worker_id`, `worker_name`, `websocket`, `tailscale_ip`. We'll pass `coordinator_http_url` and `worker_install_token`.

- [ ] **Step 2: Failing test**

In `tests/worker/test_agent_handlers.py`, append:

```python
@pytest.mark.asyncio
async def test_handle_start_instance_invokes_runtime_bring_up(monkeypatch):
    from worker.agent import WorkerAgent
    from worker import live_instance_runtime, package_cache

    fake_runtime = MagicMock()
    fake_runtime.is_healthy = MagicMock(return_value=True)
    monkeypatch.setattr(
        live_instance_runtime.LiveInstanceRuntime, "bring_up",
        AsyncMock(return_value=fake_runtime),
    )

    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
    )
    await agent._handle_start_instance({
        "instance_id": "d1",
        "run_id": "r1",
        "algorithm_id": "algo-1",
        "algorithm_commit_sha": "sha-abc",
        "manifest": {"entry_point": "x.y", "class_name": "Z", "trigger": "bar:1min",
                     "requirements": {"data_dependencies": []}},
        "broker_type": "alpaca",
        "environment": "paper",
        "credentials": {"api_key": "k", "secret_key": "s"},
        "config": {},
        "persisted_state": None,
    })
    assert "d1" in agent._running_instances
    assert agent._running_instances["d1"] is fake_runtime


@pytest.mark.asyncio
async def test_handle_start_instance_emits_instance_error_on_bring_up_failure(monkeypatch):
    from worker.agent import WorkerAgent
    from worker import live_instance_runtime

    monkeypatch.setattr(
        live_instance_runtime.LiveInstanceRuntime, "bring_up",
        AsyncMock(side_effect=RuntimeError("nope")),
    )

    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
    )
    await agent._handle_start_instance({
        "instance_id": "d1", "run_id": "r1",
        "algorithm_id": "algo-1", "algorithm_commit_sha": "sha",
        "manifest": {"entry_point": "x", "class_name": "Z",
                     "requirements": {"data_dependencies": []}},
        "broker_type": "alpaca", "environment": "paper",
        "credentials": {}, "config": {}, "persisted_state": None,
    })
    # Inspect sent messages.
    sent_jsons = [json.loads(c.args[0]) for c in ws.send.call_args_list]
    assert any(m.get("type") == "instance_error" for m in sent_jsons)
    assert "d1" not in agent._running_instances


@pytest.mark.asyncio
async def test_handle_start_instance_idempotent_when_already_healthy(monkeypatch):
    from worker.agent import WorkerAgent
    from worker import live_instance_runtime

    existing = MagicMock()
    existing.is_healthy = MagicMock(return_value=True)

    bring_up = AsyncMock()
    monkeypatch.setattr(
        live_instance_runtime.LiveInstanceRuntime, "bring_up", bring_up,
    )

    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
    )
    agent._running_instances["d1"] = existing
    await agent._handle_start_instance({
        "instance_id": "d1", "run_id": "r1",
        "algorithm_id": "a", "algorithm_commit_sha": "s",
        "manifest": {"entry_point": "x", "class_name": "Z",
                     "requirements": {"data_dependencies": []}},
        "broker_type": "alpaca", "environment": "paper",
        "credentials": {}, "config": {}, "persisted_state": None,
    })
    bring_up.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_tick_batch_dispatches_to_runtimes():
    from worker.agent import WorkerAgent
    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
    )
    runtime_a = MagicMock()
    runtime_a.on_tick_batch_entry = AsyncMock()
    runtime_b = MagicMock()
    runtime_b.on_tick_batch_entry = AsyncMock()
    agent._running_instances["d1"] = runtime_a
    agent._running_instances["d2"] = runtime_b
    await agent._handle_tick_batch({
        "type": "tick_batch",
        "ticks": [
            {"instance_id": "d1", "run_id": "r1", "timestamp": "2026-05-16T12:00:00Z"},
            {"instance_id": "d2", "run_id": "r2", "timestamp": "2026-05-16T12:00:00Z"},
            {"instance_id": "unknown", "timestamp": "..."},
        ],
    })
    # Spawn happens async via create_task; give it a moment.
    await asyncio.sleep(0.01)
    runtime_a.on_tick_batch_entry.assert_awaited_once()
    runtime_b.on_tick_batch_entry.assert_awaited_once()
```

Make sure `json`, `asyncio`, and `MagicMock`/`AsyncMock` are imported at the top of the test file.

- [ ] **Step 3: Run, verify fail**

Run: `pytest tests/worker/test_agent_handlers.py -v`
Expected: new tests fail.

- [ ] **Step 4: Update `worker/config.py`**

Read the file. If `worker_install_token` doesn't already exist on `WorkerConfig`, add it:

```python
# WorkerConfig is likely loaded from env vars. Add a field reading WORKER_TOKEN
# (which the install script already sets in the systemd unit).
worker_install_token: str = ""
```

In the env-loading section (where `WORKER_ID`, `WORKER_NAME` etc. are read):
```python
worker_install_token = os.environ.get("WORKER_TOKEN", "")
```

- [ ] **Step 5: Update `worker/agent.py`**

Add new constructor parameters `coordinator_http_url: str = ""` and `worker_install_token: str = ""`, save them as `self.coordinator_http_url` and `self.worker_install_token`.

Replace `_handle_start_instance` with:

```python
async def _handle_start_instance(self, message: dict) -> None:
    from worker.live_instance_runtime import LiveInstanceRuntime

    instance_id = message["instance_id"]
    # Idempotent: already healthy → no-op.
    existing = self._running_instances.get(instance_id)
    if existing is not None and getattr(existing, "is_healthy", lambda: False)():
        logger.info("Ignoring duplicate start_instance for %s (already healthy)", instance_id)
        return
    if existing is not None:
        try:
            await existing.shut_down()
        except Exception:
            logger.exception("Failed to shut down zombie runtime for %s", instance_id)
        self._running_instances.pop(instance_id, None)

    try:
        runtime = await LiveInstanceRuntime.bring_up(
            agent=self,
            instance_id=instance_id,
            run_id=message["run_id"],
            algorithm_id=message["algorithm_id"],
            algorithm_commit_sha=message["algorithm_commit_sha"],
            manifest=message["manifest"],
            config=message.get("config") or {},
            persisted_state=message.get("persisted_state"),
            broker_type=message["broker_type"],
            environment=message["environment"],
            credentials=message["credentials"],
            data_client=self._data_client,
        )
    except Exception as e:
        logger.exception("Failed to bring up instance %s", instance_id)
        await self.send_event("instance_error", instance_id, payload={"error": str(e)})
        await self.send_activity_event(
            instance_id, "instance_error", severity="error",
            payload={"error": str(e)},
        )
        return

    self._running_instances[instance_id] = runtime
    await self.send_event("instance_started", instance_id)
    await self.send_activity_event(instance_id, "instance_started", severity="info")
    logger.info("Started instance %s", instance_id)
```

Replace `_handle_stop_instance` with:

```python
async def _handle_stop_instance(self, message: dict) -> None:
    instance_id = message["instance_id"]
    runtime = self._running_instances.pop(instance_id, None)
    if runtime is not None:
        try:
            final_state = await runtime.shut_down()
            await self.send_state_checkpoint(instance_id, final_state)
        except Exception:
            logger.exception("Error shutting down instance %s", instance_id)
    await self.send_event("instance_stopped", instance_id)
    await self.send_activity_event(instance_id, "instance_stopped", severity="info")
    logger.info("Stopped instance %s", instance_id)
```

Add a new `_handle_tick_batch` handler and register it:

```python
def register_handlers(self) -> None:
    self.router.register("start_instance", self._handle_start_instance)
    self.router.register("stop_instance", self._handle_stop_instance)
    self.router.register("heartbeat_ack", self._handle_heartbeat_ack)
    self.router.register("tick_batch", self._handle_tick_batch)  # NEW

async def _handle_tick_batch(self, message: dict) -> None:
    import asyncio as _asyncio
    for entry in (message.get("ticks") or []):
        inst_id = entry.get("instance_id")
        runtime = self._running_instances.get(inst_id)
        if runtime is None:
            logger.debug("tick_batch entry for unknown instance %s; ignoring", inst_id)
            continue
        # Per-instance task: a slow algorithm doesn't block sibling instances.
        _asyncio.create_task(runtime.on_tick_batch_entry(entry))
```

The agent needs a `_data_client` attribute for `LiveInstanceRuntime.bring_up`. If the agent doesn't already hold one, add to the constructor: `data_client: Any = None` and `self._data_client = data_client`.

- [ ] **Step 6: Update `worker/main.py`**

In `run_worker`, change the `WorkerAgent` construction to pass the new params:

```python
agent = WorkerAgent(
    worker_id=config.worker_id,
    worker_name=config.worker_name,
    websocket=websocket,
    tailscale_ip=tailscale_ip,
    coordinator_http_url=config.coordinator_http_url,
    worker_install_token=config.worker_install_token,
    data_client=data_client,
)
```

- [ ] **Step 7: Run tests + regression check**

Run: `pytest tests/worker/ -v`
Expected: all pass; existing test_activity_emit tests for `_handle_start_instance` will need updating to pass the new payload fields. If any existing test breaks because the old start_instance handler didn't require `algorithm_id`/`run_id`/etc., update it to provide those fields (or to mock `LiveInstanceRuntime.bring_up`).

- [ ] **Step 8: Commit**

```bash
git add worker/agent.py worker/main.py worker/config.py tests/worker/test_agent_handlers.py
git commit -m "feat(worker): _handle_start_instance uses LiveInstanceRuntime; add _handle_tick_batch"
```

---

## Milestone 4 — Coordinator TickScheduler + Aggregator Subscriber API

### Task 4.1: `live_feed_aggregator` subscribe/dispatch API

**Files:**
- Modify: `coordinator/services/live_feed_aggregator.py`
- Modify: `tests/coordinator/services/test_live_feed_aggregator.py` (extend)

- [ ] **Step 1: Read existing aggregator structure**

The class manages one async task per `LiveSubscription(broker, symbol)`. It writes ticks + 1-min bars to parquet. We add subscriber callbacks fired from the existing bar-flush path.

- [ ] **Step 2: Failing test**

Append to `tests/coordinator/services/test_live_feed_aggregator.py`:

```python
@pytest.mark.asyncio
async def test_subscribe_bars_receives_callback_on_dispatch():
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator

    # Use a minimal aggregator instance — we're just testing the subscriber API.
    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}

    received: list = []
    async def cb(bar):
        received.append(bar)
    agg.subscribe_bars("alpaca", "AAPL", "1min", cb)
    await agg._dispatch_bar("alpaca", "AAPL", "1min", {"close": 100.0})
    assert received == [{"close": 100.0}]


@pytest.mark.asyncio
async def test_unsubscribe_bars_stops_callbacks():
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator

    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}

    received: list = []
    async def cb(bar):
        received.append(bar)
    agg.subscribe_bars("alpaca", "AAPL", "1min", cb)
    agg.unsubscribe_bars("alpaca", "AAPL", "1min", cb)
    await agg._dispatch_bar("alpaca", "AAPL", "1min", {"close": 100.0})
    assert received == []


@pytest.mark.asyncio
async def test_subscribe_events_receives_callback_on_dispatch():
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator

    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}

    received: list = []
    async def cb(evt):
        received.append(evt)
    agg.subscribe_events("alpaca", "AAPL", cb)
    await agg._dispatch_event("alpaca", "AAPL", {"price": 100.0, "size": 10})
    assert len(received) == 1


@pytest.mark.asyncio
async def test_subscriber_exception_does_not_break_other_subscribers():
    from coordinator.services.live_feed_aggregator import LiveFeedAggregator

    agg = LiveFeedAggregator.__new__(LiveFeedAggregator)
    agg._bar_subscribers = {}
    agg._event_subscribers = {}

    received: list = []
    async def bad_cb(bar):
        raise RuntimeError("boom")
    async def good_cb(bar):
        received.append(bar)
    agg.subscribe_bars("alpaca", "AAPL", "1min", bad_cb)
    agg.subscribe_bars("alpaca", "AAPL", "1min", good_cb)
    await agg._dispatch_bar("alpaca", "AAPL", "1min", {"close": 100.0})
    assert received == [{"close": 100.0}]
```

- [ ] **Step 3: Run, verify fail**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator.py -v -k "subscribe or dispatch"`
Expected: tests fail.

- [ ] **Step 4: Implement subscriber API in aggregator**

Edit `coordinator/services/live_feed_aggregator.py`. In `LiveFeedAggregator.__init__`, add:

```python
self._bar_subscribers: dict[tuple[str, str, str], set[Callable]] = {}
self._event_subscribers: dict[tuple[str, str], set[Callable]] = {}
```

Add public methods on the class:

```python
def subscribe_bars(self, broker: str, symbol: str, timeframe: str, callback: Callable) -> None:
    self._bar_subscribers.setdefault((broker, symbol, timeframe), set()).add(callback)

def unsubscribe_bars(self, broker: str, symbol: str, timeframe: str, callback: Callable) -> None:
    s = self._bar_subscribers.get((broker, symbol, timeframe))
    if s:
        s.discard(callback)
        if not s:
            self._bar_subscribers.pop((broker, symbol, timeframe), None)

def subscribe_events(self, broker: str, symbol: str, callback: Callable) -> None:
    self._event_subscribers.setdefault((broker, symbol), set()).add(callback)

def unsubscribe_events(self, broker: str, symbol: str, callback: Callable) -> None:
    s = self._event_subscribers.get((broker, symbol))
    if s:
        s.discard(callback)
        if not s:
            self._event_subscribers.pop((broker, symbol), None)

async def _dispatch_bar(self, broker: str, symbol: str, timeframe: str, bar: dict) -> None:
    for cb in list(self._bar_subscribers.get((broker, symbol, timeframe), ())):
        try:
            await cb(bar)
        except Exception:
            logger.exception("Bar subscriber failed for %s/%s/%s", broker, symbol, timeframe)

async def _dispatch_event(self, broker: str, symbol: str, event: dict) -> None:
    for cb in list(self._event_subscribers.get((broker, symbol), ())):
        try:
            await cb(event)
        except Exception:
            logger.exception("Event subscriber failed for %s/%s", broker, symbol)
```

Find where the existing bar-flush path runs (around the 1-min boundary). After the parquet write succeeds, also call `await self._dispatch_bar(broker, symbol, "1min", bar_dict)`. If the existing flow doesn't have an awaitable context at that point, schedule via `asyncio.create_task(self._dispatch_bar(...))` — pick what fits the existing structure. **DO NOT block the bar-write hot path on subscriber latency**.

Similarly, in the trade/quote ingestion loop, after appending to the in-memory buffer, call `asyncio.create_task(self._dispatch_event(broker, symbol, event))`.

- [ ] **Step 5: Run tests + regression check**

Run: `pytest tests/coordinator/services/test_live_feed_aggregator.py -v`
Expected: all pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/live_feed_aggregator.py tests/coordinator/services/test_live_feed_aggregator.py
git commit -m "feat(coordinator): live_feed_aggregator subscribe/dispatch API for bars + events"
```

### Task 4.2: `MarketClock` helper

**Files:**
- Create: `coordinator/services/market_clock.py`
- Create: `tests/coordinator/services/test_market_clock.py`

- [ ] **Step 1: Failing tests**

```python
# tests/coordinator/services/test_market_clock.py
from datetime import datetime, timezone, timedelta
import pytest


def _et_to_utc(year, month, day, hour, minute):
    """Helper: convert ET wall time to UTC (assuming EDT for ~Mar-Nov, EST otherwise).

    For simplicity in tests we just construct UTC datetimes corresponding to
    well-known ET market boundary moments.
    """
    # 13:30 UTC = 09:30 EDT (during DST); use this for Spring-Fall dates.
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_equities_open_during_regular_hours():
    from coordinator.services.market_clock import is_market_open
    # 2026-05-15 (Fri), 14:00 UTC == 10:00 EDT == during 09:30-16:00.
    ts = datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc)
    assert is_market_open("equities", ts)


def test_equities_closed_at_night():
    from coordinator.services.market_clock import is_market_open
    # 2026-05-15 23:00 UTC == 19:00 EDT, past 16:00 close.
    ts = datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc)
    assert not is_market_open("equities", ts)


def test_equities_closed_on_weekend():
    from coordinator.services.market_clock import is_market_open
    ts = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)  # Saturday
    assert not is_market_open("equities", ts)


def test_equities_closed_on_holiday():
    from coordinator.services.market_clock import is_market_open
    # 2026-01-01 New Year's Day (Thu) — should be closed.
    ts = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
    assert not is_market_open("equities", ts)


def test_unknown_asset_type_returns_true():
    from coordinator.services.market_clock import is_market_open
    ts = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)  # Saturday
    assert is_market_open("crypto", ts)
    assert is_market_open("futures", ts)


def test_equity_options_uses_same_calendar_as_equities():
    from coordinator.services.market_clock import is_market_open
    ts_weekday = datetime(2026, 5, 15, 14, 0, tzinfo=timezone.utc)
    ts_weekend = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)
    assert is_market_open("equity_options", ts_weekday)
    assert not is_market_open("equity_options", ts_weekend)
```

- [ ] **Step 2: Run, fail**

Run: `pytest tests/coordinator/services/test_market_clock.py -v`

- [ ] **Step 3: Implement**

```python
# coordinator/services/market_clock.py
"""US equity market clock — used by interval-trigger algorithms.

v1 only handles US equities and equity_options (same hours). All other
asset types return True (always open) — algorithms for futures/crypto/forex
are responsible for their own time gating until we add proper calendars.

Holidays cover 2024-2026 explicitly. Annual maintenance required.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

US_EQUITIES_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19),
    date(2024, 3, 29), date(2024, 5, 27), date(2024, 6, 19),
    date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28), date(2024, 12, 25),
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
}

EQUITIES_TYPES = {"equities", "equity_options"}


def _to_et(ts_utc: datetime) -> datetime:
    """Convert UTC to America/New_York wall clock.

    Uses zoneinfo if available; falls back to a hardcoded offset only if not.
    """
    try:
        from zoneinfo import ZoneInfo
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=timezone.utc)
        return ts_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # Last-resort fallback assumes EST (no DST). Not great but bounded.
        return (ts_utc - timedelta(hours=5)).replace(tzinfo=None)


def is_market_open(asset_type: str, ts: datetime) -> bool:
    if asset_type not in EQUITIES_TYPES:
        return True
    et = _to_et(ts)
    if et.weekday() >= 5:
        return False
    if et.date() in US_EQUITIES_HOLIDAYS:
        return False
    open_t = time(9, 30)
    close_t = time(16, 0)
    return open_t <= et.time() <= close_t
```

- [ ] **Step 4: Run + commit**

Run: `pytest tests/coordinator/services/test_market_clock.py -v` → 6 passed.

```bash
git add coordinator/services/market_clock.py tests/coordinator/services/test_market_clock.py
git commit -m "feat(coordinator): MarketClock for US equities/options interval-trigger gating"
```

### Task 4.3: `TickScheduler` core

**Files:**
- Create: `coordinator/services/tick_scheduler.py`
- Modify: `coordinator/api/dependencies.py` (add `tick_scheduler` attribute on container)
- Modify: `coordinator/main.py` (instantiate + start in lifespan)
- Create: `tests/coordinator/services/test_tick_scheduler.py`

- [ ] **Step 1: Add `tick_scheduler` to `ServiceContainer`**

In `coordinator/api/dependencies.py`:
- In `TYPE_CHECKING` block: `from coordinator.services.tick_scheduler import TickScheduler`
- In `ServiceContainer.__init__`: `self.tick_scheduler: Optional["TickScheduler"] = None`

- [ ] **Step 2: Failing tests**

```python
# tests/coordinator/services/test_tick_scheduler.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


class _FakeAggregator:
    def __init__(self):
        self.subscribed: list = []
        self.unsubscribed: list = []
    def subscribe_bars(self, broker, symbol, tf, cb):
        self.subscribed.append(("bars", broker, symbol, tf, cb))
    def unsubscribe_bars(self, broker, symbol, tf, cb):
        self.unsubscribed.append(("bars", broker, symbol, tf, cb))
    def subscribe_events(self, broker, symbol, cb):
        self.subscribed.append(("events", broker, symbol, cb))
    def unsubscribe_events(self, broker, symbol, cb):
        self.unsubscribed.append(("events", broker, symbol, cb))


@pytest.mark.asyncio
async def test_start_instance_subscribes_aggregator_for_bar_trigger():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    sched = TickScheduler(aggregator=agg, ws_manager=MagicMock())
    await sched.start_instance({
        "instance_id": "d1", "run_id": "r1", "worker_id": "w1",
        "broker_type": "alpaca", "asset_type": "equities",
        "trigger": "bar:1min",
        "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
    })
    assert any(s[1] == "alpaca" and s[2] == "AAPL" and s[3] == "1min"
               for s in agg.subscribed if s[0] == "bars")


@pytest.mark.asyncio
async def test_stop_instance_unsubscribes():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    sched = TickScheduler(aggregator=agg, ws_manager=MagicMock())
    await sched.start_instance({
        "instance_id": "d1", "run_id": "r1", "worker_id": "w1",
        "broker_type": "alpaca", "asset_type": "equities",
        "trigger": "bar:1min",
        "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
    })
    await sched.stop_instance("d1")
    assert any(u[1] == "alpaca" and u[2] == "AAPL" and u[3] == "1min"
               for u in agg.unsubscribed if u[0] == "bars")


@pytest.mark.asyncio
async def test_bar_close_callback_enqueues_tick_for_worker():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    ws_manager = MagicMock()
    ws_manager.worker_connections = {"w1": MagicMock()}
    sent: list = []
    async def fake_send(msg):
        sent.append(msg)
    ws_manager.worker_connections["w1"].send_json = fake_send
    sched = TickScheduler(aggregator=agg, ws_manager=ws_manager, coalesce_ms=20)
    await sched.start_instance({
        "instance_id": "d1", "run_id": "r1", "worker_id": "w1",
        "broker_type": "alpaca", "asset_type": "equities",
        "trigger": "bar:1min",
        "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
    })
    # Find the subscribed callback and invoke it directly.
    bar_cb = next(s[4] for s in agg.subscribed if s[0] == "bars" and s[2] == "AAPL")
    await bar_cb({"timestamp": "2026-05-16T13:34:00Z", "close": 100.0})
    # Wait for coalescer to drain (20 ms window + slack).
    await asyncio.sleep(0.1)
    assert sent
    msg = sent[0]
    assert msg["type"] == "tick_batch"
    assert any(t["instance_id"] == "d1" for t in msg["ticks"])


@pytest.mark.asyncio
async def test_multiple_ticks_for_same_worker_coalesce_into_one_batch():
    from coordinator.services.tick_scheduler import TickScheduler

    agg = _FakeAggregator()
    ws_manager = MagicMock()
    ws_manager.worker_connections = {"w1": MagicMock()}
    sent: list = []
    async def fake_send(msg):
        sent.append(msg)
    ws_manager.worker_connections["w1"].send_json = fake_send
    sched = TickScheduler(aggregator=agg, ws_manager=ws_manager, coalesce_ms=30)
    for inst_id in ("d1", "d2"):
        await sched.start_instance({
            "instance_id": inst_id, "run_id": f"r-{inst_id}", "worker_id": "w1",
            "broker_type": "alpaca", "asset_type": "equities",
            "trigger": "bar:1min",
            "symbols": [{"symbol": "AAPL", "timeframe": "1min"}],
        })
    bar_cbs = [s[4] for s in agg.subscribed if s[0] == "bars" and s[2] == "AAPL"]
    # Fire both at almost the same instant.
    for cb in bar_cbs:
        await cb({"timestamp": "2026-05-16T13:34:00Z", "close": 100.0})
    await asyncio.sleep(0.15)
    assert len(sent) == 1
    assert len(sent[0]["ticks"]) == 2
```

- [ ] **Step 3: Run, fail**

Run: `pytest tests/coordinator/services/test_tick_scheduler.py -v`

- [ ] **Step 4: Implement `coordinator/services/tick_scheduler.py`**

```python
"""TickScheduler — per-instance tick orchestration with per-worker batching.

For each running AlgorithmInstance, subscribes to the live_feed_aggregator
according to the algorithm's manifest trigger (bar:tf, event, interval:Ns)
and enqueues a tick payload onto the per-worker outbound coalescer queue.
The coalescer drains every coalesce_ms (default 10ms), packing all pending
ticks for that worker into a single `tick_batch` ws message.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(r"^interval:(\d+)([smh])$")
_INTERVAL_MULTS = {"s": 1, "m": 60, "h": 3600}


def _parse_interval_seconds(trigger: str) -> int:
    m = _INTERVAL_RE.match(trigger)
    if not m:
        raise ValueError(f"Not an interval trigger: {trigger!r}")
    return int(m.group(1)) * _INTERVAL_MULTS[m.group(2)]


class _WorkerOutbound:
    """Per-worker outbound queue + drain task."""

    def __init__(self, worker_id: str, ws_manager: Any, coalesce_ms: int) -> None:
        self.worker_id = worker_id
        self._ws_manager = ws_manager
        self._queue: asyncio.Queue = asyncio.Queue()
        self._coalesce_s = coalesce_ms / 1000.0
        self._drain_task: Optional[asyncio.Task] = None

    def ensure_running(self) -> None:
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def enqueue(self, tick: dict) -> None:
        await self._queue.put(tick)
        self.ensure_running()

    async def _drain_loop(self) -> None:
        try:
            while True:
                first = await self._queue.get()
                batch = [first]
                deadline = asyncio.get_running_loop().time() + self._coalesce_s
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        nxt = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                        batch.append(nxt)
                    except asyncio.TimeoutError:
                        break
                ws = self._ws_manager.worker_connections.get(self.worker_id)
                if ws is None:
                    logger.debug("Dropping batch for offline worker %s (%d ticks)",
                                 self.worker_id, len(batch))
                    continue
                try:
                    await ws.send_json({"type": "tick_batch", "ticks": batch})
                except Exception:
                    logger.exception("Failed to send tick_batch to worker %s", self.worker_id)
        except asyncio.CancelledError:
            return

    async def shutdown(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):
                pass


class _InstanceContext:
    def __init__(
        self,
        *,
        instance_id: str,
        run_id: str,
        worker_id: str,
        broker_type: str,
        asset_type: str,
        trigger: str,
        symbols: list[dict],
        scheduler: "TickScheduler",
    ) -> None:
        self.instance_id = instance_id
        self.run_id = run_id
        self.worker_id = worker_id
        self.broker_type = broker_type
        self.asset_type = asset_type
        self.trigger = trigger
        self.symbols = symbols
        self._scheduler = scheduler
        self._subscriptions: list = []
        self._interval_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self.trigger.startswith("bar:"):
            tf = self.trigger.split(":", 1)[1]
            for dep in self.symbols:
                sym = dep.get("symbol")
                if not sym:
                    continue
                cb = self._make_bar_callback(sym, tf)
                self._scheduler._aggregator.subscribe_bars(
                    self.broker_type, sym, tf, cb,
                )
                self._subscriptions.append(("bars", self.broker_type, sym, tf, cb))
        elif self.trigger == "event":
            for dep in self.symbols:
                sym = dep.get("symbol")
                if not sym:
                    continue
                cb = self._make_event_callback(sym)
                self._scheduler._aggregator.subscribe_events(
                    self.broker_type, sym, cb,
                )
                self._subscriptions.append(("events", self.broker_type, sym, cb))
        elif self.trigger.startswith("interval:"):
            secs = _parse_interval_seconds(self.trigger)
            self._interval_task = asyncio.create_task(self._interval_loop(secs))
        else:
            raise ValueError(f"Unknown trigger {self.trigger!r}")

    def _make_bar_callback(self, symbol: str, tf: str):
        async def cb(bar: dict) -> None:
            tick = {
                "instance_id": self.instance_id,
                "run_id": self.run_id,
                "timestamp": bar.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "trigger_kind": "bar",
                "trigger_meta": {"timeframe": tf},
                "data": {symbol: {"timeframe": tf, "bars": [bar]}},
            }
            await self._scheduler._enqueue_tick(self.worker_id, tick)
        return cb

    def _make_event_callback(self, symbol: str):
        async def cb(event: dict) -> None:
            tick = {
                "instance_id": self.instance_id,
                "run_id": self.run_id,
                "timestamp": event.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "trigger_kind": "event",
                "trigger_meta": {},
                "data": {symbol: {"event": event}},
            }
            await self._scheduler._enqueue_tick(self.worker_id, tick)
        return cb

    async def _interval_loop(self, interval_s: int) -> None:
        from coordinator.services.market_clock import is_market_open
        try:
            while True:
                if is_market_open(self.asset_type, datetime.now(timezone.utc)):
                    tick = {
                        "instance_id": self.instance_id,
                        "run_id": self.run_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "trigger_kind": "interval",
                        "trigger_meta": {"seconds": interval_s},
                        "data": {},
                    }
                    await self._scheduler._enqueue_tick(self.worker_id, tick)
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        for sub in self._subscriptions:
            kind = sub[0]
            try:
                if kind == "bars":
                    _, broker, sym, tf, cb = sub
                    self._scheduler._aggregator.unsubscribe_bars(broker, sym, tf, cb)
                elif kind == "events":
                    _, broker, sym, cb = sub
                    self._scheduler._aggregator.unsubscribe_events(broker, sym, cb)
            except Exception:
                logger.exception("Failed to unsubscribe %r", sub)
        self._subscriptions.clear()
        if self._interval_task is not None:
            self._interval_task.cancel()
            try:
                await self._interval_task
            except (asyncio.CancelledError, Exception):
                pass
            self._interval_task = None


class TickScheduler:
    def __init__(self, *, aggregator: Any, ws_manager: Any, coalesce_ms: Optional[int] = None) -> None:
        self._aggregator = aggregator
        self._ws_manager = ws_manager
        self._coalesce_ms = coalesce_ms if coalesce_ms is not None else int(
            os.environ.get("QT_TICK_COALESCE_WINDOW_MS", "10")
        )
        self._instances: dict[str, _InstanceContext] = {}
        self._worker_outbound: dict[str, _WorkerOutbound] = {}

    async def start_instance(self, spec: dict) -> None:
        """spec keys: instance_id, run_id, worker_id, broker_type, asset_type,
        trigger, symbols (list of data_dependencies dicts)."""
        inst_id = spec["instance_id"]
        if inst_id in self._instances:
            await self.stop_instance(inst_id)
        ctx = _InstanceContext(
            instance_id=inst_id,
            run_id=spec["run_id"],
            worker_id=spec["worker_id"],
            broker_type=spec["broker_type"],
            asset_type=spec.get("asset_type", "equities"),
            trigger=spec["trigger"],
            symbols=spec.get("symbols") or [],
            scheduler=self,
        )
        await ctx.start()
        self._instances[inst_id] = ctx
        # Lazily create the worker's outbound queue.
        self._worker_outbound.setdefault(
            spec["worker_id"],
            _WorkerOutbound(spec["worker_id"], self._ws_manager, self._coalesce_ms),
        )

    async def stop_instance(self, instance_id: str) -> None:
        ctx = self._instances.pop(instance_id, None)
        if ctx is not None:
            await ctx.stop()

    async def drop_worker(self, worker_id: str) -> None:
        """Called when a worker disconnects. Cancels per-instance subs for
        all instances on that worker and shuts down the outbound queue."""
        to_stop = [iid for iid, ctx in self._instances.items() if ctx.worker_id == worker_id]
        for iid in to_stop:
            await self.stop_instance(iid)
        outbound = self._worker_outbound.pop(worker_id, None)
        if outbound is not None:
            await outbound.shutdown()

    async def _enqueue_tick(self, worker_id: str, tick: dict) -> None:
        outbound = self._worker_outbound.get(worker_id)
        if outbound is None:
            # Race: instance started before outbound was created.
            outbound = _WorkerOutbound(worker_id, self._ws_manager, self._coalesce_ms)
            self._worker_outbound[worker_id] = outbound
        await outbound.enqueue(tick)

    async def shutdown(self) -> None:
        for ctx in list(self._instances.values()):
            await ctx.stop()
        self._instances.clear()
        for outbound in list(self._worker_outbound.values()):
            await outbound.shutdown()
        self._worker_outbound.clear()
```

- [ ] **Step 5: Wire into `coordinator/main.py`**

After `container.live_finalizer = ...`, add:

```python
from coordinator.services.tick_scheduler import TickScheduler
from coordinator.api.websocket import manager as ws_manager

container.tick_scheduler = TickScheduler(
    aggregator=container.live_feed_aggregator,
    ws_manager=ws_manager,
)
```

In the `finally` block, call:
```python
await container.tick_scheduler.shutdown()
```

- [ ] **Step 6: Run + commit**

Run: `pytest tests/coordinator/services/test_tick_scheduler.py tests/coordinator/test_lifespan_wiring.py -v`

```bash
git add coordinator/services/tick_scheduler.py coordinator/api/dependencies.py coordinator/main.py tests/coordinator/services/test_tick_scheduler.py
git commit -m "feat(coordinator): TickScheduler — per-instance scheduling + per-worker coalescing"
```

---

## Milestone 5 — Enrich `start_instance` Payload + Wire Scheduler to Deployments API

### Task 5.1: Enrich `/api/deployments/:id/start` payload

**Files:**
- Modify: `coordinator/api/routes/deployments.py`
- Modify: `tests/coordinator/test_deployments_start_stop.py` (extend)

- [ ] **Step 1: Failing test**

Append to `tests/coordinator/test_deployments_start_stop.py`:

```python
@pytest.mark.asyncio
async def test_start_instance_payload_includes_run_id_manifest_and_credentials(client, db_session):
    from coordinator.api.websocket import manager
    from coordinator.api.dependencies import get_container
    from coordinator.services.encryption import EncryptionService
    import json as _json

    algo = Algorithm(repo_url="https://github.com/x/test-algo", name="A", commit_hash="sha-abc")
    encryption = get_container().encryption
    acct = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials=encryption.encrypt(_json.dumps({"api_key": "k", "secret_key": "s"})),
        supported_asset_types=["equities"],
    )
    worker = Worker(name="W", status="online")
    db_session.add_all([algo, acct, worker])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id,
        worker_id=worker.id, status="stopped",
    )
    db_session.add(inst); await db_session.commit()

    # Stash the manifest on disk so the endpoint can read it.
    import os, pathlib
    pkg_dir = pathlib.Path("data/packages/test-algo")
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "quilt.yaml").write_text(
        """
name: A
type: algorithm
version: 1.0.0
entry_point: test_algo.algorithm
class_name: TestAlgo
trigger: bar:1min
requirements:
  asset_types: [equities]
  data_dependencies:
    - { symbol: "AAPL", timeframe: "1min" }
""".strip()
    )

    fake_ws = MagicMock()
    fake_ws.send_json = AsyncMock()
    manager.register_worker(worker.id, fake_ws)
    try:
        r = await client.post(f"/api/deployments/{inst.id}/start")
    finally:
        manager.worker_connections.pop(worker.id, None)
        # Cleanup
        (pkg_dir / "quilt.yaml").unlink(missing_ok=True)
        pkg_dir.rmdir()

    assert r.status_code == 200
    sent = fake_ws.send_json.call_args.args[0]
    assert sent["type"] == "start_instance"
    assert sent["run_id"]
    assert sent["algorithm_id"] == algo.id
    assert sent["algorithm_commit_sha"] == "sha-abc"
    assert sent["broker_type"] == "alpaca"
    assert sent["environment"] == "paper"
    assert sent["credentials"]["api_key"] == "k"
    assert sent["manifest"]["entry_point"] == "test_algo.algorithm"
    assert sent["manifest"]["trigger"] == "bar:1min"
```

Make sure imports at top of test file include `MagicMock`, `AsyncMock` from `unittest.mock`.

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Update `start_deployment` in `coordinator/api/routes/deployments.py`**

Find the existing `worker_ws.send_json({...})` call. Replace with an enriched payload:

```python
# Load Algorithm + Account for the enriched payload.
algo = (await db.execute(
    select(Algorithm).where(Algorithm.id == inst.algorithm_id)
)).scalar_one()
account = (await db.execute(
    select(Account).where(Account.id == inst.account_id)
)).scalar_one()

# Decrypt credentials.
from coordinator.api.dependencies import get_container as _gc
import json as _json
encryption = _gc().encryption
try:
    creds = _json.loads(encryption.decrypt(account.credentials))
except Exception:
    inst.status = "error"
    run.status = "error"
    await db.commit()
    await _broadcast_status_changed(inst.id, "error", run.id)
    raise HTTPException(status_code=500, detail="Failed to decrypt account credentials")

# Load manifest YAML for the worker.
manifest_dict = _load_manifest_dict(algo)

payload = {
    "type": "start_instance",
    "instance_id": inst.id,
    "run_id": run.id,
    "algorithm_id": algo.id,
    "algorithm_commit_sha": algo.commit_hash,
    "manifest": manifest_dict,
    "broker_type": account.broker_type,
    "environment": account.environment,
    "credentials": creds,
    "config": inst.config_values or {},
    "persisted_state": inst.persisted_state,
}
try:
    await worker_ws.send_json(payload)
except Exception:
    inst.status = "error"
    run.status = "error"
    await db.commit()
    await _broadcast_status_changed(inst.id, "error", run.id)
    raise HTTPException(status_code=502, detail="Failed to reach worker")
```

Add `_load_manifest_dict` helper at the top of the module:

```python
def _load_manifest_dict(algo: Algorithm) -> dict:
    """Read the algorithm's manifest YAML from disk as a plain dict.

    Workers parse this on bring-up. We send the raw YAML structure rather
    than the QuiltManifest dataclass so worker code doesn't have to depend
    on sdk.manifest internals.
    """
    import re
    from pathlib import Path
    import yaml
    m = re.match(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", algo.repo_url or "")
    if not m:
        raise HTTPException(status_code=500, detail=f"Cannot derive package dir from {algo.repo_url!r}")
    pkg_dir = m.group(1).split("/", 1)[1]
    path = Path("data/packages") / pkg_dir / "quilt.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Manifest not on disk for algorithm {algo.id}")
    with open(path) as f:
        return yaml.safe_load(f) or {}
```

- [ ] **Step 4: Wire `tick_scheduler.start_instance(...)` in the same endpoint**

After successful `worker_ws.send_json(payload)`, before `return {"ok": True, "active_run_id": run.id}`:

```python
container = get_container()
if getattr(container, "tick_scheduler", None) is not None:
    try:
        await container.tick_scheduler.start_instance({
            "instance_id": inst.id,
            "run_id": run.id,
            "worker_id": inst.worker_id,
            "broker_type": account.broker_type,
            "asset_type": (account.supported_asset_types or ["equities"])[0],
            "trigger": manifest_dict.get("trigger", "bar:1min"),
            "symbols": (manifest_dict.get("requirements") or {}).get("data_dependencies") or [],
        })
    except Exception:
        logger.exception("Failed to register instance with TickScheduler")
```

Similarly in `stop_deployment`, after the broadcast call, add:
```python
container = get_container()
if getattr(container, "tick_scheduler", None) is not None:
    try:
        await container.tick_scheduler.stop_instance(deployment_id)
    except Exception:
        logger.exception("Failed to unregister instance from TickScheduler")
```

Make sure `import logging; logger = logging.getLogger(__name__)` is present in the file (likely is).

- [ ] **Step 5: Run + commit**

Run: `pytest tests/coordinator/test_deployments_start_stop.py -v`

```bash
git add coordinator/api/routes/deployments.py tests/coordinator/test_deployments_start_stop.py
git commit -m "feat(api): enrich start_instance payload + register with TickScheduler on start/stop"
```

### Task 5.2: TickScheduler reconciliation on worker reconnect

**Files:**
- Modify: `coordinator/api/websocket.py` (extend heartbeat handler)
- Test: `tests/coordinator/test_websocket_handlers.py` (extend)

- [ ] **Step 1: Failing test**

Append to `tests/coordinator/test_websocket_handlers.py`:

```python
@pytest.mark.asyncio
async def test_worker_reconnect_resends_start_instance_for_running_instances(running_app, db_session):
    """After a worker disconnects then reconnects (sends heartbeat with prior_status='offline'),
    the coordinator should re-send start_instance for every status='running' AlgorithmInstance
    on that worker."""
    from coordinator.database.models import (
        Algorithm, Account, Worker, AlgorithmInstance, AlgorithmRun,
    )
    from coordinator.api.websocket import manager, handle_worker_message
    from coordinator.api.dependencies import get_container

    encryption = get_container().encryption
    import json as _json
    algo = Algorithm(repo_url="https://github.com/x/test-algo", name="A", commit_hash="sha-abc")
    acct = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials=encryption.encrypt(_json.dumps({"api_key": "k", "secret_key": "s"})),
        supported_asset_types=["equities"],
    )
    w = Worker(name="W", status="offline")  # was offline; reconnecting now
    db_session.add_all([algo, acct, w])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=w.id,
        status="running",
    )
    db_session.add(inst); await db_session.flush()
    run = AlgorithmRun(instance_id=inst.id, run_number=1, status="running")
    db_session.add(run)
    inst.active_run_id = run.id
    await db_session.commit()

    # Stash manifest on disk
    import pathlib
    pkg_dir = pathlib.Path("data/packages/test-algo")
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "quilt.yaml").write_text(
        "name: A\ntype: algorithm\nversion: 1.0\nentry_point: a.b\nclass_name: A\n"
        "trigger: bar:1min\nrequirements: {asset_types: [equities], data_dependencies: []}\n"
    )

    worker_ws = FakeWebSocket()
    try:
        # Simulate the heartbeat that arrives on reconnect.
        await handle_worker_message(worker_ws, {
            "type": "heartbeat",
            "worker_id": w.id, "worker_name": w.name,
        })
    finally:
        (pkg_dir / "quilt.yaml").unlink(missing_ok=True)
        try:
            pkg_dir.rmdir()
        except Exception:
            pass

    sent_types = [m.get("type") for m in worker_ws.sent]
    assert "start_instance" in sent_types
    start_msg = next(m for m in worker_ws.sent if m.get("type") == "start_instance")
    assert start_msg["instance_id"] == inst.id
    assert start_msg["run_id"] == run.id
```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Extend the heartbeat handler in `coordinator/api/websocket.py`**

In `handle_worker_message`'s `heartbeat` branch, after the `prior_status != "online"` broadcast, add:

```python
if prior_status != "online":
    # Reconcile: re-send start_instance for every running instance on this worker.
    await _reconcile_worker_instances(worker_id, websocket)
```

Add helper at module level:

```python
async def _reconcile_worker_instances(worker_id: str, worker_ws) -> None:
    """Re-send start_instance to a freshly-reconnected worker for every
    running instance assigned to it."""
    from sqlalchemy import select
    from coordinator.database.models import (
        Algorithm, Account, AlgorithmInstance, AlgorithmRun,
    )
    container = get_container()
    async with container.session_factory() as session:
        result = await session.execute(
            select(AlgorithmInstance).where(
                AlgorithmInstance.worker_id == worker_id,
                AlgorithmInstance.status == "running",
            )
        )
        running_insts = result.scalars().all()
        for inst in running_insts:
            algo = (await session.execute(select(Algorithm).where(Algorithm.id == inst.algorithm_id))).scalar_one()
            account = (await session.execute(select(Account).where(Account.id == inst.account_id))).scalar_one()
            run = None
            if inst.active_run_id:
                run = (await session.execute(select(AlgorithmRun).where(AlgorithmRun.id == inst.active_run_id))).scalar_one_or_none()
            if run is None:
                logger.warning("Skipping reconcile for instance %s: no active_run_id", inst.id)
                continue
            try:
                import json as _json
                encryption = container.encryption
                creds = _json.loads(encryption.decrypt(account.credentials))
            except Exception:
                logger.exception("Reconcile: failed to decrypt creds for %s", inst.id)
                continue
            try:
                manifest_dict = _load_manifest_dict_for_reconcile(algo)
            except Exception:
                logger.exception("Reconcile: failed to load manifest for %s", inst.id)
                continue
            payload = {
                "type": "start_instance",
                "instance_id": inst.id,
                "run_id": run.id,
                "algorithm_id": algo.id,
                "algorithm_commit_sha": algo.commit_hash,
                "manifest": manifest_dict,
                "broker_type": account.broker_type,
                "environment": account.environment,
                "credentials": creds,
                "config": inst.config_values or {},
                "persisted_state": inst.persisted_state,
            }
            try:
                await worker_ws.send_json(payload)
                # Also re-register with the TickScheduler.
                if getattr(container, "tick_scheduler", None) is not None:
                    await container.tick_scheduler.start_instance({
                        "instance_id": inst.id,
                        "run_id": run.id,
                        "worker_id": worker_id,
                        "broker_type": account.broker_type,
                        "asset_type": (account.supported_asset_types or ["equities"])[0],
                        "trigger": manifest_dict.get("trigger", "bar:1min"),
                        "symbols": (manifest_dict.get("requirements") or {}).get("data_dependencies") or [],
                    })
            except Exception:
                logger.exception("Reconcile send_json failed for instance %s", inst.id)


def _load_manifest_dict_for_reconcile(algo) -> dict:
    """Same shape as deployments.py:_load_manifest_dict but local to avoid import cycle."""
    import re
    from pathlib import Path
    import yaml
    m = re.match(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", algo.repo_url or "")
    if not m:
        raise ValueError(f"Cannot derive package dir from {algo.repo_url!r}")
    pkg_dir = m.group(1).split("/", 1)[1]
    path = Path("data/packages") / pkg_dir / "quilt.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Manifest not on disk for algorithm {algo.id}")
    with open(path) as f:
        return yaml.safe_load(f) or {}
```

- [ ] **Step 4: Wire scheduler `drop_worker` into the disconnect handler**

In `handle_worker_disconnect` (added by previous spec's M1.3), after marking the worker offline + broadcasting:

```python
container = get_container()
if getattr(container, "tick_scheduler", None) is not None:
    try:
        await container.tick_scheduler.drop_worker(worker_id)
    except Exception:
        logger.exception("Failed to drop worker %s from TickScheduler", worker_id)
```

- [ ] **Step 5: Run + commit**

Run: `pytest tests/coordinator/test_websocket_handlers.py -v`

```bash
git add coordinator/api/websocket.py tests/coordinator/test_websocket_handlers.py
git commit -m "feat(ws): reconcile running instances on worker reconnect; drop scheduler subs on disconnect"
```

---

## Milestone 6 — End-to-End Sanity

### Task 6.1: Smoke test the full pipeline

**Files:**
- Test: `tests/integration/test_live_execution_e2e.py` (new — first integration test of this kind)

This task verifies the pieces compose correctly. Uses heavy mocking of the broker but real coordinator + a real worker `LiveInstanceRuntime`.

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_live_execution_e2e.py
"""End-to-end smoke test: a tick_batch ws message into the worker results in
an algorithm tick + equity_sample being emitted back."""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_tick_batch_results_in_algo_tick_and_equity_sample_emission(tmp_path, monkeypatch):
    from worker.agent import WorkerAgent
    from worker import live_instance_runtime, package_cache

    monkeypatch.setattr(package_cache, "PACKAGE_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(live_instance_runtime.package_cache, "ensure",
                        AsyncMock(return_value=tmp_path / "fake"))

    class FakeAlgo:
        ticks: list = []
        def on_start(self, config, restored_state): pass
        def on_tick(self, ctx):
            FakeAlgo.ticks.append(ctx.timestamp)
            return []
        def on_stop(self): return {}
        def save_state(self): return {"n": len(FakeAlgo.ticks)}
        def on_signal_rejected(self, *args): pass
        def on_trade_executed(self, *args): pass

    monkeypatch.setattr(live_instance_runtime.package_cache, "load_algorithm_class",
                        MagicMock(return_value=FakeAlgo))
    fake_broker = MagicMock()
    fake_broker.get_account_info = MagicMock(return_value={
        "cash": 100, "portfolio_value": 150, "buying_power": 100,
    })
    fake_broker.get_positions = MagicMock(return_value={})
    monkeypatch.setattr(live_instance_runtime, "make_broker_adapter",
                        MagicMock(return_value=fake_broker))

    sent_jsons = []
    ws = AsyncMock()
    async def fake_send(s):
        sent_jsons.append(json.loads(s))
    ws.send = AsyncMock(side_effect=fake_send)
    data_client = AsyncMock()
    data_client.get_market_data = AsyncMock(return_value=__import__("pandas").DataFrame())

    agent = WorkerAgent(
        worker_id="w1", worker_name="W",
        websocket=ws,
        coordinator_http_url="http://coord:8000",
        worker_install_token="tok",
        data_client=data_client,
    )

    # Bring up the instance.
    await agent._handle_start_instance({
        "instance_id": "d1", "run_id": "r1",
        "algorithm_id": "algo-1", "algorithm_commit_sha": "sha",
        "manifest": {
            "entry_point": "x", "class_name": "F",
            "trigger": "bar:1min",
            "requirements": {"data_dependencies": []},
        },
        "broker_type": "alpaca", "environment": "paper",
        "credentials": {"api_key": "k", "secret_key": "s"},
        "config": {}, "persisted_state": None,
    })
    assert "d1" in agent._running_instances

    # Send a tick_batch.
    await agent._handle_tick_batch({
        "type": "tick_batch",
        "ticks": [{
            "instance_id": "d1", "run_id": "r1",
            "timestamp": "2026-05-16T13:34:00Z",
            "trigger_kind": "bar",
            "trigger_meta": {"timeframe": "1min"},
            "data": {},
        }],
    })
    # Spawned task takes a beat.
    await asyncio.sleep(0.05)

    # Algorithm got a tick.
    assert len(FakeAlgo.ticks) == 1
    # Equity sample was emitted (and state checkpoint).
    types = [m.get("type") for m in sent_jsons]
    assert "equity_sample" in types
    assert "state_checkpoint" in types
```

- [ ] **Step 2: Run + commit**

Run: `pytest tests/integration/test_live_execution_e2e.py -v`
Expected: passes.

```bash
git add tests/integration/test_live_execution_e2e.py
git commit -m "test: end-to-end smoke for live execution (tick → on_tick → equity_sample)"
```

---

## Final Acceptance Checklist

- [ ] `pytest tests/coordinator tests/worker tests/sdk tests/integration --ignore=tests/coordinator/test_backtest_finalizer.py --ignore=tests/coordinator/services/test_backtest_finalizer.py --ignore=tests/coordinator/test_backtest_metrics_qs.py -v` — all green.
- [ ] Manual smoke test: install `simple-ma-crossover` against an Alpaca paper account, start the deployment via the dashboard. Within ~3 seconds the worker logs show "Started instance d1". Within ~60 seconds the deployment page shows an equity sample data point and a runs list entry for run #1.
- [ ] Stop the deployment. The dashboard flips to "Stopped" within ~1 second. No tick_batch messages flow to the worker.
- [ ] Restart the deployment. The same algorithm picks up where its `persisted_state` left off.
- [ ] Crash the worker process. Coordinator marks it offline within 60s. Dashboard shows worker offline + deployment in error state.
- [ ] Restart the worker. Coordinator re-sends start_instance via reconcile; deployment returns to running state; algorithm resumes from last checkpoint.

---

## Spec Coverage Map

| Spec section | Tasks |
|---|---|
| §2 Architecture | (informational; implemented across all tasks) |
| §3 Manifest additions | Task 1.1 |
| §4.1 Per-instance scheduler | Task 4.3 |
| §4.2 Coalescer | Task 4.3 (`_WorkerOutbound`) |
| §4.3 Aggregator subscriber API | Task 4.1 |
| §4.4 Market clock | Task 4.2 |
| §4.5 Lifespan wiring + start/stop | Task 4.3 (lifespan) + Task 5.1 (start/stop) |
| §4.6 start_instance payload | Task 5.1 |
| §4.7 tick_batch payload shape | Task 4.3 |
| §5.1 Slim _handle_start_instance | Task 3.2 |
| §5.2 LiveInstanceRuntime | Task 3.1 |
| §5.3 _handle_tick_batch | Task 3.2 |
| §5.4 package_cache | Task 2.1 |
| §5.5 RollingDataBuffer | Task 2.2 |
| §5.6 LiveTickContext extension | Task 2.4 |
| §5.7 CachingBrokerAdapter | Task 2.3 |
| §5.8 _handle_stop_instance | Task 3.2 |
| §6.1 Package endpoint | Task 1.2 |
| §6.2 Updated start_instance | Task 5.1 |
| §7 Lifecycle flows | Tested in Task 6.1; reconcile in Task 5.2 |
| §8 Failure handling | Algo exception + 5-strike in Task 3.1; broker errors in Task 3.1 (via TickProcessor); disconnect handling in Task 5.2 |
