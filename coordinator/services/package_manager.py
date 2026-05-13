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
            ["git", "clone", clone_url, target], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise PackageError(f"Clone failed: {result.stderr}")

    def create_venv(self, name: str) -> None:
        venv = self.venv_path(name)
        result = subprocess.run(
            ["python", "-m", "venv", venv], capture_output=True, text=True
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
