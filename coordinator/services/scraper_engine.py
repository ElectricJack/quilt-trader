import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Optional

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


def runs_headed(manifest: Optional[dict], config: dict) -> bool:
    """Whether a scraper launches a visible browser: `headless` resolves false.

    The override in `config` wins; otherwise the manifest's declared default
    applies. Truthiness mirrors the scraper side, which reads
    bool(config["headless"]). Shared by scrape runs and login sessions, which
    must launch the browser the same way.
    """
    if "headless" in config:
        return not bool(config["headless"])
    try:
        params = ((manifest or {}).get("config") or {}).get("parameters") or []
    except AttributeError:
        return False
    for param in params:
        if isinstance(param, dict) and param.get("name") == "headless" and "default" in param:
            return not bool(param["default"])
    return False


def child_env(
    name: str,
    manifest: Optional[dict],
    config: dict,
    *,
    x11_socket_dir: str = X11_SOCKET_DIR,
) -> tuple[Optional[dict], Optional[str]]:
    """Environment for a scraper's browser process, or an error when it can't run.

    Returns (None, None) to inherit the coordinator's environment unchanged.
    A headed browser needs an X display, but a coordinator started by cron
    has no DISPLAY; Chromium then exits at launch and patchright reports an
    opaque TargetClosedError. So hand the child the host's X server when
    one is listening (WSLg's :0), and otherwise refuse with a message that
    says what is missing.
    """
    if not runs_headed(manifest, config) or os.environ.get("DISPLAY"):
        return None, None
    display = find_x_display(x11_socket_dir)
    if display is None:
        return None, (
            f"scraper {name} runs a headed browser (headless=false) but there is "
            f"no X display: DISPLAY is unset in the coordinator's environment "
            f"(e.g. it was started by cron) and no X server answers under "
            f"{x11_socket_dir}. Start the coordinator with DISPLAY set or "
            f"under xvfb-run, or set headless=true for this scraper."
        )
    logger.info(
        "scraper %s runs headed and the coordinator has no DISPLAY; using X server %s",
        name, display,
    )
    return {**os.environ, "DISPLAY": display}, None


@dataclass
class ScraperResult:
    success: bool
    output_path: Optional[str] = None
    error: Optional[str] = None
    # None on success; otherwise one of auth_required, bot_blocked,
    # profile_busy, error (set by the engine from the runner's result file),
    # or paused, login_in_progress (set by the registry, never by the runner).
    error_kind: Optional[str] = None


# Result-file statuses that carry their own message instead of a traceback.
_RUNNER_MESSAGE_KINDS = ("auth_required", "bot_blocked", "profile_busy")


def _read_runner_result(path: str) -> Optional[dict]:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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

    def _manifest_or_none(self, name: str) -> Optional[dict]:
        try:
            return self.parse_manifest(name)
        except Exception:
            return None

    def _runs_headed(self, name: str, config: dict) -> bool:
        return runs_headed(self._manifest_or_none(name), config)

    def _child_env(self, name: str, config: dict) -> tuple[Optional[dict], Optional[str]]:
        return child_env(
            name, self._manifest_or_none(name), config, x11_socket_dir=self._x11_socket_dir,
        )

    def runner_command(
        self, python: str, pkg_dir: str, config: dict, out_path: str, result_path: str,
    ) -> list[str]:
        """argv that runs sdk.scraper_runner in the package's interpreter."""
        bootstrap = (
            f"import sys; sys.path.insert(0, {pkg_dir!r}); sys.path.append({self._quilt_root!r}); "
            f"from sdk.scraper_runner import main; sys.exit(main())"
        )
        return [
            python, "-c", bootstrap,
            "--pkg-dir", pkg_dir,
            "--config-json", json.dumps(config),
            "--out", out_path,
            "--result", result_path,
        ]

    def run_scraper(self, name: str, output_format: str, config: Optional[dict] = None) -> ScraperResult:
        pkg_dir = os.path.join(self._packages_dir, name)
        venv_python = os.path.join(pkg_dir, ".venv", "bin", "python")
        # Fall back to the interpreter running the coordinator if no venv exists
        # (e.g. when running tests without a real venv per-package).
        python = venv_python if os.path.exists(venv_python) else sys.executable
        env, env_error = self._child_env(name, config or {})
        if env_error is not None:
            return ScraperResult(success=False, error=env_error, error_kind="error")
        out_path = self.output_path(name, output_format)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        fd, result_path = tempfile.mkstemp(prefix=f"quilt-scraper-{name}-", suffix=".json")
        os.close(fd)
        try:
            proc = subprocess.run(
                self.runner_command(python, pkg_dir, config or {}, out_path, result_path),
                capture_output=True, text=True, cwd=pkg_dir, env=env,
            )
            outcome = _read_runner_result(result_path)
        finally:
            try:
                os.unlink(result_path)
            except OSError:
                pass
        return self._to_result(proc, outcome, out_path)

    @staticmethod
    def _to_result(proc: Any, outcome: Optional[dict], out_path: str) -> ScraperResult:
        status = (outcome or {}).get("status")
        if status == "ok" and proc.returncode == 0:
            return ScraperResult(success=True, output_path=out_path)
        if status in _RUNNER_MESSAGE_KINDS:
            message = outcome.get("message") or status
            return ScraperResult(success=False, error=str(message), error_kind=status)
        # A runner-reported error, or no readable result at all (segfault,
        # OOM kill, the scraper calling sys.exit): the stderr tail is the
        # most useful thing to show, as it always was.
        error = proc.stderr
        if not error:
            if outcome and outcome.get("message"):
                error = f"{outcome.get('error_type', 'Error')}: {outcome['message']}"
            else:
                error = f"scraper runner exited with code {proc.returncode} and no result"
        return ScraperResult(success=False, error=error, error_kind="error")
