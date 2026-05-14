# Plan 1: SDK + Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the QuiltTrader SDK — the Python package that defines the contract algorithms and scrapers implement, plus the project scaffolding.

**Architecture:** The SDK is a standalone Python package (`quilt_trader_sdk`) that algorithm authors install. It defines base classes (`QuiltAlgorithm`, `QuiltScraper`), data classes (`Signal`, `SignalLeg`, `TickContext`, `Position`, `TradeFill`, `OptionChain`), and a `quilt.yaml` schema validator. The SDK has zero dependencies on the coordinator or worker — it's purely the contract.

**Tech Stack:** Python 3.11+, pydantic (for data validation), PyYAML, pandas (TickContext returns DataFrames), pytest

---

## File Map

| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Root project config — defines sdk, coordinator, and worker as packages |
| `sdk/__init__.py` | Public API re-exports |
| `sdk/signals.py` | `SignalType`, `OrderType`, `SignalLeg`, `Signal` |
| `sdk/models.py` | `Position`, `TradeFill`, `OptionChain`, `OptionContract` |
| `sdk/algorithm.py` | `QuiltAlgorithm` base class |
| `sdk/scraper.py` | `QuiltScraper` base class |
| `sdk/context.py` | `TickContext` base class (abstract — concrete implementations live in worker) |
| `sdk/manifest.py` | `quilt.yaml` parsing and validation |
| `tests/sdk/__init__.py` | Test package |
| `tests/sdk/test_signals.py` | Signal and SignalLeg tests |
| `tests/sdk/test_models.py` | Position, TradeFill, OptionChain tests |
| `tests/sdk/test_algorithm.py` | QuiltAlgorithm contract tests |
| `tests/sdk/test_scraper.py` | QuiltScraper contract tests |
| `tests/sdk/test_manifest.py` | quilt.yaml validation tests |
| `tests/sdk/fixtures/` | Test fixture YAML files |

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `sdk/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/sdk/__init__.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "quilt-trader"
version = "0.1.0"
description = "Algorithmic trading framework for Raspberry Pi clusters"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "pandas>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package files**

`sdk/__init__.py`:
```python
"""QuiltTrader SDK — contract for trading algorithms and scrapers."""
```

`tests/__init__.py`: empty file

`tests/sdk/__init__.py`: empty file

- [ ] **Step 3: Install and verify**

Run: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
Expected: Installs successfully

Run: `pytest --co`
Expected: "no tests ran" (no test files yet), exits 0 or 5 (no tests collected)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml sdk/__init__.py tests/__init__.py tests/sdk/__init__.py
git commit -m "feat: project scaffolding with pyproject.toml and package structure"
```

---

### Task 2: Signal and SignalLeg Data Classes

**Files:**
- Create: `sdk/signals.py`
- Create: `tests/sdk/test_signals.py`

- [ ] **Step 1: Write failing tests**

`tests/sdk/test_signals.py`:
```python
from sdk.signals import SignalType, OrderType, SignalLeg, Signal


class TestSignalType:
    def test_enum_values(self):
        assert SignalType.BUY.value == "buy"
        assert SignalType.SELL.value == "sell"
        assert SignalType.SELL_SHORT.value == "sell_short"
        assert SignalType.BUY_TO_COVER.value == "buy_to_cover"


class TestOrderType:
    def test_enum_values(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.STOP.value == "stop"
        assert OrderType.STOP_LIMIT.value == "stop_limit"


class TestSignalLeg:
    def test_create_equity_leg(self):
        leg = SignalLeg(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            quantity=100,
        )
        assert leg.symbol == "AAPL"
        assert leg.signal_type == SignalType.BUY
        assert leg.quantity == 100
        assert leg.asset_type == "equities"
        assert leg.order_type == OrderType.MARKET
        assert leg.limit_price is None
        assert leg.stop_price is None

    def test_create_options_leg_with_limit(self):
        leg = SignalLeg(
            symbol="AAPL250620C00200000",
            signal_type=SignalType.BUY,
            quantity=1,
            asset_type="options",
            order_type=OrderType.LIMIT,
            limit_price=3.50,
        )
        assert leg.asset_type == "options"
        assert leg.order_type == OrderType.LIMIT
        assert leg.limit_price == 3.50

    def test_create_crypto_leg_fractional(self):
        leg = SignalLeg(
            symbol="BTC/USD",
            signal_type=SignalType.BUY,
            quantity=0.00142,
            asset_type="crypto",
        )
        assert leg.quantity == 0.00142
        assert leg.asset_type == "crypto"

    def test_serialization_roundtrip(self):
        leg = SignalLeg(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            quantity=100,
            order_type=OrderType.LIMIT,
            limit_price=150.00,
        )
        d = leg.to_dict()
        restored = SignalLeg.from_dict(d)
        assert restored.symbol == leg.symbol
        assert restored.signal_type == leg.signal_type
        assert restored.quantity == leg.quantity
        assert restored.order_type == leg.order_type
        assert restored.limit_price == leg.limit_price


class TestSignal:
    def test_simple_constructor(self):
        signal = Signal.simple(
            "AAPL", SignalType.BUY, 100, reasoning="Momentum breakout"
        )
        assert len(signal.legs) == 1
        assert signal.legs[0].symbol == "AAPL"
        assert signal.legs[0].signal_type == SignalType.BUY
        assert signal.legs[0].quantity == 100
        assert signal.strategy_type == "single"
        assert signal.reasoning == "Momentum breakout"
        assert signal.net_debit_limit is None
        assert signal.net_credit_limit is None

    def test_simple_crypto(self):
        signal = Signal.simple(
            "BTC/USD", SignalType.BUY, 0.05, asset_type="crypto"
        )
        assert signal.legs[0].asset_type == "crypto"
        assert signal.legs[0].quantity == 0.05

    def test_multi_leg_spread(self):
        signal = Signal(
            legs=[
                SignalLeg("AAPL250620C00200000", SignalType.BUY, 1, asset_type="options"),
                SignalLeg("AAPL250620C00210000", SignalType.SELL, 1, asset_type="options"),
            ],
            strategy_type="bull_call_spread",
            net_debit_limit=3.50,
            reasoning="Bullish into earnings",
        )
        assert len(signal.legs) == 2
        assert signal.strategy_type == "bull_call_spread"
        assert signal.net_debit_limit == 3.50

    def test_pairs_trade(self):
        signal = Signal(
            legs=[
                SignalLeg("BTC/USD", SignalType.BUY, 0.1, asset_type="crypto"),
                SignalLeg("ETH/USD", SignalType.SELL, 2.0, asset_type="crypto"),
            ],
            strategy_type="pairs_trade",
            reasoning="BTC/ETH ratio reverting",
        )
        assert len(signal.legs) == 2
        assert signal.strategy_type == "pairs_trade"

    def test_is_multi_leg(self):
        single = Signal.simple("AAPL", SignalType.BUY, 100)
        assert single.is_multi_leg is False

        multi = Signal(
            legs=[
                SignalLeg("AAPL", SignalType.BUY, 100),
                SignalLeg("MSFT", SignalType.SELL, 50),
            ],
            strategy_type="pairs_trade",
        )
        assert multi.is_multi_leg is True

    def test_serialization_roundtrip(self):
        signal = Signal(
            legs=[
                SignalLeg("AAPL250620C00200000", SignalType.BUY, 1, asset_type="options"),
                SignalLeg("AAPL250620C00210000", SignalType.SELL, 1, asset_type="options"),
            ],
            strategy_type="bull_call_spread",
            net_debit_limit=3.50,
            reasoning="test",
            metadata={"confidence": 0.85},
        )
        d = signal.to_dict()
        restored = Signal.from_dict(d)
        assert len(restored.legs) == 2
        assert restored.legs[0].symbol == "AAPL250620C00200000"
        assert restored.strategy_type == "bull_call_spread"
        assert restored.net_debit_limit == 3.50
        assert restored.reasoning == "test"
        assert restored.metadata == {"confidence": 0.85}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sdk/test_signals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdk.signals'`

- [ ] **Step 3: Implement signals.py**

`sdk/signals.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    SELL_SHORT = "sell_short"
    BUY_TO_COVER = "buy_to_cover"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@dataclass
class SignalLeg:
    symbol: str
    signal_type: SignalType
    quantity: float
    asset_type: str = "equities"
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type.value,
            "quantity": self.quantity,
            "asset_type": self.asset_type,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
        }

    @staticmethod
    def from_dict(d: dict) -> SignalLeg:
        return SignalLeg(
            symbol=d["symbol"],
            signal_type=SignalType(d["signal_type"]),
            quantity=d["quantity"],
            asset_type=d.get("asset_type", "equities"),
            order_type=OrderType(d.get("order_type", "market")),
            limit_price=d.get("limit_price"),
            stop_price=d.get("stop_price"),
        )


@dataclass
class Signal:
    legs: list[SignalLeg]
    strategy_type: str = "single"
    net_debit_limit: Optional[float] = None
    net_credit_limit: Optional[float] = None
    reasoning: Optional[str] = None
    metadata: Optional[dict] = field(default=None)

    @property
    def is_multi_leg(self) -> bool:
        return len(self.legs) > 1

    @staticmethod
    def simple(
        symbol: str,
        signal_type: SignalType,
        quantity: float,
        asset_type: str = "equities",
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        reasoning: Optional[str] = None,
    ) -> Signal:
        return Signal(
            legs=[
                SignalLeg(
                    symbol=symbol,
                    signal_type=signal_type,
                    quantity=quantity,
                    asset_type=asset_type,
                    order_type=order_type,
                    limit_price=limit_price,
                )
            ],
            strategy_type="single",
            reasoning=reasoning,
        )

    def to_dict(self) -> dict:
        return {
            "legs": [leg.to_dict() for leg in self.legs],
            "strategy_type": self.strategy_type,
            "net_debit_limit": self.net_debit_limit,
            "net_credit_limit": self.net_credit_limit,
            "reasoning": self.reasoning,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(d: dict) -> Signal:
        return Signal(
            legs=[SignalLeg.from_dict(leg) for leg in d["legs"]],
            strategy_type=d.get("strategy_type", "single"),
            net_debit_limit=d.get("net_debit_limit"),
            net_credit_limit=d.get("net_credit_limit"),
            reasoning=d.get("reasoning"),
            metadata=d.get("metadata"),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_signals.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/signals.py tests/sdk/test_signals.py
git commit -m "feat: add Signal and SignalLeg data classes with serialization"
```

---

### Task 3: Position, TradeFill, and OptionChain Models

**Files:**
- Create: `sdk/models.py`
- Create: `tests/sdk/test_models.py`

- [ ] **Step 1: Write failing tests**

`tests/sdk/test_models.py`:
```python
from datetime import datetime, date
from sdk.models import Position, TradeFill, OptionContract, OptionChain


class TestTradeFill:
    def test_create_fill(self):
        fill = TradeFill(
            symbol="AAPL",
            side="buy",
            quantity=100,
            filled_price=150.25,
            fees=1.00,
            slippage=0.05,
            timestamp=datetime(2026, 5, 12, 10, 30, 0),
        )
        assert fill.symbol == "AAPL"
        assert fill.filled_price == 150.25
        assert fill.fees == 1.00
        assert fill.slippage == 0.05

    def test_fill_with_fee_breakdown(self):
        fill = TradeFill(
            symbol="BTC/USD",
            side="buy",
            quantity=0.05,
            filled_price=68000.00,
            fees=3.40,
            slippage=10.00,
            timestamp=datetime(2026, 5, 12, 10, 30, 0),
            fee_breakdown={"commission": 0.00, "exchange_fee": 3.40, "network_fee": 0.00},
        )
        assert fill.fee_breakdown["exchange_fee"] == 3.40

    def test_serialization_roundtrip(self):
        fill = TradeFill(
            symbol="AAPL",
            side="buy",
            quantity=100,
            filled_price=150.25,
            fees=1.00,
            slippage=0.05,
            timestamp=datetime(2026, 5, 12, 10, 30, 0),
        )
        d = fill.to_dict()
        restored = TradeFill.from_dict(d)
        assert restored.symbol == fill.symbol
        assert restored.filled_price == fill.filled_price


class TestPosition:
    def test_create_single_position(self):
        pos = Position(
            symbol="AAPL",
            quantity=100,
            avg_cost=150.00,
            current_price=155.00,
            asset_type="equities",
        )
        assert pos.symbol == "AAPL"
        assert pos.market_value == 15500.00
        assert pos.unrealized_pnl == 500.00
        assert pos.unrealized_pnl_pct == (155.00 - 150.00) / 150.00 * 100

    def test_short_position_pnl(self):
        pos = Position(
            symbol="TSLA",
            quantity=-50,
            avg_cost=200.00,
            current_price=190.00,
            asset_type="equities",
        )
        assert pos.market_value == -9500.00
        assert pos.unrealized_pnl == 500.00  # Shorted at 200, now 190 = profit

    def test_serialization_roundtrip(self):
        pos = Position(
            symbol="AAPL",
            quantity=100,
            avg_cost=150.00,
            current_price=155.00,
            asset_type="equities",
        )
        d = pos.to_dict()
        restored = Position.from_dict(d)
        assert restored.symbol == pos.symbol
        assert restored.quantity == pos.quantity
        assert restored.avg_cost == pos.avg_cost


class TestOptionContract:
    def test_create_call(self):
        contract = OptionContract(
            symbol="AAPL250620C00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="call",
            bid=3.50,
            ask=3.70,
            last=3.60,
            volume=1500,
            open_interest=12000,
            implied_volatility=0.32,
        )
        assert contract.option_type == "call"
        assert contract.strike == 200.00
        assert contract.mid == 3.60

    def test_create_put(self):
        contract = OptionContract(
            symbol="AAPL250620P00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="put",
            bid=2.10,
            ask=2.30,
            last=2.20,
            volume=800,
            open_interest=5000,
            implied_volatility=0.30,
        )
        assert contract.option_type == "put"
        assert contract.mid == 2.20


class TestOptionChain:
    def test_create_chain(self):
        call = OptionContract(
            symbol="AAPL250620C00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="call",
            bid=3.50, ask=3.70, last=3.60,
            volume=1500, open_interest=12000,
            implied_volatility=0.32,
        )
        put = OptionContract(
            symbol="AAPL250620P00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="put",
            bid=2.10, ask=2.30, last=2.20,
            volume=800, open_interest=5000,
            implied_volatility=0.30,
        )
        chain = OptionChain(
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            calls=[call],
            puts=[put],
        )
        assert chain.underlying == "AAPL"
        assert len(chain.calls) == 1
        assert len(chain.puts) == 1

    def test_get_strike(self):
        call = OptionContract(
            symbol="AAPL250620C00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="call",
            bid=3.50, ask=3.70, last=3.60,
            volume=1500, open_interest=12000,
            implied_volatility=0.32,
        )
        chain = OptionChain(
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            calls=[call],
            puts=[],
        )
        found = chain.get_call(200.00)
        assert found is not None
        assert found.strike == 200.00
        assert chain.get_call(999.00) is None

    def test_strikes(self):
        calls = [
            OptionContract(
                symbol=f"AAPL250620C{int(s*1000):08d}",
                underlying="AAPL", expiration=date(2025, 6, 20),
                strike=s, option_type="call",
                bid=1.0, ask=1.5, last=1.25,
                volume=100, open_interest=500,
                implied_volatility=0.30,
            )
            for s in [195.0, 200.0, 205.0]
        ]
        chain = OptionChain(
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            calls=calls,
            puts=[],
        )
        assert chain.strikes == [195.0, 200.0, 205.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sdk/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdk.models'`

- [ ] **Step 3: Implement models.py**

`sdk/models.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


@dataclass
class TradeFill:
    symbol: str
    side: str
    quantity: float
    filled_price: float
    fees: float
    slippage: float
    timestamp: datetime
    fee_breakdown: Optional[dict] = None
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "filled_price": self.filled_price,
            "fees": self.fees,
            "slippage": self.slippage,
            "timestamp": self.timestamp.isoformat(),
            "fee_breakdown": self.fee_breakdown,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(d: dict) -> TradeFill:
        return TradeFill(
            symbol=d["symbol"],
            side=d["side"],
            quantity=d["quantity"],
            filled_price=d["filled_price"],
            fees=d["fees"],
            slippage=d["slippage"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            fee_breakdown=d.get("fee_breakdown"),
            metadata=d.get("metadata"),
        )


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    current_price: float
    asset_type: str = "equities"

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.avg_cost == 0:
            return 0.0
        return (self.current_price - self.avg_cost) / abs(self.avg_cost) * 100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "current_price": self.current_price,
            "asset_type": self.asset_type,
        }

    @staticmethod
    def from_dict(d: dict) -> Position:
        return Position(
            symbol=d["symbol"],
            quantity=d["quantity"],
            avg_cost=d["avg_cost"],
            current_price=d["current_price"],
            asset_type=d.get("asset_type", "equities"),
        )


@dataclass
class OptionContract:
    symbol: str
    underlying: str
    expiration: date
    strike: float
    option_type: str  # "call" or "put"
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_volatility: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class OptionChain:
    underlying: str
    expiration: date
    calls: list[OptionContract] = field(default_factory=list)
    puts: list[OptionContract] = field(default_factory=list)

    @property
    def strikes(self) -> list[float]:
        all_strikes = set()
        for c in self.calls:
            all_strikes.add(c.strike)
        for p in self.puts:
            all_strikes.add(p.strike)
        return sorted(all_strikes)

    def get_call(self, strike: float) -> Optional[OptionContract]:
        for c in self.calls:
            if c.strike == strike:
                return c
        return None

    def get_put(self, strike: float) -> Optional[OptionContract]:
        for p in self.puts:
            if p.strike == strike:
                return p
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_models.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/models.py tests/sdk/test_models.py
git commit -m "feat: add Position, TradeFill, OptionChain, OptionContract models"
```

---

### Task 4: QuiltAlgorithm Base Class

**Files:**
- Create: `sdk/algorithm.py`
- Create: `tests/sdk/test_algorithm.py`

- [ ] **Step 1: Write failing tests**

`tests/sdk/test_algorithm.py`:
```python
import pytest
from sdk.algorithm import QuiltAlgorithm
from sdk.signals import Signal, SignalType


class DummyAlgorithm(QuiltAlgorithm):
    def on_start(self, config, restored_state):
        self.config = config
        self.restored_state = restored_state
        self.started = True

    def on_tick(self, ctx):
        return [Signal.simple("AAPL", SignalType.BUY, 100)]

    def on_stop(self):
        return {"final": True}

    def save_state(self):
        return {"checkpoint": True}


class IncompleteAlgorithm(QuiltAlgorithm):
    pass


class TestQuiltAlgorithm:
    def test_subclass_implements_required_methods(self):
        algo = DummyAlgorithm()
        algo.on_start({"risk": 0.02}, None)
        assert algo.started is True
        assert algo.config == {"risk": 0.02}
        assert algo.restored_state is None

    def test_on_tick_returns_signals(self):
        algo = DummyAlgorithm()
        algo.on_start({}, None)
        signals = algo.on_tick(None)
        assert len(signals) == 1
        assert signals[0].legs[0].symbol == "AAPL"

    def test_on_stop_returns_state(self):
        algo = DummyAlgorithm()
        state = algo.on_stop()
        assert state == {"final": True}

    def test_save_state_returns_state(self):
        algo = DummyAlgorithm()
        state = algo.save_state()
        assert state == {"checkpoint": True}

    def test_incomplete_raises_on_required_methods(self):
        algo = IncompleteAlgorithm()
        with pytest.raises(NotImplementedError):
            algo.on_start({}, None)
        with pytest.raises(NotImplementedError):
            algo.on_tick(None)
        with pytest.raises(NotImplementedError):
            algo.on_stop()
        with pytest.raises(NotImplementedError):
            algo.save_state()

    def test_on_signal_rejected_default_noop(self):
        algo = DummyAlgorithm()
        signal = Signal.simple("AAPL", SignalType.BUY, 100)
        algo.on_signal_rejected(signal, "PDT limit reached")

    def test_on_trade_executed_default_noop(self):
        from sdk.models import TradeFill
        from datetime import datetime
        algo = DummyAlgorithm()
        signal = Signal.simple("AAPL", SignalType.BUY, 100)
        fill = TradeFill(
            symbol="AAPL", side="buy", quantity=100,
            filled_price=150.25, fees=1.00, slippage=0.05,
            timestamp=datetime(2026, 5, 12, 10, 30, 0),
        )
        algo.on_trade_executed(signal, fill)

    def test_notify_stores_event(self):
        algo = DummyAlgorithm()
        algo.notify("unusual_volume", "AAPL volume 3x average", {"symbol": "AAPL"})
        assert len(algo._pending_notifications) == 1
        event = algo._pending_notifications[0]
        assert event["event_name"] == "unusual_volume"
        assert event["message"] == "AAPL volume 3x average"
        assert event["data"] == {"symbol": "AAPL"}

    def test_drain_notifications(self):
        algo = DummyAlgorithm()
        algo.notify("event1", "msg1")
        algo.notify("event2", "msg2")
        events = algo.drain_notifications()
        assert len(events) == 2
        assert len(algo._pending_notifications) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sdk/test_algorithm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdk.algorithm'`

- [ ] **Step 3: Implement algorithm.py**

`sdk/algorithm.py`:
```python
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sdk.context import TickContext
    from sdk.models import TradeFill
    from sdk.signals import Signal


class QuiltAlgorithm:
    """Base class that all trading algorithms must implement."""

    def __init__(self) -> None:
        self._pending_notifications: list[dict] = []

    def on_start(self, config: dict, restored_state: Optional[dict]) -> None:
        raise NotImplementedError

    def on_tick(self, ctx: TickContext) -> list[Signal]:
        raise NotImplementedError

    def on_stop(self) -> dict:
        raise NotImplementedError

    def save_state(self) -> dict:
        raise NotImplementedError

    def on_signal_rejected(self, signal: Signal, reason: str) -> None:
        pass

    def on_trade_executed(self, signal: Signal, fill: TradeFill) -> None:
        pass

    def notify(self, event_name: str, message: str, data: Optional[dict] = None) -> None:
        self._pending_notifications.append({
            "event_name": event_name,
            "message": message,
            "data": data,
        })

    def drain_notifications(self) -> list[dict]:
        events = list(self._pending_notifications)
        self._pending_notifications.clear()
        return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_algorithm.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/algorithm.py tests/sdk/test_algorithm.py
git commit -m "feat: add QuiltAlgorithm base class with notification support"
```

---

### Task 5: QuiltScraper Base Class

**Files:**
- Create: `sdk/scraper.py`
- Create: `tests/sdk/test_scraper.py`

- [ ] **Step 1: Write failing tests**

`tests/sdk/test_scraper.py`:
```python
import pytest
import pandas as pd
from sdk.scraper import QuiltScraper


class DummyScraper(QuiltScraper):
    def on_run(self):
        return pd.DataFrame({"symbol": ["AAPL", "MSFT"], "score": [0.8, 0.6]})


class IncompleteScraper(QuiltScraper):
    pass


class TestQuiltScraper:
    def test_subclass_implements_on_run(self):
        scraper = DummyScraper()
        result = scraper.on_run()
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert list(result.columns) == ["symbol", "score"]

    def test_on_start_default_noop(self):
        scraper = DummyScraper()
        scraper.on_start({})

    def test_on_stop_default_noop(self):
        scraper = DummyScraper()
        scraper.on_stop()

    def test_incomplete_raises_on_run(self):
        scraper = IncompleteScraper()
        with pytest.raises(NotImplementedError):
            scraper.on_run()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sdk/test_scraper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdk.scraper'`

- [ ] **Step 3: Implement scraper.py**

`sdk/scraper.py`:
```python
from __future__ import annotations

import pandas as pd


class QuiltScraper:
    """Base class that all data scrapers must implement."""

    def on_start(self, config: dict) -> None:
        pass

    def on_run(self) -> pd.DataFrame:
        raise NotImplementedError

    def on_stop(self) -> None:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_scraper.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/scraper.py tests/sdk/test_scraper.py
git commit -m "feat: add QuiltScraper base class"
```

---

### Task 6: TickContext Abstract Base Class

**Files:**
- Create: `sdk/context.py`
- Create: `tests/sdk/test_context.py`

- [ ] **Step 1: Write failing tests**

`tests/sdk/test_context.py`:
```python
import pytest
import pandas as pd
from datetime import datetime, date
from sdk.context import TickContext
from sdk.models import Position, OptionChain, OptionContract


class MockTickContext(TickContext):
    """Concrete implementation for testing the abstract interface."""

    def __init__(self):
        self._timestamp = datetime(2026, 5, 12, 10, 30, 0)
        self._mode = "live"
        self._positions = {
            "AAPL": Position("AAPL", 100, 150.00, 155.00),
        }
        self._account_value = 100000.00
        self._cash = 85000.00
        self._buying_power = 170000.00

    @property
    def timestamp(self):
        return self._timestamp

    @property
    def mode(self):
        return self._mode

    @property
    def positions(self):
        return dict(self._positions)

    @property
    def account_value(self):
        return self._account_value

    @property
    def cash(self):
        return self._cash

    @property
    def buying_power(self):
        return self._buying_power

    def market_data(self, symbol, timeframe="1min", bars=100):
        return pd.DataFrame({
            "open": [150.0], "high": [155.0], "low": [149.0],
            "close": [154.0], "volume": [1000000],
            "timestamp": [self._timestamp],
        })

    def data(self, source_name):
        return pd.DataFrame({"symbol": ["AAPL"], "score": [0.8]})

    def option_chain(self, symbol, expiration=None):
        call = OptionContract(
            symbol="AAPL250620C00200000", underlying="AAPL",
            expiration=date(2025, 6, 20), strike=200.00, option_type="call",
            bid=3.50, ask=3.70, last=3.60,
            volume=1500, open_interest=12000, implied_volatility=0.32,
        )
        return OptionChain(underlying=symbol, expiration=date(2025, 6, 20), calls=[call], puts=[])


class TestTickContext:
    def test_timestamp(self):
        ctx = MockTickContext()
        assert ctx.timestamp == datetime(2026, 5, 12, 10, 30, 0)

    def test_mode(self):
        ctx = MockTickContext()
        assert ctx.mode == "live"

    def test_positions(self):
        ctx = MockTickContext()
        positions = ctx.positions
        assert "AAPL" in positions
        assert positions["AAPL"].quantity == 100

    def test_account_value(self):
        ctx = MockTickContext()
        assert ctx.account_value == 100000.00

    def test_cash(self):
        ctx = MockTickContext()
        assert ctx.cash == 85000.00

    def test_buying_power(self):
        ctx = MockTickContext()
        assert ctx.buying_power == 170000.00

    def test_market_data_returns_dataframe(self):
        ctx = MockTickContext()
        df = ctx.market_data("AAPL")
        assert isinstance(df, pd.DataFrame)
        assert "close" in df.columns

    def test_data_returns_dataframe(self):
        ctx = MockTickContext()
        df = ctx.data("alpha-picks")
        assert isinstance(df, pd.DataFrame)

    def test_option_chain(self):
        ctx = MockTickContext()
        chain = ctx.option_chain("AAPL")
        assert chain.underlying == "AAPL"
        assert len(chain.calls) == 1

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            TickContext()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sdk/test_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdk.context'`

- [ ] **Step 3: Implement context.py**

`sdk/context.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, date
from typing import Optional

import pandas as pd

from sdk.models import Position, OptionChain


class TickContext(ABC):
    """Abstract base providing all data an algorithm needs during a tick.

    Concrete implementations live in the worker (for live trading)
    and in the SDK CLI (for backtesting).
    """

    @property
    @abstractmethod
    def timestamp(self) -> datetime:
        ...

    @property
    @abstractmethod
    def mode(self) -> str:
        ...

    @property
    @abstractmethod
    def positions(self) -> dict[str, Position]:
        ...

    @property
    @abstractmethod
    def account_value(self) -> float:
        ...

    @property
    @abstractmethod
    def cash(self) -> float:
        ...

    @property
    @abstractmethod
    def buying_power(self) -> float:
        ...

    @abstractmethod
    def market_data(self, symbol: str, timeframe: str = "1min", bars: int = 100) -> pd.DataFrame:
        ...

    @abstractmethod
    def data(self, source_name: str) -> pd.DataFrame:
        ...

    @abstractmethod
    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_context.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/context.py tests/sdk/test_context.py
git commit -m "feat: add TickContext abstract base class"
```

---

### Task 7: quilt.yaml Manifest Parsing and Validation

**Files:**
- Create: `sdk/manifest.py`
- Create: `tests/sdk/test_manifest.py`
- Create: `tests/sdk/fixtures/valid_algorithm.yaml`
- Create: `tests/sdk/fixtures/valid_scraper.yaml`
- Create: `tests/sdk/fixtures/minimal_algorithm.yaml`
- Create: `tests/sdk/fixtures/invalid_missing_name.yaml`
- Create: `tests/sdk/fixtures/invalid_bad_type.yaml`

- [ ] **Step 1: Create test fixture files**

`tests/sdk/fixtures/valid_algorithm.yaml`:
```yaml
name: momentum-scalper
type: algorithm
version: 1.0.0
description: Intraday momentum scalping strategy
entry_point: algorithm.py
class_name: MomentumScalper

requirements:
  asset_types:
    - equities
    - options
  options_level: 3
  account_features:
    - margin
    - short_selling
  brokers:
    - alpaca
    - tradier
  data_dependencies:
    - name: alpha-picks-scraper
      repo: ElectricJack/alpha-picks-scraper

config:
  parameters:
    - name: risk_per_trade
      type: float
      default: 0.02
      description: Maximum portfolio percentage risked per trade
      min: 0.001
      max: 0.10
    - name: max_positions
      type: int
      default: 5
      description: Maximum concurrent positions

notifications:
  custom_events:
    - name: unusual_volume
      description: Triggered when volume exceeds 3x average
      severity: info
```

`tests/sdk/fixtures/valid_scraper.yaml`:
```yaml
name: alpha-picks-scraper
type: scraper
version: 1.0.0
description: Scrapes alpha stock picks
schedule: "*/30 * * * *"
output:
  format: csv
  filename: alpha-picks.csv
```

`tests/sdk/fixtures/minimal_algorithm.yaml`:
```yaml
name: simple-algo
type: algorithm
version: 0.1.0
entry_point: algo.py
class_name: SimpleAlgo

requirements:
  asset_types:
    - equities
```

`tests/sdk/fixtures/invalid_missing_name.yaml`:
```yaml
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: Algo
requirements:
  asset_types:
    - equities
```

`tests/sdk/fixtures/invalid_bad_type.yaml`:
```yaml
name: something
type: widget
version: 1.0.0
```

- [ ] **Step 2: Write failing tests**

`tests/sdk/test_manifest.py`:
```python
import pytest
from pathlib import Path
from sdk.manifest import QuiltManifest, ManifestError

FIXTURES = Path(__file__).parent / "fixtures"


class TestManifestLoading:
    def test_load_valid_algorithm(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_algorithm.yaml")
        assert manifest.name == "momentum-scalper"
        assert manifest.type == "algorithm"
        assert manifest.version == "1.0.0"
        assert manifest.description == "Intraday momentum scalping strategy"
        assert manifest.entry_point == "algorithm.py"
        assert manifest.class_name == "MomentumScalper"

    def test_load_valid_scraper(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_scraper.yaml")
        assert manifest.name == "alpha-picks-scraper"
        assert manifest.type == "scraper"
        assert manifest.schedule == "*/30 * * * *"
        assert manifest.output_format == "csv"
        assert manifest.output_filename == "alpha-picks.csv"

    def test_load_minimal_algorithm(self):
        manifest = QuiltManifest.from_file(FIXTURES / "minimal_algorithm.yaml")
        assert manifest.name == "simple-algo"
        assert manifest.requirements.asset_types == ["equities"]
        assert manifest.requirements.options_level is None
        assert manifest.requirements.account_features == []
        assert manifest.requirements.brokers is None
        assert manifest.requirements.data_dependencies == []

    def test_load_from_string(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 0.1.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
"""
        manifest = QuiltManifest.from_string(yaml_str)
        assert manifest.name == "test-algo"


class TestManifestRequirements:
    def test_full_requirements(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_algorithm.yaml")
        reqs = manifest.requirements
        assert reqs.asset_types == ["equities", "options"]
        assert reqs.options_level == 3
        assert reqs.account_features == ["margin", "short_selling"]
        assert reqs.brokers == ["alpaca", "tradier"]
        assert len(reqs.data_dependencies) == 1
        assert reqs.data_dependencies[0]["name"] == "alpha-picks-scraper"
        assert reqs.data_dependencies[0]["repo"] == "ElectricJack/alpha-picks-scraper"


class TestManifestConfig:
    def test_parameters(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_algorithm.yaml")
        params = manifest.config_parameters
        assert len(params) == 2
        assert params[0]["name"] == "risk_per_trade"
        assert params[0]["type"] == "float"
        assert params[0]["default"] == 0.02
        assert params[1]["name"] == "max_positions"
        assert params[1]["type"] == "int"

    def test_no_config(self):
        manifest = QuiltManifest.from_file(FIXTURES / "minimal_algorithm.yaml")
        assert manifest.config_parameters == []


class TestManifestNotifications:
    def test_custom_events(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_algorithm.yaml")
        events = manifest.custom_events
        assert len(events) == 1
        assert events[0]["name"] == "unusual_volume"
        assert events[0]["severity"] == "info"

    def test_no_notifications(self):
        manifest = QuiltManifest.from_file(FIXTURES / "minimal_algorithm.yaml")
        assert manifest.custom_events == []


class TestManifestValidation:
    def test_missing_name_raises(self):
        with pytest.raises(ManifestError, match="name"):
            QuiltManifest.from_file(FIXTURES / "invalid_missing_name.yaml")

    def test_bad_type_raises(self):
        with pytest.raises(ManifestError, match="type"):
            QuiltManifest.from_file(FIXTURES / "invalid_bad_type.yaml")

    def test_algorithm_missing_entry_point_raises(self):
        yaml_str = """
name: test
type: algorithm
version: 1.0.0
requirements:
  asset_types: [equities]
"""
        with pytest.raises(ManifestError, match="entry_point"):
            QuiltManifest.from_string(yaml_str)

    def test_algorithm_missing_class_name_raises(self):
        yaml_str = """
name: test
type: algorithm
version: 1.0.0
entry_point: algo.py
requirements:
  asset_types: [equities]
"""
        with pytest.raises(ManifestError, match="class_name"):
            QuiltManifest.from_string(yaml_str)

    def test_algorithm_missing_asset_types_raises(self):
        yaml_str = """
name: test
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: Test
"""
        with pytest.raises(ManifestError, match="asset_types"):
            QuiltManifest.from_string(yaml_str)

    def test_scraper_missing_schedule_raises(self):
        yaml_str = """
name: test
type: scraper
version: 1.0.0
output:
  format: csv
  filename: test.csv
"""
        with pytest.raises(ManifestError, match="schedule"):
            QuiltManifest.from_string(yaml_str)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            QuiltManifest.from_file(Path("/nonexistent/quilt.yaml"))
```

- [ ] **Step 2b: Run tests to verify they fail**

Run: `pytest tests/sdk/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdk.manifest'`

- [ ] **Step 3: Implement manifest.py**

`sdk/manifest.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


class ManifestError(Exception):
    pass


@dataclass
class ManifestRequirements:
    asset_types: list[str] = field(default_factory=list)
    options_level: Optional[int] = None
    account_features: list[str] = field(default_factory=list)
    brokers: Optional[list[str]] = None
    data_dependencies: list[dict] = field(default_factory=list)


@dataclass
class QuiltManifest:
    name: str
    type: str
    version: str
    description: str = ""
    entry_point: str = ""
    class_name: str = ""
    requirements: ManifestRequirements = field(default_factory=ManifestRequirements)
    config_parameters: list[dict] = field(default_factory=list)
    custom_events: list[dict] = field(default_factory=list)
    schedule: str = ""
    output_format: str = ""
    output_filename: str = ""

    @staticmethod
    def from_file(path: Path) -> QuiltManifest:
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        return QuiltManifest._parse(data)

    @staticmethod
    def from_string(yaml_str: str) -> QuiltManifest:
        data = yaml.safe_load(yaml_str)
        return QuiltManifest._parse(data)

    @staticmethod
    def _parse(data: dict) -> QuiltManifest:
        if not data.get("name"):
            raise ManifestError("Manifest must have a 'name' field")

        pkg_type = data.get("type", "")
        if pkg_type not in ("algorithm", "scraper"):
            raise ManifestError(f"Manifest 'type' must be 'algorithm' or 'scraper', got '{pkg_type}'")

        if pkg_type == "algorithm":
            if not data.get("entry_point"):
                raise ManifestError("Algorithm manifest must have an 'entry_point' field")
            if not data.get("class_name"):
                raise ManifestError("Algorithm manifest must have a 'class_name' field")
            reqs_data = data.get("requirements", {})
            if not reqs_data.get("asset_types"):
                raise ManifestError("Algorithm manifest must specify requirements.asset_types")

        if pkg_type == "scraper":
            if not data.get("schedule"):
                raise ManifestError("Scraper manifest must have a 'schedule' field")

        reqs_data = data.get("requirements", {})
        requirements = ManifestRequirements(
            asset_types=reqs_data.get("asset_types", []),
            options_level=reqs_data.get("options_level"),
            account_features=reqs_data.get("account_features", []),
            brokers=reqs_data.get("brokers"),
            data_dependencies=reqs_data.get("data_dependencies", []),
        )

        config_data = data.get("config", {})
        config_parameters = config_data.get("parameters", [])

        notifications_data = data.get("notifications", {})
        custom_events = notifications_data.get("custom_events", [])

        output_data = data.get("output", {})

        return QuiltManifest(
            name=data["name"],
            type=data["type"],
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            entry_point=data.get("entry_point", ""),
            class_name=data.get("class_name", ""),
            requirements=requirements,
            config_parameters=config_parameters,
            custom_events=custom_events,
            schedule=data.get("schedule", ""),
            output_format=output_data.get("format", ""),
            output_filename=output_data.get("filename", ""),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_manifest.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/manifest.py tests/sdk/test_manifest.py tests/sdk/fixtures/
git commit -m "feat: add quilt.yaml manifest parsing and validation"
```

---

### Task 8: SDK Public API and Final Integration

**Files:**
- Modify: `sdk/__init__.py`
- Create: `tests/sdk/test_init.py`

- [ ] **Step 1: Write failing test**

`tests/sdk/test_init.py`:
```python
def test_public_api_imports():
    from sdk import (
        QuiltAlgorithm,
        QuiltScraper,
        TickContext,
        Signal,
        SignalLeg,
        SignalType,
        OrderType,
        Position,
        TradeFill,
        OptionChain,
        OptionContract,
        QuiltManifest,
        ManifestError,
    )
    assert QuiltAlgorithm is not None
    assert QuiltScraper is not None
    assert TickContext is not None
    assert Signal is not None
    assert SignalLeg is not None
    assert SignalType is not None
    assert OrderType is not None
    assert Position is not None
    assert TradeFill is not None
    assert OptionChain is not None
    assert OptionContract is not None
    assert QuiltManifest is not None
    assert ManifestError is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/sdk/test_init.py -v`
Expected: FAIL — `ImportError: cannot import name 'QuiltAlgorithm' from 'sdk'`

- [ ] **Step 3: Update sdk/__init__.py with re-exports**

`sdk/__init__.py`:
```python
"""QuiltTrader SDK — contract for trading algorithms and scrapers."""

from sdk.algorithm import QuiltAlgorithm
from sdk.scraper import QuiltScraper
from sdk.context import TickContext
from sdk.signals import Signal, SignalLeg, SignalType, OrderType
from sdk.models import Position, TradeFill, OptionChain, OptionContract
from sdk.manifest import QuiltManifest, ManifestError

__all__ = [
    "QuiltAlgorithm",
    "QuiltScraper",
    "TickContext",
    "Signal",
    "SignalLeg",
    "SignalType",
    "OrderType",
    "Position",
    "TradeFill",
    "OptionChain",
    "OptionContract",
    "QuiltManifest",
    "ManifestError",
]
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/sdk/ -v`
Expected: All tests PASS (should be ~46 tests total across all test files)

- [ ] **Step 5: Commit**

```bash
git add sdk/__init__.py tests/sdk/test_init.py
git commit -m "feat: add SDK public API re-exports"
```

---

## Summary

After completing this plan, you have:
- A Python package with all SDK data classes (`Signal`, `SignalLeg`, `Position`, `TradeFill`, `OptionChain`)
- `QuiltAlgorithm` and `QuiltScraper` base classes that define the contract
- `TickContext` abstract base class for the data interface algorithms use
- `QuiltManifest` parser and validator for `quilt.yaml` files
- Full test coverage for all of the above
- Clean `pyproject.toml` with dev dependencies

**Next plan:** Plan 2 (Coordinator Core) builds the database, FastAPI app, and basic API routes.
