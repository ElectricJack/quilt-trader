from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml

class ConfigError(Exception):
    pass

@dataclass
class QuiltDevConfig:
    data_mode: str = "standalone"
    coordinator_url: Optional[str] = None
    polygon_api_key: Optional[str] = None
    theta_data_username: Optional[str] = None
    theta_data_password: Optional[str] = None

    @staticmethod
    def load(path: Path) -> QuiltDevConfig:
        if not path.exists():
            return QuiltDevConfig()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        mode = data.get("data_mode", "standalone")
        if mode not in ("standalone", "connected"):
            raise ConfigError(f"data_mode must be 'standalone' or 'connected', got '{mode}'")
        if mode == "connected" and not data.get("coordinator_url"):
            raise ConfigError("Connected mode requires 'coordinator_url' in quilt.config.yaml")
        return QuiltDevConfig(
            data_mode=mode, coordinator_url=data.get("coordinator_url"),
            polygon_api_key=data.get("polygon_api_key"),
            theta_data_username=data.get("theta_data_username"),
            theta_data_password=data.get("theta_data_password"),
        )


import os
from typing import Optional

DEFAULT_COORDINATOR_URL = "http://localhost:8000"
DEFAULT_CONFIG_PATH = Path.home() / ".quilt" / "config.yaml"


def _config_path() -> Path:
    override = os.environ.get("QUILT_CONFIG")
    if override:
        return Path(override)
    return DEFAULT_CONFIG_PATH


def _load_config_file() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_coordinator_url(flag_value: Optional[str]) -> str:
    if flag_value:
        return flag_value
    env = os.environ.get("QUILT_COORDINATOR_URL")
    if env:
        return env
    file_val = _load_config_file().get("coordinator_url")
    if file_val:
        return file_val
    return DEFAULT_COORDINATOR_URL


def quilt_home() -> Path:
    override = os.environ.get("QUILT_HOME")
    if override:
        return Path(override)
    return Path.home() / ".quilt"


def quilt_run_dir() -> Path:
    p = quilt_home() / "run"
    p.mkdir(parents=True, exist_ok=True)
    return p


def quilt_log_dir() -> Path:
    p = quilt_home() / "log"
    p.mkdir(parents=True, exist_ok=True)
    return p
