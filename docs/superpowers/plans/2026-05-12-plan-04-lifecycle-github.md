# Plan 4: Algorithm Lifecycle + GitHub Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the coordinator services that manage algorithm packages end-to-end — discovering repos on GitHub, cloning and validating packages, creating isolated virtualenvs, managing algorithm instances (create, start, stop), enforcing account locks, handling scraper dependencies, and coordinating with workers to deploy running algorithms.

**Architecture:** The GitHub service uses PyGithub to query the user's repos and filter by `quilt.yaml` presence. The lifecycle manager orchestrates installation (clone → validate manifest → install deps → record in DB) and instance management (pre-start checks → lock account → start scrapers → send start command to worker → handle stop/error). A package manager handles virtualenv creation and dependency installation. The worker manager bridges coordinator commands to connected workers via WebSocket.

**Tech Stack:** Python 3.11+, PyGithub, subprocess (for git/pip/venv), SQLAlchemy, FastAPI, pytest

---

## File Map

| File | Responsibility |
|------|---------------|
| `coordinator/services/github_service.py` | GitHub API integration — repo discovery, filtering by quilt.yaml |
| `coordinator/services/package_manager.py` | Clone repos, create virtualenvs, install deps, validate packages |
| `coordinator/services/lifecycle.py` | Algorithm instance lifecycle — create, start, stop, error handling, account lock |
| `coordinator/services/scraper_manager.py` | Scraper lifecycle — start/stop based on algorithm dependencies |
| `coordinator/api/routes/github.py` | API endpoints for repo listing and algorithm installation |
| `tests/coordinator/test_github_service.py` | GitHub service tests (mocked API) |
| `tests/coordinator/test_package_manager.py` | Package manager tests (mocked subprocess) |
| `tests/coordinator/test_lifecycle.py` | Lifecycle manager tests |
| `tests/coordinator/test_scraper_manager.py` | Scraper manager tests |
| `tests/coordinator/test_github_api.py` | GitHub API route tests |

---

### Task 1: GitHub Service

**Files:**
- Create: `coordinator/services/github_service.py`
- Create: `tests/coordinator/test_github_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_github_service.py
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from coordinator.services.github_service import GitHubService, RepoInfo


def make_mock_repo(name, full_name, description="", has_quilt_yaml=True):
    repo = MagicMock()
    repo.name = name
    repo.full_name = full_name
    repo.description = description
    repo.html_url = f"https://github.com/{full_name}"
    repo.clone_url = f"https://github.com/{full_name}.git"
    if has_quilt_yaml:
        repo.get_contents.return_value = MagicMock()
    else:
        from github import GithubException
        repo.get_contents.side_effect = GithubException(404, {}, {})
    return repo


def test_list_quilt_repos():
    mock_github = MagicMock()
    mock_user = MagicMock()
    repos = [
        make_mock_repo("algo-1", "user/algo-1", "My algo", True),
        make_mock_repo("not-quilt", "user/not-quilt", "No manifest", False),
        make_mock_repo("scraper-1", "user/scraper-1", "A scraper", True),
    ]
    mock_user.get_repos.return_value = repos
    mock_github.get_user.return_value = mock_user

    service = GitHubService(github_client=mock_github)
    results = service.list_quilt_repos()

    assert len(results) == 2
    assert results[0].name == "algo-1"
    assert results[1].name == "scraper-1"
    assert all(isinstance(r, RepoInfo) for r in results)


def test_list_quilt_repos_empty():
    mock_github = MagicMock()
    mock_user = MagicMock()
    mock_user.get_repos.return_value = []
    mock_github.get_user.return_value = mock_user

    service = GitHubService(github_client=mock_github)
    results = service.list_quilt_repos()
    assert results == []


def test_repo_info_fields():
    mock_github = MagicMock()
    mock_user = MagicMock()
    repo = make_mock_repo("test-algo", "ElectricJack/test-algo", "A test algorithm")
    mock_user.get_repos.return_value = [repo]
    mock_github.get_user.return_value = mock_user

    service = GitHubService(github_client=mock_github)
    results = service.list_quilt_repos()
    assert results[0].full_name == "ElectricJack/test-algo"
    assert results[0].description == "A test algorithm"
    assert results[0].clone_url == "https://github.com/ElectricJack/test-algo.git"


def test_get_repo_clone_url():
    mock_github = MagicMock()
    mock_repo = MagicMock()
    mock_repo.clone_url = "https://github.com/ElectricJack/algo.git"
    mock_github.get_repo.return_value = mock_repo

    service = GitHubService(github_client=mock_github)
    url = service.get_clone_url("ElectricJack/algo")
    assert url == "https://github.com/ElectricJack/algo.git"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_github_service.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/github_service.py
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RepoInfo:
    name: str
    full_name: str
    description: str
    clone_url: str
    html_url: str


class GitHubService:
    def __init__(self, github_client: Any = None, pat: str | None = None) -> None:
        if github_client:
            self._github = github_client
        else:
            from github import Github
            self._github = Github(pat)

    def list_quilt_repos(self) -> list[RepoInfo]:
        user = self._github.get_user()
        results = []
        for repo in user.get_repos():
            try:
                repo.get_contents("quilt.yaml")
                results.append(RepoInfo(
                    name=repo.name,
                    full_name=repo.full_name,
                    description=repo.description or "",
                    clone_url=repo.clone_url,
                    html_url=repo.html_url,
                ))
            except Exception:
                continue
        return results

    def get_clone_url(self, full_name: str) -> str:
        repo = self._github.get_repo(full_name)
        return repo.clone_url
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_github_service.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/github_service.py tests/coordinator/test_github_service.py
git commit -m "feat(coordinator): add GitHub service for repo discovery"
```

---

### Task 2: Package Manager

**Files:**
- Create: `coordinator/services/package_manager.py`
- Create: `tests/coordinator/test_package_manager.py`

Manages cloning repos, creating virtualenvs, installing dependencies, and validating quilt.yaml.

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_package_manager.py
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from coordinator.services.package_manager import PackageManager, PackageError


@pytest.fixture
def packages_dir(tmp_path):
    return str(tmp_path / "packages")


@pytest.fixture
def manager(packages_dir):
    return PackageManager(packages_dir=packages_dir)


def test_package_path(manager, packages_dir):
    path = manager.package_path("momentum-scalper")
    assert path == os.path.join(packages_dir, "momentum-scalper")


def test_venv_path(manager, packages_dir):
    path = manager.venv_path("momentum-scalper")
    assert path == os.path.join(packages_dir, "momentum-scalper", ".venv")


@patch("coordinator.services.package_manager.subprocess")
def test_clone_repo(mock_subprocess, manager, packages_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0)
    manager.clone_repo("https://github.com/user/algo.git", "algo")
    mock_subprocess.run.assert_called_once()
    args = mock_subprocess.run.call_args
    assert "git" in args[0][0]
    assert "clone" in args[0][0]


@patch("coordinator.services.package_manager.subprocess")
def test_clone_repo_failure_raises(mock_subprocess, manager):
    mock_subprocess.run.return_value = MagicMock(returncode=1, stderr="fatal: repo not found")
    with pytest.raises(PackageError, match="Clone failed"):
        manager.clone_repo("https://github.com/user/bad.git", "bad")


@patch("coordinator.services.package_manager.subprocess")
def test_create_venv(mock_subprocess, manager, packages_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0)
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    manager.create_venv("algo")
    assert mock_subprocess.run.called


@patch("coordinator.services.package_manager.subprocess")
def test_install_requirements(mock_subprocess, manager, packages_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0)
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "requirements.txt").write_text("pandas>=2.0\nnumpy\n")
    manager.install_requirements("algo")
    assert mock_subprocess.run.called


def test_install_requirements_no_file_is_noop(manager, packages_dir):
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    manager.install_requirements("algo")


def test_validate_package_missing_manifest(manager, packages_dir):
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    with pytest.raises(PackageError, match="quilt.yaml not found"):
        manager.validate_package("algo")


def test_validate_package_valid(manager, packages_dir):
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "quilt.yaml").write_text(
        "name: algo\ntype: algorithm\nversion: 1.0.0\n"
        "entry_point: algorithm.py\nclass_name: MyAlgo\n"
    )
    (pkg_dir / "algorithm.py").write_text("class MyAlgo: pass\n")
    manifest = manager.validate_package("algo")
    assert manifest["name"] == "algo"
    assert manifest["type"] == "algorithm"


def test_validate_package_missing_entry_point(manager, packages_dir):
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "quilt.yaml").write_text(
        "name: algo\ntype: algorithm\nversion: 1.0.0\n"
        "entry_point: missing.py\nclass_name: MyAlgo\n"
    )
    with pytest.raises(PackageError, match="Entry point"):
        manager.validate_package("algo")


@patch("coordinator.services.package_manager.subprocess")
def test_update_package(mock_subprocess, manager, packages_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="abc123\n")
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    commit_hash = manager.update_package("algo")
    assert commit_hash == "abc123"


@patch("coordinator.services.package_manager.subprocess")
def test_get_commit_hash(mock_subprocess, manager, packages_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="def456\n")
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    assert manager.get_commit_hash("algo") == "def456"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_package_manager.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/package_manager.py
import logging
import os
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class PackageError(Exception):
    pass


class PackageManager:
    def __init__(self, packages_dir: str) -> None:
        self._packages_dir = packages_dir

    def package_path(self, name: str) -> str:
        return os.path.join(self._packages_dir, name)

    def venv_path(self, name: str) -> str:
        return os.path.join(self._packages_dir, name, ".venv")

    def clone_repo(self, clone_url: str, name: str) -> None:
        target = self.package_path(name)
        result = subprocess.run(
            ["git", "clone", clone_url, target],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PackageError(f"Clone failed: {result.stderr}")

    def create_venv(self, name: str) -> None:
        venv = self.venv_path(name)
        result = subprocess.run(
            ["python", "-m", "venv", venv],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PackageError(f"Venv creation failed: {result.stderr}")

    def install_requirements(self, name: str) -> None:
        req_file = Path(self.package_path(name)) / "requirements.txt"
        if not req_file.exists():
            return
        pip = os.path.join(self.venv_path(name), "bin", "pip")
        result = subprocess.run(
            [pip, "install", "-r", str(req_file)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PackageError(f"Dependency install failed: {result.stderr}")

    def validate_package(self, name: str) -> dict:
        pkg_dir = Path(self.package_path(name))
        manifest_path = pkg_dir / "quilt.yaml"
        if not manifest_path.exists():
            raise PackageError(f"quilt.yaml not found in {pkg_dir}")

        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)

        entry_point = manifest.get("entry_point")
        if entry_point:
            entry_path = pkg_dir / entry_point
            if not entry_path.exists():
                raise PackageError(f"Entry point '{entry_point}' not found in {pkg_dir}")

        return manifest

    def update_package(self, name: str) -> str:
        pkg_dir = self.package_path(name)
        subprocess.run(
            ["git", "-C", pkg_dir, "pull"],
            capture_output=True,
            text=True,
        )
        return self.get_commit_hash(name)

    def get_commit_hash(self, name: str) -> str:
        pkg_dir = self.package_path(name)
        result = subprocess.run(
            ["git", "-C", pkg_dir, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def remove_package(self, name: str) -> None:
        import shutil
        pkg_dir = self.package_path(name)
        if os.path.exists(pkg_dir):
            shutil.rmtree(pkg_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_package_manager.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/package_manager.py tests/coordinator/test_package_manager.py
git commit -m "feat(coordinator): add package manager for cloning, venvs, and validation"
```

---

### Task 3: Scraper Manager

**Files:**
- Create: `coordinator/services/scraper_manager.py`
- Create: `tests/coordinator/test_scraper_manager.py`

Manages scraper lifecycle — tracks which scrapers are running, auto-starts scrapers when a dependent algorithm starts, and auto-stops when no algorithms depend on them.

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_scraper_manager.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from coordinator.services.scraper_manager import ScraperManager


@pytest.fixture
def scraper_manager():
    return ScraperManager()


def test_register_scraper(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    assert scraper_manager.is_registered("alpha-picks")
    assert not scraper_manager.is_running("alpha-picks")


def test_start_scraper(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.start("alpha-picks")
    assert scraper_manager.is_running("alpha-picks")


def test_stop_scraper(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.start("alpha-picks")
    scraper_manager.stop("alpha-picks")
    assert not scraper_manager.is_running("alpha-picks")


def test_add_dependent(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.add_dependent("alpha-picks", "instance-1")
    assert scraper_manager.dependent_count("alpha-picks") == 1


def test_remove_dependent(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.add_dependent("alpha-picks", "instance-1")
    scraper_manager.add_dependent("alpha-picks", "instance-2")
    scraper_manager.remove_dependent("alpha-picks", "instance-1")
    assert scraper_manager.dependent_count("alpha-picks") == 1


def test_should_stop_when_no_dependents(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.start("alpha-picks")
    scraper_manager.add_dependent("alpha-picks", "instance-1")
    scraper_manager.remove_dependent("alpha-picks", "instance-1")
    assert scraper_manager.should_stop("alpha-picks") is True


def test_should_not_stop_with_dependents(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.start("alpha-picks")
    scraper_manager.add_dependent("alpha-picks", "instance-1")
    assert scraper_manager.should_stop("alpha-picks") is False


def test_ensure_running_starts_if_needed(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    assert scraper_manager.is_running("alpha-picks")
    assert scraper_manager.dependent_count("alpha-picks") == 1


def test_ensure_running_idempotent(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    assert scraper_manager.dependent_count("alpha-picks") == 1


def test_release_and_stop_if_no_dependents(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    stopped = scraper_manager.release("alpha-picks", "instance-1")
    assert stopped is True
    assert not scraper_manager.is_running("alpha-picks")


def test_release_keeps_running_if_other_dependents(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    scraper_manager.ensure_running("alpha-picks", "instance-2")
    stopped = scraper_manager.release("alpha-picks", "instance-1")
    assert stopped is False
    assert scraper_manager.is_running("alpha-picks")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_scraper_manager.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/scraper_manager.py
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScraperState:
    schedule: str
    running: bool = False
    dependents: set[str] = field(default_factory=set)


class ScraperManager:
    def __init__(self) -> None:
        self._scrapers: dict[str, ScraperState] = {}

    def register(self, name: str, schedule: str) -> None:
        self._scrapers[name] = ScraperState(schedule=schedule)

    def is_registered(self, name: str) -> bool:
        return name in self._scrapers

    def is_running(self, name: str) -> bool:
        state = self._scrapers.get(name)
        return state.running if state else False

    def start(self, name: str) -> None:
        state = self._scrapers.get(name)
        if state:
            state.running = True
            logger.info("Started scraper: %s", name)

    def stop(self, name: str) -> None:
        state = self._scrapers.get(name)
        if state:
            state.running = False
            logger.info("Stopped scraper: %s", name)

    def add_dependent(self, name: str, instance_id: str) -> None:
        state = self._scrapers.get(name)
        if state:
            state.dependents.add(instance_id)

    def remove_dependent(self, name: str, instance_id: str) -> None:
        state = self._scrapers.get(name)
        if state:
            state.dependents.discard(instance_id)

    def dependent_count(self, name: str) -> int:
        state = self._scrapers.get(name)
        return len(state.dependents) if state else 0

    def should_stop(self, name: str) -> bool:
        state = self._scrapers.get(name)
        if not state:
            return False
        return state.running and len(state.dependents) == 0

    def ensure_running(self, name: str, instance_id: str) -> None:
        self.add_dependent(name, instance_id)
        if not self.is_running(name):
            self.start(name)

    def release(self, name: str, instance_id: str) -> bool:
        self.remove_dependent(name, instance_id)
        if self.should_stop(name):
            self.stop(name)
            return True
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_scraper_manager.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/scraper_manager.py tests/coordinator/test_scraper_manager.py
git commit -m "feat(coordinator): add scraper manager with dependency tracking"
```

---

### Task 4: Lifecycle Manager

**Files:**
- Create: `coordinator/services/lifecycle.py`
- Create: `tests/coordinator/test_lifecycle.py`

The lifecycle manager is the central orchestrator for algorithm instances. It handles compatibility checking, account locking, scraper startup, and coordinating start/stop with workers.

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_lifecycle.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from coordinator.services.lifecycle import LifecycleManager, CompatibilityError


@pytest.fixture
def lifecycle():
    worker_manager = AsyncMock()
    scraper_manager = MagicMock()
    event_bus = AsyncMock()
    return LifecycleManager(
        worker_manager=worker_manager,
        scraper_manager=scraper_manager,
        event_bus=event_bus,
    )


def test_check_compatibility_passes():
    account = {
        "supported_asset_types": ["equities", "options"],
        "options_level": 3,
        "account_features": ["margin", "short_selling"],
        "broker_type": "alpaca",
    }
    algorithm = {
        "required_asset_types": ["equities", "options"],
        "required_options_level": 2,
        "required_account_features": ["margin"],
        "supported_brokers": ["alpaca", "tradier"],
    }
    result = LifecycleManager.check_compatibility(account, algorithm)
    assert result.compatible is True
    assert result.mismatches == []


def test_check_compatibility_missing_asset_type():
    account = {
        "supported_asset_types": ["equities"],
        "options_level": None,
        "account_features": [],
        "broker_type": "alpaca",
    }
    algorithm = {
        "required_asset_types": ["equities", "options"],
        "required_options_level": None,
        "required_account_features": [],
        "supported_brokers": None,
    }
    result = LifecycleManager.check_compatibility(account, algorithm)
    assert result.compatible is False
    assert any("options" in m for m in result.mismatches)


def test_check_compatibility_insufficient_options_level():
    account = {
        "supported_asset_types": ["equities", "options"],
        "options_level": 1,
        "account_features": [],
        "broker_type": "alpaca",
    }
    algorithm = {
        "required_asset_types": ["equities"],
        "required_options_level": 3,
        "required_account_features": [],
        "supported_brokers": None,
    }
    result = LifecycleManager.check_compatibility(account, algorithm)
    assert result.compatible is False
    assert any("options level" in m.lower() for m in result.mismatches)


def test_check_compatibility_missing_feature():
    account = {
        "supported_asset_types": ["equities"],
        "options_level": None,
        "account_features": [],
        "broker_type": "alpaca",
    }
    algorithm = {
        "required_asset_types": ["equities"],
        "required_options_level": None,
        "required_account_features": ["margin"],
        "supported_brokers": None,
    }
    result = LifecycleManager.check_compatibility(account, algorithm)
    assert result.compatible is False
    assert any("margin" in m for m in result.mismatches)


def test_check_compatibility_unsupported_broker():
    account = {
        "supported_asset_types": ["equities"],
        "options_level": None,
        "account_features": [],
        "broker_type": "alpaca",
    }
    algorithm = {
        "required_asset_types": ["equities"],
        "required_options_level": None,
        "required_account_features": [],
        "supported_brokers": ["tradier"],
    }
    result = LifecycleManager.check_compatibility(account, algorithm)
    assert result.compatible is False
    assert any("broker" in m.lower() for m in result.mismatches)


def test_check_compatibility_any_broker_ok():
    account = {
        "supported_asset_types": ["equities"],
        "options_level": None,
        "account_features": [],
        "broker_type": "alpaca",
    }
    algorithm = {
        "required_asset_types": ["equities"],
        "required_options_level": None,
        "required_account_features": [],
        "supported_brokers": None,
    }
    result = LifecycleManager.check_compatibility(account, algorithm)
    assert result.compatible is True


@pytest.mark.asyncio
async def test_pre_start_checks_account_locked(lifecycle):
    account = MagicMock()
    account.locked_by = "other-instance"
    algorithm = MagicMock()
    instance = MagicMock()
    instance.id = "inst-1"

    with pytest.raises(CompatibilityError, match="locked"):
        await lifecycle.pre_start_checks(account, algorithm, instance)


@pytest.mark.asyncio
async def test_pre_start_checks_pass(lifecycle):
    account = MagicMock()
    account.locked_by = None
    account.supported_asset_types = ["equities"]
    account.options_level = None
    account.account_features = []
    account.broker_type = "alpaca"

    algorithm = MagicMock()
    algorithm.required_asset_types = ["equities"]
    algorithm.required_options_level = None
    algorithm.required_account_features = []
    algorithm.supported_brokers = None
    algorithm.data_dependencies = None

    instance = MagicMock()
    instance.id = "inst-1"

    await lifecycle.pre_start_checks(account, algorithm, instance)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_lifecycle.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/lifecycle.py
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from coordinator.services.event_bus import EventBus, SystemEvent
from coordinator.services.scraper_manager import ScraperManager

logger = logging.getLogger(__name__)


class CompatibilityError(Exception):
    pass


@dataclass
class CompatibilityResult:
    compatible: bool
    mismatches: list[str] = field(default_factory=list)


class LifecycleManager:
    def __init__(
        self,
        worker_manager: Any,
        scraper_manager: ScraperManager,
        event_bus: EventBus,
    ) -> None:
        self._worker_manager = worker_manager
        self._scraper_manager = scraper_manager
        self._event_bus = event_bus

    @staticmethod
    def check_compatibility(account: dict, algorithm: dict) -> CompatibilityResult:
        mismatches = []

        required_types = algorithm.get("required_asset_types") or []
        supported_types = account.get("supported_asset_types") or []
        for asset_type in required_types:
            if asset_type not in supported_types:
                mismatches.append(f"Missing asset type: {asset_type}")

        req_options = algorithm.get("required_options_level")
        acct_options = account.get("options_level")
        if req_options is not None:
            if acct_options is None or acct_options < req_options:
                mismatches.append(
                    f"Insufficient options level: requires {req_options}, account has {acct_options}"
                )

        required_features = algorithm.get("required_account_features") or []
        account_features = account.get("account_features") or []
        for feature in required_features:
            if feature not in account_features:
                mismatches.append(f"Missing account feature: {feature}")

        supported_brokers = algorithm.get("supported_brokers")
        if supported_brokers is not None:
            broker = account.get("broker_type")
            if broker not in supported_brokers:
                mismatches.append(
                    f"Unsupported broker: {broker} (algorithm supports: {supported_brokers})"
                )

        return CompatibilityResult(
            compatible=len(mismatches) == 0,
            mismatches=mismatches,
        )

    async def pre_start_checks(
        self, account: Any, algorithm: Any, instance: Any
    ) -> None:
        if account.locked_by is not None and account.locked_by != instance.id:
            raise CompatibilityError(
                f"Account is locked by instance {account.locked_by}"
            )

        acct_dict = {
            "supported_asset_types": account.supported_asset_types or [],
            "options_level": account.options_level,
            "account_features": account.account_features or [],
            "broker_type": account.broker_type,
        }
        algo_dict = {
            "required_asset_types": algorithm.required_asset_types or [],
            "required_options_level": algorithm.required_options_level,
            "required_account_features": algorithm.required_account_features or [],
            "supported_brokers": algorithm.supported_brokers,
        }

        result = self.check_compatibility(acct_dict, algo_dict)
        if not result.compatible:
            raise CompatibilityError(
                f"Compatibility check failed: {'; '.join(result.mismatches)}"
            )

        deps = algorithm.data_dependencies
        if deps:
            for dep in deps:
                name = dep.get("name") if isinstance(dep, dict) else dep
                if self._scraper_manager.is_registered(name):
                    self._scraper_manager.ensure_running(name, instance.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_lifecycle.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/lifecycle.py tests/coordinator/test_lifecycle.py
git commit -m "feat(coordinator): add lifecycle manager with compatibility checking"
```

---

### Task 5: GitHub API Routes

**Files:**
- Create: `coordinator/api/routes/github.py`
- Create: `tests/coordinator/test_github_api.py`

API endpoints for listing repos and installing algorithms from GitHub.

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_github_api.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from coordinator.services.github_service import RepoInfo


@pytest.mark.asyncio
async def test_list_repos_no_pat(client):
    response = await client.get("/api/github/repos")
    assert response.status_code == 400
    assert "GitHub PAT" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_repos_with_pat(client):
    # Set up a PAT first
    await client.put("/api/settings/github-pat", json={"value": "ghp_test123"})

    with patch("coordinator.api.routes.github.GitHubService") as mock_cls:
        mock_service = MagicMock()
        mock_service.list_quilt_repos.return_value = [
            RepoInfo(
                name="algo-1",
                full_name="user/algo-1",
                description="Test algo",
                clone_url="https://github.com/user/algo-1.git",
                html_url="https://github.com/user/algo-1",
            ),
        ]
        mock_cls.return_value = mock_service

        response = await client.get("/api/github/repos")
        assert response.status_code == 200
        repos = response.json()
        assert len(repos) == 1
        assert repos[0]["name"] == "algo-1"


@pytest.mark.asyncio
async def test_install_algorithm_from_github(client):
    await client.put("/api/settings/github-pat", json={"value": "ghp_test123"})

    with patch("coordinator.api.routes.github.GitHubService") as mock_gh, \
         patch("coordinator.api.routes.github.PackageManager") as mock_pm:

        mock_gh_inst = MagicMock()
        mock_gh_inst.get_clone_url.return_value = "https://github.com/user/algo.git"
        mock_gh.return_value = mock_gh_inst

        mock_pm_inst = MagicMock()
        mock_pm_inst.validate_package.return_value = {
            "name": "algo",
            "type": "algorithm",
            "version": "1.0.0",
            "entry_point": "algorithm.py",
            "class_name": "MyAlgo",
        }
        mock_pm_inst.get_commit_hash.return_value = "abc123"
        mock_pm.return_value = mock_pm_inst

        response = await client.post("/api/github/install", json={
            "full_name": "user/algo",
        })
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "algo"
        assert body["install_status"] == "installed"
        mock_pm_inst.clone_repo.assert_called_once()
        mock_pm_inst.create_venv.assert_called_once()
        mock_pm_inst.install_requirements.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_github_api.py -v`
Expected: FAIL with 404 (routes not registered)

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/routes/github.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db, get_container
from coordinator.database.models import Algorithm, Setting
from coordinator.services.github_service import GitHubService
from coordinator.services.package_manager import PackageManager, PackageError

router = APIRouter(prefix="/api/github", tags=["github"])


class InstallRequest(BaseModel):
    full_name: str


async def _get_pat(db: AsyncSession) -> str:
    result = await db.execute(select(Setting).where(Setting.key == "github_pat"))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=400, detail="GitHub PAT not configured")
    container = get_container()
    return container.encryption.decrypt(setting.value)


@router.get("/repos")
async def list_repos(db: AsyncSession = Depends(get_db)):
    pat = await _get_pat(db)
    service = GitHubService(pat=pat)
    repos = service.list_quilt_repos()
    return [
        {
            "name": r.name,
            "full_name": r.full_name,
            "description": r.description,
            "clone_url": r.clone_url,
            "html_url": r.html_url,
        }
        for r in repos
    ]


@router.post("/install", status_code=201)
async def install_algorithm(body: InstallRequest, db: AsyncSession = Depends(get_db)):
    pat = await _get_pat(db)
    gh = GitHubService(pat=pat)
    clone_url = gh.get_clone_url(body.full_name)

    container = get_container()
    pm = PackageManager(packages_dir="data/packages")

    name = body.full_name.split("/")[-1]

    try:
        pm.clone_repo(clone_url, name)
        pm.create_venv(name)
        pm.install_requirements(name)
        manifest = pm.validate_package(name)
        commit_hash = pm.get_commit_hash(name)
    except PackageError as e:
        raise HTTPException(status_code=422, detail=str(e))

    pkg_type = manifest.get("type", "algorithm")
    algo = Algorithm(
        repo_url=f"https://github.com/{body.full_name}",
        name=manifest.get("name", name),
        description=manifest.get("description"),
        version=manifest.get("version"),
        commit_hash=commit_hash,
        required_asset_types=manifest.get("requirements", {}).get("asset_types") if isinstance(manifest.get("requirements"), dict) else None,
        required_options_level=manifest.get("requirements", {}).get("options_level") if isinstance(manifest.get("requirements"), dict) else None,
        required_account_features=manifest.get("requirements", {}).get("account_features") if isinstance(manifest.get("requirements"), dict) else None,
        supported_brokers=manifest.get("requirements", {}).get("brokers") if isinstance(manifest.get("requirements"), dict) else None,
        data_dependencies=manifest.get("requirements", {}).get("data_dependencies") if isinstance(manifest.get("requirements"), dict) else None,
        config_schema=manifest.get("config"),
        custom_events=manifest.get("notifications", {}).get("custom_events") if isinstance(manifest.get("notifications"), dict) else None,
        install_status="installed",
    )
    db.add(algo)
    await db.flush()

    return {
        "id": algo.id,
        "name": algo.name,
        "description": algo.description,
        "version": algo.version,
        "install_status": algo.install_status,
        "repo_url": algo.repo_url,
    }
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py`:

```python
    from coordinator.api.routes.github import router as github_router
    app.include_router(github_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_github_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/github.py coordinator/main.py tests/coordinator/test_github_api.py
git commit -m "feat(coordinator): add GitHub API for repo discovery and algorithm installation"
```

---

### Task 6: Update pyproject.toml with Lifecycle Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add PyGithub to coordinator dependencies**

In `[project.optional-dependencies]`, add `PyGithub>=2.0.0` to the coordinator list.

- [ ] **Step 2: Install and verify**

Run: `cd /home/jkern/dev/quilt-trader && pip install -e ".[coordinator,dev]"`
Expected: PyGithub installs successfully

- [ ] **Step 3: Run full test suite for this plan**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_github_service.py tests/coordinator/test_package_manager.py tests/coordinator/test_scraper_manager.py tests/coordinator/test_lifecycle.py tests/coordinator/test_github_api.py -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add PyGithub dependency for GitHub integration"
```

---

### Task 7: Final Integration Verification

**Files:** None (verification only)

- [ ] **Step 1: Run all coordinator tests**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Verify all lifecycle modules are importable**

Run: `cd /home/jkern/dev/quilt-trader && python -c "from coordinator.services.github_service import GitHubService, RepoInfo; from coordinator.services.package_manager import PackageManager, PackageError; from coordinator.services.scraper_manager import ScraperManager; from coordinator.services.lifecycle import LifecycleManager, CompatibilityResult, CompatibilityError; print('All lifecycle modules imported successfully')"`
Expected: `All lifecycle modules imported successfully`

- [ ] **Step 3: Run full test suite (SDK + coordinator + worker)**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -v --tb=short`
Expected: All tests pass
