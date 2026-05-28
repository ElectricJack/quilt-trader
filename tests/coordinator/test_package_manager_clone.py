"""Test that clone_repo handles an existing package dir gracefully."""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from coordinator.services.package_manager import PackageManager, PackageError


def _init_git_repo(path: Path, remote_url: str) -> None:
    """Initialize an empty git repo with a configured origin remote."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote_url], check=True, capture_output=True)
    # Create an initial commit so refs/remotes/origin/HEAD has somewhere to point
    (path / "README.md").write_text("test\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t.t", "-c", "user.name=t",
                    "commit", "-m", "init"], check=True, capture_output=True)


def test_clone_repo_rejects_non_git_existing_dir(tmp_path):
    pkgs = tmp_path / "packages"
    pkgs.mkdir()
    (pkgs / "myalgo").mkdir()
    (pkgs / "myalgo" / "stray.txt").write_text("not a clone")
    pm = PackageManager(str(pkgs))
    with pytest.raises(PackageError, match="not a git clone"):
        pm.clone_repo("https://github.com/x/y", "myalgo")


def test_clone_repo_rejects_mismatched_remote(tmp_path):
    pkgs = tmp_path / "packages"
    pkgs.mkdir()
    target = pkgs / "myalgo"
    _init_git_repo(target, "https://github.com/other/repo.git")
    pm = PackageManager(str(pkgs))
    with pytest.raises(PackageError, match="exists but is a clone of"):
        pm.clone_repo("https://github.com/x/y", "myalgo")


def test_clone_repo_recovers_matching_existing_clone(tmp_path):
    """If the existing dir is a clone of the same repo, we fetch + reset rather
    than failing. The test doesn't actually fetch from GitHub — it verifies
    that the code path doesn't raise an exception when the remote URLs match.
    The git fetch will fail (since the remote URL is fake), so we expect
    PackageError from the fetch step — NOT from the initial 'exists' check."""
    pkgs = tmp_path / "packages"
    pkgs.mkdir()
    target = pkgs / "myalgo"
    _init_git_repo(target, "https://github.com/x/y.git")
    pm = PackageManager(str(pkgs))
    # The clone URL matches the existing remote, so the validity check passes
    # but git fetch will fail (fake remote). That's the expected next step.
    with pytest.raises(PackageError, match="git fetch failed"):
        pm.clone_repo("https://github.com/x/y", "myalgo")
