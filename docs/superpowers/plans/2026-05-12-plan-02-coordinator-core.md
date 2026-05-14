# Plan 2: Coordinator Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the coordinator's core infrastructure — database models, migrations, FastAPI app, config management, credential encryption, event bus, and REST API routes for CRUD operations on accounts, workers, algorithms, and settings.

**Architecture:** The coordinator is a FastAPI application backed by SQLite via SQLAlchemy (async with aiosqlite). All 18 tables from the spec become SQLAlchemy models. Alembic manages schema migrations. A Fernet-based encryption service handles credential storage at rest. An in-process event bus (async) provides typed event routing. REST API routes provide CRUD for all entities, and a WebSocket endpoint scaffolds real-time communication with both the dashboard and workers.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy (async), aiosqlite, Alembic, cryptography (Fernet), pydantic, uvicorn, pytest, httpx (test client)

---

## File Map

| File | Responsibility |
|------|---------------|
| `coordinator/__init__.py` | Package marker |
| `coordinator/main.py` | FastAPI app factory, lifespan, middleware, static file serving |
| `coordinator/config.py` | Pydantic Settings for coordinator configuration |
| `coordinator/database/__init__.py` | Package marker |
| `coordinator/database/connection.py` | Async engine + session factory |
| `coordinator/database/models.py` | All 18 SQLAlchemy models |
| `coordinator/database/migrations/env.py` | Alembic environment |
| `coordinator/database/migrations/script.py.mako` | Alembic migration template |
| `coordinator/services/__init__.py` | Package marker |
| `coordinator/services/encryption.py` | Fernet credential encryption/decryption |
| `coordinator/services/event_bus.py` | In-process async event bus with typed events |
| `coordinator/api/__init__.py` | Package marker |
| `coordinator/api/dependencies.py` | FastAPI dependency injection (db session, services) |
| `coordinator/api/routes/__init__.py` | Package marker |
| `coordinator/api/routes/accounts.py` | Account CRUD endpoints |
| `coordinator/api/routes/workers.py` | Worker CRUD endpoints |
| `coordinator/api/routes/algorithms.py` | Algorithm + instance CRUD endpoints |
| `coordinator/api/routes/settings.py` | Settings endpoints (GitHub PAT, Discord token, data provider keys) |
| `coordinator/api/routes/events.py` | Event history query endpoints |
| `coordinator/api/websocket.py` | WebSocket handler scaffold (dashboard + worker connections) |
| `tests/coordinator/__init__.py` | Test package |
| `tests/coordinator/test_config.py` | Config loading tests |
| `tests/coordinator/test_models.py` | Model creation + relationship tests |
| `tests/coordinator/test_encryption.py` | Encryption service tests |
| `tests/coordinator/test_event_bus.py` | Event bus tests |
| `tests/coordinator/test_accounts_api.py` | Account API endpoint tests |
| `tests/coordinator/test_workers_api.py` | Worker API endpoint tests |
| `tests/coordinator/test_algorithms_api.py` | Algorithm API endpoint tests |
| `tests/coordinator/test_settings_api.py` | Settings API endpoint tests |
| `tests/coordinator/test_events_api.py` | Events API endpoint tests |
| `tests/coordinator/conftest.py` | Shared fixtures (test app, db session, test client) |

---

### Task 1: Coordinator Config

**Files:**
- Create: `coordinator/__init__.py`
- Create: `coordinator/config.py`
- Create: `tests/coordinator/__init__.py`
- Create: `tests/coordinator/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/__init__.py
# (empty)
```

```python
# tests/coordinator/test_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_config.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/__init__.py
# (empty)
```

```python
# coordinator/config.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/__init__.py coordinator/config.py tests/coordinator/__init__.py tests/coordinator/test_config.py
git commit -m "feat(coordinator): add config with pydantic-settings"
```

---

### Task 2: Database Connection

**Files:**
- Create: `coordinator/database/__init__.py`
- Create: `coordinator/database/connection.py`
- Create: `tests/coordinator/conftest.py`
- Create: `tests/coordinator/test_connection.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/conftest.py
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base


@pytest_asyncio.fixture
async def db_engine():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session:
        yield session
        await session.rollback()
```

```python
# tests/coordinator/test_connection.py
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_engine_connects(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_session_works(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_connection.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/database/__init__.py
# (empty)
```

```python
# coordinator/database/connection.py
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        echo=False,
        connect_args={"check_same_thread": False},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_connection.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/database/__init__.py coordinator/database/connection.py tests/coordinator/conftest.py tests/coordinator/test_connection.py
git commit -m "feat(coordinator): add async database connection layer"
```

---

### Task 3: Database Models — Core Tables (Accounts, Algorithms, Workers)

**Files:**
- Create: `coordinator/database/models.py`
- Create: `tests/coordinator/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_models.py
import pytest
from datetime import datetime, timezone
from sqlalchemy import select

from coordinator.database.models import Account, Algorithm, Worker


@pytest.mark.asyncio
async def test_create_account(db_session):
    account = Account(
        name="Alpaca Main",
        broker_type="alpaca",
        credentials="encrypted-blob",
        supported_asset_types=["equities", "options", "crypto"],
        options_level=3,
        account_features=["margin", "short_selling"],
        pdt_mode="warn",
    )
    db_session.add(account)
    await db_session.flush()

    result = await db_session.execute(select(Account).where(Account.name == "Alpaca Main"))
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.broker_type == "alpaca"
    assert fetched.supported_asset_types == ["equities", "options", "crypto"]
    assert fetched.options_level == 3
    assert fetched.account_features == ["margin", "short_selling"]
    assert fetched.pdt_mode == "warn"
    assert fetched.locked_by is None
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_create_algorithm(db_session):
    algo = Algorithm(
        repo_url="https://github.com/ElectricJack/momentum-scalper",
        name="momentum-scalper",
        description="Intraday momentum scalping strategy",
        version="1.0.0",
        commit_hash="abc123",
        required_asset_types=["equities", "options"],
        required_options_level=3,
        required_account_features=["margin"],
        supported_brokers=None,
        data_dependencies=[{"name": "alpha-picks-scraper", "repo": "ElectricJack/alpha-picks-scraper"}],
        config_schema={"parameters": [{"name": "risk_per_trade", "type": "float", "default": 0.02}]},
        custom_events=[{"name": "unusual_volume", "severity": "info"}],
        install_status="installed",
    )
    db_session.add(algo)
    await db_session.flush()

    result = await db_session.execute(select(Algorithm).where(Algorithm.name == "momentum-scalper"))
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.required_options_level == 3
    assert fetched.data_dependencies[0]["name"] == "alpha-picks-scraper"
    assert fetched.install_status == "installed"


@pytest.mark.asyncio
async def test_create_worker(db_session):
    worker = Worker(
        name="Pi Living Room",
        tailscale_ip="100.64.0.1",
        status="online",
        max_algorithms=3,
    )
    db_session.add(worker)
    await db_session.flush()

    result = await db_session.execute(select(Worker).where(Worker.name == "Pi Living Room"))
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.tailscale_ip == "100.64.0.1"
    assert fetched.status == "online"
    assert fetched.max_algorithms == 3


@pytest.mark.asyncio
async def test_account_default_timestamps(db_session):
    account = Account(
        name="Test",
        broker_type="tradier",
        credentials="enc",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()
    assert account.created_at is not None
    assert account.updated_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_models.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/database/models.py
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    ForeignKey,
    JSON,
    Date,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    broker_type: Mapped[str] = mapped_column(String, nullable=False)
    credentials: Mapped[str] = mapped_column(Text, nullable=False)
    supported_asset_types: Mapped[list] = mapped_column(JSON, nullable=False)
    options_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    account_features: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    pdt_mode: Mapped[str] = mapped_column(String, nullable=False, default="off")
    locked_by: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    instances: Mapped[list["AlgorithmInstance"]] = relationship(
        back_populates="account", foreign_keys="AlgorithmInstance.account_id"
    )
    cash_flows: Mapped[list["AccountCashFlow"]] = relationship(back_populates="account")
    snapshots: Mapped[list["AccountSnapshot"]] = relationship(back_populates="account")


class Algorithm(Base):
    __tablename__ = "algorithms"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    repo_url: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    commit_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    required_asset_types: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    required_options_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    required_account_features: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    supported_brokers: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    data_dependencies: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    config_schema: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    custom_events: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    install_status: Mapped[str] = mapped_column(String, nullable=False, default="installed")
    install_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    instances: Mapped[list["AlgorithmInstance"]] = relationship(back_populates="algorithm")


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tailscale_ip: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="offline")
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    max_algorithms: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    instances: Mapped[list["AlgorithmInstance"]] = relationship(back_populates="worker")


class AlgorithmInstance(Base):
    __tablename__ = "algorithm_instances"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    algorithm_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithms.id"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    worker_id: Mapped[str] = mapped_column(
        String, ForeignKey("workers.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="stopped")
    active_run_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("algorithm_runs.id"), nullable=True
    )
    config_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    persisted_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    state_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lifetime_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    algorithm: Mapped["Algorithm"] = relationship(back_populates="instances")
    account: Mapped["Account"] = relationship(
        back_populates="instances", foreign_keys=[account_id]
    )
    worker: Mapped["Worker"] = relationship(back_populates="instances")
    runs: Mapped[list["AlgorithmRun"]] = relationship(
        back_populates="instance", foreign_keys="AlgorithmRun.instance_id"
    )


class Scraper(Base):
    __tablename__ = "scrapers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    repo_url: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    commit_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    schedule: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    output_format: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    output_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="stopped")
    dependent_algorithm_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_success: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class AlgorithmRun(Base):
    __tablename__ = "algorithm_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    instance_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=False
    )
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    starting_equity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ending_equity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_slippage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    instance: Mapped["AlgorithmInstance"] = relationship(
        back_populates="runs", foreign_keys=[instance_id]
    )


class TradeLog(Base):
    __tablename__ = "trade_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    group_id: Mapped[str] = mapped_column(String, nullable=False, default=_new_uuid)
    instance_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=True
    )
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    position_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("positions.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False, default="equities")
    side: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    order_type: Mapped[str] = mapped_column(String, nullable=False, default="market")
    requested_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filled_price: Mapped[float] = mapped_column(Float, nullable=False)
    fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fee_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    slippage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_day_trade: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)


class DecisionLog(Base):
    __tablename__ = "decision_log"
    __table_args__ = (
        Index("ix_decision_log_instance_mode_ts", "instance_id", "mode", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    instance_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    tick_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    signals_produced: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    reasoning: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    data_sources_used: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    routed_to_discord: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    discord_channel: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    type: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_updated: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)


class BacktestComparison(Base):
    __tablename__ = "backtest_comparisons"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    instance_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=False
    )
    algorithm_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithms.id"), nullable=False
    )
    time_range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    matching_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    match_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    divergences: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PDTTracking(Base):
    __tablename__ = "pdt_tracking"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    trade_id: Mapped[str] = mapped_column(
        String, ForeignKey("trade_log.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    open_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    day_trade_date: Mapped[datetime] = mapped_column(Date, nullable=False)


class MarketDataDownload(Base):
    __tablename__ = "market_data_downloads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    symbols: Mapped[list] = mapped_column(JSON, nullable=False)
    date_range_start: Mapped[datetime] = mapped_column(Date, nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    data_type: Mapped[str] = mapped_column(String, nullable=False, default="bars")
    timeframe: Mapped[str] = mapped_column(String, nullable=False, default="1day")
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DataArchival(Base):
    __tablename__ = "data_archival"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    date_range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    instance_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=True
    )
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    strategy_type: Mapped[str] = mapped_column(String, nullable=False, default="single")
    legs: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    open_group_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    close_group_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    net_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_proceeds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    adjustments: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)


class AccountCashFlow(Base):
    __tablename__ = "account_cash_flows"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    account: Mapped["Account"] = relationship(back_populates="cash_flows")


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    total_value: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    positions_value: Mapped[float] = mapped_column(Float, nullable=False)
    net_deposits_cumulative: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String, nullable=False)

    account: Mapped["Account"] = relationship(back_populates="snapshots")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/database/models.py tests/coordinator/test_models.py
git commit -m "feat(coordinator): add all 18 SQLAlchemy database models"
```

---

### Task 4: Database Models — Relationship Tests

**Files:**
- Modify: `tests/coordinator/test_models.py`

- [ ] **Step 1: Write the failing test**

Add these tests to `tests/coordinator/test_models.py`:

```python
@pytest.mark.asyncio
async def test_algorithm_instance_relationships(db_session):
    account = Account(
        name="Test Account",
        broker_type="alpaca",
        credentials="enc",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    algo = Algorithm(
        repo_url="https://github.com/test/algo",
        name="test-algo",
        install_status="installed",
    )
    worker = Worker(
        name="Test Worker",
        tailscale_ip="100.64.0.2",
        status="online",
    )
    db_session.add_all([account, algo, worker])
    await db_session.flush()

    instance = AlgorithmInstance(
        algorithm_id=algo.id,
        account_id=account.id,
        worker_id=worker.id,
        status="stopped",
    )
    db_session.add(instance)
    await db_session.flush()

    result = await db_session.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == instance.id)
    )
    fetched = result.scalar_one()
    assert fetched.algorithm_id == algo.id
    assert fetched.account_id == account.id
    assert fetched.worker_id == worker.id


@pytest.mark.asyncio
async def test_algorithm_run_belongs_to_instance(db_session):
    account = Account(
        name="Run Test Account",
        broker_type="alpaca",
        credentials="enc",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    algo = Algorithm(
        repo_url="https://github.com/test/algo",
        name="run-test-algo",
        install_status="installed",
    )
    worker = Worker(
        name="Run Test Worker",
        tailscale_ip="100.64.0.3",
        status="online",
    )
    db_session.add_all([account, algo, worker])
    await db_session.flush()

    instance = AlgorithmInstance(
        algorithm_id=algo.id,
        account_id=account.id,
        worker_id=worker.id,
        status="running",
    )
    db_session.add(instance)
    await db_session.flush()

    run = AlgorithmRun(
        instance_id=instance.id,
        run_number=1,
        status="running",
        starting_equity=50000.0,
    )
    db_session.add(run)
    await db_session.flush()

    result = await db_session.execute(
        select(AlgorithmRun).where(AlgorithmRun.instance_id == instance.id)
    )
    fetched = result.scalar_one()
    assert fetched.run_number == 1
    assert fetched.starting_equity == 50000.0
    assert fetched.status == "running"


@pytest.mark.asyncio
async def test_trade_log_creation(db_session):
    account = Account(
        name="Trade Account",
        broker_type="alpaca",
        credentials="enc",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    trade = TradeLog(
        account_id=account.id,
        source="manual",
        symbol="AAPL",
        asset_type="equities",
        side="buy",
        quantity=100.0,
        order_type="market",
        filled_price=150.50,
        fees=1.00,
        fee_breakdown={"commission": 0.50, "exchange_fee": 0.50},
    )
    db_session.add(trade)
    await db_session.flush()

    result = await db_session.execute(select(TradeLog).where(TradeLog.symbol == "AAPL"))
    fetched = result.scalar_one()
    assert fetched.filled_price == 150.50
    assert fetched.fee_breakdown["commission"] == 0.50
    assert fetched.group_id is not None


@pytest.mark.asyncio
async def test_position_creation(db_session):
    account = Account(
        name="Position Account",
        broker_type="alpaca",
        credentials="enc",
        supported_asset_types=["equities", "options"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    position = Position(
        account_id=account.id,
        strategy_type="bull_call_spread",
        legs=[
            {"symbol": "AAPL250620C00200000", "side": "buy", "quantity": 1, "avg_cost": 5.00, "asset_type": "options"},
            {"symbol": "AAPL250620C00210000", "side": "sell", "quantity": 1, "avg_cost": 2.50, "asset_type": "options"},
        ],
        status="open",
        net_cost=2.50,
    )
    db_session.add(position)
    await db_session.flush()

    result = await db_session.execute(select(Position).where(Position.id == position.id))
    fetched = result.scalar_one()
    assert fetched.strategy_type == "bull_call_spread"
    assert len(fetched.legs) == 2
    assert fetched.net_cost == 2.50
    assert fetched.status == "open"


@pytest.mark.asyncio
async def test_account_cash_flow_and_snapshot(db_session):
    account = Account(
        name="Cash Flow Account",
        broker_type="tradier",
        credentials="enc",
        supported_asset_types=["equities"],
        pdt_mode="off",
    )
    db_session.add(account)
    await db_session.flush()

    cash_flow = AccountCashFlow(
        account_id=account.id,
        type="deposit",
        amount=10000.0,
        notes="Initial deposit",
    )
    snapshot = AccountSnapshot(
        account_id=account.id,
        total_value=10000.0,
        cash=10000.0,
        positions_value=0.0,
        net_deposits_cumulative=10000.0,
        source="cash_flow",
    )
    db_session.add_all([cash_flow, snapshot])
    await db_session.flush()

    cf_result = await db_session.execute(
        select(AccountCashFlow).where(AccountCashFlow.account_id == account.id)
    )
    cf = cf_result.scalar_one()
    assert cf.type == "deposit"
    assert cf.amount == 10000.0

    snap_result = await db_session.execute(
        select(AccountSnapshot).where(AccountSnapshot.account_id == account.id)
    )
    snap = snap_result.scalar_one()
    assert snap.total_value == 10000.0
    assert snap.source == "cash_flow"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_models.py -v`
Expected: FAIL with NameError (new model imports not added yet)

- [ ] **Step 3: Add missing imports to test file**

Add to the imports at the top of `tests/coordinator/test_models.py`:

```python
from coordinator.database.models import (
    Account,
    Algorithm,
    Worker,
    AlgorithmInstance,
    AlgorithmRun,
    TradeLog,
    Position,
    AccountCashFlow,
    AccountSnapshot,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_models.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/coordinator/test_models.py
git commit -m "test(coordinator): add relationship and complex model tests"
```

---

### Task 5: Alembic Migration Setup

**Files:**
- Create: `coordinator/database/migrations/env.py`
- Create: `coordinator/database/migrations/script.py.mako`
- Create: `alembic.ini`

- [ ] **Step 1: Create alembic.ini**

```ini
# alembic.ini
[alembic]
script_location = coordinator/database/migrations
sqlalchemy.url = sqlite+aiosqlite:///data/quilt_trader.db

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Create migrations directory and env.py**

```bash
mkdir -p coordinator/database/migrations/versions
```

```python
# coordinator/database/migrations/env.py
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from coordinator.database.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Create script.py.mako template**

```mako
# coordinator/database/migrations/script.py.mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Generate initial migration**

Run: `cd /home/jkern/dev/quilt-trader && alembic revision --autogenerate -m "initial schema"`
Expected: Migration file created in `coordinator/database/migrations/versions/`

- [ ] **Step 5: Run migration against a fresh database**

Run: `cd /home/jkern/dev/quilt-trader && mkdir -p data && alembic upgrade head`
Expected: All tables created. Verify with:
Run: `cd /home/jkern/dev/quilt-trader && python -c "import sqlite3; conn = sqlite3.connect('data/quilt_trader.db'); print([t[0] for t in conn.execute('SELECT name FROM sqlite_master WHERE type=\"table\"').fetchall()]); conn.close()"`
Expected: List includes accounts, algorithms, workers, algorithm_instances, algorithm_runs, trade_log, decision_log, events, data_sources, backtest_comparisons, pdt_tracking, market_data_downloads, data_archival, positions, account_cash_flows, account_snapshots, alembic_version

- [ ] **Step 6: Commit**

```bash
git add alembic.ini coordinator/database/migrations/
git commit -m "feat(coordinator): add Alembic migration setup with initial schema"
```

---

### Task 6: Encryption Service

**Files:**
- Create: `coordinator/services/__init__.py`
- Create: `coordinator/services/encryption.py`
- Create: `tests/coordinator/test_encryption.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_encryption.py
import json
import pytest
from coordinator.services.encryption import EncryptionService


def test_encrypt_decrypt_roundtrip():
    svc = EncryptionService("test-key-that-is-32-bytes-long!!")
    plaintext = "super-secret-api-key"
    encrypted = svc.encrypt(plaintext)
    assert encrypted != plaintext
    assert svc.decrypt(encrypted) == plaintext


def test_encrypt_produces_different_ciphertexts():
    svc = EncryptionService("test-key-that-is-32-bytes-long!!")
    plaintext = "same-input"
    enc1 = svc.encrypt(plaintext)
    enc2 = svc.encrypt(plaintext)
    assert enc1 != enc2


def test_decrypt_wrong_key_fails():
    svc1 = EncryptionService("key-one-that-is-32-bytes-long!!")
    svc2 = EncryptionService("key-two-that-is-32-bytes-long!!")
    encrypted = svc1.encrypt("secret")
    with pytest.raises(Exception):
        svc2.decrypt(encrypted)


def test_encrypt_json_credentials():
    svc = EncryptionService("test-key-that-is-32-bytes-long!!")
    creds = {"api_key": "pk_123", "api_secret": "sk_456"}
    encrypted = svc.encrypt_json(creds)
    decrypted = svc.decrypt_json(encrypted)
    assert decrypted == creds
    assert decrypted["api_key"] == "pk_123"


def test_encrypt_empty_string():
    svc = EncryptionService("test-key-that-is-32-bytes-long!!")
    encrypted = svc.encrypt("")
    assert svc.decrypt(encrypted) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_encryption.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/__init__.py
# (empty)
```

```python
# coordinator/services/encryption.py
import base64
import hashlib
import json

from cryptography.fernet import Fernet


class EncryptionService:
    def __init__(self, key: str) -> None:
        derived = hashlib.sha256(key.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()

    def encrypt_json(self, data: dict) -> str:
        return self.encrypt(json.dumps(data))

    def decrypt_json(self, ciphertext: str) -> dict:
        return json.loads(self.decrypt(ciphertext))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_encryption.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/__init__.py coordinator/services/encryption.py tests/coordinator/test_encryption.py
git commit -m "feat(coordinator): add Fernet encryption service for credentials"
```

---

### Task 7: Event Bus

**Files:**
- Create: `coordinator/services/event_bus.py`
- Create: `tests/coordinator/test_event_bus.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_event_bus.py
import pytest
from coordinator.services.event_bus import EventBus, SystemEvent


@pytest.mark.asyncio
async def test_subscribe_and_publish():
    bus = EventBus()
    received = []

    async def handler(event: SystemEvent):
        received.append(event)

    bus.subscribe("trade_executed", handler)
    event = SystemEvent(
        event_type="trade_executed",
        source_type="algorithm",
        source_id="inst-123",
        severity="info",
        payload={"symbol": "AAPL", "side": "buy", "quantity": 100},
    )
    await bus.publish(event)
    assert len(received) == 1
    assert received[0].payload["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_multiple_subscribers():
    bus = EventBus()
    results_a = []
    results_b = []

    async def handler_a(event: SystemEvent):
        results_a.append(event)

    async def handler_b(event: SystemEvent):
        results_b.append(event)

    bus.subscribe("algo_started", handler_a)
    bus.subscribe("algo_started", handler_b)
    event = SystemEvent(
        event_type="algo_started",
        source_type="system",
        severity="info",
    )
    await bus.publish(event)
    assert len(results_a) == 1
    assert len(results_b) == 1


@pytest.mark.asyncio
async def test_wildcard_subscriber():
    bus = EventBus()
    received = []

    async def catch_all(event: SystemEvent):
        received.append(event)

    bus.subscribe("*", catch_all)
    await bus.publish(SystemEvent(event_type="trade_executed", source_type="algorithm", severity="info"))
    await bus.publish(SystemEvent(event_type="algo_error", source_type="system", severity="error"))
    assert len(received) == 2


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    received = []

    async def handler(event: SystemEvent):
        received.append(event)

    bus.subscribe("algo_stopped", handler)
    await bus.publish(SystemEvent(event_type="algo_stopped", source_type="system", severity="info"))
    assert len(received) == 1

    bus.unsubscribe("algo_stopped", handler)
    await bus.publish(SystemEvent(event_type="algo_stopped", source_type="system", severity="info"))
    assert len(received) == 1


@pytest.mark.asyncio
async def test_no_subscribers_does_not_error():
    bus = EventBus()
    event = SystemEvent(event_type="unknown_event", source_type="system", severity="info")
    await bus.publish(event)


@pytest.mark.asyncio
async def test_system_event_fields():
    event = SystemEvent(
        event_type="pdt_warning",
        source_type="system",
        source_id="account-456",
        severity="warning",
        payload={"day_trade_count": 3, "account_name": "Alpaca Main"},
    )
    assert event.event_type == "pdt_warning"
    assert event.source_type == "system"
    assert event.source_id == "account-456"
    assert event.severity == "warning"
    assert event.payload["day_trade_count"] == 3


@pytest.mark.asyncio
async def test_handler_error_does_not_break_other_handlers():
    bus = EventBus()
    results = []

    async def bad_handler(event: SystemEvent):
        raise ValueError("boom")

    async def good_handler(event: SystemEvent):
        results.append(event)

    bus.subscribe("test_event", bad_handler)
    bus.subscribe("test_event", good_handler)
    await bus.publish(SystemEvent(event_type="test_event", source_type="system", severity="info"))
    assert len(results) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_event_bus.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/event_bus.py
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


@dataclass
class SystemEvent:
    event_type: str
    source_type: str
    severity: str
    source_id: Optional[str] = None
    payload: Optional[dict[str, Any]] = None


EventHandler = Callable[[SystemEvent], Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: SystemEvent) -> None:
        handlers = list(self._handlers.get(event.event_type, []))
        handlers.extend(self._handlers.get("*", []))
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "Event handler %s failed for event %s",
                    handler.__name__,
                    event.event_type,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_event_bus.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/event_bus.py tests/coordinator/test_event_bus.py
git commit -m "feat(coordinator): add async event bus with typed events"
```

---

### Task 8: FastAPI App Factory + Dependencies

**Files:**
- Create: `coordinator/main.py`
- Create: `coordinator/api/__init__.py`
- Create: `coordinator/api/dependencies.py`
- Modify: `tests/coordinator/conftest.py`
- Create: `tests/coordinator/test_app.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_app.py
import pytest
from httpx import ASGITransport, AsyncClient

from coordinator.main import create_app


@pytest.mark.asyncio
async def test_app_health_endpoint():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_app_creates_tables():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_app.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/__init__.py
# (empty)
```

```python
# coordinator/api/dependencies.py
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.services.event_bus import EventBus
from coordinator.services.encryption import EncryptionService


class ServiceContainer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        encryption: EncryptionService,
    ) -> None:
        self.session_factory = session_factory
        self.event_bus = event_bus
        self.encryption = encryption


_container: ServiceContainer | None = None


def set_container(container: ServiceContainer) -> None:
    global _container
    _container = container


def get_container() -> ServiceContainer:
    assert _container is not None, "ServiceContainer not initialized"
    return _container


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    container = get_container()
    async with container.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

```python
# coordinator/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import Base
from coordinator.services.event_bus import EventBus
from coordinator.services.encryption import EncryptionService
from coordinator.api.dependencies import ServiceContainer, set_container


def create_app(
    database_url: str = "sqlite+aiosqlite:///data/quilt_trader.db",
    encryption_key: str = "default-dev-key-32-bytes-long!!!",
) -> FastAPI:
    engine = create_engine(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = create_session_factory(engine)
        event_bus = EventBus()
        encryption = EncryptionService(encryption_key)
        container = ServiceContainer(session_factory, event_bus, encryption)
        set_container(container)
        yield
        await engine.dispose()

    app = FastAPI(title="QuiltTrader", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health():
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_app.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Update conftest.py with test client fixture**

Add to `tests/coordinator/conftest.py`:

```python
from httpx import ASGITransport, AsyncClient
from coordinator.main import create_app


@pytest_asyncio.fixture
async def test_app():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    yield app


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        yield c
```

- [ ] **Step 6: Commit**

```bash
git add coordinator/main.py coordinator/api/__init__.py coordinator/api/dependencies.py tests/coordinator/test_app.py tests/coordinator/conftest.py
git commit -m "feat(coordinator): add FastAPI app factory with health endpoint"
```

---

### Task 9: Accounts API

**Files:**
- Create: `coordinator/api/routes/__init__.py`
- Create: `coordinator/api/routes/accounts.py`
- Create: `tests/coordinator/test_accounts_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_accounts_api.py
import pytest


@pytest.mark.asyncio
async def test_create_account(client):
    response = await client.post("/api/accounts", json={
        "name": "Alpaca Main",
        "broker_type": "alpaca",
        "credentials": {"api_key": "pk_123", "api_secret": "sk_456"},
        "supported_asset_types": ["equities", "options", "crypto"],
        "options_level": 3,
        "account_features": ["margin"],
        "pdt_mode": "warn",
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Alpaca Main"
    assert body["broker_type"] == "alpaca"
    assert body["supported_asset_types"] == ["equities", "options", "crypto"]
    assert body["options_level"] == 3
    assert body["pdt_mode"] == "warn"
    assert "id" in body
    assert "credentials" not in body


@pytest.mark.asyncio
async def test_list_accounts(client):
    await client.post("/api/accounts", json={
        "name": "Account 1",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k1"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    await client.post("/api/accounts", json={
        "name": "Account 2",
        "broker_type": "tradier",
        "credentials": {"api_key": "k2"},
        "supported_asset_types": ["equities", "options"],
        "pdt_mode": "block",
    })
    response = await client.get("/api/accounts")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2


@pytest.mark.asyncio
async def test_get_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "Get Test",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.get(f"/api/accounts/{account_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get Test"


@pytest.mark.asyncio
async def test_get_account_not_found(client):
    response = await client.get("/api/accounts/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "Before Update",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.patch(f"/api/accounts/{account_id}", json={
        "name": "After Update",
        "pdt_mode": "block",
    })
    assert response.status_code == 200
    assert response.json()["name"] == "After Update"
    assert response.json()["pdt_mode"] == "block"


@pytest.mark.asyncio
async def test_delete_account(client):
    create_resp = await client.post("/api/accounts", json={
        "name": "To Delete",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    account_id = create_resp.json()["id"]
    response = await client.delete(f"/api/accounts/{account_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/accounts/{account_id}")
    assert get_resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_accounts_api.py -v`
Expected: FAIL with 404 (routes not registered)

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/routes/__init__.py
# (empty)
```

```python
# coordinator/api/routes/accounts.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db, get_container
from coordinator.database.models import Account

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    name: str
    broker_type: str
    credentials: dict
    supported_asset_types: list[str]
    options_level: Optional[int] = None
    account_features: Optional[list[str]] = None
    pdt_mode: str = "off"


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    credentials: Optional[dict] = None
    supported_asset_types: Optional[list[str]] = None
    options_level: Optional[int] = None
    account_features: Optional[list[str]] = None
    pdt_mode: Optional[str] = None


class AccountResponse(BaseModel):
    id: str
    name: str
    broker_type: str
    supported_asset_types: list[str]
    options_level: Optional[int]
    account_features: Optional[list[str]]
    pdt_mode: str
    locked_by: Optional[str]
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


def _to_response(account: Account) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "broker_type": account.broker_type,
        "supported_asset_types": account.supported_asset_types,
        "options_level": account.options_level,
        "account_features": account.account_features,
        "pdt_mode": account.pdt_mode,
        "locked_by": account.locked_by,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "updated_at": account.updated_at.isoformat() if account.updated_at else None,
    }


@router.post("", status_code=201)
async def create_account(body: AccountCreate, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted_creds = container.encryption.encrypt_json(body.credentials)
    account = Account(
        name=body.name,
        broker_type=body.broker_type,
        credentials=encrypted_creds,
        supported_asset_types=body.supported_asset_types,
        options_level=body.options_level,
        account_features=body.account_features,
        pdt_mode=body.pdt_mode,
    )
    db.add(account)
    await db.flush()
    return _to_response(account)


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account))
    accounts = result.scalars().all()
    return [_to_response(a) for a in accounts]


@router.get("/{account_id}")
async def get_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return _to_response(account)


@router.patch("/{account_id}")
async def update_account(
    account_id: str, body: AccountUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    if body.name is not None:
        account.name = body.name
    if body.credentials is not None:
        container = get_container()
        account.credentials = container.encryption.encrypt_json(body.credentials)
    if body.supported_asset_types is not None:
        account.supported_asset_types = body.supported_asset_types
    if body.options_level is not None:
        account.options_level = body.options_level
    if body.account_features is not None:
        account.account_features = body.account_features
    if body.pdt_mode is not None:
        account.pdt_mode = body.pdt_mode

    await db.flush()
    return _to_response(account)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    await db.delete(account)
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py` after the health endpoint, before `return app`:

```python
    from coordinator.api.routes.accounts import router as accounts_router
    app.include_router(accounts_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_accounts_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/__init__.py coordinator/api/routes/accounts.py coordinator/main.py tests/coordinator/test_accounts_api.py
git commit -m "feat(coordinator): add accounts CRUD API with encrypted credentials"
```

---

### Task 10: Workers API

**Files:**
- Create: `coordinator/api/routes/workers.py`
- Create: `tests/coordinator/test_workers_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_workers_api.py
import pytest


@pytest.mark.asyncio
async def test_create_worker(client):
    response = await client.post("/api/workers", json={
        "name": "Pi Living Room",
        "tailscale_ip": "100.64.0.1",
        "max_algorithms": 3,
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Pi Living Room"
    assert body["tailscale_ip"] == "100.64.0.1"
    assert body["status"] == "offline"
    assert body["max_algorithms"] == 3
    assert "id" in body


@pytest.mark.asyncio
async def test_list_workers(client):
    await client.post("/api/workers", json={
        "name": "Pi A",
        "tailscale_ip": "100.64.0.1",
    })
    await client.post("/api/workers", json={
        "name": "Pi B",
        "tailscale_ip": "100.64.0.2",
    })
    response = await client.get("/api/workers")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_worker(client):
    create_resp = await client.post("/api/workers", json={
        "name": "Get Test Pi",
        "tailscale_ip": "100.64.0.3",
    })
    worker_id = create_resp.json()["id"]
    response = await client.get(f"/api/workers/{worker_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get Test Pi"


@pytest.mark.asyncio
async def test_get_worker_not_found(client):
    response = await client.get("/api/workers/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_worker(client):
    create_resp = await client.post("/api/workers", json={
        "name": "Old Name",
        "tailscale_ip": "100.64.0.4",
    })
    worker_id = create_resp.json()["id"]
    response = await client.patch(f"/api/workers/{worker_id}", json={
        "name": "New Name",
        "max_algorithms": 5,
    })
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"
    assert response.json()["max_algorithms"] == 5


@pytest.mark.asyncio
async def test_delete_worker(client):
    create_resp = await client.post("/api/workers", json={
        "name": "To Delete",
        "tailscale_ip": "100.64.0.5",
    })
    worker_id = create_resp.json()["id"]
    response = await client.delete(f"/api/workers/{worker_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/workers/{worker_id}")
    assert get_resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_workers_api.py -v`
Expected: FAIL with 404 (routes not registered)

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/routes/workers.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import Worker

router = APIRouter(prefix="/api/workers", tags=["workers"])


class WorkerCreate(BaseModel):
    name: str
    tailscale_ip: str
    max_algorithms: int = 2


class WorkerUpdate(BaseModel):
    name: Optional[str] = None
    tailscale_ip: Optional[str] = None
    max_algorithms: Optional[int] = None


def _to_response(worker: Worker) -> dict:
    return {
        "id": worker.id,
        "name": worker.name,
        "tailscale_ip": worker.tailscale_ip,
        "status": worker.status,
        "last_heartbeat": worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
        "max_algorithms": worker.max_algorithms,
        "created_at": worker.created_at.isoformat() if worker.created_at else None,
    }


@router.post("", status_code=201)
async def create_worker(body: WorkerCreate, db: AsyncSession = Depends(get_db)):
    worker = Worker(
        name=body.name,
        tailscale_ip=body.tailscale_ip,
        max_algorithms=body.max_algorithms,
    )
    db.add(worker)
    await db.flush()
    return _to_response(worker)


@router.get("")
async def list_workers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Worker))
    workers = result.scalars().all()
    return [_to_response(w) for w in workers]


@router.get("/{worker_id}")
async def get_worker(worker_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Worker).where(Worker.id == worker_id))
    worker = result.scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return _to_response(worker)


@router.patch("/{worker_id}")
async def update_worker(
    worker_id: str, body: WorkerUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Worker).where(Worker.id == worker_id))
    worker = result.scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")

    if body.name is not None:
        worker.name = body.name
    if body.tailscale_ip is not None:
        worker.tailscale_ip = body.tailscale_ip
    if body.max_algorithms is not None:
        worker.max_algorithms = body.max_algorithms

    await db.flush()
    return _to_response(worker)


@router.delete("/{worker_id}", status_code=204)
async def delete_worker(worker_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Worker).where(Worker.id == worker_id))
    worker = result.scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    await db.delete(worker)
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py` after the accounts router inclusion:

```python
    from coordinator.api.routes.workers import router as workers_router
    app.include_router(workers_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_workers_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/workers.py coordinator/main.py tests/coordinator/test_workers_api.py
git commit -m "feat(coordinator): add workers CRUD API"
```

---

### Task 11: Algorithms API

**Files:**
- Create: `coordinator/api/routes/algorithms.py`
- Create: `tests/coordinator/test_algorithms_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_algorithms_api.py
import pytest


@pytest.fixture
async def seed_entities(client):
    """Create account and worker needed for algorithm instances."""
    acct_resp = await client.post("/api/accounts", json={
        "name": "Test Acct",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    worker_resp = await client.post("/api/workers", json={
        "name": "Test Pi",
        "tailscale_ip": "100.64.0.1",
    })
    return {
        "account_id": acct_resp.json()["id"],
        "worker_id": worker_resp.json()["id"],
    }


@pytest.mark.asyncio
async def test_create_algorithm(client):
    response = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/ElectricJack/momentum-scalper",
        "name": "momentum-scalper",
        "description": "Intraday momentum",
        "version": "1.0.0",
        "commit_hash": "abc123",
        "required_asset_types": ["equities"],
        "config_schema": {"parameters": []},
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "momentum-scalper"
    assert body["install_status"] == "installed"


@pytest.mark.asyncio
async def test_list_algorithms(client):
    await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo1",
        "name": "algo-1",
    })
    await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo2",
        "name": "algo-2",
    })
    response = await client.get("/api/algorithms")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_algorithm(client):
    create_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo",
        "name": "test-algo",
    })
    algo_id = create_resp.json()["id"]
    response = await client.get(f"/api/algorithms/{algo_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "test-algo"


@pytest.mark.asyncio
async def test_delete_algorithm(client):
    create_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/delete-me",
        "name": "delete-me",
    })
    algo_id = create_resp.json()["id"]
    response = await client.delete(f"/api/algorithms/{algo_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_create_instance(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/inst-algo",
        "name": "inst-algo",
    })
    algo_id = algo_resp.json()["id"]

    response = await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
        "config_values": {"risk_per_trade": 0.02},
    })
    assert response.status_code == 201
    body = response.json()
    assert body["algorithm_id"] == algo_id
    assert body["account_id"] == seed_entities["account_id"]
    assert body["status"] == "stopped"
    assert body["config_values"] == {"risk_per_trade": 0.02}


@pytest.mark.asyncio
async def test_list_instances_for_algorithm(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/list-inst",
        "name": "list-inst",
    })
    algo_id = algo_resp.json()["id"]

    await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
    })
    response = await client.get(f"/api/algorithms/{algo_id}/instances")
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_get_instance(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/get-inst",
        "name": "get-inst",
    })
    algo_id = algo_resp.json()["id"]

    inst_resp = await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
    })
    inst_id = inst_resp.json()["id"]
    response = await client.get(f"/api/instances/{inst_id}")
    assert response.status_code == 200
    assert response.json()["id"] == inst_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_algorithms_api.py -v`
Expected: FAIL with 404 (routes not registered)

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/routes/algorithms.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import Algorithm, AlgorithmInstance

router = APIRouter(tags=["algorithms"])


class AlgorithmCreate(BaseModel):
    repo_url: str
    name: str
    description: Optional[str] = None
    version: Optional[str] = None
    commit_hash: Optional[str] = None
    required_asset_types: Optional[list[str]] = None
    required_options_level: Optional[int] = None
    required_account_features: Optional[list[str]] = None
    supported_brokers: Optional[list[str]] = None
    data_dependencies: Optional[list[dict]] = None
    config_schema: Optional[dict] = None
    custom_events: Optional[list[dict]] = None


class InstanceCreate(BaseModel):
    account_id: str
    worker_id: str
    config_values: Optional[dict] = None


def _algo_to_response(algo: Algorithm) -> dict:
    return {
        "id": algo.id,
        "repo_url": algo.repo_url,
        "name": algo.name,
        "description": algo.description,
        "version": algo.version,
        "commit_hash": algo.commit_hash,
        "required_asset_types": algo.required_asset_types,
        "required_options_level": algo.required_options_level,
        "required_account_features": algo.required_account_features,
        "supported_brokers": algo.supported_brokers,
        "data_dependencies": algo.data_dependencies,
        "config_schema": algo.config_schema,
        "custom_events": algo.custom_events,
        "install_status": algo.install_status,
        "install_error": algo.install_error,
        "installed_at": algo.installed_at.isoformat() if algo.installed_at else None,
        "updated_at": algo.updated_at.isoformat() if algo.updated_at else None,
    }


def _instance_to_response(inst: AlgorithmInstance) -> dict:
    return {
        "id": inst.id,
        "algorithm_id": inst.algorithm_id,
        "account_id": inst.account_id,
        "worker_id": inst.worker_id,
        "status": inst.status,
        "active_run_id": inst.active_run_id,
        "config_values": inst.config_values,
        "persisted_state": inst.persisted_state,
        "state_stale": inst.state_stale,
        "lifetime_metrics": inst.lifetime_metrics,
        "created_at": inst.created_at.isoformat() if inst.created_at else None,
        "updated_at": inst.updated_at.isoformat() if inst.updated_at else None,
    }


@router.post("/api/algorithms", status_code=201)
async def create_algorithm(body: AlgorithmCreate, db: AsyncSession = Depends(get_db)):
    algo = Algorithm(
        repo_url=body.repo_url,
        name=body.name,
        description=body.description,
        version=body.version,
        commit_hash=body.commit_hash,
        required_asset_types=body.required_asset_types,
        required_options_level=body.required_options_level,
        required_account_features=body.required_account_features,
        supported_brokers=body.supported_brokers,
        data_dependencies=body.data_dependencies,
        config_schema=body.config_schema,
        custom_events=body.custom_events,
        install_status="installed",
    )
    db.add(algo)
    await db.flush()
    return _algo_to_response(algo)


@router.get("/api/algorithms")
async def list_algorithms(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Algorithm))
    return [_algo_to_response(a) for a in result.scalars().all()]


@router.get("/api/algorithms/{algorithm_id}")
async def get_algorithm(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))
    algo = result.scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")
    return _algo_to_response(algo)


@router.delete("/api/algorithms/{algorithm_id}", status_code=204)
async def delete_algorithm(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))
    algo = result.scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")
    await db.delete(algo)


@router.post("/api/algorithms/{algorithm_id}/instances", status_code=201)
async def create_instance(
    algorithm_id: str, body: InstanceCreate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Algorithm).where(Algorithm.id == algorithm_id))
    algo = result.scalar_one_or_none()
    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")

    instance = AlgorithmInstance(
        algorithm_id=algorithm_id,
        account_id=body.account_id,
        worker_id=body.worker_id,
        config_values=body.config_values,
        status="stopped",
    )
    db.add(instance)
    await db.flush()
    return _instance_to_response(instance)


@router.get("/api/algorithms/{algorithm_id}/instances")
async def list_instances(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.algorithm_id == algorithm_id)
    )
    return [_instance_to_response(i) for i in result.scalars().all()]


@router.get("/api/instances/{instance_id}")
async def get_instance(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return _instance_to_response(inst)
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py` after the workers router inclusion:

```python
    from coordinator.api.routes.algorithms import router as algorithms_router
    app.include_router(algorithms_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_algorithms_api.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/algorithms.py coordinator/main.py tests/coordinator/test_algorithms_api.py
git commit -m "feat(coordinator): add algorithms + instances CRUD API"
```

---

### Task 12: Settings API

**Files:**
- Create: `coordinator/api/routes/settings.py`
- Create: `tests/coordinator/test_settings_api.py`

The settings API manages system-wide configuration: GitHub PAT, Discord bot token, data provider credentials. These are stored encrypted. The API exposes set/get (with masking) operations.

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_settings_api.py
import pytest


@pytest.mark.asyncio
async def test_get_settings_empty(client):
    response = await client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["github_pat_set"] is False
    assert body["discord_bot_token_set"] is False
    assert body["polygon_api_key_set"] is False
    assert body["theta_data_set"] is False


@pytest.mark.asyncio
async def test_set_github_pat(client):
    response = await client.put("/api/settings/github-pat", json={
        "value": "ghp_1234567890abcdef",
    })
    assert response.status_code == 200
    assert response.json()["github_pat_set"] is True

    get_resp = await client.get("/api/settings")
    assert get_resp.json()["github_pat_set"] is True


@pytest.mark.asyncio
async def test_set_discord_token(client):
    response = await client.put("/api/settings/discord-token", json={
        "value": "MTIzNDU2Nzg5.discord.token",
    })
    assert response.status_code == 200
    assert response.json()["discord_bot_token_set"] is True


@pytest.mark.asyncio
async def test_set_polygon_key(client):
    response = await client.put("/api/settings/polygon-key", json={
        "value": "pk_abcdefghij",
    })
    assert response.status_code == 200
    assert response.json()["polygon_api_key_set"] is True


@pytest.mark.asyncio
async def test_set_theta_data_credentials(client):
    response = await client.put("/api/settings/theta-data", json={
        "username": "user@example.com",
        "password": "secret123",
    })
    assert response.status_code == 200
    assert response.json()["theta_data_set"] is True


@pytest.mark.asyncio
async def test_delete_github_pat(client):
    await client.put("/api/settings/github-pat", json={"value": "ghp_test"})
    response = await client.delete("/api/settings/github-pat")
    assert response.status_code == 200
    assert response.json()["github_pat_set"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_settings_api.py -v`
Expected: FAIL with 404 (routes not registered)

- [ ] **Step 3: Write minimal implementation**

Settings are stored in a `settings` table in SQLite (key-value, encrypted values).

First, add the Settings model to `coordinator/database/models.py`:

```python
class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
```

Then create the routes:

```python
# coordinator/api/routes/settings.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from coordinator.api.dependencies import get_db, get_container
from coordinator.database.models import Setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SingleValueBody(BaseModel):
    value: str


class ThetaDataBody(BaseModel):
    username: str
    password: str


async def _get_setting(db: AsyncSession, key: str) -> Optional[str]:
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        db.add(Setting(key=key, value=value))


async def _delete_setting(db: AsyncSession, key: str) -> None:
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        await db.delete(setting)


async def _settings_status(db: AsyncSession) -> dict:
    keys = ["github_pat", "discord_bot_token", "polygon_api_key", "theta_data_username"]
    result = {}
    for key in keys:
        val = await _get_setting(db, key)
        result[f"{key}_set"] = val is not None
    result["theta_data_set"] = result.pop("theta_data_username_set")
    return result


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    return await _settings_status(db)


@router.put("/github-pat")
async def set_github_pat(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted = container.encryption.encrypt(body.value)
    await _set_setting(db, "github_pat", encrypted)
    return await _settings_status(db)


@router.delete("/github-pat")
async def delete_github_pat(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "github_pat")
    return await _settings_status(db)


@router.put("/discord-token")
async def set_discord_token(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted = container.encryption.encrypt(body.value)
    await _set_setting(db, "discord_bot_token", encrypted)
    return await _settings_status(db)


@router.put("/polygon-key")
async def set_polygon_key(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted = container.encryption.encrypt(body.value)
    await _set_setting(db, "polygon_api_key", encrypted)
    return await _settings_status(db)


@router.put("/theta-data")
async def set_theta_data(body: ThetaDataBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    await _set_setting(db, "theta_data_username", container.encryption.encrypt(body.username))
    await _set_setting(db, "theta_data_password", container.encryption.encrypt(body.password))
    return await _settings_status(db)
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py`:

```python
    from coordinator.api.routes.settings import router as settings_router
    app.include_router(settings_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_settings_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/database/models.py coordinator/api/routes/settings.py coordinator/main.py tests/coordinator/test_settings_api.py
git commit -m "feat(coordinator): add settings API with encrypted credential storage"
```

---

### Task 13: Events API

**Files:**
- Create: `coordinator/api/routes/events.py`
- Create: `tests/coordinator/test_events_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_events_api.py
import pytest


@pytest.mark.asyncio
async def test_list_events_empty(client):
    response = await client.get("/api/events")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_create_and_list_events(client):
    # Manually insert events via the internal endpoint
    for i in range(3):
        await client.post("/api/events", json={
            "source_type": "system",
            "event_type": "algo_started",
            "severity": "info",
            "payload": {"index": i},
        })
    response = await client.get("/api/events")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


@pytest.mark.asyncio
async def test_filter_events_by_type(client):
    await client.post("/api/events", json={
        "source_type": "algorithm",
        "event_type": "trade_executed",
        "severity": "info",
    })
    await client.post("/api/events", json={
        "source_type": "system",
        "event_type": "algo_error",
        "severity": "error",
    })
    response = await client.get("/api/events?event_type=trade_executed")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "trade_executed"


@pytest.mark.asyncio
async def test_filter_events_by_severity(client):
    await client.post("/api/events", json={
        "source_type": "system",
        "event_type": "info_event",
        "severity": "info",
    })
    await client.post("/api/events", json={
        "source_type": "system",
        "event_type": "error_event",
        "severity": "error",
    })
    response = await client.get("/api/events?severity=error")
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_events_pagination(client):
    for i in range(25):
        await client.post("/api/events", json={
            "source_type": "system",
            "event_type": "bulk_event",
            "severity": "info",
            "payload": {"index": i},
        })
    response = await client.get("/api/events?limit=10&offset=0")
    body = response.json()
    assert len(body["items"]) == 10
    assert body["total"] == 25

    response2 = await client.get("/api/events?limit=10&offset=20")
    body2 = response2.json()
    assert len(body2["items"]) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_events_api.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/routes/events.py
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import Event

router = APIRouter(prefix="/api/events", tags=["events"])


class EventCreate(BaseModel):
    source_type: str
    source_id: Optional[str] = None
    event_type: str
    severity: str = "info"
    payload: Optional[dict] = None


def _to_response(event: Event) -> dict:
    return {
        "id": event.id,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "event_type": event.event_type,
        "severity": event.severity,
        "payload": event.payload,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "routed_to_discord": event.routed_to_discord,
        "discord_channel": event.discord_channel,
    }


@router.post("", status_code=201)
async def create_event(body: EventCreate, db: AsyncSession = Depends(get_db)):
    event = Event(
        source_type=body.source_type,
        source_id=body.source_id,
        event_type=body.event_type,
        severity=body.severity,
        payload=body.payload,
    )
    db.add(event)
    await db.flush()
    return _to_response(event)


@router.get("")
async def list_events(
    event_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Event)
    count_query = select(func.count(Event.id))

    if event_type:
        query = query.where(Event.event_type == event_type)
        count_query = count_query.where(Event.event_type == event_type)
    if severity:
        query = query.where(Event.severity == severity)
        count_query = count_query.where(Event.severity == severity)
    if source_type:
        query = query.where(Event.source_type == source_type)
        count_query = count_query.where(Event.source_type == source_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(desc(Event.timestamp)).offset(offset).limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()

    return {
        "items": [_to_response(e) for e in events],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py`:

```python
    from coordinator.api.routes.events import router as events_router
    app.include_router(events_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_events_api.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/events.py coordinator/main.py tests/coordinator/test_events_api.py
git commit -m "feat(coordinator): add events API with filtering and pagination"
```

---

### Task 14: WebSocket Handler Scaffold

**Files:**
- Create: `coordinator/api/websocket.py`
- Create: `tests/coordinator/test_websocket.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_websocket.py
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from coordinator.main import create_app


def test_dashboard_websocket_connects():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    client = TestClient(app)
    with client.websocket_connect("/ws/dashboard") as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data["type"] == "pong"


def test_worker_websocket_connects():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    client = TestClient(app)
    with client.websocket_connect("/ws/worker") as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data["type"] == "pong"


def test_dashboard_websocket_receives_events():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    client = TestClient(app)
    with client.websocket_connect("/ws/dashboard") as ws:
        ws.send_json({"type": "subscribe", "events": ["trade_executed"]})
        data = ws.receive_json()
        assert data["type"] == "subscribed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_websocket.py -v`
Expected: FAIL with connection refused or 404

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/websocket.py
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.dashboard_connections: list[WebSocket] = []
        self.worker_connections: dict[str, WebSocket] = {}

    async def connect_dashboard(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.dashboard_connections.append(websocket)

    async def disconnect_dashboard(self, websocket: WebSocket) -> None:
        if websocket in self.dashboard_connections:
            self.dashboard_connections.remove(websocket)

    async def connect_worker(self, websocket: WebSocket, worker_id: str = "unknown") -> None:
        await websocket.accept()
        self.worker_connections[worker_id] = websocket

    async def disconnect_worker(self, worker_id: str) -> None:
        self.worker_connections.pop(worker_id, None)

    async def broadcast_to_dashboards(self, message: dict) -> None:
        disconnected = []
        for ws in self.dashboard_connections:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            await self.disconnect_dashboard(ws)


manager = ConnectionManager()


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    await manager.connect_dashboard(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "subscribe":
                await websocket.send_json({"type": "subscribed", "events": data.get("events", [])})
    except WebSocketDisconnect:
        await manager.disconnect_dashboard(websocket)


@router.websocket("/ws/worker")
async def worker_websocket(websocket: WebSocket):
    await manager.connect_worker(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "heartbeat":
                await websocket.send_json({"type": "heartbeat_ack"})
    except WebSocketDisconnect:
        logger.info("Worker disconnected")
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py`:

```python
    from coordinator.api.websocket import router as ws_router
    app.include_router(ws_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_websocket.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/websocket.py coordinator/main.py tests/coordinator/test_websocket.py
git commit -m "feat(coordinator): add WebSocket handler scaffold for dashboard and workers"
```

---

### Task 15: Update pyproject.toml with Coordinator Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add coordinator dependency group to pyproject.toml**

Add the following to the existing `pyproject.toml` under `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
coordinator = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "aiosqlite>=0.20.0",
    "alembic>=1.14.0",
    "cryptography>=43.0.0",
    "pydantic-settings>=2.6.0",
    "httpx>=0.27.0",
]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
]
```

- [ ] **Step 2: Install and verify**

Run: `cd /home/jkern/dev/quilt-trader && pip install -e ".[coordinator,dev]"`
Expected: All dependencies install successfully

- [ ] **Step 3: Run full test suite**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -v`
Expected: All tests pass (SDK tests from Plan 1 + coordinator tests from this plan)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add coordinator dependencies to pyproject.toml"
```

---

### Task 16: Final Integration Verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full coordinator test suite**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Verify the app starts**

Run: `cd /home/jkern/dev/quilt-trader && QT_ENCRYPTION_KEY="test-key-that-is-32-bytes-long!!" python -c "from coordinator.main import create_app; app = create_app(); print('App created:', app.title, app.version)"`
Expected: `App created: QuiltTrader 0.1.0`

- [ ] **Step 3: Verify all models are importable**

Run: `cd /home/jkern/dev/quilt-trader && python -c "from coordinator.database.models import Account, Algorithm, Worker, AlgorithmInstance, AlgorithmRun, Scraper, TradeLog, DecisionLog, Event, DataSource, BacktestComparison, PDTTracking, MarketDataDownload, DataArchival, Position, AccountCashFlow, AccountSnapshot, Setting; print('All 18 models imported successfully')"`
Expected: `All 18 models imported successfully`

- [ ] **Step 4: Verify all services are importable**

Run: `cd /home/jkern/dev/quilt-trader && python -c "from coordinator.services.encryption import EncryptionService; from coordinator.services.event_bus import EventBus, SystemEvent; print('Services imported successfully')"`
Expected: `Services imported successfully`
