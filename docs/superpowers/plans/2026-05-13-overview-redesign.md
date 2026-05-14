# Dashboard Overview Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Overview page widgets with trader-focused versions: stacked equity chart, 4×2 KPI grid, algorithms list with sparklines, real trade details, click-through everywhere, no GUIDs in user-facing text.

**Architecture:** Two-sided change — coordinator gets 6 new read endpoints + 1 enriched `/api/instances` response (no schema changes), and the dashboard gets 8 new/rewritten widgets backed by new typed hooks. Customizable widget grid (`DashboardGrid` + `useDashboardStore`) is preserved unchanged; only the widget contents and the default widget set change. Three reusable chart components (`Sparkline`, `Donut`, `StackedAreaChart`) live alongside the existing `EquityCurve`.

**Tech Stack:** FastAPI + SQLAlchemy (async) on the backend, React 18 + TypeScript + React Query + Zustand on the frontend. Charts via `lightweight-charts` for time series and inline SVG for sparklines and donuts.

**Spec:** `docs/superpowers/specs/2026-05-13-overview-redesign-design.md`

**Execution waves (for subagent dispatch):**

1. Wave 1 — 6 backend tasks in parallel (Tasks 1–6)
2. Wave 2 — 3 shared frontend chart components in parallel (Tasks 7–9)
3. Wave 3 — types + hooks consolidation (Task 10), sequential after waves 1–2
4. Wave 4 — 8 widget rewrites in parallel (Tasks 11–18)
5. Wave 5 — wiring + seed + verification (Tasks 19–21), sequential

**Model selection:**
- **opus** — Task 1 (portfolio aggregations), Task 6 (instances enrichment with sparkline downsampling), Task 19 (store migration)
- **sonnet** — every other task

---

## File Map

### Backend — `coordinator/`

| File | Change | Responsibility |
|------|--------|----------------|
| `coordinator/api/routes/portfolio.py` | Create | `/api/portfolio/equity`, `/api/portfolio/kpis`, `/api/portfolio/allocation` |
| `coordinator/api/routes/positions.py` | Create | `/api/positions` listing endpoint |
| `coordinator/api/routes/trades.py` | Create | `/api/trades` listing endpoint |
| `coordinator/api/routes/alerts.py` | Create | `/api/alerts` aggregated alerts endpoint |
| `coordinator/api/routes/accounts.py` | Modify | Add `/api/accounts/snapshots/latest` endpoint |
| `coordinator/api/routes/algorithms.py` | Modify | Enrich `/api/instances` response with `algorithm_name`, `account_name`, `today_pnl`, `pnl_sparkline` |
| `coordinator/main.py` | Modify | Register the 4 new routers |

### Backend tests — `tests/coordinator/`

| File | Change | Responsibility |
|------|--------|----------------|
| `tests/coordinator/test_portfolio_api.py` | Create | Tests for `/equity`, `/kpis`, `/allocation` |
| `tests/coordinator/test_positions_api.py` | Create | Tests for `/api/positions` |
| `tests/coordinator/test_trades_api.py` | Create | Tests for `/api/trades` |
| `tests/coordinator/test_alerts_api.py` | Create | Tests for `/api/alerts` |
| `tests/coordinator/test_accounts_api.py` | Modify | Add tests for `/api/accounts/snapshots/latest` |
| `tests/coordinator/test_algorithms_api.py` | Modify | Add tests for enriched `/api/instances` response |

### Frontend — `dashboard/src/`

| File | Change | Responsibility |
|------|--------|----------------|
| `dashboard/src/components/Sparkline.tsx` | Create | Reusable SVG sparkline |
| `dashboard/src/components/Donut.tsx` | Create | Reusable SVG donut |
| `dashboard/src/components/StackedAreaChart.tsx` | Create | Wraps lightweight-charts for stacked equity |
| `dashboard/src/types/index.ts` | Modify | Add `PortfolioEquityResponse`, `PortfolioKpis`, `AllocSegment`, `AllocationResponse`, `AlertItem`, `AccountSnapshotLatest`; extend `AlgorithmInstance` |
| `dashboard/src/api/client.ts` | Modify | Add new endpoint functions |
| `dashboard/src/api/hooks.ts` | Modify | Add 7 new hooks |
| `dashboard/src/components/widgets/PortfolioEquityWidget.tsx` | Create | Replaces `PortfolioValueWidget` |
| `dashboard/src/components/widgets/KpiStripWidget.tsx` | Create | 4×2 KPI grid |
| `dashboard/src/components/widgets/AlgorithmsWidget.tsx` | Create | Replaces both `ActiveAlgorithmsWidget` and `TodaysPnLWidget` |
| `dashboard/src/components/widgets/OpenPositionsWidget.tsx` | Rewrite | Real symbol/side/qty rows |
| `dashboard/src/components/widgets/RecentTradesWidget.tsx` | Rewrite | Real trade rows |
| `dashboard/src/components/widgets/AccountBalancesWidget.tsx` | Rewrite | With cash/positions stacked bar |
| `dashboard/src/components/widgets/AssetAllocationWidget.tsx` | Create | Donut with By Class / By Symbol toggle |
| `dashboard/src/components/widgets/AlertsWidget.tsx` | Create | Replaces both `SystemEventsWidget` and `BacktestAlertsWidget` |
| `dashboard/src/components/widgets/PortfolioValueWidget.tsx` | Delete | Replaced by PortfolioEquityWidget |
| `dashboard/src/components/widgets/ActiveAlgorithmsWidget.tsx` | Delete | Merged into AlgorithmsWidget |
| `dashboard/src/components/widgets/TodaysPnLWidget.tsx` | Delete | Merged into AlgorithmsWidget |
| `dashboard/src/components/widgets/WorkerHealthWidget.tsx` | Delete | Removed from Overview |
| `dashboard/src/components/widgets/SystemEventsWidget.tsx` | Delete | Merged into AlertsWidget |
| `dashboard/src/components/widgets/BacktestAlertsWidget.tsx` | Delete | Merged into AlertsWidget |
| `dashboard/src/components/widgets/index.ts` | Modify | New WIDGET_REGISTRY + WIDGET_TITLES |
| `dashboard/src/stores/dashboard.ts` | Modify | New DEFAULT_WIDGETS + stale-ID handling for persisted layouts |

### Misc

| File | Change | Responsibility |
|------|--------|----------------|
| `scripts/seed_data.py` | Modify | Seed enough data for new widgets to look populated |

---

## Phase 1 — Backend endpoints (Wave 1, parallel)

### Task 1: Portfolio aggregation endpoints — `/equity`, `/kpis`, `/allocation`

**Files:**
- Create: `coordinator/api/routes/portfolio.py`
- Create: `tests/coordinator/test_portfolio_api.py`
- Modify: `coordinator/main.py` (register router)

**Model:** opus

These three endpoints are bundled in one task because they share a route file and overlapping aggregation logic.

- [ ] **Step 1: Write the failing test for `/api/portfolio/equity`**

Create `tests/coordinator/test_portfolio_api.py`:

```python
import pytest
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_portfolio_equity_empty(client):
    response = await client.get("/api/portfolio/equity?range=1m")
    assert response.status_code == 200
    body = response.json()
    assert body == {"accounts": []}


@pytest.mark.asyncio
async def test_portfolio_equity_with_snapshots(client, db_session):
    from coordinator.database.models import Account, AccountSnapshot

    acct = Account(
        name="Alpaca Main", broker_type="alpaca",
        supported_asset_types=["equities"], pdt_mode="off",
        credentials={},
    )
    db_session.add(acct)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    for i in range(5):
        db_session.add(AccountSnapshot(
            account_id=acct.id,
            timestamp=now - timedelta(days=4 - i),
            total_value=10000.0 + i * 100,
            cash=5000.0,
            positions_value=5000.0 + i * 100,
            source="seed",
        ))
    await db_session.commit()

    response = await client.get("/api/portfolio/equity?range=1m")
    assert response.status_code == 200
    body = response.json()
    assert len(body["accounts"]) == 1
    assert body["accounts"][0]["account_name"] == "Alpaca Main"
    assert len(body["accounts"][0]["points"]) == 5
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/coordinator/test_portfolio_api.py::test_portfolio_equity_empty -v
```
Expected: FAIL with 404 (route does not exist).

- [ ] **Step 3: Create the portfolio router with `/equity`**

Create `coordinator/api/routes/portfolio.py`:

```python
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import Account, AccountSnapshot

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

RangeLiteral = Literal["1d", "1w", "1m", "all"]


def _range_to_cutoff(rng: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    return {
        "1d": now - timedelta(days=1),
        "1w": now - timedelta(weeks=1),
        "1m": now - timedelta(days=30),
        "all": None,
    }[rng]


@router.get("/equity")
async def portfolio_equity(
    range: RangeLiteral = Query("1m"),
    db: AsyncSession = Depends(get_db),
):
    cutoff = _range_to_cutoff(range)

    accts_result = await db.execute(select(Account))
    accounts = accts_result.scalars().all()

    out = []
    for acct in accounts:
        snap_query = select(AccountSnapshot).where(AccountSnapshot.account_id == acct.id)
        if cutoff is not None:
            snap_query = snap_query.where(AccountSnapshot.timestamp >= cutoff)
        snap_query = snap_query.order_by(AccountSnapshot.timestamp)
        snap_result = await db.execute(snap_query)
        snaps = snap_result.scalars().all()

        if not snaps:
            continue

        out.append({
            "account_id": acct.id,
            "account_name": acct.name,
            "points": [
                {"timestamp": s.timestamp.isoformat(), "value": s.total_value}
                for s in snaps
            ],
        })

    return {"accounts": out}
```

- [ ] **Step 4: Register the router in `coordinator/main.py`**

Add to `create_app()` alongside the other `app.include_router(...)` calls (find them after the `health` endpoint):

```python
    from coordinator.api.routes.portfolio import router as portfolio_router
    app.include_router(portfolio_router)
```

- [ ] **Step 5: Run the equity tests to verify they pass**

```bash
pytest tests/coordinator/test_portfolio_api.py -v -k equity
```
Expected: 2 passed.

- [ ] **Step 6: Write the failing test for `/api/portfolio/kpis`**

Append to `tests/coordinator/test_portfolio_api.py`:

```python
@pytest.mark.asyncio
async def test_portfolio_kpis_empty(client):
    response = await client.get("/api/portfolio/kpis")
    assert response.status_code == 200
    body = response.json()
    assert body["total_equity"] == 0
    assert body["today_pnl"] == 0
    assert body["trades_today"] == 0
    assert body["open_positions"] == 0


@pytest.mark.asyncio
async def test_portfolio_kpis_with_data(client, db_session):
    from coordinator.database.models import (
        Account, AccountSnapshot, Position, TradeLog,
    )

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials={})
    db_session.add(acct)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now, total_value=100000.0,
        cash=40000.0, positions_value=60000.0, source="seed",
    ))
    db_session.add(Position(
        account_id=acct.id, status="open", legs=[],
        unrealized_pnl=500.0, net_cost=10000.0,
    ))
    db_session.add(TradeLog(
        account_id=acct.id, source="test", symbol="SPY", side="buy",
        quantity=1.0, filled_price=400.0, timestamp=now,
    ))
    await db_session.commit()

    response = await client.get("/api/portfolio/kpis")
    assert response.status_code == 200
    body = response.json()
    assert body["total_equity"] == 100000.0
    assert body["trades_today"] == 1
    assert body["open_positions"] == 1
    assert body["open_risk"] == 500.0
    assert body["deployed_pct"] == 60.0
    assert body["buying_power"] == 40000.0
```

- [ ] **Step 7: Run the kpis tests to verify they fail**

```bash
pytest tests/coordinator/test_portfolio_api.py -v -k kpis
```
Expected: FAIL with 404.

- [ ] **Step 8: Implement `/api/portfolio/kpis`**

Append to `coordinator/api/routes/portfolio.py`:

```python
from sqlalchemy import func
from coordinator.database.models import Position, TradeLog


@router.get("/kpis")
async def portfolio_kpis(db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Total equity: sum of latest snapshot per account
    accts_result = await db.execute(select(Account))
    accounts = accts_result.scalars().all()
    total_equity = 0.0
    total_cash = 0.0
    total_positions_value = 0.0
    for acct in accounts:
        snap_q = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == acct.id)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(1)
        )
        snap = (await db.execute(snap_q)).scalar_one_or_none()
        if snap:
            total_equity += snap.total_value
            total_cash += snap.cash
            total_positions_value += snap.positions_value

    # Open positions
    pos_q = select(Position).where(Position.status == "open")
    open_positions = (await db.execute(pos_q)).scalars().all()
    open_risk = sum(p.unrealized_pnl or 0.0 for p in open_positions)

    # Today's trades
    trade_q = select(func.count(TradeLog.id)).where(TradeLog.timestamp >= today_start)
    trades_today = (await db.execute(trade_q)).scalar() or 0

    deployed_pct = (
        (total_positions_value / total_equity * 100.0) if total_equity > 0 else 0.0
    )

    return {
        "total_equity": total_equity,
        "today_pnl": open_risk,  # placeholder: realized today + unrealized delta
        "today_pnl_pct": 0.0,
        "trades_today": trades_today,
        "trades_today_wins": 0,
        "trades_today_losses": 0,
        "win_rate": 0.0,
        "win_rate_7d_avg": 0.0,
        "open_positions": len(open_positions),
        "open_positions_long": sum(1 for p in open_positions if (p.net_cost or 0) >= 0),
        "open_positions_short": sum(1 for p in open_positions if (p.net_cost or 0) < 0),
        "open_risk": open_risk,
        "open_risk_pct_equity": (open_risk / total_equity * 100.0) if total_equity > 0 else 0.0,
        "deployed_pct": deployed_pct,
        "deployed_usd": total_positions_value,
        "buying_power": total_cash,
        "buying_power_pct": (total_cash / total_equity * 100.0) if total_equity > 0 else 0.0,
    }
```

- [ ] **Step 9: Run kpis tests to verify they pass**

```bash
pytest tests/coordinator/test_portfolio_api.py -v -k kpis
```
Expected: 2 passed.

- [ ] **Step 10: Write the failing test for `/api/portfolio/allocation`**

Append to `tests/coordinator/test_portfolio_api.py`:

```python
@pytest.mark.asyncio
async def test_portfolio_allocation_empty(client):
    response = await client.get("/api/portfolio/allocation")
    assert response.status_code == 200
    body = response.json()
    assert body["by_class"] == []
    assert body["by_symbol"] == []


@pytest.mark.asyncio
async def test_portfolio_allocation_with_positions(client, db_session):
    from coordinator.database.models import Account, AccountSnapshot, Position

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials={})
    db_session.add(acct)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now, total_value=100000.0,
        cash=20000.0, positions_value=80000.0, source="seed",
    ))
    db_session.add(Position(
        account_id=acct.id, status="open",
        legs=[{"symbol": "SPY", "asset_type": "equities", "value": 50000.0}],
        net_cost=50000.0,
    ))
    db_session.add(Position(
        account_id=acct.id, status="open",
        legs=[{"symbol": "BTC", "asset_type": "crypto", "value": 30000.0}],
        net_cost=30000.0,
    ))
    await db_session.commit()

    response = await client.get("/api/portfolio/allocation")
    body = response.json()
    classes = {seg["key"]: seg for seg in body["by_class"]}
    assert classes["equities"]["value_usd"] == 50000.0
    assert classes["crypto"]["value_usd"] == 30000.0
    assert classes["cash"]["value_usd"] == 20000.0
    symbols = {seg["key"]: seg for seg in body["by_symbol"]}
    assert "SPY" in symbols
    assert "BTC" in symbols
```

- [ ] **Step 11: Run allocation tests to verify they fail**

```bash
pytest tests/coordinator/test_portfolio_api.py -v -k allocation
```
Expected: FAIL.

- [ ] **Step 12: Implement `/api/portfolio/allocation`**

Append to `coordinator/api/routes/portfolio.py`:

```python
CLASS_COLORS = {
    "equities": "#f59e0b",
    "crypto": "#3b82f6",
    "options": "#8b5cf6",
    "futures": "#ef4444",
    "cash": "#6b7280",
}

SYMBOL_PALETTE = [
    "#f97316", "#3b82f6", "#14b8a6", "#a855f7", "#ec4899",
    "#84cc16", "#06b6d4", "#eab308", "#10b981", "#f43f5e",
]


@router.get("/allocation")
async def portfolio_allocation(db: AsyncSession = Depends(get_db)):
    # Cash: sum across latest snapshots per account
    accts = (await db.execute(select(Account))).scalars().all()
    total_cash = 0.0
    for acct in accts:
        snap_q = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == acct.id)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(1)
        )
        snap = (await db.execute(snap_q)).scalar_one_or_none()
        if snap:
            total_cash += snap.cash

    # Open positions
    pos_q = select(Position).where(Position.status == "open")
    positions = (await db.execute(pos_q)).scalars().all()

    class_totals: dict[str, float] = {}
    symbol_totals: dict[str, float] = {}
    for pos in positions:
        for leg in pos.legs or []:
            asset_class = leg.get("asset_type", "equities")
            symbol = leg.get("symbol", "?")
            value = float(leg.get("value", 0.0))
            class_totals[asset_class] = class_totals.get(asset_class, 0.0) + value
            symbol_totals[symbol] = symbol_totals.get(symbol, 0.0) + value

    if total_cash > 0:
        class_totals["cash"] = total_cash
        symbol_totals["Cash"] = total_cash

    grand = sum(class_totals.values()) or 1.0

    by_class = [
        {
            "key": k,
            "label": k.title(),
            "value_usd": v,
            "percent": round(v / grand * 100.0, 1),
            "color": CLASS_COLORS.get(k, "#6b7280"),
        }
        for k, v in sorted(class_totals.items(), key=lambda x: -x[1])
    ]

    # Top 6 symbols + "More" rollup + cash always last
    cash_value = symbol_totals.pop("Cash", 0.0)
    ranked = sorted(symbol_totals.items(), key=lambda x: -x[1])
    top = ranked[:6]
    rest = ranked[6:]
    by_symbol = [
        {
            "key": sym,
            "label": sym,
            "value_usd": v,
            "percent": round(v / grand * 100.0, 1),
            "color": SYMBOL_PALETTE[i % len(SYMBOL_PALETTE)],
        }
        for i, (sym, v) in enumerate(top)
    ]
    if rest:
        rest_total = sum(v for _, v in rest)
        by_symbol.append({
            "key": "_more",
            "label": f"+{len(rest)} more",
            "value_usd": rest_total,
            "percent": round(rest_total / grand * 100.0, 1),
            "color": "#6b7280",
        })
    if cash_value > 0:
        by_symbol.append({
            "key": "Cash",
            "label": "Cash",
            "value_usd": cash_value,
            "percent": round(cash_value / grand * 100.0, 1),
            "color": "#6b7280",
        })

    return {"by_class": by_class, "by_symbol": by_symbol}
```

- [ ] **Step 13: Run all portfolio tests**

```bash
pytest tests/coordinator/test_portfolio_api.py -v
```
Expected: 6 passed.

- [ ] **Step 14: Commit**

```bash
git add coordinator/api/routes/portfolio.py coordinator/main.py tests/coordinator/test_portfolio_api.py
git commit -m "feat(coordinator): add portfolio equity/kpis/allocation endpoints"
```

---

### Task 2: `GET /api/positions` listing endpoint

**Files:**
- Create: `coordinator/api/routes/positions.py`
- Create: `tests/coordinator/test_positions_api.py`
- Modify: `coordinator/main.py`

**Model:** sonnet

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_positions_api.py`:

```python
import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_positions_empty(client):
    response = await client.get("/api/positions")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.asyncio
async def test_positions_open_only(client, db_session):
    from coordinator.database.models import (
        Account, Algorithm, AlgorithmInstance, Worker, Position,
    )

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials={})
    db_session.add(acct)
    await db_session.flush()
    worker = Worker(name="w1", tailscale_ip="1.1.1.1", status="active", max_algorithms=5)
    db_session.add(worker)
    algo = Algorithm(repo_url="x", name="momentum-btc", install_status="installed")
    db_session.add(algo)
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
        status="running", state_stale=False,
    )
    db_session.add(inst)
    await db_session.flush()

    db_session.add(Position(
        account_id=acct.id, instance_id=inst.id, status="open",
        legs=[{"symbol": "BTC", "side": "long", "quantity": 0.5,
               "avg_price": 68000.0, "current_price": 70000.0, "asset_type": "crypto"}],
        unrealized_pnl=1000.0, net_cost=34000.0,
    ))
    db_session.add(Position(
        account_id=acct.id, instance_id=inst.id, status="closed",
        legs=[], net_cost=0.0,
    ))
    await db_session.commit()

    response = await client.get("/api/positions?status=open")
    body = response.json()
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["symbol"] == "BTC"
    assert row["side"] == "long"
    assert row["quantity"] == 0.5
    assert row["algorithm_name"] == "momentum-btc"
    assert row["instance_id"] == inst.id
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/coordinator/test_positions_api.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the positions router**

Create `coordinator/api/routes/positions.py`:

```python
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import (
    Position, AlgorithmInstance, Algorithm,
)

router = APIRouter(prefix="/api/positions", tags=["positions"])


def _expand(pos: Position, algo_name: Optional[str]) -> dict:
    legs = pos.legs or []
    first = legs[0] if legs else {}
    extra_legs = max(0, len(legs) - 1)
    return {
        "id": pos.id,
        "instance_id": pos.instance_id,
        "account_id": pos.account_id,
        "algorithm_name": algo_name,
        "status": pos.status,
        "symbol": first.get("symbol"),
        "side": first.get("side"),
        "quantity": first.get("quantity"),
        "avg_price": first.get("avg_price"),
        "current_price": first.get("current_price"),
        "asset_type": first.get("asset_type"),
        "unrealized_pnl": pos.unrealized_pnl,
        "net_pnl": pos.net_pnl,
        "net_cost": pos.net_cost,
        "extra_legs": extra_legs,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
    }


@router.get("")
async def list_positions(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(Position)
    if status:
        query = query.where(Position.status == status)
    query = query.order_by(Position.opened_at.desc()).limit(limit)
    positions = (await db.execute(query)).scalars().all()

    # Resolve algorithm names via instance_id
    instance_ids = {p.instance_id for p in positions if p.instance_id}
    algo_names: dict[str, str] = {}
    if instance_ids:
        joined = await db.execute(
            select(AlgorithmInstance.id, Algorithm.name)
            .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
            .where(AlgorithmInstance.id.in_(instance_ids))
        )
        algo_names = {row[0]: row[1] for row in joined.all()}

    return {
        "items": [_expand(p, algo_names.get(p.instance_id) if p.instance_id else None) for p in positions]
    }
```

- [ ] **Step 4: Register router in `coordinator/main.py`**

```python
    from coordinator.api.routes.positions import router as positions_router
    app.include_router(positions_router)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
pytest tests/coordinator/test_positions_api.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/positions.py coordinator/main.py tests/coordinator/test_positions_api.py
git commit -m "feat(coordinator): add /api/positions listing endpoint"
```

---

### Task 3: `GET /api/trades` listing endpoint

**Files:**
- Create: `coordinator/api/routes/trades.py`
- Create: `tests/coordinator/test_trades_api.py`
- Modify: `coordinator/main.py`

**Model:** sonnet

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_trades_api.py`:

```python
import pytest
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_trades_empty(client):
    response = await client.get("/api/trades")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.asyncio
async def test_trades_sorted_desc_with_algo_name(client, db_session):
    from coordinator.database.models import (
        Account, Algorithm, AlgorithmInstance, Worker, TradeLog,
    )

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials={})
    db_session.add(acct)
    worker = Worker(name="w", tailscale_ip="1.1.1.1", status="active", max_algorithms=1)
    db_session.add(worker)
    algo = Algorithm(repo_url="x", name="momentum-btc", install_status="installed")
    db_session.add(algo)
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
        status="running", state_stale=False,
    )
    db_session.add(inst)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(TradeLog(
        account_id=acct.id, instance_id=inst.id, source="alpaca",
        symbol="BTC", side="buy", quantity=0.05, filled_price=70000.0,
        timestamp=now - timedelta(minutes=5),
    ))
    db_session.add(TradeLog(
        account_id=acct.id, instance_id=inst.id, source="alpaca",
        symbol="ETH", side="sell", quantity=1.0, filled_price=3200.0,
        timestamp=now,
    ))
    await db_session.commit()

    response = await client.get("/api/trades?limit=10")
    body = response.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["symbol"] == "ETH"  # newest first
    assert body["items"][0]["algorithm_name"] == "momentum-btc"
    assert body["items"][0]["notional"] == 3200.0
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/coordinator/test_trades_api.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the trades router**

Create `coordinator/api/routes/trades.py`:

```python
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import (
    TradeLog, AlgorithmInstance, Algorithm,
)

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _to_response(trade: TradeLog, algo_name: Optional[str]) -> dict:
    notional = (trade.filled_price or 0.0) * (trade.quantity or 0.0)
    return {
        "id": trade.id,
        "instance_id": trade.instance_id,
        "account_id": trade.account_id,
        "algorithm_name": algo_name,
        "timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
        "symbol": trade.symbol,
        "asset_type": trade.asset_type,
        "side": trade.side,
        "quantity": trade.quantity,
        "filled_price": trade.filled_price,
        "notional": notional,
        "fees": trade.fees,
    }


@router.get("")
async def list_trades(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(TradeLog)
        .order_by(desc(TradeLog.timestamp))
        .limit(limit)
    )
    trades = (await db.execute(query)).scalars().all()

    instance_ids = {t.instance_id for t in trades if t.instance_id}
    algo_names: dict[str, str] = {}
    if instance_ids:
        joined = await db.execute(
            select(AlgorithmInstance.id, Algorithm.name)
            .join(Algorithm, AlgorithmInstance.algorithm_id == Algorithm.id)
            .where(AlgorithmInstance.id.in_(instance_ids))
        )
        algo_names = {row[0]: row[1] for row in joined.all()}

    return {
        "items": [_to_response(t, algo_names.get(t.instance_id) if t.instance_id else None) for t in trades]
    }
```

- [ ] **Step 4: Register router in `coordinator/main.py`**

```python
    from coordinator.api.routes.trades import router as trades_router
    app.include_router(trades_router)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
pytest tests/coordinator/test_trades_api.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/trades.py coordinator/main.py tests/coordinator/test_trades_api.py
git commit -m "feat(coordinator): add /api/trades listing endpoint"
```

---

### Task 4: `GET /api/alerts` aggregated endpoint

**Files:**
- Create: `coordinator/api/routes/alerts.py`
- Create: `tests/coordinator/test_alerts_api.py`
- Modify: `coordinator/main.py`

**Model:** sonnet

- [ ] **Step 1: Write the failing test**

Create `tests/coordinator/test_alerts_api.py`:

```python
import pytest
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_alerts_empty(client):
    response = await client.get("/api/alerts")
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.asyncio
async def test_alerts_combines_events_and_backtests(client, db_session):
    from coordinator.database.models import (
        Account, Algorithm, AlgorithmInstance, Worker, Event, BacktestComparison,
    )

    acct = Account(name="A", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials={})
    db_session.add(acct)
    worker = Worker(name="pi-alpha", tailscale_ip="1.1.1.1", status="active", max_algorithms=1)
    db_session.add(worker)
    algo = Algorithm(repo_url="x", name="momentum-btc", install_status="installed")
    db_session.add(algo)
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
        status="running", state_stale=False,
    )
    db_session.add(inst)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(Event(
        source_type="instance", source_id=inst.id,
        event_type="signal_rejected", severity="warning",
        timestamp=now,
    ))
    db_session.add(Event(
        source_type="worker", source_id=worker.id,
        event_type="worker_disconnected", severity="error",
        timestamp=now - timedelta(minutes=5),
    ))
    db_session.add(BacktestComparison(
        instance_id=inst.id, algorithm_id=algo.id,
        time_range_start=now - timedelta(days=1),
        time_range_end=now,
        total_ticks=100, matching_ticks=87, match_percentage=87.0,
    ))
    await db_session.commit()

    response = await client.get("/api/alerts")
    body = response.json()
    assert len(body["items"]) == 3

    sources = {item["source_name"] for item in body["items"]}
    assert "momentum-btc" in sources
    assert "pi-alpha" in sources
    for item in body["items"]:
        assert item["link_path"] is not None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/coordinator/test_alerts_api.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create the alerts router**

Create `coordinator/api/routes/alerts.py`:

```python
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import (
    Event, BacktestComparison, AlgorithmInstance, Algorithm, Worker,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


async def _resolve_event(event: Event, db: AsyncSession) -> tuple[str, str | None]:
    """Return (source_name, link_path)."""
    if event.source_type == "instance" and event.source_id:
        result = await db.execute(
            select(Algorithm.name)
            .join(AlgorithmInstance, AlgorithmInstance.algorithm_id == Algorithm.id)
            .where(AlgorithmInstance.id == event.source_id)
        )
        name = result.scalar_one_or_none() or event.source_id
        return name, f"/instances/{event.source_id}"
    if event.source_type == "worker" and event.source_id:
        worker = (await db.execute(
            select(Worker).where(Worker.id == event.source_id)
        )).scalar_one_or_none()
        name = worker.name if worker else event.source_id
        return name, f"/workers/{event.source_id}"
    return event.source_id or "system", None


@router.get("")
async def list_alerts(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)

    # Warning/error events from the last 2 days
    events_result = await db.execute(
        select(Event)
        .where(or_(Event.severity == "warning", Event.severity == "error"))
        .where(Event.timestamp >= cutoff)
        .order_by(Event.timestamp.desc())
        .limit(limit)
    )
    events = events_result.scalars().all()

    items = []
    for ev in events:
        source_name, link = await _resolve_event(ev, db)
        items.append({
            "kind": "event",
            "id": ev.id,
            "severity": ev.severity,
            "label": ev.event_type.replace("_", " ").title(),
            "source_name": source_name,
            "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
            "link_path": link,
            "pill": ev.severity.upper()[:4],
            "pill_color": "err" if ev.severity == "error" else "warn",
        })

    # Backtest divergences with <90% match in the last 2 days
    bt_result = await db.execute(
        select(BacktestComparison, Algorithm.name)
        .join(Algorithm, BacktestComparison.algorithm_id == Algorithm.id)
        .where(BacktestComparison.match_percentage < 90.0)
        .where(BacktestComparison.created_at >= cutoff)
        .order_by(BacktestComparison.created_at.desc())
        .limit(limit)
    )
    for bt, algo_name in bt_result.all():
        items.append({
            "kind": "backtest",
            "id": bt.id,
            "severity": "warning",
            "label": "Backtest divergence",
            "source_name": algo_name,
            "timestamp": bt.created_at.isoformat() if bt.created_at else None,
            "link_path": f"/backtests/{bt.id}",
            "pill": f"{bt.match_percentage:.1f}%",
            "pill_color": "backtest",
        })

    items.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return {"items": items[:limit]}
```

- [ ] **Step 4: Register router in `coordinator/main.py`**

```python
    from coordinator.api.routes.alerts import router as alerts_router
    app.include_router(alerts_router)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
pytest tests/coordinator/test_alerts_api.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/alerts.py coordinator/main.py tests/coordinator/test_alerts_api.py
git commit -m "feat(coordinator): add /api/alerts aggregated endpoint"
```

---

### Task 5: `GET /api/accounts/snapshots/latest`

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Modify: `tests/coordinator/test_accounts_api.py`

**Model:** sonnet

- [ ] **Step 1: Append the failing test**

Append to `tests/coordinator/test_accounts_api.py`:

```python
@pytest.mark.asyncio
async def test_accounts_snapshots_latest(client, db_session):
    from datetime import datetime, timedelta, timezone
    from coordinator.database.models import Account, AccountSnapshot

    acct = Account(
        name="Alpaca Main", broker_type="alpaca",
        supported_asset_types=["equities"], pdt_mode="off",
        credentials={},
    )
    db_session.add(acct)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now - timedelta(hours=25),
        total_value=10000.0, cash=4000.0, positions_value=6000.0,
        source="seed",
    ))
    db_session.add(AccountSnapshot(
        account_id=acct.id, timestamp=now,
        total_value=10500.0, cash=4000.0, positions_value=6500.0,
        source="seed",
    ))
    await db_session.commit()

    response = await client.get("/api/accounts/snapshots/latest")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["account_name"] == "Alpaca Main"
    assert item["latest"]["total_value"] == 10500.0
    assert item["prior"]["total_value"] == 10000.0
    assert item["day_pct"] == pytest.approx(5.0)
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/coordinator/test_accounts_api.py::test_accounts_snapshots_latest -v
```
Expected: FAIL with 404.

- [ ] **Step 3: Add the endpoint to `coordinator/api/routes/accounts.py`**

Add at the bottom of the file (after the existing endpoints):

```python
from datetime import datetime, timedelta, timezone
from coordinator.database.models import AccountSnapshot


def _snap_to_dict(snap: "AccountSnapshot") -> dict:
    return {
        "timestamp": snap.timestamp.isoformat(),
        "total_value": snap.total_value,
        "cash": snap.cash,
        "positions_value": snap.positions_value,
    }


@router.get("/snapshots/latest")
async def accounts_snapshots_latest(db: AsyncSession = Depends(get_db)):
    accounts = (await db.execute(select(Account))).scalars().all()
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    items = []
    for acct in accounts:
        latest_q = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == acct.id)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(1)
        )
        latest = (await db.execute(latest_q)).scalar_one_or_none()
        if not latest:
            continue

        prior_q = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == acct.id)
            .where(AccountSnapshot.timestamp <= cutoff_24h)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(1)
        )
        prior = (await db.execute(prior_q)).scalar_one_or_none()

        day_pct = None
        if prior and prior.total_value:
            day_pct = (latest.total_value - prior.total_value) / prior.total_value * 100.0

        items.append({
            "account_id": acct.id,
            "account_name": acct.name,
            "broker_type": acct.broker_type,
            "latest": _snap_to_dict(latest),
            "prior": _snap_to_dict(prior) if prior else None,
            "day_pct": day_pct,
        })

    return {"items": items}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/coordinator/test_accounts_api.py -v
```
Expected: all pass (including the new test).

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/accounts.py tests/coordinator/test_accounts_api.py
git commit -m "feat(coordinator): add /api/accounts/snapshots/latest endpoint"
```

---

### Task 6: Enrich `/api/instances` response

**Files:**
- Modify: `coordinator/api/routes/algorithms.py`
- Modify: `tests/coordinator/test_algorithms_api.py`

**Model:** opus

Adds four new fields to the existing list response: `algorithm_name`, `account_name`, `today_pnl`, `pnl_sparkline`. Existing tests must still pass.

- [ ] **Step 1: Append the failing test**

Append to `tests/coordinator/test_algorithms_api.py`:

```python
@pytest.mark.asyncio
async def test_list_instances_enriched_fields(client, db_session):
    from datetime import datetime, timezone
    from coordinator.database.models import (
        Account, Worker, Algorithm, AlgorithmInstance, AlgorithmRun, TradeLog,
    )

    acct = Account(name="Alpaca Main", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials={})
    db_session.add(acct)
    worker = Worker(name="pi-alpha", tailscale_ip="1.1.1.1", status="active", max_algorithms=2)
    db_session.add(worker)
    algo = Algorithm(repo_url="x", name="momentum-btc", install_status="installed")
    db_session.add(algo)
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
        status="running", state_stale=False,
    )
    db_session.add(inst)
    await db_session.flush()
    db_session.add(AlgorithmRun(
        instance_id=inst.id, run_number=1, status="running",
        equity_curve=[
            {"timestamp": "2026-01-01T00:00:00Z", "equity": 10000.0},
            {"timestamp": "2026-01-02T00:00:00Z", "equity": 10100.0},
            {"timestamp": "2026-01-03T00:00:00Z", "equity": 10250.0},
        ],
    ))
    now = datetime.now(timezone.utc)
    db_session.add(TradeLog(
        account_id=acct.id, instance_id=inst.id, source="alpaca",
        symbol="BTC", side="buy", quantity=0.01, filled_price=70000.0,
        timestamp=now,
    ))
    await db_session.commit()

    response = await client.get("/api/instances")
    body = response.json()
    assert len(body) >= 1
    row = next(r for r in body if r["id"] == inst.id)
    assert row["algorithm_name"] == "momentum-btc"
    assert row["account_name"] == "Alpaca Main"
    assert row["pnl_sparkline"] is not None
    assert len(row["pnl_sparkline"]) <= 20
    assert row["today_pnl"] is not None
```

- [ ] **Step 2: Run the new test to verify it fails**

```bash
pytest tests/coordinator/test_algorithms_api.py::test_list_instances_enriched_fields -v
```
Expected: FAIL (KeyError on `algorithm_name` or similar).

- [ ] **Step 3: Locate the existing `_instance_to_response` helper**

In `coordinator/api/routes/algorithms.py`, find the function that converts an `AlgorithmInstance` to a dict. Read the existing implementation to understand its structure. It will need replacing with an async version that takes the db session and does the joins.

- [ ] **Step 4: Implement the enrichment helper**

Add to `coordinator/api/routes/algorithms.py` (replacing the existing sync `_instance_to_response`, OR adding a new `_enrich_instance` that the existing one calls):

```python
from datetime import datetime, timezone
from coordinator.database.models import AlgorithmRun, TradeLog


def _downsample(curve: list[dict], target: int = 20) -> list[float]:
    if not curve:
        return []
    points = [float(p.get("equity", 0.0)) for p in curve]
    if len(points) <= target:
        return points
    step = len(points) / target
    return [points[int(i * step)] for i in range(target)]


async def _enrich_instance(inst: "AlgorithmInstance", db: AsyncSession) -> dict:
    # Resolve names
    algo = (await db.execute(
        select(Algorithm).where(Algorithm.id == inst.algorithm_id)
    )).scalar_one_or_none()
    acct = (await db.execute(
        select(Account).where(Account.id == inst.account_id)
    )).scalar_one_or_none()

    # Latest run's equity curve, downsampled
    run = (await db.execute(
        select(AlgorithmRun)
        .where(AlgorithmRun.instance_id == inst.id)
        .order_by(AlgorithmRun.run_number.desc())
        .limit(1)
    )).scalar_one_or_none()
    sparkline = _downsample(run.equity_curve or []) if run else None

    # Today's P&L from trade_log (realized only — unrealized delta is hard without per-tick snapshots)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    trades_today = (await db.execute(
        select(TradeLog)
        .where(TradeLog.instance_id == inst.id)
        .where(TradeLog.timestamp >= today_start)
    )).scalars().all()
    today_pnl = sum(
        (t.filled_price or 0.0) * (t.quantity or 0.0) * (-1 if (t.side or "").lower() == "buy" else 1)
        for t in trades_today
    )

    return {
        "id": inst.id,
        "algorithm_id": inst.algorithm_id,
        "algorithm_name": algo.name if algo else None,
        "account_id": inst.account_id,
        "account_name": acct.name if acct else None,
        "worker_id": inst.worker_id,
        "status": inst.status,
        "active_run_id": inst.active_run_id,
        "config_values": inst.config_values,
        "persisted_state": inst.persisted_state,
        "state_stale": inst.state_stale,
        "lifetime_metrics": inst.lifetime_metrics,
        "today_pnl": today_pnl,
        "pnl_sparkline": sparkline,
        "created_at": inst.created_at.isoformat() if inst.created_at else None,
        "updated_at": inst.updated_at.isoformat() if inst.updated_at else None,
    }
```

Import `Account` at the top of the file if not already present.

- [ ] **Step 5: Update `list_all_instances` and `list_instances` to use the new helper**

Find both functions (around lines 140–152 of `coordinator/api/routes/algorithms.py`) and replace their bodies:

```python
@router.get("/api/algorithms/{algorithm_id}/instances")
async def list_instances(algorithm_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.algorithm_id == algorithm_id)
    )
    return [await _enrich_instance(i, db) for i in result.scalars().all()]


@router.get("/api/instances")
async def list_all_instances(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlgorithmInstance))
    return [await _enrich_instance(i, db) for i in result.scalars().all()]
```

The single-instance `get_instance` should also be updated for consistency:

```python
@router.get("/api/instances/{instance_id}")
async def get_instance(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
    )
    inst = result.scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return await _enrich_instance(inst, db)
```

- [ ] **Step 6: Verify the old sync `_instance_to_response` is no longer referenced**

Search the file for `_instance_to_response`:

```bash
grep -n "_instance_to_response" coordinator/api/routes/algorithms.py
```

Replace each remaining usage with `await _enrich_instance(..., db)`. Delete the old sync helper once unused.

- [ ] **Step 7: Run all algorithms tests to verify nothing regressed**

```bash
pytest tests/coordinator/test_algorithms_api.py -v
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add coordinator/api/routes/algorithms.py tests/coordinator/test_algorithms_api.py
git commit -m "feat(coordinator): enrich /api/instances with names, today_pnl, pnl_sparkline"
```

---

## Phase 2 — Frontend shared chart components (Wave 2, parallel)

### Task 7: `Sparkline.tsx`

**Files:**
- Create: `dashboard/src/components/Sparkline.tsx`

**Model:** sonnet

A pure SVG sparkline used in `AlgorithmsWidget`. No test (no test runner is configured in `dashboard/`).

- [ ] **Step 1: Create the component**

Create `dashboard/src/components/Sparkline.tsx`:

```tsx
interface SparklineProps {
  points: number[];
  color?: string;
  height?: number;
  className?: string;
}

export function Sparkline({
  points,
  color = "#10b981",
  height = 26,
  className,
}: SparklineProps) {
  if (points.length < 2) {
    return (
      <svg
        viewBox="0 0 100 30"
        preserveAspectRatio="none"
        style={{ width: "100%", height, display: "block" }}
        className={className}
      />
    );
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;

  const path = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * 100;
      const y = 30 - ((p - min) / range) * 28 - 1;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox="0 0 100 30"
      preserveAspectRatio="none"
      style={{ width: "100%", height, display: "block" }}
      className={className}
    >
      <path
        d={path}
        stroke={color}
        strokeWidth={1.2}
        fill="none"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
```

- [ ] **Step 2: Build the dashboard to verify it type-checks**

```bash
cd dashboard && npm run typecheck
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/Sparkline.tsx
git commit -m "feat(dashboard): add reusable Sparkline component"
```

---

### Task 8: `Donut.tsx`

**Files:**
- Create: `dashboard/src/components/Donut.tsx`

**Model:** sonnet

- [ ] **Step 1: Create the component**

Create `dashboard/src/components/Donut.tsx`:

```tsx
export interface DonutSegment {
  key: string;
  label: string;
  value_usd: number;
  percent: number;
  color: string;
}

interface DonutProps {
  segments: DonutSegment[];
  size?: number;
  thickness?: number;
}

export function Donut({ segments, size = 130, thickness = 26 }: DonutProps) {
  const radius = size / 2;
  const innerRadius = radius - thickness;
  const circumference = 2 * Math.PI * (radius - thickness / 2);

  let offset = 0;
  const arcs = segments.map((seg) => {
    const length = (seg.percent / 100) * circumference;
    const arc = (
      <circle
        key={seg.key}
        cx={radius}
        cy={radius}
        r={radius - thickness / 2}
        fill="none"
        stroke={seg.color}
        strokeWidth={thickness}
        strokeDasharray={`${length} ${circumference - length}`}
        strokeDashoffset={-offset}
        transform={`rotate(-90 ${radius} ${radius})`}
      />
    );
    offset += length;
    return arc;
  });

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      style={{ flexShrink: 0 }}
    >
      <circle
        cx={radius}
        cy={radius}
        r={radius - thickness / 2}
        fill="none"
        stroke="#1f2937"
        strokeWidth={thickness}
      />
      {arcs}
    </svg>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/Donut.tsx
git commit -m "feat(dashboard): add reusable Donut component"
```

---

### Task 9: `StackedAreaChart.tsx`

**Files:**
- Create: `dashboard/src/components/StackedAreaChart.tsx`

**Model:** sonnet

A thin wrapper around `lightweight-charts` that adds one `addAreaSeries` per band, with each series receiving its band's cumulative values.

- [ ] **Step 1: Create the component**

Create `dashboard/src/components/StackedAreaChart.tsx`:

```tsx
import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type AreaData,
  type Time,
} from "lightweight-charts";

export interface StackedAreaBand {
  key: string;
  name: string;
  color: string;
  points: { timestamp: string; value: number }[];
}

interface StackedAreaChartProps {
  bands: StackedAreaBand[];
  height?: number;
}

const BAND_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#a855f7", "#ec4899"];

export function StackedAreaChart({ bands, height = 200 }: StackedAreaChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#111827" },
        textColor: "#9ca3af",
      },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      rightPriceScale: { borderColor: "#374151" },
      timeScale: { borderColor: "#374151", timeVisible: true },
    });
    chartRef.current = chart;

    const observer = new ResizeObserver((entries) => {
      const e = entries[0];
      if (e && chartRef.current) {
        chartRef.current.applyOptions({ width: e.contentRect.width });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || bands.length === 0) return;

    // Build a unified timeline
    const allTimestamps = new Set<number>();
    bands.forEach((b) =>
      b.points.forEach((p) =>
        allTimestamps.add(new Date(p.timestamp).getTime() / 1000)
      )
    );
    const timeline = [...allTimestamps].sort((a, b) => a - b);

    // Per band: align to timeline (last-known carry-forward), then accumulate vertically.
    const bandValuesAtTime: Map<number, number[]> = new Map();
    bands.forEach((band, bi) => {
      const sorted = [...band.points].sort(
        (a, b) =>
          new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      );
      let cursor = 0;
      let last = 0;
      timeline.forEach((t) => {
        while (
          cursor < sorted.length &&
          new Date(sorted[cursor].timestamp).getTime() / 1000 <= t
        ) {
          last = sorted[cursor].value;
          cursor++;
        }
        const arr = bandValuesAtTime.get(t) ?? [];
        arr[bi] = last;
        bandValuesAtTime.set(t, arr);
      });
    });

    bands.forEach((band, bi) => {
      const color = band.color || BAND_COLORS[bi % BAND_COLORS.length];
      const series = chart.addAreaSeries({
        lineColor: color,
        topColor: color + "cc",
        bottomColor: color + "33",
        lineWidth: 1,
      });
      const data: AreaData<Time>[] = timeline.map((t) => {
        const values = bandValuesAtTime.get(t) ?? [];
        const cumulative = values.slice(0, bi + 1).reduce((s, v) => s + (v || 0), 0);
        return { time: t as Time, value: cumulative };
      });
      series.setData(data);
    });

    chart.timeScale().fitContent();
  }, [bands]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height, display: "block" }}
    />
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/StackedAreaChart.tsx
git commit -m "feat(dashboard): add StackedAreaChart wrapper for lightweight-charts"
```

---

## Phase 3 — Types and hooks (Wave 3, sequential)

### Task 10: Types + API client + hooks for the new endpoints

**Files:**
- Modify: `dashboard/src/types/index.ts`
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/api/hooks.ts`

**Model:** sonnet

- [ ] **Step 1: Add types**

Append to `dashboard/src/types/index.ts`:

```ts
export interface PortfolioEquityPoint {
  timestamp: string;
  value: number;
}

export interface PortfolioEquityAccount {
  account_id: string;
  account_name: string;
  points: PortfolioEquityPoint[];
}

export interface PortfolioEquityResponse {
  accounts: PortfolioEquityAccount[];
}

export interface PortfolioKpis {
  total_equity: number;
  today_pnl: number;
  today_pnl_pct: number;
  trades_today: number;
  trades_today_wins: number;
  trades_today_losses: number;
  win_rate: number;
  win_rate_7d_avg: number;
  open_positions: number;
  open_positions_long: number;
  open_positions_short: number;
  open_risk: number;
  open_risk_pct_equity: number;
  deployed_pct: number;
  deployed_usd: number;
  buying_power: number;
  buying_power_pct: number;
}

export interface AllocSegment {
  key: string;
  label: string;
  value_usd: number;
  percent: number;
  color: string;
}

export interface AllocationResponse {
  by_class: AllocSegment[];
  by_symbol: AllocSegment[];
}

export interface OpenPositionRow {
  id: string;
  instance_id: string | null;
  account_id: string;
  algorithm_name: string | null;
  status: string;
  symbol: string | null;
  side: string | null;
  quantity: number | null;
  avg_price: number | null;
  current_price: number | null;
  asset_type: string | null;
  unrealized_pnl: number | null;
  net_pnl: number | null;
  net_cost: number;
  extra_legs: number;
  opened_at: string | null;
}

export interface TradeRow {
  id: string;
  instance_id: string | null;
  account_id: string;
  algorithm_name: string | null;
  timestamp: string | null;
  symbol: string;
  asset_type: string;
  side: string;
  quantity: number;
  filled_price: number;
  notional: number;
  fees: number;
}

export interface AlertItem {
  kind: "event" | "backtest";
  id: string;
  severity: string;
  label: string;
  source_name: string;
  timestamp: string | null;
  link_path: string | null;
  pill: string;
  pill_color: "warn" | "err" | "backtest";
}

export interface AccountSnapshotLatestItem {
  account_id: string;
  account_name: string;
  broker_type: string;
  latest: {
    timestamp: string;
    total_value: number;
    cash: number;
    positions_value: number;
  };
  prior: {
    timestamp: string;
    total_value: number;
    cash: number;
    positions_value: number;
  } | null;
  day_pct: number | null;
}
```

Modify the existing `AlgorithmInstance` interface to add four optional fields:

```ts
export interface AlgorithmInstance {
  id: string;
  algorithm_id: string;
  account_id: string;
  worker_id: string;
  status: string;
  active_run_id: string | null;
  config_values: Record<string, unknown> | null;
  persisted_state: Record<string, unknown> | null;
  state_stale: boolean;
  lifetime_metrics: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  // Enriched fields from /api/instances response:
  algorithm_name?: string | null;
  account_name?: string | null;
  today_pnl?: number | null;
  pnl_sparkline?: number[] | null;
}
```

- [ ] **Step 2: Add client functions**

Append to the `api` object in `dashboard/src/api/client.ts`:

```ts
  // Portfolio
  portfolioEquity(range: "1d" | "1w" | "1m" | "all" = "1m"): Promise<PortfolioEquityResponse> {
    return request<PortfolioEquityResponse>(`/api/portfolio/equity?range=${range}`);
  },
  portfolioKpis(): Promise<PortfolioKpis> {
    return request<PortfolioKpis>("/api/portfolio/kpis");
  },
  portfolioAllocation(): Promise<AllocationResponse> {
    return request<AllocationResponse>("/api/portfolio/allocation");
  },

  // Positions
  listOpenPositions(limit = 10): Promise<{ items: OpenPositionRow[] }> {
    return request<{ items: OpenPositionRow[] }>(`/api/positions?status=open&limit=${limit}`);
  },

  // Trades
  listRecentTrades(limit = 10): Promise<{ items: TradeRow[] }> {
    return request<{ items: TradeRow[] }>(`/api/trades?limit=${limit}`);
  },

  // Alerts
  listAlerts(limit = 10): Promise<{ items: AlertItem[] }> {
    return request<{ items: AlertItem[] }>(`/api/alerts?limit=${limit}`);
  },

  // Account snapshots
  accountSnapshotsLatest(): Promise<{ items: AccountSnapshotLatestItem[] }> {
    return request<{ items: AccountSnapshotLatestItem[] }>("/api/accounts/snapshots/latest");
  },
```

Update the imports at the top of `client.ts` to include the new types:

```ts
import type {
  // ... existing imports
  PortfolioEquityResponse,
  PortfolioKpis,
  AllocationResponse,
  OpenPositionRow,
  TradeRow,
  AlertItem,
  AccountSnapshotLatestItem,
} from "../types";
```

- [ ] **Step 3: Add hooks**

Append to `dashboard/src/api/hooks.ts`:

```ts
// ─── Portfolio ────────────────────────────────────────────────────────────────

export function usePortfolioEquity(range: "1d" | "1w" | "1m" | "all" = "1m") {
  return useQuery({
    queryKey: ["portfolio", "equity", range] as const,
    queryFn: () => api.portfolioEquity(range),
    staleTime: 30_000,
  });
}

export function usePortfolioKpis() {
  return useQuery({
    queryKey: ["portfolio", "kpis"] as const,
    queryFn: api.portfolioKpis,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function usePortfolioAllocation() {
  return useQuery({
    queryKey: ["portfolio", "allocation"] as const,
    queryFn: api.portfolioAllocation,
    staleTime: 60_000,
  });
}

// ─── Positions ────────────────────────────────────────────────────────────────

export function useOpenPositions(limit = 10) {
  return useQuery({
    queryKey: ["positions", "open", limit] as const,
    queryFn: () => api.listOpenPositions(limit),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

// ─── Trades ───────────────────────────────────────────────────────────────────

export function useRecentTrades(limit = 10) {
  return useQuery({
    queryKey: ["trades", "recent", limit] as const,
    queryFn: () => api.listRecentTrades(limit),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

export function useAlerts(limit = 10) {
  return useQuery({
    queryKey: ["alerts", limit] as const,
    queryFn: () => api.listAlerts(limit),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// ─── Account snapshots ────────────────────────────────────────────────────────

export function useAccountSnapshotsLatest() {
  return useQuery({
    queryKey: ["accounts", "snapshots", "latest"] as const,
    queryFn: api.accountSnapshotsLatest,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}
```

- [ ] **Step 4: Typecheck**

```bash
cd dashboard && npm run typecheck
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/types/index.ts dashboard/src/api/client.ts dashboard/src/api/hooks.ts
git commit -m "feat(dashboard): add types + hooks for overview redesign endpoints"
```

---

## Phase 4 — Widget rewrites (Wave 4, parallel)

All widget tasks share the same skeleton: thin title strip on top, body fills the rest edge-to-edge with `width: 100%; box-sizing: border-box;` and no inner padding. Reference visuals exist at `.superpowers/brainstorm/141338-1778709978/content/widget-set-v6.html` (kept for review; do not commit changes there).

### Task 11: `PortfolioEquityWidget`

**Files:**
- Create: `dashboard/src/components/widgets/PortfolioEquityWidget.tsx`

**Model:** sonnet

- [ ] **Step 1: Create the component**

Create `dashboard/src/components/widgets/PortfolioEquityWidget.tsx`:

```tsx
import { useState } from "react";
import { Widget } from "../Widget";
import { StackedAreaChart, type StackedAreaBand } from "../StackedAreaChart";
import { usePortfolioEquity } from "../../api/hooks";

type Range = "1d" | "1w" | "1m" | "all";

const RANGES: Range[] = ["1d", "1w", "1m", "all"];
const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#a855f7", "#ec4899"];

function formatDollar(v: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);
}

export function PortfolioEquityWidget() {
  const [range, setRange] = useState<Range>("1m");
  const { data, isLoading } = usePortfolioEquity(range);

  const bands: StackedAreaBand[] = (data?.accounts ?? []).map((a, i) => ({
    key: a.account_id,
    name: a.account_name,
    color: COLORS[i % COLORS.length],
    points: a.points,
  }));

  const latestTotal = bands.reduce((sum, b) => {
    const last = b.points[b.points.length - 1];
    return sum + (last?.value ?? 0);
  }, 0);
  const firstTotal = bands.reduce((sum, b) => sum + (b.points[0]?.value ?? 0), 0);
  const delta = latestTotal - firstTotal;
  const deltaPct = firstTotal > 0 ? (delta / firstTotal) * 100 : 0;

  return (
    <Widget title="Portfolio Equity" isLoading={isLoading} bodyClass="!p-0">
      <div className="flex items-start justify-between px-3 py-3 pb-1.5">
        <div>
          <div className="text-2xl font-bold text-white leading-tight">{formatDollar(latestTotal)}</div>
          <div className={`text-xs ${delta >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {delta >= 0 ? "+" : ""}{formatDollar(delta)} ({deltaPct.toFixed(1)}%) {range}
          </div>
        </div>
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`px-2 py-0.5 rounded text-[10px] uppercase ${
                r === range ? "bg-indigo-500 text-white" : "bg-gray-800 text-gray-400"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </div>
      {bands.length > 0 && (
        <div className="flex flex-wrap gap-3 px-3 pb-1 text-[10px] text-gray-400">
          {bands.map((b) => (
            <span key={b.key} className="inline-flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-sm" style={{ background: b.color }} />
              {b.name}
            </span>
          ))}
        </div>
      )}
      <StackedAreaChart bands={bands} height={200} />
    </Widget>
  );
}
```

Note: `Widget` currently doesn't have a `bodyClass` prop. If it doesn't, render directly as a styled div using the same look-and-feel pattern as `Widget` — read `dashboard/src/components/Widget.tsx` first and either add a `bodyClass?: string` prop with sensible default OR inline the same outer styling without `Widget`.

- [ ] **Step 2: If `Widget.tsx` needs a `bodyClass` prop, add it**

Read `dashboard/src/components/Widget.tsx`. If its body has fixed padding, modify it to accept an optional `bodyClass?: string` prop and apply it on the body div, falling back to the existing padding. This is the minimal change that lets edge-to-edge widgets coexist with padded ones.

- [ ] **Step 3: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/components/widgets/PortfolioEquityWidget.tsx dashboard/src/components/Widget.tsx
git commit -m "feat(dashboard): add PortfolioEquityWidget with stacked area chart"
```

---

### Task 12: `KpiStripWidget`

**Files:**
- Create: `dashboard/src/components/widgets/KpiStripWidget.tsx`

**Model:** sonnet

- [ ] **Step 1: Create the component**

Create `dashboard/src/components/widgets/KpiStripWidget.tsx`:

```tsx
import { Widget } from "../Widget";
import { usePortfolioKpis } from "../../api/hooks";

function formatMoney(v: number, compact = false): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 1 : 0,
  }).format(v);
}

function pct(v: number): string {
  return `${v.toFixed(1)}%`;
}

export function KpiStripWidget() {
  const { data, isLoading } = usePortfolioKpis();

  const kpis: { label: string; value: string; sub: string; pos?: boolean; neg?: boolean }[] = [
    {
      label: "Today P&L",
      value: data ? formatMoney(data.today_pnl) : "—",
      sub: data ? pct(data.today_pnl_pct) : "",
      pos: (data?.today_pnl ?? 0) > 0,
      neg: (data?.today_pnl ?? 0) < 0,
    },
    {
      label: "Total Equity",
      value: data ? formatMoney(data.total_equity, true) : "—",
      sub: "all accounts",
    },
    {
      label: "Trades Today",
      value: data ? String(data.trades_today) : "—",
      sub: data ? `${data.trades_today_wins} win · ${data.trades_today_losses} loss` : "",
    },
    {
      label: "Win Rate",
      value: data ? pct(data.win_rate) : "—",
      sub: data ? `7d avg ${pct(data.win_rate_7d_avg)}` : "",
      pos: (data?.win_rate ?? 0) >= 50,
    },
    {
      label: "Open Positions",
      value: data ? String(data.open_positions) : "—",
      sub: data ? `${data.open_positions_long} long · ${data.open_positions_short} short` : "",
    },
    {
      label: "Open Risk",
      value: data ? formatMoney(data.open_risk, true) : "—",
      sub: data ? `${pct(data.open_risk_pct_equity)} of equity` : "",
    },
    {
      label: "Deployed",
      value: data ? pct(data.deployed_pct) : "—",
      sub: data ? formatMoney(data.deployed_usd, true) : "",
    },
    {
      label: "Buying Power",
      value: data ? formatMoney(data.buying_power, true) : "—",
      sub: data ? `${pct(data.buying_power_pct)} available` : "",
    },
  ];

  return (
    <Widget title="Today's KPIs" isLoading={isLoading} bodyClass="!p-0">
      <div className="grid grid-cols-4 grid-rows-2 gap-px bg-gray-800 h-full min-h-[180px]">
        {kpis.map((k) => (
          <div key={k.label} className="bg-gray-950 px-4 py-3 flex flex-col justify-center">
            <div className="text-[10px] uppercase tracking-wide text-gray-400 font-medium">{k.label}</div>
            <div
              className={`text-2xl font-bold mt-1 leading-tight ${
                k.pos ? "text-emerald-400" : k.neg ? "text-red-400" : "text-white"
              }`}
            >
              {k.value}
            </div>
            {k.sub && <div className="text-[10px] text-gray-500 mt-0.5">{k.sub}</div>}
          </div>
        ))}
      </div>
    </Widget>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/widgets/KpiStripWidget.tsx
git commit -m "feat(dashboard): add KpiStripWidget (4x2 grid)"
```

---

### Task 13: `AlgorithmsWidget`

**Files:**
- Create: `dashboard/src/components/widgets/AlgorithmsWidget.tsx`

**Model:** sonnet

- [ ] **Step 1: Create the component**

Create `dashboard/src/components/widgets/AlgorithmsWidget.tsx`:

```tsx
import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { Sparkline } from "../Sparkline";
import { useAllInstances } from "../../api/hooks";

function formatMoney(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 0,
  }).format(v)}`;
}

export function AlgorithmsWidget() {
  const navigate = useNavigate();
  const { data: instances, isLoading } = useAllInstances();

  const rows = (instances ?? []).map((inst) => {
    const metrics = inst.lifetime_metrics ?? {};
    const lifetime = typeof metrics.total_pnl === "number" ? metrics.total_pnl : 0;
    const tradeCount = typeof metrics.trade_count === "number" ? metrics.trade_count : 0;
    const winRate = typeof metrics.win_rate === "number" ? metrics.win_rate : 0;
    return {
      id: inst.id,
      name: inst.algorithm_name ?? inst.id.slice(0, 8),
      account: inst.account_name ?? "—",
      status: inst.status,
      today: inst.today_pnl ?? 0,
      sparkline: inst.pnl_sparkline ?? [],
      trades: tradeCount,
      win_rate: winRate,
      lifetime,
    };
  });
  rows.sort((a, b) => b.lifetime - a.lifetime);

  const total = rows.reduce((s, r) => s + r.lifetime, 0);
  const running = rows.filter((r) => r.status === "running").length;
  const stopped = rows.length - running;

  return (
    <Widget title="Algorithms" isLoading={isLoading} bodyClass="!p-0">
      <div className="px-3.5 py-3 border-b border-gray-800">
        <div className={`text-xl font-bold ${total >= 0 ? "text-emerald-400" : "text-red-400"}`}>
          {formatMoney(total)}
          <span className="text-xs font-normal text-gray-400 ml-2">
            lifetime · {running} running · {stopped} stopped
          </span>
        </div>
      </div>
      <div className="grid grid-cols-[1fr_80px_44px_44px_76px] gap-2 px-3.5 py-1.5 bg-gray-950 border-b border-gray-800 text-[9px] uppercase tracking-wide text-gray-500">
        <span>Algorithm</span><span>P&L Curve</span>
        <span className="text-right">Trades</span><span className="text-right">Win %</span><span className="text-right">Lifetime</span>
      </div>
      {rows.map((r) => (
        <div
          key={r.id}
          onClick={() => navigate(`/instances/${r.id}`)}
          className="grid grid-cols-[1fr_80px_44px_44px_76px] gap-2 px-3.5 py-2.5 border-b border-gray-800 last:border-b-0 hover:bg-gray-800 cursor-pointer items-center"
        >
          <div>
            <div className="text-xs font-semibold text-gray-200 leading-tight">
              <span
                className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle ${
                  r.status === "running" ? "bg-emerald-500" : "bg-gray-500"
                }`}
              />
              {r.name}
            </div>
            <div className="text-[9px] text-gray-500 mt-0.5">
              {r.account} · today{" "}
              <span className={r.today >= 0 ? "text-emerald-400" : "text-red-400"}>
                {formatMoney(r.today)}
              </span>
            </div>
          </div>
          <Sparkline
            points={r.sparkline}
            color={r.status === "running" ? "#10b981" : "#6b7280"}
          />
          <span className="text-right text-xs tabular-nums">{r.trades}</span>
          <span className={`text-right text-xs tabular-nums ${r.win_rate >= 50 ? "text-emerald-400" : ""}`}>
            {(r.win_rate * 100).toFixed(0)}%
          </span>
          <span className={`text-right text-xs tabular-nums font-semibold ${r.lifetime >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {formatMoney(r.lifetime)}
          </span>
        </div>
      ))}
    </Widget>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/widgets/AlgorithmsWidget.tsx
git commit -m "feat(dashboard): add AlgorithmsWidget (merges active + lifetime P&L)"
```

---

### Task 14: `OpenPositionsWidget` rewrite

**Files:**
- Modify: `dashboard/src/components/widgets/OpenPositionsWidget.tsx`

**Model:** sonnet

- [ ] **Step 1: Replace the file**

Replace the contents of `dashboard/src/components/widgets/OpenPositionsWidget.tsx`:

```tsx
import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useOpenPositions } from "../../api/hooks";

function formatMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function formatPct(unrealized: number | null, cost: number): string {
  if (unrealized == null || !cost) return "";
  const pct = (unrealized / Math.abs(cost)) * 100;
  const sign = pct >= 0 ? "+" : "";
  return `(${sign}${pct.toFixed(1)}%)`;
}

export function OpenPositionsWidget() {
  const navigate = useNavigate();
  const { data, isLoading } = useOpenPositions(10);
  const items = data?.items ?? [];

  return (
    <Widget title="Open Positions" isLoading={isLoading} bodyClass="!p-0">
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm p-3.5">No open positions</p>
      ) : (
        items.map((p) => {
          const sideLabel = (p.side ?? "").toLowerCase() === "long" ? "Long" : "Short";
          const pos = (p.unrealized_pnl ?? 0) >= 0;
          return (
            <div
              key={p.id}
              onClick={() => p.instance_id && navigate(`/instances/${p.instance_id}`)}
              className="px-3.5 py-2.5 border-b border-gray-800 last:border-b-0 cursor-pointer hover:bg-gray-800"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm">
                  <strong>{p.symbol ?? "—"}</strong>
                  {p.extra_legs > 0 && <span className="text-gray-500 ml-1">+{p.extra_legs} legs</span>}
                  <span className="text-gray-400 ml-2">· {sideLabel} {p.quantity}</span>
                </span>
                <span className={`text-xs ${pos ? "text-emerald-400" : "text-red-400"}`}>
                  {formatMoney(p.unrealized_pnl)} {formatPct(p.unrealized_pnl, p.net_cost)}
                </span>
              </div>
              <div className="flex items-center justify-between mt-1 text-[10px] text-gray-500">
                <span>
                  @ {p.avg_price ?? "—"} → {p.current_price ?? "—"}
                </span>
                <span className="text-gray-500">{p.algorithm_name ?? "—"}</span>
              </div>
            </div>
          );
        })
      )}
    </Widget>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/widgets/OpenPositionsWidget.tsx
git commit -m "refactor(dashboard): rewrite OpenPositionsWidget with real position data"
```

---

### Task 15: `RecentTradesWidget` rewrite

**Files:**
- Modify: `dashboard/src/components/widgets/RecentTradesWidget.tsx`

**Model:** sonnet

- [ ] **Step 1: Replace the file**

Replace the contents of `dashboard/src/components/widgets/RecentTradesWidget.tsx`:

```tsx
import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useRecentTrades } from "../../api/hooks";

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtDollar(v: number): string {
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function RecentTradesWidget() {
  const navigate = useNavigate();
  const { data, isLoading } = useRecentTrades(10);
  const items = data?.items ?? [];

  return (
    <Widget title="Recent Trades" isLoading={isLoading} bodyClass="!p-0">
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm p-3.5">No recent trades</p>
      ) : (
        items.map((t) => {
          const isBuy = (t.side ?? "").toLowerCase() === "buy";
          return (
            <div
              key={t.id}
              onClick={() => t.instance_id && navigate(`/instances/${t.instance_id}`)}
              className="grid grid-cols-[50px_44px_80px_1fr_80px_auto] gap-2.5 items-center px-3.5 py-2 border-b border-gray-800 last:border-b-0 hover:bg-gray-800 cursor-pointer text-xs"
            >
              <span className="text-gray-500">{fmtTime(t.timestamp)}</span>
              <span className={`font-semibold ${isBuy ? "text-emerald-400" : "text-red-400"}`}>
                {isBuy ? "BUY" : "SELL"}
              </span>
              <span className="font-semibold">{t.symbol}</span>
              <span className="tabular-nums">{t.quantity} @ {t.filled_price.toLocaleString()}</span>
              <span className="text-right tabular-nums">{fmtDollar(t.notional)}</span>
              <span className="text-gray-500 truncate max-w-[110px]">{t.algorithm_name ?? "—"}</span>
            </div>
          );
        })
      )}
    </Widget>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/widgets/RecentTradesWidget.tsx
git commit -m "refactor(dashboard): rewrite RecentTradesWidget with real trade data"
```

---

### Task 16: `AccountBalancesWidget` rewrite

**Files:**
- Modify: `dashboard/src/components/widgets/AccountBalancesWidget.tsx`

**Model:** sonnet

- [ ] **Step 1: Replace the file**

```tsx
import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useAccountSnapshotsLatest } from "../../api/hooks";

function fmtMoney(v: number): string {
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function AccountBalancesWidget() {
  const navigate = useNavigate();
  const { data, isLoading } = useAccountSnapshotsLatest();
  const items = data?.items ?? [];

  return (
    <Widget title="Account Balances" isLoading={isLoading} bodyClass="!p-0">
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm p-3.5">No accounts configured</p>
      ) : (
        items.map((a) => {
          const total = a.latest.total_value;
          const positionsPct = total > 0 ? (a.latest.positions_value / total) * 100 : 0;
          const cashPct = total > 0 ? (a.latest.cash / total) * 100 : 0;
          const dayPct = a.day_pct;
          return (
            <div
              key={a.account_id}
              onClick={() => navigate(`/accounts/${a.account_id}`)}
              className="px-3.5 py-3 border-b border-gray-800 last:border-b-0 cursor-pointer hover:bg-gray-800"
            >
              <div className="flex items-baseline justify-between mb-1.5">
                <strong className="text-xs">{a.account_name}</strong>
                <span className="text-sm font-semibold">
                  {fmtMoney(total)}{" "}
                  {dayPct !== null && (
                    <span className={`text-[11px] ${dayPct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {dayPct >= 0 ? "+" : ""}{dayPct.toFixed(1)}%
                    </span>
                  )}
                </span>
              </div>
              <div className="flex h-3 rounded-sm overflow-hidden mb-1">
                <div style={{ width: `${positionsPct}%`, background: "#3b82f6" }} />
                <div style={{ width: `${cashPct}%`, background: "#10b981" }} />
              </div>
              <div className="text-[10px] text-gray-500">
                {positionsPct.toFixed(0)}% positions ({fmtMoney(a.latest.positions_value)})
                {" · "}
                {cashPct.toFixed(0)}% cash ({fmtMoney(a.latest.cash)})
              </div>
            </div>
          );
        })
      )}
    </Widget>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/widgets/AccountBalancesWidget.tsx
git commit -m "refactor(dashboard): rewrite AccountBalancesWidget with cash/positions split"
```

---

### Task 17: `AssetAllocationWidget`

**Files:**
- Create: `dashboard/src/components/widgets/AssetAllocationWidget.tsx`

**Model:** sonnet

- [ ] **Step 1: Create the component**

```tsx
import { useState } from "react";
import { Widget } from "../Widget";
import { Donut } from "../Donut";
import { usePortfolioAllocation } from "../../api/hooks";

function fmtCompact(v: number): string {
  return `$${(v / 1000).toFixed(1)}k`;
}

export function AssetAllocationWidget() {
  const { data, isLoading } = usePortfolioAllocation();
  const [view, setView] = useState<"class" | "symbol">("class");
  const segments = (view === "class" ? data?.by_class : data?.by_symbol) ?? [];

  return (
    <Widget title="Asset Allocation" isLoading={isLoading} bodyClass="!p-0">
      <div className="flex gap-1 px-3.5 pt-2 pb-2">
        {(["class", "symbol"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`px-2.5 py-0.5 rounded text-[10px] capitalize ${
              v === view ? "bg-indigo-500 text-white" : "bg-gray-800 text-gray-400"
            }`}
          >
            By {v === "class" ? "Class" : "Symbol"}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-4 px-4 pb-4">
        <Donut segments={segments} size={130} />
        <div className="flex-1 text-[11px] leading-relaxed">
          {segments.map((s) => (
            <div key={s.key}>
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm mr-2 align-middle"
                style={{ background: s.color }}
              />
              <strong>{s.label}</strong> {s.percent}% · {fmtCompact(s.value_usd)}
            </div>
          ))}
        </div>
      </div>
    </Widget>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/widgets/AssetAllocationWidget.tsx
git commit -m "feat(dashboard): add AssetAllocationWidget with By Class/By Symbol toggle"
```

---

### Task 18: `AlertsWidget`

**Files:**
- Create: `dashboard/src/components/widgets/AlertsWidget.tsx`

**Model:** sonnet

- [ ] **Step 1: Create the component**

```tsx
import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useAlerts } from "../../api/hooks";

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function AlertsWidget() {
  const navigate = useNavigate();
  const { data, isLoading } = useAlerts(10);
  const items = data?.items ?? [];

  return (
    <Widget title="Alerts" isLoading={isLoading} bodyClass="!p-0">
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm p-3.5">No alerts</p>
      ) : (
        items.map((a) => {
          const pillBg =
            a.pill_color === "err"
              ? "bg-red-950 text-red-300"
              : a.pill_color === "backtest"
                ? "bg-blue-950 text-blue-300"
                : "bg-yellow-950 text-yellow-400";
          return (
            <div
              key={`${a.kind}-${a.id}`}
              onClick={() => a.link_path && navigate(a.link_path)}
              className="flex gap-2.5 items-center px-3.5 py-2.5 border-b border-gray-800 last:border-b-0 cursor-pointer hover:bg-gray-800"
            >
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-semibold min-w-[42px] text-center ${pillBg}`}
              >
                {a.pill}
              </span>
              <div className="text-xs">
                <strong>{a.label}</strong>
                <div className="text-[10px] text-gray-500">
                  {a.source_name} · {fmtTime(a.timestamp)}
                </div>
              </div>
            </div>
          );
        })
      )}
    </Widget>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd dashboard && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/widgets/AlertsWidget.tsx
git commit -m "feat(dashboard): add AlertsWidget (merges system events + backtest alerts)"
```

---

## Phase 5 — Wiring (Wave 5, sequential)

### Task 19: Widget registry + store defaults + stale-ID handling

**Files:**
- Modify: `dashboard/src/components/widgets/index.ts`
- Modify: `dashboard/src/stores/dashboard.ts`
- Delete: `dashboard/src/components/widgets/PortfolioValueWidget.tsx`
- Delete: `dashboard/src/components/widgets/ActiveAlgorithmsWidget.tsx`
- Delete: `dashboard/src/components/widgets/TodaysPnLWidget.tsx`
- Delete: `dashboard/src/components/widgets/WorkerHealthWidget.tsx`
- Delete: `dashboard/src/components/widgets/SystemEventsWidget.tsx`
- Delete: `dashboard/src/components/widgets/BacktestAlertsWidget.tsx`

**Model:** opus

- [ ] **Step 1: Replace `widgets/index.ts`**

```tsx
import React from "react";
import { PortfolioEquityWidget } from "./PortfolioEquityWidget";
import { KpiStripWidget } from "./KpiStripWidget";
import { AlgorithmsWidget } from "./AlgorithmsWidget";
import { OpenPositionsWidget } from "./OpenPositionsWidget";
import { RecentTradesWidget } from "./RecentTradesWidget";
import { AccountBalancesWidget } from "./AccountBalancesWidget";
import { AssetAllocationWidget } from "./AssetAllocationWidget";
import { AlertsWidget } from "./AlertsWidget";

export const WIDGET_REGISTRY: Record<string, React.ComponentType> = {
  "portfolio-equity": PortfolioEquityWidget,
  "kpi-strip": KpiStripWidget,
  "algorithms": AlgorithmsWidget,
  "open-positions": OpenPositionsWidget,
  "recent-trades": RecentTradesWidget,
  "account-balances": AccountBalancesWidget,
  "asset-allocation": AssetAllocationWidget,
  "alerts": AlertsWidget,
};

export const WIDGET_TITLES: Record<string, string> = {
  "portfolio-equity": "Portfolio Equity",
  "kpi-strip": "Today's KPIs",
  "algorithms": "Algorithms",
  "open-positions": "Open Positions",
  "recent-trades": "Recent Trades",
  "account-balances": "Account Balances",
  "asset-allocation": "Asset Allocation",
  "alerts": "Alerts",
};
```

- [ ] **Step 2: Update `stores/dashboard.ts` defaults + stale-ID filtering**

Replace the entire file:

```tsx
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { WIDGET_REGISTRY } from "../components/widgets";

export interface WidgetConfig {
  id: string;
  visible: boolean;
  order: number;
  colSpan: 1 | 2;
}

interface DashboardState {
  widgets: WidgetConfig[];
  reorder: (fromIndex: number, toIndex: number) => void;
  toggleWidget: (id: string) => void;
  resetLayout: () => void;
}

const DEFAULT_WIDGETS: WidgetConfig[] = [
  { id: "portfolio-equity", visible: true, order: 0, colSpan: 2 },
  { id: "kpi-strip", visible: true, order: 1, colSpan: 2 },
  { id: "algorithms", visible: true, order: 2, colSpan: 2 },
  { id: "open-positions", visible: true, order: 3, colSpan: 1 },
  { id: "recent-trades", visible: true, order: 4, colSpan: 1 },
  { id: "account-balances", visible: true, order: 5, colSpan: 1 },
  { id: "asset-allocation", visible: true, order: 6, colSpan: 1 },
  { id: "alerts", visible: true, order: 7, colSpan: 1 },
];

function reconcileLayout(persisted: WidgetConfig[]): WidgetConfig[] {
  const validIds = new Set(Object.keys(WIDGET_REGISTRY));
  const kept = persisted.filter((w) => validIds.has(w.id));
  const persistedIds = new Set(kept.map((w) => w.id));
  const additions = DEFAULT_WIDGETS.filter((w) => !persistedIds.has(w.id)).map(
    (w, i) => ({ ...w, order: kept.length + i })
  );
  return [...kept, ...additions].map((w, i) => ({ ...w, order: i }));
}

export const useDashboardStore = create<DashboardState>()(
  persist(
    (set) => ({
      widgets: DEFAULT_WIDGETS,
      reorder: (fromIndex, toIndex) =>
        set((state) => {
          const widgets = [...state.widgets];
          const [moved] = widgets.splice(fromIndex, 1);
          widgets.splice(toIndex, 0, moved);
          return { widgets: widgets.map((w, i) => ({ ...w, order: i })) };
        }),
      toggleWidget: (id) =>
        set((state) => ({
          widgets: state.widgets.map((w) =>
            w.id === id ? { ...w, visible: !w.visible } : w
          ),
        })),
      resetLayout: () => set({ widgets: DEFAULT_WIDGETS }),
    }),
    {
      name: "quilt-dashboard-layout",
      storage: createJSONStorage(() => localStorage),
      merge: (persisted, current) => {
        if (!persisted || typeof persisted !== "object") return current;
        const ps = persisted as Partial<DashboardState>;
        const widgets = ps.widgets ? reconcileLayout(ps.widgets) : DEFAULT_WIDGETS;
        return { ...current, widgets };
      },
    }
  )
);
```

- [ ] **Step 3: Delete the six removed widget files**

```bash
git rm dashboard/src/components/widgets/PortfolioValueWidget.tsx
git rm dashboard/src/components/widgets/ActiveAlgorithmsWidget.tsx
git rm dashboard/src/components/widgets/TodaysPnLWidget.tsx
git rm dashboard/src/components/widgets/WorkerHealthWidget.tsx
git rm dashboard/src/components/widgets/SystemEventsWidget.tsx
git rm dashboard/src/components/widgets/BacktestAlertsWidget.tsx
```

- [ ] **Step 4: Typecheck and build**

```bash
cd dashboard && npm run typecheck && npm run build
```
Expected: both succeed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/widgets/index.ts dashboard/src/stores/dashboard.ts
git commit -m "refactor(dashboard): wire new widget set + reconcile persisted layouts"
```

---

### Task 20: Seed data for the new widgets

**Files:**
- Modify: `scripts/seed_data.py`

**Model:** sonnet

- [ ] **Step 1: Read the existing seed script**

```bash
cat scripts/seed_data.py
```

Understand what it currently seeds. Identify gaps for the new widgets: per-account snapshot histories (need ~5 points each over a few days), open `Position` rows (need ~3), `TradeLog` rows (need ~10), and `algorithm_runs.equity_curve` populated for each running instance.

- [ ] **Step 2: Extend `seed_data.py`**

Add the missing data. Each running instance should have:
- An `AlgorithmRun` row with a non-trivial `equity_curve` (~20 points)
- 2–3 `TradeLog` rows from the last day
- 1 open `Position` row with realistic `legs` JSON

Each account should have ~5 `AccountSnapshot` rows spanning 7 days with slight upward drift.

The exact shape depends on the existing script; replicate the patterns it already uses. Run the seed script after editing:

```bash
python scripts/seed_data.py
```
Expected: success, no errors.

- [ ] **Step 3: Commit**

```bash
git add scripts/seed_data.py
git commit -m "chore(seed): seed snapshots, positions, trades, and equity curves for overview"
```

---

### Task 21: Manual verification

**Files:** none (runtime check)

**Model:** sonnet

- [ ] **Step 1: Start the coordinator**

```bash
uvicorn coordinator.main:create_app --factory --reload --port 8000
```

- [ ] **Step 2: Start the dashboard**

```bash
cd dashboard && npm run dev
```

- [ ] **Step 3: Open the Overview page**

Visit http://localhost:5173. Verify each widget:

- [ ] **Portfolio Equity** renders the stacked area chart with one band per account, range pills work
- [ ] **Today's KPIs** shows 8 KPIs in a 4×2 grid
- [ ] **Algorithms** lists running instances with sparklines, trade counts, win %, lifetime $
- [ ] **Open Positions** shows symbol/side/qty rows (no GUIDs)
- [ ] **Recent Trades** shows BUY/SELL rows with symbol, qty@price, $, algorithm name
- [ ] **Account Balances** shows total + day Δ% + stacked positions/cash bar
- [ ] **Asset Allocation** donut renders; toggle between By Class / By Symbol
- [ ] **Alerts** lists warnings/errors and backtest divergences with resolved names

- [ ] **Step 4: Click-through smoke test**

Click one row in each list widget. Verify navigation:
- Algorithm row → `/instances/:id` page renders
- Position row → `/instances/:id` page renders
- Trade row → `/instances/:id` page renders
- Account row → `/accounts/:id` page renders
- Alert with `link_path` → corresponding detail page renders

- [ ] **Step 5: Customize modal**

Open the `Customize` modal. Verify all 8 widgets are listed and toggling them shows/hides them. Drag-to-reorder still works.

- [ ] **Step 6: Persisted layout reconciliation**

In the browser dev tools, set `localStorage["quilt-dashboard-layout"]` to a value containing a removed widget ID:

```js
localStorage.setItem("quilt-dashboard-layout", JSON.stringify({
  state: { widgets: [{ id: "worker-health", visible: true, order: 0, colSpan: 1 }] },
  version: 0,
}));
```

Reload. Confirm: no console errors. The page shows the new widgets (stale `worker-health` ID was filtered out, new defaults appended).

- [ ] **Step 7: Commit any small fixes discovered**

```bash
git status
# review, then:
git commit -am "fix(dashboard): <describe>" # if anything required adjustment
```

---

## Self-Review Notes

Spec coverage checked against the design doc. Each spec section maps to at least one task:

- §2.1 PortfolioEquityWidget → Tasks 1, 9, 10, 11
- §2.2 KpiStripWidget → Tasks 1, 10, 12
- §2.3 AlgorithmsWidget → Tasks 6, 7, 10, 13
- §2.4 OpenPositionsWidget → Tasks 2, 10, 14
- §2.5 RecentTradesWidget → Tasks 3, 10, 15
- §2.6 AccountBalancesWidget → Tasks 5, 10, 16
- §2.7 AssetAllocationWidget → Tasks 1, 8, 10, 17
- §2.8 AlertsWidget → Tasks 4, 10, 18
- §3 Backend endpoints → Tasks 1–6
- §4.1 New widget components → Tasks 11–18
- §4.2 Shared chart components → Tasks 7–9
- §4.3 New hooks → Task 10
- §4.4 Type additions → Task 10
- §4.5 Dashboard store changes → Task 19
- §5 Click-through routing → wired in Tasks 13–18, verified in Task 21
- §6 Testing → Tasks 1–6 (backend tests); §6.3 manual verification → Task 21
- §7 Migration → Task 19 (reconcile) + Task 20 (seed) + Task 21 (verify)
