# Congress Copy-Trading Algorithm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `congress-copytrader` algorithm package that mirrors a configurable whitelist of Congress members, weighting each name by combined share-of-portfolio conviction, rebalanced daily.

**Architecture:** A single-file algorithm package (`data/packages/congress-copytrader/algorithm.py`). The worker's package loader (`worker/package_cache.py::load_algorithm_class`) loads the entry file via `spec_from_file_location` and deliberately does **not** add the package dir to `sys.path` — so sibling-module imports do not work and there are no multi-file packages in this repo. Therefore all logic lives in one file as module-level **pure functions** (parse, weight, order) plus the `CongressCopyTrader(QuiltAlgorithm)` orchestration class. The pure functions are unit-tested by loading the file via `importlib` (they only depend on `pandas` and `sdk.signals`, both importable from the repo root). `on_tick` is a stateless pure recomputation over the bitemporal `fmp.house_disclosures` / `fmp.senate_disclosures` datasets read through `ctx.dataset()`.

**Tech Stack:** Python 3.12, pandas, the Quilt SDK (`sdk.algorithm.QuiltAlgorithm`, `sdk.signals`), pytest. Spec: `docs/superpowers/specs/2026-06-19-congress-copytrading-design.md`.

**Conventions:**
- Run Python/pytest with the repo venv: `.venv/bin/python -m pytest ...` from `/home/jkern/dev/quilt-trader`.
- The manifest `data:` block only accepts `type ∈ {scraper, csv, json, parquet}` (`sdk/manifest.py:211`). Datasets resolve via the coordinator registry independent of the manifest, so the manifest has **no** `data:` block — `ctx.dataset("fmp.house_disclosures")` is called directly.

---

## File Map

| File | Responsibility |
|------|----------------|
| `data/packages/congress-copytrader/quilt.yaml` | Manifest: metadata, `bar:1day` trigger, config params, SPY anchor asset. |
| `data/packages/congress-copytrader/algorithm.py` | Pure functions (`parse_amount_midpoint`, `signed_amount`, `parse_members`, `compute_target_weights`, `compute_orders`, `OrderDelta`) + `CongressCopyTrader` class. |
| `data/packages/congress-copytrader/requirements.txt` | Empty (pandas already available). |
| `tests/packages/congress_copytrader/conftest.py` | Loads `algorithm.py` by file path via `importlib`; exposes an `algo_mod` fixture. |
| `tests/packages/congress_copytrader/test_parsing.py` | Tests for `parse_amount_midpoint`, `signed_amount`, `parse_members`. |
| `tests/packages/congress_copytrader/test_weights.py` | Tests for `compute_target_weights` (normalization, floor, cap). |
| `tests/packages/congress_copytrader/test_orders.py` | Tests for `compute_orders` (sizing, churn band, exit). |
| `tests/packages/congress_copytrader/test_algorithm.py` | Integration test: `CongressCopyTrader.on_tick` against a fake `TickContext`. |
| `tests/packages/congress_copytrader/test_manifest.py` | Manifest parses and validates clean. |

---

### Task 1: Scaffold the package and manifest

**Files:**
- Create: `data/packages/congress-copytrader/quilt.yaml`
- Create: `data/packages/congress-copytrader/requirements.txt`
- Create: `data/packages/congress-copytrader/algorithm.py` (minimal class stub so the manifest validates)
- Create: `tests/packages/congress_copytrader/test_manifest.py`

- [ ] **Step 1: Write the manifest**

Create `data/packages/congress-copytrader/quilt.yaml`:

```yaml
name: congress-copytrader
type: algorithm
version: 1.0.0
description: Mirrors whitelisted members of Congress, weighting each name by combined
  share-of-portfolio conviction, rebalanced daily.
entry_point: algorithm.py
class_name: CongressCopyTrader
trigger: bar:1day
requirements:
  asset_types: [equities]
config:
  parameters:
    - name: members
      type: string
      default: ""
    - name: chambers
      type: string
      default: both
    - name: history_days
      type: integer
      default: 0
    - name: min_member_history_usd
      type: integer
      default: 50000
    - name: max_weight
      type: float
      default: 0.25
    - name: min_rebalance_pct
      type: float
      default: 0.02
    - name: pct_invest
      type: float
      default: 0.95
    - name: disclosure_lag_days
      type: integer
      default: 0
assets:
  - symbol: SPY
    asset_class: equities
    timeframe: 1day
    source: polygon
```

- [ ] **Step 2: Write the empty requirements file**

Create `data/packages/congress-copytrader/requirements.txt` (empty file, zero bytes).

- [ ] **Step 3: Write a minimal algorithm stub**

Create `data/packages/congress-copytrader/algorithm.py`:

```python
from __future__ import annotations

from typing import Optional

from sdk.algorithm import QuiltAlgorithm
from sdk.signals import Signal


class CongressCopyTrader(QuiltAlgorithm):
    def on_start(self, config: dict, restored_state: Optional[dict]) -> None:
        pass

    def on_tick(self, ctx) -> list[Signal]:
        return []

    def on_stop(self) -> dict:
        return {}

    def save_state(self) -> dict:
        return {}
```

- [ ] **Step 4: Write the manifest validation test**

Create `tests/packages/congress_copytrader/test_manifest.py`:

```python
from pathlib import Path

from sdk.manifest import QuiltManifest
from sdk.validation import validate_algorithm_package

_PKG = Path(__file__).resolve().parents[3] / "data" / "packages" / "congress-copytrader"


def test_manifest_parses():
    manifest = QuiltManifest.from_file(_PKG / "quilt.yaml")
    assert manifest.name == "congress-copytrader"
    assert manifest.type == "algorithm"
    assert manifest.class_name == "CongressCopyTrader"
    assert manifest.trigger == "bar:1day"


def test_package_validates_clean():
    errors = validate_algorithm_package(_PKG)
    assert errors == [], f"unexpected validation errors: {errors}"
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_manifest.py -v`
Expected: PASS (2 passed). If `validate_algorithm_package` reports an error, fix the manifest/stub until clean.

- [ ] **Step 6: Commit**

```bash
git add data/packages/congress-copytrader/quilt.yaml \
        data/packages/congress-copytrader/requirements.txt \
        data/packages/congress-copytrader/algorithm.py \
        tests/packages/congress_copytrader/test_manifest.py
git commit -m "feat(congress-copytrader): scaffold package + manifest"
```

---

### Task 2: Amount and type parsing helpers

**Files:**
- Modify: `data/packages/congress-copytrader/algorithm.py`
- Create: `tests/packages/congress_copytrader/conftest.py`
- Create: `tests/packages/congress_copytrader/test_parsing.py`

- [ ] **Step 1: Write the conftest that loads the algorithm module by path**

Create `tests/packages/congress_copytrader/conftest.py`:

```python
import importlib.util
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[3] / "data" / "packages" / "congress-copytrader"


@pytest.fixture
def algo_mod():
    """Load the package's algorithm.py by file path (the package dir is not on sys.path)."""
    path = _PKG / "algorithm.py"
    spec = importlib.util.spec_from_file_location("congress_copytrader_algorithm", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 2: Write the failing parsing tests**

Create `tests/packages/congress_copytrader/test_parsing.py`:

```python
import pytest


def test_parse_amount_midpoint_two_bounds(algo_mod):
    assert algo_mod.parse_amount_midpoint("$1,001 - $15,000") == 8000.5
    assert algo_mod.parse_amount_midpoint("$15,001 - $50,000") == 32500.5
    assert algo_mod.parse_amount_midpoint("$1,000,001 - $5,000,000") == 3000000.5


def test_parse_amount_midpoint_single_bound(algo_mod):
    assert algo_mod.parse_amount_midpoint("Over $50,000,000") == 50000000.0
    assert algo_mod.parse_amount_midpoint("$50,000,000+") == 50000000.0


def test_parse_amount_midpoint_garbage(algo_mod):
    assert algo_mod.parse_amount_midpoint("") is None
    assert algo_mod.parse_amount_midpoint("unknown") is None
    assert algo_mod.parse_amount_midpoint(None) is None


def test_signed_amount_buy_and_sell(algo_mod):
    assert algo_mod.signed_amount("Purchase", 8000.5) == 8000.5
    assert algo_mod.signed_amount("Sale (Partial)", 8000.5) == -8000.5
    assert algo_mod.signed_amount("Sale (Full)", 100.0) == -100.0


def test_signed_amount_unknown_type(algo_mod):
    assert algo_mod.signed_amount("Exchange", 100.0) is None
    assert algo_mod.signed_amount(None, 100.0) is None


def test_parse_members_forms(algo_mod):
    assert algo_mod.parse_members("") == []
    assert algo_mod.parse_members("Pelosi") == [("pelosi", None)]
    assert algo_mod.parse_members("Pelosi, Nancy") == [("pelosi", "nancy")]
    assert algo_mod.parse_members("Pelosi, Nancy; Tuberville") == [
        ("pelosi", "nancy"),
        ("tuberville", None),
    ]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_parsing.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'parse_amount_midpoint'`.

- [ ] **Step 4: Implement the parsing helpers**

Replace the entire contents of `data/packages/congress-copytrader/algorithm.py` with:

```python
from __future__ import annotations

import re
from typing import Optional

from sdk.algorithm import QuiltAlgorithm
from sdk.signals import Signal

_MONEY_RE = re.compile(r"\$\s*([\d,]+)")
_BUY_TOKENS = ("purchase", "buy")
_SELL_TOKENS = ("sale", "sell")


def parse_amount_midpoint(amount: object) -> Optional[float]:
    """Parse an FMP disclosure amount range string to its dollar midpoint.

    "$1,001 - $15,000" -> 8000.5; "Over $50,000,000" -> 50000000.0; junk -> None.
    """
    if not isinstance(amount, str):
        return None
    nums = [int(m.replace(",", "")) for m in _MONEY_RE.findall(amount)]
    if not nums:
        return None
    if len(nums) == 1:
        return float(nums[0])
    return (nums[0] + nums[1]) / 2.0


def signed_amount(transaction_type: object, midpoint: float) -> Optional[float]:
    """+midpoint for purchases, -midpoint for sales, None for unknown types."""
    if not isinstance(transaction_type, str):
        return None
    t = transaction_type.lower()
    if any(tok in t for tok in _BUY_TOKENS):
        return midpoint
    if any(tok in t for tok in _SELL_TOKENS):
        return -midpoint
    return None


def parse_members(spec: str) -> list[tuple[str, Optional[str]]]:
    """Parse the `members` config string into [(last, first|None), ...], lowercased.

    Entries are separated by ';'. Each entry is "Last" or "Last, First".
    """
    out: list[tuple[str, Optional[str]]] = []
    if not spec:
        return out
    for entry in spec.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "," in entry:
            last, first = entry.split(",", 1)
            out.append((last.strip().lower(), first.strip().lower() or None))
        else:
            out.append((entry.lower(), None))
    return out


class CongressCopyTrader(QuiltAlgorithm):
    def on_start(self, config: dict, restored_state: Optional[dict]) -> None:
        pass

    def on_tick(self, ctx) -> list[Signal]:
        return []

    def on_stop(self) -> dict:
        return {}

    def save_state(self) -> dict:
        return {}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_parsing.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add data/packages/congress-copytrader/algorithm.py \
        tests/packages/congress_copytrader/conftest.py \
        tests/packages/congress_copytrader/test_parsing.py
git commit -m "feat(congress-copytrader): amount/type/member parsing helpers"
```

---

### Task 3: Target-weight computation

**Files:**
- Modify: `data/packages/congress-copytrader/algorithm.py`
- Create: `tests/packages/congress_copytrader/test_weights.py`

- [ ] **Step 1: Write the failing weight tests**

Create `tests/packages/congress_copytrader/test_weights.py`:

```python
import pandas as pd
import pytest


def _df(rows):
    return pd.DataFrame(rows, columns=["lastName", "firstName", "symbol", "type", "amount"])


def test_single_member_two_names_share_of_book(algo_mod):
    # Member's book = 8000.5 (AAA) + 32500.5 (BBB) = 40501. Shares: AAA ~0.1975, BBB ~0.8025.
    df = _df([
        ["Doe", "Jane", "AAA", "Purchase", "$1,001 - $15,000"],
        ["Doe", "Jane", "BBB", "Purchase", "$15,001 - $50,000"],
    ])
    w = algo_mod.compute_target_weights(df, members=[], min_member_history_usd=0, max_weight=1.0)
    assert pytest.approx(w["AAA"], rel=1e-6) == 8000.5 / 40501.0
    assert pytest.approx(w["BBB"], rel=1e-6) == 32500.5 / 40501.0
    assert pytest.approx(sum(w.values()), rel=1e-9) == 1.0


def test_combine_across_members(algo_mod):
    # Two members each fully invested in AAA -> conviction sums, normalized weight 1.0.
    df = _df([
        ["Doe", "Jane", "AAA", "Purchase", "$1,001 - $15,000"],
        ["Roe", "John", "AAA", "Purchase", "$15,001 - $50,000"],
    ])
    w = algo_mod.compute_target_weights(df, members=[], min_member_history_usd=0, max_weight=1.0)
    assert pytest.approx(w["AAA"], rel=1e-9) == 1.0


def test_sale_nets_out_and_excludes_name(algo_mod):
    # Bought then fully sold AAA -> net <= 0 -> AAA excluded; BBB is the only holding.
    df = _df([
        ["Doe", "Jane", "AAA", "Purchase", "$1,001 - $15,000"],
        ["Doe", "Jane", "AAA", "Sale", "$1,001 - $15,000"],
        ["Doe", "Jane", "BBB", "Purchase", "$15,001 - $50,000"],
    ])
    w = algo_mod.compute_target_weights(df, members=[], min_member_history_usd=0, max_weight=1.0)
    assert "AAA" not in w
    assert pytest.approx(w["BBB"], rel=1e-9) == 1.0


def test_min_member_history_floor_excludes_member(algo_mod):
    # Jane's book (8000.5) is below the 50k floor -> dropped. John's AAA remains.
    df = _df([
        ["Doe", "Jane", "AAA", "Purchase", "$1,001 - $15,000"],
        ["Roe", "John", "BBB", "Purchase", "$1,000,001 - $5,000,000"],
    ])
    w = algo_mod.compute_target_weights(df, members=[], min_member_history_usd=50000, max_weight=1.0)
    assert "AAA" not in w
    assert pytest.approx(w["BBB"], rel=1e-9) == 1.0


def test_whitelist_filters_members(algo_mod):
    df = _df([
        ["Doe", "Jane", "AAA", "Purchase", "$1,001 - $15,000"],
        ["Roe", "John", "BBB", "Purchase", "$1,001 - $15,000"],
    ])
    members = algo_mod.parse_members("Doe, Jane")
    w = algo_mod.compute_target_weights(df, members=members, min_member_history_usd=0, max_weight=1.0)
    assert set(w) == {"AAA"}


def test_max_weight_cap_redistributes(algo_mod):
    # AAA dominates raw weight (~0.999); cap 0.5 -> AAA pinned to 0.5, BBB/CCC
    # scaled up to absorb the freed 0.5 proportionally -> 0.25 each.
    df = _df([
        ["Doe", "Jane", "AAA", "Purchase", "$5,000,001 - $25,000,000"],
        ["Doe", "Jane", "BBB", "Purchase", "$1,001 - $15,000"],
        ["Doe", "Jane", "CCC", "Purchase", "$1,001 - $15,000"],
    ])
    w = algo_mod.compute_target_weights(df, members=[], min_member_history_usd=0, max_weight=0.5)
    assert pytest.approx(w["AAA"], rel=1e-6) == 0.5
    assert pytest.approx(w["BBB"], rel=1e-6) == 0.25
    assert pytest.approx(w["CCC"], rel=1e-6) == 0.25


def test_no_disclosures_returns_empty(algo_mod):
    df = _df([])
    w = algo_mod.compute_target_weights(df, members=[], min_member_history_usd=0, max_weight=1.0)
    assert w == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_weights.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'compute_target_weights'`.

- [ ] **Step 3: Implement the weight functions**

In `data/packages/congress-copytrader/algorithm.py`, add `import pandas as pd` to the imports at the top (place it after `import re`), then insert these two functions immediately **before** the `class CongressCopyTrader` line:

```python
def compute_target_weights(
    df: "pd.DataFrame",
    members: list[tuple[str, Optional[str]]],
    min_member_history_usd: float,
    max_weight: float,
) -> dict[str, float]:
    """Disclosures -> {symbol: target_weight}. Weights sum to <= 1.0.

    Per member: book = sum of signed midpoints across all their rows; a name's
    weight contribution = max(net,0)/book ("share of their disclosed portfolio").
    Members below `min_member_history_usd` are dropped. Conviction is summed across
    members per symbol, normalized, then capped at `max_weight` with redistribution.
    """
    book: dict[tuple, float] = {}
    net: dict[tuple, dict] = {}
    for row in df.itertuples(index=False):
        midpoint = parse_amount_midpoint(getattr(row, "amount", None))
        if midpoint is None:
            continue
        signed = signed_amount(getattr(row, "type", None), midpoint)
        if signed is None:
            continue
        last = (getattr(row, "lastName", "") or "")
        first = (getattr(row, "firstName", "") or "")
        if not _member_matches(last, first, members):
            continue
        symbol = (getattr(row, "symbol", "") or "").strip().upper()
        if not symbol:
            continue
        mkey = (last.lower(), first.lower())
        book[mkey] = book.get(mkey, 0.0) + signed
        net.setdefault(mkey, {})
        net[mkey][symbol] = net[mkey].get(symbol, 0.0) + signed

    conviction: dict[str, float] = {}
    for mkey, positions in net.items():
        book_m = book.get(mkey, 0.0)
        if book_m < min_member_history_usd:
            continue
        for symbol, net_amt in positions.items():
            if net_amt <= 0:
                continue
            conviction[symbol] = conviction.get(symbol, 0.0) + net_amt / book_m

    total = sum(conviction.values())
    if total <= 0:
        return {}
    weights = {s: c / total for s, c in conviction.items()}
    return _apply_max_weight(weights, max_weight)


def _member_matches(last: str, first: str, members: list[tuple[str, Optional[str]]]) -> bool:
    if not members:
        return True
    last = (last or "").lower()
    first = (first or "").lower()
    for ml, mf in members:
        if last == ml and (mf is None or first == mf):
            return True
    return False


def _apply_max_weight(weights: dict[str, float], max_weight: float) -> dict[str, float]:
    if max_weight <= 0 or max_weight >= 1:
        return weights
    weights = dict(weights)
    capped: set = set()
    for _ in range(len(weights) + 1):
        over = [s for s, w in weights.items() if s not in capped and w > max_weight + 1e-12]
        if not over:
            break
        for s in over:
            weights[s] = max_weight
            capped.add(s)
        free = [s for s in weights if s not in capped]
        free_total = sum(weights[s] for s in free)
        remaining = 1.0 - max_weight * len(capped)
        if free_total <= 0 or remaining <= 0:
            break
        scale = remaining / free_total
        for s in free:
            weights[s] *= scale
    return weights
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_weights.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add data/packages/congress-copytrader/algorithm.py \
        tests/packages/congress_copytrader/test_weights.py
git commit -m "feat(congress-copytrader): share-of-portfolio target weights"
```

---

### Task 4: Order generation

**Files:**
- Modify: `data/packages/congress-copytrader/algorithm.py`
- Create: `tests/packages/congress_copytrader/test_orders.py`

- [ ] **Step 1: Write the failing order tests**

Create `tests/packages/congress_copytrader/test_orders.py`:

```python
import pytest


def test_buy_to_reach_target(algo_mod):
    # Target 100% of $10,000 at $100 -> 100 shares, none held -> BUY 100.
    orders = algo_mod.compute_orders(
        target_weights={"AAA": 1.0}, account_value=10000.0, pct_invest=1.0,
        prices={"AAA": 100.0}, current_qty={}, min_rebalance_pct=0.0,
    )
    assert len(orders) == 1
    o = orders[0]
    assert (o.symbol, o.side, o.quantity) == ("AAA", "buy", 100)


def test_full_exit_when_name_leaves_basket(algo_mod):
    # Hold 50 AAA but it's no longer in target_weights -> SELL all 50.
    orders = algo_mod.compute_orders(
        target_weights={}, account_value=10000.0, pct_invest=1.0,
        prices={"AAA": 100.0}, current_qty={"AAA": 50}, min_rebalance_pct=0.0,
    )
    assert len(orders) == 1
    assert (orders[0].symbol, orders[0].side, orders[0].quantity) == ("AAA", "sell", 50)


def test_trim_overweight_position(algo_mod):
    # Target 50 shares, hold 80 -> SELL 30.
    orders = algo_mod.compute_orders(
        target_weights={"AAA": 0.5}, account_value=10000.0, pct_invest=1.0,
        prices={"AAA": 100.0}, current_qty={"AAA": 80}, min_rebalance_pct=0.0,
    )
    assert (orders[0].symbol, orders[0].side, orders[0].quantity) == ("AAA", "sell", 30)


def test_churn_band_suppresses_small_drift(algo_mod):
    # Target 100 shares, hold 99 -> $100 drift on $10,000 = 1% < 2% band -> no order.
    orders = algo_mod.compute_orders(
        target_weights={"AAA": 1.0}, account_value=10000.0, pct_invest=1.0,
        prices={"AAA": 100.0}, current_qty={"AAA": 99}, min_rebalance_pct=0.02,
    )
    assert orders == []


def test_skips_symbol_without_price(algo_mod):
    # No price for AAA -> cannot size -> skipped silently.
    orders = algo_mod.compute_orders(
        target_weights={"AAA": 1.0}, account_value=10000.0, pct_invest=1.0,
        prices={}, current_qty={}, min_rebalance_pct=0.0,
    )
    assert orders == []


def test_pct_invest_leaves_cash_buffer(algo_mod):
    # pct_invest 0.9 -> deploy $9,000 at $100 -> 90 shares.
    orders = algo_mod.compute_orders(
        target_weights={"AAA": 1.0}, account_value=10000.0, pct_invest=0.9,
        prices={"AAA": 100.0}, current_qty={}, min_rebalance_pct=0.0,
    )
    assert (orders[0].side, orders[0].quantity) == ("buy", 90)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_orders.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'compute_orders'`.

- [ ] **Step 3: Implement OrderDelta and compute_orders**

In `data/packages/congress-copytrader/algorithm.py`, add `from dataclasses import dataclass` to the imports (after `from __future__ import annotations`), then insert this immediately **before** the `class CongressCopyTrader` line:

```python
@dataclass
class OrderDelta:
    symbol: str
    side: str        # "buy" | "sell"
    quantity: int
    target_weight: float


def compute_orders(
    target_weights: dict[str, float],
    account_value: float,
    pct_invest: float,
    prices: dict[str, float],
    current_qty: dict[str, float],
    min_rebalance_pct: float,
) -> list[OrderDelta]:
    """Diff target dollar positions against current holdings into buy/sell deltas.

    Symbols absent from `prices` are skipped (caller could not resolve a price).
    A symbol in `current_qty` but not in `target_weights` targets 0 shares -> full exit.
    Trades whose dollar drift is below `min_rebalance_pct` of equity are suppressed.
    """
    orders: list[OrderDelta] = []
    symbols = set(target_weights) | set(current_qty)
    for symbol in sorted(symbols):
        px = prices.get(symbol)
        if px is None or px <= 0:
            continue
        target_dollars = target_weights.get(symbol, 0.0) * account_value * pct_invest
        target_shares = int(target_dollars // px)
        cur = int(current_qty.get(symbol, 0))
        cur_dollars = cur * px
        drift = abs(target_dollars - cur_dollars) / account_value if account_value > 0 else 0.0
        if drift < min_rebalance_pct:
            continue
        delta = target_shares - cur
        if delta > 0:
            orders.append(OrderDelta(symbol, "buy", delta, target_weights.get(symbol, 0.0)))
        elif delta < 0:
            orders.append(OrderDelta(symbol, "sell", -delta, target_weights.get(symbol, 0.0)))
    return orders
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_orders.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add data/packages/congress-copytrader/algorithm.py \
        tests/packages/congress_copytrader/test_orders.py
git commit -m "feat(congress-copytrader): order generation with churn band + exits"
```

---

### Task 5: Wire the `CongressCopyTrader` orchestration

**Files:**
- Modify: `data/packages/congress-copytrader/algorithm.py`
- Create: `tests/packages/congress_copytrader/test_algorithm.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/packages/congress_copytrader/test_algorithm.py`:

```python
from datetime import datetime, timezone

import pandas as pd
import pytest

from sdk.models import Position


class _FakeCtx:
    """Minimal stand-in for TickContext: serves a fixed disclosure frame + prices."""

    def __init__(self, disclosures: pd.DataFrame, prices: dict, positions: dict, account_value: float):
        self._disclosures = disclosures
        self._prices = prices
        self._positions = positions
        self._account_value = account_value

    @property
    def timestamp(self):
        return datetime(2026, 6, 19, tzinfo=timezone.utc)

    @property
    def positions(self):
        return self._positions

    @property
    def account_value(self):
        return self._account_value

    def dataset(self, name, **kwargs):
        if name == "fmp.house_disclosures":
            return self._disclosures
        return pd.DataFrame(columns=["lastName", "firstName", "symbol", "type", "amount"])

    def market_data(self, symbol, timeframe="1day", bars=1, source=None):
        px = self._prices.get(symbol)
        if px is None:
            return pd.DataFrame()
        return pd.DataFrame({"close": [px]})


def _disclosures(rows):
    return pd.DataFrame(rows, columns=["lastName", "firstName", "symbol", "type", "amount"])


def _make_algo(algo_mod, **overrides):
    algo = algo_mod.CongressCopyTrader()
    config = {"chambers": "house", "min_member_history_usd": 0, "pct_invest": 1.0,
              "min_rebalance_pct": 0.0, "max_weight": 1.0}
    config.update(overrides)
    algo.on_start(config, None)
    return algo


def test_on_tick_emits_buy_signals(algo_mod):
    ctx = _FakeCtx(
        disclosures=_disclosures([["Doe", "Jane", "AAA", "Purchase", "$1,001 - $15,000"]]),
        prices={"AAA": 100.0},
        positions={},
        account_value=10000.0,
    )
    algo = _make_algo(algo_mod)
    signals = algo.on_tick(ctx)
    assert len(signals) == 1
    leg = signals[0].legs[0]
    assert leg.symbol == "AAA"
    assert leg.signal_type.value == "buy"
    assert leg.quantity == 100
    assert leg.asset_type == "equities"


def test_on_tick_exits_name_congress_sold(algo_mod):
    # Hold AAA; the only disclosure is a net-zero (bought+sold) so basket is empty -> SELL all.
    pos = Position(symbol="AAA", quantity=50, avg_cost=90.0, current_price=100.0, asset_type="equities")
    ctx = _FakeCtx(
        disclosures=_disclosures([
            ["Doe", "Jane", "AAA", "Purchase", "$1,001 - $15,000"],
            ["Doe", "Jane", "AAA", "Sale", "$1,001 - $15,000"],
        ]),
        prices={"AAA": 100.0},
        positions={"AAA": pos},
        account_value=10000.0,
    )
    algo = _make_algo(algo_mod)
    signals = algo.on_tick(ctx)
    assert len(signals) == 1
    leg = signals[0].legs[0]
    assert leg.symbol == "AAA" and leg.signal_type.value == "sell" and leg.quantity == 50


def test_on_tick_no_disclosures_emits_nothing(algo_mod):
    ctx = _FakeCtx(
        disclosures=_disclosures([]),
        prices={}, positions={}, account_value=10000.0,
    )
    algo = _make_algo(algo_mod)
    assert algo.on_tick(ctx) == []


def test_on_stop_is_stateless(algo_mod):
    algo = _make_algo(algo_mod)
    assert algo.on_stop() == {}
    assert algo.save_state() == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_algorithm.py -v`
Expected: FAIL — `on_tick` returns `[]` (the stub), so the buy/exit assertions fail.

- [ ] **Step 3: Implement the orchestration class**

In `data/packages/congress-copytrader/algorithm.py`, add `import logging` and `from datetime import timedelta` to the imports, add `from sdk.signals import Signal, SignalType, OrderType` (replacing the existing `from sdk.signals import Signal`), and add a module-level logger after the imports:

```python
logger = logging.getLogger(__name__)

_DISCLOSURE_COLUMNS = ["lastName", "firstName", "symbol", "type", "amount"]
```

Then replace the entire `class CongressCopyTrader` block with:

```python
class CongressCopyTrader(QuiltAlgorithm):
    def on_start(self, config: dict, restored_state: Optional[dict]) -> None:
        self.members = parse_members(config.get("members", "") or "")
        self.chambers = (config.get("chambers", "both") or "both").lower()
        self.history_days = int(config.get("history_days", 0) or 0)
        self.min_member_history_usd = float(config.get("min_member_history_usd", 50000))
        self.max_weight = float(config.get("max_weight", 0.25))
        self.min_rebalance_pct = float(config.get("min_rebalance_pct", 0.02))
        self.pct_invest = float(config.get("pct_invest", 0.95))
        self.disclosure_lag_days = int(config.get("disclosure_lag_days", 0) or 0)

    def _chamber_datasets(self) -> list[str]:
        if self.chambers == "house":
            return ["fmp.house_disclosures"]
        if self.chambers == "senate":
            return ["fmp.senate_disclosures"]
        return ["fmp.house_disclosures", "fmp.senate_disclosures"]

    def _load_disclosures(self, ctx) -> "pd.DataFrame":
        frames = []
        lag = timedelta(days=self.disclosure_lag_days)
        start = None
        if self.history_days > 0:
            start = (ctx.timestamp - lag).date() - timedelta(days=self.history_days)
        for name in self._chamber_datasets():
            try:
                df = ctx.dataset(name, start=start, lag=lag)
            except Exception:
                logger.exception("congress-copytrader: failed to load %s", name)
                continue
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=_DISCLOSURE_COLUMNS)
        combined = pd.concat(frames, ignore_index=True)
        for col in _DISCLOSURE_COLUMNS:
            if col not in combined.columns:
                combined[col] = ""
        return combined[_DISCLOSURE_COLUMNS]

    def on_tick(self, ctx) -> list[Signal]:
        df = self._load_disclosures(ctx)
        weights = compute_target_weights(
            df, self.members, self.min_member_history_usd, self.max_weight,
        )
        prices: dict[str, float] = {}
        for symbol in set(weights) | set(ctx.positions):
            bars = ctx.market_data(symbol, "1day", 1)
            if bars is None or len(bars) == 0:
                logger.info("congress-copytrader: skip %s — no price data", symbol)
                continue
            prices[symbol] = float(bars["close"].iloc[-1])
        current_qty = {s: p.quantity for s, p in ctx.positions.items()}
        orders = compute_orders(
            weights, ctx.account_value, self.pct_invest,
            prices, current_qty, self.min_rebalance_pct,
        )
        return [self._to_signal(o) for o in orders]

    @staticmethod
    def _to_signal(order: "OrderDelta") -> Signal:
        side = SignalType.BUY if order.side == "buy" else SignalType.SELL
        return Signal.simple(
            order.symbol, side, order.quantity,
            asset_type="equities", order_type=OrderType.MARKET,
            reasoning=f"Congress copy: target weight {order.target_weight:.1%}",
        )

    def on_stop(self) -> dict:
        return self.save_state()

    def save_state(self) -> dict:
        return {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/test_algorithm.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add data/packages/congress-copytrader/algorithm.py \
        tests/packages/congress_copytrader/test_algorithm.py
git commit -m "feat(congress-copytrader): wire on_tick orchestration + signals"
```

---

### Task 6: Full-suite verification and package validation

**Files:** none (verification only)

- [ ] **Step 1: Run the full package test suite**

Run: `.venv/bin/python -m pytest tests/packages/congress_copytrader/ -v`
Expected: PASS (all tests across the 5 test files green).

- [ ] **Step 2: Validate the package via the SDK**

Run: `.venv/bin/quilt validate data/packages/congress-copytrader`
Expected: exit code 0 and a success message (no validation errors). If `.venv/bin/quilt` is absent, run `.venv/bin/python -m sdk.cli.main validate data/packages/congress-copytrader`.

- [ ] **Step 3: Confirm the module imports cleanly**

Run: `.venv/bin/python -c "import importlib.util; s=importlib.util.spec_from_file_location('a','data/packages/congress-copytrader/algorithm.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('OK', m.CongressCopyTrader)"`
Expected: prints `OK <class '...CongressCopyTrader'>`.

- [ ] **Step 4: Commit any final fixes**

If Steps 1-3 required fixes, stage and commit them:

```bash
git add data/packages/congress-copytrader tests/packages/congress_copytrader
git commit -m "test(congress-copytrader): full-suite + package validation"
```

If nothing changed, skip this commit.

---

## Notes for the implementer

- **Stateless by design:** `on_tick` recomputes the entire basket each call from the point-in-time disclosure data; `save_state`/`on_stop` intentionally return `{}`. Do not add persisted tallies — the bitemporal `dataset()` already guarantees no-lookahead.
- **Dynamic universe:** the manifest lists only SPY as the anchor (drives the daily tick + NYSE calendar). Every traded symbol is resolved at runtime via `ctx.market_data`; a symbol with no bars is skipped and logged, never fatal.
- **Backtest prerequisite (out of scope here):** a meaningful backtest needs the disclosure datasets downloaded and OHLCV bars present for traded symbols. Collect the "skip — no price data" logs and queue those symbols via `quilt data` / `DataGoal`. Auto-download is a backlog item.
- **Member config delimiter:** entries are `;`-separated; each is `Last` or `Last, First`. This disambiguates the comma inside "Last, First" (a refinement over the spec's prose, which left the delimiter implicit).
```
