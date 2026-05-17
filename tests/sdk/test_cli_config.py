import pytest
from pathlib import Path
from sdk.cli.config import QuiltDevConfig, ConfigError

class TestQuiltDevConfig:
    def test_load_standalone_config(self, tmp_path):
        config_file = tmp_path / "quilt.config.yaml"
        config_file.write_text("data_mode: standalone\npolygon_api_key: pk_test123\n")
        config = QuiltDevConfig.load(config_file)
        assert config.data_mode == "standalone"
        assert config.polygon_api_key == "pk_test123"
        assert config.coordinator_url is None

    def test_load_connected_config(self, tmp_path):
        config_file = tmp_path / "quilt.config.yaml"
        config_file.write_text("data_mode: connected\ncoordinator_url: http://100.1.2.3:8000\n")
        config = QuiltDevConfig.load(config_file)
        assert config.data_mode == "connected"
        assert config.coordinator_url == "http://100.1.2.3:8000"

    def test_default_standalone_when_no_file(self, tmp_path):
        config = QuiltDevConfig.load(tmp_path / "nonexistent.yaml")
        assert config.data_mode == "standalone"
        assert config.polygon_api_key is None

    def test_invalid_data_mode_raises(self, tmp_path):
        config_file = tmp_path / "quilt.config.yaml"
        config_file.write_text("data_mode: invalid\n")
        with pytest.raises(ConfigError, match="data_mode"):
            QuiltDevConfig.load(config_file)

    def test_connected_mode_requires_coordinator_url(self, tmp_path):
        config_file = tmp_path / "quilt.config.yaml"
        config_file.write_text("data_mode: connected\n")
        with pytest.raises(ConfigError, match="coordinator_url"):
            QuiltDevConfig.load(config_file)

    def test_theta_data_credentials(self, tmp_path):
        config_file = tmp_path / "quilt.config.yaml"
        config_file.write_text("data_mode: standalone\ntheta_data_username: user1\ntheta_data_password: pass1\n")
        config = QuiltDevConfig.load(config_file)
        assert config.theta_data_username == "user1"
        assert config.theta_data_password == "pass1"


import os
from pathlib import Path
from sdk.cli.config import resolve_coordinator_url


def test_resolve_default_when_nothing_set(tmp_path, monkeypatch):
    monkeypatch.delenv("QUILT_COORDINATOR_URL", raising=False)
    monkeypatch.delenv("QUILT_CONFIG", raising=False)
    monkeypatch.setattr("sdk.cli.config.DEFAULT_CONFIG_PATH",
                        tmp_path / "nope" / "config.yaml")
    assert resolve_coordinator_url(flag_value=None) == "http://localhost:8000"


def test_resolve_uses_config_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("coordinator_url: http://from-file:9000\n")
    monkeypatch.setenv("QUILT_CONFIG", str(cfg))
    monkeypatch.delenv("QUILT_COORDINATOR_URL", raising=False)
    assert resolve_coordinator_url(flag_value=None) == "http://from-file:9000"


def test_resolve_env_var_overrides_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("coordinator_url: http://from-file:9000\n")
    monkeypatch.setenv("QUILT_CONFIG", str(cfg))
    monkeypatch.setenv("QUILT_COORDINATOR_URL", "http://from-env:9001")
    assert resolve_coordinator_url(flag_value=None) == "http://from-env:9001"


def test_resolve_flag_beats_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_COORDINATOR_URL", "http://from-env:9001")
    assert resolve_coordinator_url(flag_value="http://from-flag:9002") == "http://from-flag:9002"


def test_quilt_home_respects_env_override(tmp_path, monkeypatch):
    from sdk.cli.config import quilt_home
    monkeypatch.setenv("QUILT_HOME", str(tmp_path / "alt"))
    assert quilt_home() == tmp_path / "alt"
