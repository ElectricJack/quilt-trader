from worker.config import WorkerConfig


def test_default_config():
    config = WorkerConfig(coordinator_url="ws://100.64.0.1:8000")
    assert config.coordinator_url == "ws://100.64.0.1:8000"
    assert config.coordinator_http_url == "http://100.64.0.1:8000"
    assert config.worker_name == "worker"
    assert config.heartbeat_interval == 30
    assert config.data_cache_ttl == 60
    assert config.max_algorithms == 2


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("QTW_COORDINATOR_URL", "ws://localhost:8000")
    monkeypatch.setenv("QTW_WORKER_NAME", "pi-garage")
    monkeypatch.setenv("QTW_HEARTBEAT_INTERVAL", "15")
    monkeypatch.setenv("QTW_DATA_CACHE_TTL", "30")
    config = WorkerConfig()
    assert config.coordinator_url == "ws://localhost:8000"
    assert config.worker_name == "pi-garage"
    assert config.heartbeat_interval == 15
    assert config.data_cache_ttl == 30


def test_coordinator_http_url_derived():
    config = WorkerConfig(coordinator_url="ws://10.0.0.5:9000")
    assert config.coordinator_http_url == "http://10.0.0.5:9000"
    config_wss = WorkerConfig(coordinator_url="wss://10.0.0.5:9000")
    assert config_wss.coordinator_http_url == "https://10.0.0.5:9000"
