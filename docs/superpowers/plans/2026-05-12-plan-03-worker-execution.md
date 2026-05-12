# Plan 3: Worker + Execution Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the worker node — the process that runs on each Raspberry Pi, manages algorithm subprocesses, connects to brokers via Lumibot, executes trades after coordinator approval, and streams events back to the coordinator.

**Architecture:** The worker is a standalone Python process that connects to the coordinator via WebSocket over Tailscale. It receives commands (start/stop algorithm), spawns algorithm subprocesses, builds TickContext objects per tick, sends signal requests to the coordinator for PDT approval, executes approved trades via Lumibot broker adapters, and streams decision logs, trade fills, state checkpoints, and heartbeats back. Each algorithm runs in its own subprocess for isolation. A data client fetches market data and custom scraper output from the coordinator's REST API with local TTL caching.

**Tech Stack:** Python 3.11+, websockets (async WebSocket client), httpx (async HTTP client for data API), Lumibot (broker adapters as library), asyncio, subprocess, pytest

---

## File Map

| File | Responsibility |
|------|---------------|
| `worker/__init__.py` | Package marker |
| `worker/config.py` | Worker configuration (coordinator URL, worker name, shared secret) |
| `worker/agent.py` | WebSocket client connecting to coordinator, command dispatch, heartbeat loop |
| `worker/runner.py` | Algorithm subprocess lifecycle — spawn, monitor, stop, state management |
| `worker/tick_loop.py` | Tick loop that calls algorithm.on_tick(), captures signals, sends to coordinator |
| `worker/broker_adapter.py` | Thin wrapper around Lumibot broker classes for order execution |
| `worker/data_client.py` | HTTP client for coordinator data API with TTL cache |
| `worker/context.py` | Concrete TickContext implementation for live trading |
| `worker/main.py` | Worker entry point — arg parsing, agent startup |
| `tests/worker/__init__.py` | Test package |
| `tests/worker/test_config.py` | Worker config tests |
| `tests/worker/test_data_client.py` | Data client + caching tests |
| `tests/worker/test_broker_adapter.py` | Broker adapter tests (with mock broker) |
| `tests/worker/test_runner.py` | Subprocess runner tests |
| `tests/worker/test_tick_loop.py` | Tick loop + signal flow tests |
| `tests/worker/test_context.py` | LiveTickContext tests |
| `tests/worker/test_agent.py` | Agent WebSocket tests |
| `tests/worker/conftest.py` | Shared fixtures |

---

### Task 1: Worker Config

**Files:**
- Create: `worker/__init__.py`
- Create: `worker/config.py`
- Create: `tests/worker/__init__.py`
- Create: `tests/worker/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/__init__.py
# (empty)
```

```python
# tests/worker/test_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_config.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# worker/__init__.py
# (empty)
```

```python
# worker/config.py
from pydantic_settings import BaseSettings


class WorkerConfig(BaseSettings):
    model_config = {"env_prefix": "QTW_"}

    coordinator_url: str = "ws://localhost:8000"
    worker_name: str = "worker"
    heartbeat_interval: int = 30
    data_cache_ttl: int = 60
    max_algorithms: int = 2

    @property
    def coordinator_http_url(self) -> str:
        url = self.coordinator_url
        if url.startswith("wss://"):
            return "https://" + url[6:]
        if url.startswith("ws://"):
            return "http://" + url[5:]
        return url
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/__init__.py worker/config.py tests/worker/__init__.py tests/worker/test_config.py
git commit -m "feat(worker): add worker config with pydantic-settings"
```

---

### Task 2: Data Client with TTL Cache

**Files:**
- Create: `worker/data_client.py`
- Create: `tests/worker/test_data_client.py`
- Create: `tests/worker/conftest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/conftest.py
# (empty for now)
```

```python
# tests/worker/test_data_client.py
import time
import pytest
import pandas as pd

from worker.data_client import DataClient


class FakeHTTPResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class FakeHTTPClient:
    def __init__(self):
        self.call_count = 0
        self.responses = {}

    def set_response(self, url, data):
        self.responses[url] = data

    async def get(self, url, **kwargs):
        self.call_count += 1
        data = self.responses.get(url, {"data": []})
        return FakeHTTPResponse(data)

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_fetch_market_data():
    http = FakeHTTPClient()
    http.set_response(
        "http://coordinator:8000/api/data/market/AAPL",
        {
            "data": [
                {"timestamp": "2025-01-01T09:30:00", "open": 150.0, "high": 151.0, "low": 149.0, "close": 150.5, "volume": 1000},
                {"timestamp": "2025-01-01T09:31:00", "open": 150.5, "high": 152.0, "low": 150.0, "close": 151.0, "volume": 1500},
            ]
        },
    )
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=60, http_client=http)
    df = await client.get_market_data("AAPL", timeframe="1min", bars=100)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "close" in df.columns


@pytest.mark.asyncio
async def test_fetch_custom_data():
    http = FakeHTTPClient()
    http.set_response(
        "http://coordinator:8000/api/data/custom/alpha-picks",
        {
            "data": [
                {"symbol": "TSLA", "score": 0.95},
                {"symbol": "NVDA", "score": 0.88},
            ]
        },
    )
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=60, http_client=http)
    df = await client.get_custom_data("alpha-picks")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "symbol" in df.columns


@pytest.mark.asyncio
async def test_cache_prevents_duplicate_requests():
    http = FakeHTTPClient()
    http.set_response(
        "http://coordinator:8000/api/data/custom/alpha-picks",
        {"data": [{"symbol": "TSLA"}]},
    )
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=60, http_client=http)
    await client.get_custom_data("alpha-picks")
    await client.get_custom_data("alpha-picks")
    assert http.call_count == 1


@pytest.mark.asyncio
async def test_cache_expires():
    http = FakeHTTPClient()
    http.set_response(
        "http://coordinator:8000/api/data/custom/alpha-picks",
        {"data": [{"symbol": "TSLA"}]},
    )
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=0, http_client=http)
    await client.get_custom_data("alpha-picks")
    await client.get_custom_data("alpha-picks")
    assert http.call_count == 2


@pytest.mark.asyncio
async def test_clear_cache():
    http = FakeHTTPClient()
    http.set_response(
        "http://coordinator:8000/api/data/custom/test",
        {"data": [{"val": 1}]},
    )
    client = DataClient(base_url="http://coordinator:8000", cache_ttl=60, http_client=http)
    await client.get_custom_data("test")
    client.clear_cache()
    await client.get_custom_data("test")
    assert http.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_data_client.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# worker/data_client.py
import time
from typing import Any, Optional

import pandas as pd


class DataClient:
    def __init__(
        self,
        base_url: str,
        cache_ttl: int = 60,
        http_client: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cache_ttl = cache_ttl
        self._http = http_client
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    def _get_cached(self, key: str) -> Optional[pd.DataFrame]:
        if key in self._cache:
            ts, df = self._cache[key]
            if time.monotonic() - ts < self._cache_ttl:
                return df
            del self._cache[key]
        return None

    def _set_cached(self, key: str, df: pd.DataFrame) -> None:
        self._cache[key] = (time.monotonic(), df)

    def clear_cache(self) -> None:
        self._cache.clear()

    async def get_market_data(
        self,
        symbol: str,
        timeframe: str = "1min",
        bars: int = 100,
    ) -> pd.DataFrame:
        url = f"{self._base_url}/api/data/market/{symbol}"
        cache_key = f"market:{symbol}:{timeframe}:{bars}"

        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        response = await self._http.get(
            url, params={"timeframe": timeframe, "bars": bars}
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        df = pd.DataFrame(data)
        self._set_cached(cache_key, df)
        return df

    async def get_custom_data(self, source_name: str) -> pd.DataFrame:
        url = f"{self._base_url}/api/data/custom/{source_name}"
        cache_key = f"custom:{source_name}"

        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        response = await self._http.get(url)
        response.raise_for_status()
        data = response.json().get("data", [])
        df = pd.DataFrame(data)
        self._set_cached(cache_key, df)
        return df
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_data_client.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/data_client.py tests/worker/test_data_client.py tests/worker/conftest.py
git commit -m "feat(worker): add data client with TTL cache for coordinator API"
```

---

### Task 3: Broker Adapter (Mock + Interface)

**Files:**
- Create: `worker/broker_adapter.py`
- Create: `tests/worker/test_broker_adapter.py`

The broker adapter wraps Lumibot's broker classes. For testing, we build a mock adapter that implements the same interface. The real Lumibot integration happens in a later plan; here we define the interface and the mock.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_broker_adapter.py
import pytest
from worker.broker_adapter import BrokerAdapter, MockBrokerAdapter, OrderResult


def test_mock_broker_get_positions():
    broker = MockBrokerAdapter()
    broker.set_positions({
        "AAPL": {"symbol": "AAPL", "quantity": 100, "avg_cost": 150.0, "current_price": 155.0},
    })
    positions = broker.get_positions()
    assert "AAPL" in positions
    assert positions["AAPL"]["quantity"] == 100


def test_mock_broker_get_account_info():
    broker = MockBrokerAdapter()
    broker.set_account_info(cash=50000.0, portfolio_value=75000.0, buying_power=100000.0)
    info = broker.get_account_info()
    assert info["cash"] == 50000.0
    assert info["portfolio_value"] == 75000.0
    assert info["buying_power"] == 100000.0


def test_mock_broker_submit_order():
    broker = MockBrokerAdapter()
    broker.set_fill_price(151.0)
    result = broker.submit_order(
        symbol="AAPL",
        side="buy",
        quantity=100,
        order_type="market",
    )
    assert isinstance(result, OrderResult)
    assert result.filled_price == 151.0
    assert result.quantity == 100
    assert result.symbol == "AAPL"
    assert result.fees == 0.0


def test_mock_broker_submit_order_with_fees():
    broker = MockBrokerAdapter()
    broker.set_fill_price(200.0)
    broker.set_fees(1.50)
    result = broker.submit_order(
        symbol="TSLA",
        side="sell",
        quantity=50,
        order_type="limit",
        limit_price=200.0,
    )
    assert result.fees == 1.50
    assert result.filled_price == 200.0


def test_mock_broker_order_history():
    broker = MockBrokerAdapter()
    broker.set_fill_price(150.0)
    broker.submit_order(symbol="AAPL", side="buy", quantity=100, order_type="market")
    broker.submit_order(symbol="TSLA", side="buy", quantity=50, order_type="market")
    assert len(broker.order_history) == 2
    assert broker.order_history[0].symbol == "AAPL"
    assert broker.order_history[1].symbol == "TSLA"


def test_broker_adapter_is_abstract():
    with pytest.raises(TypeError):
        BrokerAdapter()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_broker_adapter.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# worker/broker_adapter.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OrderResult:
    symbol: str
    side: str
    quantity: float
    order_type: str
    filled_price: float
    fees: float = 0.0
    fee_breakdown: Optional[dict] = None
    broker_order_id: Optional[str] = None


class BrokerAdapter(ABC):
    @abstractmethod
    def get_positions(self) -> dict[str, dict]:
        ...

    @abstractmethod
    def get_account_info(self) -> dict:
        ...

    @abstractmethod
    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> OrderResult:
        ...


class MockBrokerAdapter(BrokerAdapter):
    def __init__(self) -> None:
        self._positions: dict[str, dict] = {}
        self._account_info: dict = {
            "cash": 100000.0,
            "portfolio_value": 100000.0,
            "buying_power": 200000.0,
        }
        self._fill_price: float = 0.0
        self._fees: float = 0.0
        self.order_history: list[OrderResult] = []

    def set_positions(self, positions: dict[str, dict]) -> None:
        self._positions = positions

    def set_account_info(
        self, cash: float, portfolio_value: float, buying_power: float
    ) -> None:
        self._account_info = {
            "cash": cash,
            "portfolio_value": portfolio_value,
            "buying_power": buying_power,
        }

    def set_fill_price(self, price: float) -> None:
        self._fill_price = price

    def set_fees(self, fees: float) -> None:
        self._fees = fees

    def get_positions(self) -> dict[str, dict]:
        return dict(self._positions)

    def get_account_info(self) -> dict:
        return dict(self._account_info)

    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> OrderResult:
        result = OrderResult(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            filled_price=self._fill_price,
            fees=self._fees,
        )
        self.order_history.append(result)
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_broker_adapter.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/broker_adapter.py tests/worker/test_broker_adapter.py
git commit -m "feat(worker): add broker adapter interface with mock implementation"
```

---

### Task 4: Live TickContext

**Files:**
- Create: `worker/context.py`
- Create: `tests/worker/test_context.py`

This is the concrete TickContext implementation used during live trading. It wraps the broker adapter and data client to provide market data, positions, and account info to algorithms.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_context.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd

from worker.context import LiveTickContext
from worker.broker_adapter import MockBrokerAdapter


@pytest.fixture
def mock_broker():
    broker = MockBrokerAdapter()
    broker.set_positions({
        "AAPL": {"symbol": "AAPL", "quantity": 100, "avg_cost": 150.0, "current_price": 155.0},
    })
    broker.set_account_info(cash=50000.0, portfolio_value=75000.0, buying_power=100000.0)
    return broker


@pytest.fixture
def mock_data_client():
    client = AsyncMock()
    client.get_market_data.return_value = pd.DataFrame({
        "timestamp": ["2025-01-01T09:30:00", "2025-01-01T09:31:00"],
        "open": [150.0, 150.5],
        "high": [151.0, 152.0],
        "low": [149.0, 150.0],
        "close": [150.5, 151.0],
        "volume": [1000, 1500],
    })
    client.get_custom_data.return_value = pd.DataFrame({
        "symbol": ["TSLA", "NVDA"],
        "score": [0.95, 0.88],
    })
    return client


def test_tick_context_timestamp(mock_broker, mock_data_client):
    ts = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    ctx = LiveTickContext(
        timestamp=ts,
        mode="live",
        broker=mock_broker,
        data_client=mock_data_client,
    )
    assert ctx.timestamp == ts
    assert ctx.mode == "live"


def test_tick_context_positions(mock_broker, mock_data_client):
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live",
        broker=mock_broker,
        data_client=mock_data_client,
    )
    positions = ctx.positions
    assert "AAPL" in positions
    assert positions["AAPL"]["quantity"] == 100


def test_tick_context_account_values(mock_broker, mock_data_client):
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live",
        broker=mock_broker,
        data_client=mock_data_client,
    )
    assert ctx.account_value == 75000.0
    assert ctx.cash == 50000.0
    assert ctx.buying_power == 100000.0


@pytest.mark.asyncio
async def test_tick_context_market_data(mock_broker, mock_data_client):
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live",
        broker=mock_broker,
        data_client=mock_data_client,
    )
    df = await ctx.market_data("AAPL", timeframe="1min", bars=100)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    mock_data_client.get_market_data.assert_called_once_with("AAPL", timeframe="1min", bars=100)


@pytest.mark.asyncio
async def test_tick_context_custom_data(mock_broker, mock_data_client):
    ctx = LiveTickContext(
        timestamp=datetime.now(timezone.utc),
        mode="live",
        broker=mock_broker,
        data_client=mock_data_client,
    )
    df = await ctx.data("alpha-picks")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    mock_data_client.get_custom_data.assert_called_once_with("alpha-picks")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_context.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# worker/context.py
from datetime import datetime
from typing import Optional

import pandas as pd

from worker.broker_adapter import BrokerAdapter
from worker.data_client import DataClient


class LiveTickContext:
    def __init__(
        self,
        timestamp: datetime,
        mode: str,
        broker: BrokerAdapter,
        data_client: DataClient,
    ) -> None:
        self._timestamp = timestamp
        self._mode = mode
        self._broker = broker
        self._data_client = data_client

    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def positions(self) -> dict:
        return self._broker.get_positions()

    @property
    def account_value(self) -> float:
        return self._broker.get_account_info()["portfolio_value"]

    @property
    def cash(self) -> float:
        return self._broker.get_account_info()["cash"]

    @property
    def buying_power(self) -> float:
        return self._broker.get_account_info()["buying_power"]

    async def market_data(
        self, symbol: str, timeframe: str = "1min", bars: int = 100
    ) -> pd.DataFrame:
        return await self._data_client.get_market_data(
            symbol, timeframe=timeframe, bars=bars
        )

    async def data(self, source_name: str) -> pd.DataFrame:
        return await self._data_client.get_custom_data(source_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_context.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/context.py tests/worker/test_context.py
git commit -m "feat(worker): add LiveTickContext wrapping broker and data client"
```

---

### Task 5: Algorithm Subprocess Runner

**Files:**
- Create: `worker/runner.py`
- Create: `tests/worker/test_runner.py`

The runner manages the lifecycle of a single algorithm subprocess. For testability, we implement in-process algorithm execution (calling the algorithm object directly) rather than actual subprocess spawning. Real subprocess isolation will be layered on top in a later plan.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_runner.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from sdk.signals import Signal, SignalType
from worker.runner import AlgorithmRunner, RunnerState


class FakeAlgorithm:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.tick_count = 0
        self.config = None
        self.restored_state = None
        self._signals = []
        self._notifications = []

    def on_start(self, config, restored_state):
        self.started = True
        self.config = config
        self.restored_state = restored_state

    def on_tick(self, ctx):
        self.tick_count += 1
        return list(self._signals)

    def on_stop(self):
        self.stopped = True
        return {"tick_count": self.tick_count}

    def save_state(self):
        return {"tick_count": self.tick_count}

    def set_signals(self, signals):
        self._signals = signals

    def on_signal_rejected(self, signal, reason):
        pass

    def on_trade_executed(self, signal, fill):
        pass

    def notify(self, event_name, message, data=None):
        self._notifications.append((event_name, message, data))

    def drain_notifications(self):
        notifs = list(self._notifications)
        self._notifications.clear()
        return notifs


def test_runner_initial_state():
    runner = AlgorithmRunner(
        instance_id="inst-1",
        algorithm=FakeAlgorithm(),
        config={"risk": 0.02},
        restored_state=None,
    )
    assert runner.state == RunnerState.STOPPED


def test_runner_start():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(
        instance_id="inst-1",
        algorithm=algo,
        config={"risk": 0.02},
        restored_state={"tick_count": 5},
    )
    runner.start()
    assert runner.state == RunnerState.RUNNING
    assert algo.started is True
    assert algo.config == {"risk": 0.02}
    assert algo.restored_state == {"tick_count": 5}


def test_runner_stop():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(
        instance_id="inst-1",
        algorithm=algo,
        config={},
        restored_state=None,
    )
    runner.start()
    final_state = runner.stop()
    assert runner.state == RunnerState.STOPPED
    assert algo.stopped is True
    assert final_state == {"tick_count": 0}


def test_runner_tick_no_signals():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(
        instance_id="inst-1",
        algorithm=algo,
        config={},
        restored_state=None,
    )
    runner.start()
    ctx = MagicMock()
    signals = runner.tick(ctx)
    assert signals == []
    assert algo.tick_count == 1


def test_runner_tick_with_signals():
    algo = FakeAlgorithm()
    signal = Signal.simple("AAPL", SignalType.BUY, 100, reasoning="Test buy")
    algo.set_signals([signal])

    runner = AlgorithmRunner(
        instance_id="inst-1",
        algorithm=algo,
        config={},
        restored_state=None,
    )
    runner.start()
    ctx = MagicMock()
    signals = runner.tick(ctx)
    assert len(signals) == 1
    assert signals[0].legs[0].symbol == "AAPL"


def test_runner_save_state():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(
        instance_id="inst-1",
        algorithm=algo,
        config={},
        restored_state=None,
    )
    runner.start()
    ctx = MagicMock()
    runner.tick(ctx)
    runner.tick(ctx)
    state = runner.save_state()
    assert state == {"tick_count": 2}


def test_runner_tick_while_stopped_raises():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(
        instance_id="inst-1",
        algorithm=algo,
        config={},
        restored_state=None,
    )
    with pytest.raises(RuntimeError, match="not running"):
        runner.tick(MagicMock())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_runner.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# worker/runner.py
from enum import Enum
from typing import Any, Optional

from sdk.signals import Signal


class RunnerState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


class AlgorithmRunner:
    def __init__(
        self,
        instance_id: str,
        algorithm: Any,
        config: dict,
        restored_state: Optional[dict],
    ) -> None:
        self.instance_id = instance_id
        self._algorithm = algorithm
        self._config = config
        self._restored_state = restored_state
        self.state = RunnerState.STOPPED

    def start(self) -> None:
        self._algorithm.on_start(self._config, self._restored_state)
        self.state = RunnerState.RUNNING

    def stop(self) -> dict:
        final_state = self._algorithm.on_stop()
        self.state = RunnerState.STOPPED
        return final_state

    def tick(self, ctx: Any) -> list[Signal]:
        if self.state != RunnerState.RUNNING:
            raise RuntimeError("Algorithm is not running")
        signals = self._algorithm.on_tick(ctx)
        return signals if signals else []

    def save_state(self) -> dict:
        return self._algorithm.save_state()

    def on_signal_rejected(self, signal: Signal, reason: str) -> None:
        self._algorithm.on_signal_rejected(signal, reason)

    def on_trade_executed(self, signal: Signal, fill: Any) -> None:
        self._algorithm.on_trade_executed(signal, fill)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_runner.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/runner.py tests/worker/test_runner.py
git commit -m "feat(worker): add algorithm runner with lifecycle management"
```

---

### Task 6: Tick Loop + Signal Approval Flow

**Files:**
- Create: `worker/tick_loop.py`
- Create: `tests/worker/test_tick_loop.py`

The tick loop coordinates a single tick cycle: build context, call runner.tick(), for each signal send approval request to coordinator, execute approved signals via broker, report results. This is the core execution pipeline.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_tick_loop.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from sdk.signals import Signal, SignalType
from worker.tick_loop import TickProcessor, TickResult
from worker.broker_adapter import MockBrokerAdapter, OrderResult
from worker.runner import AlgorithmRunner


class SimpleAlgo:
    def __init__(self):
        self._signals = []

    def on_start(self, config, restored_state):
        pass

    def on_tick(self, ctx):
        return list(self._signals)

    def on_stop(self):
        return {}

    def save_state(self):
        return {}

    def on_signal_rejected(self, signal, reason):
        self.last_rejection = (signal, reason)

    def on_trade_executed(self, signal, fill):
        self.last_fill = (signal, fill)

    def notify(self, event_name, message, data=None):
        pass

    def drain_notifications(self):
        return []


@pytest.fixture
def broker():
    b = MockBrokerAdapter()
    b.set_fill_price(150.0)
    b.set_account_info(cash=50000.0, portfolio_value=75000.0, buying_power=100000.0)
    return b


@pytest.fixture
def coordinator_client():
    client = AsyncMock()
    client.request_signal_approval.return_value = {"approved": True}
    return client


@pytest.fixture
def data_client():
    return AsyncMock()


@pytest.mark.asyncio
async def test_tick_no_signals(broker, coordinator_client, data_client):
    algo = SimpleAlgo()
    runner = AlgorithmRunner(
        instance_id="inst-1", algorithm=algo, config={}, restored_state=None
    )
    runner.start()

    processor = TickProcessor(
        runner=runner,
        broker=broker,
        data_client=data_client,
        coordinator_client=coordinator_client,
    )
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert isinstance(result, TickResult)
    assert result.signals_produced == 0
    assert result.trades_executed == 0
    assert result.trades_rejected == 0


@pytest.mark.asyncio
async def test_tick_with_approved_signal(broker, coordinator_client, data_client):
    algo = SimpleAlgo()
    algo._signals = [Signal.simple("AAPL", SignalType.BUY, 100)]
    runner = AlgorithmRunner(
        instance_id="inst-1", algorithm=algo, config={}, restored_state=None
    )
    runner.start()

    processor = TickProcessor(
        runner=runner,
        broker=broker,
        data_client=data_client,
        coordinator_client=coordinator_client,
    )
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert result.signals_produced == 1
    assert result.trades_executed == 1
    assert result.trades_rejected == 0
    coordinator_client.request_signal_approval.assert_called_once()


@pytest.mark.asyncio
async def test_tick_with_rejected_signal(broker, coordinator_client, data_client):
    coordinator_client.request_signal_approval.return_value = {
        "approved": False,
        "reason": "PDT limit reached",
    }
    algo = SimpleAlgo()
    algo._signals = [Signal.simple("AAPL", SignalType.SELL, 50)]
    runner = AlgorithmRunner(
        instance_id="inst-1", algorithm=algo, config={}, restored_state=None
    )
    runner.start()

    processor = TickProcessor(
        runner=runner,
        broker=broker,
        data_client=data_client,
        coordinator_client=coordinator_client,
    )
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert result.signals_produced == 1
    assert result.trades_executed == 0
    assert result.trades_rejected == 1
    assert algo.last_rejection[1] == "PDT limit reached"


@pytest.mark.asyncio
async def test_tick_builds_decision_log(broker, coordinator_client, data_client):
    algo = SimpleAlgo()
    algo._signals = [Signal.simple("AAPL", SignalType.BUY, 100, reasoning="Momentum")]
    runner = AlgorithmRunner(
        instance_id="inst-1", algorithm=algo, config={}, restored_state=None
    )
    runner.start()

    processor = TickProcessor(
        runner=runner,
        broker=broker,
        data_client=data_client,
        coordinator_client=coordinator_client,
    )
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert result.decision_log is not None
    assert result.decision_log["signals_produced"] is not None
    assert len(result.decision_log["signals_produced"]) == 1


@pytest.mark.asyncio
async def test_tick_multiple_signals(broker, coordinator_client, data_client):
    coordinator_client.request_signal_approval.side_effect = [
        {"approved": True},
        {"approved": False, "reason": "PDT"},
    ]
    algo = SimpleAlgo()
    algo._signals = [
        Signal.simple("AAPL", SignalType.BUY, 100),
        Signal.simple("TSLA", SignalType.BUY, 50),
    ]
    runner = AlgorithmRunner(
        instance_id="inst-1", algorithm=algo, config={}, restored_state=None
    )
    runner.start()

    processor = TickProcessor(
        runner=runner,
        broker=broker,
        data_client=data_client,
        coordinator_client=coordinator_client,
    )
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert result.signals_produced == 2
    assert result.trades_executed == 1
    assert result.trades_rejected == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_tick_loop.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# worker/tick_loop.py
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sdk.signals import Signal
from worker.broker_adapter import BrokerAdapter, OrderResult
from worker.context import LiveTickContext
from worker.data_client import DataClient
from worker.runner import AlgorithmRunner


@dataclass
class TradeResult:
    signal: Signal
    order_result: OrderResult


@dataclass
class TickResult:
    timestamp: datetime
    signals_produced: int = 0
    trades_executed: int = 0
    trades_rejected: int = 0
    trade_results: list[TradeResult] = field(default_factory=list)
    decision_log: Optional[dict] = None


class TickProcessor:
    def __init__(
        self,
        runner: AlgorithmRunner,
        broker: BrokerAdapter,
        data_client: DataClient,
        coordinator_client: Any,
    ) -> None:
        self._runner = runner
        self._broker = broker
        self._data_client = data_client
        self._coordinator = coordinator_client

    async def process_tick(self, timestamp: datetime) -> TickResult:
        ctx = LiveTickContext(
            timestamp=timestamp,
            mode="live",
            broker=self._broker,
            data_client=self._data_client,
        )
        signals = self._runner.tick(ctx)

        result = TickResult(timestamp=timestamp, signals_produced=len(signals))

        serialized_signals = []
        for signal in signals:
            serialized_signals.append(signal.to_dict())
            approval = await self._coordinator.request_signal_approval(
                instance_id=self._runner.instance_id,
                signal=signal.to_dict(),
            )
            if approval.get("approved"):
                for leg in signal.legs:
                    order_result = self._broker.submit_order(
                        symbol=leg.symbol,
                        side=leg.signal_type.value,
                        quantity=leg.quantity,
                        order_type=leg.order_type.value,
                        limit_price=leg.limit_price,
                        stop_price=leg.stop_price,
                    )
                    result.trade_results.append(TradeResult(signal=signal, order_result=order_result))
                result.trades_executed += 1
                self._runner.on_trade_executed(signal, result.trade_results[-1].order_result)
            else:
                reason = approval.get("reason", "Unknown")
                result.trades_rejected += 1
                self._runner.on_signal_rejected(signal, reason)

        result.decision_log = {
            "instance_id": self._runner.instance_id,
            "timestamp": timestamp.isoformat(),
            "mode": "live",
            "signals_produced": serialized_signals,
        }

        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_tick_loop.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/tick_loop.py tests/worker/test_tick_loop.py
git commit -m "feat(worker): add tick processor with signal approval flow"
```

---

### Task 7: Worker Agent (WebSocket Client)

**Files:**
- Create: `worker/agent.py`
- Create: `tests/worker/test_agent.py`

The agent is the main worker process. It maintains a WebSocket connection to the coordinator, dispatches commands (start/stop algorithm), sends heartbeats, and relays events from runners back to the coordinator. For testing, we mock the WebSocket.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_agent.py
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from worker.agent import WorkerAgent, MessageRouter


@pytest.mark.asyncio
async def test_message_router_dispatches():
    router = MessageRouter()
    received = []

    async def handler(msg):
        received.append(msg)

    router.register("start_algorithm", handler)
    await router.dispatch({"type": "start_algorithm", "instance_id": "i-1"})
    assert len(received) == 1
    assert received[0]["instance_id"] == "i-1"


@pytest.mark.asyncio
async def test_message_router_unknown_type():
    router = MessageRouter()
    await router.dispatch({"type": "unknown_command"})


@pytest.mark.asyncio
async def test_agent_sends_heartbeat():
    ws = AsyncMock()
    agent = WorkerAgent(
        worker_name="test-pi",
        websocket=ws,
    )
    await agent.send_heartbeat()
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "heartbeat"
    assert sent["worker_name"] == "test-pi"


@pytest.mark.asyncio
async def test_agent_sends_event():
    ws = AsyncMock()
    agent = WorkerAgent(
        worker_name="test-pi",
        websocket=ws,
    )
    await agent.send_event(
        event_type="trade_executed",
        instance_id="inst-1",
        payload={"symbol": "AAPL", "side": "buy"},
    )
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "trade_executed"
    assert sent["instance_id"] == "inst-1"
    assert sent["payload"]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_agent_sends_signal_request():
    ws = AsyncMock()
    ws.recv.return_value = json.dumps({"type": "signal_approved", "approved": True})
    agent = WorkerAgent(
        worker_name="test-pi",
        websocket=ws,
    )
    result = await agent.request_signal_approval(
        instance_id="inst-1",
        signal={"legs": [{"symbol": "AAPL"}]},
    )
    assert result["approved"] is True
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "signal_request"


@pytest.mark.asyncio
async def test_agent_sends_state_checkpoint():
    ws = AsyncMock()
    agent = WorkerAgent(
        worker_name="test-pi",
        websocket=ws,
    )
    await agent.send_state_checkpoint(
        instance_id="inst-1",
        state={"positions": ["AAPL"]},
    )
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "state_checkpoint"
    assert sent["state"]["positions"] == ["AAPL"]


@pytest.mark.asyncio
async def test_agent_sends_decision_log():
    ws = AsyncMock()
    agent = WorkerAgent(
        worker_name="test-pi",
        websocket=ws,
    )
    await agent.send_decision_log(
        instance_id="inst-1",
        log_entry={"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
    )
    ws.send.assert_called_once()
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["type"] == "decision_log"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_agent.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# worker/agent.py
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict], Coroutine[Any, Any, None]]


class MessageRouter:
    def __init__(self) -> None:
        self._handlers: dict[str, EventHandler] = {}

    def register(self, message_type: str, handler: EventHandler) -> None:
        self._handlers[message_type] = handler

    async def dispatch(self, message: dict) -> None:
        msg_type = message.get("type")
        handler = self._handlers.get(msg_type)
        if handler:
            await handler(message)
        else:
            logger.debug("No handler for message type: %s", msg_type)


class WorkerAgent:
    def __init__(
        self,
        worker_name: str,
        websocket: Any,
    ) -> None:
        self.worker_name = worker_name
        self._ws = websocket
        self.router = MessageRouter()

    async def _send(self, data: dict) -> None:
        await self._ws.send(json.dumps(data))

    async def _recv(self) -> dict:
        raw = await self._ws.recv()
        return json.loads(raw)

    async def send_heartbeat(self) -> None:
        await self._send({
            "type": "heartbeat",
            "worker_name": self.worker_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def send_event(
        self,
        event_type: str,
        instance_id: str,
        payload: Optional[dict] = None,
    ) -> None:
        await self._send({
            "type": event_type,
            "instance_id": instance_id,
            "payload": payload or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def request_signal_approval(
        self,
        instance_id: str,
        signal: dict,
    ) -> dict:
        await self._send({
            "type": "signal_request",
            "instance_id": instance_id,
            "signal": signal,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        response = await self._recv()
        return response

    async def send_state_checkpoint(
        self, instance_id: str, state: dict
    ) -> None:
        await self._send({
            "type": "state_checkpoint",
            "instance_id": instance_id,
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def send_decision_log(
        self, instance_id: str, log_entry: dict
    ) -> None:
        await self._send({
            "type": "decision_log",
            "instance_id": instance_id,
            "log_entry": log_entry,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/test_agent.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/agent.py tests/worker/test_agent.py
git commit -m "feat(worker): add worker agent with WebSocket messaging"
```

---

### Task 8: Worker Entry Point

**Files:**
- Create: `worker/main.py`

- [ ] **Step 1: Write the entry point**

```python
# worker/main.py
import argparse
import asyncio
import logging
import signal
import sys

from worker.config import WorkerConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_worker(config: WorkerConfig) -> None:
    import websockets
    from worker.agent import WorkerAgent
    from worker.data_client import DataClient

    logger.info(
        "Starting worker '%s', connecting to %s",
        config.worker_name,
        config.coordinator_url,
    )

    data_client = DataClient(
        base_url=config.coordinator_http_url,
        cache_ttl=config.data_cache_ttl,
    )

    ws_url = f"{config.coordinator_url}/ws/worker"
    async for websocket in websockets.connect(ws_url):
        try:
            agent = WorkerAgent(
                worker_name=config.worker_name,
                websocket=websocket,
            )
            logger.info("Connected to coordinator")

            # Heartbeat task
            async def heartbeat_loop():
                while True:
                    await agent.send_heartbeat()
                    await asyncio.sleep(config.heartbeat_interval)

            heartbeat_task = asyncio.create_task(heartbeat_loop())

            try:
                async for raw_message in websocket:
                    import json
                    message = json.loads(raw_message)
                    await agent.router.dispatch(message)
            finally:
                heartbeat_task.cancel()

        except websockets.ConnectionClosed:
            logger.warning("Connection to coordinator lost, reconnecting...")
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="QuiltTrader Worker Agent")
    parser.add_argument(
        "--coordinator-url",
        help="WebSocket URL of the coordinator (e.g. ws://100.64.0.1:8000)",
    )
    parser.add_argument("--name", help="Worker name")
    args = parser.parse_args()

    config = WorkerConfig()
    if args.coordinator_url:
        config.coordinator_url = args.coordinator_url
    if args.name:
        config.worker_name = args.name

    try:
        asyncio.run(run_worker(config))
    except KeyboardInterrupt:
        logger.info("Worker shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the module is importable**

Run: `cd /home/jkern/dev/quilt-trader && python -c "from worker.main import main; print('Worker main importable')"`
Expected: `Worker main importable`

- [ ] **Step 3: Commit**

```bash
git add worker/main.py
git commit -m "feat(worker): add entry point with reconnecting WebSocket loop"
```

---

### Task 9: Update pyproject.toml with Worker Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add worker dependency group to pyproject.toml**

Add to `[project.optional-dependencies]`:

```toml
worker = [
    "websockets>=13.0",
    "httpx>=0.27.0",
    "pandas>=2.0.0",
    "pydantic-settings>=2.6.0",
]
```

- [ ] **Step 2: Install and verify**

Run: `cd /home/jkern/dev/quilt-trader && pip install -e ".[worker,dev]"`
Expected: All dependencies install successfully

- [ ] **Step 3: Run full worker test suite**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add worker dependencies to pyproject.toml"
```

---

### Task 10: Final Integration Verification

**Files:** None (verification only)

- [ ] **Step 1: Run full worker test suite**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/worker/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Verify all worker modules are importable**

Run: `cd /home/jkern/dev/quilt-trader && python -c "from worker.config import WorkerConfig; from worker.data_client import DataClient; from worker.broker_adapter import BrokerAdapter, MockBrokerAdapter, OrderResult; from worker.context import LiveTickContext; from worker.runner import AlgorithmRunner, RunnerState; from worker.tick_loop import TickProcessor, TickResult; from worker.agent import WorkerAgent, MessageRouter; print('All worker modules imported successfully')"`
Expected: `All worker modules imported successfully`

- [ ] **Step 3: Run all tests (SDK + coordinator + worker)**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -v --tb=short`
Expected: All tests pass across all packages
