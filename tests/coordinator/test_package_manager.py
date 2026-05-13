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
    assert manager.package_path("momentum-scalper") == os.path.join(packages_dir, "momentum-scalper")


def test_venv_path(manager, packages_dir):
    assert manager.venv_path("momentum-scalper") == os.path.join(packages_dir, "momentum-scalper", ".venv")


@patch("coordinator.services.package_manager.subprocess")
def test_clone_repo(mock_subprocess, manager):
    mock_subprocess.run.return_value = MagicMock(returncode=0)
    manager.clone_repo("https://github.com/user/algo.git", "algo")
    mock_subprocess.run.assert_called_once()
    assert "clone" in mock_subprocess.run.call_args[0][0]


@patch("coordinator.services.package_manager.subprocess")
def test_clone_repo_failure_raises(mock_subprocess, manager):
    mock_subprocess.run.return_value = MagicMock(returncode=1, stderr="fatal: repo not found")
    with pytest.raises(PackageError, match="Clone failed"):
        manager.clone_repo("https://github.com/user/bad.git", "bad")


@patch("coordinator.services.package_manager.subprocess")
def test_create_venv(mock_subprocess, manager, packages_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0)
    Path(packages_dir, "algo").mkdir(parents=True)
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
    Path(packages_dir, "algo").mkdir(parents=True)
    manager.install_requirements("algo")


def test_validate_package_missing_manifest(manager, packages_dir):
    Path(packages_dir, "algo").mkdir(parents=True)
    with pytest.raises(PackageError, match="quilt.yaml not found"):
        manager.validate_package("algo")


def test_validate_package_valid(manager, packages_dir):
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "quilt.yaml").write_text(
        "name: algo\ntype: algorithm\nversion: 1.0.0\nentry_point: algorithm.py\nclass_name: MyAlgo\n"
    )
    (pkg_dir / "algorithm.py").write_text("class MyAlgo: pass\n")
    manifest = manager.validate_package("algo")
    assert manifest["name"] == "algo"


def test_validate_package_missing_entry_point(manager, packages_dir):
    pkg_dir = Path(packages_dir) / "algo"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "quilt.yaml").write_text(
        "name: algo\ntype: algorithm\nversion: 1.0.0\nentry_point: missing.py\nclass_name: MyAlgo\n"
    )
    with pytest.raises(PackageError, match="Entry point"):
        manager.validate_package("algo")


@patch("coordinator.services.package_manager.subprocess")
def test_update_package(mock_subprocess, manager, packages_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="abc123\n")
    Path(packages_dir, "algo").mkdir(parents=True)
    assert manager.update_package("algo") == "abc123"


@patch("coordinator.services.package_manager.subprocess")
def test_get_commit_hash(mock_subprocess, manager, packages_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="def456\n")
    Path(packages_dir, "algo").mkdir(parents=True)
    assert manager.get_commit_hash("algo") == "def456"
