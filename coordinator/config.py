from pydantic_settings import BaseSettings
from typing import Optional


class CoordinatorConfig(BaseSettings):
    model_config = {"env_prefix": "QT_"}

    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "sqlite+aiosqlite:///data/quilt_trader.db"
    encryption_key: str

    data_dir: str = "data"
    packages_dir: str = "data/packages"
    market_data_dir: str = "data/market"
    custom_data_dir: str = "data/custom"
    archive_dir: str = "data/archive"

    retention_days: int = 90
    archival_cron: str = "0 3 * * 0"
    backtest_cron: str = "0 2 * * *"
    divergence_threshold: float = 5.0
    snapshot_interval_market_minutes: int = 15
    snapshot_interval_off_hours_minutes: int = 60
    metrics_update_interval_minutes: int = 5

    github_pat: Optional[str] = None
    discord_bot_token: Optional[str] = None
    polygon_api_key: Optional[str] = None
    theta_data_username: Optional[str] = None
    theta_data_password: Optional[str] = None

    # Worker install bootstrap script. Hosted publicly so a fresh Pi (not yet on Tailscale)
    # can fetch it; the script then installs Tailscale and pulls the worker package from
    # the coordinator over the private network.
    default_history_provider: str = "tradier"

    worker_install_script_url: str = (
        "https://raw.githubusercontent.com/ElectricJack/quilt-trader/main/scripts/install-worker.sh"
    )
