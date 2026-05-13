import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Optional
import yaml

logger = logging.getLogger(__name__)

@dataclass
class ScraperResult:
    success: bool
    output_path: Optional[str] = None
    error: Optional[str] = None

class ScraperEngine:
    def __init__(self, packages_dir: str, output_dir: str) -> None:
        self._packages_dir = packages_dir
        self._output_dir = output_dir

    def parse_manifest(self, name: str) -> dict:
        manifest_path = os.path.join(self._packages_dir, name, "quilt.yaml")
        with open(manifest_path) as f:
            return yaml.safe_load(f)

    def output_path(self, name: str, fmt: str) -> str:
        return os.path.join(self._output_dir, f"{name}.{fmt}")

    def run_scraper(self, name: str, output_format: str, output_filename: str) -> ScraperResult:
        pkg_dir = os.path.join(self._packages_dir, name)
        venv_python = os.path.join(pkg_dir, ".venv", "bin", "python")
        python = venv_python if os.path.exists(venv_python) else "python"
        out_path = self.output_path(name, output_format)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        runner_script = (
            f"import sys; sys.path.insert(0, '{pkg_dir}'); "
            f"import yaml; "
            f"manifest = yaml.safe_load(open('{pkg_dir}/quilt.yaml')); "
            f"entry = manifest.get('entry_point', 'scraper.py'); "
            f"class_name = manifest.get('class_name', 'Scraper'); "
            f"import importlib.util; "
            f"spec = importlib.util.spec_from_file_location('mod', '{pkg_dir}/' + entry); "
            f"mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
            f"scraper = getattr(mod, class_name)(); "
            f"scraper.on_start({{}}); "
            f"df = scraper.on_run(); "
            f"df.to_csv('{out_path}', index=False); "
            f"scraper.on_stop(); "
        )
        result = subprocess.run(
            [python, "-c", runner_script], capture_output=True, text=True, cwd=pkg_dir,
        )
        if result.returncode == 0:
            return ScraperResult(success=True, output_path=out_path)
        else:
            return ScraperResult(success=False, error=result.stderr)
