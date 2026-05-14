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
