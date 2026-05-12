import os
from coordinator.config import CoordinatorConfig


def test_default_config():
    config = CoordinatorConfig(
        encryption_key="test-key-that-is-32-bytes-long!!"
    )
    assert config.host == "0.0.0.0"
    assert config.port == 8000
    assert config.database_url == "sqlite+aiosqlite:///data/quilt_trader.db"
    assert config.data_dir == "data"
    assert config.packages_dir == "data/packages"
    assert config.market_data_dir == "data/market"
    assert config.custom_data_dir == "data/custom"
    assert config.archive_dir == "data/archive"
    assert config.retention_days == 90
    assert config.archival_cron == "0 3 * * 0"
    assert config.backtest_cron == "0 2 * * *"
    assert config.divergence_threshold == 5.0
    assert config.snapshot_interval_market_minutes == 15
    assert config.snapshot_interval_off_hours_minutes == 60
    assert config.metrics_update_interval_minutes == 5


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("QT_HOST", "127.0.0.1")
    monkeypatch.setenv("QT_PORT", "9000")
    monkeypatch.setenv("QT_DATABASE_URL", "sqlite+aiosqlite:///custom.db")
    monkeypatch.setenv("QT_ENCRYPTION_KEY", "test-key-that-is-32-bytes-long!!")
    monkeypatch.setenv("QT_RETENTION_DAYS", "30")
    config = CoordinatorConfig()
    assert config.host == "127.0.0.1"
    assert config.port == 9000
    assert config.database_url == "sqlite+aiosqlite:///custom.db"
    assert config.retention_days == 30


def test_config_optional_secrets_default_none():
    config = CoordinatorConfig(
        encryption_key="test-key-that-is-32-bytes-long!!"
    )
    assert config.github_pat is None
    assert config.discord_bot_token is None
    assert config.polygon_api_key is None
    assert config.theta_data_username is None
    assert config.theta_data_password is None
