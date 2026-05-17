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
        # Content comes from a trusted coordinator over a Tailscale-encrypted channel.
        tar.extractall(target)

    logger.info("Cached algorithm %s @ %s to %s", algorithm_id, commit_sha, target)
    return target


def load_algorithm_class(*, pkg_dir: Path, entry_point: str, class_name: str) -> type:
    """Load `class_name` from `entry_point` (e.g. "my_pkg.algorithm") within `pkg_dir`.

    Uses importlib.util.spec_from_file_location so we don't pollute global sys.path
    or risk colliding with other algorithms loaded into the same worker process.

    Falls back to looking one directory deeper, since coordinator tarballs are
    extracted as `<repo_name>/<entry_point>.py` rather than `<entry_point>.py`.
    """
    pkg_dir = Path(pkg_dir)
    module_relpath = Path(entry_point.replace(".", "/") + ".py")
    module_path = pkg_dir / module_relpath
    effective_pkg_dir = pkg_dir

    if not module_path.exists():
        # Walk one level deeper: maybe the tarball extracted into pkg_dir/<repo-name>/
        for sub in pkg_dir.iterdir():
            if sub.is_dir():
                candidate = sub / module_relpath
                if candidate.exists():
                    module_path = candidate
                    effective_pkg_dir = sub
                    break

    if not module_path.exists():
        # Try package init form: <entry_point>/__init__.py
        init_path = pkg_dir / entry_point.replace(".", "/") / "__init__.py"
        if init_path.exists():
            module_path = init_path
            effective_pkg_dir = pkg_dir
        else:
            for sub in pkg_dir.iterdir():
                if sub.is_dir():
                    candidate = sub / entry_point.replace(".", "/") / "__init__.py"
                    if candidate.exists():
                        module_path = candidate
                        effective_pkg_dir = sub
                        break

    if not module_path.exists():
        raise FileNotFoundError(
            f"Algorithm entry_point {entry_point!r} not found in {pkg_dir}"
        )

    spec = importlib.util.spec_from_file_location(
        entry_point, module_path,
        submodule_search_locations=[str(effective_pkg_dir)],
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
