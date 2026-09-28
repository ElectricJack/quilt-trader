import json
import logging
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional
import yaml

logger = logging.getLogger(__name__)

# Where X servers put their listening sockets; display :N is socket XN.
# WSLg serves display :0 here.
X11_SOCKET_DIR = "/tmp/.X11-unix"


def find_x_display(socket_dir: str = X11_SOCKET_DIR) -> Optional[str]:
    """Return ':N' for the lowest-numbered X server that accepts a connection.

    A socket file alone isn't proof: one left behind by a dead server refuses
    connections, and pointing Chromium at it fails exactly like no display.
    """
    try:
        names = os.listdir(socket_dir)
    except OSError:
        return None
    matches = (re.fullmatch(r"X(\d+)", name) for name in names)
    numbers = sorted(int(m.group(1)) for m in matches if m)
    for n in numbers:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect(os.path.join(socket_dir, f"X{n}"))
        except OSError:
            continue
        finally:
            sock.close()
        return f":{n}"
    return None


@dataclass
class ScraperResult:
    success: bool
    output_path: Optional[str] = None
    error: Optional[str] = None

class ScraperEngine:
    def __init__(
        self,
        packages_dir: str,
        output_dir: str,
        quilt_root: Optional[str] = None,
        x11_socket_dir: str = X11_SOCKET_DIR,
    ) -> None:
        self._packages_dir = packages_dir
        self._output_dir = output_dir
        self._x11_socket_dir = x11_socket_dir
        if quilt_root is None:
            quilt_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._quilt_root = quilt_root

    def parse_manifest(self, name: str) -> dict:
        manifest_path = os.path.join(self._packages_dir, name, "quilt.yaml")
        with open(manifest_path) as f:
            return yaml.safe_load(f)

    def output_path(self, name: str, fmt: str) -> str:
        return os.path.join(self._output_dir, f"{name}.{fmt}")

    def _runs_headed(self, name: str, config: dict) -> bool:
        """Whether this run launches a visible browser: `headless` resolves false.

        The override wins; otherwise the manifest's declared default applies.
        Truthiness mirrors the scraper side, which reads bool(config["headless"]).
        """
        if "headless" in config:
            return not bool(config["headless"])
        try:
            params = (self.parse_manifest(name).get("config") or {}).get("parameters") or []
        except Exception:
            return False
        for param in params:
            if isinstance(param, dict) and param.get("name") == "headless" and "default" in param:
                return not bool(param["default"])
        return False

    def _child_env(self, name: str, config: dict) -> tuple[Optional[dict], Optional[str]]:
        """Environment for the scraper subprocess, or an error when it can't run.

        Returns (None, None) to inherit the coordinator's environment unchanged.
        A headed browser needs an X display, but a coordinator started by cron
        has no DISPLAY; Chromium then exits at launch and patchright reports an
        opaque TargetClosedError. So hand the child the host's X server when
        one is listening (WSLg's :0), and otherwise refuse with a message that
        says what is missing.
        """
        if not self._runs_headed(name, config) or os.environ.get("DISPLAY"):
            return None, None
        display = find_x_display(self._x11_socket_dir)
        if display is None:
            return None, (
                f"scraper {name} runs a headed browser (headless=false) but there is "
                f"no X display: DISPLAY is unset in the coordinator's environment "
                f"(e.g. it was started by cron) and no X server answers under "
                f"{self._x11_socket_dir}. Start the coordinator with DISPLAY set or "
                f"under xvfb-run, or set headless=true for this scraper."
            )
        logger.info(
            "scraper %s runs headed and the coordinator has no DISPLAY; using X server %s",
            name, display,
        )
        return {**os.environ, "DISPLAY": display}, None

    def run_scraper(self, name: str, output_format: str, config: Optional[dict] = None) -> ScraperResult:
        pkg_dir = os.path.join(self._packages_dir, name)
        venv_python = os.path.join(pkg_dir, ".venv", "bin", "python")
        # Fall back to the interpreter running the coordinator if no venv exists
        # (e.g. when running tests without a real venv per-package).
        python = venv_python if os.path.exists(venv_python) else sys.executable
        env, env_error = self._child_env(name, config or {})
        if env_error is not None:
            return ScraperResult(success=False, error=env_error)
        out_path = self.output_path(name, output_format)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        config_json = json.dumps(config or {})
        runner_script = (
            f"import sys, json; sys.path.insert(0, '{pkg_dir}'); sys.path.append('{self._quilt_root}'); "
            f"import yaml; "
            f"manifest = yaml.safe_load(open('{pkg_dir}/quilt.yaml')); "
            f"entry = manifest.get('entry_point', 'scraper.py'); "
            f"class_name = manifest.get('class_name', 'Scraper'); "
            f"import importlib.util; "
            f"spec = importlib.util.spec_from_file_location('mod', '{pkg_dir}/' + entry); "
            f"mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
            f"scraper = getattr(mod, class_name)(); "
            f"scraper.on_start(json.loads({config_json!r})); "
            f"df = scraper.on_run(); "
            f"df.to_csv('{out_path}', index=False); "
            f"scraper.on_stop(); "
        )
        result = subprocess.run(
            [python, "-c", runner_script], capture_output=True, text=True, cwd=pkg_dir, env=env,
        )
        if result.returncode == 0:
            return ScraperResult(success=True, output_path=out_path)
        else:
            return ScraperResult(success=False, error=result.stderr)
