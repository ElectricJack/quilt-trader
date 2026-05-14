# Plan 8: Advanced Features (PDT, Backtest Comparison, Discord Bot)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three advanced coordinator features: the Pattern Day Trading monitor that tracks and enforces PDT rules during signal approval, the nightly backtest comparison engine that detects live/backtest divergence, and the Discord bot for event notifications and remote command management.

**Architecture:** The PDT monitor is a stateless service that queries the pdt_tracking table to count day trades in a rolling 5-business-day window, automatically excludes crypto trades, and returns approve/warn/block decisions based on the account's pdt_mode. The backtest comparison engine replays recent decision logs through the algorithm in backtest mode and compares signals tick-by-tick. The Discord bot runs as part of the coordinator process using discord.py, subscribes to the event bus, and formats/routes events to configured channels.

**Tech Stack:** Python 3.11+, discord.py, SQLAlchemy, pandas, pytest

---

## File Map

| File | Responsibility |
|------|---------------|
| `coordinator/services/pdt_monitor.py` | PDT tracking, day trade counting, signal approval |
| `coordinator/services/backtest_engine.py` | Nightly backtest comparison engine |
| `coordinator/services/discord_bot.py` | Discord bot — event notifications + commands |
| `coordinator/api/routes/backtests.py` | Backtest comparison result API endpoints |
| `tests/coordinator/test_pdt_monitor.py` | PDT monitor tests |
| `tests/coordinator/test_backtest_engine.py` | Backtest engine tests |
| `tests/coordinator/test_discord_bot.py` | Discord bot tests (mocked discord.py) |
| `tests/coordinator/test_backtests_api.py` | Backtest API tests |

---

### Task 1: PDT Monitor

**Files:**
- Create: `coordinator/services/pdt_monitor.py`
- Create: `tests/coordinator/test_pdt_monitor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_pdt_monitor.py
import pytest
from datetime import date, datetime, timezone, timedelta

from coordinator.services.pdt_monitor import PDTMonitor, PDTResult


def test_check_signal_no_day_trades():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[],
        signal_legs=[
            {"symbol": "AAPL", "asset_type": "equities", "side": "sell"},
        ],
        open_positions={"AAPL": {"opened_today": True}},
        today=date(2025, 3, 15),
    )
    assert result.approved is True
    assert result.day_trade_count == 1
    assert result.warning is False


def test_check_signal_crypto_exempt():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[
            {"day_trade_date": date(2025, 3, 13)},
            {"day_trade_date": date(2025, 3, 14)},
            {"day_trade_date": date(2025, 3, 15)},
        ],
        signal_legs=[
            {"symbol": "BTC/USD", "asset_type": "crypto", "side": "sell"},
        ],
        open_positions={"BTC/USD": {"opened_today": True}},
        today=date(2025, 3, 15),
    )
    assert result.approved is True
    assert result.would_be_day_trade is False


def test_check_signal_warning_at_3():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="warn",
        existing_day_trades=[
            {"day_trade_date": date(2025, 3, 12)},
            {"day_trade_date": date(2025, 3, 13)},
        ],
        signal_legs=[
            {"symbol": "TSLA", "asset_type": "equities", "side": "sell"},
        ],
        open_positions={"TSLA": {"opened_today": True}},
        today=date(2025, 3, 15),
    )
    assert result.approved is True
    assert result.warning is True
    assert result.day_trade_count == 3


def test_check_signal_blocked_at_4():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[
            {"day_trade_date": date(2025, 3, 12)},
            {"day_trade_date": date(2025, 3, 13)},
            {"day_trade_date": date(2025, 3, 14)},
        ],
        signal_legs=[
            {"symbol": "NVDA", "asset_type": "equities", "side": "sell"},
        ],
        open_positions={"NVDA": {"opened_today": True}},
        today=date(2025, 3, 15),
    )
    assert result.approved is False
    assert result.reason == "PDT limit reached"
    assert result.day_trade_count == 4


def test_check_signal_warn_mode_does_not_block():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="warn",
        existing_day_trades=[
            {"day_trade_date": date(2025, 3, 12)},
            {"day_trade_date": date(2025, 3, 13)},
            {"day_trade_date": date(2025, 3, 14)},
        ],
        signal_legs=[
            {"symbol": "NVDA", "asset_type": "equities", "side": "sell"},
        ],
        open_positions={"NVDA": {"opened_today": True}},
        today=date(2025, 3, 15),
    )
    assert result.approved is True
    assert result.warning is True


def test_check_signal_off_mode():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="off",
        existing_day_trades=[
            {"day_trade_date": date(2025, 3, 12)},
            {"day_trade_date": date(2025, 3, 13)},
            {"day_trade_date": date(2025, 3, 14)},
        ],
        signal_legs=[
            {"symbol": "NVDA", "asset_type": "equities", "side": "sell"},
        ],
        open_positions={"NVDA": {"opened_today": True}},
        today=date(2025, 3, 15),
    )
    assert result.approved is True
    assert result.warning is False


def test_not_a_day_trade_if_not_closing_same_day():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[
            {"day_trade_date": date(2025, 3, 12)},
            {"day_trade_date": date(2025, 3, 13)},
            {"day_trade_date": date(2025, 3, 14)},
        ],
        signal_legs=[
            {"symbol": "AAPL", "asset_type": "equities", "side": "buy"},
        ],
        open_positions={},
        today=date(2025, 3, 15),
    )
    assert result.approved is True
    assert result.would_be_day_trade is False


def test_multi_leg_with_mixed_crypto_equity():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[
            {"day_trade_date": date(2025, 3, 12)},
            {"day_trade_date": date(2025, 3, 13)},
            {"day_trade_date": date(2025, 3, 14)},
        ],
        signal_legs=[
            {"symbol": "BTC/USD", "asset_type": "crypto", "side": "sell"},
            {"symbol": "AAPL", "asset_type": "equities", "side": "sell"},
        ],
        open_positions={
            "BTC/USD": {"opened_today": True},
            "AAPL": {"opened_today": True},
        },
        today=date(2025, 3, 15),
    )
    assert result.approved is False


def test_rolling_window_excludes_old_trades():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[
            {"day_trade_date": date(2025, 3, 7)},
            {"day_trade_date": date(2025, 3, 8)},
            {"day_trade_date": date(2025, 3, 9)},
        ],
        signal_legs=[
            {"symbol": "AAPL", "asset_type": "equities", "side": "sell"},
        ],
        open_positions={"AAPL": {"opened_today": True}},
        today=date(2025, 3, 15),
    )
    assert result.approved is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_pdt_monitor.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/pdt_monitor.py
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass
class PDTResult:
    approved: bool
    would_be_day_trade: bool
    day_trade_count: int
    warning: bool = False
    reason: Optional[str] = None


class PDTMonitor:
    ROLLING_WINDOW_DAYS = 5

    def check_signal(
        self,
        pdt_mode: str,
        existing_day_trades: list[dict],
        signal_legs: list[dict],
        open_positions: dict,
        today: date,
    ) -> PDTResult:
        if pdt_mode == "off":
            return PDTResult(approved=True, would_be_day_trade=False, day_trade_count=0)

        would_be_day_trade = False
        for leg in signal_legs:
            if leg["asset_type"] == "crypto":
                continue
            symbol = leg["symbol"]
            side = leg["side"]
            if side in ("sell", "sell_short", "buy_to_cover"):
                pos = open_positions.get(symbol)
                if pos and pos.get("opened_today"):
                    would_be_day_trade = True
                    break

        window_start = today - timedelta(days=self.ROLLING_WINDOW_DAYS)
        recent_trades = [
            dt for dt in existing_day_trades
            if dt["day_trade_date"] > window_start
        ]
        count = len(recent_trades)

        if would_be_day_trade:
            count += 1

        if pdt_mode == "block" and would_be_day_trade and count >= 4:
            return PDTResult(
                approved=False,
                would_be_day_trade=True,
                day_trade_count=count,
                warning=True,
                reason="PDT limit reached",
            )

        warning = would_be_day_trade and count >= 3

        return PDTResult(
            approved=True,
            would_be_day_trade=would_be_day_trade,
            day_trade_count=count,
            warning=warning,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_pdt_monitor.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/pdt_monitor.py tests/coordinator/test_pdt_monitor.py
git commit -m "feat(coordinator): add PDT monitor with rolling window tracking"
```

---

### Task 2: Backtest Comparison Engine

**Files:**
- Create: `coordinator/services/backtest_engine.py`
- Create: `tests/coordinator/test_backtest_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_backtest_engine.py
import pytest
from coordinator.services.backtest_engine import BacktestComparator, ComparisonResult


def test_compare_matching_signals():
    live_decisions = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
        {"timestamp": "2025-01-01T09:32:00", "signals_produced": [{"legs": [{"symbol": "TSLA", "signal_type": "sell"}]}]},
    ]
    backtest_decisions = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
        {"timestamp": "2025-01-01T09:32:00", "signals_produced": [{"legs": [{"symbol": "TSLA", "signal_type": "sell"}]}]},
    ]
    result = BacktestComparator.compare(live_decisions, backtest_decisions)
    assert isinstance(result, ComparisonResult)
    assert result.total_ticks == 3
    assert result.matching_ticks == 3
    assert result.match_percentage == 100.0
    assert result.divergences == []


def test_compare_with_divergence():
    live_decisions = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
    ]
    backtest_decisions = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "sell"}]}]},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
    ]
    result = BacktestComparator.compare(live_decisions, backtest_decisions)
    assert result.total_ticks == 2
    assert result.matching_ticks == 1
    assert result.match_percentage == 50.0
    assert len(result.divergences) == 1
    assert result.divergences[0]["timestamp"] == "2025-01-01T09:30:00"


def test_compare_signal_present_vs_absent():
    live_decisions = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]},
    ]
    backtest_decisions = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
    ]
    result = BacktestComparator.compare(live_decisions, backtest_decisions)
    assert result.matching_ticks == 0
    assert len(result.divergences) == 1


def test_compare_empty():
    result = BacktestComparator.compare([], [])
    assert result.total_ticks == 0
    assert result.match_percentage == 100.0


def test_compare_different_lengths():
    live = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
    ]
    backtest = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
    ]
    result = BacktestComparator.compare(live, backtest)
    assert result.total_ticks == 2
    assert result.matching_ticks == 1
    assert len(result.divergences) == 1


def test_exceeds_threshold():
    live = [
        {"timestamp": f"2025-01-01T09:{i:02d}:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "buy"}]}]}
        for i in range(10)
    ]
    backtest = [
        {"timestamp": f"2025-01-01T09:{i:02d}:00", "signals_produced": [{"legs": [{"symbol": "AAPL", "signal_type": "sell"}]}]}
        for i in range(10)
    ]
    result = BacktestComparator.compare(live, backtest, threshold=5.0)
    assert result.exceeds_threshold is True


def test_below_threshold():
    live = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
    ]
    backtest = [
        {"timestamp": "2025-01-01T09:30:00", "signals_produced": []},
        {"timestamp": "2025-01-01T09:31:00", "signals_produced": []},
    ]
    result = BacktestComparator.compare(live, backtest, threshold=5.0)
    assert result.exceeds_threshold is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_backtest_engine.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/backtest_engine.py
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ComparisonResult:
    total_ticks: int
    matching_ticks: int
    match_percentage: float
    divergences: list[dict] = field(default_factory=list)
    exceeds_threshold: bool = False


class BacktestComparator:
    @staticmethod
    def compare(
        live_decisions: list[dict],
        backtest_decisions: list[dict],
        threshold: float = 5.0,
    ) -> ComparisonResult:
        if not live_decisions and not backtest_decisions:
            return ComparisonResult(
                total_ticks=0, matching_ticks=0, match_percentage=100.0
            )

        bt_by_ts = {d["timestamp"]: d for d in backtest_decisions}
        total = len(live_decisions)
        matching = 0
        divergences = []

        for live in live_decisions:
            ts = live["timestamp"]
            bt = bt_by_ts.get(ts)

            if bt is None:
                divergences.append({
                    "timestamp": ts,
                    "live_signals": live.get("signals_produced", []),
                    "backtest_signals": None,
                    "reason": "No backtest tick for this timestamp",
                })
                continue

            live_sigs = live.get("signals_produced", [])
            bt_sigs = bt.get("signals_produced", [])

            if BacktestComparator._signals_match(live_sigs, bt_sigs):
                matching += 1
            else:
                divergences.append({
                    "timestamp": ts,
                    "live_signals": live_sigs,
                    "backtest_signals": bt_sigs,
                    "reason": "Signal mismatch",
                })

        pct = (matching / total * 100) if total > 0 else 100.0
        divergence_pct = 100 - pct

        return ComparisonResult(
            total_ticks=total,
            matching_ticks=matching,
            match_percentage=round(pct, 2),
            divergences=divergences,
            exceeds_threshold=divergence_pct > threshold,
        )

    @staticmethod
    def _signals_match(live: list, backtest: list) -> bool:
        if len(live) != len(backtest):
            return False
        if not live and not backtest:
            return True

        live_key = BacktestComparator._signal_key(live)
        bt_key = BacktestComparator._signal_key(backtest)
        return live_key == bt_key

    @staticmethod
    def _signal_key(signals: list) -> frozenset:
        keys = set()
        for sig in signals:
            legs = sig.get("legs", [])
            for leg in legs:
                keys.add((leg.get("symbol"), leg.get("signal_type")))
        return frozenset(keys)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_backtest_engine.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_engine.py tests/coordinator/test_backtest_engine.py
git commit -m "feat(coordinator): add backtest comparison engine for divergence detection"
```

---

### Task 3: Discord Bot

**Files:**
- Create: `coordinator/services/discord_bot.py`
- Create: `tests/coordinator/test_discord_bot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_discord_bot.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from coordinator.services.discord_bot import DiscordNotifier, format_trade_event, format_algo_event, format_pdt_event


def test_format_trade_event():
    payload = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 100,
        "filled_price": 150.50,
        "fees": 1.00,
        "pnl": 500.0,
    }
    msg = format_trade_event(payload)
    assert "AAPL" in msg
    assert "buy" in msg.lower()
    assert "150.50" in msg


def test_format_algo_event():
    payload = {
        "algorithm_name": "momentum-scalper",
        "account_name": "Alpaca Main",
        "old_status": "running",
        "new_status": "stopped",
    }
    msg = format_algo_event(payload)
    assert "momentum-scalper" in msg
    assert "stopped" in msg


def test_format_pdt_event():
    payload = {
        "account_name": "Alpaca Main",
        "day_trade_count": 3,
        "remaining": 1,
    }
    msg = format_pdt_event(payload)
    assert "3" in msg
    assert "Alpaca Main" in msg


def test_notifier_channel_routing():
    notifier = DiscordNotifier()
    notifier.set_route("trade_executed", "trades-channel")
    notifier.set_route("algo_error", "alerts-channel")
    assert notifier.get_channel("trade_executed") == "trades-channel"
    assert notifier.get_channel("algo_error") == "alerts-channel"
    assert notifier.get_channel("unknown") is None


def test_notifier_severity_filter():
    notifier = DiscordNotifier()
    notifier.set_route("algo_started", "status-channel", min_severity="warning")
    assert notifier.should_send("algo_started", "info") is False
    assert notifier.should_send("algo_started", "warning") is True
    assert notifier.should_send("algo_started", "error") is True


def test_notifier_disable_event():
    notifier = DiscordNotifier()
    notifier.set_route("trade_executed", "trades-channel")
    notifier.disable_route("trade_executed")
    assert notifier.get_channel("trade_executed") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_discord_bot.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/discord_bot.py
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def format_trade_event(payload: dict) -> str:
    symbol = payload.get("symbol", "?")
    side = payload.get("side", "?")
    qty = payload.get("quantity", 0)
    price = payload.get("filled_price", 0)
    fees = payload.get("fees", 0)
    pnl = payload.get("pnl")
    msg = f"**Trade Executed** | {side.upper()} {qty} {symbol} @ ${price:.2f} | Fees: ${fees:.2f}"
    if pnl is not None:
        msg += f" | P/L: ${pnl:+.2f}"
    return msg


def format_algo_event(payload: dict) -> str:
    name = payload.get("algorithm_name", "?")
    account = payload.get("account_name", "?")
    old = payload.get("old_status", "?")
    new = payload.get("new_status", "?")
    return f"**Algorithm Status** | {name} on {account}: {old} → {new}"


def format_pdt_event(payload: dict) -> str:
    account = payload.get("account_name", "?")
    count = payload.get("day_trade_count", 0)
    remaining = payload.get("remaining", 0)
    return f"**PDT Warning** | {account}: {count} day trades in 5 days ({remaining} remaining)"


@dataclass
class RouteConfig:
    channel: str
    min_severity: str = "info"
    enabled: bool = True


class DiscordNotifier:
    def __init__(self) -> None:
        self._routes: dict[str, RouteConfig] = {}

    def set_route(
        self, event_type: str, channel: str, min_severity: str = "info"
    ) -> None:
        self._routes[event_type] = RouteConfig(
            channel=channel, min_severity=min_severity
        )

    def disable_route(self, event_type: str) -> None:
        if event_type in self._routes:
            self._routes[event_type].enabled = False

    def get_channel(self, event_type: str) -> Optional[str]:
        route = self._routes.get(event_type)
        if route and route.enabled:
            return route.channel
        return None

    def should_send(self, event_type: str, severity: str) -> bool:
        route = self._routes.get(event_type)
        if not route or not route.enabled:
            return False
        return SEVERITY_ORDER.get(severity, 0) >= SEVERITY_ORDER.get(
            route.min_severity, 0
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_discord_bot.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/discord_bot.py tests/coordinator/test_discord_bot.py
git commit -m "feat(coordinator): add Discord notifier with event routing and formatting"
```

---

### Task 4: Backtest Comparison API

**Files:**
- Create: `coordinator/api/routes/backtests.py`
- Create: `tests/coordinator/test_backtests_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_backtests_api.py
import pytest
from sqlalchemy import select

from coordinator.database.models import BacktestComparison, Algorithm, AlgorithmInstance, Account, Worker


@pytest.fixture
async def seed_comparison(client):
    acct = await client.post("/api/accounts", json={
        "name": "BT Acct", "broker_type": "alpaca",
        "credentials": {"k": "v"}, "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    worker = await client.post("/api/workers", json={
        "name": "BT Pi", "tailscale_ip": "100.64.0.20",
    })
    algo = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/bt-algo", "name": "bt-algo",
    })
    inst = await client.post(f"/api/algorithms/{algo.json()['id']}/instances", json={
        "account_id": acct.json()["id"], "worker_id": worker.json()["id"],
    })
    return {
        "algorithm_id": algo.json()["id"],
        "instance_id": inst.json()["id"],
    }


@pytest.mark.asyncio
async def test_list_comparisons_empty(client):
    response = await client.get("/api/backtests")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_and_list_comparison(client, seed_comparison):
    response = await client.post("/api/backtests", json={
        "instance_id": seed_comparison["instance_id"],
        "algorithm_id": seed_comparison["algorithm_id"],
        "time_range_start": "2025-01-01T00:00:00+00:00",
        "time_range_end": "2025-01-02T00:00:00+00:00",
        "total_ticks": 100,
        "matching_ticks": 95,
        "match_percentage": 95.0,
        "divergences": [{"timestamp": "2025-01-01T10:00:00", "reason": "Signal mismatch"}],
        "summary": "5% divergence in afternoon session",
    })
    assert response.status_code == 201

    list_resp = await client.get("/api/backtests")
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["match_percentage"] == 95.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_backtests_api.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/routes/backtests.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import BacktestComparison

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class ComparisonCreate(BaseModel):
    instance_id: str
    algorithm_id: str
    time_range_start: str
    time_range_end: str
    total_ticks: int
    matching_ticks: int
    match_percentage: float
    divergences: Optional[list[dict]] = None
    summary: Optional[str] = None


def _to_response(c: BacktestComparison) -> dict:
    return {
        "id": c.id,
        "instance_id": c.instance_id,
        "algorithm_id": c.algorithm_id,
        "time_range_start": c.time_range_start.isoformat() if c.time_range_start else None,
        "time_range_end": c.time_range_end.isoformat() if c.time_range_end else None,
        "total_ticks": c.total_ticks,
        "matching_ticks": c.matching_ticks,
        "match_percentage": c.match_percentage,
        "divergences": c.divergences,
        "summary": c.summary,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("")
async def list_comparisons(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BacktestComparison).order_by(desc(BacktestComparison.created_at))
    )
    return [_to_response(c) for c in result.scalars().all()]


@router.post("", status_code=201)
async def create_comparison(body: ComparisonCreate, db: AsyncSession = Depends(get_db)):
    from datetime import datetime
    comp = BacktestComparison(
        instance_id=body.instance_id,
        algorithm_id=body.algorithm_id,
        time_range_start=datetime.fromisoformat(body.time_range_start),
        time_range_end=datetime.fromisoformat(body.time_range_end),
        total_ticks=body.total_ticks,
        matching_ticks=body.matching_ticks,
        match_percentage=body.match_percentage,
        divergences=body.divergences,
        summary=body.summary,
    )
    db.add(comp)
    await db.flush()
    return _to_response(comp)


@router.get("/{comparison_id}")
async def get_comparison(comparison_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BacktestComparison).where(BacktestComparison.id == comparison_id)
    )
    comp = result.scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return _to_response(comp)
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py`:

```python
    from coordinator.api.routes.backtests import router as backtests_router
    app.include_router(backtests_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_backtests_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/backtests.py coordinator/main.py tests/coordinator/test_backtests_api.py
git commit -m "feat(coordinator): add backtest comparison API endpoints"
```

---

### Task 5: Update pyproject.toml + Final Verification

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add discord.py to coordinator dependencies**

Add `"discord.py>=2.4.0"` to the coordinator optional dependencies.

- [ ] **Step 2: Install and verify**

Run: `cd /home/jkern/dev/quilt-trader && pip install -e ".[coordinator,dev]"`
Expected: discord.py installs successfully

- [ ] **Step 3: Run full test suite**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add discord.py dependency"
```
