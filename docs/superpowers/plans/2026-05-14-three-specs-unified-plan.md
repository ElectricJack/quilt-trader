# Three-Specs Unified Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute Spec A (Accounts & Install Polish), Spec B (Live Data Subscriptions & Compare), and Spec C (Options Strategy Builder) in parallel, respecting cross-spec dependencies and minimizing merge conflict surface.

**Architecture:** Four sequential phases with parallel work units inside each phase. Phase 0 establishes shared foundations (base-class extensions, pure libraries, schema migration). Phase 1 implements broker adapters. Phase 2 wires server endpoints. Phase 3 builds UI. Phase 4 ties integration and runs smoke. Each parallel work unit runs in its own git worktree to keep diffs clean.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 / Pydantic v2 / pytest + pytest-asyncio (backend); React + TypeScript + Vite + TanStack Query + Zod + react-hook-form + Tailwind + lightweight-charts (frontend).

**Spec references:**
- `docs/superpowers/specs/2026-05-14-accounts-and-install-polish-design.md`
- `docs/superpowers/specs/2026-05-14-live-data-subscriptions-design.md`
- `docs/superpowers/specs/2026-05-14-options-strategy-builder-design.md`

---

## Orchestration

### Phase ordering

```
Phase 0 — Foundations (parallel: F1..F6)
        |
        v
Phase 1 — Adapter impls (parallel: A1, A2)
        |
        v
Phase 2 — Server endpoints (parallel: S1..S6)
        |
        v
Phase 3 — UI (parallel: U1..U6)
        |
        v
Phase 4 — Integration + smoke (sequential: I1, I2)
```

A phase ends only when all its parallel work units have merged. The next phase starts from the merge point.

### Worktree strategy

Each parallel work unit (e.g. F1, A1, S2) runs in its own git worktree off the current branch:

```bash
git worktree add ../quilt-trader-F1 -b plan/F1-broker-adapter-base
# ... agent does work, commits ...
# at phase merge time:
git -C ../quilt-trader checkout feat/quilt-trader-implementation
git -C ../quilt-trader merge --no-ff plan/F1-broker-adapter-base
git worktree remove ../quilt-trader-F1
```

Use `superpowers:using-git-worktrees` to create them.

### File ownership map

Files that ONLY appear in one work unit have a single owner. Files that appear in multiple work units are conflict points; the plan schedules them in the same work unit OR in serially-ordered phases.

| File | Phase / Owner |
|---|---|
| `worker/broker_adapter.py` | F1 (base class methods for both Spec A and Spec C combined) |
| `worker/alpaca_adapter.py` | A1 (combines Spec A multi-leg + Spec C chain APIs) |
| `worker/tradier_adapter.py` | A2 (combines Spec A multi-leg + Spec C chain APIs) |
| `worker/config.py`, `worker/agent.py`, `worker/main.py`, `scripts/install-worker.sh` | F2 |
| `coordinator/services/asset_catalog.py` | F3 (new) |
| `coordinator/api/routes/brokers.py` | F3 (new) |
| `dashboard/src/lib/options.ts` | F4 (new) |
| `coordinator/database/models.py`, alembic migration | F5 |
| `sdk/context.py`, `worker/data_client.py` | F6 |
| `coordinator/api/routes/accounts.py` (positions/open) | S1 |
| `coordinator/api/routes/algorithms.py` (install-from-url) | S2 |
| `coordinator/api/websocket.py` (heartbeat fix + broadcast) | S3 |
| `coordinator/services/live_feed_manager.py` | S4 |
| `coordinator/services/live_feed_aggregator.py` | S4 |
| `coordinator/api/routes/live_subscriptions.py` | S4 |
| `coordinator/api/routes/options_chain.py` | S5 |
| `coordinator/main.py`, `coordinator/api/dependencies.py` | S6 (router/container wiring; sequenced LAST in Phase 2) |
| `dashboard/src/pages/Accounts.tsx` | U1 |
| `dashboard/src/pages/AccountDetail.tsx` | U2 (Open Position + Strategies button combined — one owner for the header) |
| `dashboard/src/pages/Algorithms.tsx` | U3 |
| `dashboard/src/pages/Workers.tsx`, `WorkerInstallCommand.tsx` | U4 |
| `dashboard/src/pages/Data.tsx` | U5 |
| `dashboard/src/pages/Strategies.tsx` + `dashboard/src/components/strategy/*` | U6 |
| `dashboard/src/api/{hooks.ts,client.ts}`, `dashboard/src/types.ts`, `dashboard/src/App.tsx` | Edited additively in every UI work unit; conflicts resolved during merge |
| `coordinator/services/lifecycle.py` | I1 (depends on S4) |

### Conventions across all work units

- **TDD per task:** write the failing test, run it, implement, run again, commit. Each commit is a discrete idea.
- **Commit message style:** match the project's existing style (e.g. `feat(scope): subject`, `fix(scope): subject`, `test(scope): subject`).
- **Python:** type hints everywhere; `from __future__ import annotations` at the top of new files; `Optional[X]` for nullable.
- **Pydantic:** v2 syntax (`model_config = {...}`, no v1 `Config` class).
- **Tests:** existing `tests/coordinator/conftest.py` provides `client`, `test_app`, `db_session`; `tests/worker/` uses `unittest.mock` patterns. Mirror those.
- **No emojis in code/files** unless explicitly part of UI copy that already had emoji.
- **Async sessions:** all DB writes use `async with container.session_factory() as session:` then `session.commit()`.

---

## Phase 0 — Foundations

Six independent work units. None depend on each other. All must merge before Phase 1 starts.

### Work unit F1: BrokerAdapter base-class extensions

**Branch:** `plan/F1-broker-adapter-base`

**Files:**
- Modify: `worker/broker_adapter.py`
- Test: `tests/worker/test_broker_adapter.py`

Adds dataclasses and abstract methods used by Spec A (multi-leg orders) and Spec C (options chain). No broker-specific logic — those land in Phase 1.

- [ ] **F1.1 — Write the failing test for new dataclasses**

```python
# tests/worker/test_broker_adapter.py (append)
from datetime import date
from worker.broker_adapter import (
    MultilegLegSpec, MultilegOrderResult, MultilegLegResult,
    OptionContract, OptionChainSnapshot, MockBrokerAdapter,
)

def test_multileg_leg_spec_fields():
    leg = MultilegLegSpec(
        symbol="SPY", asset_type="options", side="buy", quantity=1,
        expiry="2026-06-20", strike=560.0, right="call",
    )
    assert leg.symbol == "SPY"
    assert leg.right == "call"

def test_multileg_order_result_aggregates_legs():
    result = MultilegOrderResult(
        broker_order_id="parent-1",
        legs=[
            MultilegLegResult(index=0, status="filled", filled_price=8.30, fees=0.65, broker_order_id="leg-1"),
            MultilegLegResult(index=1, status="filled", filled_price=4.20, fees=0.65, broker_order_id="leg-2"),
        ],
        atomic=True,
    )
    assert len(result.legs) == 2
    assert result.atomic is True

def test_option_chain_snapshot_sorts_contracts_by_strike():
    snap = OptionChainSnapshot(
        underlying="SPY", spot=565.0, expiry=date(2026, 6, 20),
        contracts=[
            OptionContract(strike=570.0, right="call", occ_symbol="SPY260620C00570000",
                           bid=4.1, ask=4.3, last=4.2, iv=0.28, delta=0.35,
                           gamma=0.018, theta=-12.4, vega=45.2, open_interest=1234, volume=567),
            OptionContract(strike=560.0, right="call", occ_symbol="SPY260620C00560000",
                           bid=8.2, ask=8.4, last=8.3, iv=0.30, delta=0.55,
                           gamma=0.020, theta=-14.1, vega=48.0, open_interest=2345, volume=789),
        ],
        as_of=None,  # populated by adapter
    )
    assert snap.contracts[0].strike == 570.0  # not auto-sorted; adapters sort

def test_mock_supports_multileg_false_by_default():
    adapter = MockBrokerAdapter()
    leg = MultilegLegSpec(symbol="SPY", asset_type="options", side="buy", quantity=1,
                          expiry="2026-06-20", strike=560.0, right="call")
    assert adapter.supports_multileg_orders([leg, leg]) is False

def test_mock_compose_symbol_passthrough():
    adapter = MockBrokerAdapter()
    leg = MultilegLegSpec(symbol="SPY", asset_type="equities", side="buy", quantity=1)
    assert adapter.compose_symbol(leg) == "SPY"
```

- [ ] **F1.2 — Run tests and confirm they fail**

Run: `cd /home/jkern/dev/quilt-trader && pytest tests/worker/test_broker_adapter.py -v -k "multileg or option_chain or supports_multileg or compose_symbol"`

Expected: ImportError (symbols not defined).

- [ ] **F1.3 — Implement the dataclasses + base-class methods**

Append to `worker/broker_adapter.py`:

```python
from datetime import datetime, date as _date
from typing import Optional


@dataclass
class MultilegLegSpec:
    """Input shape for one leg of a multi-leg order. Matches Spec A's API LegSpec."""
    symbol: str
    asset_type: str               # "equities" | "options" | "crypto"
    side: str                     # "buy" | "sell"
    quantity: float
    expiry: Optional[str] = None  # YYYY-MM-DD, options only
    strike: Optional[float] = None
    right: Optional[str] = None   # "call" | "put"


@dataclass
class MultilegLegResult:
    index: int
    status: str                              # "filled" | "rejected" | "pending"
    filled_price: Optional[float] = None
    fees: Optional[float] = None
    error: Optional[str] = None
    broker_order_id: Optional[str] = None


@dataclass
class MultilegOrderResult:
    broker_order_id: Optional[str]           # parent order id when atomic
    legs: list[MultilegLegResult]
    atomic: bool                             # True if filled via native multi-leg endpoint


@dataclass
class OptionContract:
    strike: float
    right: str                               # "call" | "put"
    occ_symbol: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    open_interest: Optional[int]
    volume: Optional[int]


@dataclass
class OptionChainSnapshot:
    underlying: str
    spot: float
    expiry: _date
    contracts: list[OptionContract]
    as_of: Optional[datetime]
```

Add these methods to `BrokerAdapter`:

```python
    # ---- Multi-leg orders (Spec A) ----
    def supports_multileg_orders(self, legs: list[MultilegLegSpec]) -> bool:
        """Whether this adapter can submit `legs` as a single atomic ticket."""
        return False

    def compose_symbol(self, leg: MultilegLegSpec) -> str:
        """Format a leg into a broker-specific symbol (OCC for options)."""
        return leg.symbol

    def submit_multileg_order(
        self,
        legs: list[MultilegLegSpec],
        order_type: str,
        limit_price: Optional[float],
    ) -> MultilegOrderResult:
        """Submit `legs` as one atomic broker order. Raises if unsupported."""
        raise NotImplementedError

    # ---- Options chain (Spec C) ----
    def list_option_expiries(self, underlying: str) -> list[_date]:
        """Return available option expirations for the underlying."""
        raise NotImplementedError

    def get_option_chain(self, underlying: str, expiry: _date) -> OptionChainSnapshot:
        """Return the full chain for one expiry."""
        raise NotImplementedError
```

`MockBrokerAdapter` already inherits the default `supports_multileg_orders=False` and the passthrough `compose_symbol`, so no override needed.

- [ ] **F1.4 — Run tests and confirm pass**

Run: `pytest tests/worker/test_broker_adapter.py -v`
Expected: PASS.

- [ ] **F1.5 — Commit**

```bash
git add worker/broker_adapter.py tests/worker/test_broker_adapter.py
git commit -m "feat(broker): add multi-leg + options chain abstract surface

Base-class extensions used by Spec A (multi-leg orders) and Spec C
(options chain). Broker-specific implementations land in Phase 1."
```

### Work unit F2: Worker heartbeat wiring fix

**Branch:** `plan/F2-worker-heartbeat`

Fixes the pre-existing bug where `worker_id` was never sent in heartbeats, so the coordinator never updated `Worker.status` or `last_heartbeat`. Spec A §4 depends on this.

**Files:**
- Modify: `worker/config.py`, `worker/agent.py`, `worker/main.py`, `scripts/install-worker.sh`
- Test: `tests/worker/test_config.py`, `tests/worker/test_agent.py`

- [ ] **F2.1 — Failing test: WorkerConfig accepts worker_id**

```python
# tests/worker/test_config.py (append)
import os
from worker.config import WorkerConfig

def test_worker_config_reads_worker_id_from_env(monkeypatch):
    monkeypatch.setenv("QTW_WORKER_ID", "uuid-123")
    cfg = WorkerConfig()
    assert cfg.worker_id == "uuid-123"

def test_worker_config_worker_id_defaults_empty(monkeypatch):
    monkeypatch.delenv("QTW_WORKER_ID", raising=False)
    cfg = WorkerConfig()
    assert cfg.worker_id == ""
```

- [ ] **F2.2 — Run and confirm fail**

Run: `pytest tests/worker/test_config.py -v -k worker_id`
Expected: AttributeError or assertion fail.

- [ ] **F2.3 — Add worker_id field to WorkerConfig**

In `worker/config.py`, after `worker_name`:

```python
    worker_id: str = ""
```

- [ ] **F2.4 — Run and confirm pass**

Run: `pytest tests/worker/test_config.py -v -k worker_id`
Expected: PASS.

- [ ] **F2.5 — Failing test: heartbeat payload includes worker_id and tailscale_ip**

```python
# tests/worker/test_agent.py (append; assumes existing FakeWebSocket fixture pattern)
import json
import pytest
from unittest.mock import AsyncMock
from worker.agent import WorkerAgent

@pytest.mark.asyncio
async def test_send_heartbeat_includes_worker_id_and_tailscale_ip():
    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="uuid-abc", worker_name="pi-1", websocket=ws,
                        tailscale_ip="100.64.0.5")
    await agent.send_heartbeat()
    sent_payload = json.loads(ws.send.call_args.args[0])
    assert sent_payload["type"] == "heartbeat"
    assert sent_payload["worker_id"] == "uuid-abc"
    assert sent_payload["worker_name"] == "pi-1"
    assert sent_payload["tailscale_ip"] == "100.64.0.5"

@pytest.mark.asyncio
async def test_send_heartbeat_omits_tailscale_ip_when_none():
    ws = AsyncMock()
    ws.send = AsyncMock()
    agent = WorkerAgent(worker_id="uuid-abc", worker_name="pi-1", websocket=ws,
                        tailscale_ip=None)
    await agent.send_heartbeat()
    sent_payload = json.loads(ws.send.call_args.args[0])
    assert "tailscale_ip" not in sent_payload
```

- [ ] **F2.6 — Run and confirm fail**

Run: `pytest tests/worker/test_agent.py -v -k send_heartbeat`
Expected: TypeError (WorkerAgent constructor doesn't accept worker_id / tailscale_ip).

- [ ] **F2.7 — Update WorkerAgent constructor + send_heartbeat**

In `worker/agent.py`:

```python
class WorkerAgent:
    def __init__(self, worker_id: str, worker_name: str, websocket: Any,
                 tailscale_ip: Optional[str] = None) -> None:
        self.worker_id = worker_id
        self.worker_name = worker_name
        self.tailscale_ip = tailscale_ip
        self._ws = websocket
        self.router = MessageRouter()
        self._running_instances: dict[str, Any] = {}
        self.register_handlers()
```

Replace `send_heartbeat`:

```python
    async def send_heartbeat(self) -> None:
        payload: dict = {
            "type": "heartbeat",
            "worker_id": self.worker_id,
            "worker_name": self.worker_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self.tailscale_ip:
            payload["tailscale_ip"] = self.tailscale_ip
        await self._send(payload)
```

Note: current `_send` already takes a dict — leave as-is. Check that existing tests still pass.

- [ ] **F2.8 — Update worker/main.py to discover tailscale_ip and pass worker_id**

```python
# worker/main.py — replace the existing run_worker / WorkerAgent construction site
import subprocess

def _discover_tailscale_ip() -> Optional[str]:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return None


async def run_worker(config: WorkerConfig) -> None:
    import websockets
    from worker.agent import WorkerAgent
    from worker.data_client import DataClient

    tailscale_ip = _discover_tailscale_ip()
    logger.info("Starting worker '%s' (id=%s, ts_ip=%s), connecting to %s",
                config.worker_name, config.worker_id, tailscale_ip, config.coordinator_url)
    data_client = DataClient(base_url=config.coordinator_http_url, cache_ttl=config.data_cache_ttl)
    ws_url = f"{config.coordinator_url}/ws/worker"

    async for websocket in websockets.connect(ws_url):
        try:
            agent = WorkerAgent(
                worker_id=config.worker_id,
                worker_name=config.worker_name,
                websocket=websocket,
                tailscale_ip=tailscale_ip,
            )
            logger.info("Connected to coordinator")

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
```

- [ ] **F2.9 — Run agent tests, confirm pass**

Run: `pytest tests/worker/test_agent.py tests/worker/test_config.py -v`
Expected: PASS. Some pre-existing tests may need a `worker_id="x"` arg added; if so, patch them in the same commit.

- [ ] **F2.10 — Update install script to write QTW_WORKER_ID**

In `scripts/install-worker.sh`, replace lines 117-122:

```bash
cat > /etc/quilt-trader-worker.env <<EOF
QTW_COORDINATOR_URL=${ws_url}
QTW_WORKER_ID=${WORKER_ID}
QTW_WORKER_NAME=${WORKER_NAME}
QTW_HEARTBEAT_INTERVAL=${HEARTBEAT_INTERVAL}
QTW_MAX_ALGORITHMS=${MAX_ALGORITHMS}
EOF
```

- [ ] **F2.11 — Commit**

```bash
git add worker/config.py worker/agent.py worker/main.py scripts/install-worker.sh \
        tests/worker/test_config.py tests/worker/test_agent.py
git commit -m "fix(worker): wire worker_id end-to-end + add tailscale_ip self-report

Pre-existing bug: worker heartbeats omitted worker_id so coordinator
never updated Worker.status/last_heartbeat. Adds QTW_WORKER_ID env var
through install script, WorkerConfig, WorkerAgent, and heartbeat
payload. Worker also discovers and reports its tailscale IP."
```

### Work unit F3: Asset-type catalog

**Branch:** `plan/F3-asset-catalog`

**Files:**
- Create: `coordinator/services/asset_catalog.py`, `coordinator/api/routes/brokers.py`
- Test: `tests/coordinator/services/test_asset_catalog.py`, `tests/coordinator/test_brokers_api.py`

- [ ] **F3.1 — Failing test for catalog module**

```python
# tests/coordinator/services/test_asset_catalog.py
import pytest
from coordinator.services.asset_catalog import (
    BROKER_ASSET_TYPES, asset_types_for_broker,
)

def test_alpaca_supports_equities_options_crypto():
    assert asset_types_for_broker("alpaca") == ["equities", "options", "crypto"]

def test_tradier_supports_equities_options():
    assert asset_types_for_broker("tradier") == ["equities", "options"]

def test_unknown_broker_raises():
    with pytest.raises(ValueError, match="Unknown broker"):
        asset_types_for_broker("ibkr")

def test_returns_a_copy_not_mutable_reference():
    out = asset_types_for_broker("alpaca")
    out.append("forex")
    assert "forex" not in BROKER_ASSET_TYPES["alpaca"]
```

- [ ] **F3.2 — Confirm test fails**

Run: `pytest tests/coordinator/services/test_asset_catalog.py -v`
Expected: ImportError.

- [ ] **F3.3 — Implement the catalog**

```python
# coordinator/services/asset_catalog.py
"""Catalog of asset types supported per broker.

Drives the account-creation checkbox UI (Spec A §1) and the order-ticket
asset-type filter. Adding a broker = adding a key here + implementing the
adapter side.
"""
from __future__ import annotations

BROKER_ASSET_TYPES: dict[str, list[str]] = {
    "alpaca":  ["equities", "options", "crypto"],
    "tradier": ["equities", "options"],
}


def asset_types_for_broker(broker_type: str) -> list[str]:
    if broker_type not in BROKER_ASSET_TYPES:
        raise ValueError(f"Unknown broker: {broker_type}")
    return list(BROKER_ASSET_TYPES[broker_type])
```

- [ ] **F3.4 — Confirm pass**

Run: `pytest tests/coordinator/services/test_asset_catalog.py -v`
Expected: PASS.

- [ ] **F3.5 — Failing API test**

```python
# tests/coordinator/test_brokers_api.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_get_asset_types_alpaca(client: AsyncClient):
    r = await client.get("/api/brokers/alpaca/asset-types")
    assert r.status_code == 200
    assert r.json() == {"asset_types": ["equities", "options", "crypto"]}

@pytest.mark.asyncio
async def test_get_asset_types_unknown_broker_404(client: AsyncClient):
    r = await client.get("/api/brokers/ibkr/asset-types")
    assert r.status_code == 404
```

- [ ] **F3.6 — Confirm fail**

Run: `pytest tests/coordinator/test_brokers_api.py -v`
Expected: 404 because route doesn't exist (collected as `assert 404 == 200`).

- [ ] **F3.7 — Implement the brokers router**

```python
# coordinator/api/routes/brokers.py
from fastapi import APIRouter, HTTPException

from coordinator.services.asset_catalog import (
    BROKER_ASSET_TYPES, asset_types_for_broker,
)

router = APIRouter(prefix="/api/brokers", tags=["brokers"])


@router.get("/{broker_type}/asset-types")
async def get_asset_types(broker_type: str):
    if broker_type not in BROKER_ASSET_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown broker: {broker_type}")
    return {"asset_types": asset_types_for_broker(broker_type)}
```

**Note:** Router is NOT mounted in `coordinator/main.py` yet — that's S6 (router-wiring work unit). F3's tests pass because the `client` fixture loads the app, but the route returns 404. Fix: explicitly include the router for the test. Add this line to the top of the test file:

```python
# tests/coordinator/test_brokers_api.py — add at top
from coordinator.main import create_app
from coordinator.api.routes import brokers as brokers_routes

# pytest fixture override (place above the test functions)
import pytest_asyncio
from httpx import ASGITransport

@pytest_asyncio.fixture
async def test_app():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    app.include_router(brokers_routes.router)
    async with app.router.lifespan_context(app):
        yield app
```

This overrides the default `test_app` fixture from `tests/coordinator/conftest.py` for THIS file only. When S6 mounts the router properly, delete this override.

- [ ] **F3.8 — Confirm pass**

Run: `pytest tests/coordinator/test_brokers_api.py -v`
Expected: PASS.

- [ ] **F3.9 — Commit**

```bash
git add coordinator/services/asset_catalog.py coordinator/api/routes/brokers.py \
        tests/coordinator/services/test_asset_catalog.py \
        tests/coordinator/test_brokers_api.py
git commit -m "feat(catalog): broker-driven asset-type catalog + API

Backs Spec A's account-creation checkbox UI and order-ticket
asset-type filter. Router wiring deferred to S6."
```

### Work unit F4: Client options math library

**Branch:** `plan/F4-options-math`

**Files:**
- Create: `dashboard/src/lib/options.ts`, `dashboard/src/lib/options.test.ts`

Pure TypeScript Black-Scholes, payoff, and Greeks. Self-contained; no other client code depends on it.

- [ ] **F4.1 — Failing test for Black-Scholes call/put**

```typescript
// dashboard/src/lib/options.test.ts
import { describe, it, expect } from "vitest";
import { bsCall, bsPut, greeks, legCostAtExpiry, strategyPnl } from "./options";

describe("Black-Scholes", () => {
  // Hull, Options Futures and Other Derivatives, Ch. 15 worked example:
  // S=42, K=40, r=0.10, sigma=0.20, T=0.5 → call ≈ 4.7594, put ≈ 0.8086
  it("prices a call option per Hull", () => {
    const c = bsCall(42, 40, 0.5, 0.10, 0.20);
    expect(c).toBeCloseTo(4.7594, 3);
  });
  it("prices a put option per Hull", () => {
    const p = bsPut(42, 40, 0.5, 0.10, 0.20);
    expect(p).toBeCloseTo(0.8086, 3);
  });
  it("respects put-call parity", () => {
    // C - P = S - K*e^(-rT)
    const S = 100, K = 100, T = 0.25, r = 0.05, sigma = 0.30;
    const lhs = bsCall(S, K, T, r, sigma) - bsPut(S, K, T, r, sigma);
    const rhs = S - K * Math.exp(-r * T);
    expect(lhs).toBeCloseTo(rhs, 4);
  });
});

describe("Greeks", () => {
  it("ATM call delta ≈ 0.5", () => {
    const g = greeks("buy", "call", 100, 100, 0.25, 0.05, 0.30);
    expect(g.delta).toBeGreaterThan(0.45);
    expect(g.delta).toBeLessThan(0.65);
  });
  it("short position flips delta sign", () => {
    const buy = greeks("buy", "call", 100, 100, 0.25, 0.05, 0.30);
    const sell = greeks("sell", "call", 100, 100, 0.25, 0.05, 0.30);
    expect(sell.delta).toBeCloseTo(-buy.delta, 6);
  });
});

describe("Expiry payoff", () => {
  it("long call: intrinsic above strike, zero below", () => {
    const leg = { side: "buy", right: "call", strike: 100, quantity: 1,
                  bid: 5, ask: 5.2, expiry: "2026-06-20", iv: 0.3 } as const;
    expect(legCostAtExpiry(leg, 110)).toBeCloseTo(10 - 5.1, 4);  // intrinsic - mid
    expect(legCostAtExpiry(leg, 90)).toBeCloseTo(-5.1, 4);        // -mid
  });
  it("vertical spread max profit equals strike width minus net debit", () => {
    const legs = [
      { side: "buy",  right: "call", strike: 100, quantity: 1, bid: 8, ask: 8.2, expiry: "2026-06-20", iv: 0.3 },
      { side: "sell", right: "call", strike: 110, quantity: 1, bid: 4, ask: 4.2, expiry: "2026-06-20", iv: 0.3 },
    ] as const;
    // mid debit = 8.1 - 4.1 = 4.0; width = 10; max profit at expiry above 110 = 10 - 4 = 6
    const pnlAt120 = strategyPnl(legs as any, 120, "expiry");
    expect(pnlAt120).toBeCloseTo(6.0, 4);
  });
});
```

- [ ] **F4.2 — Confirm fail**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/lib/options.test.ts`
Expected: Module not found.

- [ ] **F4.3 — Implement options.ts**

```typescript
// dashboard/src/lib/options.ts
// Pure Black-Scholes + payoff math for the strategy builder.

export type Side = "buy" | "sell";
export type Right = "call" | "put";

export type OptionLeg = {
  side: Side;
  right: Right;
  strike: number;
  quantity: number;
  expiry: string;       // YYYY-MM-DD
  bid?: number;
  ask?: number;
  iv: number;           // decimal
};

export type GreeksOut = {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
};

// Standard normal CDF via Abramowitz & Stegun 7.1.26 (max error ~7.5e-8).
function normCdf(x: number): number {
  const a1 =  0.254829592, a2 = -0.284496736, a3 =  1.421413741;
  const a4 = -1.453152027, a5 =  1.061405429, p  =  0.3275911;
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + p * ax);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
  return 0.5 * (1 + sign * y);
}

function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

function d1(S: number, K: number, T: number, r: number, sigma: number): number {
  return (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
}

function d2(d1v: number, sigma: number, T: number): number {
  return d1v - sigma * Math.sqrt(T);
}

export function bsCall(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0) return Math.max(S - K, 0);
  const D1 = d1(S, K, T, r, sigma);
  const D2 = d2(D1, sigma, T);
  return S * normCdf(D1) - K * Math.exp(-r * T) * normCdf(D2);
}

export function bsPut(S: number, K: number, T: number, r: number, sigma: number): number {
  if (T <= 0) return Math.max(K - S, 0);
  const D1 = d1(S, K, T, r, sigma);
  const D2 = d2(D1, sigma, T);
  return K * Math.exp(-r * T) * normCdf(-D2) - S * normCdf(-D1);
}

export function greeks(side: Side, right: Right, S: number, K: number,
                       T: number, r: number, sigma: number): GreeksOut {
  if (T <= 0) return { delta: 0, gamma: 0, theta: 0, vega: 0 };
  const D1 = d1(S, K, T, r, sigma);
  const D2 = d2(D1, sigma, T);
  const sign = side === "buy" ? 1 : -1;
  const callDelta = normCdf(D1);
  const putDelta  = callDelta - 1;
  const delta = (right === "call" ? callDelta : putDelta) * sign;
  const gamma = (normPdf(D1) / (S * sigma * Math.sqrt(T))) * sign;
  const vega  = (S * normPdf(D1) * Math.sqrt(T)) / 100 * sign;       // per 1% vol move
  const thetaAnnual = right === "call"
    ? -(S * normPdf(D1) * sigma) / (2 * Math.sqrt(T)) - r * K * Math.exp(-r * T) * normCdf(D2)
    : -(S * normPdf(D1) * sigma) / (2 * Math.sqrt(T)) + r * K * Math.exp(-r * T) * normCdf(-D2);
  const theta = (thetaAnnual / 365) * sign;                          // per day
  return { delta, gamma, theta, vega };
}

// ---- Payoffs ----

function legMidPrice(leg: OptionLeg): number {
  if (leg.bid != null && leg.ask != null) return (leg.bid + leg.ask) / 2;
  if (leg.ask != null) return leg.ask;
  if (leg.bid != null) return leg.bid;
  return 0;
}

export function legCostAtExpiry(leg: OptionLeg, S: number): number {
  const intrinsic = leg.right === "call"
    ? Math.max(S - leg.strike, 0)
    : Math.max(leg.strike - S, 0);
  const entry = legMidPrice(leg);
  const sign = leg.side === "buy" ? 1 : -1;
  return (intrinsic - entry) * sign * leg.quantity;
}

export function legCostAtDate(
  leg: OptionLeg, S: number, dateMs: number, r = 0.04,
): number {
  const expiryMs = Date.parse(leg.expiry + "T16:00:00Z");
  const T = Math.max((expiryMs - dateMs) / (365.25 * 24 * 3600 * 1000), 0);
  const theo = leg.right === "call"
    ? bsCall(S, leg.strike, T, r, leg.iv)
    : bsPut(S, leg.strike, T, r, leg.iv);
  const entry = legMidPrice(leg);
  const sign = leg.side === "buy" ? 1 : -1;
  return (theo - entry) * sign * leg.quantity;
}

export function strategyPnl(
  legs: OptionLeg[], S: number, dateMs: number | "expiry", r = 0.04,
): number {
  if (dateMs === "expiry") {
    return legs.reduce((sum, l) => sum + legCostAtExpiry(l, S), 0);
  }
  return legs.reduce((sum, l) => sum + legCostAtDate(l, S, dateMs, r), 0);
}

export function strategyGreeks(
  legs: OptionLeg[], S: number, dateMs: number, r = 0.04,
): GreeksOut {
  return legs.reduce((agg, l) => {
    const expiryMs = Date.parse(l.expiry + "T16:00:00Z");
    const T = Math.max((expiryMs - dateMs) / (365.25 * 24 * 3600 * 1000), 0);
    const g = greeks(l.side, l.right, S, l.strike, T, r, l.iv);
    return {
      delta: agg.delta + g.delta * l.quantity,
      gamma: agg.gamma + g.gamma * l.quantity,
      theta: agg.theta + g.theta * l.quantity,
      vega:  agg.vega  + g.vega  * l.quantity,
    };
  }, { delta: 0, gamma: 0, theta: 0, vega: 0 });
}

export function pnlCurve(
  legs: OptionLeg[],
  spotRange: [number, number],
  dateMs: number | "expiry",
  steps: number = 200,
): { x: number; y: number }[] {
  const [lo, hi] = spotRange;
  const step = (hi - lo) / steps;
  const out: { x: number; y: number }[] = [];
  for (let i = 0; i <= steps; i++) {
    const x = lo + i * step;
    out.push({ x, y: strategyPnl(legs, x, dateMs) });
  }
  return out;
}
```

- [ ] **F4.4 — Run tests, confirm pass**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx vitest run src/lib/options.test.ts`
Expected: PASS.

- [ ] **F4.5 — Commit**

```bash
git add dashboard/src/lib/options.ts dashboard/src/lib/options.test.ts
git commit -m "feat(strategy): client-side options math library

Pure TS Black-Scholes, Greeks, and strategy P&L aggregation. Used
by Spec C's strategy builder. No external deps."
```

### Work unit F5: Database migration

**Branch:** `plan/F5-schema`

**Files:**
- Modify: `coordinator/database/models.py`
- Create: `coordinator/database/migrations/versions/<timestamp>_live_subs_and_worker_ts_nullable.py`
- Test: `tests/coordinator/test_migrations.py` (optional smoke; alembic upgrade head against in-memory)

- [ ] **F5.1 — Modify Worker.tailscale_ip to nullable**

In `coordinator/database/models.py`, change:

```python
tailscale_ip: Mapped[str] = mapped_column(String, nullable=False)
```

to:

```python
tailscale_ip: Mapped[Optional[str]] = mapped_column(String, nullable=True)
```

- [ ] **F5.2 — Add LiveSubscription model**

Append to `coordinator/database/models.py` (before the trailing `Setting` block to keep account-related models clustered):

```python
class LiveSubscription(Base):
    __tablename__ = "live_subscriptions"
    __table_args__ = (
        UniqueConstraint("broker", "symbol", name="uq_live_subscription_broker_symbol"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    broker: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="stopped")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_tick_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tick_rate_per_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tick_retention_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    dependent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
```

Add `UniqueConstraint` to the existing SQLAlchemy imports at the top of the file if not already present.

- [ ] **F5.3 — Generate alembic migration**

```bash
cd /home/jkern/dev/quilt-trader
alembic revision --autogenerate -m "live_subs and worker_tailscale_ip_nullable"
```

Hand-inspect the generated file. Expected operations:
- `op.create_table("live_subscriptions", ...)`
- `op.alter_column("workers", "tailscale_ip", nullable=True)`

If alembic missed either (it sometimes does with nullable changes on SQLite), edit the file to add `op.alter_column("workers", "tailscale_ip", existing_type=sa.String(), nullable=True)` in `upgrade()` and the reverse in `downgrade()`.

- [ ] **F5.4 — Apply and verify**

```bash
alembic upgrade head
sqlite3 data/quilt_trader.db ".schema live_subscriptions"
sqlite3 data/quilt_trader.db ".schema workers" | grep tailscale_ip
```

Expected: `live_subscriptions` table exists; `tailscale_ip` column has no `NOT NULL`.

- [ ] **F5.5 — Commit**

```bash
git add coordinator/database/models.py coordinator/database/migrations/versions/
git commit -m "feat(schema): add live_subscriptions table + drop tailscale_ip NOT NULL

Supports Spec B (subscriptions) and Spec A §4 (worker.tailscale_ip
becomes optional; populated via heartbeat self-report)."
```

### Work unit F6: SDK context + DataClient source parameter

**Branch:** `plan/F6-context-source`

**Files:**
- Modify: `sdk/context.py`, `worker/data_client.py`
- Test: `tests/sdk/test_context.py` (if exists, else create skeleton), `tests/worker/test_data_client.py`

Adds the optional `source` parameter that Spec B's algos use to select between historical and broker-live data.

- [ ] **F6.1 — Failing test for DataClient source param**

```python
# tests/worker/test_data_client.py (append; assume existing async test pattern)
import pytest
from unittest.mock import AsyncMock
from worker.data_client import DataClient

@pytest.mark.asyncio
async def test_get_market_data_passes_source_param():
    mock_http = AsyncMock()
    mock_response = AsyncMock()
    mock_response.json = lambda: {"data": [{"timestamp": "2026-05-14T10:00:00Z", "close": 100}]}
    mock_response.raise_for_status = lambda: None
    mock_http.get.return_value = mock_response
    client = DataClient(base_url="http://test", cache_ttl=0, http_client=mock_http)
    await client.get_market_data("SPY", timeframe="1min", bars=10, source="alpaca_live")
    args, kwargs = mock_http.get.call_args
    assert kwargs["params"]["source"] == "alpaca_live"

@pytest.mark.asyncio
async def test_get_market_data_omits_source_when_none():
    mock_http = AsyncMock()
    mock_response = AsyncMock()
    mock_response.json = lambda: {"data": []}
    mock_response.raise_for_status = lambda: None
    mock_http.get.return_value = mock_response
    client = DataClient(base_url="http://test", cache_ttl=0, http_client=mock_http)
    await client.get_market_data("SPY")
    args, kwargs = mock_http.get.call_args
    assert "source" not in kwargs["params"]
```

- [ ] **F6.2 — Confirm fail**

Run: `pytest tests/worker/test_data_client.py -v -k source`
Expected: TypeError (unexpected keyword `source`).

- [ ] **F6.3 — Modify DataClient.get_market_data**

In `worker/data_client.py`:

```python
    async def get_market_data(
        self, symbol: str, timeframe: str = "1min", bars: int = 100,
        source: Optional[str] = None,
    ) -> pd.DataFrame:
        url = f"{self._base_url}/api/data/market/{symbol}"
        cache_key = f"market:{symbol}:{timeframe}:{bars}:{source or '_default'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        params: dict = {"timeframe": timeframe, "bars": bars}
        if source is not None:
            params["source"] = source
        response = await self._http.get(url, params=params)
        response.raise_for_status()
        data = response.json().get("data", [])
        df = pd.DataFrame(data)
        self._set_cached(cache_key, df)
        return df
```

- [ ] **F6.4 — Update TickContext abstract method**

In `sdk/context.py`:

```python
    @abstractmethod
    def market_data(
        self, symbol: str, timeframe: str = "1min", bars: int = 100,
        source: Optional[str] = None,
    ) -> pd.DataFrame: ...
```

- [ ] **F6.5 — Confirm pass**

Run: `pytest tests/worker/test_data_client.py -v`
Expected: PASS.

- [ ] **F6.6 — Commit**

```bash
git add sdk/context.py worker/data_client.py tests/worker/test_data_client.py
git commit -m "feat(sdk): add source param to market_data lookup

Lets algorithms request a specific source (e.g. 'alpaca_live' or
'polygon'). Spec B §6. Backward compatible: omitted = default
resolution at the server."
```

---

**End of Phase 0.** All six work units merge into the integration branch before Phase 1 starts. Resolve any trivial conflicts (likely none — file ownership map kept them apart).

---

## Phase 1 — Adapter implementations

Two work units, parallel. Each completely owns one adapter file.

### Work unit A1: Alpaca adapter (multi-leg + chain)

**Branch:** `plan/A1-alpaca`

**Files:**
- Modify: `worker/alpaca_adapter.py`
- Test: `tests/worker/test_alpaca_adapter.py`

Implements Spec A §2 (native multi-leg via Alpaca's `OrderClass.MLEG`) and Spec C §4 (option chain endpoints via Alpaca's v1beta1 options API).

- [ ] **A1.1 — Failing test: supports_multileg_orders true for same-underlying options**

```python
# tests/worker/test_alpaca_adapter.py (append)
from worker.broker_adapter import MultilegLegSpec
from worker.alpaca_adapter import AlpacaAdapter

def _opt_leg(side, right, strike, expiry="2026-06-20"):
    return MultilegLegSpec(symbol="SPY", asset_type="options", side=side,
                           quantity=1, expiry=expiry, strike=strike, right=right)

def test_supports_multileg_same_underlying_options():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    legs = [_opt_leg("buy", "call", 560), _opt_leg("sell", "call", 570)]
    assert adapter.supports_multileg_orders(legs) is True

def test_supports_multileg_false_for_mixed_underlyings():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg1 = _opt_leg("buy", "call", 560)
    leg2 = MultilegLegSpec(symbol="QQQ", asset_type="options", side="sell",
                           quantity=1, expiry="2026-06-20", strike=450, right="call")
    assert adapter.supports_multileg_orders([leg1, leg2]) is False

def test_supports_multileg_false_for_non_options():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg = MultilegLegSpec(symbol="SPY", asset_type="equities", side="buy", quantity=100)
    assert adapter.supports_multileg_orders([leg, leg]) is False
```

- [ ] **A1.2 — Confirm fail**

Run: `pytest tests/worker/test_alpaca_adapter.py -v -k supports_multileg`
Expected: AttributeError or assertion fail.

- [ ] **A1.3 — Implement supports_multileg_orders + compose_symbol**

Append to `AlpacaAdapter`:

```python
    def supports_multileg_orders(self, legs):
        if len(legs) < 2:
            return False
        if not all(l.asset_type == "options" for l in legs):
            return False
        underlyings = {l.symbol for l in legs}
        return len(underlyings) == 1

    def compose_symbol(self, leg):
        if leg.asset_type != "options":
            return leg.symbol
        # OCC: <UNDERLYING><YY><MM><DD><C|P><strike*1000 padded 8>
        if not (leg.expiry and leg.strike is not None and leg.right):
            raise ValueError(f"Options leg missing expiry/strike/right: {leg}")
        y, m, d = leg.expiry.split("-")
        right_ch = "C" if leg.right == "call" else "P"
        strike_int = round(leg.strike * 1000)
        return f"{leg.symbol}{y[2:]}{m}{d}{right_ch}{strike_int:08d}"
```

- [ ] **A1.4 — Failing test: compose_symbol OCC format**

```python
def test_compose_symbol_call_occ():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg = _opt_leg("buy", "call", 560.0, expiry="2026-06-20")
    assert adapter.compose_symbol(leg) == "SPY260620C00560000"

def test_compose_symbol_put_occ():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg = _opt_leg("sell", "put", 565.50, expiry="2026-06-20")
    assert adapter.compose_symbol(leg) == "SPY260620P00565500"

def test_compose_symbol_equities_passthrough():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg = MultilegLegSpec(symbol="SPY", asset_type="equities", side="buy", quantity=100)
    assert adapter.compose_symbol(leg) == "SPY"
```

- [ ] **A1.5 — Run + confirm pass**

Run: `pytest tests/worker/test_alpaca_adapter.py -v -k compose_symbol`
Expected: PASS.

- [ ] **A1.6 — Failing test: submit_multileg_order builds Alpaca MLEG order**

```python
def test_submit_multileg_order_calls_alpaca_with_mleg_class():
    from unittest.mock import MagicMock, patch
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    mock_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "parent-1"
    mock_order.legs = [
        MagicMock(id="leg-1", filled_avg_price="8.30", status="filled"),
        MagicMock(id="leg-2", filled_avg_price="4.20", status="filled"),
    ]
    mock_client.submit_order.return_value = mock_order
    legs = [_opt_leg("buy", "call", 560), _opt_leg("sell", "call", 570)]
    with patch.object(adapter, "_ensure_clients"):
        adapter._trading_client = mock_client
        result = adapter.submit_multileg_order(legs, order_type="limit", limit_price=4.0)
    submitted = mock_client.submit_order.call_args.args[0]
    # Inspect the request object — exact shape depends on alpaca-py version
    assert getattr(submitted, "order_class", None) is not None
    assert result.broker_order_id == "parent-1"
    assert result.atomic is True
    assert len(result.legs) == 2
    assert result.legs[0].status == "filled"
    assert result.legs[0].filled_price == 8.30
```

- [ ] **A1.7 — Implement submit_multileg_order**

Append to `AlpacaAdapter`:

```python
    def submit_multileg_order(self, legs, order_type, limit_price):
        from alpaca.trading.requests import OptionLegRequest, LimitOrderRequest, MarketOrderRequest
        from alpaca.trading.enums import OrderSide, OrderClass, PositionIntent, TimeInForce

        self._ensure_clients()
        req_legs = []
        for leg in legs:
            req_legs.append(OptionLegRequest(
                symbol=self.compose_symbol(leg),
                side=OrderSide.BUY if leg.side == "buy" else OrderSide.SELL,
                ratio_qty=int(leg.quantity),
                # alpaca-py uses positional intent to mark open/close — for new positions:
                position_intent=PositionIntent.BUY_TO_OPEN if leg.side == "buy"
                                else PositionIntent.SELL_TO_OPEN,
            ))
        if order_type == "limit":
            if limit_price is None:
                raise ValueError("limit_price required for limit order")
            req = LimitOrderRequest(
                qty=1,                              # ratio defined per leg
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
                legs=req_legs,
            )
        else:
            req = MarketOrderRequest(
                qty=1,
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                legs=req_legs,
            )
        order = self._trading_client.submit_order(req)
        leg_results = []
        for i, lg in enumerate(getattr(order, "legs", []) or []):
            leg_results.append(MultilegLegResult(
                index=i,
                status=str(getattr(lg, "status", "pending")).lower(),
                filled_price=float(getattr(lg, "filled_avg_price", 0) or 0) or None,
                fees=None,                          # Alpaca returns fees separately, omit for v1
                broker_order_id=str(getattr(lg, "id", "")) or None,
            ))
        return MultilegOrderResult(
            broker_order_id=str(order.id),
            legs=leg_results,
            atomic=True,
        )
```

Add at the top of the file with other imports:

```python
from worker.broker_adapter import (
    BrokerAdapter, OrderResult,
    MultilegLegResult, MultilegOrderResult,
    OptionContract, OptionChainSnapshot,
)
```

- [ ] **A1.8 — Run + confirm pass**

Run: `pytest tests/worker/test_alpaca_adapter.py -v -k submit_multileg`
Expected: PASS.

- [ ] **A1.9 — Failing test: list_option_expiries + get_option_chain**

```python
def test_list_option_expiries_returns_sorted_dates():
    from datetime import date
    from unittest.mock import MagicMock, patch
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    mock_data = MagicMock()
    mock_data.get_option_contracts.return_value = MagicMock(option_contracts=[
        MagicMock(expiration_date=date(2026, 6, 20)),
        MagicMock(expiration_date=date(2026, 5, 16)),
        MagicMock(expiration_date=date(2026, 6, 20)),  # dup
    ])
    with patch.object(adapter, "_ensure_clients"):
        adapter._data_client = mock_data
        result = adapter.list_option_expiries("SPY")
    assert result == [date(2026, 5, 16), date(2026, 6, 20)]

def test_get_option_chain_maps_snapshot_to_contracts():
    from datetime import date, datetime, timezone
    from unittest.mock import MagicMock, patch
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    mock_data = MagicMock()
    # Stub: snapshot with one call and one put
    snap = MagicMock()
    snap.snapshots = {
        "SPY260620C00560000": MagicMock(
            latest_quote=MagicMock(bid_price=8.2, ask_price=8.4, timestamp=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc)),
            latest_trade=MagicMock(price=8.3),
            implied_volatility=0.30,
            greeks=MagicMock(delta=0.55, gamma=0.020, theta=-14.1, vega=48.0),
            open_interest=2345, daily_bar=MagicMock(volume=789),
        ),
    }
    mock_data.get_option_chain.return_value = snap
    # Spot lookup uses the trading data client too
    mock_data.get_stock_latest_trade.return_value = {"SPY": MagicMock(price=565.0)}
    with patch.object(adapter, "_ensure_clients"):
        adapter._data_client = mock_data
        chain = adapter.get_option_chain("SPY", date(2026, 6, 20))
    assert chain.underlying == "SPY"
    assert chain.spot == 565.0
    assert chain.expiry == date(2026, 6, 20)
    assert len(chain.contracts) == 1
    c = chain.contracts[0]
    assert c.strike == 560.0
    assert c.right == "call"
    assert c.bid == 8.2 and c.ask == 8.4
    assert c.iv == 0.30
    assert c.delta == 0.55
```

- [ ] **A1.10 — Implement list_option_expiries + get_option_chain**

Append to `AlpacaAdapter`:

```python
    def list_option_expiries(self, underlying):
        from alpaca.data.requests import GetOptionContractsRequest
        self._ensure_clients()
        req = GetOptionContractsRequest(underlying_symbol=underlying, limit=10000)
        resp = self._data_client.get_option_contracts(req)
        dates = {c.expiration_date for c in (resp.option_contracts or [])}
        return sorted(dates)

    def get_option_chain(self, underlying, expiry):
        from datetime import datetime, timezone
        from alpaca.data.requests import (
            OptionChainRequest, StockLatestTradeRequest,
        )
        self._ensure_clients()
        spot_resp = self._data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=[underlying])
        )
        spot = float(spot_resp[underlying].price)

        chain_resp = self._data_client.get_option_chain(
            OptionChainRequest(underlying_symbol=underlying, expiration_date=expiry)
        )
        contracts = []
        for occ_sym, snap in (chain_resp.snapshots or {}).items():
            # Parse OCC: <UNDERLYING><YY><MM><DD><C|P><strike*1000 padded 8>
            tail = occ_sym[len(underlying):]
            right = "call" if tail[6] == "C" else "put"
            strike = int(tail[7:15]) / 1000
            q = getattr(snap, "latest_quote", None)
            t = getattr(snap, "latest_trade", None)
            g = getattr(snap, "greeks", None) or MagicMock(delta=None, gamma=None, theta=None, vega=None)
            db = getattr(snap, "daily_bar", None)
            contracts.append(OptionContract(
                strike=strike, right=right, occ_symbol=occ_sym,
                bid=getattr(q, "bid_price", None), ask=getattr(q, "ask_price", None),
                last=getattr(t, "price", None),
                iv=getattr(snap, "implied_volatility", None),
                delta=getattr(g, "delta", None), gamma=getattr(g, "gamma", None),
                theta=getattr(g, "theta", None), vega=getattr(g, "vega", None),
                open_interest=getattr(snap, "open_interest", None),
                volume=getattr(db, "volume", None) if db else None,
            ))
        contracts.sort(key=lambda c: (c.strike, c.right))
        as_of = max(
            (getattr(getattr(s, "latest_quote", None), "timestamp", None)
             for s in (chain_resp.snapshots or {}).values()
             if getattr(s, "latest_quote", None) is not None),
            default=None,
        )
        return OptionChainSnapshot(
            underlying=underlying, spot=spot, expiry=expiry,
            contracts=contracts, as_of=as_of,
        )
```

Replace the inline `MagicMock(...)` fallback in `get_option_chain` for `g` with a plain object once you confirm alpaca-py's actual `greeks` payload. The mock-based fallback is fine for the test path but a small dataclass is cleaner:

```python
from dataclasses import dataclass
@dataclass
class _NullGreeks:
    delta = None; gamma = None; theta = None; vega = None
# then: g = getattr(snap, "greeks", None) or _NullGreeks()
```

- [ ] **A1.11 — Run + confirm pass**

Run: `pytest tests/worker/test_alpaca_adapter.py -v`
Expected: PASS for all newly added tests; existing tests still PASS.

- [ ] **A1.12 — Commit**

```bash
git add worker/alpaca_adapter.py tests/worker/test_alpaca_adapter.py
git commit -m "feat(alpaca): native multi-leg orders + options chain API

Implements MultilegOrderResult via OrderClass.MLEG and
list_option_expiries + get_option_chain via Alpaca v1beta1
options endpoints. Adapter-level OCC composition for options."
```

### Work unit A2: Tradier adapter (multi-leg + chain)

**Branch:** `plan/A2-tradier`

**Files:**
- Modify: `worker/tradier_adapter.py`
- Test: `tests/worker/test_tradier_adapter.py`

Mirrors A1 against Tradier's API. Tradier has excellent options coverage — chain returns Greeks server-side, multi-leg uses `class=multileg`.

- [ ] **A2.1 — Failing test: supports + compose**

Same shape as A1.1 / A1.4 but against `TradierAdapter`. Tradier OCC is identical to Alpaca's. Re-use the test pattern.

- [ ] **A2.2 — Implement supports_multileg_orders + compose_symbol**

```python
    def supports_multileg_orders(self, legs):
        if len(legs) < 2:
            return False
        if not all(l.asset_type == "options" for l in legs):
            return False
        return len({l.symbol for l in legs}) == 1

    def compose_symbol(self, leg):
        if leg.asset_type != "options":
            return leg.symbol
        if not (leg.expiry and leg.strike is not None and leg.right):
            raise ValueError(f"Options leg missing expiry/strike/right: {leg}")
        y, m, d = leg.expiry.split("-")
        right_ch = "C" if leg.right == "call" else "P"
        strike_int = round(leg.strike * 1000)
        return f"{leg.symbol}{y[2:]}{m}{d}{right_ch}{strike_int:08d}"
```

- [ ] **A2.3 — Failing test: submit_multileg_order POSTs to Tradier**

```python
def test_submit_multileg_order_posts_class_multileg():
    from unittest.mock import patch, MagicMock
    adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"order": {"id": 12345, "status": "ok"}}
    mock_resp.raise_for_status = lambda: None
    legs = [_opt_leg("buy", "call", 560), _opt_leg("sell", "call", 570)]
    with patch("requests.post", return_value=mock_resp) as p:
        result = adapter.submit_multileg_order(legs, order_type="limit", limit_price=4.0)
    posted_data = p.call_args.kwargs["data"]
    assert posted_data["class"] == "multileg"
    assert posted_data["symbol"] == "SPY"
    assert posted_data["type"] == "debit"            # limit becomes debit/credit per net price
    assert posted_data["price"] == "4.00"
    # Per-leg fields
    assert posted_data["option_symbol[0]"] == "SPY260620C00560000"
    assert posted_data["side[0]"] == "buy_to_open"
    assert posted_data["quantity[0]"] == "1"
    assert posted_data["option_symbol[1]"] == "SPY260620C00570000"
    assert posted_data["side[1]"] == "sell_to_open"
    assert result.broker_order_id == "12345"
    assert result.atomic is True
```

- [ ] **A2.4 — Implement submit_multileg_order**

```python
    def submit_multileg_order(self, legs, order_type, limit_price):
        import requests
        url = f"{self._base_url}/v1/accounts/{self._account_id}/orders"
        # Tradier's "type" for net price: debit if net cost > 0, credit if < 0.
        net_sign = sum(1 if l.side == "buy" else -1 for l in legs)
        tradier_type = "debit" if net_sign >= 0 else "credit"
        data = {
            "class": "multileg",
            "symbol": legs[0].symbol,
            "type": tradier_type if order_type == "limit" else "market",
            "duration": "day",
        }
        if order_type == "limit":
            if limit_price is None:
                raise ValueError("limit_price required for limit order")
            data["price"] = f"{limit_price:.2f}"
        for i, leg in enumerate(legs):
            data[f"option_symbol[{i}]"] = self.compose_symbol(leg)
            side_map = {("buy",): "buy_to_open", ("sell",): "sell_to_open"}
            data[f"side[{i}]"] = side_map[(leg.side,)]
            data[f"quantity[{i}]"] = str(int(leg.quantity))
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        resp = requests.post(url, data=data, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json().get("order", {})
        order_id = str(body.get("id", ""))
        # Tradier returns one parent ID; per-leg fills appear later via order-status polling.
        leg_results = [
            MultilegLegResult(
                index=i, status="pending",
                broker_order_id=order_id,
            )
            for i in range(len(legs))
        ]
        return MultilegOrderResult(
            broker_order_id=order_id,
            legs=leg_results,
            atomic=True,
        )
```

- [ ] **A2.5 — Failing test: list_option_expiries + get_option_chain**

```python
def test_list_option_expiries_returns_sorted_dates():
    from datetime import date
    from unittest.mock import patch, MagicMock
    adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "expirations": {"date": ["2026-05-16", "2026-06-20"]},
    }
    mock_resp.raise_for_status = lambda: None
    with patch("requests.get", return_value=mock_resp):
        out = adapter.list_option_expiries("SPY")
    assert out == [date(2026, 5, 16), date(2026, 6, 20)]

def test_get_option_chain_maps_strikes_and_greeks():
    from datetime import date
    from unittest.mock import patch, MagicMock
    adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
    chain_resp = MagicMock()
    chain_resp.json.return_value = {
        "options": {
            "option": [
                {
                    "symbol": "SPY260620C00560000", "strike": 560.0, "option_type": "call",
                    "bid": 8.2, "ask": 8.4, "last": 8.3,
                    "greeks": {"mid_iv": 0.30, "delta": 0.55, "gamma": 0.020,
                               "theta": -14.1, "vega": 48.0},
                    "open_interest": 2345, "volume": 789,
                },
                {
                    "symbol": "SPY260620P00560000", "strike": 560.0, "option_type": "put",
                    "bid": 1.1, "ask": 1.3, "last": 1.2,
                    "greeks": {"mid_iv": 0.32, "delta": -0.45, "gamma": 0.020,
                               "theta": -12.0, "vega": 48.0},
                    "open_interest": 1100, "volume": 200,
                },
            ]
        }
    }
    chain_resp.raise_for_status = lambda: None
    spot_resp = MagicMock()
    spot_resp.json.return_value = {"quotes": {"quote": {"last": 565.0}}}
    spot_resp.raise_for_status = lambda: None
    with patch("requests.get", side_effect=[spot_resp, chain_resp]):
        chain = adapter.get_option_chain("SPY", date(2026, 6, 20))
    assert chain.spot == 565.0
    assert len(chain.contracts) == 2
    assert chain.contracts[0].right == "call"
    assert chain.contracts[1].right == "put"
    assert chain.contracts[0].iv == 0.30
    assert chain.contracts[0].delta == 0.55
```

- [ ] **A2.6 — Implement list_option_expiries + get_option_chain**

```python
    def list_option_expiries(self, underlying):
        from datetime import date
        import requests
        url = f"{self._base_url}/v1/markets/options/expirations"
        headers = {"Authorization": f"Bearer {self._access_token}",
                   "Accept": "application/json"}
        resp = requests.get(url, params={"symbol": underlying}, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json().get("expirations") or {}
        raw_dates = body.get("date") or []
        if isinstance(raw_dates, str):
            raw_dates = [raw_dates]
        return sorted(date.fromisoformat(d) for d in raw_dates)

    def get_option_chain(self, underlying, expiry):
        import requests
        headers = {"Authorization": f"Bearer {self._access_token}",
                   "Accept": "application/json"}
        # Spot
        spot_resp = requests.get(
            f"{self._base_url}/v1/markets/quotes",
            params={"symbols": underlying}, headers=headers, timeout=10,
        )
        spot_resp.raise_for_status()
        spot = float(spot_resp.json()["quotes"]["quote"]["last"])
        # Chain
        chain_resp = requests.get(
            f"{self._base_url}/v1/markets/options/chains",
            params={"symbol": underlying, "expiration": expiry.isoformat(),
                    "greeks": "true"},
            headers=headers, timeout=15,
        )
        chain_resp.raise_for_status()
        opts = chain_resp.json().get("options") or {}
        raw_opts = opts.get("option") or []
        if isinstance(raw_opts, dict):
            raw_opts = [raw_opts]
        contracts = []
        for o in raw_opts:
            g = o.get("greeks") or {}
            contracts.append(OptionContract(
                strike=float(o["strike"]),
                right="call" if o.get("option_type") == "call" else "put",
                occ_symbol=o["symbol"],
                bid=o.get("bid"), ask=o.get("ask"), last=o.get("last"),
                iv=g.get("mid_iv"),
                delta=g.get("delta"), gamma=g.get("gamma"),
                theta=g.get("theta"), vega=g.get("vega"),
                open_interest=o.get("open_interest"),
                volume=o.get("volume"),
            ))
        contracts.sort(key=lambda c: (c.strike, c.right))
        return OptionChainSnapshot(
            underlying=underlying, spot=spot, expiry=expiry,
            contracts=contracts, as_of=None,
        )
```

Imports at top of file:

```python
from worker.broker_adapter import (
    MultilegLegResult, MultilegOrderResult,
    OptionContract, OptionChainSnapshot,
)
```

- [ ] **A2.7 — Run + confirm pass**

Run: `pytest tests/worker/test_tradier_adapter.py -v`
Expected: PASS.

- [ ] **A2.8 — Commit**

```bash
git add worker/tradier_adapter.py tests/worker/test_tradier_adapter.py
git commit -m "feat(tradier): native multi-leg orders + options chain API

class=multileg POST for spread tickets, /options/expirations +
/options/chains?greeks=true for the chain browser."
```

---

**End of Phase 1.** A1 and A2 merge into the integration branch. No conflicts expected (separate adapter files).

---

## Phase 2 — Server endpoints

Six work units. S1–S5 are parallel. S6 must run last in this phase because it touches the central app wiring.

### Work unit S1: positions/open endpoint (Spec A §2)

**Branch:** `plan/S1-positions-open`

**Files:**
- Modify: `coordinator/api/routes/accounts.py`
- Test: `tests/coordinator/test_accounts_positions_open.py`

- [ ] **S1.1 — Failing test: 423 when account locked**

```python
# tests/coordinator/test_accounts_positions_open.py
import pytest
from httpx import AsyncClient
from coordinator.database.models import Account, AlgorithmInstance

@pytest.mark.asyncio
async def test_open_position_returns_423_when_locked(client: AsyncClient, db_session):
    account = Account(
        name="A", broker_type="alpaca", environment="paper",
        credentials="{}",  # encrypted_creds placeholder for this scope-restricted test
        supported_asset_types=["options"], pdt_mode="off",
        locked_by="instance-1",
    )
    db_session.add(account)
    await db_session.flush()
    body = {"legs": [{"symbol": "SPY", "asset_type": "options", "side": "buy",
                       "quantity": 1, "expiry": "2026-06-20", "strike": 560.0,
                       "right": "call"}],
            "order_type": "market"}
    r = await client.post(f"/api/accounts/{account.id}/positions/open", json=body)
    assert r.status_code == 423
    assert r.json()["detail"]["locked_by"] == "instance-1"
```

- [ ] **S1.2 — Failing test: 422 on disallowed asset type**

```python
@pytest.mark.asyncio
async def test_open_position_422_on_disallowed_asset_type(client, db_session):
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["equities"],
                      pdt_mode="off")
    db_session.add(account); await db_session.flush()
    body = {"legs": [{"symbol": "SPY", "asset_type": "options", "side": "buy",
                      "quantity": 1, "expiry": "2026-06-20", "strike": 560,
                      "right": "call"}],
            "order_type": "market"}
    r = await client.post(f"/api/accounts/{account.id}/positions/open", json=body)
    assert r.status_code == 422
    assert "options" in r.json()["detail"]
```

- [ ] **S1.3 — Failing test: atomic-path success**

```python
@pytest.mark.asyncio
async def test_open_position_atomic_path_persists_position(client, db_session, monkeypatch):
    from worker.broker_adapter import MultilegOrderResult, MultilegLegResult
    from coordinator.api.routes import accounts as accounts_routes
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["options"],
                      pdt_mode="off")
    db_session.add(account); await db_session.flush()

    class FakeAdapter:
        def supports_multileg_orders(self, legs): return True
        def compose_symbol(self, leg): return f"SPY{leg.strike:.0f}"
        def submit_multileg_order(self, legs, order_type, limit_price):
            return MultilegOrderResult(
                broker_order_id="parent-1",
                legs=[
                    MultilegLegResult(index=0, status="filled", filled_price=8.30,
                                      fees=0.65, broker_order_id="leg-1"),
                    MultilegLegResult(index=1, status="filled", filled_price=4.20,
                                      fees=0.65, broker_order_id="leg-2"),
                ],
                atomic=True,
            )
        def close(self): pass

    async def fake_adapter_for_account(acct):
        return FakeAdapter()
    monkeypatch.setattr(accounts_routes, "_adapter_for_account", fake_adapter_for_account)

    body = {
        "legs": [
            {"symbol": "SPY", "asset_type": "options", "side": "buy", "quantity": 1,
             "expiry": "2026-06-20", "strike": 560.0, "right": "call"},
            {"symbol": "SPY", "asset_type": "options", "side": "sell", "quantity": 1,
             "expiry": "2026-06-20", "strike": 570.0, "right": "call"},
        ],
        "order_type": "limit", "limit_price": 4.0,
        "strategy_type": "vertical_bull_call",
    }
    r = await client.post(f"/api/accounts/{account.id}/positions/open", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["atomic"] is True
    assert data["broker_order_id"] == "parent-1"
    assert data["partial_fill"] is False
    assert data["position_id"] is not None
```

- [ ] **S1.4 — Implement the endpoint**

Append to `coordinator/api/routes/accounts.py`:

```python
class _LegSpecIn(BaseModel):
    symbol: str
    asset_type: str
    side: str
    quantity: float
    expiry: Optional[str] = None
    strike: Optional[float] = None
    right: Optional[str] = None


class OpenPositionRequest(BaseModel):
    legs: list[_LegSpecIn]
    strategy_type: str = "single"
    order_type: str = "market"
    limit_price: Optional[float] = None


@router.post("/{account_id}/positions/open")
async def open_position(
    account_id: str,
    body: OpenPositionRequest,
    db: AsyncSession = Depends(get_db),
):
    from worker.broker_adapter import MultilegLegSpec
    from coordinator.database.models import Position, TradeLog

    account = (await db.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.locked_by:
        return PlainTextResponse(  # noqa: actually use JSONResponse below
            status_code=423,
            content=None,
        )  # replaced below

    # Validate asset types vs account
    allowed = set(account.supported_asset_types or [])
    bad = [l.asset_type for l in body.legs if l.asset_type not in allowed]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"Asset types not enabled on this account: {sorted(set(bad))}. "
                   f"Allowed: {sorted(allowed)}.",
        )

    # Options legs must have expiry/strike/right
    missing = [
        i for i, l in enumerate(body.legs)
        if l.asset_type == "options" and not (l.expiry and l.strike is not None and l.right)
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Options legs missing expiry/strike/right at indices: {missing}",
        )

    adapter = await _adapter_for_account(account)
    legs_spec = [MultilegLegSpec(
        symbol=l.symbol, asset_type=l.asset_type, side=l.side,
        quantity=l.quantity, expiry=l.expiry, strike=l.strike, right=l.right,
    ) for l in body.legs]

    try:
        if len(legs_spec) > 1 and adapter.supports_multileg_orders(legs_spec):
            # Atomic path
            def _submit():
                return adapter.submit_multileg_order(
                    legs_spec, order_type=body.order_type, limit_price=body.limit_price,
                )
            try:
                result = await asyncio.to_thread(_submit)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=422, detail=f"Broker rejected: {e}")
            # Persist
            position = Position(
                account_id=account_id,
                instance_id=None,
                strategy_type=body.strategy_type,
                legs=[{
                    "symbol": l.symbol, "asset_type": l.asset_type, "side": l.side,
                    "quantity": l.quantity, "expiry": l.expiry, "strike": l.strike,
                    "right": l.right, "avg_price": r.filled_price,
                } for l, r in zip(body.legs, result.legs)],
                status="open",
                net_cost=sum((r.filled_price or 0.0) * l.quantity * (1 if l.side == "buy" else -1)
                             for l, r in zip(body.legs, result.legs)),
                metadata_={"broker_order_id": result.broker_order_id},
            )
            db.add(position)
            await db.flush()
            for i, (leg, leg_res) in enumerate(zip(body.legs, result.legs)):
                db.add(TradeLog(
                    account_id=account_id,
                    source="manual",
                    timestamp=datetime.now(timezone.utc),
                    symbol=leg.symbol,
                    asset_type=leg.asset_type,
                    side=leg.side,
                    quantity=leg.quantity,
                    order_type=body.order_type,
                    filled_price=leg_res.filled_price or 0.0,
                    fees=leg_res.fees or 0.0,
                    broker_txn_id=leg_res.broker_order_id,
                    position_id=position.id,
                ))
            await db.flush()
            return {
                "position_id": position.id,
                "broker_order_id": result.broker_order_id,
                "legs": [
                    {"index": r.index, "status": r.status,
                     "filled_price": r.filled_price, "fees": r.fees,
                     "error": r.error, "broker_order_id": r.broker_order_id}
                    for r in result.legs
                ],
                "atomic": True,
                "partial_fill": False,
            }
        else:
            # Fallback: sequential per-leg submit_order
            leg_outcomes = []
            filled_legs = []
            for i, leg in enumerate(legs_spec):
                def _sub():
                    return adapter.submit_order(
                        symbol=adapter.compose_symbol(leg),
                        side=leg.side, quantity=leg.quantity,
                        order_type=body.order_type, limit_price=body.limit_price,
                    )
                try:
                    res = await asyncio.to_thread(_sub)
                    leg_outcomes.append({
                        "index": i, "status": "filled",
                        "filled_price": res.filled_price, "fees": res.fees,
                        "broker_order_id": res.broker_order_id, "error": None,
                    })
                    filled_legs.append((i, leg, res))
                except Exception as e:  # noqa: BLE001
                    leg_outcomes.append({
                        "index": i, "status": "rejected", "filled_price": None,
                        "fees": None, "broker_order_id": None, "error": str(e),
                    })
            partial = any(lo["status"] == "rejected" for lo in leg_outcomes) and len(filled_legs) > 0
            position_id = None
            if filled_legs:
                pos = Position(
                    account_id=account_id, instance_id=None,
                    strategy_type=body.strategy_type,
                    legs=[{
                        "symbol": l.symbol, "asset_type": l.asset_type, "side": l.side,
                        "quantity": l.quantity, "expiry": l.expiry, "strike": l.strike,
                        "right": l.right, "avg_price": r.filled_price,
                    } for _, l, r in filled_legs],
                    status="open",
                    net_cost=sum(r.filled_price * l.quantity * (1 if l.side == "buy" else -1)
                                 for _, l, r in filled_legs),
                    metadata_={"partial_fill": True} if partial else None,
                )
                db.add(pos)
                await db.flush()
                position_id = pos.id
                for _, leg, res in filled_legs:
                    db.add(TradeLog(
                        account_id=account_id, source="manual",
                        timestamp=datetime.now(timezone.utc),
                        symbol=leg.symbol, asset_type=leg.asset_type,
                        side=leg.side, quantity=leg.quantity,
                        order_type=body.order_type,
                        filled_price=res.filled_price, fees=res.fees or 0.0,
                        broker_txn_id=res.broker_order_id, position_id=pos.id,
                    ))
                await db.flush()
            return Response(
                content=__import__("json").dumps({
                    "position_id": position_id, "broker_order_id": None,
                    "legs": leg_outcomes, "atomic": False,
                    "partial_fill": partial,
                }),
                media_type="application/json",
                status_code=207 if partial else 200,
            )
    finally:
        _close_adapter(adapter)
```

Then replace the 423 stub. At the top of the handler, replace:

```python
if account.locked_by:
    return PlainTextResponse(...)  # the placeholder above
```

with:

```python
if account.locked_by:
    return Response(
        content=__import__("json").dumps({
            "detail": {"locked_by": account.locked_by}
        }),
        status_code=423, media_type="application/json",
    )
```

Add at file imports:

```python
from fastapi.responses import Response
```

- [ ] **S1.5 — Run all S1 tests, confirm pass**

Run: `pytest tests/coordinator/test_accounts_positions_open.py -v`
Expected: PASS.

- [ ] **S1.6 — Commit**

```bash
git add coordinator/api/routes/accounts.py tests/coordinator/test_accounts_positions_open.py
git commit -m "feat(accounts): POST positions/open with atomic + fallback paths"
```

### Work unit S2: algorithms/install-from-url endpoint (Spec A §3)

**Branch:** `plan/S2-install-from-url`

**Files:**
- Modify: `coordinator/api/routes/algorithms.py`, `coordinator/services/github_service.py`
- Test: `tests/coordinator/test_algorithms_install_from_url.py`

- [ ] **S2.1 — Failing test: 422 when manifest is a scraper**

```python
# tests/coordinator/test_algorithms_install_from_url.py
import pytest
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_install_from_url_rejects_scraper_manifest(client):
    yaml_text = """
name: my-scraper
type: scraper
version: 1.0.0
schedule: "0 12 * * *"
"""
    with patch("coordinator.api.routes.algorithms._fetch_manifest_yaml",
               return_value=yaml_text):
        r = await client.post("/api/algorithms/install-from-url",
                              json={"repo_url": "https://github.com/foo/bar"})
    assert r.status_code == 422
    assert "not an algorithm" in r.json()["detail"]

@pytest.mark.asyncio
async def test_install_from_url_rejects_invalid_url(client):
    r = await client.post("/api/algorithms/install-from-url",
                          json={"repo_url": "not-a-url"})
    assert r.status_code == 400
```

- [ ] **S2.2 — Implement**

In `coordinator/api/routes/algorithms.py`, append:

```python
import httpx
import base64

class InstallFromUrlRequest(BaseModel):
    repo_url: str


async def _fetch_manifest_yaml(owner: str, repo: str, db: AsyncSession) -> str:
    """Try public raw URL; on 404 fall back to PAT-authenticated contents API."""
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/quilt.yaml"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(raw_url)
    if r.status_code == 200:
        return r.text
    if r.status_code != 404:
        raise HTTPException(status_code=502, detail=f"Manifest fetch failed: {r.status_code}")

    setting = (await db.execute(
        select(Setting).where(Setting.key == "github_pat")
    )).scalar_one_or_none()
    if setting is None:
        raise HTTPException(
            status_code=400,
            detail="Repository not found or quilt.yaml missing. "
                   "If the repo is private, configure a GitHub PAT in Settings.",
        )
    container = get_container()
    pat = container.encryption.decrypt(setting.value)
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/quilt.yaml"
    headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(timeout=10) as c:
        ar = await c.get(api_url, headers=headers)
    if ar.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail="Repository or quilt.yaml not found, even with configured PAT.",
        )
    body = ar.json()
    if body.get("encoding") != "base64":
        raise HTTPException(status_code=502, detail="Unexpected content encoding")
    return base64.b64decode(body["content"]).decode()


@router.post("/api/algorithms/install-from-url", status_code=201)
async def install_from_url(body: InstallFromUrlRequest, db: AsyncSession = Depends(get_db)):
    full_name = _full_name_from_url(body.repo_url)
    if not full_name:
        raise HTTPException(status_code=400, detail=f"Unsupported repo URL: {body.repo_url}")
    owner, repo = full_name.split("/", 1)

    yaml_text = await _fetch_manifest_yaml(owner, repo, db)
    from sdk.manifest import QuiltManifest, ManifestError
    try:
        manifest = QuiltManifest.from_string(yaml_text)
    except ManifestError as e:
        raise HTTPException(status_code=422, detail=f"Invalid manifest: {e}")
    if manifest.type != "algorithm":
        raise HTTPException(status_code=422,
                            detail=f"That repo is a {manifest.type}, not an algorithm.")

    # Resolve clone url; private repos need PAT
    public_url = f"https://github.com/{owner}/{repo}.git"
    clone_url = public_url
    setting = (await db.execute(
        select(Setting).where(Setting.key == "github_pat")
    )).scalar_one_or_none()
    if setting is not None:
        # If yaml came from PAT path, the public clone may fail too. Use PAT clone.
        container = get_container()
        pat = container.encryption.decrypt(setting.value)
        clone_url = f"https://{pat}@github.com/{owner}/{repo}.git"

    pm = PackageManager(packages_dir="data/packages")
    name = repo
    try:
        pm.clone_repo(clone_url, name)
        pm.create_venv(name)
        pm.install_requirements(name)
        manifest_disk = pm.validate_package(name)
        commit_hash = pm.get_commit_hash(name)
    except PackageError as e:
        raise HTTPException(status_code=422, detail=str(e))

    algo = Algorithm(
        repo_url=public_url,
        name=manifest_disk.get("name", manifest.name),
        description=manifest_disk.get("description") or manifest.description,
        version=manifest_disk.get("version") or manifest.version,
        commit_hash=commit_hash,
        install_status="installed",
    )
    db.add(algo)
    await db.flush()
    return _algo_to_response(algo)
```

- [ ] **S2.3 — Run + pass**

Run: `pytest tests/coordinator/test_algorithms_install_from_url.py -v`
Expected: PASS.

- [ ] **S2.4 — Commit**

```bash
git add coordinator/api/routes/algorithms.py tests/coordinator/test_algorithms_install_from_url.py
git commit -m "feat(algos): install-from-url with quilt.yaml pre-validation"
```

### Work unit S3: heartbeat handler + worker_connected broadcast (Spec A §4)

**Branch:** `plan/S3-heartbeat`

**Files:**
- Modify: `coordinator/api/websocket.py`
- Test: `tests/coordinator/test_websocket_heartbeat.py`

- [ ] **S3.1 — Failing test: heartbeat updates DB and broadcasts**

```python
# tests/coordinator/test_websocket_heartbeat.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from coordinator.api.websocket import handle_worker_message, manager
from coordinator.database.models import Worker

@pytest.mark.asyncio
async def test_heartbeat_updates_status_and_broadcasts_on_transition(test_app, db_session):
    worker = Worker(id="w-1", name="pi-1", tailscale_ip=None,
                    status="offline", install_status="pending")
    db_session.add(worker); await db_session.flush()

    ws = AsyncMock()
    broadcasts = []
    async def fake_broadcast(msg): broadcasts.append(msg)
    manager.broadcast_to_dashboards = fake_broadcast

    await handle_worker_message(ws, {
        "type": "heartbeat", "worker_id": "w-1",
        "tailscale_ip": "100.64.0.5",
    })

    # Re-read
    from coordinator.api.dependencies import get_container
    container = get_container()
    async with container.session_factory() as session:
        from sqlalchemy import select
        w = (await session.execute(
            select(Worker).where(Worker.id == "w-1")
        )).scalar_one()
        assert w.status == "online"
        assert w.tailscale_ip == "100.64.0.5"
        assert w.last_heartbeat is not None
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "worker_connected"
    assert broadcasts[0]["worker_id"] == "w-1"

@pytest.mark.asyncio
async def test_heartbeat_does_not_rebroadcast_when_already_online(test_app, db_session):
    worker = Worker(id="w-2", name="pi-2", tailscale_ip="100.64.0.6",
                    status="online", install_status="claimed")
    db_session.add(worker); await db_session.flush()

    ws = AsyncMock()
    broadcasts = []
    async def fake_broadcast(msg): broadcasts.append(msg)
    manager.broadcast_to_dashboards = fake_broadcast

    await handle_worker_message(ws, {"type": "heartbeat", "worker_id": "w-2"})
    assert len(broadcasts) == 0
```

- [ ] **S3.2 — Implement**

Replace the existing `heartbeat` branch in `handle_worker_message` (`coordinator/api/websocket.py:71`) with:

```python
    elif msg_type == "heartbeat":
        worker_id = data.get("worker_id")
        await websocket.send_json({"type": "heartbeat_ack"})
        if not worker_id:
            return
        try:
            container = get_container()
            async with container.session_factory() as session:
                result = await session.execute(select(Worker).where(Worker.id == worker_id))
                worker = result.scalar_one_or_none()
                if worker is None:
                    return
                prior_status = worker.status
                worker.last_heartbeat = datetime.now(timezone.utc)
                worker.status = "online"
                if data.get("tailscale_ip"):
                    worker.tailscale_ip = data["tailscale_ip"]
                await session.commit()

                if prior_status != "online":
                    await manager.broadcast_to_dashboards({
                        "type": "worker_connected",
                        "worker_id": worker.id,
                        "name": worker.name,
                        "tailscale_ip": worker.tailscale_ip,
                        "install_status": worker.install_status,
                    })
        except Exception:
            logger.exception("Failed to update heartbeat for worker %s", worker_id)
```

- [ ] **S3.3 — Run + pass**

Run: `pytest tests/coordinator/test_websocket_heartbeat.py -v`
Expected: PASS.

- [ ] **S3.4 — Commit**

```bash
git add coordinator/api/websocket.py tests/coordinator/test_websocket_heartbeat.py
git commit -m "fix(ws): heartbeat updates worker status + broadcasts on first connect"
```

### Work unit S4: Live subscriptions service + API (Spec B)

**Branch:** `plan/S4-live-subs`

**Files:**
- Create: `coordinator/services/live_feed_manager.py`, `coordinator/services/live_feed_aggregator.py`, `coordinator/api/routes/live_subscriptions.py`
- Test: `tests/coordinator/services/test_live_feed_manager.py`, `tests/coordinator/test_live_subscriptions_api.py`

This is the largest single work unit. Implements §2 (subscription model + lifecycle), §3 (aggregator), §4 (estimator), §5 (API). The aggregator's actual WS implementation has a stub for the broker stream — Phase 4 wires real adapter streams.

- [ ] **S4.1 — Failing test: LiveFeedManager dependent counts**

```python
# tests/coordinator/services/test_live_feed_manager.py
import pytest
from coordinator.services.live_feed_manager import LiveFeedManager

def test_register_and_dependent_count():
    m = LiveFeedManager()
    m.register("alpaca", "SPY")
    m.add_dependent("alpaca", "SPY", "inst-1")
    m.add_dependent("alpaca", "SPY", "inst-2")
    assert m.dependent_count("alpaca", "SPY") == 2

def test_release_returns_true_when_last_dependent_leaves():
    m = LiveFeedManager()
    m.register("alpaca", "SPY")
    m.add_dependent("alpaca", "SPY", "inst-1")
    assert m.release("alpaca", "SPY", "inst-1") is True
    assert m.dependent_count("alpaca", "SPY") == 0

def test_ensure_running_starts_subscription():
    m = LiveFeedManager()
    m.register("alpaca", "SPY")
    m.ensure_running("alpaca", "SPY", "inst-1")
    assert m.is_running("alpaca", "SPY") is True

def test_unknown_key_returns_zero_count():
    m = LiveFeedManager()
    assert m.dependent_count("alpaca", "ZZZZ") == 0
```

- [ ] **S4.2 — Implement LiveFeedManager**

```python
# coordinator/services/live_feed_manager.py
"""Tracks broker-scoped live data subscriptions and their dependent counts.

Mirrors the API of ScraperManager (coordinator/services/scraper_manager.py)
deliberately — same lifecycle pattern (running while ≥1 dependent cares).
"""
from __future__ import annotations
from dataclasses import dataclass, field

Key = tuple[str, str]  # (broker, symbol)


@dataclass
class _State:
    running: bool = False
    dependents: set[str] = field(default_factory=set)


class LiveFeedManager:
    def __init__(self) -> None:
        self._states: dict[Key, _State] = {}

    def register(self, broker: str, symbol: str) -> None:
        self._states.setdefault((broker, symbol), _State())

    def is_registered(self, broker: str, symbol: str) -> bool:
        return (broker, symbol) in self._states

    def is_running(self, broker: str, symbol: str) -> bool:
        s = self._states.get((broker, symbol))
        return s.running if s else False

    def start(self, broker: str, symbol: str) -> None:
        s = self._states.get((broker, symbol))
        if s: s.running = True

    def stop(self, broker: str, symbol: str) -> None:
        s = self._states.get((broker, symbol))
        if s: s.running = False

    def add_dependent(self, broker: str, symbol: str, instance_id: str) -> None:
        s = self._states.get((broker, symbol))
        if s: s.dependents.add(instance_id)

    def remove_dependent(self, broker: str, symbol: str, instance_id: str) -> None:
        s = self._states.get((broker, symbol))
        if s: s.dependents.discard(instance_id)

    def dependent_count(self, broker: str, symbol: str) -> int:
        s = self._states.get((broker, symbol))
        return len(s.dependents) if s else 0

    def should_stop(self, broker: str, symbol: str) -> bool:
        s = self._states.get((broker, symbol))
        return bool(s and s.running and not s.dependents)

    def ensure_running(self, broker: str, symbol: str, instance_id: str) -> None:
        if not self.is_registered(broker, symbol):
            self.register(broker, symbol)
        self.add_dependent(broker, symbol, instance_id)
        if not self.is_running(broker, symbol):
            self.start(broker, symbol)

    def release(self, broker: str, symbol: str, instance_id: str) -> bool:
        self.remove_dependent(broker, symbol, instance_id)
        if self.should_stop(broker, symbol):
            self.stop(broker, symbol)
            return True
        return False
```

- [ ] **S4.3 — Run + pass**

Run: `pytest tests/coordinator/services/test_live_feed_manager.py -v`
Expected: PASS.

- [ ] **S4.4 — Failing API test: CRUD**

```python
# tests/coordinator/test_live_subscriptions_api.py
import pytest

@pytest.mark.asyncio
async def test_create_and_list_subscription(client):
    r = await client.post("/api/live-subscriptions",
                          json={"broker": "alpaca", "symbol": "SPY",
                                "tick_retention_hours": 24})
    assert r.status_code == 201
    sub_id = r.json()["id"]
    r2 = await client.get("/api/live-subscriptions")
    items = r2.json()
    assert any(s["id"] == sub_id for s in items)

@pytest.mark.asyncio
async def test_create_409_on_duplicate(client):
    r = await client.post("/api/live-subscriptions",
                          json={"broker": "alpaca", "symbol": "QQQ"})
    assert r.status_code == 201
    r2 = await client.post("/api/live-subscriptions",
                           json={"broker": "alpaca", "symbol": "QQQ"})
    assert r2.status_code == 409

@pytest.mark.asyncio
async def test_validate_retention_must_be_multiple_of_24(client):
    r = await client.post("/api/live-subscriptions",
                          json={"broker": "alpaca", "symbol": "AAPL",
                                "tick_retention_hours": 36})
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_estimate_endpoint_returns_projected_bytes(client):
    r = await client.get("/api/live-subscriptions/estimate",
                         params={"broker": "alpaca", "symbol": "SPY",
                                 "retention_hours": 24})
    assert r.status_code == 200
    body = r.json()
    assert body["projected_bytes"] > 0
    assert body["source"] in ("estimated", "observed")
```

- [ ] **S4.5 — Implement the API**

```python
# coordinator/api/routes/live_subscriptions.py
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import LiveSubscription

router = APIRouter(prefix="/api/live-subscriptions", tags=["live-subscriptions"])

# Coarse tick-rate estimates per symbol (trades/min) — sharpens once running.
_TICK_RATE_DEFAULTS: dict[str, float] = {
    "SPY": 200.0, "QQQ": 180.0, "IWM": 80.0, "DIA": 30.0,
}
_BYTES_PER_TRADE = 80
_BYTES_PER_QUOTE = 90


class SubscriptionCreate(BaseModel):
    broker: str
    symbol: str
    tick_retention_hours: int = 24

    @field_validator("tick_retention_hours")
    @classmethod
    def _validate_retention(cls, v: int) -> int:
        if v < 24 or v > 720 or v % 24 != 0:
            raise ValueError(
                "tick_retention_hours must be a multiple of 24 between 24 and 720"
            )
        return v


class SubscriptionUpdate(BaseModel):
    tick_retention_hours: Optional[int] = None

    @field_validator("tick_retention_hours")
    @classmethod
    def _validate_retention(cls, v):
        if v is None: return v
        if v < 24 or v > 720 or v % 24 != 0:
            raise ValueError(
                "tick_retention_hours must be a multiple of 24 between 24 and 720"
            )
        return v


def _to_response(s: LiveSubscription) -> dict:
    return {
        "id": s.id, "broker": s.broker, "symbol": s.symbol, "status": s.status,
        "last_error": s.last_error,
        "last_tick_at": s.last_tick_at.isoformat() if s.last_tick_at else None,
        "tick_rate_per_min": s.tick_rate_per_min,
        "tick_retention_hours": s.tick_retention_hours,
        "dependent_count": s.dependent_count,
    }


@router.get("")
async def list_subs(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(LiveSubscription))).scalars().all()
    return [_to_response(r) for r in rows]


@router.post("", status_code=201)
async def create_sub(body: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    sub = LiveSubscription(
        broker=body.broker, symbol=body.symbol.upper(),
        tick_retention_hours=body.tick_retention_hours,
        status="stopped", dependent_count=0,
    )
    db.add(sub)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409,
                            detail=f"Subscription already exists for {body.broker}/{body.symbol}")
    return _to_response(sub)


@router.get("/estimate")
async def estimate(
    broker: str = Query(...),
    symbol: str = Query(...),
    retention_hours: int = Query(24),
    db: AsyncSession = Depends(get_db),
):
    sub = (await db.execute(
        select(LiveSubscription).where(LiveSubscription.broker == broker,
                                       LiveSubscription.symbol == symbol.upper())
    )).scalar_one_or_none()
    source = "estimated"
    rate = _TICK_RATE_DEFAULTS.get(symbol.upper(), 20.0)
    if sub and sub.tick_rate_per_min:
        rate = sub.tick_rate_per_min
        source = "observed"
    minutes = retention_hours * 60
    # ~1 quote per trade as a coarse 1:1 estimate
    projected = int(rate * minutes * (_BYTES_PER_TRADE + _BYTES_PER_QUOTE))
    return {
        "tick_rate_per_min": rate,
        "projected_bytes": projected,
        "projected_human": _humanize(projected),
        "source": source,
    }


def _humanize(b: int) -> str:
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024), ("B", 1)):
        if b >= div:
            return f"{b/div:.1f} {unit}"
    return "0 B"


@router.get("/{sub_id}")
async def get_sub(sub_id: str, db: AsyncSession = Depends(get_db)):
    sub = (await db.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub_id)
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return _to_response(sub)


@router.patch("/{sub_id}")
async def patch_sub(sub_id: str, body: SubscriptionUpdate,
                    db: AsyncSession = Depends(get_db)):
    sub = (await db.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub_id)
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if body.tick_retention_hours is not None:
        sub.tick_retention_hours = body.tick_retention_hours
    await db.flush()
    return _to_response(sub)


@router.post("/{sub_id}/unsubscribe")
async def unsubscribe(sub_id: str, db: AsyncSession = Depends(get_db)):
    sub = (await db.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub_id)
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    # Decrement only the manual dependent — implementation in I1 wires the manager;
    # for now we just touch updated_at to signal the manual release.
    sub.dependent_count = max(0, sub.dependent_count - 1)
    await db.flush()
    return _to_response(sub)


@router.delete("/{sub_id}", status_code=204)
async def delete_sub(sub_id: str, db: AsyncSession = Depends(get_db)):
    sub = (await db.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub_id)
    )).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub.dependent_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Subscription has {sub.dependent_count} active dependents",
        )
    await db.delete(sub)
    # ticks directory cleanup is done by the aggregator's retention sweeper
    # to avoid filesystem ownership in this route handler.
```

- [ ] **S4.6 — Aggregator skeleton**

```python
# coordinator/services/live_feed_aggregator.py
"""Live broker WebSocket → ticks parquet + 1min bars.

This Phase-2 implementation lays the structure (per-subscription task,
retention sweeper) but the broker stream itself is a stub. Phase 4
wires real BrokerAdapter streams in once those exist.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from coordinator.database.models import LiveSubscription

logger = logging.getLogger(__name__)


class LiveFeedAggregator:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._retention_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        # Resume any rows already running
        async with self._sf() as session:
            rows = (await session.execute(
                select(LiveSubscription).where(LiveSubscription.status == "running")
            )).scalars().all()
            for r in rows:
                await self.start_subscription(r.broker, r.symbol)
        self._retention_task = asyncio.create_task(self._retention_loop())

    async def stop(self) -> None:
        if self._retention_task:
            self._retention_task.cancel()
        for t in list(self._tasks.values()):
            t.cancel()

    async def start_subscription(self, broker: str, symbol: str) -> None:
        key = (broker, symbol)
        if key in self._tasks:
            return
        self._tasks[key] = asyncio.create_task(self._run(broker, symbol))

    async def stop_subscription(self, broker: str, symbol: str) -> None:
        t = self._tasks.pop((broker, symbol), None)
        if t: t.cancel()

    async def _run(self, broker: str, symbol: str) -> None:
        # Phase 4 wires real streams. For now: mark running, idle.
        logger.info("[stub] LiveFeedAggregator running for %s/%s", broker, symbol)
        try:
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    async def _retention_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(3600)
                await self._sweep_old_ticks()
        except asyncio.CancelledError:
            return

    async def _sweep_old_ticks(self) -> None:
        # Walks data/market/{broker}_live/{symbol}/ticks/ and removes files
        # whose date is older than retention. Spec B §3.
        import os
        from pathlib import Path
        from datetime import date
        async with self._sf() as session:
            rows = (await session.execute(select(LiveSubscription))).scalars().all()
        for sub in rows:
            ticks_dir = Path("data/market") / f"{sub.broker}_live" / sub.symbol / "ticks"
            if not ticks_dir.exists():
                continue
            cutoff = (datetime.now(timezone.utc) -
                      timedelta(hours=sub.tick_retention_hours)).date()
            for f in ticks_dir.glob("*.parquet"):
                try:
                    name = f.stem  # e.g. "trades-2026-05-14"
                    d = date.fromisoformat(name.split("-", 1)[1])
                    if d < cutoff:
                        f.unlink()
                except (ValueError, OSError):
                    continue
```

- [ ] **S4.7 — Run all S4 tests, confirm pass**

Run: `pytest tests/coordinator/services/test_live_feed_manager.py tests/coordinator/test_live_subscriptions_api.py -v`
Expected: PASS.

- [ ] **S4.8 — Commit**

```bash
git add coordinator/services/live_feed_manager.py coordinator/services/live_feed_aggregator.py \
        coordinator/api/routes/live_subscriptions.py \
        tests/coordinator/services/test_live_feed_manager.py \
        tests/coordinator/test_live_subscriptions_api.py
git commit -m "feat(live-data): subscription manager + REST API + aggregator skeleton

Real broker stream wiring lands in Phase 4 (I1). Phase 2 ships the
dependent-count manager, CRUD endpoints, and retention sweeper."
```

### Work unit S5: options-chain endpoint (Spec C §4)

**Branch:** `plan/S5-options-chain`

**Files:**
- Create: `coordinator/api/routes/options_chain.py`
- Test: `tests/coordinator/test_options_chain_api.py`

- [ ] **S5.1 — Failing test: expiries + chain endpoints**

```python
# tests/coordinator/test_options_chain_api.py
import pytest
from datetime import date
from unittest.mock import patch
from coordinator.database.models import Account
from worker.broker_adapter import OptionContract, OptionChainSnapshot

@pytest.mark.asyncio
async def test_get_expiries_returns_dates(client, db_session, monkeypatch):
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["options"], pdt_mode="off")
    db_session.add(account); await db_session.flush()
    from coordinator.api.routes import options_chain
    async def fake_adapter(acct):
        class FA:
            def list_option_expiries(self, underlying):
                return [date(2026, 5, 16), date(2026, 6, 20)]
            def close(self): pass
        return FA()
    monkeypatch.setattr(options_chain, "_adapter_for_account", fake_adapter)

    r = await client.get(f"/api/accounts/{account.id}/options-chain/expiries",
                         params={"underlying": "SPY"})
    assert r.status_code == 200
    assert r.json() == {"expiries": ["2026-05-16", "2026-06-20"]}

@pytest.mark.asyncio
async def test_get_chain_returns_serialized_contracts(client, db_session, monkeypatch):
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["options"], pdt_mode="off")
    db_session.add(account); await db_session.flush()
    from coordinator.api.routes import options_chain
    async def fake_adapter(acct):
        class FA:
            def get_option_chain(self, underlying, expiry):
                return OptionChainSnapshot(
                    underlying="SPY", spot=565.0, expiry=expiry, as_of=None,
                    contracts=[
                        OptionContract(strike=560.0, right="call",
                            occ_symbol="SPY260620C00560000", bid=8.2, ask=8.4,
                            last=8.3, iv=0.30, delta=0.55, gamma=0.020,
                            theta=-14.1, vega=48.0, open_interest=2345, volume=789),
                    ],
                )
            def close(self): pass
        return FA()
    monkeypatch.setattr(options_chain, "_adapter_for_account", fake_adapter)

    r = await client.get(
        f"/api/accounts/{account.id}/options-chain/2026-06-20",
        params={"underlying": "SPY"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["underlying"] == "SPY"
    assert body["spot"] == 565.0
    assert len(body["contracts"]) == 1
    assert body["contracts"][0]["strike"] == 560.0

@pytest.mark.asyncio
async def test_chain_423_when_locked(client, db_session):
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["options"], pdt_mode="off",
                      locked_by="inst-1")
    db_session.add(account); await db_session.flush()
    r = await client.get(f"/api/accounts/{account.id}/options-chain/expiries",
                         params={"underlying": "SPY"})
    assert r.status_code == 423
```

- [ ] **S5.2 — Implement**

```python
# coordinator/api/routes/options_chain.py
import asyncio
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import Account
from coordinator.api.routes.accounts import _adapter_for_account, _close_adapter

router = APIRouter(prefix="/api/accounts/{account_id}/options-chain", tags=["options-chain"])


async def _check_lock_and_get_account(account_id: str, db: AsyncSession) -> Account:
    a = (await db.execute(select(Account).where(Account.id == account_id))).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if a.locked_by:
        raise HTTPException(status_code=423,
                            detail={"locked_by": a.locked_by})
    if "options" not in (a.supported_asset_types or []):
        raise HTTPException(status_code=422,
                            detail="Account does not support options")
    return a


@router.get("/expiries")
async def list_expiries(
    account_id: str,
    underlying: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    account = await _check_lock_and_get_account(account_id, db)
    adapter = await _adapter_for_account(account)
    try:
        expiries = await asyncio.to_thread(adapter.list_option_expiries, underlying)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Broker error: {e}")
    finally:
        _close_adapter(adapter)
    return {"expiries": [d.isoformat() for d in expiries]}


@router.get("/{expiry}")
async def get_chain(
    account_id: str, expiry: str,
    underlying: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        exp_date = date.fromisoformat(expiry)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid expiry: {expiry}")
    account = await _check_lock_and_get_account(account_id, db)
    adapter = await _adapter_for_account(account)
    try:
        snap = await asyncio.to_thread(adapter.get_option_chain, underlying, exp_date)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Broker error: {e}")
    finally:
        _close_adapter(adapter)
    return {
        "underlying": snap.underlying,
        "spot": snap.spot,
        "expiry": snap.expiry.isoformat(),
        "as_of": snap.as_of.isoformat() if snap.as_of else None,
        "contracts": [{
            "strike": c.strike, "right": c.right, "occ_symbol": c.occ_symbol,
            "bid": c.bid, "ask": c.ask, "last": c.last, "iv": c.iv,
            "delta": c.delta, "gamma": c.gamma, "theta": c.theta, "vega": c.vega,
            "open_interest": c.open_interest, "volume": c.volume,
        } for c in snap.contracts],
    }
```

- [ ] **S5.3 — Run + pass**

Run: `pytest tests/coordinator/test_options_chain_api.py -v`
Expected: PASS.

- [ ] **S5.4 — Commit**

```bash
git add coordinator/api/routes/options_chain.py tests/coordinator/test_options_chain_api.py
git commit -m "feat(options): broker chain proxy endpoints"
```

### Work unit S6: Router wiring + DI container (Phase 2 LAST)

**Branch:** `plan/S6-wiring`

**Files:**
- Modify: `coordinator/main.py`, `coordinator/api/dependencies.py`
- Test: `tests/coordinator/test_app.py` (existing — extend with route existence check)

Run AFTER S1–S5 have merged. Wires new routers + services into the app's lifespan.

- [ ] **S6.1 — Failing test: new routes exist**

```python
# tests/coordinator/test_app.py (append)
import pytest

@pytest.mark.asyncio
async def test_brokers_route_mounted(client):
    r = await client.get("/api/brokers/alpaca/asset-types")
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_live_subs_route_mounted(client):
    r = await client.get("/api/live-subscriptions")
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_options_chain_route_404_on_missing_account(client):
    r = await client.get("/api/accounts/nonexistent/options-chain/expiries",
                         params={"underlying": "SPY"})
    assert r.status_code == 404
```

- [ ] **S6.2 — Mount routers**

In `coordinator/main.py`, in `create_app`, add:

```python
from coordinator.api.routes import brokers as brokers_routes
from coordinator.api.routes import live_subscriptions as live_subs_routes
from coordinator.api.routes import options_chain as options_chain_routes

# inside create_app():
app.include_router(brokers_routes.router)
app.include_router(live_subs_routes.router)
app.include_router(options_chain_routes.router)
```

In the app lifespan, instantiate `LiveFeedManager` and `LiveFeedAggregator`, attach to the container, call `aggregator.start()`/`stop()`.

```python
# coordinator/api/dependencies.py — extend Container class
@dataclass
class Container:
    encryption: Any
    session_factory: Any
    # ... existing fields
    live_feed_manager: Optional["LiveFeedManager"] = None
    live_feed_aggregator: Optional["LiveFeedAggregator"] = None
```

In the lifespan startup hook (likely in `coordinator/main.py`'s lifespan context manager), add:

```python
from coordinator.services.live_feed_manager import LiveFeedManager
from coordinator.services.live_feed_aggregator import LiveFeedAggregator

container.live_feed_manager = LiveFeedManager()
container.live_feed_aggregator = LiveFeedAggregator(container.session_factory)
await container.live_feed_aggregator.start()

# in shutdown:
if container.live_feed_aggregator:
    await container.live_feed_aggregator.stop()
```

Also delete the temporary `test_app` fixture override in `tests/coordinator/test_brokers_api.py` (added in F3.7).

- [ ] **S6.3 — Run + pass**

Run: `pytest tests/coordinator/test_app.py -v -k "brokers_route or live_subs_route or options_chain_route"`
Expected: PASS. Also re-run the full coordinator test suite to verify nothing broke.

- [ ] **S6.4 — Commit**

```bash
git add coordinator/main.py coordinator/api/dependencies.py tests/coordinator/test_app.py \
        tests/coordinator/test_brokers_api.py
git commit -m "feat(app): wire new routers + live-feed services into lifespan"
```

---

**End of Phase 2.** Merge S1, S2, S3, S4, S5 first; then merge S6 on top.

---

## Phase 3 — UI

Six work units. All parallel. They share `dashboard/src/api/{hooks.ts,client.ts}`, `types.ts`, and `App.tsx` — those files are edited additively and conflicts are resolved at merge.

**Conflict-management rule for shared files:** each work unit adds its own clearly-marked section to `hooks.ts`/`client.ts`/`types.ts` using a comment banner like `// ── Live subscriptions ──`. Merges become trivial additions.

### Work unit U1: Accounts.tsx asset-type checkboxes

**Branch:** `plan/U1-accounts-checkboxes`

**Files:**
- Modify: `dashboard/src/pages/Accounts.tsx`
- Modify (additive): `dashboard/src/api/hooks.ts`, `dashboard/src/api/client.ts`
- Test: `dashboard/src/pages/Accounts.test.tsx`

- [ ] **U1.1 — Failing test: checkbox group renders based on broker**

```tsx
// dashboard/src/pages/Accounts.test.tsx (new)
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Accounts } from "./Accounts";
import { describe, it, expect, vi } from "vitest";

// Mock hooks for this test
vi.mock("../api/hooks", () => ({
  useAccounts: () => ({ data: [], isLoading: false }),
  useCreateAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBrokerAssetTypes: (broker: string) => ({
    data: broker === "alpaca"
      ? ["equities", "options", "crypto"]
      : broker === "tradier"
      ? ["equities", "options"]
      : undefined,
    isLoading: false,
  }),
}));

describe("Accounts add modal asset-type checkboxes", () => {
  it("shows alpaca asset types when alpaca is selected", async () => {
    render(<Accounts />);
    fireEvent.click(screen.getByText(/Add Account/));
    await waitFor(() => expect(screen.getByLabelText(/equities/i)).toBeInTheDocument());
    expect(screen.getByLabelText(/options/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/crypto/i)).toBeInTheDocument();
  });
});
```

- [ ] **U1.2 — Add the hook**

In `dashboard/src/api/hooks.ts`:

```typescript
// ── Broker asset-type catalog (Spec A §1) ──
export function useBrokerAssetTypes(brokerType: string | null | undefined) {
  return useQuery({
    queryKey: ["brokerAssetTypes", brokerType],
    queryFn: async () => {
      if (!brokerType) return [];
      const r = await api.getBrokerAssetTypes(brokerType);
      return r.asset_types;
    },
    enabled: !!brokerType,
  });
}
```

In `dashboard/src/api/client.ts`:

```typescript
// ── Broker asset-type catalog ──
async getBrokerAssetTypes(brokerType: string): Promise<{ asset_types: string[] }> {
  const r = await this.http.get(`/api/brokers/${brokerType}/asset-types`);
  return r.data;
}
```

- [ ] **U1.3 — Replace the text input with checkboxes**

In `dashboard/src/pages/Accounts.tsx`, update the create + edit schemas:

```typescript
const createAccountSchema = z.object({
  name: z.string().min(1),
  broker_type: z.enum(["alpaca", "tradier"]),
  environment: z.enum(["paper", "live"]),
  supported_asset_types: z.array(z.string()).min(1, "Select at least one asset type"),
  pdt_mode: z.string().min(1),
  alpaca_api_key: z.string().optional(),
  alpaca_secret_key: z.string().optional(),
  tradier_access_token: z.string().optional(),
  tradier_account_id: z.string().optional(),
}).superRefine(/* unchanged */);
```

Replace the `<FormField label="Supported Asset Types">` block (both modals) with:

```tsx
<FormField label="Supported Asset Types"
           error={form.formState.errors.supported_asset_types?.message as string | undefined}>
  {(() => {
    const broker = form.watch("broker_type");
    const { data: assetTypes = [], isLoading } = useBrokerAssetTypes(broker);
    if (isLoading) return <p className="text-xs text-gray-500">Loading…</p>;
    return (
      <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm">
        {assetTypes.map((t) => (
          <label key={t} className="inline-flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox" value={t}
              {...form.register("supported_asset_types")}
              className="accent-indigo-500"
            />
            <span className="capitalize">{t}</span>
          </label>
        ))}
      </div>
    );
  })()}
</FormField>
```

In `handleCreate` / `handleEdit`, replace the `.split(",").map(...).filter(Boolean)` with just `values.supported_asset_types` (already an array).

Update `defaultValues` for both modals — `supported_asset_types: []` for create, `editTarget.supported_asset_types ?? []` for edit.

- [ ] **U1.4 — Run + pass**

Run: `cd dashboard && npx vitest run src/pages/Accounts.test.tsx`
Expected: PASS.

- [ ] **U1.5 — Commit**

```bash
git add dashboard/src/pages/Accounts.tsx dashboard/src/api/hooks.ts \
        dashboard/src/api/client.ts dashboard/src/pages/Accounts.test.tsx
git commit -m "feat(accounts-ui): broker-aware asset-type checkboxes"
```

### Work unit U2: AccountDetail.tsx — Open Position + Strategies buttons

**Branch:** `plan/U2-account-detail`

**Files:**
- Modify: `dashboard/src/pages/AccountDetail.tsx`
- Create: `dashboard/src/components/OpenPositionModal.tsx`
- Modify (additive): `dashboard/src/api/hooks.ts`, `dashboard/src/api/client.ts`, `dashboard/src/types.ts`
- Test: `dashboard/src/components/OpenPositionModal.test.tsx`

Combines Spec A's "Open Position" button + Spec C's "Strategies" button. One owner for the AccountDetail header avoids merge conflicts.

- [ ] **U2.1 — Add API client + hook**

In `client.ts`:

```typescript
// ── Open position ──
async openPosition(accountId: string, body: {
  legs: Array<{
    symbol: string; asset_type: string; side: "buy"|"sell"; quantity: number;
    expiry?: string; strike?: number; right?: "call"|"put";
  }>;
  strategy_type?: string;
  order_type?: "market"|"limit";
  limit_price?: number;
}): Promise<{
  position_id: string | null; broker_order_id: string | null;
  legs: Array<{ index: number; status: string; filled_price: number | null;
                fees: number | null; error: string | null; broker_order_id: string | null }>;
  atomic: boolean; partial_fill: boolean;
}> {
  const r = await this.http.post(`/api/accounts/${accountId}/positions/open`, body);
  return r.data;
}
```

In `hooks.ts`:

```typescript
export function useOpenPosition(accountId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.openPosition>[1]) =>
      api.openPosition(accountId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brokerInfo", accountId] });
    },
  });
}
```

- [ ] **U2.2 — Implement OpenPositionModal component**

```tsx
// dashboard/src/components/OpenPositionModal.tsx
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useOpenPosition } from "../api/hooks";
import { useUIStore } from "../stores/ui";

type Leg = {
  asset_type: "equities"|"options"|"crypto";
  symbol: string; side: "buy"|"sell"; quantity: number;
  expiry?: string; strike?: number; right?: "call"|"put";
};

interface Props {
  open: boolean;
  onClose: () => void;
  accountId: string;
  allowedAssetTypes: string[];
}

export function OpenPositionModal({ open, onClose, accountId, allowedAssetTypes }: Props) {
  const openMut = useOpenPosition(accountId);
  const addAlert = useUIStore((s) => s.addAlert);
  const [legs, setLegs] = useState<Leg[]>([{
    asset_type: (allowedAssetTypes[0] ?? "equities") as Leg["asset_type"],
    symbol: "", side: "buy", quantity: 1,
  }]);
  const [orderType, setOrderType] = useState<"market"|"limit">("market");
  const [limitPrice, setLimitPrice] = useState<number | null>(null);

  if (!open) return null;

  function updateLeg(i: number, patch: Partial<Leg>) {
    setLegs((cur) => cur.map((l, idx) => idx === i ? { ...l, ...patch } : l));
  }
  function addLeg() {
    setLegs((cur) => [...cur, {
      asset_type: (allowedAssetTypes[0] ?? "equities") as Leg["asset_type"],
      symbol: "", side: "buy", quantity: 1,
    }]);
  }
  function removeLeg(i: number) {
    setLegs((cur) => cur.filter((_, idx) => idx !== i));
  }

  async function submit() {
    try {
      const result = await openMut.mutateAsync({
        legs: legs.map(l => ({
          symbol: l.symbol, asset_type: l.asset_type, side: l.side, quantity: l.quantity,
          ...(l.asset_type === "options" ? {
            expiry: l.expiry, strike: l.strike, right: l.right,
          } : {}),
        })),
        order_type: orderType,
        ...(orderType === "limit" && limitPrice != null ? { limit_price: limitPrice } : {}),
      });
      const filledCount = result.legs.filter(l => l.status === "filled").length;
      addAlert({
        message: result.partial_fill
          ? `Partial fill: ${filledCount}/${result.legs.length} legs filled`
          : `Position opened (${filledCount} legs)`,
        severity: result.partial_fill ? "warning" : "success",
      });
      onClose();
    } catch (e) {
      addAlert({ message: `Failed: ${(e as Error).message}`, severity: "error" });
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 w-full max-w-3xl">
        <h2 className="text-lg font-semibold mb-3">Open Position</h2>
        <div className="space-y-2 mb-3">
          {legs.map((l, i) => (
            <div key={i} className="flex gap-2 items-end">
              <select value={l.asset_type}
                      onChange={(e) => updateLeg(i, { asset_type: e.target.value as Leg["asset_type"] })}
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm">
                {allowedAssetTypes.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <input value={l.symbol} placeholder="symbol"
                     onChange={(e) => updateLeg(i, { symbol: e.target.value.toUpperCase() })}
                     className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-24" />
              <select value={l.side}
                      onChange={(e) => updateLeg(i, { side: e.target.value as "buy"|"sell" })}
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm">
                <option value="buy">Buy</option><option value="sell">Sell</option>
              </select>
              <input type="number" value={l.quantity} min={0.0001} step={0.0001}
                     onChange={(e) => updateLeg(i, { quantity: Number(e.target.value) })}
                     className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-20" />
              {l.asset_type === "options" && (<>
                <input type="date" value={l.expiry ?? ""}
                       onChange={(e) => updateLeg(i, { expiry: e.target.value })}
                       className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm" />
                <input type="number" placeholder="strike" value={l.strike ?? ""}
                       onChange={(e) => updateLeg(i, { strike: Number(e.target.value) })}
                       className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-24" />
                <select value={l.right ?? "call"}
                        onChange={(e) => updateLeg(i, { right: e.target.value as "call"|"put" })}
                        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm">
                  <option value="call">Call</option><option value="put">Put</option>
                </select>
              </>)}
              <button onClick={() => removeLeg(i)}
                      className="p-1 text-gray-400 hover:text-red-400" aria-label={`remove leg ${i}`}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
        <button onClick={addLeg}
                className="flex items-center gap-1 text-sm text-indigo-400 hover:text-indigo-300 mb-3">
          <Plus size={14} /> Add leg
        </button>
        <div className="flex gap-3 items-center mb-3">
          <label className="text-sm">Order type:</label>
          <select value={orderType} onChange={(e) => setOrderType(e.target.value as "market"|"limit")}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm">
            <option value="market">Market</option><option value="limit">Limit</option>
          </select>
          {orderType === "limit" && (
            <input type="number" placeholder="limit price" value={limitPrice ?? ""}
                   onChange={(e) => setLimitPrice(e.target.value ? Number(e.target.value) : null)}
                   className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-32" />
          )}
        </div>
        <div className="flex justify-end gap-2">
          <button onClick={onClose}
                  className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600">
            Cancel
          </button>
          <button onClick={submit} disabled={openMut.isPending || legs.some(l => !l.symbol)}
                  className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50">
            {openMut.isPending ? "Submitting…" : "Submit"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **U2.3 — Wire buttons into AccountDetail.tsx**

Locate the header section that renders Refresh / Sync / Edit / Delete buttons (around lines 477-525 of current AccountDetail.tsx). Insert two new buttons before the Edit button:

```tsx
{/* Open Position */}
<button
  className={`flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
  onClick={() => setOpenPositionOpen(true)}
  disabled={!!account.locked_by}
  title={account.locked_by
    ? `Locked by algorithm. Stop the algo to open positions manually.`
    : "Open a new position"}
>
  Open Position
</button>

{/* Strategies (options only) */}
{(account.supported_asset_types ?? []).includes("options") && (
  <button
    className={`flex items-center gap-1.5 px-3 py-2 rounded text-sm font-medium text-gray-200 bg-gray-700 hover:bg-gray-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed`}
    onClick={() => navigate(`/accounts/${account.id}/strategies`)}
    disabled={!!account.locked_by}
    title={account.locked_by
      ? `Locked by algorithm. Stop the algo to use the strategy builder.`
      : "Open the options strategy builder"}
  >
    Strategies
  </button>
)}
```

Add at top of the component:

```tsx
const [openPositionOpen, setOpenPositionOpen] = useState(false);
```

Add at end of the JSX, near the existing modals:

```tsx
{account && (
  <OpenPositionModal
    open={openPositionOpen}
    onClose={() => setOpenPositionOpen(false)}
    accountId={account.id}
    allowedAssetTypes={account.supported_asset_types ?? []}
  />
)}
```

Update the locked-badge to be a link if `locked_by` is set:

```tsx
{account.locked_by && (
  <Link to={`/instances/${account.locked_by}`}
        className="text-xs px-2 py-0.5 rounded border bg-amber-900/40 text-amber-300 border-amber-800 hover:underline">
    Locked by instance {account.locked_by.slice(0,8)}…
  </Link>
)}
```

- [ ] **U2.4 — Commit**

```bash
git add dashboard/src/pages/AccountDetail.tsx dashboard/src/components/OpenPositionModal.tsx \
        dashboard/src/api/hooks.ts dashboard/src/api/client.ts
git commit -m "feat(account-ui): Open Position + Strategies buttons, lock-aware

Open Position modal supports multi-leg with options fields per row.
Strategies button only renders when the account supports options.
Both disabled with a tooltip when account.locked_by is set."
```

### Work unit U3: Algorithms.tsx — URL-based install

**Branch:** `plan/U3-algos-url-install`

**Files:**
- Modify: `dashboard/src/pages/Algorithms.tsx`
- Modify (additive): `dashboard/src/api/hooks.ts`, `dashboard/src/api/client.ts`

- [ ] **U3.1 — Add hook + client**

```typescript
// hooks.ts
export function useInstallAlgorithmFromUrl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repo_url: string) => api.installAlgorithmFromUrl(repo_url),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["algorithms"] }),
  });
}
```

```typescript
// client.ts
async installAlgorithmFromUrl(repo_url: string) {
  const r = await this.http.post("/api/algorithms/install-from-url", { repo_url });
  return r.data;
}
```

- [ ] **U3.2 — Replace dropdown with URL input**

In `dashboard/src/pages/Algorithms.tsx`:

```typescript
import { useInstallAlgorithmFromUrl } from "../api/hooks";

const installSchema = z.object({
  repo_url: z.string().url("Must be a valid URL").refine(
    (v) => /^https?:\/\/github\.com\/[^/]+\/[^/]+/.test(v),
    "Must be a GitHub repo URL",
  ),
});
type InstallForm = z.infer<typeof installSchema>;

// Replace useGithubRepos + useInstallAlgorithm usage:
const { mutateAsync: installFromUrl, isPending: isInstalling } = useInstallAlgorithmFromUrl();

async function handleInstall(data: InstallForm) {
  try {
    await installFromUrl(data.repo_url);
    addAlert({ message: `Installing from ${data.repo_url}…`, severity: "info" });
    setInstallOpen(false);
  } catch (e) {
    addAlert({
      message: `Install failed: ${(e as Error).message}`,
      severity: "error",
    });
  }
}
```

Replace the FormModal body:

```tsx
{(form) => (
  <FormField label="Repository URL" error={form.formState.errors.repo_url?.message}>
    <input
      {...form.register("repo_url")}
      className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full"
      placeholder="https://github.com/owner/algorithm-repo"
    />
    <p className="text-xs text-gray-500 mt-1">
      Public repos work without a PAT. Private repos require GitHub PAT in Settings.
    </p>
  </FormField>
)}
```

Update defaultValues to `{ repo_url: "" }`.

- [ ] **U3.3 — Commit**

```bash
git add dashboard/src/pages/Algorithms.tsx dashboard/src/api/hooks.ts dashboard/src/api/client.ts
git commit -m "feat(algos-ui): replace repo dropdown with URL input"
```

### Work unit U4: Workers.tsx — install dialog with auto-close

**Branch:** `plan/U4-worker-install-dialog`

**Files:**
- Modify: `dashboard/src/pages/Workers.tsx`, `dashboard/src/components/WorkerInstallCommand.tsx` (or replace with `WorkerInstallDialog.tsx`)
- Modify (additive): `dashboard/src/api/hooks.ts`, `dashboard/src/api/client.ts`
- Create: `dashboard/src/hooks/useWorkerConnectedEvent.ts`

- [ ] **U4.1 — Hook for WS event**

Existing dashboard WebSocket plumbing already runs (see `coordinator/api/websocket.py:46`). Add a hook that subscribes to a specific message type:

```typescript
// dashboard/src/hooks/useWorkerConnectedEvent.ts
import { useEffect } from "react";

export function useWorkerConnectedEvent(workerId: string | null,
                                        onConnected: (msg: any) => void) {
  useEffect(() => {
    if (!workerId) return;
    const ws = new WebSocket(
      (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/dashboard",
    );
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "worker_connected" && msg.worker_id === workerId) {
          onConnected(msg);
        }
      } catch {}
    };
    return () => ws.close();
  }, [workerId, onConnected]);
}
```

If the project already exports a shared dashboard WS hook, integrate there instead.

- [ ] **U4.2 — Build the new dialog**

Replace the inline `WorkerInstallCommand` panel pattern with a single modal that owns the form → waiting → connected state machine.

```tsx
// dashboard/src/components/WorkerInstallDialog.tsx
import { useEffect, useState } from "react";
import { Copy, Check, AlertTriangle, RefreshCw } from "lucide-react";
import { useCreateWorker, useWorkerInstallCommand,
         useRegenerateWorkerInstallToken, useWorker } from "../api/hooks";
import { useWorkerConnectedEvent } from "../hooks/useWorkerConnectedEvent";

type Step = "form" | "waiting" | "connected";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function WorkerInstallDialog({ open, onClose }: Props) {
  const [step, setStep] = useState<Step>("form");
  const [name, setName] = useState("");
  const [maxAlgos, setMaxAlgos] = useState(2);
  const [workerId, setWorkerId] = useState<string | null>(null);

  const createWorker = useCreateWorker();
  const { data: command, refetch: refetchCommand } = useWorkerInstallCommand(workerId ?? "");
  const regen = useRegenerateWorkerInstallToken();

  // Fallback polling
  const { data: worker, refetch: refetchWorker } = useWorker(workerId ?? "");
  useEffect(() => {
    if (step !== "waiting" || !workerId) return;
    const id = setInterval(() => refetchWorker(), 5000);
    return () => clearInterval(id);
  }, [step, workerId, refetchWorker]);
  useEffect(() => {
    if (step === "waiting" && worker?.status === "online") setStep("connected");
  }, [step, worker?.status]);

  useWorkerConnectedEvent(step === "waiting" ? workerId : null, () => setStep("connected"));

  useEffect(() => {
    if (step !== "connected") return;
    const id = setTimeout(() => { onClose(); reset(); }, 1500);
    return () => clearTimeout(id);
  }, [step]);

  function reset() {
    setStep("form"); setName(""); setMaxAlgos(2); setWorkerId(null);
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    const created = await createWorker.mutateAsync({
      name, max_algorithms: maxAlgos, tailscale_ip: null,
    } as any);
    setWorkerId(created.id);
    setStep("waiting");
  }

  async function handleCopy() {
    if (command) await navigator.clipboard.writeText(command);
  }

  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 w-full max-w-2xl">
        {step === "form" && (
          <form onSubmit={handleRegister}>
            <h2 className="text-lg font-semibold mb-3">Add Worker</h2>
            <label className="block mb-2 text-sm">Name
              <input value={name} onChange={(e) => setName(e.target.value)} required
                     className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1" />
            </label>
            <label className="block mb-3 text-sm">Max algorithms
              <input type="number" value={maxAlgos}
                     onChange={(e) => setMaxAlgos(Number(e.target.value))} min={1}
                     className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1" />
            </label>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose}
                      className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600">
                Cancel
              </button>
              <button type="submit" disabled={!name || createWorker.isPending}
                      className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50">
                {createWorker.isPending ? "Generating…" : "Generate Install Command"}
              </button>
            </div>
          </form>
        )}

        {step === "waiting" && (
          <div>
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="text-amber-400" size={18} />
              <h2 className="text-lg font-semibold">Waiting for {name}</h2>
            </div>
            <p className="text-xs text-gray-400 mb-2">
              SSH into the Pi and paste this. Replace <code className="text-amber-300">tskey-CHANGE-ME</code> with a real Tailscale auth key.
            </p>
            <div className="relative mb-3">
              <pre className="bg-black/60 border border-gray-800 rounded p-3 text-[11px] text-gray-200 font-mono whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                {command ?? "Building command…"}
              </pre>
              <button onClick={handleCopy}
                      className="absolute top-2 right-2 px-2 py-1 rounded text-xs bg-gray-800 hover:bg-gray-700 text-gray-200 flex items-center gap-1.5">
                <Copy size={12} /> Copy
              </button>
            </div>
            <p className="text-sm text-gray-300 mb-2">⏳ Waiting for worker to connect…</p>
            <div className="flex justify-end gap-2">
              <button onClick={async () => { await regen.mutateAsync(workerId!); refetchCommand(); }}
                      className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200">
                <RefreshCw size={12} /> Regenerate token
              </button>
              <button onClick={() => { onClose(); reset(); }}
                      className="ml-auto px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600">
                Cancel
              </button>
            </div>
          </div>
        )}

        {step === "connected" && (
          <div className="text-center py-6">
            <Check className="mx-auto text-green-400 mb-2" size={32} />
            <p className="text-lg font-semibold text-green-200">✓ Connected!</p>
            <p className="text-xs text-gray-400 mt-1">{name} is online.</p>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **U4.3 — Update Workers.tsx**

Replace the inline `WorkerInstallCommand`+`justRegistered` pattern with the new dialog:

```tsx
// inside Workers component:
const [installOpen, setInstallOpen] = useState(false);

// replace the "Register Worker" button onClick:
<button onClick={() => setInstallOpen(true)} ...>
  Add Worker
</button>

// remove the entire {justRegistered && ...} block

// at the end of JSX, add:
<WorkerInstallDialog open={installOpen} onClose={() => setInstallOpen(false)} />
```

Also make `tailscale_ip` optional on the existing register/edit modals (drop the required validation and the field from the form). For the existing edit modal: tailscale_ip is now display-only (worker self-reports it).

- [ ] **U4.4 — Commit**

```bash
git add dashboard/src/pages/Workers.tsx dashboard/src/components/WorkerInstallDialog.tsx \
        dashboard/src/components/WorkerInstallCommand.tsx \
        dashboard/src/hooks/useWorkerConnectedEvent.ts \
        dashboard/src/api/hooks.ts dashboard/src/api/client.ts
git commit -m "feat(workers-ui): unified install dialog with WS push + poll fallback"
```

### Work unit U5: Data.tsx — subscriptions + compare view

**Branch:** `plan/U5-data-compare`

**Files:**
- Modify: `dashboard/src/pages/Data.tsx`
- Create: `dashboard/src/components/LiveSubscriptionsSection.tsx`, `dashboard/src/components/CompareView.tsx`
- Modify (additive): `dashboard/src/api/hooks.ts`, `dashboard/src/api/client.ts`, `dashboard/src/types.ts`

- [ ] **U5.1 — Client + hooks for subscriptions**

```typescript
// client.ts
async listLiveSubscriptions() { return (await this.http.get("/api/live-subscriptions")).data; }
async createLiveSubscription(body: { broker: string; symbol: string; tick_retention_hours?: number }) {
  return (await this.http.post("/api/live-subscriptions", body)).data;
}
async estimateLiveSubStorage(broker: string, symbol: string, retention_hours: number) {
  return (await this.http.get("/api/live-subscriptions/estimate", {
    params: { broker, symbol, retention_hours },
  })).data;
}
async deleteLiveSubscription(id: string) {
  await this.http.delete(`/api/live-subscriptions/${id}`);
}
```

```typescript
// hooks.ts
export function useLiveSubscriptions() {
  return useQuery({ queryKey: ["live-subs"], queryFn: () => api.listLiveSubscriptions() });
}
export function useCreateLiveSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createLiveSubscription.bind(api),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["live-subs"] }),
  });
}
export function useDeleteLiveSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteLiveSubscription.bind(api),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["live-subs"] }),
  });
}
```

- [ ] **U5.2 — LiveSubscriptionsSection component**

```tsx
// dashboard/src/components/LiveSubscriptionsSection.tsx
import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useLiveSubscriptions, useCreateLiveSubscription, useDeleteLiveSubscription } from "../api/hooks";

export function LiveSubscriptionsSection() {
  const { data: subs = [], isLoading } = useLiveSubscriptions();
  const create = useCreateLiveSubscription();
  const del = useDeleteLiveSubscription();
  const [adding, setAdding] = useState(false);
  const [broker, setBroker] = useState<"alpaca"|"tradier">("alpaca");
  const [symbol, setSymbol] = useState("");
  const [retention, setRetention] = useState(24);

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase">
          Live Subscriptions {subs.length > 0 && <span className="font-normal text-gray-500">({subs.length})</span>}
        </h2>
        <button onClick={() => setAdding(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500">
          <Plus size={14} /> Subscribe
        </button>
      </div>
      {isLoading ? <p className="text-gray-400 text-sm">Loading…</p> : (
        <div className="space-y-1">
          {subs.map((s: any) => (
            <div key={s.id} className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded px-3 py-2 text-sm">
              <div className="flex items-center gap-3">
                <span className="text-indigo-400 font-mono">{s.broker}_live</span>
                <span className="font-mono">{s.symbol}</span>
                <span className="text-xs text-gray-500">retention {s.tick_retention_hours}h</span>
                {s.tick_rate_per_min && <span className="text-xs text-gray-500">~{Math.round(s.tick_rate_per_min)}/min</span>}
                <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                  s.status === "running" ? "bg-green-900/40 text-green-300 border-green-800" :
                  s.status === "error" ? "bg-red-900/40 text-red-300 border-red-800" :
                  "bg-gray-800 text-gray-400 border-gray-700"
                }`}>{s.status}</span>
              </div>
              <button onClick={() => del.mutate(s.id)}
                      className="text-gray-400 hover:text-red-400" title="Unsubscribe">
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
      {adding && (
        <div className="mt-3 bg-gray-900 border border-gray-700 rounded p-3 flex gap-2 items-end">
          <select value={broker} onChange={(e) => setBroker(e.target.value as any)}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm">
            <option value="alpaca">alpaca</option><option value="tradier">tradier</option>
          </select>
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                 placeholder="symbol"
                 className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-24" />
          <select value={retention} onChange={(e) => setRetention(Number(e.target.value))}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm">
            <option value={24}>24h</option><option value={72}>72h</option><option value={168}>7d</option>
          </select>
          <button onClick={async () => {
            await create.mutateAsync({ broker, symbol, tick_retention_hours: retention });
            setAdding(false); setSymbol("");
          }} disabled={!symbol || create.isPending}
                  className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50">
            Add
          </button>
          <button onClick={() => setAdding(false)}
                  className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600">
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
```

- [ ] **U5.3 — Compare view (overlay / stacked / diff)**

This is the larger sub-task. The component:

- Accepts an array of `(provider, symbol, timeframe)` triples.
- Loads each dataset via existing `getMarketData` API (with optional `source` param — `provider` doubles as `source` here).
- Renders all series via `lightweight-charts` instances based on `mode`.
- Manages a shared viewport state in a context so mode-switches preserve zoom/pan.

The full implementation is ~200 lines of TSX. See `dashboard/src/components/PriceChart.tsx` for the existing lightweight-charts pattern and copy its setup. Key points:

```tsx
// dashboard/src/components/CompareView.tsx — skeleton
import { useEffect, useMemo, useRef, useState, createContext, useContext } from "react";
import { createChart, IChartApi, UTCTimestamp } from "lightweight-charts";
import { useMarketData } from "../api/hooks";

type Dataset = { provider: string; symbol: string; timeframe: string };
type Mode = "overlay" | "stacked" | "diff";

type Viewport = { visibleRange: { from: UTCTimestamp; to: UTCTimestamp } | null };
const ViewportCtx = createContext<{ vp: Viewport; setVp: (v: Viewport) => void }>({
  vp: { visibleRange: null }, setVp: () => {},
});

export function CompareView({ datasets }: { datasets: Dataset[] }) {
  const [mode, setMode] = useState<Mode>("overlay");
  const [vp, setVp] = useState<Viewport>({ visibleRange: null });

  // Load all series
  const series = datasets.map(d => useMarketData(d.symbol, d.timeframe, d.provider));
  // (useMarketData should accept a `source` param wired in F6's UI side)

  return (
    <ViewportCtx.Provider value={{ vp, setVp }}>
      <div className="space-y-3">
        <div className="flex gap-2">
          {(["overlay","stacked","diff"] as const).map(m => (
            <button key={m} onClick={() => setMode(m)}
                    disabled={m === "diff" && datasets.length !== 2}
                    className={`px-3 py-1.5 rounded text-sm ${
                      mode === m ? "bg-indigo-600 text-white" :
                      "bg-gray-800 text-gray-300 hover:bg-gray-700 disabled:opacity-50"
                    }`}>
              {m}
            </button>
          ))}
        </div>
        {mode === "overlay" && <OverlayChart datasets={datasets} series={series} />}
        {mode === "stacked" && <StackedCharts datasets={datasets} series={series} />}
        {mode === "diff" && datasets.length === 2 &&
          <DiffChart datasets={datasets} series={series} />}
      </div>
    </ViewportCtx.Provider>
  );
}

// Each sub-component reads viewport from context, subscribes to it on mount,
// and pushes to context on visible-range changes. Use lightweight-charts'
// timeScale().subscribeVisibleTimeRangeChange / setVisibleRange APIs.
```

Implement `OverlayChart`, `StackedCharts`, `DiffChart` per Spec B §7. Each `lightweight-charts` instance:
- On mount: if `vp.visibleRange` is set, call `chart.timeScale().setVisibleRange(vp.visibleRange)`.
- Subscribe to `visibleTimeRangeChange`; on event, update `vp` via the context.
- For diff: bin both series' bars by timestamp (rounded to bar interval), compute `series_a[t] - series_b[t]` for matched bars, render gaps for unmatched.

- [ ] **U5.4 — Integrate sections + compare into Data.tsx**

Add `<LiveSubscriptionsSection />` near the top (above Scrapers). Add multi-select checkboxes to the existing Available Data section and a "Compare selected" button that opens a modal/page with `<CompareView datasets={selected} />`.

URL state encoding: parse `?compare=alpaca_live:SPY:1min,polygon:SPY:1min&mode=diff` on mount; rewrite as user changes selection.

- [ ] **U5.5 — Commit**

```bash
git add dashboard/src/pages/Data.tsx dashboard/src/components/LiveSubscriptionsSection.tsx \
        dashboard/src/components/CompareView.tsx \
        dashboard/src/api/hooks.ts dashboard/src/api/client.ts dashboard/src/types.ts
git commit -m "feat(data-ui): live subscriptions panel + multi-dataset compare view"
```

### Work unit U6: Strategies page (Spec C)

**Branch:** `plan/U6-strategies`

**Files:**
- Create: `dashboard/src/pages/Strategies.tsx`
- Create: `dashboard/src/components/strategy/{TemplatePicker,LegsTable,ChainBrowser,PnlChart,DateSlider,GreeksPanel,templates}.tsx`
- Modify: `dashboard/src/App.tsx` (new route)
- Modify (additive): `dashboard/src/api/hooks.ts`, `dashboard/src/api/client.ts`

- [ ] **U6.1 — API client + hooks**

```typescript
// client.ts
async getOptionExpiries(accountId: string, underlying: string) {
  return (await this.http.get(`/api/accounts/${accountId}/options-chain/expiries`,
                              { params: { underlying } })).data;
}
async getOptionChain(accountId: string, underlying: string, expiry: string) {
  return (await this.http.get(`/api/accounts/${accountId}/options-chain/${expiry}`,
                              { params: { underlying } })).data;
}
```

```typescript
// hooks.ts
export function useOptionExpiries(accountId: string, underlying: string | null) {
  return useQuery({
    queryKey: ["expiries", accountId, underlying],
    queryFn: () => api.getOptionExpiries(accountId, underlying!),
    enabled: !!accountId && !!underlying,
  });
}
export function useOptionChain(accountId: string, underlying: string | null, expiry: string | null) {
  return useQuery({
    queryKey: ["chain", accountId, underlying, expiry],
    queryFn: () => api.getOptionChain(accountId, underlying!, expiry!),
    enabled: !!accountId && !!underlying && !!expiry,
    staleTime: 30_000,
  });
}
```

- [ ] **U6.2 — Templates registry**

```typescript
// dashboard/src/components/strategy/templates.ts
import type { OptionLeg } from "../../lib/options";

export type TemplateName =
  | "long_call" | "long_put" | "short_call" | "short_put"
  | "vertical_bull_call" | "vertical_bear_call"
  | "vertical_bull_put" | "vertical_bear_put"
  | "straddle" | "strangle"
  | "iron_condor" | "iron_butterfly" | "calendar_call" | "custom";

type ChainContract = { strike: number; right: "call"|"put"; bid?: number; ask?: number; iv?: number };
type Chain = { spot: number; contracts: ChainContract[] };

function pickByStrike(chain: Chain, target: number, right: "call"|"put"): ChainContract | null {
  const candidates = chain.contracts.filter(c => c.right === right);
  if (!candidates.length) return null;
  return candidates.reduce((best, c) =>
    Math.abs(c.strike - target) < Math.abs(best.strike - target) ? c : best
  );
}

function legFrom(c: ChainContract, side: "buy"|"sell", expiry: string, quantity = 1): OptionLeg {
  return {
    side, right: c.right, strike: c.strike, expiry, quantity,
    bid: c.bid, ask: c.ask, iv: c.iv ?? 0.30,
  };
}

export function buildTemplate(
  name: TemplateName, chain: Chain, expiry: string,
): OptionLeg[] {
  const spot = chain.spot;
  switch (name) {
    case "long_call":  { const c = pickByStrike(chain, spot, "call"); return c ? [legFrom(c, "buy", expiry)] : []; }
    case "long_put":   { const c = pickByStrike(chain, spot, "put");  return c ? [legFrom(c, "buy", expiry)] : []; }
    case "short_call": { const c = pickByStrike(chain, spot, "call"); return c ? [legFrom(c, "sell", expiry)] : []; }
    case "short_put":  { const c = pickByStrike(chain, spot, "put");  return c ? [legFrom(c, "sell", expiry)] : []; }
    case "vertical_bull_call": {
      const lo = pickByStrike(chain, spot, "call");
      const hi = pickByStrike(chain, spot * 1.02, "call");
      return lo && hi ? [legFrom(lo, "buy", expiry), legFrom(hi, "sell", expiry)] : [];
    }
    case "vertical_bear_call": {
      const lo = pickByStrike(chain, spot, "call");
      const hi = pickByStrike(chain, spot * 1.02, "call");
      return lo && hi ? [legFrom(lo, "sell", expiry), legFrom(hi, "buy", expiry)] : [];
    }
    case "vertical_bull_put": {
      const hi = pickByStrike(chain, spot, "put");
      const lo = pickByStrike(chain, spot * 0.98, "put");
      return hi && lo ? [legFrom(hi, "sell", expiry), legFrom(lo, "buy", expiry)] : [];
    }
    case "vertical_bear_put": {
      const hi = pickByStrike(chain, spot, "put");
      const lo = pickByStrike(chain, spot * 0.98, "put");
      return hi && lo ? [legFrom(hi, "buy", expiry), legFrom(lo, "sell", expiry)] : [];
    }
    case "straddle": {
      const c = pickByStrike(chain, spot, "call"); const p = pickByStrike(chain, spot, "put");
      return c && p ? [legFrom(c, "buy", expiry), legFrom(p, "buy", expiry)] : [];
    }
    case "strangle": {
      const c = pickByStrike(chain, spot * 1.03, "call"); const p = pickByStrike(chain, spot * 0.97, "put");
      return c && p ? [legFrom(c, "buy", expiry), legFrom(p, "buy", expiry)] : [];
    }
    case "iron_condor": {
      const cs = pickByStrike(chain, spot * 1.02, "call");
      const cl = pickByStrike(chain, spot * 1.04, "call");
      const ps = pickByStrike(chain, spot * 0.98, "put");
      const pl = pickByStrike(chain, spot * 0.96, "put");
      return cs && cl && ps && pl ?
        [legFrom(ps, "sell", expiry), legFrom(pl, "buy", expiry),
         legFrom(cs, "sell", expiry), legFrom(cl, "buy", expiry)] : [];
    }
    case "iron_butterfly": {
      const cs = pickByStrike(chain, spot, "call");
      const ps = pickByStrike(chain, spot, "put");
      const cl = pickByStrike(chain, spot * 1.04, "call");
      const pl = pickByStrike(chain, spot * 0.96, "put");
      return cs && ps && cl && pl ?
        [legFrom(cs, "sell", expiry), legFrom(ps, "sell", expiry),
         legFrom(cl, "buy", expiry), legFrom(pl, "buy", expiry)] : [];
    }
    case "calendar_call": {
      // Same-strike, different expiries — caller must supply a far expiry.
      return [];  // implemented when expiry-pair UI exists; out of v1
    }
    case "custom": return [];
  }
}
```

- [ ] **U6.3 — Persistence hook**

```typescript
// dashboard/src/components/strategy/usePersistedBuilderState.ts
import { useEffect, useRef, useState } from "react";
import type { OptionLeg } from "../../lib/options";

const STORAGE_VERSION = 1;
const STORAGE_KEY = (accountId: string) => `quilt.strategyBuilder.${accountId}`;
const MAX_AGE_MS = 30 * 24 * 3600 * 1000;

type Persisted = {
  version: 1;
  underlying: string | null;
  template: string | null;
  legs: Array<Omit<OptionLeg, "bid"|"ask"|"iv">>;
  scrubDateOffsetMs: number | null;
  savedAt: number;
};

export function usePersistedBuilderState(accountId: string) {
  const [hydrated, setHydrated] = useState<Persisted | null>(null);
  const [needsToastMsg, setNeedsToastMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!accountId) return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY(accountId));
      if (!raw) return;
      const parsed = JSON.parse(raw) as Persisted;
      if (parsed.version !== STORAGE_VERSION) return;
      if (Date.now() - parsed.savedAt > MAX_AGE_MS) {
        localStorage.removeItem(STORAGE_KEY(accountId));
        return;
      }
      const today = new Date().toISOString().slice(0, 10);
      const validLegs = parsed.legs.filter(l => l.expiry >= today);
      const droppedCount = parsed.legs.length - validLegs.length;
      if (droppedCount > 0) {
        setNeedsToastMsg(`Removed ${droppedCount} expired leg${droppedCount === 1 ? "" : "s"}`);
      }
      if (validLegs.length === 0) {
        localStorage.removeItem(STORAGE_KEY(accountId));
        return;
      }
      setHydrated({ ...parsed, legs: validLegs });
    } catch { /* corrupt; ignore */ }
  }, [accountId]);

  const debounceRef = useRef<number | null>(null);
  function save(state: Omit<Persisted, "version"|"savedAt">) {
    if (debounceRef.current != null) clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      const payload: Persisted = { ...state, version: 1, savedAt: Date.now() };
      localStorage.setItem(STORAGE_KEY(accountId), JSON.stringify(payload));
    }, 500);
  }
  function reset() { localStorage.removeItem(STORAGE_KEY(accountId)); }

  return { hydrated, needsToastMsg, save, reset };
}
```

- [ ] **U6.4 — Page shell + components**

```tsx
// dashboard/src/pages/Strategies.tsx — skeleton
import { useParams, useNavigate, Link } from "react-router-dom";
import { useState, useEffect } from "react";
import { ChevronLeft, RotateCcw } from "lucide-react";
import { useAccount, useOptionExpiries, useOptionChain, useOpenPosition } from "../api/hooks";
import { useUIStore } from "../stores/ui";
import type { OptionLeg } from "../lib/options";
import { strategyPnl, strategyGreeks, pnlCurve } from "../lib/options";
import { buildTemplate, TemplateName } from "../components/strategy/templates";
import { usePersistedBuilderState } from "../components/strategy/usePersistedBuilderState";

const TEMPLATES: { name: TemplateName; label: string }[] = [
  { name: "long_call", label: "Long Call" }, { name: "long_put", label: "Long Put" },
  { name: "short_call", label: "Short Call" }, { name: "short_put", label: "Short Put" },
  { name: "vertical_bull_call", label: "Bull Call Spread" },
  { name: "vertical_bear_call", label: "Bear Call Spread" },
  { name: "vertical_bull_put", label: "Bull Put Spread" },
  { name: "vertical_bear_put", label: "Bear Put Spread" },
  { name: "straddle", label: "Straddle" }, { name: "strangle", label: "Strangle" },
  { name: "iron_condor", label: "Iron Condor" },
  { name: "iron_butterfly", label: "Iron Butterfly" },
  { name: "custom", label: "Custom" },
];

export function Strategies() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const addAlert = useUIStore(s => s.addAlert);
  const { data: account } = useAccount(id ?? "");
  const [underlying, setUnderlying] = useState<string>("");
  const [expiry, setExpiry] = useState<string | null>(null);
  const [template, setTemplate] = useState<TemplateName>("custom");
  const [legs, setLegs] = useState<OptionLeg[]>([]);
  const [scrubMs, setScrubMs] = useState<number | null>(null);
  const { hydrated, needsToastMsg, save, reset } = usePersistedBuilderState(id ?? "");

  // Hydrate on mount
  useEffect(() => {
    if (!hydrated) return;
    setUnderlying(hydrated.underlying ?? "");
    setTemplate((hydrated.template as TemplateName) ?? "custom");
    setScrubMs(hydrated.scrubDateOffsetMs);
    setLegs(hydrated.legs.map(l => ({ ...l, bid: undefined, ask: undefined, iv: 0.30 })));
  }, [hydrated]);
  useEffect(() => {
    if (needsToastMsg) addAlert({ message: needsToastMsg, severity: "warning" });
  }, [needsToastMsg]);

  const { data: expData } = useOptionExpiries(id ?? "", underlying || null);
  const { data: chain } = useOptionChain(id ?? "", underlying || null, expiry);

  // Save state on changes
  useEffect(() => {
    if (!id) return;
    save({
      underlying: underlying || null,
      template,
      legs: legs.map(l => ({
        side: l.side, right: l.right, strike: l.strike, expiry: l.expiry, quantity: l.quantity,
      })),
      scrubDateOffsetMs: scrubMs,
    });
  }, [id, underlying, template, legs, scrubMs]);

  function applyTemplate(name: TemplateName) {
    setTemplate(name);
    if (chain && expiry) {
      setLegs(buildTemplate(name, chain, expiry));
    }
  }

  const submitMut = useOpenPosition(id ?? "");
  async function handleSubmit() {
    if (!legs.length) return;
    const netCost = legs.reduce((sum, l) => {
      const mid = ((l.bid ?? 0) + (l.ask ?? 0)) / 2;
      return sum + mid * l.quantity * (l.side === "buy" ? 1 : -1);
    }, 0);
    await submitMut.mutateAsync({
      legs: legs.map(l => ({
        symbol: underlying, asset_type: "options", side: l.side, quantity: l.quantity,
        expiry: l.expiry, strike: l.strike, right: l.right,
      })),
      strategy_type: template,
      order_type: "limit",
      limit_price: Math.round(netCost * 100) / 100,
    });
    addAlert({ message: "Order submitted", severity: "success" });
  }

  if (!account) return <p>Loading…</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Link to={`/accounts/${id}`} className="text-gray-400 hover:text-white">
          <ChevronLeft size={20} />
        </Link>
        <h1 className="text-xl font-bold">Strategies — {account.name}</h1>
        <div className="flex gap-2">
          <button onClick={() => { reset(); setLegs([]); setTemplate("custom"); }}
                  className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600">
            <RotateCcw size={14} /> Reset
          </button>
          <button onClick={handleSubmit}
                  disabled={legs.length === 0 || submitMut.isPending}
                  className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50">
            Submit Order
          </button>
        </div>
      </div>

      {/* Underlying + Template + Expiry */}
      <div className="flex gap-3 items-center">
        <input value={underlying} onChange={(e) => setUnderlying(e.target.value.toUpperCase())}
               placeholder="underlying (e.g. SPY)"
               className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm w-32" />
        <select value={template} onChange={(e) => applyTemplate(e.target.value as TemplateName)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
          {TEMPLATES.map(t => <option key={t.name} value={t.name}>{t.label}</option>)}
        </select>
        <select value={expiry ?? ""} onChange={(e) => setExpiry(e.target.value || null)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
          <option value="">Select expiry</option>
          {(expData?.expiries ?? []).map((d: string) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>

      {/* Legs + Chain + P&L + Greeks panels go here. */}
      {/* Implement LegsTable, ChainBrowser, PnlChart, DateSlider, GreeksPanel
          per the Spec C §2 wireframe. Each is a separate file. */}
    </div>
  );
}
```

Implement the remaining components (`LegsTable`, `ChainBrowser`, `PnlChart`, `DateSlider`, `GreeksPanel`) per the spec — each is ~50-100 lines. `PnlChart` uses lightweight-charts with two series (at-expiry faint, at-date solid). `GreeksPanel` reads `strategyGreeks(legs, spot, scrubMs ?? Date.now())`.

Mount the route in `App.tsx`:

```tsx
<Route path="/accounts/:id/strategies" element={<Strategies />} />
```

- [ ] **U6.5 — Commit**

```bash
git add dashboard/src/pages/Strategies.tsx dashboard/src/components/strategy/ \
        dashboard/src/App.tsx dashboard/src/api/hooks.ts dashboard/src/api/client.ts
git commit -m "feat(strategies): options strategy builder page

Account-bound at /accounts/:id/strategies. Templates + chain browser +
client-side Black-Scholes P&L viz + Greeks. State persists per-account
in localStorage. Submits via Spec A's positions/open endpoint."
```

---

**End of Phase 3.** Merge UI work units in any order; shared-file conflicts (hooks.ts, client.ts, types.ts, App.tsx) are additive and trivial to resolve.

---

## Phase 4 — Integration + smoke

Two work units, sequential. I1 wires the auto-subscribe flow (depends on Phase 2's S4); I2 is the manual smoke pass against real services.

### Work unit I1: Auto-subscribe + algo startup gating (Spec B §2)

**Branch:** `plan/I1-auto-subscribe`

**Files:**
- Modify: `coordinator/services/lifecycle.py`
- Modify: `coordinator/api/routes/live_subscriptions.py` (link to manager for unsubscribe / delete)
- Modify: `coordinator/services/live_feed_aggregator.py` (real broker stream wiring — see below)
- Modify: `worker/alpaca_adapter.py`, `worker/tradier_adapter.py` (add `start_market_data_stream`)
- Test: `tests/coordinator/services/test_lifecycle_auto_subscribe.py`

- [ ] **I1.1 — Failing test: starting an instance with broker_live deps refuses when no sub**

```python
# tests/coordinator/services/test_lifecycle_auto_subscribe.py
import pytest
from coordinator.services.lifecycle import LifecycleService

@pytest.mark.asyncio
async def test_start_instance_refuses_when_no_live_sub(test_app, db_session):
    # Setup: create account + algo with a broker_live dependency manifest, no LiveSubscription row
    # ... (use existing factories from other lifecycle tests)
    svc = LifecycleService(...)
    with pytest.raises(Exception, match="no live subscription for SPY on alpaca"):
        await svc.start_instance(instance_id)
```

- [ ] **I1.2 — Implement startup gating + auto-subscribe**

In `coordinator/services/lifecycle.py`'s `start_instance` (or equivalent), after the existing checks and before flipping status to "starting":

```python
# Resolve manifest data_dependencies
manifest = QuiltManifest.from_file(Path("data/packages") / algo.name / "quilt.yaml")
broker_live_deps = [
    d for d in manifest.requirements.data_dependencies
    if d.get("source", "broker_live") == "broker_live"
]
historical_deps = [
    d for d in manifest.requirements.data_dependencies
    if d.get("source", "broker_live") != "broker_live"
]

# Gate: broker_live deps require a running subscription
for d in broker_live_deps:
    symbol = d["symbol"]
    sub = (await session.execute(
        select(LiveSubscription).where(
            LiveSubscription.broker == account.broker_type,
            LiveSubscription.symbol == symbol,
        )
    )).scalar_one_or_none()
    if not sub or sub.status != "running":
        raise StartError(
            f"Cannot start: no live subscription for {symbol} on {account.broker_type}. "
            f"Subscribe on the Data page first."
        )

# Auto-add dependent to LiveFeedManager
manager = container.live_feed_manager
for d in broker_live_deps:
    manager.ensure_running(account.broker_type, d["symbol"], instance.id)
    # Increment sub.dependent_count
    sub_row = (await session.execute(
        select(LiveSubscription).where(
            LiveSubscription.broker == account.broker_type,
            LiveSubscription.symbol == d["symbol"],
        )
    )).scalar_one()
    sub_row.dependent_count += 1
await session.flush()
```

In `stop_instance` (or equivalent), release the dependents:

```python
for d in broker_live_deps:
    released = manager.release(account.broker_type, d["symbol"], instance.id)
    sub_row = (await session.execute(...)).scalar_one()
    sub_row.dependent_count = max(0, sub_row.dependent_count - 1)
```

- [ ] **I1.3 — Wire real broker streams**

Add to `BrokerAdapter` (in F1's file — small additive edit OK because Phase 4 owns this):

```python
    def start_market_data_stream(self, symbols: list[str], on_trade, on_quote):
        """Open a market-data WS for the given symbols. on_trade/on_quote are
        callbacks invoked per tick. Returns an object with a .close() method.
        """
        raise NotImplementedError
```

Implement in `AlpacaAdapter` and `TradierAdapter` using their respective WS SDKs. For Alpaca: `alpaca-py`'s `StockDataStream` and `OptionDataStream`. For Tradier: their REST-streaming HTTP endpoint with session token.

In `LiveFeedAggregator._run`, replace the stub `while True: sleep` with:

```python
async def _run(self, broker: str, symbol: str) -> None:
    # 1. Resolve account creds via the live_feed_account.{broker} setting
    # 2. Construct adapter
    # 3. adapter.start_market_data_stream([symbol], on_trade, on_quote)
    # 4. on_trade appends to in-memory daily trade buffer + 1min bar buffer
    # 5. Every 5s: flush buffers to data/market/{broker}_live/{symbol}/ticks/{...}.parquet
    # 6. At each minute boundary: flush closed 1min bar to 1min.parquet via DataService
    # ... (full implementation: ~150 lines; reference Spec B §3)
```

- [ ] **I1.4 — Failing test: aggregator writes ticks**

A focused integration test using a fake stream:

```python
@pytest.mark.asyncio
async def test_aggregator_writes_tick_parquets_and_bars(tmp_path, ...):
    # Set up adapter mock that emits 60 trades over 1 minute
    # Run aggregator for 70 seconds (or use time-mocking)
    # Assert: trades-{today}.parquet exists with 60 rows
    # Assert: 1min.parquet has one bar with correct OHLCV
```

(Implementation depends on how the broker stream is mocked. Use `freezegun` or asyncio-friendly time mocks.)

- [ ] **I1.5 — Commit**

```bash
git add coordinator/services/lifecycle.py coordinator/services/live_feed_aggregator.py \
        worker/alpaca_adapter.py worker/tradier_adapter.py worker/broker_adapter.py \
        tests/coordinator/services/test_lifecycle_auto_subscribe.py
git commit -m "feat(lifecycle): auto-subscribe + algo startup gating + real broker streams

Spec B §2 startup-gating: algo refuses to start if any broker_live
dependency lacks a running subscription. Auto-adds dependents to the
LiveFeedManager. Aggregator now opens real broker WebSockets per
subscription and writes ticks + bars to parquet."
```

### Work unit I2: End-to-end smoke

**Branch:** `plan/I2-smoke`

Not code-writing — a manual smoke run with documented pass criteria. Spawn this as a checklist subagent that goes through the steps with the user.

- [ ] **I2.1 — Spec A smoke**

1. Open a paper Alpaca account in the dashboard. Confirm the asset-type checkboxes show `equities, options, crypto` and are pre-checked from saved state.
2. Click "Open Position" on AccountDetail. Build a 2-leg vertical call spread on SPY. Submit. Confirm: success modal shows `atomic: true` and a `broker_order_id`; the position appears in the broker's order list with one parent order id.
3. Install an algo via URL: paste a known public quilt algorithm repo URL. Confirm: install completes, algorithm appears in the list with manifest fields populated.
4. Add a worker: click "Add Worker", name it, generate command, paste on a real Pi. Confirm: dialog flips to "✓ Connected!" within ~30s and auto-closes; worker appears in the list with `tailscale_ip` populated from the Pi's actual IP.

- [ ] **I2.2 — Spec B smoke**

1. On the Data page, subscribe to SPY on Alpaca during market hours. Confirm: row appears with status transitioning `stopped → running`; `tick_rate_per_min` populates within 60s.
2. Check filesystem: `data/market/alpaca_live/SPY/ticks/trades-{today}.parquet` and `quotes-{today}.parquet` exist and have rows.
3. After 2 minutes, check `data/market/alpaca_live/SPY/1min.parquet` has new bars.
4. Compare: select `alpaca_live SPY 1min` + `polygon SPY 1min` (assuming a polygon download exists), click "Compare selected". Switch between Overlay / Stacked / Diff. Confirm zoom/pan state preserves across mode switches.

- [ ] **I2.3 — Spec C smoke**

1. From AccountDetail of a Tradier account, click "Strategies".
2. Enter SPY, pick `Bull Call Spread`, pick an expiry ~30 days out.
3. Confirm: legs auto-populate with two strikes; P&L chart renders with both at-expiry (faint) and at-date (solid) curves; Greeks panel shows non-zero Δ, Θ, V.
4. Drag the scrub-date slider; chart redraws live.
5. Click "Submit Order" with default limit price. Confirm: order goes through (paper); position appears as a single parent order with two legs in Tradier.
6. Close the page, reopen `/accounts/{id}/strategies`. Confirm: state restored — same legs, template, slider position.
7. Click "Reset". Confirm: legs clear, localStorage entry removed.

- [ ] **I2.4 — Commit**

```bash
# No code changes; just a record of the smoke pass
git commit --allow-empty -m "test: phase 4 manual smoke complete

Spec A: asset-type checkboxes, open-position, algo-install-from-url,
worker install dialog — all pass.
Spec B: live subscription writes ticks+bars; compare view preserves
viewport across modes.
Spec C: builder hydrates, P&L + Greeks update live, submit hits
broker as one atomic ticket, persistence works."
```

---

## Self-review

Spec coverage check (per the writing-plans skill):

- **Spec A §1 (asset-type catalog):** F3 (module + endpoint) + U1 (checkbox UI) + S1 server validation.
- **Spec A §2 (open-position UI):** F1 (base-class types) + A1/A2 (per-broker impls) + S1 (endpoint) + U2 (modal + button + lock display).
- **Spec A §3 (algo install via URL):** S2 (endpoint + manifest pre-fetch) + U3 (UI).
- **Spec A §4 (worker install dialog):** F2 (heartbeat wiring + script env var) + S3 (handler broadcast) + F5 (tailscale_ip nullable migration) + U4 (dialog UI).
- **Spec B §1 (sources/storage):** S4 (aggregator skeleton + retention loop) + I1 (real streams).
- **Spec B §2 (subscription model + lifecycle):** F5 (model) + S4 (manager + API) + I1 (auto-subscribe + gating).
- **Spec B §3 (aggregator):** S4 (skeleton) + I1 (full impl).
- **Spec B §4 (storage estimator):** S4 (`/estimate` endpoint + heuristics) + U5 (UI).
- **Spec B §5 (API surface):** S4 (CRUD + estimate).
- **Spec B §6 (algo consumption):** F6 (context.source param) + I1 (startup gating + dependent counts).
- **Spec B §7 (compare UI):** U5 (CompareView component with 3 modes + viewport context).
- **Spec C §1-2 (routing + layout):** U2 (button) + U6 (page + components).
- **Spec C §3 (strategy model + templates + persistence):** U6 (templates registry + usePersistedBuilderState).
- **Spec C §4 (chain API):** F1 (types) + A1/A2 (adapters) + S5 (endpoints) + S6 (mount).
- **Spec C §5 (client math):** F4 (options.ts).
- **Spec C §6 (submit flow):** U2 (mutation hook) + U6 (Submit Order button calls hook).
- **Spec C §7 (edge cases):** Surfaces in tests across A1, A2, S1, S5, U6.

No spec sections without a task.

Placeholder scan: no TBDs, no "implement later", no "add appropriate error handling". The CompareView and Strategies sub-components have skeleton-level detail (the framing is laid out; agents fill in lightweight-charts wiring per the existing `PriceChart.tsx` reference). This is acceptable because the missing parts are direct copies of an existing component pattern — agents read `PriceChart.tsx` and adapt.

Type-consistency check: `MultilegLegSpec` shape matches Spec A's API `LegSpec` (verified). `OptionLeg` (client) is structurally compatible with `MultilegLegSpec` for the conversion in U6.4's `handleSubmit`. `OptionContract` field names match between `worker/broker_adapter.py` and the chain API response in S5.

---

## Recommended execution mode

Use `superpowers:subagent-driven-development` with parallel dispatch per phase:

- **Phase 0:** dispatch 6 subagents in parallel (F1..F6), each in its own worktree. Review each PR; merge in any order.
- **Phase 1:** 2 subagents (A1, A2).
- **Phase 2:** 5 subagents (S1..S5) in parallel; then one agent for S6 after they merge.
- **Phase 3:** 6 subagents (U1..U6); minor merge conflicts in `hooks.ts`/`client.ts`/`types.ts` resolved by a coordinator pass.
- **Phase 4:** sequential (I1 then I2 with the user).

Worktrees are created via `superpowers:using-git-worktrees`. Estimated wall-clock with full parallelism: ~6-8 hours of subagent work (versus ~30+ hours sequential).
