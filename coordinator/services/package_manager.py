import logging
import os
import subprocess
import sys
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
        """Clone a package repo into the packages dir.

        If the target directory already exists and is a valid clone of the
        same repo (matching `origin` remote URL), reset it to the latest
        commit of the default branch instead of failing with
        'destination path already exists'. This handles the common case
        where a previous install was partially completed or the DB row
        was dropped but `data/packages/<name>/` wasn't cleaned up.

        If the directory exists but is NOT a git clone of the expected
        repo, raise PackageError with a clear message asking the user to
        remove it.
        """
        target = self.package_path(name)
        target_path = Path(target)

        if target_path.exists():
            # Existing dir — figure out whether it's a valid clone of clone_url
            git_dir = target_path / ".git"
            if not git_dir.exists():
                raise PackageError(
                    f"Destination {target!r} already exists and is not a git "
                    f"clone. Remove it manually before retrying."
                )
            # Check the remote URL matches
            remote = subprocess.run(
                ["git", "-C", target, "config", "--get", "remote.origin.url"],
                capture_output=True, text=True,
            )
            existing_url = remote.stdout.strip()
            if not _git_urls_equivalent(existing_url, clone_url):
                raise PackageError(
                    f"Destination {target!r} exists but is a clone of "
                    f"{existing_url!r}, not {clone_url!r}. Remove it manually."
                )
            # Recover by fetching + hard-resetting to the remote's default branch
            fetch = subprocess.run(
                ["git", "-C", target, "fetch", "origin"],
                capture_output=True, text=True,
            )
            if fetch.returncode != 0:
                raise PackageError(f"git fetch failed in existing dir: {fetch.stderr}")
            # Determine default branch
            head_ref = subprocess.run(
                ["git", "-C", target, "symbolic-ref", "refs/remotes/origin/HEAD"],
                capture_output=True, text=True,
            )
            default_branch = "main"
            if head_ref.returncode == 0:
                # output is like "refs/remotes/origin/main"
                default_branch = head_ref.stdout.strip().rsplit("/", 1)[-1] or "main"
            reset = subprocess.run(
                ["git", "-C", target, "reset", "--hard", f"origin/{default_branch}"],
                capture_output=True, text=True,
            )
            if reset.returncode != 0:
                raise PackageError(f"git reset failed in existing dir: {reset.stderr}")
            logger.info(
                "Reused existing package dir %s, reset to origin/%s",
                target, default_branch,
            )
            return

        result = subprocess.run(
            ["git", "clone", clone_url, target], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise PackageError(f"Clone failed: {result.stderr}")

    def create_venv(self, name: str) -> None:
        venv = self.venv_path(name)
        # Use sys.executable so we don't depend on a `python` symlink existing in PATH
        # (Ubuntu ships `python3` but not `python` by default).
        result = subprocess.run(
            [sys.executable, "-m", "venv", venv], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise PackageError(f"Venv creation failed: {result.stderr}")

    def install_requirements(self, name: str) -> None:
        req_file = Path(self.package_path(name)) / "requirements.txt"
        if not req_file.exists():
            return
        pip = os.path.join(self.venv_path(name), "bin", "pip")
        result = subprocess.run(
            [pip, "install", "-r", str(req_file)], capture_output=True, text=True
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
            if not (pkg_dir / entry_point).exists():
                raise PackageError(f"Entry point '{entry_point}' not found in {pkg_dir}")
        return manifest

    def update_package(self, name: str) -> str:
        subprocess.run(
            ["git", "-C", self.package_path(name), "pull"],
            capture_output=True,
            text=True,
        )
        return self.get_commit_hash(name)

    def get_commit_hash(self, name: str) -> str:
        result = subprocess.run(
            ["git", "-C", self.package_path(name), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def remove_package(self, name: str) -> None:
        import shutil

        pkg_dir = self.package_path(name)
        if os.path.exists(pkg_dir):
            shutil.rmtree(pkg_dir)


def _git_urls_equivalent(a: str, b: str) -> bool:
    """Loose equivalence check between two GitHub URLs.

    Strips `.git` suffix, trailing slashes, and lowercases the host so e.g.
    'https://github.com/Foo/Bar.git' and 'https://github.com/foo/bar' match.
    """
    def norm(u: str) -> str:
        u = u.strip().rstrip("/")
        if u.endswith(".git"):
            u = u[:-4]
        if "://" in u:
            scheme, rest = u.split("://", 1)
            host, _, path = rest.partition("/")
            return f"{scheme}://{host.lower()}/{path}"
        return u
    return norm(a) == norm(b)
