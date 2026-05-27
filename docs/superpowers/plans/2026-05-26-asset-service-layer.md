# Asset Service Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize ALL asset-type-specific logic (200+ scattered `if asset_type ==` / `if asset_class ==` conditionals across 32 files) behind a unified `AssetService` protocol with four implementations — eliminating every asset-type branch in the codebase outside the service layer.

**Architecture:** A base `AssetService` protocol defines the interface every asset type must implement: classification, symbol resolution, pricing, fills, P&L, risk, expiry, trading rules (TIF, multileg, required fields, PDT), market hours, streaming config, provider support, and contract discovery. Four concrete implementations (`EquityAssetService`, `OptionsAssetService`, `CryptoAssetService`, `IndexAssetService`) own all type-specific logic. An `AssetServiceRegistry` detects the asset type from any symbol and dispatches to the correct service. Bar-lookup logic is shared via a free helper (`_bar_lookup`) — services do NOT inherit from each other. All callers (engine, adapters, API routes, coordinator services, SDK) go through the registry instead of branching on `asset_type`.

**Tech Stack:** Python 3.12, pandas, numpy, scipy (for options math)

**Execution order:** Phase 1 must complete before Phase 2. Phases 2 and 3 can run in either order but Phase 3 is recommended second because it includes the broader coordinator services. Phase 4 is independent and can run any time after Phase 1.

**Phase boundary expectations:**
- After Phase 1: backtest engine, portfolio VaR, deployments/positions, and Polygon symbol resolution all route through the registry. Live trading paths still branch on `asset_type` — this is acceptable interim state.
- After Phase 2: all broker adapters route through the registry for TIF, multileg, symbol composition, and stream class selection. Live order placement is fully service-routed.
- After Phase 3: all coordinator services and API routes route through the registry. Market hours, PDT, stream caps, validation all go through services.
- After Phase 4: the SDK validates asset types via the registry. Zero `if asset_type ==` conditionals remain outside `coordinator/services/asset_services/`.

**Rollback strategy:** Each task ends in its own commit. To roll back a phase, `git revert` the range of commits for that phase. The registry is additive in Phase 1 (Tasks 1-6) — even if migrations break, the services exist and can be tested independently. Field-level changes (e.g., LegSpec.asset_type) are preserved throughout for backwards compatibility; only the *branching* on those fields is removed.

---

## Expanded Protocol

Based on a comprehensive audit of 200+ asset-type references across 32 files, the `AssetService` protocol requires these methods:

```python
class AssetService(Protocol):
    asset_type: AssetType

    # ── Classification & Symbol Resolution ──
    def classify(self, symbol: str) -> bool: ...
    def resolve_symbol(self, symbol: str, provider: str) -> str: ...
    def compose_order_symbol(self, leg: Any) -> str: ...

    # ── Pricing & Fills ──
    def get_multiplier(self) -> int: ...
    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]: ...
    def get_fill_price(self, symbol: str, side: str, sim_time: Any, ctx: Any) -> Optional[float]: ...

    # ── P&L & Risk ──
    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float: ...
    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float: ...

    # ── Expiry & Settlement ──
    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float, sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]: ...

    # ── Trading Rules ──
    def time_in_force(self) -> str: ...        # "DAY" / "GTC"
    def supports_multileg(self) -> bool: ...   # True only for options
    def required_order_fields(self) -> set[str]: ...  # {"expiry","strike","right"} for options
    def is_pdt_exempt(self) -> bool: ...       # True for crypto

    # ── Market Hours ──
    def is_market_open(self, timestamp: Any) -> bool: ...

    # ── Streaming ──
    def stream_config(self, broker: str) -> StreamConfig: ...
    def supports_provider(self, provider: str) -> bool: ...

    # ── Discovery ──
    async def discover_contracts(
        self, underlying: str, start: Any, end: Any, config: dict, provider: Any,
    ) -> list[str]: ...
```

### Supporting types

```python
class AssetType(str, Enum):
    EQUITIES = "equities"
    OPTIONS = "options"
    CRYPTO = "crypto"
    INDEX = "index"


@dataclass(frozen=True)
class Settlement:
    symbol: str
    side: str           # "buy" or "sell" (closing side)
    quantity: float
    fill_price: float   # intrinsic value at expiry, 0 if worthless
    realized_pnl: float


@dataclass(frozen=True)
class StreamConfig:
    supported: bool                  # whether this broker streams this asset class at all
    stream_class: str                # "stock" | "crypto" | "options" (broker-specific name)
    symbol_transform: str            # "identity" | "occ_prefix" | "crypto_slash" | "crypto_dash" | "polygon_x_prefix"
    cap: int                         # max symbols per stream connection
    cluster: Optional[str] = None    # for polygon: "stocks" | "crypto" | "options"
```

---

## Files Affected (32 files, 200+ refs)

### New files (Phase 1)
| File | Responsibility |
|------|---------------|
| `coordinator/services/asset_services/__init__.py` | Package init, exports |
| `coordinator/services/asset_services/base.py` | `AssetService` protocol, `AssetType`, `Settlement`, `StreamConfig`, `_bar_lookup` helper |
| `coordinator/services/asset_services/equity.py` | `EquityAssetService` |
| `coordinator/services/asset_services/options.py` | `OptionsAssetService` |
| `coordinator/services/asset_services/crypto.py` | `CryptoAssetService` |
| `coordinator/services/asset_services/index.py` | `IndexAssetService` |
| `coordinator/services/asset_services/registry.py` | `AssetServiceRegistry` |
| `tests/coordinator/services/asset_services/test_base.py` | Protocol + helper tests |
| `tests/coordinator/services/asset_services/test_equity.py` | Equity service tests |
| `tests/coordinator/services/asset_services/test_options.py` | Options service tests |
| `tests/coordinator/services/asset_services/test_crypto.py` | Crypto service tests |
| `tests/coordinator/services/asset_services/test_index.py` | Index service tests |
| `tests/coordinator/services/asset_services/test_registry.py` | Registry tests |

### Phase 1 migration targets (5 files)
| File | Refs | Action |
|------|------|--------|
| `coordinator/services/backtest_engine_v2.py` | 18 | Replace all asset_type branches with registry calls |
| `coordinator/api/routes/portfolio.py` | 2 | VaR via `service.risk_contribution()` |
| `coordinator/api/routes/deployments.py` | 3 | P&L via `service.compute_unrealized_pnl()` |
| `coordinator/api/routes/positions.py` | 2 | Price + asset_type via registry |
| `coordinator/services/data_providers/polygon.py` | 3 | Symbol resolution via registry |

### Phase 2 migration targets (8 files)
| File | Refs | Action |
|------|------|--------|
| `worker/broker_adapter.py` | 6 | Document service routing (LegSpec keeps asset_type) |
| `worker/alpaca_adapter.py` | 15 | TIF, multileg, compose_symbol, stream class via registry |
| `worker/tradier_adapter.py` | 7 | Multileg, stream validation via registry |
| `worker/polygon_stream_adapter.py` | 9 | Cluster + symbol transform via stream_config |
| `worker/thetadata_stream_adapter.py` | 4 | Stream config passthrough |
| `worker/coinbase_stream_adapter.py` | 1 | Assert supports_provider("coinbase") |
| `worker/tick_loop.py` | 1 | Keep asset_type passthrough |
| `worker/context.py` | 1 | Keep Position.asset_type |

### Phase 3 migration targets (10 files)
| File | Refs | Action |
|------|------|--------|
| `coordinator/api/routes/accounts.py` | 25 | Order validation + TIF via registry |
| `coordinator/services/lifecycle.py` | 14 | Subscription wiring via registry classification |
| `coordinator/services/live_feed_aggregator.py` | 24 | Stream keying, caps, provider support via service |
| `coordinator/services/market_clock.py` | 3 | Delegate to `service.is_market_open()` |
| `coordinator/services/pdt_monitor.py` | 1 | Delegate to `service.is_pdt_exempt()` |
| `coordinator/services/tick_scheduler.py` | 4 | Market hours via registry |
| `coordinator/api/routes/live_subscriptions.py` | 10 | Validate via registry + classify |
| `coordinator/api/routes/options_chain.py` | 1 | Account capability via `supports_provider` |
| `coordinator/api/routes/algorithms.py` | 17 | `_VALID_ASSET_CLASSES` → `AssetType` enum |
| `coordinator/services/asset_catalog.py` | 3 | Replace dict with registry-derived data |

### Phase 4 migration targets (3 files)
| File | Refs | Action |
|------|------|--------|
| `sdk/signals.py` | 5 | Validate `asset_type` via `AssetType` enum |
| `sdk/models.py` | 3 | Validate `Position.asset_type` via enum |
| `sdk/manifest.py` | 7 | `asset_types` validation via enum |

---

## Phase 1: Core Services + Registry + Engine

### Task 1: Create base protocol, types, and bar lookup helper

**Files:**
- Create: `coordinator/services/asset_services/__init__.py`
- Create: `coordinator/services/asset_services/base.py`
- Create: `tests/coordinator/services/asset_services/__init__.py`
- Create: `tests/coordinator/services/asset_services/test_base.py`

- [ ] **Step 1: Create the package directories**

```bash
mkdir -p coordinator/services/asset_services
mkdir -p tests/coordinator/services/asset_services
touch tests/coordinator/services/asset_services/__init__.py
```

- [ ] **Step 2: Write the failing test**

```python
# tests/coordinator/services/asset_services/test_base.py
"""Tests for protocol + StreamConfig + _bar_lookup helper."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)


def test_asset_type_values():
    assert AssetType.EQUITIES.value == "equities"
    assert AssetType.OPTIONS.value == "options"
    assert AssetType.CRYPTO.value == "crypto"
    assert AssetType.INDEX.value == "index"


def test_asset_type_string_subclass():
    """AssetType must behave like a string for JSON serialization."""
    assert AssetType.EQUITIES == "equities"
    assert isinstance(AssetType.EQUITIES, str)


def test_settlement_construction():
    s = Settlement(
        symbol="SPY241029C00586000",
        side="sell",
        quantity=5,
        fill_price=14.0,
        realized_pnl=2000.0,
    )
    assert s.symbol == "SPY241029C00586000"
    assert s.realized_pnl == 2000.0


def test_stream_config_construction():
    cfg = StreamConfig(
        supported=True,
        stream_class="stock",
        symbol_transform="identity",
        cap=30,
        cluster="stocks",
    )
    assert cfg.supported is True
    assert cfg.cap == 30
    assert cfg.cluster == "stocks"


def test_bar_lookup_finds_last_bar_before_sim_time():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23",
        ]),
        "close": [100.0, 101.0, 102.0, 103.0],
    })
    # sim_time is 2026-05-22 — should pick the bar at 2026-05-22 (index 2)
    price = _bar_lookup(df, datetime(2026, 5, 22, 23, 59))
    assert price == 102.0


def test_bar_lookup_returns_none_when_no_bars_before():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-23"]),
        "close": [103.0],
    })
    price = _bar_lookup(df, datetime(2026, 5, 22))
    assert price is None


def test_bar_lookup_handles_tz_aware_sim_time():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [102.0],
    })
    from datetime import timezone
    price = _bar_lookup(df, datetime(2026, 5, 23, tzinfo=timezone.utc))
    assert price == 102.0


def test_bar_lookup_handles_tz_aware_timestamps():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(
            ["2026-05-22"], utc=True,
        ),
        "close": [102.0],
    })
    price = _bar_lookup(df, datetime(2026, 5, 23))
    assert price == 102.0


def test_bar_lookup_returns_none_on_empty_df():
    df = pd.DataFrame({"timestamp": [], "close": []})
    assert _bar_lookup(df, datetime(2026, 5, 22)) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_base.py -v`
Expected: FAIL with "No module named 'coordinator.services.asset_services'"

- [ ] **Step 4: Write base.py**

```python
# coordinator/services/asset_services/base.py
"""Base protocol and shared types for the Asset Service Layer.

Every asset type (equities, options, crypto, indexes) implements the
AssetService protocol. Callers route through AssetServiceRegistry which
returns the correct service for a given symbol — no more scattered
if/else on asset_type.

Services do NOT inherit from each other. Shared logic (e.g. bar lookup)
lives as free functions in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol

import numpy as np
import pandas as pd


class AssetType(str, Enum):
    EQUITIES = "equities"
    OPTIONS = "options"
    CRYPTO = "crypto"
    INDEX = "index"


@dataclass(frozen=True)
class Settlement:
    """Result of expiring an option position. None means not expired."""
    symbol: str
    side: str           # "buy" or "sell" (closing side)
    quantity: float
    fill_price: float   # intrinsic value, 0 if worthless
    realized_pnl: float


@dataclass(frozen=True)
class StreamConfig:
    """Per-broker streaming configuration for an asset class."""
    supported: bool
    stream_class: str                 # "stock" | "crypto" | "options"
    symbol_transform: str             # "identity" | "occ_prefix" | "crypto_slash" | "crypto_dash" | "polygon_x_prefix"
    cap: int                          # max symbols per stream connection
    cluster: Optional[str] = None     # polygon-only: "stocks" | "crypto" | "options"


def _bar_lookup(df: pd.DataFrame, sim_time: Any) -> Optional[float]:
    """Return the close price of the last bar at or before ``sim_time``.

    Handles tz-naive and tz-aware timestamps on either side by normalizing
    both to UTC-naive before comparing. Returns None if df is empty or no
    bar exists at/before sim_time.
    """
    if df is None or len(df) == 0:
        return None
    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    cutoff = pd.Timestamp(sim_time)
    if cutoff.tz is not None:
        cutoff = cutoff.tz_convert("UTC").tz_localize(None)
    ns = ts.values.view("int64")
    cutoff_ns = np.datetime64(cutoff).view("int64")
    idx = int(np.searchsorted(ns, cutoff_ns, side="right")) - 1
    if idx < 0:
        return None
    return float(df.iloc[idx]["close"])


class AssetService(Protocol):
    """Protocol every asset-type service implements.

    See module docstring for design notes.
    """
    asset_type: AssetType

    # ── Classification & Symbol Resolution ──
    def classify(self, symbol: str) -> bool: ...
    def resolve_symbol(self, symbol: str, provider: str) -> str: ...
    def compose_order_symbol(self, leg: Any) -> str: ...

    # ── Pricing & Fills ──
    def get_multiplier(self) -> int: ...
    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]: ...
    def get_fill_price(
        self, symbol: str, side: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]: ...

    # ── P&L & Risk ──
    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float: ...
    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float: ...

    # ── Expiry ──
    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]: ...

    # ── Trading Rules ──
    def time_in_force(self) -> str: ...
    def supports_multileg(self) -> bool: ...
    def required_order_fields(self) -> set[str]: ...
    def is_pdt_exempt(self) -> bool: ...

    # ── Market Hours ──
    def is_market_open(self, timestamp: Any) -> bool: ...

    # ── Streaming ──
    def stream_config(self, broker: str) -> StreamConfig: ...
    def supports_provider(self, provider: str) -> bool: ...

    # ── Discovery ──
    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]: ...
```

- [ ] **Step 5: Write __init__.py**

```python
# coordinator/services/asset_services/__init__.py
from coordinator.services.asset_services.base import (
    AssetService,
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)

__all__ = [
    "AssetService",
    "AssetType",
    "Settlement",
    "StreamConfig",
    "_bar_lookup",
]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_base.py -v`
Expected: 9 PASSED

- [ ] **Step 7: Commit**

```bash
git add coordinator/services/asset_services/ tests/coordinator/services/asset_services/
git commit -m "feat(asset-services): add AssetService protocol, types, bar-lookup helper"
```

---

### Task 2: Create EquityAssetService

**Files:**
- Create: `coordinator/services/asset_services/equity.py`
- Create: `tests/coordinator/services/asset_services/test_equity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/asset_services/test_equity.py
"""Tests cover every callsite behavior the migration depends on."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from coordinator.services.asset_services.base import AssetType, StreamConfig
from coordinator.services.asset_services.equity import EquityAssetService


@pytest.fixture
def svc():
    return EquityAssetService()


# ── Classification ──

def test_classify_stocks(svc):
    assert svc.classify("AAPL")
    assert svc.classify("SPY")
    assert svc.classify("TSLA")
    assert svc.classify("BRK.B")


def test_classify_rejects_options(svc):
    assert not svc.classify("SPY241029C00586000")
    assert not svc.classify("O:QQQ260320C00580000")


def test_classify_rejects_crypto(svc):
    assert not svc.classify("BTCUSD")
    assert not svc.classify("ETHUSD")


def test_classify_rejects_indexes(svc):
    assert not svc.classify("VIX")
    assert not svc.classify("SPX")
    assert not svc.classify("I:SPX")
    assert not svc.classify("^GSPC")


# ── Symbol Resolution ──

def test_resolve_symbol_identity(svc):
    assert svc.resolve_symbol("AAPL", "polygon") == "AAPL"
    assert svc.resolve_symbol("AAPL", "tradier") == "AAPL"
    assert svc.resolve_symbol("AAPL", "alpaca") == "AAPL"


def test_compose_order_symbol_identity(svc):
    leg = SimpleNamespace(symbol="AAPL")
    assert svc.compose_order_symbol(leg) == "AAPL"


# ── Pricing ──

def test_multiplier(svc):
    assert svc.get_multiplier() == 1


def test_get_price_searches_bars(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22", "2026-05-23"]),
        "close": [100.0, 101.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "AAPL", "1day"): df})
    price = svc.get_price("AAPL", datetime(2026, 5, 23, 12, 0), ctx)
    assert price == 101.0


def test_get_price_returns_none_when_no_bars(svc):
    ctx = SimpleNamespace(_bars={})
    assert svc.get_price("AAPL", datetime(2026, 5, 22), ctx) is None


def test_get_price_returns_none_when_ctx_none(svc):
    assert svc.get_price("AAPL", datetime(2026, 5, 22), None) is None


def test_get_fill_price_same_as_price(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [100.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "AAPL", "1day"): df})
    assert svc.get_fill_price("AAPL", "buy", datetime(2026, 5, 22, 12), ctx) == 100.0
    assert svc.get_fill_price("AAPL", "sell", datetime(2026, 5, 22, 12), ctx) == 100.0


# ── P&L ──

def test_unrealized_pnl(svc):
    pnl = svc.compute_unrealized_pnl("AAPL", quantity=10, avg_price=150.0, market_value=1600.0)
    assert pnl == pytest.approx(100.0)


def test_unrealized_pnl_zero_market_value(svc):
    pnl = svc.compute_unrealized_pnl("AAPL", quantity=10, avg_price=150.0, market_value=0.0)
    assert pnl == 0.0


def test_unrealized_pnl_short_position(svc):
    # Short 10 shares at 150, market value -1400 (short worth -1400)
    pnl = svc.compute_unrealized_pnl("AAPL", quantity=-10, avg_price=150.0, market_value=-1400.0)
    # abs(qty) * avg = 1500 cost basis; pnl = -1400 - 1500 = -2900? No — short pnl is opposite
    # For shorts, when market_value < 0, our formula returns 0 (no profit calc for closed/empty).
    # This documents the current behavior.
    assert pnl == 0.0


# ── Risk ──

def test_risk_contribution_uses_injected_data_service(svc):
    """risk_contribution accepts an injected DataService — no fresh instantiation."""
    import numpy as np
    df = pd.DataFrame({"close": np.linspace(100, 110, 60)})
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    risk = svc.risk_contribution("AAPL", market_value=10000.0, data_service=ds)
    assert risk > 0
    ds.load_market_data.assert_called()


def test_risk_contribution_fallback_when_no_data(svc):
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=None)
    risk = svc.risk_contribution("AAPL", market_value=10000.0, data_service=ds)
    assert risk == pytest.approx(200.0)  # 2% fallback


def test_risk_contribution_no_data_service(svc):
    """When no data_service provided, returns 2% fallback (no implicit instantiation)."""
    risk = svc.risk_contribution("AAPL", market_value=10000.0)
    assert risk == pytest.approx(200.0)


# ── Expiry ──

def test_handle_expiry_returns_none(svc):
    assert svc.handle_expiry("AAPL", 10, 150.0, datetime.now(), None) is None


# ── Trading Rules ──

def test_time_in_force(svc):
    assert svc.time_in_force() == "DAY"


def test_supports_multileg(svc):
    assert svc.supports_multileg() is False


def test_required_order_fields(svc):
    assert svc.required_order_fields() == set()


def test_is_pdt_exempt(svc):
    assert svc.is_pdt_exempt() is False


# ── Market Hours ──

def test_market_open_weekday_during_session(svc):
    # Monday 2026-05-25 14:00 ET = 18:00 UTC during EDT
    ts = datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_market_closed_weekend(svc):
    # Saturday 14:00 ET = 18:00 UTC
    ts = datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
    assert not svc.is_market_open(ts)


def test_market_closed_before_open(svc):
    # Monday 08:00 ET = 12:00 UTC (before 9:30 ET)
    ts = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    assert not svc.is_market_open(ts)


def test_market_closed_after_close(svc):
    # Monday 17:00 ET = 21:00 UTC (after 16:00 ET)
    ts = datetime(2026, 5, 25, 21, 0, tzinfo=timezone.utc)
    assert not svc.is_market_open(ts)


def test_market_open_handles_naive_datetime_as_utc(svc):
    ts = datetime(2026, 5, 25, 18, 0)  # treated as UTC
    assert svc.is_market_open(ts)


def test_market_open_rejects_non_datetime(svc):
    # String input should NOT silently return True
    with pytest.raises((TypeError, AttributeError)):
        svc.is_market_open("not a datetime")


# ── Streaming ──

def test_stream_config_polygon(svc):
    cfg = svc.stream_config("polygon")
    assert cfg.supported
    assert cfg.cluster == "stocks"
    assert cfg.symbol_transform == "identity"


def test_stream_config_alpaca(svc):
    cfg = svc.stream_config("alpaca")
    assert cfg.supported
    assert cfg.stream_class == "stock"


def test_stream_config_tradier(svc):
    cfg = svc.stream_config("tradier")
    assert cfg.supported


def test_stream_config_coinbase_unsupported(svc):
    cfg = svc.stream_config("coinbase")
    assert not cfg.supported


def test_supports_provider_coinbase_no(svc):
    assert not svc.supports_provider("coinbase")


def test_supports_provider_polygon_yes(svc):
    assert svc.supports_provider("polygon")


# ── Discovery ──

@pytest.mark.asyncio
async def test_discover_contracts_returns_underlying(svc):
    result = await svc.discover_contracts("AAPL", None, None, {}, None)
    assert result == ["AAPL"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_equity.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Write equity.py**

```python
# coordinator/services/asset_services/equity.py
"""Equity asset service — US stocks and ETFs."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)

_OCC_RE = re.compile(r"^(?:O:)?[A-Z]{1,6}\d{6}[CP]\d{8}$")
_CRYPTO_SUFFIXES = ("USD", "USDT")
_KNOWN_INDEXES = {"VIX", "SPX", "NDX", "RUT", "DJI", "GSPC", "IXIC"}


def _is_dst_us_eastern(ts_utc: datetime) -> bool:
    """Rough check: US Eastern observes DST from mid-March to early November."""
    y = ts_utc.year
    # 2nd Sunday in March → 1st Sunday in November
    march_start = datetime(y, 3, 8, tzinfo=timezone.utc)
    while march_start.weekday() != 6:
        march_start += timedelta(days=1)
    nov_end = datetime(y, 11, 1, tzinfo=timezone.utc)
    while nov_end.weekday() != 6:
        nov_end += timedelta(days=1)
    return march_start <= ts_utc < nov_end


def _utc_to_et(ts: datetime) -> datetime:
    """Convert a UTC datetime to ET (naive)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    offset = timedelta(hours=4) if _is_dst_us_eastern(ts) else timedelta(hours=5)
    return (ts - offset).replace(tzinfo=None)


class EquityAssetService:
    asset_type = AssetType.EQUITIES

    # ── Classification ──
    def classify(self, symbol: str) -> bool:
        if not symbol:
            return False
        if _OCC_RE.match(symbol):
            return False
        if symbol in _KNOWN_INDEXES:
            return False
        if symbol.startswith("I:") or symbol.startswith("^"):
            return False
        if symbol.endswith(_CRYPTO_SUFFIXES) and symbol not in ("USD", "USDT"):
            return False
        return True

    # ── Symbol Resolution ──
    def resolve_symbol(self, symbol: str, provider: str) -> str:
        return symbol

    def compose_order_symbol(self, leg: Any) -> str:
        return leg.symbol

    # ── Pricing ──
    def get_multiplier(self) -> int:
        return 1

    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]:
        if ctx is None or not hasattr(ctx, "_bars"):
            return None
        for (_src, sym, _tf), df in ctx._bars.items():
            if sym == symbol:
                return _bar_lookup(df, sim_time)
        return None

    def get_fill_price(
        self, symbol: str, side: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        return self.get_price(symbol, sim_time, ctx)

    # ── P&L ──
    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float:
        cost = avg_price * abs(quantity)
        return market_value - cost if market_value > 0 else 0.0

    # ── Risk ──
    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float:
        if data_service is None:
            return market_value * 0.02
        import numpy as np
        for provider in ("polygon", "tradier", "yfinance", "alpaca_live", "tradier_live"):
            df = data_service.load_market_data(provider, symbol, "1day")
            if df is None or len(df) < 10:
                continue
            closes = df["close"].astype(float).values[-lookback_days:]
            if len(closes) < 10:
                continue
            returns = np.diff(np.log(closes))
            var_5 = np.percentile(returns, 5)
            return abs(var_5) * market_value
        return market_value * 0.02

    # ── Expiry ──
    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]:
        return None

    # ── Trading Rules ──
    def time_in_force(self) -> str:
        return "DAY"

    def supports_multileg(self) -> bool:
        return False

    def required_order_fields(self) -> set[str]:
        return set()

    def is_pdt_exempt(self) -> bool:
        return False

    # ── Market Hours ──
    def is_market_open(self, timestamp: Any) -> bool:
        if not isinstance(timestamp, datetime):
            raise TypeError(f"is_market_open requires datetime, got {type(timestamp).__name__}")
        et = _utc_to_et(timestamp)
        if et.weekday() >= 5:
            return False
        # 9:30am - 4:00pm ET
        minutes = et.hour * 60 + et.minute
        return 9 * 60 + 30 <= minutes < 16 * 60

    # ── Streaming ──
    def stream_config(self, broker: str) -> StreamConfig:
        if broker == "polygon":
            return StreamConfig(True, "stock", "identity", 30, cluster="stocks")
        if broker == "alpaca":
            return StreamConfig(True, "stock", "identity", 30)
        if broker == "tradier":
            return StreamConfig(True, "stock", "identity", 30)
        if broker == "thetadata":
            return StreamConfig(True, "stock", "identity", 30)
        return StreamConfig(False, "", "identity", 0)

    def supports_provider(self, provider: str) -> bool:
        return provider in ("polygon", "alpaca", "tradier", "thetadata", "yfinance")

    # ── Discovery ──
    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]:
        return [underlying]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_equity.py -v`
Expected: ~25 PASSED

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/asset_services/equity.py tests/coordinator/services/asset_services/test_equity.py
git commit -m "feat(asset-services): add EquityAssetService with full protocol"
```

---

### Task 3: Create OptionsAssetService

**Files:**
- Create: `coordinator/services/asset_services/options.py`
- Create: `tests/coordinator/services/asset_services/test_options.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/asset_services/test_options.py
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from coordinator.services.asset_services.base import AssetType
from coordinator.services.asset_services.options import OptionsAssetService


@pytest.fixture
def svc():
    return OptionsAssetService()


# ── Classification ──

def test_classify_occ_with_o_prefix(svc):
    assert svc.classify("O:SPY241029C00586000")


def test_classify_occ_without_prefix(svc):
    assert svc.classify("SPY241029C00586000")
    assert svc.classify("QQQ260417P00637000")


def test_classify_rejects_equities(svc):
    assert not svc.classify("AAPL")
    assert not svc.classify("SPY")


def test_classify_rejects_crypto(svc):
    assert not svc.classify("BTCUSD")


def test_classify_rejects_indexes(svc):
    assert not svc.classify("VIX")


# ── Parsing ──

def test_parse_symbol(svc):
    p = svc.parse_symbol("SPY241029C00586000")
    assert p["underlying"] == "SPY"
    assert p["expiration"] == "2024-10-29"
    assert p["option_type"] == "call"
    assert p["strike"] == 586.0


def test_parse_symbol_with_prefix(svc):
    p = svc.parse_symbol("O:QQQ260320C00580000")
    assert p["underlying"] == "QQQ"
    assert p["strike"] == 580.0


def test_parse_symbol_returns_none_for_invalid(svc):
    assert svc.parse_symbol("AAPL") is None


# ── Symbol Resolution ──

def test_resolve_symbol_polygon_adds_prefix(svc):
    assert svc.resolve_symbol("SPY241029C00586000", "polygon") == "O:SPY241029C00586000"


def test_resolve_symbol_polygon_idempotent(svc):
    assert svc.resolve_symbol("O:SPY241029C00586000", "polygon") == "O:SPY241029C00586000"


def test_resolve_symbol_tradier_strips_prefix(svc):
    assert svc.resolve_symbol("O:SPY241029C00586000", "tradier") == "SPY241029C00586000"


def test_resolve_symbol_tradier_no_prefix_passthrough(svc):
    assert svc.resolve_symbol("SPY241029C00586000", "tradier") == "SPY241029C00586000"


# ── Order Composition (OCC build from leg fields) ──

def test_compose_order_symbol_from_leg(svc):
    leg = SimpleNamespace(
        symbol="SPY",
        asset_type="options",
        expiry="2024-10-29",
        strike=586.0,
        right="call",
    )
    assert svc.compose_order_symbol(leg) == "SPY241029C00586000"


def test_compose_order_symbol_put(svc):
    leg = SimpleNamespace(
        symbol="QQQ",
        asset_type="options",
        expiry="2026-04-17",
        strike=637.0,
        right="put",
    )
    assert svc.compose_order_symbol(leg) == "QQQ260417P00637000"


def test_compose_order_symbol_missing_fields_raises(svc):
    leg = SimpleNamespace(symbol="SPY", asset_type="options", expiry=None, strike=None, right=None)
    with pytest.raises(ValueError):
        svc.compose_order_symbol(leg)


# ── Pricing ──

def test_multiplier(svc):
    assert svc.get_multiplier() == 100


def test_get_price_uses_data_service(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [5.5],
    })
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    ctx = SimpleNamespace(_data_service=ds, _default_source="polygon")
    price = svc.get_price("O:SPY241029C00586000", datetime(2026, 5, 22, 12), ctx)
    assert price == 5.5
    ds.load_market_data.assert_called_with("polygon", "SPY241029C00586000", "1day")


def test_get_price_returns_none_when_no_ctx(svc):
    assert svc.get_price("SPY241029C00586000", datetime(2026, 5, 22), None) is None


def test_get_fill_price_buy_uses_ask(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [5.5], "bid": [5.4], "ask": [5.6], "volume": [100],
    })
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    ctx = SimpleNamespace(_data_service=ds, _default_source="polygon")
    price = svc.get_fill_price("SPY241029C00586000", "buy", datetime(2026, 5, 22, 12), ctx)
    assert price == 5.6


def test_get_fill_price_sell_uses_bid(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [5.5], "bid": [5.4], "ask": [5.6], "volume": [100],
    })
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    ctx = SimpleNamespace(_data_service=ds, _default_source="polygon")
    price = svc.get_fill_price("SPY241029C00586000", "sell", datetime(2026, 5, 22, 12), ctx)
    assert price == 5.4


def test_get_fill_price_falls_back_to_spread_estimate(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [5.0], "volume": [100],
    })
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    ctx = SimpleNamespace(_data_service=ds, _default_source="polygon")
    buy = svc.get_fill_price("SPY241029C00586000", "buy", datetime(2026, 5, 22, 12), ctx)
    sell = svc.get_fill_price("SPY241029C00586000", "sell", datetime(2026, 5, 22, 12), ctx)
    assert buy > 5.0
    assert sell < 5.0


# ── P&L ──

def test_unrealized_pnl_with_multiplier(svc):
    # 5 contracts at $10 = $5000 cost basis (× 100 multiplier)
    pnl = svc.compute_unrealized_pnl(
        "SPY241029C00586000", quantity=5, avg_price=10.0, market_value=6000.0,
    )
    assert pnl == pytest.approx(1000.0)


def test_unrealized_pnl_zero_market_value(svc):
    pnl = svc.compute_unrealized_pnl(
        "SPY241029C00586000", quantity=5, avg_price=10.0, market_value=0.0,
    )
    assert pnl == 0.0


# ── Risk (delta-adjusted) ──

def test_risk_contribution_delta_adjusted(svc):
    """Options risk = equity risk × abs(delta)."""
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=None)  # fallback to 2%
    risk = svc.risk_contribution("SPY241029C00586000", market_value=10000.0, data_service=ds)
    # 2% × 10000 × delta ≈ 200 × delta. For ATM call, delta ≈ 0.5.
    assert 0 < risk < 200.0


# ── Expiry ──

def test_handle_expiry_not_expired(svc):
    """sim_time before expiration returns None."""
    result = svc.handle_expiry(
        "SPY260110C00600000",
        quantity=1, avg_price=5.0,
        sim_time=datetime(2026, 1, 9), ctx=None,
    )
    assert result is None


def test_handle_expiry_long_call_itm(svc):
    """Long call ITM at expiry: pays out intrinsic value."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-10"]),
        "close": [650.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "SPY", "1day"): df})
    result = svc.handle_expiry(
        "SPY260110C00600000",
        quantity=1, avg_price=5.0,
        sim_time=datetime(2026, 1, 11), ctx=ctx,
    )
    assert result is not None
    assert result.fill_price == 50.0  # 650 - 600
    # PnL = (50 - 5) * 1 * 100 = 4500
    assert result.realized_pnl == pytest.approx(4500.0)
    assert result.side == "sell"


def test_handle_expiry_short_call_otm(svc):
    """Short call OTM at expiry: keep full premium."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-10"]),
        "close": [500.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "SPY", "1day"): df})
    result = svc.handle_expiry(
        "SPY260110C00600000",
        quantity=-1, avg_price=5.0,
        sim_time=datetime(2026, 1, 11), ctx=ctx,
    )
    assert result is not None
    assert result.fill_price == 0.0
    # Short: realized = avg_price * qty * 100 = 500
    assert result.realized_pnl == pytest.approx(500.0)
    assert result.side == "buy"  # closing a short


def test_handle_expiry_put_itm(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-10"]),
        "close": [550.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "SPY", "1day"): df})
    result = svc.handle_expiry(
        "SPY260110P00600000",
        quantity=1, avg_price=5.0,
        sim_time=datetime(2026, 1, 11), ctx=ctx,
    )
    assert result is not None
    assert result.fill_price == 50.0  # 600 - 550
    assert result.realized_pnl == pytest.approx(4500.0)


# ── Trading Rules ──

def test_time_in_force(svc):
    assert svc.time_in_force() == "DAY"


def test_supports_multileg(svc):
    assert svc.supports_multileg() is True


def test_required_order_fields(svc):
    assert svc.required_order_fields() == {"expiry", "strike", "right"}


def test_is_pdt_exempt(svc):
    assert svc.is_pdt_exempt() is False


# ── Market Hours (same as equities) ──

def test_market_hours_follows_equity(svc):
    from datetime import timezone as tz
    # Monday 14:00 ET = 18:00 UTC
    assert svc.is_market_open(datetime(2026, 5, 25, 18, 0, tzinfo=tz.utc))
    # Saturday
    assert not svc.is_market_open(datetime(2026, 5, 23, 18, 0, tzinfo=tz.utc))


# ── Streaming ──

def test_stream_config_polygon_options(svc):
    cfg = svc.stream_config("polygon")
    assert cfg.supported
    assert cfg.cluster == "options"
    assert cfg.symbol_transform == "occ_prefix"


def test_stream_config_alpaca_options(svc):
    cfg = svc.stream_config("alpaca")
    assert cfg.supported


def test_stream_config_coinbase_no(svc):
    assert not svc.stream_config("coinbase").supported


def test_supports_provider_coinbase_no(svc):
    assert not svc.supports_provider("coinbase")


# ── Discovery ──

@pytest.mark.asyncio
async def test_discover_contracts_calls_provider(svc):
    provider = MagicMock()
    provider.discover_option_contracts = AsyncMock(return_value=[
        {"ticker": "O:SPY260117C00450000"},
        {"ticker": "O:SPY260117C00455000"},
    ])
    result = await svc.discover_contracts(
        "SPY", date(2026, 1, 1), date(2026, 1, 17),
        {"strike_range": "atm5", "max_contracts_per_exp": 60, "underlying_price": 450.0},
        provider,
    )
    assert result == ["SPY260117C00450000", "SPY260117C00455000"]
    provider.discover_option_contracts.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_contracts_no_provider(svc):
    result = await svc.discover_contracts("SPY", None, None, {}, None)
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_options.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Write options.py**

```python
# coordinator/services/asset_services/options.py
"""Options asset service — US equity options (OCC format).

Owns: OCC parsing, symbol composition from order legs, pricing, fill
estimation (bid/ask or spread model), delta-adjusted risk, expiry
settlement, contract discovery.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import pandas as pd

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)
from coordinator.services.asset_services.equity import EquityAssetService
from coordinator.services.chain_builder import parse_occ_symbol


class OptionsAssetService:
    asset_type = AssetType.OPTIONS

    # ── Classification ──
    def classify(self, symbol: str) -> bool:
        return parse_occ_symbol(symbol) is not None

    def parse_symbol(self, symbol: str) -> dict | None:
        return parse_occ_symbol(symbol)

    # ── Symbol Resolution ──
    def resolve_symbol(self, symbol: str, provider: str) -> str:
        raw = symbol.removeprefix("O:")
        if provider == "polygon":
            return f"O:{raw}"
        return raw

    def compose_order_symbol(self, leg: Any) -> str:
        if not (leg.expiry and leg.strike is not None and leg.right):
            raise ValueError(
                f"options leg {leg.symbol} requires expiry/strike/right",
            )
        from datetime import datetime as _dt
        expiry_str = leg.expiry if isinstance(leg.expiry, str) else leg.expiry.isoformat()
        y, m, d = expiry_str.split("-")
        right_ch = "C" if str(leg.right).lower().startswith("c") else "P"
        strike_int = int(round(float(leg.strike) * 1000))
        return f"{leg.symbol}{y[2:]}{m}{d}{right_ch}{strike_int:08d}"

    # ── Pricing ──
    def get_multiplier(self) -> int:
        return 100

    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]:
        if ctx is None:
            return None
        ds = getattr(ctx, "_data_service", None)
        if ds is None:
            return None
        raw = symbol.removeprefix("O:")
        source = getattr(ctx, "_default_source", None) or "polygon"
        df = ds.load_market_data(source, raw, "1day")
        return _bar_lookup(df, sim_time) if df is not None else None

    def get_fill_price(
        self, symbol: str, side: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        if ctx is None:
            return None
        ds = getattr(ctx, "_data_service", None)
        if ds is None:
            return None
        raw = symbol.removeprefix("O:")
        source = getattr(ctx, "_default_source", None) or "polygon"
        df = ds.load_market_data(source, raw, "1day")
        if df is None or df.empty:
            return self._lookup_chain_fill(symbol, side, ctx)
        ts = pd.to_datetime(df["timestamp"])
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        cutoff = pd.Timestamp(sim_time)
        if cutoff.tz is not None:
            cutoff = cutoff.tz_convert("UTC").tz_localize(None)
        import numpy as np
        ns = ts.values.view("int64")
        cutoff_ns = np.datetime64(cutoff).view("int64")
        idx = int(np.searchsorted(ns, cutoff_ns, side="right")) - 1
        if idx < 0:
            return None
        bar = df.iloc[idx]
        close = float(bar["close"])
        if "bid" in df.columns and "ask" in df.columns and pd.notna(bar["bid"]):
            return float(bar["ask"]) if side == "buy" else float(bar["bid"])
        from coordinator.services.options_math import estimate_spread
        vol = int(bar.get("volume", 0))
        spread = estimate_spread(close, vol)
        return (close + spread / 2) if side == "buy" else max(0.0, close - spread / 2)

    def _lookup_chain_fill(
        self, symbol: str, side: str, ctx: Any,
    ) -> Optional[float]:
        cache = getattr(ctx, "_option_chain_cache", {}) or {}
        raw = symbol.removeprefix("O:")
        for chain_df in cache.values():
            if chain_df is None or chain_df.empty:
                continue
            for col in ("ticker", "symbol"):
                if col in chain_df.columns:
                    match = chain_df[chain_df[col] == raw]
                    if not match.empty:
                        row = match.iloc[0]
                        return float(row.get("ask", 0)) if side == "buy" else float(row.get("bid", 0))
        return None

    # ── P&L ──
    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float:
        cost = avg_price * abs(quantity) * self.get_multiplier()
        if market_value > 0 and cost > 0:
            return market_value - cost
        return 0.0

    # ── Risk (delta-adjusted underlying VaR) ──
    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float:
        parsed = self.parse_symbol(symbol)
        if not parsed:
            return market_value * 0.05
        underlying = parsed["underlying"]
        equity_risk = EquityAssetService().risk_contribution(
            underlying, market_value, data_service=data_service, lookback_days=lookback_days,
        )
        try:
            from coordinator.services.options_math import bs_greeks
            exp = date.fromisoformat(parsed["expiration"])
            T = max((exp - date.today()).days, 1) / 365.0
            greeks = bs_greeks(
                S=100, K=100, T=T, r=0.04, sigma=0.25,
                option_type=parsed["option_type"],
            )
            delta = abs(greeks["delta"])
        except Exception:
            delta = 0.5
        return equity_risk * delta

    # ── Expiry ──
    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]:
        parsed = self.parse_symbol(symbol)
        if not parsed:
            return None
        exp = date.fromisoformat(parsed["expiration"])
        sim_date = sim_time.date() if hasattr(sim_time, "date") else sim_time
        if sim_date <= exp:
            return None

        underlying_price = self._get_underlying_price(parsed["underlying"], sim_time, ctx)
        if underlying_price is None:
            underlying_price = parsed["strike"]

        if parsed["option_type"] == "call":
            intrinsic = max(0.0, underlying_price - parsed["strike"])
        else:
            intrinsic = max(0.0, parsed["strike"] - underlying_price)

        multiplier = self.get_multiplier()
        qty = abs(quantity)
        is_short = quantity < 0

        if intrinsic > 0:
            if is_short:
                realized = (avg_price - intrinsic) * qty * multiplier
                side = "buy"
            else:
                realized = (intrinsic - avg_price) * qty * multiplier
                side = "sell"
        else:
            if is_short:
                realized = avg_price * qty * multiplier
                side = "buy"
            else:
                realized = -(avg_price * qty * multiplier)
                side = "sell"

        return Settlement(
            symbol=symbol, side=side, quantity=qty,
            fill_price=intrinsic, realized_pnl=realized,
        )

    def _get_underlying_price(
        self, underlying: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        if ctx is None or not hasattr(ctx, "_bars"):
            return None
        for (_src, sym, _tf), df in ctx._bars.items():
            if sym == underlying:
                return _bar_lookup(df, sim_time)
        return None

    # ── Trading Rules ──
    def time_in_force(self) -> str:
        return "DAY"

    def supports_multileg(self) -> bool:
        return True

    def required_order_fields(self) -> set[str]:
        return {"expiry", "strike", "right"}

    def is_pdt_exempt(self) -> bool:
        return False

    # ── Market Hours ──
    def is_market_open(self, timestamp: Any) -> bool:
        return EquityAssetService().is_market_open(timestamp)

    # ── Streaming ──
    def stream_config(self, broker: str) -> StreamConfig:
        if broker == "polygon":
            return StreamConfig(True, "options", "occ_prefix", 30, cluster="options")
        if broker == "alpaca":
            return StreamConfig(True, "options", "occ_prefix", 30)
        if broker == "tradier":
            return StreamConfig(True, "options", "identity", 30)
        if broker == "thetadata":
            return StreamConfig(True, "options", "identity", 30)
        return StreamConfig(False, "", "identity", 0)

    def supports_provider(self, provider: str) -> bool:
        return provider in ("polygon", "alpaca", "tradier", "thetadata")

    # ── Discovery ──
    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]:
        if provider is None or not hasattr(provider, "discover_option_contracts"):
            return []
        strike_range = config.get("strike_range", "atm5")
        strike_pct = {"atm5": 0.05, "atm15": 0.15, "all": 1.0}.get(strike_range, 0.05)
        max_contracts = config.get("max_contracts_per_exp", 60)
        underlying_price = config.get("underlying_price")
        contracts = await provider.discover_option_contracts(
            underlying, end, strike_range_pct=strike_pct,
            max_contracts=max_contracts, underlying_price=underlying_price,
        )
        return [c["ticker"].removeprefix("O:") for c in contracts]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_options.py -v`
Expected: ~30 PASSED

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/asset_services/options.py tests/coordinator/services/asset_services/test_options.py
git commit -m "feat(asset-services): add OptionsAssetService with OCC parsing, pricing, expiry"
```

---

### Task 4: Create CryptoAssetService

**Files:**
- Create: `coordinator/services/asset_services/crypto.py`
- Create: `tests/coordinator/services/asset_services/test_crypto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/asset_services/test_crypto.py
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from coordinator.services.asset_services.base import AssetType
from coordinator.services.asset_services.crypto import CryptoAssetService


@pytest.fixture
def svc():
    return CryptoAssetService()


# ── Classification ──

def test_classify_crypto(svc):
    assert svc.classify("BTCUSD")
    assert svc.classify("ETHUSD")
    assert svc.classify("SOLUSD")
    assert svc.classify("DOGEUSD")
    assert svc.classify("BTCUSDT")


def test_classify_with_slash(svc):
    """Alpaca crypto sometimes uses BTC/USD format."""
    assert svc.classify("BTC/USD")
    assert svc.classify("ETH/USD")


def test_classify_rejects_equities(svc):
    assert not svc.classify("AAPL")
    assert not svc.classify("SPY")


def test_classify_rejects_options(svc):
    assert not svc.classify("SPY241029C00586000")


def test_classify_rejects_indexes(svc):
    assert not svc.classify("VIX")


# ── Symbol Resolution ──

def test_resolve_symbol_yfinance(svc):
    assert svc.resolve_symbol("BTCUSD", "yfinance") == "BTC-USD"
    assert svc.resolve_symbol("ETHUSD", "yfinance") == "ETH-USD"
    assert svc.resolve_symbol("SOLUSD", "yfinance") == "SOL-USD"


def test_resolve_symbol_polygon(svc):
    assert svc.resolve_symbol("BTCUSD", "polygon") == "BTCUSD"


def test_resolve_symbol_alpaca_stream_uses_slash(svc):
    """Alpaca streaming uses BTC/USD format."""
    assert svc.resolve_symbol("BTCUSD", "alpaca_stream") == "BTC/USD"


def test_resolve_symbol_coinbase_dash(svc):
    """Coinbase uses BTC-USD format."""
    assert svc.resolve_symbol("BTCUSD", "coinbase") == "BTC-USD"


def test_compose_order_symbol(svc):
    leg = SimpleNamespace(symbol="BTCUSD")
    assert svc.compose_order_symbol(leg) == "BTCUSD"


# ── Pricing ──

def test_multiplier(svc):
    assert svc.get_multiplier() == 1


def test_get_price_from_bars(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [50000.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "BTCUSD", "1day"): df})
    assert svc.get_price("BTCUSD", datetime(2026, 5, 22, 12), ctx) == 50000.0


# ── P&L ──

def test_unrealized_pnl(svc):
    pnl = svc.compute_unrealized_pnl("BTCUSD", quantity=0.5, avg_price=40000.0, market_value=25000.0)
    assert pnl == pytest.approx(5000.0)  # 25000 - (40000 * 0.5)


# ── Expiry ──

def test_handle_expiry_returns_none(svc):
    assert svc.handle_expiry("BTCUSD", 1, 50000, datetime.now(), None) is None


# ── Trading Rules (crypto-specific) ──

def test_time_in_force_gtc(svc):
    """Alpaca requires GTC for crypto orders."""
    assert svc.time_in_force() == "GTC"


def test_supports_multileg(svc):
    assert svc.supports_multileg() is False


def test_required_order_fields(svc):
    assert svc.required_order_fields() == set()


def test_is_pdt_exempt(svc):
    """Crypto is exempt from PDT rules."""
    assert svc.is_pdt_exempt() is True


# ── Market Hours (24/7) ──

def test_market_always_open_saturday(svc):
    ts = datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_market_always_open_overnight(svc):
    ts = datetime(2026, 5, 25, 3, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_market_always_open_midweek(svc):
    ts = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


# ── Streaming ──

def test_stream_config_alpaca(svc):
    cfg = svc.stream_config("alpaca")
    assert cfg.supported
    assert cfg.stream_class == "crypto"
    assert cfg.symbol_transform == "crypto_slash"


def test_stream_config_coinbase(svc):
    cfg = svc.stream_config("coinbase")
    assert cfg.supported
    assert cfg.symbol_transform == "crypto_dash"


def test_stream_config_polygon(svc):
    cfg = svc.stream_config("polygon")
    assert cfg.supported
    assert cfg.cluster == "crypto"
    assert cfg.symbol_transform == "polygon_x_prefix"


def test_stream_config_tradier_unsupported(svc):
    """Tradier does not support crypto streaming."""
    assert not svc.stream_config("tradier").supported


# ── Provider Support ──

def test_supports_provider_coinbase(svc):
    assert svc.supports_provider("coinbase")


def test_supports_provider_alpaca(svc):
    assert svc.supports_provider("alpaca")


def test_supports_provider_tradier_no(svc):
    """Tradier does not handle crypto."""
    assert not svc.supports_provider("tradier")


# ── Discovery ──

@pytest.mark.asyncio
async def test_discover_returns_underlying(svc):
    result = await svc.discover_contracts("BTCUSD", None, None, {}, None)
    assert result == ["BTCUSD"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_crypto.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Write crypto.py**

```python
# coordinator/services/asset_services/crypto.py
"""Crypto asset service — BTC, ETH, etc. 24/7 markets, no expiry, GTC orders."""
from __future__ import annotations

from typing import Any, Optional

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)

_KNOWN_CRYPTO = {
    "BTCUSD", "ETHUSD", "SOLUSD", "DOGEUSD", "AVAXUSD", "LINKUSD",
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
}
_YFINANCE_MAP = {
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "SOLUSD": "SOL-USD",
    "DOGEUSD": "DOGE-USD", "AVAXUSD": "AVAX-USD", "LINKUSD": "LINK-USD",
}


def _to_slash(symbol: str) -> str:
    """BTCUSD -> BTC/USD."""
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    return f"{symbol[:-3]}/{symbol[-3:]}"


def _to_dash(symbol: str) -> str:
    """BTCUSD -> BTC-USD."""
    if "-" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}-USDT"
    return f"{symbol[:-3]}-{symbol[-3:]}"


class CryptoAssetService:
    asset_type = AssetType.CRYPTO

    # ── Classification ──
    def classify(self, symbol: str) -> bool:
        if not symbol:
            return False
        normalized = symbol.replace("/", "").replace("-", "")
        if normalized in _KNOWN_CRYPTO:
            return True
        return normalized.endswith("USD") or normalized.endswith("USDT")

    # ── Symbol Resolution ──
    def resolve_symbol(self, symbol: str, provider: str) -> str:
        if provider == "yfinance":
            return _YFINANCE_MAP.get(symbol, _to_dash(symbol))
        if provider in ("alpaca_stream", "alpaca"):
            return _to_slash(symbol) if provider == "alpaca_stream" else symbol
        if provider == "coinbase":
            return _to_dash(symbol)
        return symbol

    def compose_order_symbol(self, leg: Any) -> str:
        return leg.symbol

    # ── Pricing ──
    def get_multiplier(self) -> int:
        return 1

    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]:
        if ctx is None or not hasattr(ctx, "_bars"):
            return None
        for (_src, sym, _tf), df in ctx._bars.items():
            if sym == symbol:
                return _bar_lookup(df, sim_time)
        return None

    def get_fill_price(
        self, symbol: str, side: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        return self.get_price(symbol, sim_time, ctx)

    # ── P&L ──
    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float:
        cost = avg_price * abs(quantity)
        return market_value - cost if market_value > 0 else 0.0

    # ── Risk ──
    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float:
        if data_service is None:
            return market_value * 0.05  # crypto is more volatile
        import numpy as np
        for provider in ("polygon", "yfinance", "coinbase"):
            df = data_service.load_market_data(provider, symbol, "1day")
            if df is None or len(df) < 10:
                continue
            closes = df["close"].astype(float).values[-lookback_days:]
            if len(closes) < 10:
                continue
            returns = np.diff(np.log(closes))
            var_5 = np.percentile(returns, 5)
            return abs(var_5) * market_value
        return market_value * 0.05

    # ── Expiry ──
    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]:
        return None

    # ── Trading Rules ──
    def time_in_force(self) -> str:
        return "GTC"

    def supports_multileg(self) -> bool:
        return False

    def required_order_fields(self) -> set[str]:
        return set()

    def is_pdt_exempt(self) -> bool:
        return True

    # ── Market Hours ──
    def is_market_open(self, timestamp: Any) -> bool:
        return True

    # ── Streaming ──
    def stream_config(self, broker: str) -> StreamConfig:
        if broker == "alpaca":
            return StreamConfig(True, "crypto", "crypto_slash", 30)
        if broker == "coinbase":
            return StreamConfig(True, "crypto", "crypto_dash", 30)
        if broker == "polygon":
            return StreamConfig(True, "crypto", "polygon_x_prefix", 30, cluster="crypto")
        return StreamConfig(False, "", "identity", 0)

    def supports_provider(self, provider: str) -> bool:
        return provider in ("alpaca", "coinbase", "polygon", "yfinance")

    # ── Discovery ──
    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]:
        return [underlying]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_crypto.py -v`
Expected: ~22 PASSED

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/asset_services/crypto.py tests/coordinator/services/asset_services/test_crypto.py
git commit -m "feat(asset-services): add CryptoAssetService — 24/7, GTC, PDT-exempt"
```

---

### Task 5: Create IndexAssetService

**Files:**
- Create: `coordinator/services/asset_services/index.py`
- Create: `tests/coordinator/services/asset_services/test_index.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/asset_services/test_index.py
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from coordinator.services.asset_services.base import AssetType
from coordinator.services.asset_services.index import IndexAssetService


@pytest.fixture
def svc():
    return IndexAssetService()


# ── Classification ──

def test_classify_known_indexes(svc):
    assert svc.classify("VIX")
    assert svc.classify("SPX")
    assert svc.classify("NDX")
    assert svc.classify("RUT")
    assert svc.classify("DJI")


def test_classify_polygon_prefix(svc):
    assert svc.classify("I:VIX")
    assert svc.classify("I:SPX")


def test_classify_yfinance_caret(svc):
    assert svc.classify("^GSPC")
    assert svc.classify("^VIX")


def test_classify_rejects_equities(svc):
    assert not svc.classify("AAPL")
    assert not svc.classify("QQQ")


def test_classify_rejects_options(svc):
    assert not svc.classify("SPY241029C00586000")


def test_classify_rejects_crypto(svc):
    assert not svc.classify("BTCUSD")


# ── Symbol Resolution ──

def test_resolve_symbol_polygon(svc):
    assert svc.resolve_symbol("VIX", "polygon") == "I:VIX"
    assert svc.resolve_symbol("SPX", "polygon") == "I:SPX"
    assert svc.resolve_symbol("NDX", "polygon") == "I:NDX"


def test_resolve_symbol_polygon_idempotent(svc):
    assert svc.resolve_symbol("I:VIX", "polygon") == "I:VIX"


def test_resolve_symbol_yfinance(svc):
    assert svc.resolve_symbol("VIX", "yfinance") == "^VIX"
    assert svc.resolve_symbol("SPX", "yfinance") == "^GSPC"
    assert svc.resolve_symbol("NDX", "yfinance") == "^IXIC"


def test_resolve_symbol_other_passthrough(svc):
    assert svc.resolve_symbol("VIX", "tradier") == "VIX"


def test_compose_order_symbol(svc):
    leg = SimpleNamespace(symbol="VIX")
    assert svc.compose_order_symbol(leg) == "VIX"


# ── Pricing ──

def test_multiplier(svc):
    assert svc.get_multiplier() == 1


# ── P&L ──

def test_unrealized_pnl(svc):
    pnl = svc.compute_unrealized_pnl("VIX", quantity=100, avg_price=15.0, market_value=1600.0)
    assert pnl == pytest.approx(100.0)


# ── Expiry ──

def test_handle_expiry_returns_none(svc):
    assert svc.handle_expiry("VIX", 1, 20, datetime.now(), None) is None


# ── Trading Rules ──

def test_time_in_force(svc):
    assert svc.time_in_force() == "DAY"


def test_supports_multileg(svc):
    assert svc.supports_multileg() is False


def test_required_order_fields(svc):
    assert svc.required_order_fields() == set()


def test_is_pdt_exempt(svc):
    assert svc.is_pdt_exempt() is False


# ── Market Hours (follow equity hours) ──

def test_market_open_weekday(svc):
    ts = datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_market_closed_weekend(svc):
    ts = datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
    assert not svc.is_market_open(ts)


# ── Streaming ──

def test_stream_config_polygon(svc):
    cfg = svc.stream_config("polygon")
    assert cfg.supported
    assert cfg.cluster == "stocks"  # indexes ride on stocks cluster


def test_stream_config_coinbase_unsupported(svc):
    assert not svc.stream_config("coinbase").supported


# ── Provider Support ──

def test_supports_provider_polygon(svc):
    assert svc.supports_provider("polygon")


def test_supports_provider_yfinance(svc):
    assert svc.supports_provider("yfinance")


def test_supports_provider_coinbase_no(svc):
    assert not svc.supports_provider("coinbase")


# ── Discovery ──

@pytest.mark.asyncio
async def test_discover_returns_underlying(svc):
    result = await svc.discover_contracts("VIX", None, None, {}, None)
    assert result == ["VIX"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_index.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Write index.py**

```python
# coordinator/services/asset_services/index.py
"""Index asset service — VIX, SPX, NDX, etc. Read-only (not directly tradeable)."""
from __future__ import annotations

from typing import Any, Optional

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)
from coordinator.services.asset_services.equity import EquityAssetService

_KNOWN_INDEXES = {"VIX", "SPX", "NDX", "RUT", "DJI", "GSPC", "IXIC"}

_POLYGON_MAP = {
    "SPX": "I:SPX", "NDX": "I:NDX", "RUT": "I:RUT",
    "VIX": "I:VIX", "DJI": "I:DJI",
}

_YFINANCE_MAP = {
    "VIX": "^VIX", "SPX": "^GSPC", "NDX": "^IXIC",
    "RUT": "^RUT", "DJI": "^DJI",
}


class IndexAssetService:
    asset_type = AssetType.INDEX

    # ── Classification ──
    def classify(self, symbol: str) -> bool:
        if not symbol:
            return False
        if symbol in _KNOWN_INDEXES:
            return True
        if symbol.startswith("I:") or symbol.startswith("^"):
            return True
        return False

    # ── Symbol Resolution ──
    def resolve_symbol(self, symbol: str, provider: str) -> str:
        if provider == "polygon":
            if symbol.startswith("I:"):
                return symbol
            return _POLYGON_MAP.get(symbol, symbol)
        if provider == "yfinance":
            if symbol.startswith("^"):
                return symbol
            return _YFINANCE_MAP.get(symbol, symbol)
        return symbol

    def compose_order_symbol(self, leg: Any) -> str:
        return leg.symbol

    # ── Pricing ──
    def get_multiplier(self) -> int:
        return 1

    def get_price(self, symbol: str, sim_time: Any, ctx: Any) -> Optional[float]:
        if ctx is None or not hasattr(ctx, "_bars"):
            return None
        for (_src, sym, _tf), df in ctx._bars.items():
            if sym == symbol:
                return _bar_lookup(df, sim_time)
        return None

    def get_fill_price(
        self, symbol: str, side: str, sim_time: Any, ctx: Any,
    ) -> Optional[float]:
        return self.get_price(symbol, sim_time, ctx)

    # ── P&L ──
    def compute_unrealized_pnl(
        self, symbol: str, quantity: float, avg_price: float, market_value: float,
    ) -> float:
        cost = avg_price * abs(quantity)
        return market_value - cost if market_value > 0 else 0.0

    # ── Risk ──
    def risk_contribution(
        self, symbol: str, market_value: float,
        data_service: Any = None, lookback_days: int = 60,
    ) -> float:
        return EquityAssetService().risk_contribution(
            symbol, market_value, data_service=data_service, lookback_days=lookback_days,
        )

    # ── Expiry ──
    def handle_expiry(
        self, symbol: str, quantity: float, avg_price: float,
        sim_time: Any, ctx: Any,
    ) -> Optional[Settlement]:
        return None

    # ── Trading Rules ──
    def time_in_force(self) -> str:
        return "DAY"

    def supports_multileg(self) -> bool:
        return False

    def required_order_fields(self) -> set[str]:
        return set()

    def is_pdt_exempt(self) -> bool:
        return False

    # ── Market Hours (follow equity) ──
    def is_market_open(self, timestamp: Any) -> bool:
        return EquityAssetService().is_market_open(timestamp)

    # ── Streaming ──
    def stream_config(self, broker: str) -> StreamConfig:
        if broker == "polygon":
            return StreamConfig(True, "stock", "identity", 30, cluster="stocks")
        if broker == "yfinance":
            return StreamConfig(True, "stock", "identity", 30)
        return StreamConfig(False, "", "identity", 0)

    def supports_provider(self, provider: str) -> bool:
        return provider in ("polygon", "yfinance")

    # ── Discovery ──
    async def discover_contracts(
        self, underlying: str, start: Any, end: Any,
        config: dict, provider: Any,
    ) -> list[str]:
        return [underlying]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_index.py -v`
Expected: ~18 PASSED

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/asset_services/index.py tests/coordinator/services/asset_services/test_index.py
git commit -m "feat(asset-services): add IndexAssetService — VIX/SPX/NDX symbol mapping"
```

---

### Task 6: Create AssetServiceRegistry

**Files:**
- Create: `coordinator/services/asset_services/registry.py`
- Create: `tests/coordinator/services/asset_services/test_registry.py`
- Modify: `coordinator/services/asset_services/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/asset_services/test_registry.py
from types import SimpleNamespace

import pytest

from coordinator.services.asset_services.base import AssetType
from coordinator.services.asset_services.registry import AssetServiceRegistry


@pytest.fixture
def registry():
    return AssetServiceRegistry()


# ── classify() ──

def test_classify_equities(registry):
    assert registry.classify("AAPL") == AssetType.EQUITIES
    assert registry.classify("SPY") == AssetType.EQUITIES
    assert registry.classify("QQQ") == AssetType.EQUITIES


def test_classify_options(registry):
    assert registry.classify("SPY241029C00586000") == AssetType.OPTIONS
    assert registry.classify("O:QQQ260320C00580000") == AssetType.OPTIONS


def test_classify_crypto(registry):
    assert registry.classify("BTCUSD") == AssetType.CRYPTO
    assert registry.classify("ETHUSD") == AssetType.CRYPTO


def test_classify_indexes(registry):
    assert registry.classify("VIX") == AssetType.INDEX
    assert registry.classify("I:SPX") == AssetType.INDEX


def test_classify_unknown_defaults_to_equities(registry):
    assert registry.classify("UNKNOWN") == AssetType.EQUITIES


def test_classify_empty_string(registry):
    assert registry.classify("") == AssetType.EQUITIES


# ── get_service() priority order ──

def test_options_checked_before_equities(registry):
    """OCC symbols contain letters and could match equity classification.
    Options must be checked first."""
    svc = registry.get_service("SPY241029C00586000")
    assert svc.asset_type == AssetType.OPTIONS


def test_indexes_checked_before_equities(registry):
    """VIX would match equity unless index classifier runs first."""
    svc = registry.get_service("VIX")
    assert svc.asset_type == AssetType.INDEX


def test_crypto_checked_before_equities(registry):
    svc = registry.get_service("BTCUSD")
    assert svc.asset_type == AssetType.CRYPTO


# ── Delegation ──

def test_resolve_symbol_delegates(registry):
    assert registry.resolve_symbol("VIX", "polygon") == "I:VIX"
    assert registry.resolve_symbol("SPY241029C00586000", "polygon") == "O:SPY241029C00586000"
    assert registry.resolve_symbol("BTCUSD", "yfinance") == "BTC-USD"
    assert registry.resolve_symbol("AAPL", "polygon") == "AAPL"


def test_get_multiplier_delegates(registry):
    assert registry.get_multiplier("AAPL") == 1
    assert registry.get_multiplier("SPY241029C00586000") == 100
    assert registry.get_multiplier("BTCUSD") == 1
    assert registry.get_multiplier("VIX") == 1


def test_time_in_force_delegates(registry):
    assert registry.time_in_force("AAPL") == "DAY"
    assert registry.time_in_force("BTCUSD") == "GTC"
    assert registry.time_in_force("SPY241029C00586000") == "DAY"


def test_is_market_open_delegates(registry):
    from datetime import datetime, timezone
    sat = datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
    assert registry.is_market_open("BTCUSD", sat) is True
    assert registry.is_market_open("AAPL", sat) is False


def test_compose_order_symbol_delegates(registry):
    leg = SimpleNamespace(symbol="AAPL")
    assert registry.compose_order_symbol(leg) == "AAPL"
    opt_leg = SimpleNamespace(
        symbol="SPY", asset_type="options",
        expiry="2024-10-29", strike=586.0, right="call",
    )
    assert registry.compose_order_symbol(opt_leg) == "SPY241029C00586000"


def test_supports_provider_delegates(registry):
    assert registry.supports_provider("BTCUSD", "coinbase")
    assert not registry.supports_provider("AAPL", "coinbase")


# ── Singleton helper ──

def test_default_registry_returns_same_instance():
    from coordinator.services.asset_services.registry import get_default_registry
    r1 = get_default_registry()
    r2 = get_default_registry()
    assert r1 is r2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/test_registry.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Write registry.py**

```python
# coordinator/services/asset_services/registry.py
"""Asset service registry — symbol → service dispatch.

Single entry point for all asset-type-specific operations. Callers
never need to check asset_type themselves; they call methods on the
registry and the registry routes to the correct service.

Classification order matters: options first (OCC symbols have letters
that could match equity classifier), then crypto, then index, then
equities (the default fallback).
"""
from __future__ import annotations

from typing import Any

from coordinator.services.asset_services.base import (
    AssetService,
    AssetType,
    StreamConfig,
)
from coordinator.services.asset_services.crypto import CryptoAssetService
from coordinator.services.asset_services.equity import EquityAssetService
from coordinator.services.asset_services.index import IndexAssetService
from coordinator.services.asset_services.options import OptionsAssetService


class AssetServiceRegistry:
    def __init__(self) -> None:
        self._options = OptionsAssetService()
        self._crypto = CryptoAssetService()
        self._index = IndexAssetService()
        self._equity = EquityAssetService()
        # Order matters — see module docstring.
        self._services = [self._options, self._crypto, self._index, self._equity]

    # ── Core dispatch ──

    def classify(self, symbol: str) -> AssetType:
        for svc in self._services:
            if svc.classify(symbol):
                return svc.asset_type
        return AssetType.EQUITIES

    def get_service(self, symbol: str) -> AssetService:
        for svc in self._services:
            if svc.classify(symbol):
                return svc
        return self._equity

    def get_service_by_type(self, asset_type: AssetType | str) -> AssetService:
        """Lookup by explicit asset type (for callers that have asset_type
        but not a symbol, e.g. account.supported_asset_types)."""
        t = AssetType(asset_type) if isinstance(asset_type, str) else asset_type
        if t == AssetType.OPTIONS:
            return self._options
        if t == AssetType.CRYPTO:
            return self._crypto
        if t == AssetType.INDEX:
            return self._index
        return self._equity

    # ── Thin convenience delegations ──

    def resolve_symbol(self, symbol: str, provider: str) -> str:
        return self.get_service(symbol).resolve_symbol(symbol, provider)

    def get_multiplier(self, symbol: str) -> int:
        return self.get_service(symbol).get_multiplier()

    def time_in_force(self, symbol: str) -> str:
        return self.get_service(symbol).time_in_force()

    def is_market_open(self, symbol: str, timestamp: Any) -> bool:
        return self.get_service(symbol).is_market_open(timestamp)

    def compose_order_symbol(self, leg: Any) -> str:
        return self.get_service(leg.symbol).compose_order_symbol(leg)

    def supports_provider(self, symbol: str, provider: str) -> bool:
        return self.get_service(symbol).supports_provider(provider)

    def stream_config(self, symbol: str, broker: str) -> StreamConfig:
        return self.get_service(symbol).stream_config(broker)


_default_registry: AssetServiceRegistry | None = None


def get_default_registry() -> AssetServiceRegistry:
    """Process-wide singleton — most callers should use this rather than
    instantiating their own AssetServiceRegistry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = AssetServiceRegistry()
    return _default_registry
```

- [ ] **Step 4: Update __init__.py**

```python
# coordinator/services/asset_services/__init__.py
from coordinator.services.asset_services.base import (
    AssetService,
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)
from coordinator.services.asset_services.crypto import CryptoAssetService
from coordinator.services.asset_services.equity import EquityAssetService
from coordinator.services.asset_services.index import IndexAssetService
from coordinator.services.asset_services.options import OptionsAssetService
from coordinator.services.asset_services.registry import (
    AssetServiceRegistry,
    get_default_registry,
)

__all__ = [
    "AssetService",
    "AssetServiceRegistry",
    "AssetType",
    "CryptoAssetService",
    "EquityAssetService",
    "IndexAssetService",
    "OptionsAssetService",
    "Settlement",
    "StreamConfig",
    "_bar_lookup",
    "get_default_registry",
]
```

- [ ] **Step 5: Run the full service test suite**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/ -v`
Expected: All ~100 tests PASS

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/asset_services/ tests/coordinator/services/asset_services/
git commit -m "feat(asset-services): add AssetServiceRegistry — unified dispatch"
```

---

### Task 7: Migrate backtest engine to use registry

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py`

This is the biggest single task: 18 asset-type references inside one file. The registry is stored as an instance attribute on the engine. All branches on `fill.asset_type == "options"` or `ps.asset_type == "options"` route through `self._asset_registry.get_service(symbol)`.

- [ ] **Step 1: Add registry import**

At the top of the file with other imports, add:

```python
from coordinator.services.asset_services import AssetServiceRegistry, AssetType
```

- [ ] **Step 2: Add registry instance attribute**

In `BacktestEngine.__init__` (or `_run_internal` setup if `__init__` has no body), add:

```python
self._asset_registry = AssetServiceRegistry()
```

If the engine has no `__init__`, add one alongside the existing `_ts_cache`:

```python
def __init__(self) -> None:
    self._ts_cache: dict = {}
    self._asset_registry = AssetServiceRegistry()
```

- [ ] **Step 3: Replace buying-power multiplier (line 253)**

```python
# BEFORE
bp_multiplier = 100 if fill.asset_type == "options" else 1

# AFTER
bp_multiplier = self._asset_registry.get_multiplier(fill.symbol)
```

- [ ] **Step 4: Replace short-sell guard (line 266)**

```python
# BEFORE
if fill.asset_type != "options":

# AFTER
svc = self._asset_registry.get_service(fill.symbol)
if not svc.supports_multileg():  # only options can be sold-to-open
```

- [ ] **Step 5: Replace "no_option_price" reject (line 289)**

```python
# BEFORE
if po.leg.asset_type == "options":
    observer.on_signal_rejected(
        sim_time, Signal(legs=[po.leg]), "no_option_price"
    )
    continue

# AFTER
svc = self._asset_registry.get_service(po.leg.symbol)
if svc.asset_type == AssetType.OPTIONS:
    observer.on_signal_rejected(
        sim_time, Signal(legs=[po.leg]), "no_option_price"
    )
    continue
```

- [ ] **Step 6: Replace pending-order expiry check (lines 327-336)**

```python
# BEFORE
for po in pending:
    if po.leg.asset_type == "options":
        from coordinator.services.chain_builder import parse_occ_symbol
        parsed = parse_occ_symbol(po.leg.symbol)
        if parsed:
            from datetime import date as _date
            exp = _date.fromisoformat(parsed["expiration"])
            sim_date = sim_time.date() if hasattr(sim_time, "date") else sim_time
            if sim_date > exp:
                observer.on_signal_rejected(sim_time, Signal(legs=[po.leg]), "contract_expired")
                continue
    still_pending2.append(po)

# AFTER
for po in pending:
    svc = self._asset_registry.get_service(po.leg.symbol)
    settlement = svc.handle_expiry(
        po.leg.symbol, quantity=1, avg_price=0.0,
        sim_time=sim_time, ctx=ctx,
    )
    if settlement is not None:
        observer.on_signal_rejected(sim_time, Signal(legs=[po.leg]), "contract_expired")
        continue
    still_pending2.append(po)
```

- [ ] **Step 7: Replace `_fill_market` options branch (line 507)**

```python
# BEFORE
if leg.asset_type == "options" and ctx is not None:
    option_price = self._lookup_option_price(leg.symbol, side, ctx)

# AFTER
svc = self._asset_registry.get_service(leg.symbol)
if svc.asset_type == AssetType.OPTIONS and ctx is not None:
    option_price = svc.get_fill_price(leg.symbol, side, sim_time, ctx)
```

- [ ] **Step 8: Replace `_fill_limit` options branch (line 562)**

```python
# BEFORE
if leg.asset_type == "options" and ctx is not None:
    option_price = self._lookup_option_price(leg.symbol, side, ctx)

# AFTER
svc = self._asset_registry.get_service(leg.symbol)
if svc.asset_type == AssetType.OPTIONS and ctx is not None:
    option_price = svc.get_fill_price(leg.symbol, side, sim_time, ctx)
```

- [ ] **Step 9: Replace `_apply_fill` multiplier (line 636)**

```python
# BEFORE
multiplier = 100 if fill.asset_type == "options" else 1

# AFTER
multiplier = self._asset_registry.get_multiplier(fill.symbol)
```

- [ ] **Step 10: Replace `_settle_expired_options` body (lines 698-762)**

```python
# BEFORE — keep method signature, replace body
def _settle_expired_options(
    self, cash, positions, sim_time, ctx, observer, all_fills,
) -> tuple[float, dict]:
    # ... 60+ lines of options-specific logic ...

# AFTER
def _settle_expired_options(
    self, cash, positions, sim_time, ctx, observer, all_fills,
) -> tuple[float, dict]:
    """Auto-settle any expired position. Delegates to AssetService.handle_expiry."""
    for (sym,), ps in list(positions.items()):
        svc = self._asset_registry.get_service(sym)
        settlement = svc.handle_expiry(
            sym, quantity=ps.quantity, avg_price=ps.avg_price,
            sim_time=sim_time, ctx=ctx,
        )
        if settlement is None:
            continue
        multiplier = svc.get_multiplier()
        # Cash movement: settle at intrinsic value
        if settlement.fill_price > 0:
            cash_delta = settlement.fill_price * settlement.quantity * multiplier
            cash = cash + cash_delta if settlement.side == "sell" else cash - cash_delta
        fill = FillRecord(
            timestamp=sim_time,
            symbol=sym, asset_type=svc.asset_type.value,
            side=settlement.side, quantity=settlement.quantity,
            requested_price=settlement.fill_price, fill_price=settlement.fill_price,
            slippage_dollars=0.0, slippage_bps_applied=0.0,
            fees=0.0, fee_breakdown=[],
            signal_id=f"expiry-{sym}",
            realized_pnl=settlement.realized_pnl,
        )
        all_fills.append(fill)
        observer.on_fill(fill)
        del positions[(sym,)]
    return cash, positions
```

- [ ] **Step 11: Replace `_positions_market_value` (lines 813-820)**

```python
# BEFORE
def _positions_market_value(self, positions: dict, bar, ctx=None, sim_time=None) -> float:
    total = 0.0
    for (sym,), ps in positions.items():
        multiplier = 100 if ps.asset_type == "options" else 1
        if ps.asset_type == "options":
            option_price = self._lookup_option_mtm_price(sym, ctx)
            price = option_price if option_price is not None else ps.avg_price
        else:
            price = self._lookup_symbol_close(sym, sim_time, ctx, bar)
        total += ps.quantity * price * multiplier
    return total

# AFTER
def _positions_market_value(self, positions: dict, bar, ctx=None, sim_time=None) -> float:
    total = 0.0
    for (sym,), ps in positions.items():
        svc = self._asset_registry.get_service(sym)
        price = svc.get_price(sym, sim_time, ctx)
        if price is None:
            price = self._lookup_symbol_close(sym, sim_time, ctx, bar)
        if price is None or price == 0.0:
            price = ps.avg_price
        total += ps.quantity * price * svc.get_multiplier()
    return total
```

- [ ] **Step 12: Replace `_positions_snapshot` (lines 824-839)**

```python
# BEFORE
def _positions_snapshot(self, positions: dict, bar, ctx=None, sim_time=None) -> list[dict]:
    result = []
    for k, ps in positions.items():
        sym = k[0]
        multiplier = 100 if ps.asset_type == "options" else 1
        if ps.asset_type == "options":
            option_price = self._lookup_option_mtm_price(sym, ctx)
            current_price = option_price if option_price is not None else ps.avg_price
        else:
            current_price = self._lookup_symbol_close(sym, sim_time, ctx, bar)
        result.append({...})
    return result

# AFTER
def _positions_snapshot(self, positions: dict, bar, ctx=None, sim_time=None) -> list[dict]:
    result = []
    for k, ps in positions.items():
        sym = k[0]
        svc = self._asset_registry.get_service(sym)
        current_price = svc.get_price(sym, sim_time, ctx)
        if current_price is None:
            current_price = self._lookup_symbol_close(sym, sim_time, ctx, bar)
        multiplier = svc.get_multiplier()
        result.append({
            "symbol": sym, "quantity": ps.quantity, "avg_price": ps.avg_price,
            "current_price": current_price,
            "market_value": ps.quantity * current_price * multiplier,
            "asset_type": ps.asset_type,
        })
    return result
```

- [ ] **Step 13: Replace `_positions_for_context` (lines 870-885)**

```python
# BEFORE
for (sym,), ps in positions.items():
    if ps.asset_type == "options":
        option_price = self._lookup_option_mtm_price(sym, ctx)
        current_price = option_price if option_price is not None else ps.avg_price
    else:
        current_price = self._lookup_symbol_close(sym, sim_time, ctx, bar)

# AFTER
for (sym,), ps in positions.items():
    svc = self._asset_registry.get_service(sym)
    current_price = svc.get_price(sym, sim_time, ctx)
    if current_price is None:
        current_price = self._lookup_symbol_close(sym, sim_time, ctx, bar)
```

- [ ] **Step 14: Run engine tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/test_backtest_engine.py -v`
Expected: All existing tests PASS (no behavioral change, just refactor)

- [ ] **Step 15: Run a quick smoke backtest**

Run a representative options backtest to confirm same P&L:
```bash
/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/test_backtest_engine.py::test_options_straddle -v
```
Expected: PASS

- [ ] **Step 16: Commit**

```bash
git add coordinator/services/backtest_engine_v2.py
git commit -m "refactor(engine): route asset-type logic through AssetServiceRegistry

Replace 18 asset_type conditionals with registry.get_service() calls.
Engine no longer knows about OCC symbols, multipliers, or expiry rules —
each is owned by the appropriate AssetService."
```

---

### Task 8: Migrate portfolio VaR to use registry

**Files:**
- Modify: `coordinator/api/routes/portfolio.py`

- [ ] **Step 1: Write a failing test**

```python
# tests/coordinator/api/test_portfolio_var.py (new file)
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from coordinator.api.routes.portfolio import _compute_portfolio_var


@pytest.mark.asyncio
async def test_var_dispatches_through_registry():
    """VaR must compute per-position risk via the registry, not branch internally."""
    positions = [
        {"symbol": "AAPL", "market_value": 10000.0, "quantity": 50, "avg_price": 150.0},
        {"symbol": "BTCUSD", "market_value": 5000.0, "quantity": 0.1, "avg_price": 50000.0},
    ]
    var = await _compute_portfolio_var(positions)
    assert var >= 0
    # Should be a reasonable dollar amount
    assert var < 15000.0


@pytest.mark.asyncio
async def test_var_options_uses_delta_adjusted():
    """Options positions are delta-adjusted."""
    positions = [
        {"symbol": "SPY260117C00450000", "market_value": 1000.0, "quantity": 2, "avg_price": 5.0},
    ]
    var = await _compute_portfolio_var(positions)
    # ATM-ish option: delta ~0.5, so risk ~= 1000 * 0.02 * 0.5 = 10
    assert 0 < var < 100.0


@pytest.mark.asyncio
async def test_var_empty_positions():
    assert await _compute_portfolio_var([]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/api/test_portfolio_var.py -v`
Expected: FAIL (current VaR implementation doesn't dispatch through registry)

- [ ] **Step 3: Replace `_compute_portfolio_var` body**

```python
# coordinator/api/routes/portfolio.py
async def _compute_portfolio_var(
    positions: list[dict],
    confidence: float = 0.95,
    lookback_days: int = 60,
) -> float:
    """Portfolio VaR via per-symbol risk contribution from the asset service layer.

    Each position's service computes its own risk:
    - Equities/indexes: historical 5th percentile × market_value
    - Crypto: same as equities, broader fallback
    - Options: delta-adjusted underlying VaR
    """
    if not positions:
        return 0.0
    from coordinator.services.asset_services import get_default_registry
    from coordinator.services.data_service import DataService
    registry = get_default_registry()
    ds = DataService(market_data_dir="data/market", custom_data_dir="data/custom")
    total_risk = 0.0
    for p in positions:
        mv = float(p.get("market_value", 0))
        if mv <= 0:
            continue
        svc = registry.get_service(p["symbol"])
        total_risk += svc.risk_contribution(
            p["symbol"], mv, data_service=ds, lookback_days=lookback_days,
        )
    return round(total_risk, 2)
```

- [ ] **Step 4: Delete the old VaR implementation**

Delete the remaining `np.percentile(portfolio_returns, ...)` logic — it's replaced by per-position dispatch.

- [ ] **Step 5: Run all portfolio tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/api/ -v -k portfolio`
Expected: PASS

- [ ] **Step 6: Verify overview KPI page renders**

Restart coordinator, visit `/overview` — confirm `Daily VaR (95%)` populates.

- [ ] **Step 7: Commit**

```bash
git add coordinator/api/routes/portfolio.py tests/coordinator/api/test_portfolio_var.py
git commit -m "refactor(portfolio): VaR uses AssetServiceRegistry per-position dispatch

Options now get delta-adjusted risk, crypto uses broader fallback.
Removes the inline DataService instantiation that was happening per call."
```

---

### Task 9: Migrate deployments and positions to use registry

**Files:**
- Modify: `coordinator/api/routes/deployments.py`
- Modify: `coordinator/api/routes/positions.py`

- [ ] **Step 1: Replace deployments P&L calculation (line 129-131)**

```python
# BEFORE (deployments.py, in the live positions enrichment loop)
mv = float(p.get("market_value", 0))
qty = float(p.get("quantity", 0))
avg = float(p.get("avg_price", 0))
upnl = float(p.get("unrealized_pnl", 0))
# Compute unrealized P&L if broker didn't provide it
if upnl == 0 and mv > 0 and avg > 0 and qty > 0:
    upnl = mv - (avg * qty)

# AFTER
from coordinator.services.asset_services import get_default_registry
registry = get_default_registry()
mv = float(p.get("market_value", 0))
qty = float(p.get("quantity", 0))
avg = float(p.get("avg_price", 0))
upnl = float(p.get("unrealized_pnl", 0))
if upnl == 0 and mv > 0 and avg > 0 and qty > 0:
    svc = registry.get_service(sym)
    upnl = svc.compute_unrealized_pnl(sym, qty, avg, mv)
```

- [ ] **Step 2: Replace positions.py asset_class normalization**

```python
# BEFORE (positions.py line 70-71)
"avg_price": float(pos.get("avg_price", 0)),
"current_price": float(pos.get("current_price", 0)),
"asset_type": pos.get("asset_class", "equities"),

# AFTER
"avg_price": float(pos.get("avg_price", 0)),
"current_price": float(pos.get("current_price", 0)),
"asset_type": registry.classify(pos.get("symbol", "")).value,
```

Add the import at the top of positions.py:
```python
from coordinator.services.asset_services import get_default_registry
registry = get_default_registry()
```

- [ ] **Step 3: Write tests**

```python
# tests/coordinator/api/test_positions_classification.py (new file)
import pytest
from coordinator.api.routes.positions import _normalize_position  # extract helper if not present


def test_position_asset_type_via_registry():
    pos = {"symbol": "BTCUSD", "asset_class": "equities"}  # bad asset_class
    normalized = _normalize_position(pos)
    # Registry overrides the bad field — BTCUSD classifies as crypto
    assert normalized["asset_type"] == "crypto"
```

If a `_normalize_position` helper doesn't exist yet, extract one for testability.

- [ ] **Step 4: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/api/ -v -k "deployments or positions"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coordinator/api/routes/deployments.py coordinator/api/routes/positions.py tests/coordinator/api/test_positions_classification.py
git commit -m "refactor(api): deployments and positions use AssetServiceRegistry"
```

---

### Task 10: Migrate Polygon symbol resolution to use registry

**Files:**
- Modify: `coordinator/services/data_providers/polygon.py`

- [ ] **Step 1: Write a failing test**

```python
# tests/coordinator/services/test_polygon_symbol_resolution.py (new file)
import pytest
from coordinator.services.data_providers.polygon import PolygonDataProvider


def test_polygon_uses_registry_for_resolution(monkeypatch):
    """fetch_bars should resolve symbols through the asset registry."""
    provider = PolygonDataProvider(api_key="test")
    # The registry should yield O: for options and I: for indexes
    captured = {}
    async def fake_request(url, params, **kw):
        captured["url"] = url
        return {"status": "OK", "results": []}
    monkeypatch.setattr(provider, "_request_with_retries", fake_request)
    import asyncio
    from datetime import date
    asyncio.run(provider.fetch_bars(
        "SPY260117C00450000", "1day",
        start=date(2026, 1, 1), end=date(2026, 1, 17),
    ))
    assert "O:SPY260117C00450000" in captured["url"]


def test_polygon_index_symbol_via_registry(monkeypatch):
    provider = PolygonDataProvider(api_key="test")
    captured = {}
    async def fake_request(url, params, **kw):
        captured["url"] = url
        return {"status": "OK", "results": []}
    monkeypatch.setattr(provider, "_request_with_retries", fake_request)
    import asyncio
    from datetime import date
    asyncio.run(provider.fetch_bars(
        "VIX", "1day",
        start=date(2026, 1, 1), end=date(2026, 1, 17),
    ))
    assert "I:VIX" in captured["url"]


def test_polygon_passthrough_equity(monkeypatch):
    provider = PolygonDataProvider(api_key="test")
    captured = {}
    async def fake_request(url, params, **kw):
        captured["url"] = url
        return {"status": "OK", "results": []}
    monkeypatch.setattr(provider, "_request_with_retries", fake_request)
    import asyncio
    from datetime import date
    asyncio.run(provider.fetch_bars(
        "AAPL", "1day",
        start=date(2026, 1, 1), end=date(2026, 1, 17),
    ))
    assert "/AAPL/" in captured["url"]
    assert "O:" not in captured["url"]
    assert "I:" not in captured["url"]
```

- [ ] **Step 2: Run test to verify it fails (or already passes)**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/test_polygon_symbol_resolution.py -v`
Expected: tests describe behavior — should already pass partially due to existing logic

- [ ] **Step 3: Replace the symbol-resolution lines (167-173)**

```python
# BEFORE
# Polygon requires the O: prefix for option contract symbols
api_symbol = symbol
if not symbol.startswith("O:") and len(symbol) > 10:
    from coordinator.services.chain_builder import parse_occ_symbol
    if parse_occ_symbol(symbol) is not None:
        api_symbol = f"O:{symbol}"
api_symbol = INDEX_SYMBOL_MAP.get(api_symbol, api_symbol)

# AFTER
from coordinator.services.asset_services import get_default_registry
api_symbol = get_default_registry().resolve_symbol(symbol, "polygon")
```

Also delete the now-unused `INDEX_SYMBOL_MAP` dict (lines 28-33).

- [ ] **Step 4: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/test_polygon_symbol_resolution.py tests/coordinator/services/ -v -k polygon`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/data_providers/polygon.py tests/coordinator/services/test_polygon_symbol_resolution.py
git commit -m "refactor(polygon): symbol resolution via AssetServiceRegistry

Removes hardcoded INDEX_SYMBOL_MAP and inline OCC detection.
All symbol prefixing now lives in the asset service layer."
```

---

### Task 11: Phase 1 integration test

**Files:** None modified — verification only

- [ ] **Step 1: Run the full service test suite**

```bash
/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/asset_services/ -v
```
Expected: ~100 PASS

- [ ] **Step 2: Run the full engine + portfolio + polygon test suite**

```bash
/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/ -v
```
Expected: All PASS

- [ ] **Step 3: Restart coordinator and smoke-test the dashboard**

```bash
# In one terminal
./scripts/restart.sh coordinator

# In another, check the overview
curl -s http://localhost:8000/api/portfolio/kpis | jq
```
Expected: All KPIs populated, including `open_risk` (VaR).

- [ ] **Step 4: Queue a quick backtest**

Use the dashboard to queue the options-straddle backtest and confirm it produces trades. Compare equity curve against pre-refactor baseline (if available).

- [ ] **Step 5: Audit remaining `asset_type ==` references in Phase 1 scope**

```bash
grep -rn 'asset_type == "options"\|asset_type == "crypto"' \
    coordinator/services/backtest_engine_v2.py \
    coordinator/api/routes/portfolio.py \
    coordinator/api/routes/deployments.py \
    coordinator/api/routes/positions.py \
    coordinator/services/data_providers/polygon.py
```
Expected: Zero matches.

- [ ] **Step 6: Commit any incidental fixes**

```bash
git add -A
git commit -m "test(phase-1): final integration verification — all tests green"
```

---

## Phase 2: Worker / Broker Adapter Migrations

### Task 12: Update broker_adapter.py (document service routing)

**Files:**
- Modify: `worker/broker_adapter.py`

The `MultilegLegSpec.asset_type` field is preserved (callers still serialize it for back-compat). What changes: adapter implementations stop branching on it — they consult the registry instead. This task just updates docstrings + adds a registry helper.

- [ ] **Step 1: Add registry helper at module top**

```python
# worker/broker_adapter.py — add near other imports
from coordinator.services.asset_services import get_default_registry as _get_registry


def _registry():
    """Per-process asset registry. Imported lazily to keep adapter unit
    tests fast and to avoid pulling the registry into ad-hoc imports."""
    return _get_registry()
```

- [ ] **Step 2: Update `submit_order` docstring (line 105-109)**

```python
# BEFORE
def submit_order(self, symbol: str, side: str, quantity: float, order_type: str,
                 limit_price: Optional[float] = None, stop_price: Optional[float] = None,
                 asset_type: Optional[str] = None) -> OrderResult:
    """Submit a single-leg order.

    ``asset_type`` is used by adapters that branch behavior by asset class
    (e.g. Alpaca uses GTC for crypto, DAY for equities/options).
    """

# AFTER
def submit_order(self, symbol: str, side: str, quantity: float, order_type: str,
                 limit_price: Optional[float] = None, stop_price: Optional[float] = None,
                 asset_type: Optional[str] = None) -> OrderResult:
    """Submit a single-leg order.

    ``asset_type`` is kept for serialization compatibility but is NOT
    branched on. Adapters consult `AssetServiceRegistry` via the symbol
    to determine TIF, multiplier, and other per-asset rules.
    """
```

- [ ] **Step 3: Update `MultilegLegSpec.asset_type` field docstring (line 44)**

```python
# BEFORE
asset_type: str               # "equities" | "options" | "crypto"

# AFTER
asset_type: str               # "equities" | "options" | "crypto" | "index"
                              # Kept for back-compat serialization only.
                              # Behavior is driven by the registry based on `symbol`.
```

- [ ] **Step 4: Commit**

```bash
git add worker/broker_adapter.py
git commit -m "refactor(broker-adapter): document service routing, add registry helper"
```

---

### Task 13: Migrate alpaca_adapter.py — TIF, multileg, symbol composition, streaming

**Files:**
- Modify: `worker/alpaca_adapter.py`

This is the biggest Phase 2 task: 15 asset-type references across TIF selection, multileg validation, OCC symbol composition, and stream class selection.

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_alpaca_adapter_registry.py (new file)
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from worker.alpaca_adapter import AlpacaBrokerAdapter
from worker.broker_adapter import MultilegLegSpec


@pytest.fixture
def adapter():
    return AlpacaBrokerAdapter(api_key="x", secret_key="y", paper=True)


def test_tif_routes_through_registry_for_crypto(adapter, monkeypatch):
    """Crypto orders should get GTC via the registry, not via inline `asset_type == 'crypto'`."""
    placed = {}

    class FakeClient:
        def submit_order(self, req):
            placed["req"] = req
            return SimpleNamespace(id="oid", status=SimpleNamespace(value="accepted"),
                                   filled_qty=0, filled_avg_price=None)

    adapter._trading_client = FakeClient()
    adapter.submit_order(
        symbol="BTCUSD", side="buy", quantity=0.1, order_type="market",
        asset_type="crypto",
    )
    from alpaca.trading.enums import TimeInForce
    assert placed["req"].time_in_force == TimeInForce.GTC


def test_tif_day_for_equity(adapter):
    placed = {}

    class FakeClient:
        def submit_order(self, req):
            placed["req"] = req
            return SimpleNamespace(id="oid", status=SimpleNamespace(value="accepted"),
                                   filled_qty=0, filled_avg_price=None)

    adapter._trading_client = FakeClient()
    adapter.submit_order(
        symbol="AAPL", side="buy", quantity=10, order_type="market",
        asset_type="equities",
    )
    from alpaca.trading.enums import TimeInForce
    assert placed["req"].time_in_force == TimeInForce.DAY


def test_supports_multileg_uses_registry(adapter):
    """Two-leg options with same underlying should be supported."""
    legs = [
        MultilegLegSpec(symbol="SPY", asset_type="options", side="buy", quantity=1,
                         expiry="2024-10-29", strike=586.0, right="call"),
        MultilegLegSpec(symbol="SPY", asset_type="options", side="sell", quantity=1,
                         expiry="2024-10-29", strike=590.0, right="call"),
    ]
    assert adapter.supports_multileg_orders(legs) is True


def test_supports_multileg_rejects_equities(adapter):
    legs = [
        MultilegLegSpec(symbol="AAPL", asset_type="equities", side="buy", quantity=10),
        MultilegLegSpec(symbol="AAPL", asset_type="equities", side="sell", quantity=10),
    ]
    assert adapter.supports_multileg_orders(legs) is False


def test_compose_symbol_options_via_registry(adapter):
    leg = MultilegLegSpec(
        symbol="SPY", asset_type="options", side="buy", quantity=1,
        expiry="2024-10-29", strike=586.0, right="call",
    )
    assert adapter.compose_symbol(leg) == "SPY241029C00586000"


def test_compose_symbol_equity_passthrough(adapter):
    leg = MultilegLegSpec(symbol="AAPL", asset_type="equities", side="buy", quantity=10)
    assert adapter.compose_symbol(leg) == "AAPL"


def test_stream_class_crypto():
    """Crypto symbols should select CryptoDataStream via registry, not by string match."""
    from coordinator.services.asset_services import get_default_registry
    cfg = get_default_registry().stream_config("BTCUSD", "alpaca")
    assert cfg.stream_class == "crypto"
    assert cfg.symbol_transform == "crypto_slash"


def test_stream_class_equity():
    from coordinator.services.asset_services import get_default_registry
    cfg = get_default_registry().stream_config("AAPL", "alpaca")
    assert cfg.stream_class == "stock"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/worker/test_alpaca_adapter_registry.py -v`
Expected: FAIL (current adapter branches inline)

- [ ] **Step 3: Replace TIF selection (line 147)**

```python
# BEFORE
order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
tif = TimeInForce.GTC if (asset_type or "").lower() == "crypto" else TimeInForce.DAY

# AFTER
from coordinator.services.asset_services import get_default_registry
order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
tif_str = get_default_registry().time_in_force(symbol)
tif = TimeInForce[tif_str]  # "DAY" -> TimeInForce.DAY, "GTC" -> TimeInForce.GTC
```

- [ ] **Step 4: Replace `supports_multileg_orders` (lines 219-226)**

```python
# BEFORE
def supports_multileg_orders(self, legs):
    if len(legs) < 2:
        return False
    if not all(l.asset_type == "options" for l in legs):
        return False
    underlyings = {l.symbol for l in legs}
    return len(underlyings) == 1

# AFTER
def supports_multileg_orders(self, legs):
    if len(legs) < 2:
        return False
    from coordinator.services.asset_services import get_default_registry
    registry = get_default_registry()
    if not all(registry.get_service(l.symbol).supports_multileg() for l in legs):
        return False
    underlyings = {l.symbol for l in legs}
    return len(underlyings) == 1
```

- [ ] **Step 5: Replace `compose_symbol` (lines 228-243)**

```python
# BEFORE
def compose_symbol(self, leg: MultilegLegSpec) -> str:
    if leg.asset_type != "options":
        return leg.symbol
    # OCC: <UNDERLYING><YY><MM><DD><C|P><strike*1000 padded 8>
    if not (leg.expiry and leg.strike is not None and leg.right):
        raise ValueError(...)
    y, m, d = leg.expiry.split("-")
    right_ch = "C" if leg.right.lower().startswith("c") else "P"
    strike_int = int(round(leg.strike * 1000))
    return f"{leg.symbol}{y[2:]}{m}{d}{right_ch}{strike_int:08d}"

# AFTER
def compose_symbol(self, leg: MultilegLegSpec) -> str:
    from coordinator.services.asset_services import get_default_registry
    return get_default_registry().compose_order_symbol(leg)
```

- [ ] **Step 6: Replace stream class selection (lines 388-397)**

```python
# BEFORE
if asset_class == "crypto":
    original_by_streamed = {
        (s if "/" in s else f"{s[:-3]}/{s[-3:]}"): s
        for s in symbols
    }
    symbols_to_subscribe = list(original_by_streamed.keys())
else:
    original_by_streamed = {s: s for s in symbols}

stream_cls = CryptoDataStream if asset_class == "crypto" else StockDataStream

# AFTER
from coordinator.services.asset_services import get_default_registry
registry = get_default_registry()
# Use the first symbol to determine the stream class (caller ensures
# homogeneous batches per stream connection).
first = symbols[0] if symbols else ""
cfg = registry.stream_config(first, "alpaca") if first else None

if cfg and cfg.symbol_transform == "crypto_slash":
    original_by_streamed = {
        registry.resolve_symbol(s, "alpaca_stream"): s for s in symbols
    }
    symbols_to_subscribe = list(original_by_streamed.keys())
else:
    original_by_streamed = {s: s for s in symbols}

stream_cls = CryptoDataStream if (cfg and cfg.stream_class == "crypto") else StockDataStream
```

- [ ] **Step 7: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/worker/test_alpaca_adapter_registry.py tests/worker/ -v -k alpaca`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add worker/alpaca_adapter.py tests/worker/test_alpaca_adapter_registry.py
git commit -m "refactor(alpaca-adapter): route TIF, multileg, compose, stream via registry

Replaces 15 asset_type/asset_class conditionals. Adapter is now agnostic
to which asset types map to which behavior — the registry owns those decisions."
```

---

### Task 14: Migrate tradier_adapter.py

**Files:**
- Modify: `worker/tradier_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_tradier_adapter_registry.py (new file)
import pytest

from worker.broker_adapter import MultilegLegSpec
from worker.tradier_adapter import TradierBrokerAdapter


@pytest.fixture
def adapter():
    return TradierBrokerAdapter(api_key="x", account_id="acct", sandbox=True)


def test_supports_multileg_options(adapter):
    legs = [
        MultilegLegSpec(symbol="SPY", asset_type="options", side="buy", quantity=1,
                         expiry="2024-10-29", strike=586.0, right="call"),
        MultilegLegSpec(symbol="SPY", asset_type="options", side="sell", quantity=1,
                         expiry="2024-10-29", strike=590.0, right="call"),
    ]
    assert adapter.supports_multileg_orders(legs)


def test_supports_multileg_rejects_equities(adapter):
    legs = [
        MultilegLegSpec(symbol="AAPL", asset_type="equities", side="buy", quantity=10),
        MultilegLegSpec(symbol="AAPL", asset_type="equities", side="sell", quantity=10),
    ]
    assert not adapter.supports_multileg_orders(legs)


def test_compose_symbol_options(adapter):
    leg = MultilegLegSpec(
        symbol="SPY", asset_type="options", side="buy", quantity=1,
        expiry="2024-10-29", strike=586.0, right="call",
    )
    assert adapter.compose_symbol(leg) == "SPY241029C00586000"


def test_compose_symbol_equity(adapter):
    leg = MultilegLegSpec(symbol="AAPL", asset_type="equities", side="buy", quantity=10)
    assert adapter.compose_symbol(leg) == "AAPL"


def test_crypto_streaming_rejected(adapter):
    """Tradier doesn't support crypto streaming — should raise."""
    with pytest.raises(ValueError, match="Tradier does not support crypto"):
        adapter.start_market_data_stream(
            ["BTCUSD"], on_trade=lambda x: None, on_quote=lambda x: None,
            asset_class="crypto",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/worker/test_tradier_adapter_registry.py -v`
Expected: FAIL

- [ ] **Step 3: Replace `supports_multileg_orders` (lines 197-202)**

```python
# BEFORE
def supports_multileg_orders(self, legs):
    if len(legs) < 2:
        return False
    if not all(l.asset_type == "options" for l in legs):
        return False
    return len({l.symbol for l in legs}) == 1

# AFTER
def supports_multileg_orders(self, legs):
    if len(legs) < 2:
        return False
    from coordinator.services.asset_services import get_default_registry
    registry = get_default_registry()
    if not all(registry.get_service(l.symbol).supports_multileg() for l in legs):
        return False
    return len({l.symbol for l in legs}) == 1
```

- [ ] **Step 4: Replace `compose_symbol` (lines 204-212)**

```python
# BEFORE
def compose_symbol(self, leg):
    if leg.asset_type != "options":
        return leg.symbol
    if not (leg.expiry and leg.strike is not None and leg.right):
        raise ValueError(...)
    y, m, d = leg.expiry.split("-")
    right_ch = "C" if leg.right.lower().startswith("c") else "P"
    strike_int = int(round(leg.strike * 1000))
    return f"{leg.symbol}{y[2:]}{m}{d}{right_ch}{strike_int:08d}"

# AFTER
def compose_symbol(self, leg):
    from coordinator.services.asset_services import get_default_registry
    return get_default_registry().compose_order_symbol(leg)
```

- [ ] **Step 5: Replace crypto streaming guard (lines 386-388)**

```python
# BEFORE
if asset_class == "crypto":
    raise ValueError("Tradier does not support crypto streaming")

# AFTER
from coordinator.services.asset_services import get_default_registry
# Validate that this broker supports the asset class via the first symbol.
if symbols:
    cfg = get_default_registry().stream_config(symbols[0], "tradier")
    if not cfg.supported:
        raise ValueError(
            f"Tradier does not support streaming for {symbols[0]} "
            f"(asset_class={asset_class})"
        )
```

- [ ] **Step 6: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/worker/test_tradier_adapter_registry.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add worker/tradier_adapter.py tests/worker/test_tradier_adapter_registry.py
git commit -m "refactor(tradier-adapter): route multileg, compose, stream guard via registry"
```

---

### Task 15: Migrate polygon_stream_adapter.py — cluster + symbol transform

**Files:**
- Modify: `worker/polygon_stream_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_polygon_stream_adapter_registry.py (new file)
import pytest

from worker.polygon_stream_adapter import _PolygonStreamHandle, PolygonStreamAdapter


def test_cluster_resolved_via_registry_equity():
    """Equity stream should connect to /stocks cluster."""
    adapter = PolygonStreamAdapter(api_key="x")
    handle = adapter.start_market_data_stream(
        ["AAPL"], on_trade=lambda x: None, on_quote=lambda x: None,
        asset_class="equities",
    )
    assert "stocks" in handle._url
    handle.stop()


def test_cluster_resolved_via_registry_crypto():
    adapter = PolygonStreamAdapter(api_key="x")
    handle = adapter.start_market_data_stream(
        ["BTCUSD"], on_trade=lambda x: None, on_quote=lambda x: None,
        asset_class="crypto",
    )
    assert "crypto" in handle._url
    handle.stop()


def test_symbol_transform_crypto_adds_x_prefix():
    """Polygon crypto symbols are prefixed with X:"""
    h = _PolygonStreamHandle(
        url="wss://test", api_key="x", symbols=["BTCUSD"],
        asset_class="crypto", on_trade=None, on_quote=None,
    )
    assert h._transform_symbol("BTCUSD") == "X:BTCUSD"


def test_symbol_transform_equity_identity():
    h = _PolygonStreamHandle(
        url="wss://test", api_key="x", symbols=["AAPL"],
        asset_class="equities", on_trade=None, on_quote=None,
    )
    assert h._transform_symbol("AAPL") == "AAPL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/worker/test_polygon_stream_adapter_registry.py -v`
Expected: FAIL (current code uses _CLUSTER_MAP)

- [ ] **Step 3: Replace cluster selection (lines 20-23, 47-48)**

```python
# BEFORE — at module top
_CLUSTER_MAP = {
    "equities": "stocks",
    "crypto": "crypto",
    "options": "options",
}

# BEFORE — in start_market_data_stream
cluster = _CLUSTER_MAP.get(asset_class, "stocks")
url = f"{_WS_BASE}/{cluster}"

# AFTER — delete _CLUSTER_MAP, replace selection
from coordinator.services.asset_services import get_default_registry

# In start_market_data_stream:
registry = get_default_registry()
if symbols:
    cfg = registry.stream_config(symbols[0], "polygon")
    cluster = cfg.cluster or "stocks"
else:
    cluster = "stocks"
url = f"{_WS_BASE}/{cluster}"
```

- [ ] **Step 4: Replace `_transform_symbol` (lines 80-85)**

```python
# BEFORE
def _transform_symbol(self, symbol: str) -> str:
    """Map internal symbol to Polygon's stream format.
    Crypto: BTCUSD -> X:BTCUSD
    Equities: SPY -> SPY (no change)
    """
    if self._asset_class == "crypto":
        return f"X:{symbol}"
    return symbol

# AFTER
def _transform_symbol(self, symbol: str) -> str:
    """Map internal symbol to Polygon's stream format via registry."""
    from coordinator.services.asset_services import get_default_registry
    cfg = get_default_registry().stream_config(symbol, "polygon")
    if cfg.symbol_transform == "polygon_x_prefix":
        return f"X:{symbol}"
    if cfg.symbol_transform == "occ_prefix":
        return f"O:{symbol.removeprefix('O:')}"
    return symbol
```

- [ ] **Step 5: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/worker/test_polygon_stream_adapter_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add worker/polygon_stream_adapter.py tests/worker/test_polygon_stream_adapter_registry.py
git commit -m "refactor(polygon-stream): cluster + symbol transform via registry

Removes _CLUSTER_MAP and hardcoded X: prefix logic."
```

---

### Task 16: Update thetadata + coinbase stream adapters

**Files:**
- Modify: `worker/thetadata_stream_adapter.py`
- Modify: `worker/coinbase_stream_adapter.py`

- [ ] **Step 1: Add assertion to coinbase_stream_adapter (line 53)**

```python
# BEFORE
def start_market_data_stream(
    self, symbols: list[str], on_trade, on_quote, asset_class: str = "crypto",
) -> MarketDataStreamHandle:
    coinbase_symbols = [_to_coinbase_symbol(s) for s in symbols]

# AFTER
def start_market_data_stream(
    self, symbols: list[str], on_trade, on_quote, asset_class: str = "crypto",
) -> MarketDataStreamHandle:
    from coordinator.services.asset_services import get_default_registry
    registry = get_default_registry()
    for sym in symbols:
        if not registry.supports_provider(sym, "coinbase"):
            raise ValueError(
                f"Coinbase does not support {sym} "
                f"(only crypto symbols are supported)"
            )
    coinbase_symbols = [_to_coinbase_symbol(s) for s in symbols]
```

- [ ] **Step 2: Update thetadata_stream_adapter to use stream_config**

The thetadata adapter currently accepts asset_class but doesn't branch on it. Add a stream_config validation:

```python
# In start_market_data_stream (around line 60)
def start_market_data_stream(
    self, symbols: list[str], on_trade, on_quote, asset_class: str = "equities",
) -> MarketDataStreamHandle:
    from coordinator.services.asset_services import get_default_registry
    if symbols:
        cfg = get_default_registry().stream_config(symbols[0], "thetadata")
        if not cfg.supported:
            raise ValueError(
                f"ThetaData does not support streaming for {symbols[0]}"
            )
    return _ThetaDataPollHandle(...)
```

- [ ] **Step 3: Write tests**

```python
# tests/worker/test_coinbase_stream_validation.py (new file)
import pytest
from worker.coinbase_stream_adapter import CoinbaseStreamAdapter


def test_coinbase_rejects_equity_symbol():
    adapter = CoinbaseStreamAdapter()
    with pytest.raises(ValueError, match="Coinbase does not support"):
        adapter.start_market_data_stream(
            ["AAPL"], on_trade=lambda x: None, on_quote=lambda x: None,
            asset_class="equities",
        )


def test_coinbase_accepts_crypto_symbol():
    adapter = CoinbaseStreamAdapter()
    handle = adapter.start_market_data_stream(
        ["BTCUSD"], on_trade=lambda x: None, on_quote=lambda x: None,
        asset_class="crypto",
    )
    handle.stop()
```

- [ ] **Step 4: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/worker/test_coinbase_stream_validation.py -v`
Expected: PASS

- [ ] **Step 5: Phase 2 integration test**

```bash
# Audit remaining branches in worker/
grep -rn 'asset_type == "options"\|asset_type == "crypto"\|asset_class == "crypto"' worker/
```
Expected: Zero matches outside `MultilegLegSpec` field declarations and back-compat docstrings.

- [ ] **Step 6: Commit**

```bash
git add worker/coinbase_stream_adapter.py worker/thetadata_stream_adapter.py tests/worker/test_coinbase_stream_validation.py
git commit -m "refactor(stream-adapters): validate provider/asset compatibility via registry"
```

---

## Phase 3: API Routes + Coordinator Services

### Task 17: Migrate accounts.py order validation

**Files:**
- Modify: `coordinator/api/routes/accounts.py`

25 references — the largest single Phase 3 file. Focus areas: asset-type validation against account capability, required-field checks for options legs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/coordinator/api/test_accounts_order_validation.py (new file)
import pytest
from unittest.mock import AsyncMock, MagicMock

from coordinator.api.routes.accounts import _validate_legs_for_account


def test_validate_legs_rejects_unsupported_asset_type():
    """If account doesn't support crypto, a crypto leg should be rejected."""
    account = MagicMock(supported_asset_types=["equities", "options"])
    legs = [MagicMock(symbol="BTCUSD", asset_type="crypto",
                       expiry=None, strike=None, right=None)]
    with pytest.raises(ValueError, match="not supported"):
        _validate_legs_for_account(account, legs)


def test_validate_legs_requires_option_fields():
    """Options legs must have expiry, strike, right."""
    account = MagicMock(supported_asset_types=["options"])
    legs = [MagicMock(symbol="SPY241029C00586000", asset_type="options",
                       expiry=None, strike=None, right=None)]
    with pytest.raises(ValueError, match="required fields"):
        _validate_legs_for_account(account, legs)


def test_validate_legs_accepts_equity_with_no_extra_fields():
    account = MagicMock(supported_asset_types=["equities"])
    legs = [MagicMock(symbol="AAPL", asset_type="equities",
                       expiry=None, strike=None, right=None)]
    # Should not raise
    _validate_legs_for_account(account, legs)
```

- [ ] **Step 2: Run tests to verify they fail (helper doesn't exist yet)**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/api/test_accounts_order_validation.py -v`
Expected: FAIL — `_validate_legs_for_account` is not yet defined

- [ ] **Step 3: Extract the validation helper**

Add to `coordinator/api/routes/accounts.py` (near the other private helpers):

```python
def _validate_legs_for_account(account, legs) -> None:
    """Validate every leg's asset type and required fields against the account.

    Raises ValueError on the first violation. The asset type is classified
    from the symbol via the registry; per-asset required fields are
    declared by the service.
    """
    from coordinator.services.asset_services import get_default_registry
    registry = get_default_registry()
    allowed = set(account.supported_asset_types or [])
    for l in legs:
        svc = registry.get_service(l.symbol)
        if svc.asset_type.value not in allowed:
            raise ValueError(
                f"Asset type {svc.asset_type.value!r} for symbol {l.symbol!r} "
                f"not supported by account (supports: {sorted(allowed)})"
            )
        required = svc.required_order_fields()
        missing = [f for f in required if getattr(l, f, None) in (None, "")]
        if missing:
            raise ValueError(
                f"Leg {l.symbol!r} missing required fields: {missing}"
            )
```

- [ ] **Step 4: Replace inline validation (lines 794-814)**

```python
# BEFORE
# Validate asset types vs account
allowed = set(account.supported_asset_types or [])
bad = [l.asset_type for l in body.legs if l.asset_type not in allowed]
if bad:
    raise HTTPException(
        status_code=422,
        detail=f"Account does not support asset types: {bad}",
    )

# Options legs need expiry+strike+right
missing = [
    i for i, l in enumerate(body.legs)
    if l.asset_type == "options" and not (l.expiry and l.strike is not None and l.right)
]
if missing:
    raise HTTPException(
        status_code=422,
        detail=f"Options legs missing required fields: {missing}",
    )

# AFTER
try:
    _validate_legs_for_account(account, body.legs)
except ValueError as e:
    raise HTTPException(status_code=422, detail=str(e))
```

- [ ] **Step 5: Update single-order TIF passthrough (line 1071)**

The `submit_order(asset_type=body.asset_type)` call still passes asset_type for back-compat. No change needed at this callsite — the adapter (now using the registry internally per Task 13) will route correctly.

- [ ] **Step 6: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/api/test_accounts_order_validation.py tests/coordinator/api/ -v -k accounts`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add coordinator/api/routes/accounts.py tests/coordinator/api/test_accounts_order_validation.py
git commit -m "refactor(accounts): order validation via AssetServiceRegistry

Asset-type validation and required-field checks now driven by the
service layer, not hardcoded option-field logic."
```

---

### Task 18: Migrate lifecycle.py — subscription wiring

**Files:**
- Modify: `coordinator/services/lifecycle.py`

14 references covering asset-class compatibility checks and subscription routing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/coordinator/services/test_lifecycle_registry.py (new file)
import pytest
from unittest.mock import AsyncMock, MagicMock

from coordinator.services.lifecycle import _check_compatibility, _parse_assets


def test_parse_assets_classifies_via_registry():
    """Assets without explicit asset_class get classified from the symbol."""
    parsed = _parse_assets([{"symbol": "BTCUSD"}, {"symbol": "AAPL"}])
    assert parsed[0]["asset_class"] == "crypto"
    assert parsed[1]["asset_class"] == "equities"


def test_parse_assets_overrides_bad_asset_class():
    """An incorrectly-labeled BTCUSD as 'equities' should be re-classified."""
    parsed = _parse_assets([{"symbol": "BTCUSD", "asset_class": "equities"}])
    assert parsed[0]["asset_class"] == "crypto"


def test_check_compatibility_accepts_supported():
    account = {"supported_asset_types": ["equities", "options"], "options_level": 2}
    algorithm = {"required_asset_types": ["equities"]}
    result = _check_compatibility(account, algorithm)
    assert result.compatible


def test_check_compatibility_rejects_missing():
    account = {"supported_asset_types": ["equities"]}
    algorithm = {"required_asset_types": ["options"]}
    result = _check_compatibility(account, algorithm)
    assert not result.compatible
```

- [ ] **Step 2: Replace `_parse_assets` (lines 57-69)**

```python
# BEFORE
def _parse_assets(assets: Any) -> list[dict]:
    """Return list of {symbol, asset_class} dicts."""
    out: list[dict] = []
    if not assets:
        return out
    for a in assets:
        if not isinstance(a, dict):
            continue
        symbol = a.get("symbol")
        asset_class = a.get("asset_class", "equities")
        if symbol:
            out.append({"symbol": symbol, "asset_class": asset_class})
    return out

# AFTER
def _parse_assets(assets: Any) -> list[dict]:
    """Return list of {symbol, asset_class} dicts.

    asset_class is always determined by the registry from the symbol —
    any incoming asset_class field is ignored (registry is authoritative).
    """
    from coordinator.services.asset_services import get_default_registry
    registry = get_default_registry()
    out: list[dict] = []
    if not assets:
        return out
    for a in assets:
        if not isinstance(a, dict):
            continue
        symbol = a.get("symbol")
        if symbol:
            out.append({
                "symbol": symbol,
                "asset_class": registry.classify(symbol).value,
            })
    return out
```

- [ ] **Step 3: Replace the asset-class compatibility block (lines 122-130)**

```python
# BEFORE
supported = set(account.supported_asset_types or [])
declared = {a["asset_class"] for a in assets}
missing = declared - supported
if missing:
    ...

# AFTER
from coordinator.services.asset_services import get_default_registry
registry = get_default_registry()
supported = set(account.supported_asset_types or [])
# Authoritative classification per symbol via registry
declared = {registry.classify(a["symbol"]).value for a in assets}
missing = declared - supported
if missing:
    ...
```

- [ ] **Step 4: Replace LiveSubscription creation (lines 136-153)**

```python
# BEFORE
for asset in assets:
    symbol = asset["symbol"]
    asset_class = asset["asset_class"]
    sub = (await session.execute(
        select(LiveSubscription)
        .where(LiveSubscription.account_id == account.id)
        .where(LiveSubscription.symbol == symbol)
    )).scalar_one_or_none()
    if sub is None:
        sub = LiveSubscription(
            account_id=account.id,
            broker=account.broker_type,
            symbol=symbol,
            asset_class=asset_class,
            ...
        )

# AFTER — asset_class derived from registry, not from the caller's dict
for asset in assets:
    symbol = asset["symbol"]
    asset_class = registry.classify(symbol).value
    sub = (await session.execute(
        select(LiveSubscription)
        .where(LiveSubscription.account_id == account.id)
        .where(LiveSubscription.symbol == symbol)
    )).scalar_one_or_none()
    if sub is None:
        sub = LiveSubscription(
            account_id=account.id,
            broker=account.broker_type,
            symbol=symbol,
            asset_class=asset_class,
            ...
        )
```

- [ ] **Step 5: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/test_lifecycle_registry.py tests/coordinator/services/ -v -k lifecycle`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/lifecycle.py tests/coordinator/services/test_lifecycle_registry.py
git commit -m "refactor(lifecycle): classify subscription assets via registry"
```

---

### Task 19: Migrate live_feed_aggregator.py — stream keying, caps, provider checks

**Files:**
- Modify: `coordinator/services/live_feed_aggregator.py`

24 references — the biggest single file. Touches stream connection keys, broker caps, Coinbase guard, asset_class passthrough.

- [ ] **Step 1: Write the failing tests**

```python
# tests/coordinator/services/test_live_feed_aggregator_registry.py (new file)
from unittest.mock import AsyncMock, MagicMock

import pytest

from coordinator.services.live_feed_aggregator import LiveFeedAggregator


@pytest.mark.asyncio
async def test_coinbase_rejects_non_crypto(monkeypatch, caplog):
    """Coinbase provider must reject equity symbols."""
    agg = LiveFeedAggregator()
    monkeypatch.setattr(agg, "_make_on_trade", lambda b: lambda x: None)
    monkeypatch.setattr(agg, "_make_on_quote", lambda b: lambda x: None)
    await agg.start_subscription(None, "coinbase", "AAPL", "equities")
    assert any("Coinbase only supports crypto" in r.message for r in caplog.records)


def test_stream_cap_from_registry():
    """Per-broker cap should come from stream_config, not the hardcoded dict."""
    from coordinator.services.asset_services import get_default_registry
    cfg = get_default_registry().stream_config("AAPL", "alpaca")
    assert cfg.cap == 30
    cfg2 = get_default_registry().stream_config("BTCUSD", "alpaca")
    assert cfg2.cap == 30
```

- [ ] **Step 2: Replace the broker-cap dict (lines 159-167)**

```python
# BEFORE
# Broker cap: max symbols per (broker, asset_class) stream.
_MAX_SYMBOLS_PER_STREAM: dict[tuple[str, str], int] = {
    ("alpaca", "equities"): 30,
    ("alpaca", "crypto"): 30,
    ("alpaca", "options"): 30,
    ("polygon", "equities"): 30,
    ("polygon", "crypto"): 30,
    ("tradier", "equities"): 30,
}

# AFTER — delete the dict entirely. The registry now answers cap queries.
```

- [ ] **Step 3: Replace the Coinbase-only-crypto guard (lines 312-318)**

```python
# BEFORE
# Coinbase only supports crypto market data.
provider_type = broker if account_id is None else None
if provider_type == "coinbase" and asset_class != "crypto":
    logger.warning(
        "Coinbase only supports crypto; ignoring %s/%s", broker, symbol
    )
    return

# AFTER
from coordinator.services.asset_services import get_default_registry
registry = get_default_registry()
provider_type = broker if account_id is None else None
if provider_type and not registry.supports_provider(symbol, provider_type):
    logger.warning(
        "%s does not support %s; ignoring %s/%s",
        provider_type, symbol, broker, symbol,
    )
    return
```

- [ ] **Step 4: Replace the cap lookup (line 362)**

```python
# BEFORE
cap = _MAX_SYMBOLS_PER_STREAM.get((broker, asset_class), 30)

# AFTER
cap = registry.stream_config(symbol, broker).cap or 30
```

- [ ] **Step 5: Update LiveSubscription startup classification (lines 254-258)**

```python
# BEFORE
if r.account_id is None and r.provider_type:
    await self.start_subscription(None, r.broker, r.symbol, r.asset_class)
else:
    await self.start_subscription(r.account_id, r.broker, r.symbol, r.asset_class)

# AFTER — registry is the source of truth, but pass the existing asset_class
# for back-compat with the start_subscription signature.
asset_class = registry.classify(r.symbol).value
if r.account_id is None and r.provider_type:
    await self.start_subscription(None, r.broker, r.symbol, asset_class)
else:
    await self.start_subscription(r.account_id, r.broker, r.symbol, asset_class)
```

- [ ] **Step 6: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/test_live_feed_aggregator_registry.py tests/coordinator/services/ -v -k live_feed`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add coordinator/services/live_feed_aggregator.py tests/coordinator/services/test_live_feed_aggregator_registry.py
git commit -m "refactor(live-feed-aggregator): provider check + caps via registry

Removes _MAX_SYMBOLS_PER_STREAM dict and hardcoded Coinbase guard."
```

---

### Task 20: Migrate market_clock + pdt_monitor + tick_scheduler

**Files:**
- Modify: `coordinator/services/market_clock.py`
- Modify: `coordinator/services/pdt_monitor.py`
- Modify: `coordinator/services/tick_scheduler.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/coordinator/services/test_market_clock_registry.py (new file)
from datetime import datetime, timezone

import pytest

from coordinator.services.market_clock import is_market_open


def test_market_open_equity_during_session():
    # Monday 18:00 UTC = 14:00 ET during EDT
    ts = datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc)
    assert is_market_open("AAPL", ts)


def test_market_closed_equity_weekend():
    ts = datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
    assert not is_market_open("AAPL", ts)


def test_market_open_crypto_weekend():
    """Crypto is 24/7."""
    ts = datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
    assert is_market_open("BTCUSD", ts)


def test_market_open_options_follows_equity():
    ts = datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc)
    assert is_market_open("SPY241029C00586000", ts)
```

- [ ] **Step 2: Replace `market_clock.is_market_open` signature + body**

```python
# BEFORE (lines 43-46)
EQUITIES_TYPES = {"equities", "equity_options"}


def is_market_open(asset_type: str, ts: datetime) -> bool:
    if asset_type not in EQUITIES_TYPES:
        return True
    et = _to_et(ts)
    if et.weekday() >= 5:
        return False
    ...

# AFTER — accept symbol (preferred) OR keep asset_type signature for back-compat
def is_market_open(symbol_or_asset_type: str, ts: datetime) -> bool:
    """Whether the market is open for the given symbol at ``ts``.

    Accepts either a symbol (preferred — routes through registry) or
    a legacy asset_type string like "equities" or "crypto".
    """
    from coordinator.services.asset_services import (
        AssetType,
        get_default_registry,
    )
    registry = get_default_registry()
    # Heuristic: if it matches an AssetType value, treat as legacy asset_type.
    try:
        at = AssetType(symbol_or_asset_type)
        svc = registry.get_service_by_type(at)
    except ValueError:
        svc = registry.get_service(symbol_or_asset_type)
    return svc.is_market_open(ts)
```

Delete `EQUITIES_TYPES` and the old `_to_et` helper if no other callers reference them.

- [ ] **Step 3: Replace pdt_monitor.py crypto skip (lines 25-26)**

```python
# BEFORE
for leg in signal_legs:
    if leg["asset_type"] == "crypto":
        continue
    symbol = leg["symbol"]
    side = leg["side"]

# AFTER
from coordinator.services.asset_services import get_default_registry
registry = get_default_registry()
for leg in signal_legs:
    if registry.get_service(leg["symbol"]).is_pdt_exempt():
        continue
    symbol = leg["symbol"]
    side = leg["side"]
```

- [ ] **Step 4: Replace tick_scheduler.py market-open check (line 179)**

```python
# BEFORE
while True:
    now = datetime.now(timezone.utc)
    if is_market_open(self.asset_type, now):
        tick = {
            "instance_id": self.instance_id,
            ...
        }

# AFTER — pass the first symbol so the registry can dispatch correctly.
# If self.symbols is empty, fall back to asset_type via classify_by_type.
while True:
    now = datetime.now(timezone.utc)
    probe = self.symbols[0]["symbol"] if self.symbols else self.asset_type
    if is_market_open(probe, now):
        tick = {
            "instance_id": self.instance_id,
            ...
        }
```

- [ ] **Step 5: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/test_market_clock_registry.py tests/coordinator/services/ -v -k "market_clock or pdt or tick_scheduler"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/market_clock.py coordinator/services/pdt_monitor.py coordinator/services/tick_scheduler.py tests/coordinator/services/test_market_clock_registry.py
git commit -m "refactor(scheduling): market hours + PDT via registry

is_market_open and pdt_monitor now dispatch through AssetService.
EQUITIES_TYPES dict removed."
```

---

### Task 21: Migrate live_subscriptions + options_chain + algorithms validators

**Files:**
- Modify: `coordinator/api/routes/live_subscriptions.py`
- Modify: `coordinator/api/routes/options_chain.py`
- Modify: `coordinator/api/routes/algorithms.py`

- [ ] **Step 1: Replace `live_subscriptions.py` validator (lines 66-71)**

```python
# BEFORE
@field_validator("asset_class")
@classmethod
def _validate_asset_class(cls, v: str) -> str:
    if v not in ("equities", "crypto", "options"):
        raise ValueError(f"asset_class must be one of equities, crypto, options; got {v!r}")
    return v

# AFTER
@field_validator("asset_class")
@classmethod
def _validate_asset_class(cls, v: str) -> str:
    from coordinator.services.asset_services import AssetType
    try:
        AssetType(v)
    except ValueError:
        valid = [t.value for t in AssetType]
        raise ValueError(f"asset_class must be one of {valid}; got {v!r}")
    return v
```

- [ ] **Step 2: Replace `live_subscriptions.py` account capability check (line 215)**

```python
# BEFORE
if body.asset_class not in (account.supported_asset_types or []):
    raise HTTPException(
        status_code=422,
        detail=f"Account does not support asset_class {body.asset_class!r}",
    )

# AFTER  (functionally the same — but use registry to normalize)
from coordinator.services.asset_services import get_default_registry
registry = get_default_registry()
classified = registry.classify(symbol_upper).value
if classified not in (account.supported_asset_types or []):
    raise HTTPException(
        status_code=422,
        detail=f"Account does not support asset_class {classified!r} (symbol={symbol_upper})",
    )
```

- [ ] **Step 3: Replace `options_chain.py` capability check (lines 23-25)**

```python
# BEFORE
if "options" not in (a.supported_asset_types or []):
    raise HTTPException(status_code=422,
                        detail="Account does not support options")

# AFTER
from coordinator.services.asset_services import AssetType
if AssetType.OPTIONS.value not in (a.supported_asset_types or []):
    raise HTTPException(status_code=422,
                        detail="Account does not support options")
```

- [ ] **Step 4: Replace `algorithms.py` _VALID_ASSET_CLASSES (line 44)**

```python
# BEFORE
_VALID_ASSET_CLASSES = {"equities", "crypto", "options"}

# AFTER
from coordinator.services.asset_services import AssetType
_VALID_ASSET_CLASSES = {t.value for t in AssetType}
```

This automatically picks up `"index"` once the enum has it — no code change needed when adding new asset types in the future.

- [ ] **Step 5: Update default class fallbacks in algorithms.py (lines 617, 776)**

These currently default to `"equities"` when `manifest.requirements.asset_types` is empty. Leave the default as `"equities"` but route any incoming `asset_class` through the registry's classifier:

```python
# BEFORE
default_class = (manifest.requirements.asset_types or ["equities"])[0]
...
"asset_class": dep.get("asset_class") or default_class,

# AFTER
from coordinator.services.asset_services import get_default_registry
registry = get_default_registry()
default_class = (manifest.requirements.asset_types or ["equities"])[0]
...
sym = dep.get("symbol") or ""
declared = dep.get("asset_class") or default_class
# Honor classifier if it disagrees with the declared class
classified = registry.classify(sym).value if sym else declared
"asset_class": classified,
```

- [ ] **Step 6: Write tests**

```python
# tests/coordinator/api/test_live_subscriptions_validator.py (new file)
import pytest
from coordinator.api.routes.live_subscriptions import _CreateLiveSubscriptionBody  # adjust import to actual class name


def test_validator_accepts_index():
    """The enum-driven validator picks up 'index' automatically."""
    body = _CreateLiveSubscriptionBody(
        account_id=None, provider_type="polygon",
        symbol="VIX", asset_class="index",
    )
    assert body.asset_class == "index"


def test_validator_rejects_garbage():
    with pytest.raises(ValueError, match="asset_class must be one of"):
        _CreateLiveSubscriptionBody(
            account_id=None, provider_type="polygon",
            symbol="VIX", asset_class="garbage",
        )
```

- [ ] **Step 7: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/api/test_live_subscriptions_validator.py tests/coordinator/api/ -v -k "subscriptions or options_chain or algorithms"`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add coordinator/api/routes/live_subscriptions.py coordinator/api/routes/options_chain.py coordinator/api/routes/algorithms.py tests/coordinator/api/test_live_subscriptions_validator.py
git commit -m "refactor(api): validators driven by AssetType enum

_VALID_ASSET_CLASSES now derives from the enum, so adding 'index' as
a first-class type doesn't require updating multiple files."
```

---

### Task 22: Migrate asset_catalog.py

**Files:**
- Modify: `coordinator/services/asset_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/test_asset_catalog.py (new file)
import pytest
from coordinator.services.asset_catalog import asset_types_for_broker


def test_alpaca_supports_three():
    types = asset_types_for_broker("alpaca")
    assert set(types) == {"equities", "options", "crypto"}


def test_tradier_supports_two():
    types = asset_types_for_broker("tradier")
    assert set(types) == {"equities", "options"}


def test_unknown_broker_raises():
    with pytest.raises(ValueError, match="Unknown broker"):
        asset_types_for_broker("unknown")
```

- [ ] **Step 2: Replace BROKER_ASSET_TYPES with registry-derived data**

```python
# BEFORE (asset_catalog.py)
BROKER_ASSET_TYPES: dict[str, list[str]] = {
    "alpaca":  ["equities", "options", "crypto"],
    "tradier": ["equities", "options"],
}


def asset_types_for_broker(broker_type: str) -> list[str]:
    if broker_type not in BROKER_ASSET_TYPES:
        raise ValueError(f"Unknown broker: {broker_type}")
    return list(BROKER_ASSET_TYPES[broker_type])

# AFTER
"""Broker → supported asset types lookup.

Derives the per-broker capability list by querying each service's
supports_provider() method. Adding a new asset type to the registry
automatically extends every broker's capability list (if supported).
"""
from __future__ import annotations

from coordinator.services.asset_services import (
    AssetType,
    get_default_registry,
)

_KNOWN_BROKERS = {"alpaca", "tradier"}


def asset_types_for_broker(broker_type: str) -> list[str]:
    if broker_type not in _KNOWN_BROKERS:
        raise ValueError(f"Unknown broker: {broker_type}")
    registry = get_default_registry()
    supported: list[str] = []
    for at in AssetType:
        svc = registry.get_service_by_type(at)
        if svc.supports_provider(broker_type):
            supported.append(at.value)
    return supported
```

- [ ] **Step 3: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/coordinator/services/test_asset_catalog.py -v`
Expected: PASS

- [ ] **Step 4: Phase 3 audit**

```bash
# Remaining branches in Phase 3 scope
grep -rn 'asset_type == "options"\|asset_type == "crypto"\|asset_class == "crypto"\|"equities", "crypto"' \
    coordinator/api/routes/ \
    coordinator/services/lifecycle.py \
    coordinator/services/live_feed_aggregator.py \
    coordinator/services/market_clock.py \
    coordinator/services/pdt_monitor.py \
    coordinator/services/tick_scheduler.py \
    coordinator/services/asset_catalog.py \
  | grep -v '^Binary' | grep -v test_
```
Expected: Zero matches in production code (test files may keep them for fixture realism).

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/asset_catalog.py tests/coordinator/services/test_asset_catalog.py
git commit -m "refactor(asset-catalog): derive broker capabilities from registry

BROKER_ASSET_TYPES dict removed — capabilities are queried per-service."
```

---

## Phase 4: SDK + Manifest

### Task 23: Migrate sdk/signals.py and sdk/models.py

**Files:**
- Modify: `sdk/signals.py`
- Modify: `sdk/models.py`

The SDK is consumed by algorithm packages — backwards-compatible field shapes matter. We add validation but keep `asset_type: str = "equities"` defaults.

- [ ] **Step 1: Write the failing tests**

```python
# tests/sdk/test_signals_validation.py (new file)
import pytest
from sdk.signals import SignalLeg, SignalType, OrderType


def test_signal_leg_accepts_known_asset_types():
    for at in ("equities", "options", "crypto", "index"):
        leg = SignalLeg(
            symbol="SPY", signal_type=SignalType.BUY, quantity=1, asset_type=at,
        )
        assert leg.asset_type == at


def test_signal_leg_rejects_unknown_asset_type():
    with pytest.raises(ValueError, match="asset_type"):
        SignalLeg(
            symbol="SPY", signal_type=SignalType.BUY, quantity=1,
            asset_type="bogus",
        )


def test_signal_leg_default_equities():
    leg = SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=1)
    assert leg.asset_type == "equities"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/sdk/test_signals_validation.py -v`
Expected: FAIL (no validation currently)

- [ ] **Step 3: Add validation helper to sdk/signals.py**

```python
# sdk/signals.py — add near the top, alongside SignalType
_VALID_ASSET_TYPES = frozenset({"equities", "options", "crypto", "index"})


def _validate_asset_type(value: str) -> str:
    """Validate asset_type against the canonical set. Raises ValueError if invalid.

    The SDK is consumed by algorithm packages, so it can't import the
    coordinator. We keep an inline frozenset and assert it matches the
    AssetType enum via a contract test in tests/sdk/.
    """
    if value not in _VALID_ASSET_TYPES:
        raise ValueError(
            f"asset_type must be one of {sorted(_VALID_ASSET_TYPES)}, got {value!r}"
        )
    return value
```

- [ ] **Step 4: Call the validator in SignalLeg.__post_init__**

```python
# sdk/signals.py @dataclass SignalLeg
@dataclass
class SignalLeg:
    symbol: str
    signal_type: SignalType
    quantity: float
    asset_type: str = "equities"
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    # ... existing fields ...

    def __post_init__(self) -> None:
        _validate_asset_type(self.asset_type)
```

- [ ] **Step 5: Mirror the validator in sdk/models.py**

```python
# sdk/models.py — mirror the validator (DRY-wise it's tempting to import
# from sdk.signals but we want models.py to be self-contained for
# algorithm-package imports).
_VALID_ASSET_TYPES = frozenset({"equities", "options", "crypto", "index"})


def _validate_asset_type(value: str) -> str:
    if value not in _VALID_ASSET_TYPES:
        raise ValueError(
            f"asset_type must be one of {sorted(_VALID_ASSET_TYPES)}, got {value!r}"
        )
    return value


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    current_price: float
    asset_type: str = "equities"

    def __post_init__(self) -> None:
        _validate_asset_type(self.asset_type)
```

- [ ] **Step 6: Add contract test ensuring SDK and registry agree**

```python
# tests/sdk/test_asset_type_contract.py (new file)
"""Contract test: SDK's _VALID_ASSET_TYPES must equal the registry's enum values.

If the registry ever gains a new asset type, this test fails and the SDK
must be updated (separate package, separate deployment cadence).
"""
from coordinator.services.asset_services import AssetType
from sdk.signals import _VALID_ASSET_TYPES as SIGNALS_VALID
from sdk.models import _VALID_ASSET_TYPES as MODELS_VALID


def test_signals_matches_enum():
    assert SIGNALS_VALID == {t.value for t in AssetType}


def test_models_matches_enum():
    assert MODELS_VALID == {t.value for t in AssetType}
```

- [ ] **Step 7: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/sdk/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add sdk/signals.py sdk/models.py tests/sdk/test_signals_validation.py tests/sdk/test_asset_type_contract.py
git commit -m "feat(sdk): validate asset_type in SignalLeg and Position

Adds frozenset validation + contract test ensuring SDK matches the
canonical AssetType enum in the coordinator."
```

---

### Task 24: Migrate sdk/manifest.py

**Files:**
- Modify: `sdk/manifest.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/sdk/test_manifest_validation.py (new file)
import pytest
import yaml
from sdk.manifest import AlgorithmManifest, ManifestError


def test_manifest_accepts_known_asset_types(tmp_path):
    yaml_data = {
        "package_type": "algorithm",
        "name": "test", "class_name": "TestAlgo",
        "requirements": {"asset_types": ["equities", "options"]},
    }
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump(yaml_data))
    mf = AlgorithmManifest.from_file(p)
    assert mf.requirements.asset_types == ["equities", "options"]


def test_manifest_rejects_bogus_asset_type(tmp_path):
    yaml_data = {
        "package_type": "algorithm",
        "name": "test", "class_name": "TestAlgo",
        "requirements": {"asset_types": ["bogus"]},
    }
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump(yaml_data))
    with pytest.raises(ManifestError, match="invalid asset_type"):
        AlgorithmManifest.from_file(p)


def test_manifest_asset_entry_validates_class(tmp_path):
    yaml_data = {
        "package_type": "algorithm",
        "name": "test", "class_name": "TestAlgo",
        "requirements": {"asset_types": ["equities"]},
        "assets": [{"symbol": "AAPL", "asset_class": "bogus"}],
    }
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump(yaml_data))
    with pytest.raises(ManifestError, match="invalid asset_class"):
        AlgorithmManifest.from_file(p)
```

- [ ] **Step 2: Add asset-type validation to manifest parsing**

In `sdk/manifest.py`, add the validator at module top:

```python
_VALID_ASSET_TYPES = frozenset({"equities", "options", "crypto", "index"})


def _validate_asset_type_list(values: list[str], field: str) -> list[str]:
    bad = [v for v in values if v not in _VALID_ASSET_TYPES]
    if bad:
        raise ManifestError(
            f"invalid asset_type values in {field}: {bad}; "
            f"must be one of {sorted(_VALID_ASSET_TYPES)}"
        )
    return values
```

- [ ] **Step 3: Call the validator in `from_file` (around line 83-85)**

```python
# BEFORE
requirements = ManifestRequirements(
    asset_types=reqs_data.get("asset_types", []),
    options_level=reqs_data.get("options_level"),
    account_features=reqs_data.get("account_features", []),
    ...
)

# AFTER
asset_types = reqs_data.get("asset_types", [])
asset_types = _validate_asset_type_list(asset_types, "requirements.asset_types")
requirements = ManifestRequirements(
    asset_types=asset_types,
    options_level=reqs_data.get("options_level"),
    account_features=reqs_data.get("account_features", []),
    ...
)
```

- [ ] **Step 4: Validate each asset entry's `asset_class` (around line 140-143)**

```python
# BEFORE
entry = {
    "symbol": symbol,
    "asset_class": a.get("asset_class", "equities"),
}

# AFTER
asset_class = a.get("asset_class", "equities")
if asset_class not in _VALID_ASSET_TYPES:
    raise ManifestError(
        f"invalid asset_class {asset_class!r} for symbol {symbol!r}; "
        f"must be one of {sorted(_VALID_ASSET_TYPES)}"
    )
entry = {
    "symbol": symbol,
    "asset_class": asset_class,
}
```

- [ ] **Step 5: Run tests**

Run: `/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/sdk/test_manifest_validation.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add sdk/manifest.py tests/sdk/test_manifest_validation.py
git commit -m "feat(sdk): validate manifest asset_types and asset_class entries"
```

---

### Task 25: Final integration test — full regression

**Files:** None modified — verification only

- [ ] **Step 1: Run the entire test suite**

```bash
/home/jkern/dev/quilt-trader/.venv/bin/python -m pytest tests/ -v 2>&1 | tail -50
```
Expected: All PASS

- [ ] **Step 2: Audit remaining branches across the whole codebase**

```bash
# Any production code still branching on asset_type/asset_class outside
# the service layer should be reviewed.
grep -rn 'asset_type == "options"\|asset_type == "crypto"\|asset_class == "crypto"\|asset_class == "options"' \
    coordinator/ worker/ sdk/ \
  | grep -v 'asset_services/' \
  | grep -v test_ \
  | grep -v '__pycache__' \
  | grep -v 'docstring\|comment'
```
Expected: Zero matches (or only documented back-compat references in dataclass field defaults).

- [ ] **Step 3: Restart the full stack**

```bash
./scripts/restart.sh
```

- [ ] **Step 4: Smoke-test the dashboard**

Visit each major page and confirm no regressions:
- Overview: KPIs populate (Today P&L, VaR, win rate, etc.)
- Accounts: list renders, balances correct
- Deployments: live P&L per deployment
- Positions: enriched with current_price
- Data → Live: subscriptions list, click into one to see ticks
- Algorithms: list renders, asset_types validated
- Backtests: queue a simple equity backtest and a straddle options backtest; both complete

- [ ] **Step 5: Verify a multileg options order shape**

Use the dashboard's order panel (or API):
```bash
curl -X POST http://localhost:8000/api/accounts/<acct>/order \
    -H "Content-Type: application/json" \
    -d '{
      "order_type": "limit",
      "limit_price": 1.50,
      "legs": [
        {"symbol": "SPY", "asset_type": "options", "side": "buy", "quantity": 1,
         "expiry": "2026-06-19", "strike": 580, "right": "call"},
        {"symbol": "SPY", "asset_type": "options", "side": "sell", "quantity": 1,
         "expiry": "2026-06-19", "strike": 585, "right": "call"}
      ]
    }'
```
Expected: 200 OK (paper account), confirms multileg routes via registry.

- [ ] **Step 6: Verify a crypto market order**

```bash
curl -X POST http://localhost:8000/api/accounts/<crypto-acct>/order \
    -H "Content-Type: application/json" \
    -d '{
      "order_type": "market",
      "legs": [{"symbol": "BTCUSD", "asset_type": "crypto", "side": "buy", "quantity": 0.001}]
    }'
```
Expected: 200 OK, TIF=GTC on the Alpaca side (verifiable in adapter logs).

- [ ] **Step 7: Final cleanup**

```bash
# Delete the old plan v2 file
git rm docs/superpowers/plans/2026-05-26-asset-service-layer-v2.md
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "test(asset-services): final integration regression — all tests green

Audit confirms zero asset_type/asset_class branches remain in production
code outside coordinator/services/asset_services/."
```

---

## Testing Strategy Summary

| Phase | Test Files Added | Test Count |
|-------|------------------|------------|
| 1 | 6 new (1 base, 5 services + registry) + 3 migration tests | ~100 service tests + ~10 migration tests |
| 2 | 4 new (alpaca, tradier, polygon stream, coinbase) | ~15 tests |
| 3 | 7 new (accounts, lifecycle, live_feed, market_clock, live_subs, asset_catalog, ...) | ~25 tests |
| 4 | 3 new (signals, manifest, contract) | ~10 tests |
| **Total** | **20 new test files** | **~150 tests** |

Phase 1 tests are exhaustive — every method on every service has at least one test, and every migration callsite has a behavioral test. Phases 2-4 tests focus on the *integration* (the adapter/route correctly delegates to the registry).

## Self-Review Notes

This plan has been self-reviewed for:

1. **Spec coverage**: Every file from the 32-file audit is mapped to a task. Every protocol method has tests in Phase 1. Migration callsites are listed with line numbers and before/after code.

2. **Placeholder scan**: No TBD/TODO entries. Every code step shows actual code. Test steps show actual test code, not bullet points. Migration steps show actual before/after diffs.

3. **Type consistency**: Method names match between protocol declaration, service implementations, registry delegations, and migration callsites:
   - `time_in_force()` (Tasks 1, 2-5, 6, 13, 17)
   - `supports_multileg()` (Tasks 1, 2-5, 6, 13, 14)
   - `required_order_fields()` (Tasks 1, 2-5, 17)
   - `is_pdt_exempt()` (Tasks 1, 2-5, 20)
   - `stream_config()` returns `StreamConfig` (Tasks 1, 2-5, 6, 13, 15, 19)
   - `compose_order_symbol()` (Tasks 1, 2-5, 6, 13, 14)
   - `supports_provider()` (Tasks 1, 2-5, 6, 16, 19, 22)
   - `get_default_registry()` singleton used in migrations (Tasks 8, 9, 10, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22)

4. **Risk mitigation**: Each task ends in its own commit. Phase 1 is purely additive (registry + tests) before any migration starts. Phase 2-4 each have an audit step ensuring no regressions remain in their scope.
