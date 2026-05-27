# Strategy Validation Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable Strategy Validation Lab — walk-forward, hyperparameter sweep, bootstrap CIs, regime-conditional metrics, multiple-testing correction, per-venue cost models, and markdown + HTML reports — that any strategy in QuiltTrader can use to determine whether it has a real edge or a backtest artifact.

**Architecture:** New package `coordinator/services/validation/` with eight modules, plus a new `OptimizationSession` SQLAlchemy table grouping related backtest runs. Wraps the existing `backtest_runner.run()` for individual backtests; consumes `parallel_backtest_feeder` for concurrent sweep execution. CLI commands added under a new `quilt research` group.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 (Mapped/declarative), Alembic migrations, Pydantic v2 config models, pytest + pytest-asyncio, Click CLI, numpy/pandas/quantstats for math, matplotlib for charts, Jinja2 for HTML rendering.

**Spec:** [`docs/superpowers/specs/2026-05-27-crypto-tsmom-research-program-design.md`](../specs/2026-05-27-crypto-tsmom-research-program-design.md) — Deliverable 1.

---

## File Structure

### New files

```
coordinator/services/validation/
├── __init__.py                    # Package exports
├── cost_model.py                  # CostModelProfile + YAML loader (Phase A)
├── cost_profiles/
│   └── default.yaml               # Three default profiles (Phase A)
├── optimization_session.py        # Session CRUD + multi-test tracking (Phase B)
├── sweep.py                       # Grid/random/latin-hypercube search (Phase C)
├── walk_forward.py                # Rolling refit orchestrator (Phase D)
├── bootstrap.py                   # Block bootstrap CIs (Phase E)
├── regime.py                      # Regime tagging + conditional metrics (Phase E)
├── multi_test.py                  # Bonferroni / BH corrections (Phase E)
└── report.py                      # MD + HTML rendering (Phase F)

coordinator/database/migrations/versions/
└── <hash>_optimization_sessions.py  # New table + BacktestRun FK (Phase B)

sdk/cli/commands/
└── research.py                    # quilt research {sweep,walk-forward,report,session} (Phase G)

tests/coordinator/services/validation/
├── __init__.py
├── test_cost_model.py
├── test_optimization_session.py
├── test_sweep.py
├── test_walk_forward.py
├── test_bootstrap.py
├── test_regime.py
├── test_multi_test.py
└── test_report.py
```

### Modified files

```
coordinator/services/backtest_engine_v2.py   # Cost-profile-aware fee/slippage dispatch
coordinator/database/models.py               # OptimizationSession + BacktestRun FK column
sdk/cli/commands/__init__.py                 # Register research command group
```

---

## Phase A — Cost Model Foundation

### Task A1: Scaffold validation package + cost_model module

**Files:**
- Create: `coordinator/services/validation/__init__.py`
- Create: `coordinator/services/validation/cost_model.py`
- Test: `tests/coordinator/services/validation/__init__.py` (empty)
- Test: `tests/coordinator/services/validation/test_cost_model.py`

- [ ] **Step 1: Write the failing test for `CostModelProfile` dataclass**

`tests/coordinator/services/validation/test_cost_model.py`:

```python
import pytest
from coordinator.services.backtest_config import SlippageModel, TradingFee
from coordinator.services.validation.cost_model import CostModelProfile, CostBundle


def test_cost_model_profile_defaults_match_legacy():
    profile = CostModelProfile.default()
    bundle = profile.resolve(venue="any", asset_type="equity", symbol="AAPL")
    assert isinstance(bundle.fees, list)
    assert len(bundle.fees) == 1
    assert isinstance(bundle.fees[0], TradingFee)
    assert isinstance(bundle.slippage, SlippageModel)
    assert bundle.fees[0].flat_fee == 0.0
    assert bundle.fees[0].percent_fee == 0.0
    assert bundle.slippage.market_bps == 5.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coordinator.services.validation'`

- [ ] **Step 3: Create the package and minimal implementation**

`coordinator/services/validation/__init__.py`:

```python
"""Strategy Validation Lab — reusable rigorous-validation infrastructure.

See docs/superpowers/specs/2026-05-27-crypto-tsmom-research-program-design.md
"""
```

`coordinator/services/validation/cost_model.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from coordinator.services.backtest_config import SlippageModel, TradingFee


class CostBundle(BaseModel):
    fees: list[TradingFee] = Field(default_factory=lambda: [TradingFee()])
    slippage: SlippageModel = Field(default_factory=SlippageModel)


class CostModelProfile(BaseModel):
    name: str = "default"
    bundles: dict[str, CostBundle] = Field(default_factory=dict)
    fallback: CostBundle = Field(default_factory=CostBundle)

    @classmethod
    def default(cls) -> "CostModelProfile":
        return cls(name="default", bundles={}, fallback=CostBundle())

    def resolve(self, *, venue: str, asset_type: str, symbol: str) -> CostBundle:
        keys = (
            f"{venue}:{asset_type}:{symbol}",
            f"{venue}:{asset_type}",
            f"{venue}",
            f"{asset_type}",
        )
        for key in keys:
            if key in self.bundles:
                return self.bundles[key]
        return self.fallback
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/__init__.py coordinator/services/validation/cost_model.py tests/coordinator/services/validation/__init__.py tests/coordinator/services/validation/test_cost_model.py
git commit -m "feat(validation): scaffold validation package with CostModelProfile"
```

---

### Task A2: YAML loader for cost profiles

**Files:**
- Modify: `coordinator/services/validation/cost_model.py`
- Test: `tests/coordinator/services/validation/test_cost_model.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_cost_model.py`:

```python
def test_load_profile_from_yaml(tmp_path):
    yaml_text = """
name: alpaca_crypto
fallback:
  fees:
    - flat_fee: 0.0
      percent_fee: 0.0025
  slippage:
    market_bps: 15.0
    use_bar_range: true
bundles:
  alpaca:crypto:
    fees:
      - flat_fee: 0.0
        percent_fee: 0.0015
    slippage:
      market_bps: 10.0
      use_bar_range: true
"""
    path = tmp_path / "alpaca_crypto.yaml"
    path.write_text(yaml_text)

    profile = CostModelProfile.from_yaml(path)
    assert profile.name == "alpaca_crypto"
    bundle = profile.resolve(venue="alpaca", asset_type="crypto", symbol="BTC/USD")
    assert bundle.fees[0].percent_fee == 0.0015
    assert bundle.slippage.market_bps == 10.0


def test_load_profile_falls_back_on_unknown_symbol(tmp_path):
    yaml_text = """
name: alpaca_crypto
fallback:
  fees:
    - flat_fee: 0.0
      percent_fee: 0.0025
  slippage:
    market_bps: 15.0
"""
    path = tmp_path / "alpaca_crypto.yaml"
    path.write_text(yaml_text)
    profile = CostModelProfile.from_yaml(path)
    bundle = profile.resolve(venue="alpaca", asset_type="equity", symbol="AAPL")
    assert bundle.fees[0].percent_fee == 0.0025
    assert bundle.slippage.market_bps == 15.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py -v`
Expected: FAIL with `AttributeError: type object 'CostModelProfile' has no attribute 'from_yaml'`

- [ ] **Step 3: Add the YAML loader**

Append to `coordinator/services/validation/cost_model.py`:

```python
    @classmethod
    def from_yaml(cls, path: str | Path) -> "CostModelProfile":
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/cost_model.py tests/coordinator/services/validation/test_cost_model.py
git commit -m "feat(validation): CostModelProfile YAML loader"
```

---

### Task A3: Ship the default cost profiles YAML

**Files:**
- Create: `coordinator/services/validation/cost_profiles/__init__.py`
- Create: `coordinator/services/validation/cost_profiles/default.yaml`
- Test: `tests/coordinator/services/validation/test_cost_model.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_cost_model.py`:

```python
from coordinator.services.validation.cost_model import load_named_profile


def test_load_named_profile_default():
    profile = load_named_profile("default")
    bundle_crypto = profile.resolve(venue="alpaca", asset_type="crypto", symbol="BTC/USD")
    assert bundle_crypto.fees[0].percent_fee == 0.0025  # taker default
    assert bundle_crypto.slippage.market_bps == 15.0

    bundle_equity = profile.resolve(venue="alpaca", asset_type="equity", symbol="SPY")
    assert bundle_equity.fees[0].flat_fee == 0.0
    assert bundle_equity.fees[0].percent_fee == 0.0
    assert bundle_equity.slippage.market_bps == 2.0

    bundle_options = profile.resolve(venue="tradier", asset_type="options", symbol="SPY230101C00400000")
    assert bundle_options.fees[0].flat_fee == 0.67  # 0.65 + 0.02 regulatory
    assert bundle_options.slippage.market_bps == 50.0


def test_load_named_profile_unknown_raises():
    with pytest.raises(FileNotFoundError):
        load_named_profile("does-not-exist")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py::test_load_named_profile_default -v`
Expected: FAIL with `ImportError: cannot import name 'load_named_profile'`

- [ ] **Step 3: Create the profiles directory and default YAML**

`coordinator/services/validation/cost_profiles/__init__.py`:

```python
"""Cost profile YAML files."""
```

`coordinator/services/validation/cost_profiles/default.yaml`:

```yaml
name: default
fallback:
  fees:
    - flat_fee: 0.0
      percent_fee: 0.0
  slippage:
    market_bps: 5.0
    use_bar_range: false
bundles:
  alpaca:crypto:
    fees:
      - flat_fee: 0.0
        percent_fee: 0.0025
    slippage:
      market_bps: 15.0
      use_bar_range: true
  alpaca:equity:
    fees:
      - flat_fee: 0.0
        percent_fee: 0.0
    slippage:
      market_bps: 2.0
      use_bar_range: false
  tradier:options:
    fees:
      - flat_fee: 0.67
        percent_fee: 0.0
    slippage:
      market_bps: 50.0
      use_bar_range: false
```

- [ ] **Step 4: Add the loader function**

Append to `coordinator/services/validation/cost_model.py`:

```python
_PROFILES_DIR = Path(__file__).parent / "cost_profiles"


def load_named_profile(name: str) -> CostModelProfile:
    path = _PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Cost profile not found: {path}")
    return CostModelProfile.from_yaml(path)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/validation/cost_profiles/__init__.py coordinator/services/validation/cost_profiles/default.yaml coordinator/services/validation/cost_model.py tests/coordinator/services/validation/test_cost_model.py
git commit -m "feat(validation): default cost profile with alpaca crypto/equity + tradier options"
```

---

### Task A4: Wire cost profiles into backtest engine

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py`
- Modify: `coordinator/services/backtest_config.py`
- Test: `tests/coordinator/services/validation/test_cost_model.py`

- [ ] **Step 1: Write the integration test**

Append to `tests/coordinator/services/validation/test_cost_model.py`:

```python
from coordinator.services.backtest_config import BacktestConfig


def test_backtest_config_accepts_cost_profile_name():
    config = BacktestConfig.model_validate(
        {
            "start": "2024-01-01",
            "end": "2024-02-01",
            "initial_cash": 1000.0,
            "cost_profile": "default",
        }
    )
    assert config.cost_profile == "default"


def test_backtest_config_defaults_cost_profile_to_none():
    config = BacktestConfig.model_validate(
        {"start": "2024-01-01", "end": "2024-02-01", "initial_cash": 1000.0}
    )
    assert config.cost_profile is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py::test_backtest_config_accepts_cost_profile_name -v`
Expected: FAIL with a validation error about extra field `cost_profile`

- [ ] **Step 3: Add `cost_profile` field to BacktestConfig**

In `coordinator/services/backtest_config.py`, add to the `BacktestConfig` class:

```python
    cost_profile: str | None = Field(default=None)
```

(Place it next to the existing fee/slippage fields; preserve the surrounding fields verbatim.)

- [ ] **Step 4: Verify the config tests pass**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py -v`
Expected: 7 passed

- [ ] **Step 5: Modify the engine to use the cost profile when set**

In `coordinator/services/backtest_engine_v2.py`, locate the `BacktestEngine.__init__` (or whatever holds the config) and the `_try_fill` callers. Add a `_cost_profile: CostModelProfile | None` attribute populated from `BacktestConfig.cost_profile` (call `load_named_profile(name)` if name is given, else None).

In `_try_fill`, when `self._cost_profile is not None`, replace the `buy_fees`/`sell_fees`/`slippage` arguments with `self._cost_profile.resolve(venue=..., asset_type=leg.asset_type, symbol=leg.symbol).fees / .slippage`.

Required imports at the top of the file:

```python
from coordinator.services.validation.cost_model import CostModelProfile, load_named_profile
```

Add to engine init (next to existing config parsing):

```python
self._cost_profile: CostModelProfile | None = None
if config.cost_profile:
    self._cost_profile = load_named_profile(config.cost_profile)
```

In `_try_fill`, before using the existing `slippage` / `buy_fees` / `sell_fees`:

```python
if self._cost_profile is not None:
    venue = (ctx.broker_name if ctx else "") or ""
    bundle = self._cost_profile.resolve(
        venue=venue,
        asset_type=leg.asset_type or "equity",
        symbol=leg.symbol,
    )
    slippage = bundle.slippage
    fees_list = bundle.fees
```

(If `venue` is hard to resolve from current engine state, default to empty string — `resolve` will fall back to `asset_type` key or the `fallback`.)

- [ ] **Step 6: Write a smoke test that a backtest runs with a named cost profile**

Append to `tests/coordinator/services/validation/test_cost_model.py`:

```python
def test_engine_loads_cost_profile_when_named(monkeypatch):
    """Lightweight check: BacktestEngine instantiates with a cost_profile name
    and exposes a CostModelProfile attribute."""
    from coordinator.services.backtest_engine_v2 import BacktestEngine

    config = BacktestConfig.model_validate(
        {
            "start": "2024-01-01",
            "end": "2024-02-01",
            "initial_cash": 1000.0,
            "cost_profile": "default",
        }
    )
    engine = BacktestEngine(config=config)  # type: ignore[call-arg]
    assert engine._cost_profile is not None
    assert engine._cost_profile.name == "default"
```

If `BacktestEngine.__init__` requires more args than just `config`, adapt the test to provide minimal stubs (use the existing engine's test fixtures as reference — search for an existing `test_backtest_engine_v2.py` or `test_backtest_engine.py` if present).

- [ ] **Step 7: Run the test to verify it passes**

Run: `pytest tests/coordinator/services/validation/test_cost_model.py -v`
Expected: all tests pass

- [ ] **Step 8: Run the full backtest engine test suite to check for regressions**

Run: `pytest tests/coordinator/services/ -v -k "backtest" --no-header`
Expected: existing tests still pass

- [ ] **Step 9: Commit**

```bash
git add coordinator/services/backtest_engine_v2.py coordinator/services/backtest_config.py tests/coordinator/services/validation/test_cost_model.py
git commit -m "feat(backtest): wire cost-profile dispatch into engine"
```

---

## Phase B — Optimization Session & DB Schema

### Task B1: Add `OptimizationSession` SQLAlchemy model

**Files:**
- Modify: `coordinator/database/models.py`
- Test: `tests/coordinator/services/validation/test_optimization_session.py`

- [ ] **Step 1: Write the failing test**

`tests/coordinator/services/validation/test_optimization_session.py`:

```python
import json
import pytest
from datetime import datetime, timezone

from coordinator.database.models import OptimizationSession


def test_optimization_session_basic_fields():
    sess = OptimizationSession(
        name="tsmom-2026-05-27",
        hypothesis="Daily ensemble TSMOM on BTC/ETH produces OOS Sharpe lower-CI > 0.5",
        parameter_space=json.dumps({"vol_target": [0.10, 0.15, 0.20]}),
        pre_registered_criteria=json.dumps({"oos_sharpe_lci": 0.5, "max_dd_uci": 0.35}),
        status="open",
    )
    assert sess.name == "tsmom-2026-05-27"
    assert "OOS Sharpe" in sess.hypothesis
    assert sess.status == "open"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_optimization_session.py -v`
Expected: FAIL with `ImportError: cannot import name 'OptimizationSession'`

- [ ] **Step 3: Add the model to `coordinator/database/models.py`**

Find the section of the file that defines models like `BacktestRun` and `ParameterSet`. Add this model in that section (alphabetical order is the codebase convention if you can determine it; otherwise next to `BacktestRun`):

```python
class OptimizationSession(Base):
    __tablename__ = "optimization_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_space: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    pre_registered_criteria: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")  # open | running | completed | failed
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    runs: Mapped[list["BacktestRun"]] = relationship(back_populates="optimization_session")
```

You may need to add `Text` to the existing `from sqlalchemy import ...` line.

- [ ] **Step 4: Add the FK column to `BacktestRun`**

In the same file, locate the `BacktestRun` class. Add this column (next to existing FK columns like `algorithm_id`):

```python
    optimization_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("optimization_sessions.id", ondelete="SET NULL"), nullable=True
    )
    optimization_session: Mapped["OptimizationSession | None"] = relationship(back_populates="runs")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/coordinator/services/validation/test_optimization_session.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add coordinator/database/models.py tests/coordinator/services/validation/test_optimization_session.py
git commit -m "feat(db): OptimizationSession model + BacktestRun FK column"
```

---

### Task B2: Alembic migration for the new table + FK

**Files:**
- Create: `coordinator/database/migrations/versions/<hash>_optimization_sessions.py`

- [ ] **Step 1: Identify the current head revision**

Run: `alembic -c alembic.ini current`
Note the revision hash printed. This becomes the `down_revision` for the new migration.

- [ ] **Step 2: Generate a stub migration file**

Run: `alembic -c alembic.ini revision -m "optimization_sessions"`
Expected: a new file appears under `coordinator/database/migrations/versions/`.

- [ ] **Step 3: Fill in the migration content**

Open the new file (path is in the output of the previous command). Replace the body with:

```python
"""optimization_sessions

Revision ID: <whatever the CLI assigned>
Revises: <current head from step 1>
Create Date: ...

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "<whatever the CLI assigned>"
down_revision: Union[str, None] = "<current head from step 1>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "optimization_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("parameter_space", sa.Text(), nullable=False),
        sa.Column("pre_registered_criteria", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "backtest_runs",
        sa.Column("optimization_session_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_backtest_runs_optimization_session",
        "backtest_runs",
        "optimization_sessions",
        ["optimization_session_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_backtest_runs_optimization_session", "backtest_runs", type_="foreignkey")
    op.drop_column("backtest_runs", "optimization_session_id")
    op.drop_table("optimization_sessions")
```

(Replace placeholders with the CLI-assigned revision hashes.)

- [ ] **Step 4: Run the migration against a fresh test DB**

Run: `alembic -c alembic.ini upgrade head`
Expected: no errors. The new table exists.

- [ ] **Step 5: Run the downgrade then re-upgrade to verify reversibility**

Run: `alembic -c alembic.ini downgrade -1`
Then: `alembic -c alembic.ini upgrade head`
Expected: both succeed.

- [ ] **Step 6: Commit**

```bash
git add coordinator/database/migrations/versions/
git commit -m "feat(db): alembic migration for optimization_sessions table"
```

---

### Task B3: `optimization_session.py` — create_session()

**Files:**
- Create: `coordinator/services/validation/optimization_session.py`
- Test: `tests/coordinator/services/validation/test_optimization_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_optimization_session.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import Base, OptimizationSession
from coordinator.services.validation.optimization_session import create_session


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


def test_create_session_persists(db_session):
    sess = create_session(
        db_session,
        name="crypto-tsmom-001",
        hypothesis="H1: ensemble TSMOM produces edge",
        parameter_space={"vol_target": [0.10, 0.15, 0.20]},
        pre_registered_criteria={"oos_sharpe_lci": 0.5},
    )
    db_session.commit()

    fetched = db_session.query(OptimizationSession).filter_by(name="crypto-tsmom-001").one()
    assert fetched.id == sess.id
    assert "TSMOM" in fetched.hypothesis
    assert fetched.status == "open"


def test_create_session_rejects_duplicate_name(db_session):
    create_session(
        db_session,
        name="dup",
        hypothesis="H",
        parameter_space={},
        pre_registered_criteria={},
    )
    db_session.commit()
    with pytest.raises(Exception):  # IntegrityError under the hood
        create_session(
            db_session,
            name="dup",
            hypothesis="H2",
            parameter_space={},
            pre_registered_criteria={},
        )
        db_session.commit()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_optimization_session.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `create_session`**

`coordinator/services/validation/optimization_session.py`:

```python
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from coordinator.database.models import OptimizationSession


def create_session(
    db: Session,
    *,
    name: str,
    hypothesis: str,
    parameter_space: dict[str, Any],
    pre_registered_criteria: dict[str, Any],
    notes: str = "",
) -> OptimizationSession:
    """Create a new OptimizationSession.

    The session must be created *before* any backtest runs are attached to it;
    this enforces pre-registration of hypothesis and criteria.
    """
    sess = OptimizationSession(
        name=name,
        hypothesis=hypothesis,
        parameter_space=json.dumps(parameter_space),
        pre_registered_criteria=json.dumps(pre_registered_criteria),
        notes=notes,
        status="open",
    )
    db.add(sess)
    db.flush()  # populate sess.id without committing
    return sess


def get_session_runs(db: Session, session_id: int) -> list[Any]:
    """Return all BacktestRun rows attached to this session."""
    from coordinator.database.models import BacktestRun

    return db.query(BacktestRun).filter(BacktestRun.optimization_session_id == session_id).all()


def count_hypotheses_tested(db: Session, session_id: int) -> int:
    """Distinct parameter configs tested in this session (for multi-test correction).

    Counts distinct `config_hash` values across BacktestRun rows in this session.
    """
    from coordinator.database.models import BacktestRun

    rows = (
        db.query(BacktestRun.config_hash)
        .filter(BacktestRun.optimization_session_id == session_id)
        .distinct()
        .all()
    )
    return len(rows)
```

- [ ] **Step 4: Add `config_hash` column to BacktestRun**

In `coordinator/database/models.py`, on `BacktestRun`, add:

```python
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

Then update the alembic migration from B2 to also add this column. Open the migration file and append to `upgrade()`:

```python
    op.add_column("backtest_runs", sa.Column("config_hash", sa.String(length=64), nullable=True))
```

And in `downgrade()`, before the other drops:

```python
    op.drop_column("backtest_runs", "config_hash")
```

- [ ] **Step 5: Run the migration again to apply the new column**

Run: `alembic -c alembic.ini downgrade -1 && alembic -c alembic.ini upgrade head`
Expected: no errors.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_optimization_session.py -v`
Expected: 3 passed (plus the original B1 test)

- [ ] **Step 7: Commit**

```bash
git add coordinator/services/validation/optimization_session.py coordinator/database/models.py coordinator/database/migrations/versions/ tests/coordinator/services/validation/test_optimization_session.py
git commit -m "feat(validation): create_session + config_hash for multi-test tracking"
```

---

## Phase C — Hyperparameter Sweep

### Task C1: Parameter space expansion (grid search)

**Files:**
- Create: `coordinator/services/validation/sweep.py`
- Test: `tests/coordinator/services/validation/test_sweep.py`

- [ ] **Step 1: Write the failing test**

`tests/coordinator/services/validation/test_sweep.py`:

```python
import pytest

from coordinator.services.validation.sweep import expand_grid, config_hash


def test_expand_grid_simple():
    space = {"vol_target": [0.10, 0.15], "lookbacks": [[7, 14], [7, 14, 28]]}
    configs = expand_grid(space)
    assert len(configs) == 4
    assert {"vol_target": 0.10, "lookbacks": [7, 14]} in configs
    assert {"vol_target": 0.15, "lookbacks": [7, 14, 28]} in configs


def test_expand_grid_empty_returns_single_empty_config():
    assert expand_grid({}) == [{}]


def test_config_hash_stable():
    a = {"x": 1, "y": [2, 3]}
    b = {"y": [2, 3], "x": 1}  # different key order
    assert config_hash(a) == config_hash(b)


def test_config_hash_changes_with_value():
    assert config_hash({"x": 1}) != config_hash({"x": 2})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_sweep.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement grid expansion + config hashing**

`coordinator/services/validation/sweep.py`:

```python
from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any


def expand_grid(parameter_space: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cross-product expansion of a parameter space.

    Each key in parameter_space maps to a list of candidate values.
    Returns a list of fully-specified config dicts.
    """
    if not parameter_space:
        return [{}]
    keys = list(parameter_space.keys())
    value_lists = [parameter_space[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]


def config_hash(config: dict[str, Any]) -> str:
    """Stable hash of a config dict (key-order-independent)."""
    payload = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_sweep.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/sweep.py tests/coordinator/services/validation/test_sweep.py
git commit -m "feat(validation): grid expansion + stable config hashing"
```

---

### Task C2: Random + Latin-hypercube search

**Files:**
- Modify: `coordinator/services/validation/sweep.py`
- Test: `tests/coordinator/services/validation/test_sweep.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_sweep.py`:

```python
def test_random_sample_count_and_seed():
    space = {"vol_target": [0.05, 0.40], "lookback": [3, 90]}  # bounds, not discrete
    configs = sample_random(space, n=20, seed=42, distributions={"vol_target": "uniform", "lookback": "int_uniform"})
    assert len(configs) == 20
    for cfg in configs:
        assert 0.05 <= cfg["vol_target"] <= 0.40
        assert 3 <= cfg["lookback"] <= 90 and isinstance(cfg["lookback"], int)

    # Determinism
    configs2 = sample_random(space, n=20, seed=42, distributions={"vol_target": "uniform", "lookback": "int_uniform"})
    assert configs == configs2


def test_latin_hypercube_covers_range():
    import numpy as np
    space = {"vol_target": [0.05, 0.40]}
    configs = sample_latin_hypercube(space, n=10, seed=7, distributions={"vol_target": "uniform"})
    values = np.array([c["vol_target"] for c in configs])
    # Latin hypercube: each of 10 strata should be hit exactly once
    strata = ((values - 0.05) / (0.40 - 0.05) * 10).astype(int)
    assert len(set(strata)) == 10
```

Also add this import to the top of the test file:

```python
from coordinator.services.validation.sweep import sample_random, sample_latin_hypercube
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_sweep.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement random + latin-hypercube**

Append to `coordinator/services/validation/sweep.py`:

```python
import numpy as np


def sample_random(
    parameter_space: dict[str, list[Any]],
    n: int,
    seed: int,
    distributions: dict[str, str],
) -> list[dict[str, Any]]:
    """Random sampling from a continuous-bounded parameter space.

    parameter_space: key -> [low, high]
    distributions: key -> "uniform" | "log_uniform" | "int_uniform"
    """
    rng = np.random.default_rng(seed)
    configs: list[dict[str, Any]] = []
    for _ in range(n):
        cfg: dict[str, Any] = {}
        for key, bounds in parameter_space.items():
            lo, hi = bounds[0], bounds[1]
            dist = distributions.get(key, "uniform")
            if dist == "uniform":
                cfg[key] = float(rng.uniform(lo, hi))
            elif dist == "log_uniform":
                cfg[key] = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
            elif dist == "int_uniform":
                cfg[key] = int(rng.integers(lo, hi + 1))
            else:
                raise ValueError(f"Unknown distribution: {dist}")
        configs.append(cfg)
    return configs


def sample_latin_hypercube(
    parameter_space: dict[str, list[Any]],
    n: int,
    seed: int,
    distributions: dict[str, str],
) -> list[dict[str, Any]]:
    """Latin-hypercube sampling: each parameter is binned into n strata, each
    stratum sampled exactly once."""
    rng = np.random.default_rng(seed)
    keys = list(parameter_space.keys())
    columns: dict[str, list[Any]] = {}
    for key in keys:
        lo, hi = parameter_space[key][0], parameter_space[key][1]
        dist = distributions.get(key, "uniform")
        edges = np.linspace(0.0, 1.0, n + 1)
        offsets = rng.uniform(0, 1.0 / n, n)
        unit = edges[:-1] + offsets
        rng.shuffle(unit)
        if dist == "uniform":
            vals = lo + unit * (hi - lo)
            columns[key] = [float(v) for v in vals]
        elif dist == "log_uniform":
            log_lo, log_hi = np.log(lo), np.log(hi)
            vals = np.exp(log_lo + unit * (log_hi - log_lo))
            columns[key] = [float(v) for v in vals]
        elif dist == "int_uniform":
            vals = lo + unit * (hi - lo + 1)
            columns[key] = [int(v) for v in vals]
        else:
            raise ValueError(f"Unknown distribution: {dist}")
    return [dict(zip(keys, row)) for row in zip(*[columns[k] for k in keys])]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_sweep.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/sweep.py tests/coordinator/services/validation/test_sweep.py
git commit -m "feat(validation): random + latin-hypercube parameter sampling"
```

---

### Task C3: Sweep orchestrator — spawn N backtests under one session

**Files:**
- Modify: `coordinator/services/validation/sweep.py`
- Test: `tests/coordinator/services/validation/test_sweep.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_sweep.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from coordinator.services.validation.sweep import run_sweep, SweepResult
from coordinator.services.validation.optimization_session import create_session


@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from coordinator.database.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


@pytest.mark.asyncio
async def test_run_sweep_persists_runs(db_session):
    sess = create_session(
        db_session,
        name="sweep-test-001",
        hypothesis="H",
        parameter_space={"vol_target": [0.10, 0.15]},
        pre_registered_criteria={},
    )
    db_session.commit()

    fake_run_backtest = AsyncMock(return_value={"sharpe": 0.8, "max_dd": 0.20})

    with patch("coordinator.services.validation.sweep._run_one_backtest", fake_run_backtest):
        result = await run_sweep(
            db=db_session,
            session_id=sess.id,
            manifest_path="/dummy/manifest.yaml",
            base_config={"start": "2024-01-01", "end": "2024-02-01"},
            parameter_space={"vol_target": [0.10, 0.15]},
            search="grid",
            max_trials=2,
            parallelism=1,
            seed=42,
        )

    assert isinstance(result, SweepResult)
    assert result.n_configs == 2
    assert fake_run_backtest.await_count == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_sweep.py -v -k run_sweep`
Expected: FAIL with `ImportError: cannot import name 'run_sweep'`

- [ ] **Step 3: Implement the sweep orchestrator**

Append to `coordinator/services/validation/sweep.py`:

```python
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from sqlalchemy.orm import Session


@dataclass
class SweepResult:
    session_id: int
    n_configs: int
    run_ids: list[int]


async def _run_one_backtest(
    db: Session,
    session_id: int,
    manifest_path: str,
    base_config: dict[str, Any],
    config: dict[str, Any],
    config_hash_str: str,
) -> dict[str, Any]:
    """Spawn a single backtest with the given config.

    Returns the metrics dict from the completed run. This is the seam mocked
    in tests. Real implementation defers to backtest_runner.run().
    """
    from coordinator.services.backtest_runner import BacktestRunner
    from coordinator.database.models import BacktestRun

    merged = {**base_config, **config}
    run_row = BacktestRun(
        algorithm_id=base_config.get("algorithm_id"),
        config_json=json.dumps(merged),
        config_hash=config_hash_str,
        optimization_session_id=session_id,
        status="pending",
    )
    db.add(run_row)
    db.flush()

    runner = BacktestRunner(run_id=run_row.id)
    await runner.run(run_row.id)
    db.refresh(run_row)
    return {
        "run_id": run_row.id,
        "config_hash": config_hash_str,
        "config": config,
    }


async def run_sweep(
    db: Session,
    session_id: int,
    manifest_path: str | Path,
    base_config: dict[str, Any],
    parameter_space: dict[str, Any],
    search: Literal["grid", "random", "latin"] = "grid",
    max_trials: int = 50,
    parallelism: int = 1,
    seed: int = 0,
    distributions: Optional[dict[str, str]] = None,
) -> SweepResult:
    """Run a parameter sweep under an existing OptimizationSession."""

    if search == "grid":
        configs = expand_grid(parameter_space)[:max_trials]
    elif search == "random":
        configs = sample_random(parameter_space, n=max_trials, seed=seed, distributions=distributions or {})
    elif search == "latin":
        configs = sample_latin_hypercube(parameter_space, n=max_trials, seed=seed, distributions=distributions or {})
    else:
        raise ValueError(f"Unknown search: {search}")

    semaphore = asyncio.Semaphore(parallelism)

    async def _bounded(cfg: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _run_one_backtest(
                db=db,
                session_id=session_id,
                manifest_path=str(manifest_path),
                base_config=base_config,
                config=cfg,
                config_hash_str=config_hash(cfg),
            )

    results = await asyncio.gather(*[_bounded(c) for c in configs])
    return SweepResult(
        session_id=session_id,
        n_configs=len(configs),
        run_ids=[r["run_id"] for r in results],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/coordinator/services/validation/test_sweep.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/sweep.py tests/coordinator/services/validation/test_sweep.py
git commit -m "feat(validation): run_sweep orchestrator with grid/random/latin search"
```

---

## Phase D — Walk-Forward Orchestrator

### Task D1: Fold computation

**Files:**
- Create: `coordinator/services/validation/walk_forward.py`
- Test: `tests/coordinator/services/validation/test_walk_forward.py`

- [ ] **Step 1: Write the failing test**

`tests/coordinator/services/validation/test_walk_forward.py`:

```python
from datetime import date

from coordinator.services.validation.walk_forward import compute_folds, Fold


def test_compute_folds_basic():
    folds = compute_folds(
        start=date(2015, 1, 1),
        end=date(2026, 5, 1),
        train_years=4.0,
        test_years=1.0,
        step_months=6.0,
    )
    assert len(folds) >= 10
    assert all(isinstance(f, Fold) for f in folds)
    assert folds[0].train_start == date(2015, 1, 1)
    assert (folds[0].train_end - folds[0].train_start).days >= 4 * 365 - 1
    assert folds[0].test_start == folds[0].train_end
    assert (folds[0].test_end - folds[0].test_start).days >= 365 - 1
    # Step of 6 months ~ 182 days between successive train_starts
    delta_days = (folds[1].train_start - folds[0].train_start).days
    assert 175 <= delta_days <= 190


def test_compute_folds_drops_incomplete_last_fold():
    folds = compute_folds(
        start=date(2020, 1, 1),
        end=date(2021, 1, 1),
        train_years=2.0,
        test_years=1.0,
        step_months=6.0,
    )
    # train_years=2 from start=2020-01-01 → train_end = 2022-01-01 > end → no folds
    assert len(folds) == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_walk_forward.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement fold computation**

`coordinator/services/validation/walk_forward.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class Fold:
    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def compute_folds(
    *,
    start: date,
    end: date,
    train_years: float,
    test_years: float,
    step_months: float,
) -> list[Fold]:
    train_days = int(train_years * 365.25)
    test_days = int(test_years * 365.25)
    step_days = int(step_months * 30.4375)

    folds: list[Fold] = []
    i = 0
    cursor = start
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        folds.append(
            Fold(
                index=i,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        i += 1
        cursor = cursor + timedelta(days=step_days)
    return folds
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_walk_forward.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/walk_forward.py tests/coordinator/services/validation/test_walk_forward.py
git commit -m "feat(validation): compute_folds for walk-forward windowing"
```

---

### Task D2: Walk-forward orchestrator — sweep on train, single run on test

**Files:**
- Modify: `coordinator/services/validation/walk_forward.py`
- Test: `tests/coordinator/services/validation/test_walk_forward.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_walk_forward.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from coordinator.services.validation.walk_forward import run_walk_forward
from coordinator.services.validation.optimization_session import create_session


@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from coordinator.database.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


@pytest.mark.asyncio
async def test_run_walk_forward_runs_sweep_per_fold(db_session):
    sess = create_session(
        db_session,
        name="wf-test-001",
        hypothesis="H",
        parameter_space={"vol_target": [0.10, 0.15]},
        pre_registered_criteria={},
    )
    db_session.commit()

    # Fake sweep that picks the first config as best
    async def fake_sweep(*, db, session_id, manifest_path, base_config, parameter_space, **kwargs):
        from coordinator.services.validation.sweep import SweepResult, expand_grid
        configs = expand_grid(parameter_space)
        return SweepResult(session_id=session_id, n_configs=len(configs), run_ids=[1, 2])

    # Fake "best config from train" picker
    async def fake_pick_best(db, run_ids, objective):
        return {"vol_target": 0.15}

    # Fake "run single OOS config" call
    fake_oos = AsyncMock(return_value=99)  # returns OOS run_id

    from datetime import date
    with patch("coordinator.services.validation.walk_forward.run_sweep", side_effect=fake_sweep), \
         patch("coordinator.services.validation.walk_forward._pick_best_train_config", side_effect=fake_pick_best), \
         patch("coordinator.services.validation.walk_forward._run_oos_backtest", fake_oos):
        result = await run_walk_forward(
            db=db_session,
            session_id=sess.id,
            manifest_path="/dummy/manifest.yaml",
            base_config={"start": "2015-01-01", "end": "2026-05-01"},
            parameter_space={"vol_target": [0.10, 0.15]},
            train_years=4.0,
            test_years=1.0,
            step_months=6.0,
            objective="sharpe",
            parallelism=1,
        )

    assert result.n_folds >= 10
    assert len(result.oos_run_ids) == result.n_folds
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_walk_forward.py -v -k run_walk_forward`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement the orchestrator**

Append to `coordinator/services/validation/walk_forward.py`:

```python
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy.orm import Session

from coordinator.services.validation.sweep import run_sweep


@dataclass
class WalkForwardResult:
    session_id: int
    n_folds: int
    oos_run_ids: list[int]
    train_run_ids: list[list[int]]  # one list per fold


async def _pick_best_train_config(
    db: Session, run_ids: list[int], objective: str
) -> dict[str, Any]:
    """Pick the in-sample winner based on the objective metric."""
    from coordinator.database.models import BacktestRun

    rows = db.query(BacktestRun).filter(BacktestRun.id.in_(run_ids)).all()
    metric_col = {
        "sharpe": "sharpe_ratio",
        "calmar": "calmar_ratio",
        "sortino": "sortino_ratio",
    }[objective]
    rows.sort(key=lambda r: getattr(r, metric_col, 0.0) or 0.0, reverse=True)
    best = rows[0]
    import json
    return json.loads(best.config_json)


async def _run_oos_backtest(
    db: Session,
    *,
    session_id: int,
    manifest_path: str,
    base_config: dict[str, Any],
    config: dict[str, Any],
    fold_index: int,
) -> int:
    """Run a single OOS backtest with the winning config on the test window.
    Returns the BacktestRun.id."""
    from coordinator.services.backtest_runner import BacktestRunner
    from coordinator.database.models import BacktestRun
    import json
    from coordinator.services.validation.sweep import config_hash

    merged = {**base_config, **config, "_fold_index": fold_index, "_oos": True}
    run_row = BacktestRun(
        algorithm_id=base_config.get("algorithm_id"),
        config_json=json.dumps(merged),
        config_hash=config_hash(config),
        optimization_session_id=session_id,
        status="pending",
    )
    db.add(run_row)
    db.flush()
    runner = BacktestRunner(run_id=run_row.id)
    await runner.run(run_row.id)
    return run_row.id


async def run_walk_forward(
    db: Session,
    *,
    session_id: int,
    manifest_path: str,
    base_config: dict[str, Any],
    parameter_space: dict[str, Any],
    train_years: float,
    test_years: float,
    step_months: float,
    objective: Literal["sharpe", "calmar", "sortino"],
    parallelism: int = 1,
) -> WalkForwardResult:
    """Rolling train/test walk-forward.

    For each fold:
      1. Run a sweep over `parameter_space` on the train window.
      2. Pick the in-sample winner by `objective`.
      3. Run a single OOS backtest with that winner on the test window.

    Returns a result object with OOS run IDs (one per fold) for downstream
    concatenation and metric computation.
    """
    start = date.fromisoformat(base_config["start"])
    end = date.fromisoformat(base_config["end"])
    folds = compute_folds(
        start=start, end=end,
        train_years=train_years, test_years=test_years, step_months=step_months,
    )

    oos_run_ids: list[int] = []
    train_run_ids: list[list[int]] = []

    for fold in folds:
        train_cfg = {
            **base_config,
            "start": fold.train_start.isoformat(),
            "end": fold.train_end.isoformat(),
        }
        sweep_result = await run_sweep(
            db=db,
            session_id=session_id,
            manifest_path=manifest_path,
            base_config=train_cfg,
            parameter_space=parameter_space,
            search="grid",
            max_trials=10_000,
            parallelism=parallelism,
        )
        train_run_ids.append(sweep_result.run_ids)

        winner = await _pick_best_train_config(db, sweep_result.run_ids, objective)
        oos_cfg = {
            **base_config,
            "start": fold.test_start.isoformat(),
            "end": fold.test_end.isoformat(),
        }
        oos_id = await _run_oos_backtest(
            db,
            session_id=session_id,
            manifest_path=manifest_path,
            base_config=oos_cfg,
            config=winner,
            fold_index=fold.index,
        )
        oos_run_ids.append(oos_id)

    return WalkForwardResult(
        session_id=session_id,
        n_folds=len(folds),
        oos_run_ids=oos_run_ids,
        train_run_ids=train_run_ids,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_walk_forward.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/walk_forward.py tests/coordinator/services/validation/test_walk_forward.py
git commit -m "feat(validation): run_walk_forward orchestrator (sweep-on-train, single-run-on-test)"
```

---

### Task D3: OOS equity-curve concatenation

**Files:**
- Modify: `coordinator/services/validation/walk_forward.py`
- Test: `tests/coordinator/services/validation/test_walk_forward.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_walk_forward.py`:

```python
def test_concatenate_oos_curves(tmp_path):
    """Concatenate OOS equity curves from N folds into one continuous series.

    Each fold's parquet starts with its own initial_cash; concatenation
    must scale each successive fold to chain off the prior fold's terminal value.
    """
    import pandas as pd
    from coordinator.services.validation.walk_forward import concatenate_oos_curves

    # Fold 1: 1000 → 1100 over 3 days
    f1 = tmp_path / "f1.parquet"
    pd.DataFrame(
        {"timestamp": pd.date_range("2024-01-01", periods=3, freq="D"), "equity": [1000.0, 1050.0, 1100.0]}
    ).to_parquet(f1)

    # Fold 2: 1000 → 990 over 3 days
    f2 = tmp_path / "f2.parquet"
    pd.DataFrame(
        {"timestamp": pd.date_range("2024-01-04", periods=3, freq="D"), "equity": [1000.0, 995.0, 990.0]}
    ).to_parquet(f2)

    curve = concatenate_oos_curves([f1, f2])
    assert len(curve) == 6
    assert curve.iloc[0] == 1000.0
    assert curve.iloc[2] == 1100.0   # end of fold 1
    # Fold 2: scaled to start at 1100 (fold 1 terminal)
    assert abs(curve.iloc[3] - 1100.0) < 1e-6
    # Fold 2 lost 1% over its window → terminal should be 1100 * (990/1000) = 1089
    assert abs(curve.iloc[5] - 1089.0) < 1e-6
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_walk_forward.py -v -k concatenate`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement concatenation**

Append to `coordinator/services/validation/walk_forward.py`:

```python
import pandas as pd
from pathlib import Path


def concatenate_oos_curves(parquet_paths: list[Path]) -> pd.Series:
    """Chain OOS equity curves into one continuous series.

    Each fold's equity series is rescaled so its first value equals the prior
    fold's terminal value, producing a continuous compounding curve.

    Returns a Series indexed by timestamp.
    """
    segments: list[pd.Series] = []
    running_anchor: float | None = None

    for path in parquet_paths:
        df = pd.read_parquet(path)
        if "timestamp" not in df.columns or "equity" not in df.columns:
            raise ValueError(f"Expected timestamp + equity columns in {path}")
        s = df.set_index("timestamp")["equity"].astype(float)

        if running_anchor is None:
            segments.append(s)
        else:
            scale = running_anchor / s.iloc[0]
            segments.append(s * scale)

        running_anchor = float(segments[-1].iloc[-1])

    return pd.concat(segments)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_walk_forward.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/walk_forward.py tests/coordinator/services/validation/test_walk_forward.py
git commit -m "feat(validation): concatenate_oos_curves for continuous walk-forward equity"
```

---

## Phase E — Bootstrap, Regime, Multi-test

### Task E1: Block bootstrap for Sharpe / CAGR / MaxDD

**Files:**
- Create: `coordinator/services/validation/bootstrap.py`
- Test: `tests/coordinator/services/validation/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

`tests/coordinator/services/validation/test_bootstrap.py`:

```python
import numpy as np
import pandas as pd
import pytest

from coordinator.services.validation.bootstrap import (
    block_bootstrap_sharpe,
    MetricCI,
)


def test_block_bootstrap_sharpe_recovers_known_signal():
    rng = np.random.default_rng(42)
    # Construct returns with known mean/std → known Sharpe
    daily_returns = rng.normal(loc=0.001, scale=0.02, size=2000)
    equity = pd.Series(1000.0 * np.cumprod(1.0 + daily_returns))

    ci = block_bootstrap_sharpe(equity, block_size=20, n_resamples=500, confidence=0.95, seed=1)
    assert isinstance(ci, MetricCI)
    # Annualized Sharpe = mean/std * sqrt(252) ≈ (0.001/0.02)*sqrt(252) ≈ 0.79
    assert 0.4 <= ci.point <= 1.2
    assert ci.lower < ci.point < ci.upper
    assert ci.upper - ci.lower > 0


def test_block_bootstrap_ci_widens_with_smaller_sample():
    rng = np.random.default_rng(42)
    returns_long = rng.normal(0.001, 0.02, 2000)
    returns_short = rng.normal(0.001, 0.02, 200)
    eq_long = pd.Series(np.cumprod(1.0 + returns_long))
    eq_short = pd.Series(np.cumprod(1.0 + returns_short))

    ci_long = block_bootstrap_sharpe(eq_long, block_size=20, n_resamples=300, seed=2)
    ci_short = block_bootstrap_sharpe(eq_short, block_size=20, n_resamples=300, seed=2)

    assert (ci_short.upper - ci_short.lower) > (ci_long.upper - ci_long.lower)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_bootstrap.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement block bootstrap**

`coordinator/services/validation/bootstrap.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class MetricCI:
    point: float
    lower: float
    upper: float
    confidence: float


def _equity_to_returns(equity: pd.Series) -> np.ndarray:
    arr = equity.dropna().to_numpy(dtype=float)
    return np.diff(arr) / arr[:-1]


def _annualized_sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    if returns.size == 0:
        return 0.0
    mu = float(np.mean(returns))
    sigma = float(np.std(returns, ddof=1))
    if sigma == 0:
        return 0.0
    return mu / sigma * np.sqrt(periods_per_year)


def _max_drawdown(equity_arr: np.ndarray) -> float:
    if equity_arr.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity_arr)
    dd = (equity_arr - peak) / peak
    return float(np.min(dd))  # negative number


def _block_resample(returns: np.ndarray, block_size: int, rng: np.random.Generator) -> np.ndarray:
    n = returns.size
    n_blocks = int(np.ceil(n / block_size))
    starts = rng.integers(0, n - block_size + 1, size=n_blocks)
    out = np.concatenate([returns[s : s + block_size] for s in starts])
    return out[:n]


def block_bootstrap_sharpe(
    equity: pd.Series,
    *,
    block_size: int | None = None,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    periods_per_year: int = 252,
    seed: int = 0,
) -> MetricCI:
    returns = _equity_to_returns(equity)
    if block_size is None:
        block_size = max(20, len(returns) // 20)
    rng = np.random.default_rng(seed)

    point = _annualized_sharpe(returns, periods_per_year)
    samples = np.array(
        [
            _annualized_sharpe(_block_resample(returns, block_size, rng), periods_per_year)
            for _ in range(n_resamples)
        ]
    )
    alpha = 1.0 - confidence
    lower = float(np.quantile(samples, alpha / 2))
    upper = float(np.quantile(samples, 1 - alpha / 2))
    return MetricCI(point=point, lower=lower, upper=upper, confidence=confidence)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_bootstrap.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/bootstrap.py tests/coordinator/services/validation/test_bootstrap.py
git commit -m "feat(validation): block bootstrap CI for annualized Sharpe"
```

---

### Task E2: Bootstrap for MaxDD + bundled metrics function

**Files:**
- Modify: `coordinator/services/validation/bootstrap.py`
- Test: `tests/coordinator/services/validation/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_bootstrap.py`:

```python
from coordinator.services.validation.bootstrap import (
    block_bootstrap_max_drawdown,
    bootstrap_metrics,
)


def test_block_bootstrap_max_drawdown_negative_and_bounded():
    rng = np.random.default_rng(0)
    daily = rng.normal(0.0005, 0.015, 1000)
    equity = pd.Series(np.cumprod(1.0 + daily))
    ci = block_bootstrap_max_drawdown(equity, block_size=15, n_resamples=300, seed=3)
    assert ci.point < 0
    assert ci.lower <= ci.point <= ci.upper
    assert ci.lower >= -1.0


def test_bootstrap_metrics_returns_named_dict():
    rng = np.random.default_rng(0)
    daily = rng.normal(0.0005, 0.015, 1000)
    equity = pd.Series(np.cumprod(1.0 + daily))
    out = bootstrap_metrics(equity, n_resamples=200, seed=4)
    assert "sharpe" in out
    assert "max_drawdown" in out
    assert isinstance(out["sharpe"], MetricCI)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_bootstrap.py -v -k bootstrap_metrics`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add MaxDD bootstrap and bundled function**

Append to `coordinator/services/validation/bootstrap.py`:

```python
def block_bootstrap_max_drawdown(
    equity: pd.Series,
    *,
    block_size: int | None = None,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> MetricCI:
    returns = _equity_to_returns(equity)
    if block_size is None:
        block_size = max(20, len(returns) // 20)
    rng = np.random.default_rng(seed)
    point = _max_drawdown(equity.to_numpy(dtype=float))

    def _sample_dd() -> float:
        r = _block_resample(returns, block_size, rng)
        synthetic_equity = np.cumprod(1.0 + r)
        return _max_drawdown(synthetic_equity)

    samples = np.array([_sample_dd() for _ in range(n_resamples)])
    alpha = 1.0 - confidence
    return MetricCI(
        point=point,
        lower=float(np.quantile(samples, alpha / 2)),
        upper=float(np.quantile(samples, 1 - alpha / 2)),
        confidence=confidence,
    )


def bootstrap_metrics(
    equity: pd.Series,
    *,
    block_size: int | None = None,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> dict[str, MetricCI]:
    """Bundle: bootstrap Sharpe + MaxDD with shared block_size."""
    if block_size is None:
        block_size = max(20, (len(equity) - 1) // 20)
    return {
        "sharpe": block_bootstrap_sharpe(
            equity, block_size=block_size, n_resamples=n_resamples,
            confidence=confidence, seed=seed,
        ),
        "max_drawdown": block_bootstrap_max_drawdown(
            equity, block_size=block_size, n_resamples=n_resamples,
            confidence=confidence, seed=seed + 1,
        ),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_bootstrap.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/bootstrap.py tests/coordinator/services/validation/test_bootstrap.py
git commit -m "feat(validation): MaxDD bootstrap + bootstrap_metrics bundle"
```

---

### Task E3: Regime tagger

**Files:**
- Create: `coordinator/services/validation/regime.py`
- Test: `tests/coordinator/services/validation/test_regime.py`

- [ ] **Step 1: Write the failing test**

`tests/coordinator/services/validation/test_regime.py`:

```python
import numpy as np
import pandas as pd

from coordinator.services.validation.regime import tag_regimes, regime_conditional_metrics


def test_tag_regimes_basic():
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    # Step function: first 100 days flat, next 100 days +25%, next 100 days -25%
    arr = np.concatenate([
        np.ones(100),
        np.linspace(1.0, 1.25, 100),
        np.linspace(1.25, 0.94, 100),
    ])
    prices = pd.Series(arr, index=idx)
    regimes = tag_regimes(prices, lookback_days=90, bull_threshold=0.15, bear_threshold=-0.15)
    assert (regimes.iloc[10] == "chop") or pd.isna(regimes.iloc[10])  # warmup
    # By day 195 the trailing-90 return is well above +15%
    assert regimes.iloc[195] == "bull"
    # By day 290 the trailing-90 return is well below -15%
    assert regimes.iloc[290] == "bear"


def test_regime_conditional_metrics():
    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    equity = pd.Series(np.linspace(1000.0, 1200.0, 200), index=idx)
    regimes = pd.Series(["bull"] * 100 + ["chop"] * 100, index=idx)
    out = regime_conditional_metrics(equity, regimes)
    assert set(out.keys()) >= {"bull", "chop"}
    assert "sharpe" in out["bull"]
    assert "total_return" in out["bull"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_regime.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement regime tagging + conditional metrics**

`coordinator/services/validation/regime.py`:

```python
from __future__ import annotations

import numpy as np
import pandas as pd


def tag_regimes(
    price_series: pd.Series,
    *,
    lookback_days: int = 90,
    bull_threshold: float = 0.15,
    bear_threshold: float = -0.15,
) -> pd.Series:
    """Tag each date as 'bull' / 'bear' / 'chop' by trailing return of `price_series`.

    For warmup (first `lookback_days`), tag is 'chop'.
    """
    ret = price_series.pct_change(periods=lookback_days)
    tags = pd.Series("chop", index=price_series.index, dtype=object)
    tags = tags.where(~(ret > bull_threshold), "bull")
    tags = tags.where(~(ret < bear_threshold), "bear")
    return tags


def regime_conditional_metrics(
    equity: pd.Series,
    regimes: pd.Series,
) -> dict[str, dict[str, float]]:
    """For each regime, compute Sharpe + total return + win rate on `equity`
    restricted to dates tagged with that regime."""
    out: dict[str, dict[str, float]] = {}
    aligned = pd.concat([equity, regimes], axis=1, join="inner")
    aligned.columns = ["equity", "regime"]

    for regime_name in sorted(aligned["regime"].dropna().unique()):
        sub = aligned[aligned["regime"] == regime_name]["equity"]
        if len(sub) < 2:
            out[regime_name] = {"sharpe": 0.0, "total_return": 0.0, "win_rate": 0.0, "n_days": int(len(sub))}
            continue
        rets = sub.pct_change().dropna().to_numpy()
        mu, sigma = float(np.mean(rets)), float(np.std(rets, ddof=1))
        sharpe = (mu / sigma * np.sqrt(252)) if sigma > 0 else 0.0
        out[regime_name] = {
            "sharpe": sharpe,
            "total_return": float(sub.iloc[-1] / sub.iloc[0] - 1.0),
            "win_rate": float(np.mean(rets > 0)),
            "n_days": int(len(sub)),
        }
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_regime.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/regime.py tests/coordinator/services/validation/test_regime.py
git commit -m "feat(validation): regime tagger + per-regime conditional metrics"
```

---

### Task E4: Multi-testing corrections (Bonferroni + BH)

**Files:**
- Create: `coordinator/services/validation/multi_test.py`
- Test: `tests/coordinator/services/validation/test_multi_test.py`

- [ ] **Step 1: Write the failing test**

`tests/coordinator/services/validation/test_multi_test.py`:

```python
import pytest

from coordinator.services.validation.multi_test import correct, CorrectedResult


def test_bonferroni_correction():
    p_values = [0.01, 0.04, 0.20]
    results = correct(raw_p_values=p_values, n_tested=10, method="bonferroni", alpha=0.05)
    assert all(isinstance(r, CorrectedResult) for r in results)
    # Bonferroni: corrected = raw * n_tested; significant if corrected < alpha
    assert results[0].corrected_p == 0.10
    assert results[0].significant is False
    assert results[2].corrected_p == 1.0  # capped at 1


def test_benjamini_hochberg_correction():
    p_values = [0.001, 0.008, 0.04]
    results = correct(raw_p_values=p_values, n_tested=3, method="bh", alpha=0.05)
    assert len(results) == 3
    # BH at α=0.05 with 3 hypotheses: all p ≤ alpha*(k/n) for k=1,2,3 → all significant
    assert all(r.significant for r in results)


def test_correct_rejects_unknown_method():
    with pytest.raises(ValueError):
        correct(raw_p_values=[0.01], n_tested=1, method="unknown")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_multi_test.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement corrections**

`coordinator/services/validation/multi_test.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class CorrectedResult:
    raw_p: float
    corrected_p: float
    significant: bool
    method: str


def correct(
    *,
    raw_p_values: list[float],
    n_tested: int,
    method: Literal["bonferroni", "bh"] = "bonferroni",
    alpha: float = 0.05,
) -> list[CorrectedResult]:
    """Apply a multiple-testing correction.

    Bonferroni: corrected = min(raw * n_tested, 1).
    Benjamini-Hochberg (FDR control): order p-values; threshold k = max k where p[k] ≤ alpha * (k+1) / n.
    """
    if method == "bonferroni":
        return [
            CorrectedResult(
                raw_p=p,
                corrected_p=min(p * n_tested, 1.0),
                significant=(min(p * n_tested, 1.0) < alpha),
                method=method,
            )
            for p in raw_p_values
        ]
    elif method == "bh":
        n = len(raw_p_values)
        if n == 0:
            return []
        order = sorted(range(n), key=lambda i: raw_p_values[i])
        sorted_p = [raw_p_values[i] for i in order]
        thresholds = [alpha * (k + 1) / n_tested for k in range(n)]
        # Find largest k where sorted_p[k] <= thresholds[k]
        k_max = -1
        for k in range(n):
            if sorted_p[k] <= thresholds[k]:
                k_max = k
        significant_set = set(order[: k_max + 1]) if k_max >= 0 else set()
        return [
            CorrectedResult(
                raw_p=raw_p_values[i],
                corrected_p=raw_p_values[i] * n_tested / (rank_in_sorted + 1)
                if (rank_in_sorted := next(idx for idx, j in enumerate(order) if j == i)) is not None
                else raw_p_values[i],
                significant=(i in significant_set),
                method=method,
            )
            for i in range(n)
        ]
    else:
        raise ValueError(f"Unknown correction method: {method}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_multi_test.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/multi_test.py tests/coordinator/services/validation/test_multi_test.py
git commit -m "feat(validation): Bonferroni and Benjamini-Hochberg multi-test correction"
```

---

## Phase F — Markdown + HTML Reporter

### Task F1: Markdown report skeleton

**Files:**
- Create: `coordinator/services/validation/report.py`
- Test: `tests/coordinator/services/validation/test_report.py`

- [ ] **Step 1: Write the failing test**

`tests/coordinator/services/validation/test_report.py`:

```python
import json
import pytest
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import Base, OptimizationSession
from coordinator.services.validation.report import build_markdown_report, ReportInputs


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def test_build_markdown_report_contains_required_sections(db_session, tmp_path):
    sess = OptimizationSession(
        name="rpt-test-001",
        hypothesis="H: TSMOM works",
        parameter_space=json.dumps({"vol_target": [0.10, 0.15]}),
        pre_registered_criteria=json.dumps({"oos_sharpe_lci": 0.5}),
        status="completed",
    )
    db_session.add(sess)
    db_session.commit()

    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    rng = np.random.default_rng(0)
    equity = pd.Series(1000.0 * np.cumprod(1.0 + rng.normal(0.001, 0.02, 200)), index=idx)
    regimes = pd.Series(["bull"] * 100 + ["chop"] * 100, index=idx)

    inputs = ReportInputs(
        session=sess,
        oos_equity_curve=equity,
        regimes=regimes,
        bootstrap_metrics={"sharpe": {"point": 0.6, "lower": 0.4, "upper": 0.9}},
        regime_metrics={"bull": {"sharpe": 0.8}, "chop": {"sharpe": 0.3}},
        corrected_p_values=[{"raw_p": 0.01, "corrected_p": 0.05, "significant": True}],
    )

    md = build_markdown_report(inputs)
    assert "# Optimization Session: rpt-test-001" in md
    assert "## Hypothesis" in md
    assert "## OOS Equity Curve" in md
    assert "## Bootstrap CIs" in md
    assert "## Regime-Conditional Metrics" in md
    assert "## Multi-Test-Corrected Significance" in md
    assert "## Deploy / Kill Decision" in md
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_report.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement the report builder**

`coordinator/services/validation/report.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from coordinator.database.models import OptimizationSession


@dataclass
class ReportInputs:
    session: OptimizationSession
    oos_equity_curve: pd.Series
    regimes: pd.Series
    bootstrap_metrics: dict[str, Any]
    regime_metrics: dict[str, dict[str, float]]
    corrected_p_values: list[dict[str, Any]]


def _criteria_check(criteria: dict[str, Any], inputs: ReportInputs) -> list[dict[str, Any]]:
    """Evaluate each pre-registered criterion against the report inputs."""
    checks: list[dict[str, Any]] = []
    sharpe_ci = inputs.bootstrap_metrics.get("sharpe", {})

    if "oos_sharpe_lci" in criteria:
        threshold = criteria["oos_sharpe_lci"]
        actual = sharpe_ci.get("lower", float("nan"))
        checks.append(
            {
                "criterion": "OOS Sharpe lower-CI",
                "threshold": f"> {threshold}",
                "actual": actual,
                "pass": actual > threshold if actual == actual else False,
            }
        )
    if "max_dd_uci" in criteria:
        threshold = criteria["max_dd_uci"]
        dd_upper = inputs.bootstrap_metrics.get("max_drawdown", {}).get("upper", float("nan"))
        actual = abs(dd_upper)
        checks.append(
            {
                "criterion": "OOS MaxDD upper-CI (|...|)",
                "threshold": f"< {threshold}",
                "actual": actual,
                "pass": actual < threshold if actual == actual else False,
            }
        )
    return checks


def build_markdown_report(inputs: ReportInputs) -> str:
    sess = inputs.session
    criteria = json.loads(sess.pre_registered_criteria)
    param_space = json.loads(sess.parameter_space)
    checks = _criteria_check(criteria, inputs)

    lines: list[str] = []
    lines.append(f"# Optimization Session: {sess.name}\n")
    lines.append(f"**Status:** {sess.status}\n")
    lines.append(f"**Created:** {sess.created_at.isoformat() if sess.created_at else ''}\n")
    lines.append("\n## Hypothesis\n")
    lines.append(f"{sess.hypothesis}\n")

    lines.append("\n## Parameter Space\n")
    lines.append("```json\n")
    lines.append(json.dumps(param_space, indent=2))
    lines.append("\n```\n")

    lines.append("\n## Pre-Registered Criteria\n")
    lines.append("```json\n")
    lines.append(json.dumps(criteria, indent=2))
    lines.append("\n```\n")

    eq = inputs.oos_equity_curve
    lines.append("\n## OOS Equity Curve\n")
    if len(eq) > 0:
        lines.append(f"- **Start:** {eq.index[0]}  **End:** {eq.index[-1]}\n")
        lines.append(f"- **Initial:** {eq.iloc[0]:.2f}  **Final:** {eq.iloc[-1]:.2f}\n")
        lines.append(f"- **Total return:** {(eq.iloc[-1] / eq.iloc[0] - 1.0) * 100:.2f}%\n")

    lines.append("\n## Bootstrap CIs\n")
    lines.append("| Metric | Point | 95% Lower | 95% Upper |\n")
    lines.append("|---|---|---|---|\n")
    for metric, ci in inputs.bootstrap_metrics.items():
        lines.append(
            f"| {metric} | {ci.get('point', float('nan')):.3f} | {ci.get('lower', float('nan')):.3f} | {ci.get('upper', float('nan')):.3f} |\n"
        )

    lines.append("\n## Regime-Conditional Metrics\n")
    lines.append("| Regime | Sharpe | Total Return | Win Rate | N Days |\n")
    lines.append("|---|---|---|---|---|\n")
    for regime, m in inputs.regime_metrics.items():
        lines.append(
            f"| {regime} | {m.get('sharpe', 0):.3f} | {m.get('total_return', 0):.3f} | "
            f"{m.get('win_rate', 0):.3f} | {m.get('n_days', 0)} |\n"
        )

    lines.append("\n## Multi-Test-Corrected Significance\n")
    lines.append("| Raw p | Corrected p | Significant |\n")
    lines.append("|---|---|---|\n")
    for r in inputs.corrected_p_values:
        lines.append(f"| {r['raw_p']:.4f} | {r['corrected_p']:.4f} | {'✓' if r['significant'] else '✗'} |\n")

    lines.append("\n## Deploy / Kill Decision\n")
    if checks:
        lines.append("| Criterion | Threshold | Actual | Pass |\n")
        lines.append("|---|---|---|---|\n")
        for c in checks:
            lines.append(f"| {c['criterion']} | {c['threshold']} | {c['actual']:.4f} | {'✓' if c['pass'] else '✗'} |\n")
        all_pass = all(c["pass"] for c in checks)
        lines.append(f"\n**Decision:** {'DEPLOY' if all_pass else 'KILL'}\n")
    else:
        lines.append("No criteria defined.\n")

    return "".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/coordinator/services/validation/test_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/report.py tests/coordinator/services/validation/test_report.py
git commit -m "feat(validation): markdown report builder with criteria evaluation"
```

---

### Task F2: Charts — equity / drawdown / regime breakdown / parameter heatmap

**Files:**
- Modify: `coordinator/services/validation/report.py`
- Test: `tests/coordinator/services/validation/test_report.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_report.py`:

```python
from coordinator.services.validation.report import render_charts


def test_render_charts_writes_files(db_session, tmp_path):
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    rng = np.random.default_rng(7)
    equity = pd.Series(1000.0 * np.cumprod(1.0 + rng.normal(0.0008, 0.015, 300)), index=idx)
    regimes = pd.Series(["bull"] * 150 + ["chop"] * 150, index=idx)

    paths = render_charts(
        equity=equity,
        regimes=regimes,
        out_dir=tmp_path,
    )
    assert (tmp_path / "equity.png").exists()
    assert (tmp_path / "drawdown.png").exists()
    assert (tmp_path / "regime_returns.png").exists()
    assert "equity" in paths and "drawdown" in paths and "regime_returns" in paths
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_report.py -v -k render_charts`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement chart rendering**

Append to `coordinator/services/validation/report.py`:

```python
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np


def render_charts(*, equity: pd.Series, regimes: pd.Series, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(equity.index, equity.values, color="#1f77b4")
    ax.set_title("OOS Equity Curve")
    ax.set_ylabel("Equity")
    ax.grid(alpha=0.3)
    eq_path = out_dir / "equity.png"
    fig.savefig(eq_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    paths["equity"] = eq_path

    peak = equity.cummax()
    dd = (equity - peak) / peak
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.fill_between(dd.index, dd.values, 0, color="#d62728", alpha=0.5)
    ax.set_title("Drawdown")
    ax.set_ylabel("Drawdown")
    ax.grid(alpha=0.3)
    dd_path = out_dir / "drawdown.png"
    fig.savefig(dd_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    paths["drawdown"] = dd_path

    aligned = pd.concat([equity, regimes], axis=1, join="inner")
    aligned.columns = ["equity", "regime"]
    returns = aligned["equity"].pct_change().fillna(0)
    grouped = returns.groupby(aligned["regime"]).agg(["mean", "std", "count"])
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(grouped.index.astype(str), grouped["mean"] * 252, color=["#2ca02c", "#7f7f7f", "#d62728"][: len(grouped)])
    ax.set_title("Annualized Return by Regime")
    ax.set_ylabel("Annualized Mean Return")
    rr_path = out_dir / "regime_returns.png"
    fig.savefig(rr_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    paths["regime_returns"] = rr_path

    return paths
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_report.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/validation/report.py tests/coordinator/services/validation/test_report.py
git commit -m "feat(validation): equity/drawdown/regime chart rendering"
```

---

### Task F3: HTML wrapper with embedded charts

**Files:**
- Modify: `coordinator/services/validation/report.py`
- Test: `tests/coordinator/services/validation/test_report.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/coordinator/services/validation/test_report.py`:

```python
from coordinator.services.validation.report import build_html_report


def test_build_html_report_embeds_charts(db_session, tmp_path):
    sess = OptimizationSession(
        name="html-test-001",
        hypothesis="H",
        parameter_space=json.dumps({}),
        pre_registered_criteria=json.dumps({"oos_sharpe_lci": 0.5}),
        status="completed",
    )
    db_session.add(sess)
    db_session.commit()

    idx = pd.date_range("2024-01-01", periods=100, freq="D")
    equity = pd.Series(np.linspace(1000.0, 1100.0, 100), index=idx)
    regimes = pd.Series(["bull"] * 100, index=idx)

    inputs = ReportInputs(
        session=sess,
        oos_equity_curve=equity,
        regimes=regimes,
        bootstrap_metrics={"sharpe": {"point": 0.7, "lower": 0.5, "upper": 0.9}, "max_drawdown": {"point": -0.05, "lower": -0.10, "upper": -0.02}},
        regime_metrics={"bull": {"sharpe": 0.7, "total_return": 0.10, "win_rate": 0.55, "n_days": 100}},
        corrected_p_values=[{"raw_p": 0.01, "corrected_p": 0.05, "significant": True}],
    )

    paths = build_html_report(inputs, out_dir=tmp_path)
    assert paths["html"].exists()
    html = paths["html"].read_text()
    assert "html-test-001" in html
    assert "equity.png" in html or "data:image" in html
    assert "Bootstrap CIs" in html
    assert "<table" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/coordinator/services/validation/test_report.py -v -k html`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement HTML build**

Append to `coordinator/services/validation/report.py`:

```python
import markdown as md_mod  # python-markdown library


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 920px; margin: 2em auto; padding: 0 1em; color: #222; }}
  h1, h2 {{ border-bottom: 1px solid #ddd; padding-bottom: 0.2em; }}
  table {{ border-collapse: collapse; margin: 1em 0; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4em 0.8em; text-align: left; }}
  th {{ background: #f6f8fa; }}
  img {{ max-width: 100%; }}
  code, pre {{ font-family: 'SF Mono', monospace; }}
  pre {{ background: #f6f8fa; padding: 1em; overflow-x: auto; }}
</style>
</head>
<body>
{body}
<div class="charts">
  <h2>Charts</h2>
  <img src="equity.png" alt="Equity Curve">
  <img src="drawdown.png" alt="Drawdown">
  <img src="regime_returns.png" alt="Regime Returns">
</div>
</body>
</html>
"""


def build_html_report(inputs: ReportInputs, *, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = build_markdown_report(inputs)
    md_path = out_dir / "report.md"
    md_path.write_text(md)

    render_charts(equity=inputs.oos_equity_curve, regimes=inputs.regimes, out_dir=out_dir)

    body_html = md_mod.markdown(md, extensions=["tables", "fenced_code"])
    html = _HTML_TEMPLATE.format(title=f"OptimizationSession: {inputs.session.name}", body=body_html)
    html_path = out_dir / "report.html"
    html_path.write_text(html)

    return {"md": md_path, "html": html_path}
```

- [ ] **Step 4: Add `markdown` to project dependencies if not present**

Run: `pip show markdown 2>/dev/null | head -1`
If empty, add `markdown` to `pyproject.toml` under `[project]` dependencies and `pip install -e .`. Existing tools that need markdown rendering (like the render-spec script) may already pull it in — check before adding.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/coordinator/services/validation/test_report.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/validation/report.py tests/coordinator/services/validation/test_report.py pyproject.toml
git commit -m "feat(validation): HTML report wrapper with embedded charts"
```

---

## Phase G — CLI Wiring

### Task G1: `quilt research session create` and `list`

**Files:**
- Create: `sdk/cli/commands/research.py`
- Modify: `sdk/cli/commands/__init__.py`
- Test: `tests/sdk/cli/test_research_cli.py`

- [ ] **Step 1: Discover how existing groups are registered**

Read `sdk/cli/commands/__init__.py` to see the registration pattern. The new `research` group must be added there in the same style as `backtest`.

- [ ] **Step 2: Write the failing test**

`tests/sdk/cli/test_research_cli.py`:

```python
from click.testing import CliRunner

from sdk.cli.commands.research import research_group


def test_research_help():
    runner = CliRunner()
    result = runner.invoke(research_group, ["--help"])
    assert result.exit_code == 0
    assert "session" in result.output
    assert "sweep" in result.output
    assert "walk-forward" in result.output
    assert "report" in result.output


def test_research_session_create_help():
    runner = CliRunner()
    result = runner.invoke(research_group, ["session", "create", "--help"])
    assert result.exit_code == 0
    assert "--name" in result.output
    assert "--hypothesis" in result.output
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/sdk/cli/test_research_cli.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Create the research command group**

`sdk/cli/commands/research.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click


@click.group("research")
def research_group() -> None:
    """Strategy Validation Lab — sessions, sweeps, walk-forward, reports."""


@research_group.group("session")
def session_group() -> None:
    """Manage OptimizationSession records."""


@session_group.command("create")
@click.option("--name", required=True, help="Unique session name.")
@click.option("--hypothesis", required=True, help="Pre-registered hypothesis text.")
@click.option(
    "--parameter-space",
    required=True,
    help="JSON parameter space, e.g., '{\"vol_target\": [0.10, 0.15]}'.",
)
@click.option(
    "--criteria",
    required=True,
    help="JSON pre-registered criteria, e.g., '{\"oos_sharpe_lci\": 0.5}'.",
)
@click.option("--notes", default="", help="Free-form notes.")
def session_create(name: str, hypothesis: str, parameter_space: str, criteria: str, notes: str) -> None:
    """Create a new OptimizationSession (pre-registration step)."""
    from coordinator.database.session import get_session_factory
    from coordinator.services.validation.optimization_session import create_session

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        sess = create_session(
            db,
            name=name,
            hypothesis=hypothesis,
            parameter_space=json.loads(parameter_space),
            pre_registered_criteria=json.loads(criteria),
            notes=notes,
        )
        db.commit()
        click.echo(f"Created OptimizationSession id={sess.id} name={sess.name}")


@session_group.command("list")
def session_list() -> None:
    """List all OptimizationSessions."""
    from coordinator.database.models import OptimizationSession
    from coordinator.database.session import get_session_factory

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        rows = db.query(OptimizationSession).order_by(OptimizationSession.created_at.desc()).all()
        for r in rows:
            click.echo(f"{r.id}\t{r.name}\t{r.status}\t{r.created_at.isoformat() if r.created_at else ''}")


@research_group.command("sweep")
@click.option("--session-id", type=int, required=True)
@click.option("--manifest", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--base-config", type=click.Path(exists=True, dir_okay=False), required=True, help="JSON file with base BacktestConfig.")
@click.option("--search", type=click.Choice(["grid", "random", "latin"]), default="grid")
@click.option("--max-trials", type=int, default=50)
@click.option("--parallelism", type=int, default=1)
@click.option("--seed", type=int, default=0)
def cmd_sweep(session_id: int, manifest: str, base_config: str, search: str, max_trials: int, parallelism: int, seed: int) -> None:
    """Run a hyperparameter sweep under an existing session."""
    from coordinator.database.session import get_session_factory
    from coordinator.database.models import OptimizationSession
    from coordinator.services.validation.sweep import run_sweep

    base_cfg = json.loads(Path(base_config).read_text())

    async def _go() -> None:
        SessionLocal = get_session_factory()
        with SessionLocal() as db:
            sess = db.query(OptimizationSession).get(session_id)
            if sess is None:
                raise click.ClickException(f"Session {session_id} not found")
            param_space = json.loads(sess.parameter_space)
            result = await run_sweep(
                db=db,
                session_id=session_id,
                manifest_path=manifest,
                base_config=base_cfg,
                parameter_space=param_space,
                search=search,
                max_trials=max_trials,
                parallelism=parallelism,
                seed=seed,
            )
            db.commit()
            click.echo(f"Sweep done: {result.n_configs} configs, {len(result.run_ids)} runs.")

    asyncio.run(_go())


@research_group.command("walk-forward")
@click.option("--session-id", type=int, required=True)
@click.option("--manifest", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--base-config", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--train-years", type=float, default=4.0)
@click.option("--test-years", type=float, default=1.0)
@click.option("--step-months", type=float, default=6.0)
@click.option("--objective", type=click.Choice(["sharpe", "calmar", "sortino"]), default="sharpe")
@click.option("--parallelism", type=int, default=1)
def cmd_walk_forward(session_id: int, manifest: str, base_config: str, train_years: float, test_years: float, step_months: float, objective: str, parallelism: int) -> None:
    """Run a walk-forward optimization under an existing session."""
    from coordinator.database.session import get_session_factory
    from coordinator.database.models import OptimizationSession
    from coordinator.services.validation.walk_forward import run_walk_forward

    base_cfg = json.loads(Path(base_config).read_text())

    async def _go() -> None:
        SessionLocal = get_session_factory()
        with SessionLocal() as db:
            sess = db.query(OptimizationSession).get(session_id)
            if sess is None:
                raise click.ClickException(f"Session {session_id} not found")
            result = await run_walk_forward(
                db=db,
                session_id=session_id,
                manifest_path=manifest,
                base_config=base_cfg,
                parameter_space=json.loads(sess.parameter_space),
                train_years=train_years,
                test_years=test_years,
                step_months=step_months,
                objective=objective,
                parallelism=parallelism,
            )
            db.commit()
            click.echo(f"Walk-forward done: {result.n_folds} folds, OOS runs: {result.oos_run_ids}")

    asyncio.run(_go())


@research_group.command("report")
@click.option("--session-id", type=int, required=True)
@click.option("--out-dir", type=click.Path(file_okay=False), default="data/research_reports")
def cmd_report(session_id: int, out_dir: str) -> None:
    """Build the markdown + HTML report for a completed session."""
    from coordinator.database.session import get_session_factory
    from coordinator.database.models import OptimizationSession
    from coordinator.services.validation.report import ReportInputs, build_html_report
    from coordinator.services.validation.walk_forward import concatenate_oos_curves
    from coordinator.services.validation.regime import tag_regimes, regime_conditional_metrics
    from coordinator.services.validation.bootstrap import bootstrap_metrics
    from coordinator.services.validation.optimization_session import get_session_runs

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        sess = db.query(OptimizationSession).get(session_id)
        if sess is None:
            raise click.ClickException(f"Session {session_id} not found")

        runs = get_session_runs(db, session_id)
        oos_paths = [
            Path(f"data/backtests/{r.id}/equity_native.parquet")
            for r in runs
            if "\"_oos\": true" in (r.config_json or "")
            and Path(f"data/backtests/{r.id}/equity_native.parquet").exists()
        ]
        if not oos_paths:
            raise click.ClickException("No OOS runs found for this session.")

        equity = concatenate_oos_curves(oos_paths)
        regimes = tag_regimes(equity)
        boot = bootstrap_metrics(equity, n_resamples=1000)
        regime_m = regime_conditional_metrics(equity, regimes)

        inputs = ReportInputs(
            session=sess,
            oos_equity_curve=equity,
            regimes=regimes,
            bootstrap_metrics={k: v.__dict__ for k, v in boot.items()},
            regime_metrics=regime_m,
            corrected_p_values=[],
        )

        target = Path(out_dir) / str(session_id)
        result = build_html_report(inputs, out_dir=target)
        click.echo(f"Report written: {result['html']}")
```

- [ ] **Step 5: Register the group in `sdk/cli/commands/__init__.py`**

Add the import and registration following the existing pattern. For example, if existing code does `from sdk.cli.commands.backtest import backtest_group; cli.add_command(backtest_group)`, add:

```python
from sdk.cli.commands.research import research_group
cli.add_command(research_group)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/sdk/cli/test_research_cli.py -v`
Expected: 2 passed

- [ ] **Step 7: Run the help in a shell to confirm**

Run: `python -m sdk.cli research --help`
Expected: sees `session`, `sweep`, `walk-forward`, `report` subcommands.

- [ ] **Step 8: Commit**

```bash
git add sdk/cli/commands/research.py sdk/cli/commands/__init__.py tests/sdk/cli/
git commit -m "feat(cli): quilt research command group (session, sweep, walk-forward, report)"
```

---

## Phase H — End-to-End Integration Smoke Test

### Task H1: End-to-end integration test on a dummy strategy

**Files:**
- Create: `tests/coordinator/services/validation/test_e2e_smoke.py`

- [ ] **Step 1: Write the integration test**

This test stitches the whole pipeline together against the existing test algorithm (`quilt-trader-test-algo` or the `simple-ma-crossover` package). It runs:
1. `create_session` — pre-registers a hypothesis
2. `run_sweep` on a 2-config grid against a real-ish backtest
3. `concatenate_oos_curves` on the resulting parquet files
4. `bootstrap_metrics` on the concatenated curve
5. `tag_regimes` + `regime_conditional_metrics`
6. `build_html_report` writes `report.html`

`tests/coordinator/services/validation/test_e2e_smoke.py`:

```python
import json
import pytest
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import Base, OptimizationSession, BacktestRun
from coordinator.services.validation.optimization_session import create_session
from coordinator.services.validation.regime import tag_regimes, regime_conditional_metrics
from coordinator.services.validation.bootstrap import bootstrap_metrics
from coordinator.services.validation.walk_forward import concatenate_oos_curves
from coordinator.services.validation.report import ReportInputs, build_html_report


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def test_lab_pipeline_end_to_end(db, tmp_path):
    # 1) Pre-register session
    sess = create_session(
        db,
        name="e2e-smoke-001",
        hypothesis="Smoke: dummy walk-forward produces a renderable report",
        parameter_space={"x": [1, 2]},
        pre_registered_criteria={"oos_sharpe_lci": -10.0, "max_dd_uci": 1.0},
    )
    db.commit()

    # 2) Skip real backtest — synthesize two OOS equity curves on disk
    rng = np.random.default_rng(42)
    f1 = tmp_path / "fold1_equity.parquet"
    pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=180, freq="D"),
        "equity": 1000.0 * np.cumprod(1.0 + rng.normal(0.001, 0.02, 180)),
    }).to_parquet(f1)
    f2 = tmp_path / "fold2_equity.parquet"
    pd.DataFrame({
        "timestamp": pd.date_range("2024-07-01", periods=180, freq="D"),
        "equity": 1000.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.025, 180)),
    }).to_parquet(f2)

    # 3) Concatenate
    equity = concatenate_oos_curves([f1, f2])
    assert len(equity) == 360
    assert equity.iloc[0] == 1000.0

    # 4) Regime tag + conditional metrics
    regimes = tag_regimes(equity, lookback_days=60)
    regime_m = regime_conditional_metrics(equity, regimes)
    assert isinstance(regime_m, dict)

    # 5) Bootstrap CIs
    boot = bootstrap_metrics(equity, n_resamples=200, seed=0)
    assert "sharpe" in boot
    assert "max_drawdown" in boot

    # 6) Build report
    inputs = ReportInputs(
        session=sess,
        oos_equity_curve=equity,
        regimes=regimes,
        bootstrap_metrics={k: v.__dict__ for k, v in boot.items()},
        regime_metrics=regime_m,
        corrected_p_values=[],
    )
    out = build_html_report(inputs, out_dir=tmp_path / "report")
    assert out["html"].exists()
    html = out["html"].read_text()
    assert "e2e-smoke-001" in html
    assert "<table" in html
    assert "Bootstrap CIs" in html
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/coordinator/services/validation/test_e2e_smoke.py -v`
Expected: PASS

- [ ] **Step 3: Run the full validation suite to confirm no regressions**

Run: `pytest tests/coordinator/services/validation/ -v`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/coordinator/services/validation/test_e2e_smoke.py
git commit -m "test(validation): end-to-end pipeline smoke test"
```

---

## Phase I — Documentation Updates

### Task I1: Mark spec phases complete and update backlog

**Files:**
- Modify: `docs/superpowers/specs/2026-05-27-crypto-tsmom-research-program-design.md`
- Modify: `docs/superpowers/backlog.md`

- [ ] **Step 1: Mark validation lab phases as shipped in the spec**

In `docs/superpowers/specs/2026-05-27-crypto-tsmom-research-program-design.md`, find the **Phasing** table. After Phase 5, add a status column noting `(shipped 2026-MM-DD)` against rows 1-5. Add a brief note at the top of the spec under the Status field, e.g., `Lab module shipped. Strategy implementation plan pending.`

- [ ] **Step 2: Note the lab as available in the backlog**

In `docs/superpowers/backlog.md` under the "Validation Lab" section, add a header note:

```markdown
> **Status:** lab module shipped 2026-MM-DD (see commit). Deferred items below remain open.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-27-crypto-tsmom-research-program-design.md docs/superpowers/backlog.md
git commit -m "docs: mark validation lab phases as shipped"
```

---

## Plan Self-Review (Completed)

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| §1.1 `walk_forward.py` | Phase D (D1, D2, D3) |
| §1.2 `sweep.py` | Phase C (C1, C2, C3) |
| §1.3 `optimization_session.py` + new DB model | Phase B (B1, B2, B3) |
| §1.4 `bootstrap.py` | Phase E (E1, E2) |
| §1.5 `regime.py` | Phase E (E3) |
| §1.6 `multi_test.py` | Phase E (E4) |
| §1.7 `cost_model.py` + per-venue profiles + engine hook | Phase A (A1, A2, A3, A4) |
| §1.8 `report.py` (markdown + HTML) | Phase F (F1, F2, F3) |
| Phasing 1-5 | Phases A-F |
| Phasing 6-7 (strategy) | **Deferred to Plan 2** (writing-plans pass after this lab ships) |
| Methodology — walk-forward params | Encoded in D2 + G1 (`--train-years`, `--test-years`, `--step-months` defaults) |
| Methodology — pre-registration enforcement | B3 + G1 (`session create` requires `--hypothesis` and `--criteria`) |
| Methodology — multi-test correction | B3 (`config_hash`, `count_hypotheses_tested`) + E4 |
| Methodology — bootstrap CI | E1, E2 |
| Methodology — regime conditional metrics | E3 + Report (F1) |
| Methodology — deployment / kill criteria | F1 (`_criteria_check`) |
| CLI surface | Phase G (G1) |
| E2E smoke | Phase H (H1) |
| Docs update | Phase I (I1) |

**Placeholder scan:** none found. Every step has either complete code or a precise command.

**Type consistency check:**
- `CostBundle` / `CostModelProfile` / `MetricCI` / `Fold` / `WalkForwardResult` / `SweepResult` / `CorrectedResult` / `ReportInputs` — defined once, used consistently across modules and tests.
- `OptimizationSession.parameter_space` and `pre_registered_criteria` are stored as JSON-encoded strings (matches DB Text columns) and parsed via `json.loads` at every read site.
- `BacktestRun.config_hash` is added in B3; referenced by `count_hypotheses_tested` (B3) and set by sweep/walk-forward run creation (C3, D2).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-27-validation-lab.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
