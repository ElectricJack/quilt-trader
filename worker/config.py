from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class WorkerConfig(BaseSettings):
    model_config = {"env_prefix": "QTW_", "populate_by_name": True}

    coordinator_url: str = "ws://localhost:8000"
    worker_name: str = "worker"
    worker_id: str = ""
    heartbeat_interval: int = 30
    data_cache_ttl: int = 60
    max_algorithms: int = 2
    # The install script sets WORKER_TOKEN in the systemd unit env; also
    # accepts QTW_WORKER_INSTALL_TOKEN for consistency with the prefix scheme.
    worker_install_token: str = Field(
        default="",
        validation_alias=AliasChoices("WORKER_TOKEN", "QTW_WORKER_INSTALL_TOKEN"),
    )

    @property
    def coordinator_http_url(self) -> str:
        url = self.coordinator_url
        if url.startswith("wss://"):
            return "https://" + url[6:]
        if url.startswith("ws://"):
            return "http://" + url[5:]
        return url
