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
