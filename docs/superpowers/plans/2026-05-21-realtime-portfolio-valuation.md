# Real-Time Portfolio Valuation & Historical Equity Curves — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace stale polled portfolio values and cash-flow-interpolated equity curves with mark-to-market history from actual prices and real-time WebSocket-pushed updates.

**Architecture:** Add Tradier and Alpaca as historical data providers. Replay broker transactions into a daily position ledger, join against daily close prices to materialize equity curves. During market hours, a PortfolioTracker service listens to live ticks and pushes mark-to-market updates via WebSocket. Dashboard switches from polling to WebSocket-driven updates for KPIs and extends equity charts with live intraday points.

**Tech Stack:** Python/FastAPI (coordinator), React/TypeScript (dashboard), APScheduler (background jobs), lightweight-charts (charting), WebSocket (real-time push), Parquet (price storage), SQLite (state).

**Spec:** `docs/superpowers/specs/2026-05-21-realtime-portfolio-valuation-design.md`

---

## Task 1: Database Migrations — New Tables + Setting

**Delegate to: Sonnet**

Add the `account_position_ledger` and `account_equity_daily` tables, and a `default_history_provider` row in the `settings` table.

**Files:**
- Create: `coordinator/database/migrations/versions/xxxx_position_ledger_and_equity_daily.py`
- Modify: `coordinator/database/models.py`

- [ ] **Step 1: Add SQLAlchemy models**

Add to `coordinator/database/models.py` after the `AccountCashFlow` class:

```python
class AccountPositionLedger(Base):
    __tablename__ = "account_position_ledger"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (
        UniqueConstraint("account_id", "date", "symbol", name="uq_ledger_acct_date_sym"),
    )


class AccountEquityDaily(Base):
    __tablename__ = "account_equity_daily"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_value: Mapped[float] = mapped_column(Float, nullable=False)
    positions_value: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    net_deposits_cumulative: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_equity_daily_acct_date"),
    )
```

Add imports for `date` from `datetime`, `Date` and `Boolean` from `sqlalchemy`, and `UniqueConstraint` from `sqlalchemy` at the top of the file.

- [ ] **Step 2: Create Alembic migration**

```bash
cd coordinator/database && python -m alembic revision --autogenerate -m "add position ledger and equity daily tables"
```

Review the generated migration to ensure it creates both tables with the unique constraints.

- [ ] **Step 3: Run migration**

```bash
cd coordinator/database && python -m alembic upgrade head
```

- [ ] **Step 4: Seed default_history_provider setting**

Add to the migration's `upgrade()` function (or as a separate data migration):

```python
op.execute(
    "INSERT OR IGNORE INTO settings (key, value) VALUES ('default_history_provider', '\"tradier\"')"
)
```

- [ ] **Step 5: Commit**

```bash
git add coordinator/database/models.py coordinator/database/migrations/
git commit -m "feat(db): add account_position_ledger and account_equity_daily tables"
```

---

## Task 2: Tradier Historical Data Provider

**Delegate to: Sonnet**

Implement a Tradier data provider that fetches historical daily bars via the `/markets/history` REST endpoint. Must match the existing `PolygonProvider` interface used by the download manager.

**Files:**
- Create: `coordinator/services/data_providers/tradier.py`
- Create: `tests/coordinator/services/data_providers/test_tradier.py`

**Reference:** The existing Polygon provider is at `coordinator/services/data_providers/polygon.py`. Match its `fetch_bars` signature exactly:

```python
async def fetch_bars(
    self,
    symbol: str,
    timeframe: str,
    start: date,
    end: date,
    on_page: PageCallback | None = None,
    on_status: StatusCallback | None = None,
    on_bars: BarsCallback | None = None,
) -> list[dict]
```

Each bar dict must have keys: `timestamp` (ISO string), `open`, `high`, `low`, `close`, `volume`.

Callback types are defined in `coordinator/services/data_providers/polygon.py` lines 7-15:
- `PageCallback = Callable[[int, int, float | None], Awaitable[None]]`
- `StatusCallback = Callable[[str], Awaitable[None]]`
- `BarsCallback = Callable[[list[dict]], Awaitable[None]]`

- [ ] **Step 1: Write failing tests**

```python
# tests/coordinator/services/data_providers/test_tradier.py
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_fetch_bars_returns_ohlcv_dicts():
    from coordinator.services.data_providers.tradier import TradierProvider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "history": {
            "day": [
                {"date": "2025-01-02", "open": 100.0, "high": 105.0,
                 "low": 99.0, "close": 103.0, "volume": 1000000},
                {"date": "2025-01-03", "open": 103.0, "high": 106.0,
                 "low": 102.0, "close": 105.0, "volume": 1200000},
            ]
        }
    }

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get.return_value = mock_response
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        provider = TradierProvider(access_token="test-token")
        bars = await provider.fetch_bars("AAPL", "1day", date(2025, 1, 2), date(2025, 1, 3))

    assert len(bars) == 2
    assert bars[0]["close"] == 103.0
    assert "timestamp" in bars[0]
    assert "open" in bars[0]
    assert "volume" in bars[0]


@pytest.mark.asyncio
async def test_fetch_bars_single_day_response():
    """Tradier returns a dict instead of list for single-day responses."""
    from coordinator.services.data_providers.tradier import TradierProvider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "history": {
            "day": {"date": "2025-01-02", "open": 100.0, "high": 105.0,
                    "low": 99.0, "close": 103.0, "volume": 1000000}
        }
    }

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get.return_value = mock_response
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        provider = TradierProvider(access_token="test-token")
        bars = await provider.fetch_bars("AAPL", "1day", date(2025, 1, 2), date(2025, 1, 2))

    assert len(bars) == 1


@pytest.mark.asyncio
async def test_fetch_bars_empty_history():
    from coordinator.services.data_providers.tradier import TradierProvider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"history": None}

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get.return_value = mock_response
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = client_instance

        provider = TradierProvider(access_token="test-token")
        bars = await provider.fetch_bars("AAPL", "1day", date(2025, 6, 1), date(2025, 6, 5))

    assert bars == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/coordinator/services/data_providers/test_tradier.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'coordinator.services.data_providers.tradier'`

- [ ] **Step 3: Implement TradierProvider**

```python
# coordinator/services/data_providers/tradier.py
"""Tradier historical market data provider.

Fetches daily OHLCV bars via the Tradier /markets/history endpoint.
Free with any Tradier brokerage account. Supports 10+ years of history.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

PageCallback = Callable[[int, int, "float | None"], Awaitable[None]]
StatusCallback = Callable[[str], Awaitable[None]]
BarsCallback = Callable[[list[dict]], Awaitable[None]]

_LIVE_BASE = "https://api.tradier.com/v1"
_SANDBOX_BASE = "https://sandbox.tradier.com/v1"


class TradierProvider:
    def __init__(
        self,
        access_token: str,
        sandbox: bool = False,
        min_request_interval_s: float = 0.2,
    ) -> None:
        self._access_token = access_token
        self._base_url = _SANDBOX_BASE if sandbox else _LIVE_BASE
        self._min_interval = min_request_interval_s
        self._last_request_ts: float = 0.0

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
        on_page: PageCallback | None = None,
        on_status: StatusCallback | None = None,
        on_bars: BarsCallback | None = None,
    ) -> list[dict]:
        if timeframe != "1day":
            raise ValueError(f"Tradier provider only supports 1day timeframe, got {timeframe!r}")

        import asyncio, time
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        if on_status:
            await on_status(f"Fetching {symbol} from Tradier ({start} to {end})")

        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
            timeout=30.0,
        ) as client:
            resp = await client.get("/markets/history", params={
                "symbol": symbol,
                "interval": "daily",
                "start": start.isoformat(),
                "end": end.isoformat(),
            })
            self._last_request_ts = time.monotonic()
            resp.raise_for_status()

        data = resp.json()
        history = data.get("history")
        if not history:
            return []

        days = history.get("day") or []
        if isinstance(days, dict):
            days = [days]

        bars = [
            {
                "timestamp": datetime.strptime(d["date"], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).isoformat(),
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "volume": int(d["volume"]),
            }
            for d in days
        ]

        if on_bars and bars:
            await on_bars(bars)
        if on_page:
            await on_page(0, len(bars), 1.0)

        return bars
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/coordinator/services/data_providers/test_tradier.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/data_providers/tradier.py tests/coordinator/services/data_providers/
git commit -m "feat(data): add Tradier historical data provider"
```

---

## Task 3: Alpaca Historical Data Provider

**Delegate to: Sonnet**

Same interface as Tradier provider but using the alpaca-py SDK's `StockHistoricalDataClient.get_stock_bars()`.

**Files:**
- Create: `coordinator/services/data_providers/alpaca.py`
- Create: `tests/coordinator/services/data_providers/test_alpaca.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/coordinator/services/data_providers/test_alpaca.py
import pytest
from datetime import date
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_fetch_bars_returns_ohlcv_dicts():
    from coordinator.services.data_providers.alpaca import AlpacaProvider

    mock_bar = MagicMock()
    mock_bar.timestamp = "2025-01-02T05:00:00+00:00"
    mock_bar.open = 150.0
    mock_bar.high = 155.0
    mock_bar.low = 149.0
    mock_bar.close = 153.0
    mock_bar.volume = 5000000

    with patch("coordinator.services.data_providers.alpaca.StockHistoricalDataClient") as MockClient:
        instance = MockClient.return_value
        instance.get_stock_bars.return_value = {"AAPL": [mock_bar]}

        provider = AlpacaProvider(api_key="test", secret_key="test")
        bars = await provider.fetch_bars("AAPL", "1day", date(2025, 1, 2), date(2025, 1, 2))

    assert len(bars) == 1
    assert bars[0]["close"] == 153.0
    assert "timestamp" in bars[0]


@pytest.mark.asyncio
async def test_fetch_bars_empty_result():
    from coordinator.services.data_providers.alpaca import AlpacaProvider

    with patch("coordinator.services.data_providers.alpaca.StockHistoricalDataClient") as MockClient:
        instance = MockClient.return_value
        instance.get_stock_bars.return_value = {}

        provider = AlpacaProvider(api_key="test", secret_key="test")
        bars = await provider.fetch_bars("XYZ", "1day", date(2025, 1, 2), date(2025, 1, 5))

    assert bars == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/coordinator/services/data_providers/test_alpaca.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement AlpacaProvider**

```python
# coordinator/services/data_providers/alpaca.py
"""Alpaca historical market data provider.

Fetches daily OHLCV bars via the alpaca-py SDK. Requires a paid Alpaca
data subscription for historical bars — free tier returns empty results.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timezone
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

PageCallback = Callable[[int, int, "float | None"], Awaitable[None]]
StatusCallback = Callable[[str], Awaitable[None]]
BarsCallback = Callable[[list[dict]], Awaitable[None]]

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

_TF_MAP = {
    "1min": TimeFrame.Minute,
    "5min": TimeFrame(5, "Min"),
    "15min": TimeFrame(15, "Min"),
    "1hour": TimeFrame.Hour,
    "1day": TimeFrame.Day,
}


class AlpacaProvider:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        min_request_interval_s: float = 0.2,
    ) -> None:
        self._client = StockHistoricalDataClient(api_key, secret_key)
        self._min_interval = min_request_interval_s
        self._last_request_ts: float = 0.0

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
        on_page: PageCallback | None = None,
        on_status: StatusCallback | None = None,
        on_bars: BarsCallback | None = None,
    ) -> list[dict]:
        tf = _TF_MAP.get(timeframe)
        if tf is None:
            raise ValueError(f"Unsupported timeframe {timeframe!r}")

        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

        if on_status:
            await on_status(f"Fetching {symbol} from Alpaca ({start} to {end})")

        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=tf,
            start=datetime(start.year, start.month, start.day),
            end=datetime(end.year, end.month, end.day),
        )
        raw = await asyncio.to_thread(self._client.get_stock_bars, req)
        self._last_request_ts = time.monotonic()

        raw_bars = list(raw.get(symbol, []))
        bars = [
            {
                "timestamp": (
                    b.timestamp.isoformat()
                    if hasattr(b.timestamp, "isoformat")
                    else str(b.timestamp)
                ),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": int(b.volume),
            }
            for b in raw_bars
        ]

        if on_bars and bars:
            await on_bars(bars)
        if on_page:
            await on_page(0, len(bars), 1.0)

        return bars
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/coordinator/services/data_providers/test_alpaca.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/data_providers/alpaca.py tests/coordinator/services/data_providers/
git commit -m "feat(data): add Alpaca historical data provider"
```

---

## Task 4: Register New Providers in Download Manager

**Delegate to: Sonnet**

Wire the Tradier and Alpaca providers into the coordinator startup alongside Polygon. Add `default_history_provider` to the config.

**Files:**
- Modify: `coordinator/main.py` (around lines 55-80 where providers are built)
- Modify: `coordinator/config.py` (add setting)

- [ ] **Step 1: Add config field**

In `coordinator/config.py`, add to the `CoordinatorConfig` class:

```python
default_history_provider: str = "tradier"
```

- [ ] **Step 2: Build providers dict in main.py**

In `coordinator/main.py`, find where the Polygon provider is created and the download manager is initialized. Add Tradier and Alpaca providers to the `providers` dict. The Tradier provider needs an access_token from any connected Tradier account. The Alpaca provider needs api_key/secret_key from any connected Alpaca account.

Look for where `PolygonProvider` is instantiated and `DownloadManager` is constructed. Add after the Polygon provider:

```python
# Tradier provider — use credentials from first Tradier account if available.
tradier_provider = None
alpaca_provider = None
try:
    from coordinator.services.data_providers.tradier import TradierProvider
    from coordinator.services.data_providers.alpaca import AlpacaProvider
    import json as _json
    async with session_factory() as _sess:
        from coordinator.database.models import Account
        from sqlalchemy import select
        _tradier_acct = (await _sess.execute(
            select(Account).where(Account.broker_type == "tradier").limit(1)
        )).scalar_one_or_none()
        if _tradier_acct:
            _creds = _json.loads(encryption.decrypt(_tradier_acct.credentials))
            tradier_provider = TradierProvider(
                access_token=_creds["access_token"],
                sandbox=(_tradier_acct.environment != "live"),
            )
        _alpaca_acct = (await _sess.execute(
            select(Account).where(Account.broker_type == "alpaca").limit(1)
        )).scalar_one_or_none()
        if _alpaca_acct:
            _creds = _json.loads(encryption.decrypt(_alpaca_acct.credentials))
            alpaca_provider = AlpacaProvider(
                api_key=_creds["api_key"],
                secret_key=_creds["secret_key"],
            )
except Exception:
    logger.exception("Failed to initialize broker-based data providers")
```

Then when building the `providers` dict:

```python
providers = {}
if polygon_provider:
    providers["polygon"] = polygon_provider
if tradier_provider:
    providers["tradier"] = tradier_provider
if alpaca_provider:
    providers["alpaca"] = alpaca_provider
```

- [ ] **Step 3: Commit**

```bash
git add coordinator/main.py coordinator/config.py
git commit -m "feat(coord): register Tradier and Alpaca data providers in download manager"
```

---

## Task 5: Account Backfill Service — Position Ledger + Equity Materialization

This is the core logic. Replay broker transactions into the position ledger, then join against daily close prices to build the materialized equity curve.

**Files:**
- Create: `coordinator/services/account_backfill.py`
- Create: `tests/coordinator/services/test_account_backfill.py`

- [ ] **Step 1: Write failing tests for transaction replay**

```python
# tests/coordinator/services/test_account_backfill.py
import pytest
from datetime import date


def test_replay_transactions_builds_position_ledger():
    from coordinator.services.account_backfill import replay_transactions

    transactions = [
        {"type": "fill", "timestamp": "2025-01-02T10:00:00Z", "symbol": "AAPL", "side": "buy", "quantity": 10, "price": 150.0},
        {"type": "fill", "timestamp": "2025-01-03T10:00:00Z", "symbol": "GOOG", "side": "buy", "quantity": 5, "price": 100.0},
        {"type": "fill", "timestamp": "2025-01-05T10:00:00Z", "symbol": "AAPL", "side": "sell", "quantity": 3, "price": 155.0},
    ]
    ledger, cash_by_date = replay_transactions(transactions, starting_cash=10000.0)

    # After Jan 2: AAPL=10
    assert ledger[date(2025, 1, 2)]["AAPL"]["quantity"] == 10
    # After Jan 3: AAPL=10, GOOG=5
    assert ledger[date(2025, 1, 3)]["AAPL"]["quantity"] == 10
    assert ledger[date(2025, 1, 3)]["GOOG"]["quantity"] == 5
    # After Jan 5: AAPL=7, GOOG=5
    assert ledger[date(2025, 1, 5)]["AAPL"]["quantity"] == 7
    assert ledger[date(2025, 1, 5)]["GOOG"]["quantity"] == 5

    # Cash: 10000 - (10*150) - (5*100) + (3*155) = 10000 - 1500 - 500 + 465 = 8465
    assert cash_by_date[date(2025, 1, 5)] == pytest.approx(8465.0)


def test_replay_transactions_handles_cash_flows():
    from coordinator.services.account_backfill import replay_transactions

    transactions = [
        {"type": "deposit", "timestamp": "2025-01-02T10:00:00Z", "amount": 5000.0},
        {"type": "fill", "timestamp": "2025-01-03T10:00:00Z", "symbol": "AAPL", "side": "buy", "quantity": 10, "price": 150.0},
        {"type": "dividend", "timestamp": "2025-01-05T10:00:00Z", "amount": 25.0},
    ]
    ledger, cash_by_date = replay_transactions(transactions, starting_cash=0.0)

    assert cash_by_date[date(2025, 1, 2)] == pytest.approx(5000.0)
    assert cash_by_date[date(2025, 1, 3)] == pytest.approx(3500.0)  # 5000 - 1500
    assert cash_by_date[date(2025, 1, 5)] == pytest.approx(3525.0)  # 3500 + 25


def test_materialize_equity_curve():
    from coordinator.services.account_backfill import materialize_equity

    ledger = {
        date(2025, 1, 2): {"AAPL": {"quantity": 10, "avg_cost": 150.0}},
        date(2025, 1, 3): {"AAPL": {"quantity": 10, "avg_cost": 150.0}, "GOOG": {"quantity": 5, "avg_cost": 100.0}},
    }
    cash_by_date = {
        date(2025, 1, 2): 8500.0,
        date(2025, 1, 3): 8000.0,
    }
    prices = {
        ("AAPL", date(2025, 1, 2)): 152.0,
        ("AAPL", date(2025, 1, 3)): 155.0,
        ("GOOG", date(2025, 1, 3)): 102.0,
    }

    rows = materialize_equity(ledger, cash_by_date, prices)

    assert len(rows) == 2
    # Jan 2: 10*152 + 8500 = 10020
    assert rows[0]["total_value"] == pytest.approx(10020.0)
    assert rows[0]["positions_value"] == pytest.approx(1520.0)
    # Jan 3: 10*155 + 5*102 + 8000 = 1550 + 510 + 8000 = 10060
    assert rows[1]["total_value"] == pytest.approx(10060.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/coordinator/services/test_account_backfill.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement core functions**

```python
# coordinator/services/account_backfill.py
"""Account backfill — replay transactions into a position ledger and
materialize daily equity values from historical close prices.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _parse_date(ts: str) -> date:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()


def replay_transactions(
    transactions: list[dict],
    starting_cash: float = 0.0,
) -> tuple[dict[date, dict[str, dict]], dict[date, float]]:
    """Replay a chronological list of broker transactions into a daily position ledger.

    Returns:
        ledger: {date: {symbol: {"quantity": float, "avg_cost": float}}}
        cash_by_date: {date: float}
    """
    positions: dict[str, dict] = {}
    cash = starting_cash
    net_deposits = 0.0

    ledger: dict[date, dict[str, dict]] = {}
    cash_by_date: dict[date, float] = {}

    sorted_txns = sorted(transactions, key=lambda t: t.get("timestamp", ""))
    last_date: Optional[date] = None

    for txn in sorted_txns:
        txn_date = _parse_date(txn["timestamp"])
        txn_type = txn.get("type", "")

        if txn_type == "fill":
            symbol = txn["symbol"]
            side = txn.get("side", "")
            qty = float(txn.get("quantity", 0))
            price = float(txn.get("price", 0))

            if symbol not in positions:
                positions[symbol] = {"quantity": 0.0, "avg_cost": 0.0, "total_cost": 0.0}

            pos = positions[symbol]
            if side == "buy":
                pos["total_cost"] += qty * price
                pos["quantity"] += qty
                pos["avg_cost"] = pos["total_cost"] / pos["quantity"] if pos["quantity"] else 0
                cash -= qty * price
            elif side == "sell":
                pos["quantity"] -= qty
                if pos["quantity"] <= 0.001:
                    positions.pop(symbol, None)
                else:
                    pos["total_cost"] = pos["avg_cost"] * pos["quantity"]
                cash += qty * price

        elif txn_type in ("deposit", "dividend", "interest"):
            amount = float(txn.get("amount", 0))
            cash += amount
            if txn_type == "deposit":
                net_deposits += amount

        elif txn_type in ("withdrawal", "fee"):
            amount = abs(float(txn.get("amount", 0)))
            cash -= amount
            if txn_type == "withdrawal":
                net_deposits -= amount

        # Snapshot at each date boundary
        ledger[txn_date] = {
            sym: {"quantity": p["quantity"], "avg_cost": p["avg_cost"]}
            for sym, p in positions.items()
            if p["quantity"] > 0.001
        }
        cash_by_date[txn_date] = cash

    return ledger, cash_by_date


def forward_fill_ledger(
    ledger: dict[date, dict[str, dict]],
    cash_by_date: dict[date, float],
    start: date,
    end: date,
) -> tuple[dict[date, dict[str, dict]], dict[date, float]]:
    """Fill in dates between transactions so every trading day has a row."""
    from datetime import timedelta

    all_dates = sorted(set(ledger.keys()) | set(cash_by_date.keys()))
    if not all_dates:
        return ledger, cash_by_date

    filled_ledger: dict[date, dict[str, dict]] = {}
    filled_cash: dict[date, float] = {}
    last_positions: dict[str, dict] = {}
    last_cash: float = 0.0

    d = start
    while d <= end:
        if d.weekday() < 5:  # weekdays only
            if d in ledger:
                last_positions = ledger[d]
            if d in cash_by_date:
                last_cash = cash_by_date[d]
            filled_ledger[d] = dict(last_positions)
            filled_cash[d] = last_cash
        d += timedelta(days=1)

    return filled_ledger, filled_cash


def materialize_equity(
    ledger: dict[date, dict[str, dict]],
    cash_by_date: dict[date, float],
    prices: dict[tuple[str, date], float],
) -> list[dict]:
    """Join position ledger against daily close prices to produce equity rows.

    Args:
        prices: {(symbol, date): close_price}

    Returns:
        List of dicts with keys: date, total_value, positions_value, cash, estimated
    """
    rows = []
    last_known_prices: dict[str, float] = {}

    for d in sorted(set(ledger.keys()) | set(cash_by_date.keys())):
        positions = ledger.get(d, {})
        cash = cash_by_date.get(d, 0.0)
        estimated = False
        positions_value = 0.0

        for symbol, pos in positions.items():
            qty = pos["quantity"]
            price_key = (symbol, d)
            if price_key in prices:
                close = prices[price_key]
                last_known_prices[symbol] = close
            elif symbol in last_known_prices:
                close = last_known_prices[symbol]
                estimated = True
            else:
                close = pos["avg_cost"]
                estimated = True
            positions_value += qty * close

        rows.append({
            "date": d,
            "total_value": positions_value + cash,
            "positions_value": positions_value,
            "cash": cash,
            "estimated": estimated,
        })

    return rows


async def load_prices_for_symbols(
    symbols: set[str],
    start: date,
    end: date,
    data_service: Any,
    default_provider: str,
) -> dict[tuple[str, date], float]:
    """Load daily close prices from disk for a set of symbols.

    Searches all available providers on disk, preferring default_provider.
    """
    import os
    import pandas as pd

    prices: dict[tuple[str, date], float] = {}

    for symbol in symbols:
        df = data_service.load_market_data(default_provider, symbol, "1day")
        if df is None or df.empty:
            # Try other providers on disk
            for provider_dir in os.listdir(data_service._market_dir):
                if provider_dir == default_provider or provider_dir.endswith("_live"):
                    continue
                df = data_service.load_market_data(provider_dir, symbol, "1day")
                if df is not None and not df.empty:
                    break

        if df is None or df.empty:
            continue

        if "timestamp" in df.columns:
            df["_date"] = pd.to_datetime(df["timestamp"]).dt.date
        else:
            continue

        for _, row in df.iterrows():
            d = row["_date"]
            if start <= d <= end:
                prices[(symbol, d)] = float(row["close"])

    return prices
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/coordinator/services/test_account_backfill.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/account_backfill.py tests/coordinator/services/test_account_backfill.py
git commit -m "feat(coord): account backfill — transaction replay, ledger, equity materialization"
```

---

## Task 6: PortfolioTracker Service — Real-Time Mark-to-Market

**Files:**
- Create: `coordinator/services/portfolio_tracker.py`
- Create: `tests/coordinator/services/test_portfolio_tracker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/coordinator/services/test_portfolio_tracker.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_price_update_recomputes_account_value():
    from coordinator.services.portfolio_tracker import PortfolioTracker

    ws_manager = MagicMock()
    ws_manager.broadcast_to_target = AsyncMock()
    tracker = PortfolioTracker(ws_manager=ws_manager)

    tracker.set_account_state("acct-1", {
        "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}},
        "cash": 5000.0,
    })
    tracker.add_subscriber("acct-1")

    await tracker.on_price_update("AAPL", 155.0)

    # Should broadcast updated value: 10*155 + 5000 = 6550
    ws_manager.broadcast_to_target.assert_called()
    call_args = ws_manager.broadcast_to_target.call_args
    assert call_args[0][0] == "account:acct-1"
    msg = call_args[0][1]
    assert msg["total_value"] == pytest.approx(6550.0)
    assert msg["positions_value"] == pytest.approx(1550.0)


@pytest.mark.asyncio
async def test_no_broadcast_without_subscribers():
    from coordinator.services.portfolio_tracker import PortfolioTracker

    ws_manager = MagicMock()
    ws_manager.broadcast_to_target = AsyncMock()
    tracker = PortfolioTracker(ws_manager=ws_manager)

    tracker.set_account_state("acct-1", {
        "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}},
        "cash": 5000.0,
    })
    # No subscriber added

    await tracker.on_price_update("AAPL", 155.0)
    ws_manager.broadcast_to_target.assert_not_called()


@pytest.mark.asyncio
async def test_portfolio_summary_aggregates_visible_accounts():
    from coordinator.services.portfolio_tracker import PortfolioTracker

    ws_manager = MagicMock()
    ws_manager.broadcast_to_target = AsyncMock()
    tracker = PortfolioTracker(ws_manager=ws_manager)

    tracker.set_account_state("acct-1", {
        "positions": {"AAPL": {"quantity": 10, "current_price": 150.0}},
        "cash": 5000.0,
    })
    tracker.set_account_state("acct-2", {
        "positions": {"GOOG": {"quantity": 5, "current_price": 100.0}},
        "cash": 3000.0,
    })
    tracker.add_subscriber("portfolio:summary")
    tracker.mark_account_visible("acct-1")
    tracker.mark_account_visible("acct-2")

    await tracker.on_price_update("AAPL", 155.0)

    # Should broadcast portfolio summary
    calls = [c for c in ws_manager.broadcast_to_target.call_args_list
             if c[0][0] == "portfolio:summary"]
    assert len(calls) >= 1
    msg = calls[-1][0][1]
    # acct-1: 10*155 + 5000 = 6550, acct-2: 5*100 + 3000 = 3500
    assert msg["total_equity"] == pytest.approx(10050.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/coordinator/services/test_portfolio_tracker.py -v
```

- [ ] **Step 3: Implement PortfolioTracker**

```python
# coordinator/services/portfolio_tracker.py
"""Real-time mark-to-market portfolio tracker.

Maintains per-account position/cash state in memory. Recomputes account
value when a held symbol's price ticks. Pushes updates to dashboard
subscribers via WebSocket.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEBOUNCE_S = 1.0


class PortfolioTracker:
    def __init__(self, ws_manager: Any) -> None:
        self._ws = ws_manager
        self._accounts: dict[str, dict] = {}
        self._symbol_to_accounts: dict[str, set[str]] = {}
        self._subscribers: set[str] = set()
        self._visible_accounts: set[str] = set()
        self._last_push: dict[str, float] = {}
        self._prices: dict[str, float] = {}

    def set_account_state(self, account_id: str, state: dict) -> None:
        self._accounts[account_id] = {
            "positions": dict(state.get("positions", {})),
            "cash": float(state.get("cash", 0)),
        }
        self._symbol_to_accounts.clear()
        for acct_id, acct in self._accounts.items():
            for sym in acct["positions"]:
                self._symbol_to_accounts.setdefault(sym, set()).add(acct_id)
                if sym not in self._prices:
                    pos = acct["positions"][sym]
                    self._prices[sym] = float(pos.get("current_price", 0))

    def add_subscriber(self, topic: str) -> None:
        self._subscribers.add(topic)

    def remove_subscriber(self, topic: str) -> None:
        self._subscribers.discard(topic)

    def mark_account_visible(self, account_id: str) -> None:
        self._visible_accounts.add(account_id)

    def _compute_account_value(self, account_id: str) -> Optional[dict]:
        acct = self._accounts.get(account_id)
        if not acct:
            return None
        positions_value = 0.0
        for sym, pos in acct["positions"].items():
            qty = float(pos.get("quantity", 0))
            price = self._prices.get(sym, float(pos.get("current_price", 0)))
            positions_value += qty * price
        cash = acct["cash"]
        return {
            "type": "account_equity_update",
            "account_id": account_id,
            "total_value": positions_value + cash,
            "positions_value": positions_value,
            "cash": cash,
        }

    async def on_price_update(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price
        affected = self._symbol_to_accounts.get(symbol, set())
        if not affected:
            return

        now = time.monotonic()

        for acct_id in affected:
            topic = f"account:{acct_id}"
            if topic in self._subscribers:
                if now - self._last_push.get(topic, 0) >= _DEBOUNCE_S:
                    msg = self._compute_account_value(acct_id)
                    if msg:
                        await self._ws.broadcast_to_target(topic, msg)
                        self._last_push[topic] = now

        if "portfolio:summary" in self._subscribers:
            if now - self._last_push.get("portfolio:summary", 0) >= _DEBOUNCE_S:
                total_equity = 0.0
                for vis_id in self._visible_accounts:
                    val = self._compute_account_value(vis_id)
                    if val:
                        total_equity += val["total_value"]
                await self._ws.broadcast_to_target("portfolio:summary", {
                    "type": "portfolio_summary_update",
                    "total_equity": total_equity,
                })
                self._last_push["portfolio:summary"] = now
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/coordinator/services/test_portfolio_tracker.py -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/portfolio_tracker.py tests/coordinator/services/test_portfolio_tracker.py
git commit -m "feat(coord): PortfolioTracker — real-time mark-to-market from live ticks"
```

---

## Task 7: Wire PortfolioTracker + WebSocket Topics

Connect the PortfolioTracker to the LiveFeedAggregator's bar callbacks and handle the new subscription topics in the WebSocket handler.

**Files:**
- Modify: `coordinator/main.py` (wire PortfolioTracker into lifespan)
- Modify: `coordinator/api/websocket.py` (handle new subscription topics)

- [ ] **Step 1: Wire PortfolioTracker in coordinator startup**

In `coordinator/main.py`, after the LiveFeedAggregator is created, instantiate and store the PortfolioTracker:

```python
from coordinator.services.portfolio_tracker import PortfolioTracker
portfolio_tracker = PortfolioTracker(ws_manager=ws_manager)
container.portfolio_tracker = portfolio_tracker
```

- [ ] **Step 2: Hook into LiveFeedAggregator bar callbacks**

In `coordinator/api/websocket.py`, when a dashboard client subscribes to `account:{id}`, load the account's current positions and register a bar callback for each held symbol. When unsubscribing, remove the callbacks.

In the subscribe handling section (around line 82), add:

```python
if target.startswith("account:") or target == "portfolio:summary":
    container = get_container()
    tracker = getattr(container, "portfolio_tracker", None)
    if tracker:
        tracker.add_subscriber(target)
        if target.startswith("account:"):
            acct_id = target.split(":", 1)[1]
            # Load current positions and initialize tracker state
            asyncio.create_task(_init_account_tracking(acct_id, tracker))
```

Add the corresponding unsubscribe logic that calls `tracker.remove_subscriber(target)`.

Add a helper function:

```python
async def _init_account_tracking(account_id: str, tracker) -> None:
    """Load account positions from broker and register bar callbacks."""
    from coordinator.database.models import Account
    from sqlalchemy import select
    container = get_container()
    async with container.session_factory() as session:
        account = (await session.execute(
            select(Account).where(Account.id == account_id)
        )).scalar_one_or_none()
        if not account:
            return
    try:
        import json as _json
        creds = _json.loads(container.encryption.decrypt(account.credentials))
        from worker.adapter_factory import make_broker_adapter
        adapter = make_broker_adapter(account.broker_type, account.environment, creds)
        positions = adapter.get_positions()
        info = adapter.get_account_info()
        tracker.set_account_state(account_id, {
            "positions": positions,
            "cash": info.get("cash", 0),
        })
        if account.show_in_overview:
            tracker.mark_account_visible(account_id)

        # Register bar callbacks for held symbols
        aggregator = getattr(container, "live_feed_aggregator", None)
        if aggregator:
            for symbol in positions:
                async def _on_bar(bar, sym=symbol):
                    await tracker.on_price_update(sym, float(bar.get("close", 0)))
                aggregator.subscribe_bars(account.broker_type, symbol, "1min", _on_bar)
    except Exception:
        logger.exception("Failed to init tracking for account %s", account_id)
```

- [ ] **Step 3: Commit**

```bash
git add coordinator/main.py coordinator/api/websocket.py
git commit -m "feat(coord): wire PortfolioTracker to WebSocket subscriptions and live bars"
```

---

## Task 8: Background Jobs — Periodic Sync, Daily Close, Account Setup

**Files:**
- Create: `coordinator/services/account_lifecycle.py`
- Modify: `coordinator/main.py` (register scheduler jobs)

- [ ] **Step 1: Implement account lifecycle service**

```python
# coordinator/services/account_lifecycle.py
"""Automatic account lifecycle: periodic sync, daily close, initial backfill."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from coordinator.services.account_backfill import (
    forward_fill_ledger,
    load_prices_for_symbols,
    materialize_equity,
    replay_transactions,
)

logger = logging.getLogger(__name__)


class AccountLifecycleService:
    def __init__(
        self,
        session_factory: Any,
        encryption: Any,
        data_service: Any,
        download_manager: Any,
        ws_manager: Any,
        default_provider: str = "tradier",
    ) -> None:
        self._session_factory = session_factory
        self._encryption = encryption
        self._data_service = data_service
        self._download_manager = download_manager
        self._ws = ws_manager
        self._default_provider = default_provider

    async def initial_backfill(self, account_id: str) -> None:
        """Full backfill for a newly added account."""
        import json as _json
        from sqlalchemy import select
        from coordinator.database.models import (
            Account, AccountEquityDaily, AccountPositionLedger,
        )
        from worker.adapter_factory import make_broker_adapter

        async with self._session_factory() as session:
            account = (await session.execute(
                select(Account).where(Account.id == account_id)
            )).scalar_one_or_none()
            if not account:
                return

        creds = _json.loads(self._encryption.decrypt(account.credentials))
        adapter = make_broker_adapter(account.broker_type, account.environment, creds)

        # 1. Pull full transaction history
        await self._push_progress(account_id, "Pulling transaction history...")
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        txns = await asyncio.to_thread(adapter.get_transactions, since)

        normalized = []
        for txn in txns:
            normalized.append({
                "type": txn.type,
                "timestamp": txn.timestamp.isoformat(),
                "symbol": txn.symbol,
                "side": txn.side,
                "quantity": txn.quantity,
                "price": txn.price,
                "amount": txn.amount,
            })

        # 2. Replay into position ledger
        await self._push_progress(account_id, "Building position ledger...")
        info = await asyncio.to_thread(adapter.get_account_info)
        ledger, cash_by_date = replay_transactions(normalized, starting_cash=0.0)

        if not ledger:
            await self._push_progress(account_id, "No transaction history found.")
            return

        all_dates = sorted(ledger.keys())
        start_date, end_date = all_dates[0], date.today() - timedelta(days=1)
        filled_ledger, filled_cash = forward_fill_ledger(ledger, cash_by_date, start_date, end_date)

        # 3. Collect all historically held symbols
        all_symbols: set[str] = set()
        for positions in filled_ledger.values():
            all_symbols.update(positions.keys())

        # 4. Download missing price data
        await self._push_progress(account_id, f"Downloading price data for {len(all_symbols)} symbols...")
        if all_symbols and self._download_manager:
            try:
                await self._download_manager.create_download(
                    symbols=sorted(all_symbols),
                    date_range_start=start_date,
                    date_range_end=end_date,
                    provider=self._default_provider,
                    timeframe="1day",
                )
            except Exception:
                logger.warning("Download request failed; proceeding with available data")

        # 5. Load prices and materialize
        await self._push_progress(account_id, "Materializing equity curve...")
        prices = await load_prices_for_symbols(
            all_symbols, start_date, end_date, self._data_service, self._default_provider,
        )
        equity_rows = materialize_equity(filled_ledger, filled_cash, prices)

        # 6. Write to database
        async with self._session_factory() as session:
            # Clear existing data for this account
            from sqlalchemy import delete
            await session.execute(
                delete(AccountPositionLedger).where(AccountPositionLedger.account_id == account_id)
            )
            await session.execute(
                delete(AccountEquityDaily).where(AccountEquityDaily.account_id == account_id)
            )

            for d, positions in filled_ledger.items():
                for sym, pos in positions.items():
                    session.add(AccountPositionLedger(
                        account_id=account_id, date=d,
                        symbol=sym, quantity=pos["quantity"], avg_cost=pos["avg_cost"],
                    ))

            for row in equity_rows:
                session.add(AccountEquityDaily(
                    account_id=account_id, date=row["date"],
                    total_value=row["total_value"], positions_value=row["positions_value"],
                    cash=row["cash"], estimated=row["estimated"],
                ))

            await session.commit()

        await self._push_progress(account_id, "Backfill complete.")

    async def periodic_sync(self) -> None:
        """Sync all accounts — pull new transactions since last sync."""
        from sqlalchemy import select, func
        from coordinator.database.models import Account, TradeLog, AccountCashFlow
        from worker.adapter_factory import make_broker_adapter
        import json as _json

        async with self._session_factory() as session:
            accounts = (await session.execute(select(Account))).scalars().all()

        for account in accounts:
            try:
                creds = _json.loads(self._encryption.decrypt(account.credentials))
                adapter = make_broker_adapter(account.broker_type, account.environment, creds)

                async with self._session_factory() as session:
                    latest_trade = (await session.execute(
                        select(func.max(TradeLog.timestamp)).where(TradeLog.account_id == account.id)
                    )).scalar()
                    latest_flow = (await session.execute(
                        select(func.max(AccountCashFlow.timestamp)).where(AccountCashFlow.account_id == account.id)
                    )).scalar()

                since = max(filter(None, [latest_trade, latest_flow]), default=None)
                if since is None:
                    since = datetime.now(timezone.utc) - timedelta(days=30)

                txns = await asyncio.to_thread(adapter.get_transactions, since)
                if txns:
                    logger.info("Synced %d transactions for %s", len(txns), account.name)

            except Exception:
                logger.exception("Periodic sync failed for account %s", account.name)

    async def daily_close(self) -> None:
        """Append today's closing row to account_equity_daily for all accounts."""
        from sqlalchemy import select
        from coordinator.database.models import Account, AccountEquityDaily, AccountPositionLedger
        from worker.adapter_factory import make_broker_adapter
        import json as _json

        today = date.today()

        async with self._session_factory() as session:
            accounts = (await session.execute(select(Account))).scalars().all()

        for account in accounts:
            try:
                creds = _json.loads(self._encryption.decrypt(account.credentials))
                adapter = make_broker_adapter(account.broker_type, account.environment, creds)
                info = await asyncio.to_thread(adapter.get_account_info)
                positions = await asyncio.to_thread(adapter.get_positions)

                positions_value = sum(
                    float(p.get("market_value", 0)) for p in positions.values()
                )
                cash = float(info.get("cash", 0))

                async with self._session_factory() as session:
                    existing = (await session.execute(
                        select(AccountEquityDaily).where(
                            AccountEquityDaily.account_id == account.id,
                            AccountEquityDaily.date == today,
                        )
                    )).scalar_one_or_none()

                    if existing:
                        existing.total_value = positions_value + cash
                        existing.positions_value = positions_value
                        existing.cash = cash
                        existing.estimated = False
                    else:
                        session.add(AccountEquityDaily(
                            account_id=account.id, date=today,
                            total_value=positions_value + cash,
                            positions_value=positions_value, cash=cash,
                            estimated=False,
                        ))
                    await session.commit()

            except Exception:
                logger.exception("Daily close failed for account %s", account.name)

    async def _push_progress(self, account_id: str, message: str) -> None:
        try:
            await self._ws.broadcast_to_target(
                f"account:{account_id}:setup_progress",
                {"type": "setup_progress", "account_id": account_id, "message": message},
            )
        except Exception:
            pass
```

- [ ] **Step 2: Register background jobs in coordinator startup**

In `coordinator/main.py`, after creating the scheduler service, register the periodic sync and daily close jobs:

```python
from coordinator.services.account_lifecycle import AccountLifecycleService
lifecycle = AccountLifecycleService(
    session_factory=session_factory,
    encryption=encryption,
    data_service=data_svc,
    download_manager=download_manager,
    ws_manager=ws_manager,
    default_provider=config.default_history_provider,
)
container.account_lifecycle = lifecycle

# Periodic sync every 15 minutes during market hours
scheduler_svc.add_cron_job(
    job_id="account_periodic_sync",
    func=lambda: asyncio.create_task(lifecycle.periodic_sync()),
    cron_expr="*/15 * * * 1-5",
)

# Daily close at 4:35 PM ET (20:35 UTC) on weekdays
scheduler_svc.add_cron_job(
    job_id="account_daily_close",
    func=lambda: asyncio.create_task(lifecycle.daily_close()),
    cron_expr="35 20 * * 1-5",
)
```

- [ ] **Step 3: Commit**

```bash
git add coordinator/services/account_lifecycle.py coordinator/main.py
git commit -m "feat(coord): account lifecycle — periodic sync, daily close, initial backfill"
```

---

## Task 9: Update Equity Curve API to Read Materialized Table

**Delegate to: Sonnet**

Switch `GET /api/accounts/{id}/equity-curve` from the cash-flow-interpolation approach to reading from `account_equity_daily`.

**Files:**
- Modify: `coordinator/api/routes/accounts.py` (equity-curve endpoint)

- [ ] **Step 1: Replace the equity curve endpoint logic**

Find the `equity_curve` endpoint in `coordinator/api/routes/accounts.py`. Replace its implementation to query `AccountEquityDaily`:

```python
@router.get("/{account_id}/equity-curve")
async def equity_curve(
    account_id: str,
    since: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    from coordinator.database.models import AccountEquityDaily
    stmt = select(AccountEquityDaily).where(
        AccountEquityDaily.account_id == account_id,
    ).order_by(AccountEquityDaily.date)

    if since:
        from datetime import datetime as dt
        since_dt = dt.fromisoformat(since.replace("Z", "+00:00"))
        stmt = stmt.where(AccountEquityDaily.date >= since_dt.date())

    rows = (await db.execute(stmt)).scalars().all()

    return {
        "items": [
            {
                "timestamp": r.date.isoformat() + "T00:00:00Z",
                "value": r.total_value,
                "positions_value": r.positions_value,
                "cash": r.cash,
                "estimated": r.estimated,
                "source": "materialized",
            }
            for r in rows
        ]
    }
```

Keep the old endpoint logic as a fallback when `AccountEquityDaily` has no rows (account hasn't been backfilled yet).

- [ ] **Step 2: Commit**

```bash
git add coordinator/api/routes/accounts.py
git commit -m "feat(api): equity-curve endpoint reads from materialized account_equity_daily table"
```

---

## Task 10: Dashboard — WebSocket Equity Push + Live Chart Extension

**Delegate to: Sonnet**

Update the dashboard to subscribe to account/portfolio topics and append live equity points to charts.

**Files:**
- Modify: `dashboard/src/api/hooks.ts`
- Modify: `dashboard/src/api/websocket.ts`
- Modify: `dashboard/src/components/widgets/KpiStripWidget.tsx`
- Modify: `dashboard/src/pages/AccountDetail.tsx`

- [ ] **Step 1: Add WebSocket subscription hooks**

In `dashboard/src/api/hooks.ts`, add a hook that subscribes to a topic on mount and returns live messages:

```typescript
import { useEffect, useState, useCallback } from "react";

export function useWebSocketTopic<T = unknown>(topic: string | null): T | null {
  const [latest, setLatest] = useState<T | null>(null);

  useEffect(() => {
    if (!topic) return;
    const ws = getWebSocketManager(); // import from websocket.ts
    ws.send({ type: "subscribe", target: topic });

    const unsub = ws.subscribe(
      topic.startsWith("account:") ? "account_equity_update" :
      topic === "portfolio:summary" ? "portfolio_summary_update" :
      "*",
      (data: unknown) => setLatest(data as T),
    );

    return () => {
      unsub();
      ws.send({ type: "unsubscribe", target: topic });
    };
  }, [topic]);

  return latest;
}
```

- [ ] **Step 2: Update KpiStripWidget to use WebSocket push**

In `KpiStripWidget.tsx`, consume `portfolio:summary` WebSocket topic. When a `portfolio_summary_update` message arrives, update the total equity display immediately instead of waiting for the next poll.

```typescript
const liveKpis = useWebSocketTopic<{total_equity: number}>("portfolio:summary");

// Merge liveKpis.total_equity into the existing KPI display
// Fall back to polled data when WebSocket hasn't pushed yet
const displayEquity = liveKpis?.total_equity ?? kpisData?.total_equity;
```

- [ ] **Step 3: Update AccountDetail equity curve with live extension**

In `AccountDetail.tsx`, subscribe to `account:{id}` and append each update as a new point on the equity chart:

```typescript
const liveEquity = useWebSocketTopic<{total_value: number; account_id: string}>(
  `account:${accountId}`
);

// Append to chart data when liveEquity updates
useEffect(() => {
  if (liveEquity && chartRef.current) {
    chartRef.current.update({
      time: Date.now() / 1000,
      value: liveEquity.total_value,
    });
  }
}, [liveEquity]);
```

- [ ] **Step 4: Switch KPI hooks from polling to WebSocket-invalidated**

In `hooks.ts`, modify `usePortfolioKpis` to remove `refetchInterval` and instead invalidate the query when a WebSocket message arrives:

```typescript
export function usePortfolioKpis() {
  return useQuery({
    queryKey: ["portfolio", "kpis"] as const,
    queryFn: api.portfolioKpis,
    staleTime: 15_000,
    // Remove refetchInterval — WebSocket invalidation handles updates
  });
}
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/
git commit -m "feat(dashboard): WebSocket equity push + live chart extension"
```

---

## Task 11: Demote Sync Button + Add Setup Progress

**Delegate to: Sonnet**

Move the sync button to an actions menu and add a progress indicator for initial account backfill.

**Files:**
- Modify: `dashboard/src/pages/AccountDetail.tsx`

- [ ] **Step 1: Move sync button to actions dropdown**

Replace the prominent sync button with an actions dropdown menu (using an existing dropdown component or a simple disclosure). Include "Force Sync" as a menu item.

- [ ] **Step 2: Add setup progress indicator**

Subscribe to `account:{id}:setup_progress` via WebSocket. When messages arrive, show a progress banner at the top of the account detail page:

```typescript
const setupProgress = useWebSocketTopic<{message: string}>(
  `account:${accountId}:setup_progress`
);

// In the JSX:
{setupProgress && (
  <div className="bg-blue-50 border-l-4 border-blue-400 p-4 mb-4">
    <p className="text-sm text-blue-700">{setupProgress.message}</p>
  </div>
)}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/pages/AccountDetail.tsx
git commit -m "feat(dashboard): demote sync button, add backfill progress indicator"
```

---

## Task 12: Integration — Trigger Backfill on Account Add

**Files:**
- Modify: `coordinator/api/routes/accounts.py` (the create account endpoint)

- [ ] **Step 1: Trigger initial backfill after account creation**

In the account creation endpoint, after the account is successfully created and validated, kick off the backfill as a background task:

```python
# At the end of the create account handler, after db.commit():
container = get_container()
lifecycle = getattr(container, "account_lifecycle", None)
if lifecycle:
    asyncio.create_task(lifecycle.initial_backfill(account.id))
```

- [ ] **Step 2: Manually trigger backfill for existing accounts**

Add a `POST /api/accounts/{id}/backfill` endpoint:

```python
@router.post("/{account_id}/backfill")
async def trigger_backfill(account_id: str, db: AsyncSession = Depends(get_db)):
    account = (await db.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    container = get_container()
    lifecycle = getattr(container, "account_lifecycle", None)
    if not lifecycle:
        raise HTTPException(status_code=503, detail="Lifecycle service not available")
    asyncio.create_task(lifecycle.initial_backfill(account_id))
    return {"ok": True, "message": "Backfill started"}
```

- [ ] **Step 3: Commit**

```bash
git add coordinator/api/routes/accounts.py
git commit -m "feat(api): trigger initial backfill on account creation + manual backfill endpoint"
```

---

## Summary

| Task | Description | Delegate |
|------|-------------|----------|
| 1 | Database migrations — new tables | Sonnet |
| 2 | Tradier historical data provider | Sonnet |
| 3 | Alpaca historical data provider | Sonnet |
| 4 | Register providers in download manager | Sonnet |
| 5 | Account backfill — position ledger + equity materialization | Opus (core logic) |
| 6 | PortfolioTracker — real-time mark-to-market | Opus (core service) |
| 7 | Wire PortfolioTracker + WebSocket topics | Opus (integration) |
| 8 | Background jobs — periodic sync, daily close | Opus (lifecycle) |
| 9 | Equity curve API reads materialized table | Sonnet |
| 10 | Dashboard — WebSocket equity push + live charts | Sonnet |
| 11 | Dashboard — demote sync button + setup progress | Sonnet |
| 12 | Integration — trigger backfill on account add | Sonnet |
