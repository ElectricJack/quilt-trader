# Backtest Engine Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three medium-impact gaps in the backtest engine: replace the synthetic clock with a union-of-symbol-timelines clock, add manifest `data:` block for custom data dependencies, and implement GTC/DAY order semantics.

**Architecture:** The backtest pipeline flows: `BacktestRunner.run()` builds context + clock from manifest deps, then hands off to `BacktestEngine._run_internal()` which iterates bar-by-bar, ticking the algorithm and processing pending orders. Sub-project 1 modifies the engine to rebuild the clock after the first tick when the algo dynamically loads symbols. Sub-project 2 adds a `data:` field to `QuiltManifest` and validates custom data sources exist before the engine starts. Sub-project 3 adds a `TimeInForce` enum to `SignalLeg` and changes the engine's pending-order expiry logic to support DAY/GTC/IOC semantics.

**Tech Stack:** Python 3.12, pandas, numpy, dataclasses, pytest. Key files: `sdk/signals.py`, `sdk/manifest.py`, `coordinator/services/backtest_engine_v2.py`, `coordinator/services/backtest_runner.py`, `coordinator/services/backtest_tick_context.py`.

---

## Sub-project 1: Union-of-Symbols Clock

### Task 1: Add `_build_union_clock()` helper to BacktestEngine

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`
- Create: `tests/coordinator/services/test_union_clock.py`

- [ ] **Step 1: Write failing test for union clock builder**

```python
# tests/coordinator/services/test_union_clock.py
import pytest
import pandas as pd
import numpy as np
from coordinator.services.backtest_engine_v2 import BacktestEngine


def _make_bars(symbol, dates, price_base=100.0):
    n = len(dates)
    return pd.DataFrame({
        "timestamp": pd.to_datetime(dates),
        "open": [price_base + i for i in range(n)],
        "high": [price_base + i + 1 for i in range(n)],
        "low": [price_base + i - 1 for i in range(n)],
        "close": [price_base + i + 0.5 for i in range(n)],
        "volume": [1_000_000] * n,
    })


class TestBuildUnionClock:
    def test_two_symbols_merged_and_deduplicated(self):
        """Union of AAPL (Mon-Fri) and GOOG (Wed-Fri + next Mon) produces sorted, deduped timeline."""
        aapl = _make_bars("AAPL", ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
        goog = _make_bars("GOOG", ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"], price_base=150.0)
        bars = {
            ("polygon", "AAPL", "1day"): aapl,
            ("polygon", "GOOG", "1day"): goog,
        }
        clock = BacktestEngine._build_union_clock(bars)
        timestamps = clock["timestamp"].tolist()
        assert len(timestamps) == 6  # 5 from AAPL + 1 extra from GOOG (Jan 8)
        assert timestamps == sorted(timestamps)
        # Each row must have real OHLCV (not zeros)
        assert (clock["close"] != 0).all()

    def test_single_symbol_returns_that_series(self):
        spy = _make_bars("SPY", ["2024-01-02", "2024-01-03", "2024-01-04"])
        bars = {("polygon", "SPY", "1day"): spy}
        clock = BacktestEngine._build_union_clock(bars)
        assert len(clock) == 3
        assert list(clock["close"]) == list(spy["close"])

    def test_empty_bars_returns_empty_dataframe(self):
        clock = BacktestEngine._build_union_clock({})
        assert len(clock) == 0
        assert list(clock.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
```

- [ ] **Step 2: Implement `_build_union_clock()` static method**

```python
# coordinator/services/backtest_engine_v2.py — add to BacktestEngine class

    @staticmethod
    def _build_union_clock(bars: dict[tuple, pd.DataFrame]) -> pd.DataFrame:
        """Merge all symbol timelines into a sorted, deduplicated clock.

        For each unique timestamp, picks OHLCV from the first symbol that has
        data at that timestamp (so the clock always has real prices, never zeros).
        """
        if not bars:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        # Collect all (timestamp, ohlcv) rows from every symbol
        frames = []
        for key, df in bars.items():
            if df is not None and not df.empty:
                sub = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
                sub["timestamp"] = pd.to_datetime(sub["timestamp"])
                if sub["timestamp"].dt.tz is not None:
                    sub["timestamp"] = sub["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
                frames.append(sub)

        if not frames:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        combined = pd.concat(frames, ignore_index=True)
        # Keep first occurrence per timestamp (so we get real OHLCV, not zeros)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="first")
        combined = combined.sort_values("timestamp").reset_index(drop=True)
        return combined
```

- [ ] **Step 3: Run tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_union_clock.py -v
```

### Task 2: Engine rebuilds clock after first `on_tick` for scraper-only algos

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`
- Edit: `tests/coordinator/services/test_union_clock.py`

- [ ] **Step 1: Write failing test for clock rebuild after first tick**

```python
# tests/coordinator/services/test_union_clock.py — append

from coordinator.services.backtest_engine_v2 import (
    BacktestEngine, CancelToken, EngineObserver, EngineSummary, FillRecord,
)
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import SlippageModel
from sdk.signals import Signal, SignalLeg, SignalType, OrderType


class _RecordingObserver:
    def __init__(self):
        self.equity = []
        self.fills = []
        self.complete = False
        self.error = None
        self.rejected = []
    def on_tick(self, sim_time, ctx_snapshot): pass
    def on_signals_emitted(self, sim_time, signals): pass
    def on_fill(self, fill): self.fills.append(fill)
    def on_signal_rejected(self, sim_time, signal, reason): self.rejected.append(reason)
    def on_equity_point(self, sim_time, pv, cash, positions):
        self.equity.append({"sim_time": sim_time, "pv": pv, "cash": cash})
    def on_complete(self, summary): self.complete = True
    def on_error(self, exc): self.error = exc


class _DynamicLoadAlgo:
    """Algo with no pre-declared deps that loads SPY on first tick via market_data()."""
    def __init__(self): self._loaded = False
    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx):
        if not self._loaded:
            ctx.market_data("SPY", "1day", bars=5)
            self._loaded = True
        return []
    def on_stop(self): return {}
    def save_state(self): return {}


def test_synthetic_clock_replaced_after_first_tick():
    """When the algo loads symbols dynamically, the engine should rebuild the
    clock from real data after the first tick, eliminating $0 prices."""
    import numpy as np

    # Build a synthetic (all-zeros) clock for 5 business days
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    synthetic_clock = pd.DataFrame({
        "timestamp": dates,
        "open": np.zeros(5), "high": np.zeros(5),
        "low": np.zeros(5), "close": np.zeros(5),
        "volume": np.zeros(5),
    })

    # Pre-load SPY data into the bars dict so market_data() finds it
    spy_bars = _make_bars("SPY", dates, price_base=450.0)
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): spy_bars},
        positions={}, cash=100_000.0,
    )

    obs = _RecordingObserver()
    BacktestEngine().run(
        algorithm=_DynamicLoadAlgo(),
        ctx=ctx,
        clock_series=synthetic_clock,
        clock_timeframe="1day",
        clock_source="synthetic",
        clock_symbol="_clock",
        slippage=SlippageModel(),
        buy_fees=[], sell_fees=[],
        initial_cash=100_000.0,
        observer=obs,
        cancel_token=CancelToken(),
    )

    assert obs.complete
    assert obs.error is None
    # After the first tick, the engine should have rebuilt the clock.
    # Equity points after tick 0 should NOT show $0-based valuations.
    # With no positions held, PV == cash == 100_000 at every point.
    for eq in obs.equity:
        assert eq["pv"] == pytest.approx(100_000.0, abs=1e-2)
```

- [ ] **Step 2: Add clock-rebuild logic after first tick in `_run_internal()`**

In `coordinator/services/backtest_engine_v2.py`, inside `_run_internal()`, after the first iteration of the main loop (bar_idx == 0), check if `clock_source == "synthetic"` and `ctx._bars` has real data. If so, rebuild the clock:

```python
# coordinator/services/backtest_engine_v2.py — inside _run_internal(), after the
# end of the bar_idx == 0 iteration (right after the equity point / progress block),
# add this clock-rebuild check:

            # ---- 4. Clock rebuild for scraper-only algos ----
            # After the first tick, if we started with a synthetic clock and the
            # algorithm loaded real market data via ctx.market_data(), rebuild the
            # clock from the union of all loaded symbol timelines.
            if bar_idx == 0 and clock_source == "synthetic" and ctx._bars:
                rebuilt = self._build_union_clock(dict(ctx._bars))
                if not rebuilt.empty:
                    clock = rebuilt
                    clock_source = "union"
                    # Re-derive tf_duration from the dominant timeframe
                    # (keep original clock_tf — it's still "1day" for daily algos)
                    logger.info(
                        "Rebuilt clock from %d symbol timelines (%d bars)",
                        len(ctx._bars), len(clock),
                    )
```

This requires restructuring the loop to allow restarting iteration from bar 1 with the new clock. The cleanest approach: after rebuilding, `continue` from bar_idx 0 but skip the already-processed tick (use a `clock_rebuilt` flag to avoid re-ticking bar 0).

- [ ] **Step 3: Run all backtest engine tests to confirm no regressions**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/services/test_union_clock.py -v
```

### Task 3: Remove synthetic all-zeros fallback from BacktestRunner

**Files:**
- Edit: `coordinator/services/backtest_runner.py`

- [ ] **Step 1: Replace the all-zeros synthetic clock with a placeholder clock that sets `clock_source = "synthetic"`**

The runner at lines 305-321 currently creates a zero-price DataFrame when no bars are pre-loaded. Since the engine now rebuilds the clock after the first tick, the runner should still create the synthetic timestamp skeleton (the engine needs at least one bar to iterate through), but the engine will replace it. Keep the business-day date range but mark the source as `"synthetic"` so the engine knows to rebuild:

```python
# coordinator/services/backtest_runner.py — the else branch at line 305 stays
# structurally the same (we still need the synthetic schedule for the first tick).
# The engine's clock-rebuild logic handles the rest.
# No code change needed here — the existing synthetic clock + engine rebuild
# is the complete solution.
```

- [ ] **Step 2: Add integration-level test that a scraper-only algo runs without zero-price fills**

```python
# tests/coordinator/services/test_union_clock.py — append

class _BuyOnFirstTickAlgo:
    """Loads SPY dynamically, then buys on second tick."""
    def __init__(self): self._tick = 0
    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx):
        self._tick += 1
        if self._tick == 1:
            ctx.market_data("SPY", "1day", bars=5)
            return []
        if self._tick == 2:
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=10,
                order_type=OrderType.MARKET,
            )])]
        return []
    def on_stop(self): return {}
    def save_state(self): return {}


def test_no_zero_price_fills_with_dynamic_load():
    """Fills must use real prices even when the clock started synthetic."""
    import numpy as np
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    synthetic_clock = pd.DataFrame({
        "timestamp": dates,
        "open": np.zeros(10), "high": np.zeros(10),
        "low": np.zeros(10), "close": np.zeros(10),
        "volume": np.zeros(10),
    })
    spy_bars = _make_bars("SPY", dates, price_base=450.0)
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): spy_bars},
        positions={}, cash=100_000.0,
    )
    obs = _RecordingObserver()
    BacktestEngine().run(
        algorithm=_BuyOnFirstTickAlgo(), ctx=ctx,
        clock_series=synthetic_clock,
        clock_timeframe="1day", clock_source="synthetic", clock_symbol="_clock",
        slippage=SlippageModel(market_bps=0),
        buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.complete
    # The fill must be at a real price (>$1), not $0
    assert len(obs.fills) == 1
    assert obs.fills[0].fill_price > 1.0
```

- [ ] **Step 3: Run full test suite**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/services/test_union_clock.py -v
```

---

## Sub-project 2: Manifest `data:` Block

### Task 4: Add `data` field to `QuiltManifest` dataclass

**Files:**
- Edit: `sdk/manifest.py`
- Edit: `tests/sdk/test_manifest.py`

- [ ] **Step 1: Write failing tests for `data:` block parsing**

```python
# tests/sdk/test_manifest.py — append

class TestManifestDataBlock:
    def test_data_block_parsed(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
data:
  - source: alpha-picks-scraper
    type: scraper
  - source: sector-weights.csv
    type: csv
"""
        m = QuiltManifest.from_string(yaml_str)
        assert len(m.data) == 2
        assert m.data[0] == {"source": "alpha-picks-scraper", "type": "scraper"}
        assert m.data[1] == {"source": "sector-weights.csv", "type": "csv"}

    def test_data_block_defaults_to_empty_list(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
"""
        m = QuiltManifest.from_string(yaml_str)
        assert m.data == []

    def test_data_block_ignores_entries_without_source(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
data:
  - type: csv
  - source: my-data
    type: json
"""
        m = QuiltManifest.from_string(yaml_str)
        assert len(m.data) == 1
        assert m.data[0]["source"] == "my-data"

    def test_data_block_validates_type_field(self):
        yaml_str = """
name: test-algo
type: algorithm
version: 1.0.0
entry_point: algo.py
class_name: TestAlgo
requirements:
  asset_types: [equities]
data:
  - source: my-data
    type: invalid_type
"""
        with pytest.raises(ManifestError, match="type"):
            QuiltManifest.from_string(yaml_str)
```

- [ ] **Step 2: Add `data` field to `QuiltManifest` dataclass and parse it in `_parse()`**

```python
# sdk/manifest.py — add to QuiltManifest dataclass (after `trigger` field):
    data: list[dict] = field(default_factory=list)

# sdk/manifest.py — add to _parse() method, before the `return QuiltManifest(...)`:
        # Parse top-level `data:` block for custom data dependencies (scraper
        # outputs, CSVs, JSON files). Entries without `source` are dropped.
        raw_data = data.get("data") or []
        data_deps: list[dict] = []
        valid_data_types = {"scraper", "csv", "json", "parquet"}
        if isinstance(raw_data, list):
            for d in raw_data:
                if not isinstance(d, dict):
                    continue
                source = d.get("source")
                if not source:
                    continue
                dtype = d.get("type", "csv")
                if dtype not in valid_data_types:
                    raise ManifestError(
                        f"data entry type must be one of {valid_data_types}, got {dtype!r}"
                    )
                data_deps.append({"source": source, "type": dtype})

# And in the return statement, add: data=data_deps,
```

- [ ] **Step 3: Run manifest tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/sdk/test_manifest.py -v
```

### Task 5: Validate custom data deps in BacktestRunner before engine start

**Files:**
- Edit: `coordinator/services/backtest_runner.py`
- Create: `tests/coordinator/services/test_data_validation.py`

- [ ] **Step 1: Write failing test for data dependency validation**

```python
# tests/coordinator/services/test_data_validation.py
import pytest
from pathlib import Path
from coordinator.services.backtest_runner import _validate_custom_data_deps


def test_validate_passes_when_data_exists(tmp_path):
    """No error when all declared data sources exist on disk."""
    custom_dir = tmp_path / "data" / "custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / "alpha-picks-scraper.csv").write_text("col1,col2\n1,2\n")
    data_deps = [{"source": "alpha-picks-scraper", "type": "csv"}]
    # Should not raise
    _validate_custom_data_deps(data_deps, custom_dir)


def test_validate_raises_when_data_missing(tmp_path):
    """Clear error when a declared data source is missing."""
    custom_dir = tmp_path / "data" / "custom"
    custom_dir.mkdir(parents=True)
    data_deps = [{"source": "nonexistent-scraper", "type": "scraper"}]
    with pytest.raises(FileNotFoundError, match="nonexistent-scraper"):
        _validate_custom_data_deps(data_deps, custom_dir)


def test_validate_finds_subdirectory_match(tmp_path):
    """Data source that exists as a subdirectory is accepted."""
    custom_dir = tmp_path / "data" / "custom"
    subdir = custom_dir / "alpha-picks-scraper"
    subdir.mkdir(parents=True)
    (subdir / "data.csv").write_text("col1\n1\n")
    data_deps = [{"source": "alpha-picks-scraper", "type": "scraper"}]
    _validate_custom_data_deps(data_deps, custom_dir)


def test_validate_empty_deps_is_noop(tmp_path):
    """No error when manifest declares no data deps."""
    custom_dir = tmp_path / "data" / "custom"
    _validate_custom_data_deps([], custom_dir)
```

- [ ] **Step 2: Implement `_validate_custom_data_deps()` function**

```python
# coordinator/services/backtest_runner.py — add as module-level function

def _validate_custom_data_deps(
    data_deps: list[dict], custom_dir: Path,
) -> None:
    """Validate that all declared custom data dependencies exist on disk.

    Checks the same resolution paths as BacktestTickContext._resolve_custom_data:
    exact file, file with extension, subdirectory with data files.
    """
    for dep in data_deps:
        source = dep.get("source", "")
        if not source:
            continue
        # 1. Exact path
        if (custom_dir / source).is_file():
            continue
        # 2. Try appending extensions
        found = False
        for ext in (".csv", ".parquet", ".json"):
            if (custom_dir / f"{source}{ext}").is_file():
                found = True
                break
        if found:
            continue
        # 3. Subdirectory with data files
        subdir = custom_dir / source
        if subdir.is_dir() and any(subdir.glob("*.csv")) or any(subdir.glob("*.parquet")) or any(subdir.glob("*.json")):
            continue
        # 4. Strip extension and try as subdirectory
        stem = Path(source).stem
        if stem != source:
            subdir = custom_dir / stem
            if subdir.is_dir() and (any(subdir.glob("*.csv")) or any(subdir.glob("*.parquet")) or any(subdir.glob("*.json"))):
                continue
        raise FileNotFoundError(
            f"Missing data dependency: {source!r}. "
            f"Expected file or directory at {custom_dir / source}. "
            f"Declare custom data sources in the manifest `data:` block."
        )
```

- [ ] **Step 3: Call validation in `BacktestRunner.run()` after loading manifest**

In `coordinator/services/backtest_runner.py`, inside `run()`, after `manifest = _load_manifest(pkg_dir_name)` (line 195) and before Stage 1 (data coverage), add:

```python
            # Validate custom data dependencies declared in manifest.data
            if manifest.data:
                _validate_custom_data_deps(manifest.data, Path("data/custom"))
```

- [ ] **Step 4: Run tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_data_validation.py tests/sdk/test_manifest.py -v
```

---

## Sub-project 3: GTC/DAY Order Semantics

### Task 6: Add `TimeInForce` enum and field to `SignalLeg`

**Files:**
- Edit: `sdk/signals.py`
- Create: `tests/sdk/test_time_in_force.py`

- [ ] **Step 1: Write failing tests for TimeInForce serialization**

```python
# tests/sdk/test_time_in_force.py
import pytest
from sdk.signals import SignalLeg, SignalType, OrderType, TimeInForce, Signal


def test_time_in_force_default_is_day():
    leg = SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=1)
    assert leg.time_in_force == TimeInForce.DAY


def test_time_in_force_gtc():
    leg = SignalLeg(
        symbol="SPY", signal_type=SignalType.BUY, quantity=1,
        time_in_force=TimeInForce.GTC,
    )
    assert leg.time_in_force == TimeInForce.GTC


def test_time_in_force_serializes_to_dict():
    leg = SignalLeg(
        symbol="SPY", signal_type=SignalType.BUY, quantity=1,
        time_in_force=TimeInForce.GTC,
    )
    d = leg.to_dict()
    assert d["time_in_force"] == "GTC"


def test_time_in_force_deserializes_from_dict():
    d = {
        "symbol": "SPY", "signal_type": "buy", "quantity": 1,
        "time_in_force": "GTC",
    }
    leg = SignalLeg.from_dict(d)
    assert leg.time_in_force == TimeInForce.GTC


def test_time_in_force_missing_from_dict_defaults_to_day():
    d = {"symbol": "SPY", "signal_type": "buy", "quantity": 1}
    leg = SignalLeg.from_dict(d)
    assert leg.time_in_force == TimeInForce.DAY


def test_signal_simple_accepts_time_in_force():
    sig = Signal.simple(
        symbol="SPY", signal_type=SignalType.BUY, quantity=10,
        order_type=OrderType.LIMIT, limit_price=450.0,
        time_in_force=TimeInForce.GTC,
    )
    assert sig.legs[0].time_in_force == TimeInForce.GTC
```

- [ ] **Step 2: Add `TimeInForce` enum and update `SignalLeg`**

```python
# sdk/signals.py — add enum after OrderType:

class TimeInForce(Enum):
    DAY = "DAY"    # Expires at end of trading day
    GTC = "GTC"    # Good-til-cancelled: stays pending until filled or explicitly cancelled
    IOC = "IOC"    # Immediate-or-cancel: current 1-bar behavior

# sdk/signals.py — add field to SignalLeg dataclass (after stop_price):
    time_in_force: TimeInForce = TimeInForce.DAY

# sdk/signals.py — update SignalLeg.to_dict():
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type.value,
            "quantity": self.quantity,
            "asset_type": self.asset_type,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "time_in_force": self.time_in_force.value,
        }

# sdk/signals.py — update SignalLeg.from_dict():
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
            time_in_force=TimeInForce(d.get("time_in_force", "DAY")),
        )

# sdk/signals.py — update Signal.simple() to accept time_in_force parameter:
    @staticmethod
    def simple(
        symbol: str,
        signal_type: SignalType,
        quantity: float,
        asset_type: str = "equities",
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        reasoning: Optional[str] = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
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
                    time_in_force=time_in_force,
                )
            ],
            strategy_type="single",
            reasoning=reasoning,
        )
```

- [ ] **Step 3: Run tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/sdk/test_time_in_force.py tests/coordinator/services/test_backtest_engine.py -v
```

### Task 7: Implement IOC expiry logic (preserve current 1-bar behavior)

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`
- Edit: `tests/coordinator/services/test_backtest_engine.py`

- [ ] **Step 1: Write failing test that IOC orders expire after 1 bar**

```python
# tests/coordinator/services/test_backtest_engine.py — append

def test_ioc_order_expires_after_one_bar():
    """IOC limit order that doesn't cross on the fill bar is rejected immediately."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce

    class IOCAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=90.0,  # Won't cross (low=99)
                time_in_force=TimeInForce.IOC,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 5, lows=[99.0]*5, highs=[101.0]*5)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=IOCAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert len(obs.fills) == 0
    assert len(obs.rejected) == 1
    assert "no_fill_within_timeout" in obs.rejected[0][2]
```

- [ ] **Step 2: Update the expiry branch in `_run_internal()` to check `time_in_force`**

In `coordinator/services/backtest_engine_v2.py`, replace the expiry block (lines 250-254):

```python
                else:
                    # Not filled, not stop-trigger — apply expiry based on time_in_force
                    tif = getattr(po.leg, 'time_in_force', None)
                    if tif is None:
                        # Legacy signals without time_in_force field — treat as IOC
                        from sdk.signals import TimeInForce
                        tif = TimeInForce.IOC

                    if tif == TimeInForce.IOC:
                        # Immediate-or-cancel: 1 bar expiry (original behavior)
                        observer.on_signal_rejected(
                            sim_time, Signal(legs=[po.leg]), "no_fill_within_timeout"
                        )
                    elif tif == TimeInForce.DAY:
                        # Keep pending until end of trading day (handled in Task 8)
                        still_pending.append(po)
                    elif tif == TimeInForce.GTC:
                        # Keep pending indefinitely (handled in Task 9)
                        still_pending.append(po)
                    else:
                        # Unknown TIF — reject
                        observer.on_signal_rejected(
                            sim_time, Signal(legs=[po.leg]), f"unknown_time_in_force:{tif}"
                        )
```

- [ ] **Step 3: Run tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -v
```

### Task 8: Implement DAY order expiry (cancel at day boundary)

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`
- Edit: `tests/coordinator/services/test_backtest_engine.py`

- [ ] **Step 1: Write failing tests for DAY order semantics**

```python
# tests/coordinator/services/test_backtest_engine.py — append

def test_day_order_fills_later_same_day():
    """DAY limit order that doesn't cross on bar T+1 but crosses on bar T+2 (same day) fills."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce
    # Use intraday bars (1min) on a single day so all bars are "same day"
    timestamps = pd.date_range("2024-01-02 09:30", periods=5, freq="min", tz="UTC")
    clock = pd.DataFrame({
        "timestamp": timestamps,
        "open":  [100.0, 100.0, 100.0, 100.0, 100.0],
        "high":  [101.0, 101.0, 101.0, 101.0, 101.0],
        "low":   [99.5,  99.5,  99.5,  98.0,  99.5],  # bar 3 dips to 98 → crosses limit at 99
        "close": [100.0, 100.0, 100.0, 99.0,  100.0],
        "volume": [1e6]*5,
    })

    class DayLimitAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=99.0,
                time_in_force=TimeInForce.DAY,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    ctx = BacktestTickContext(bars={("polygon", "SPY", "1min"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=DayLimitAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1min", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    # The limit order is placed on bar 0, scheduled for bar 1.
    # Bars 1 and 2 have low=99.5 (doesn't cross 99.0 strictly).
    # Bar 3 has low=98.0 (crosses 99.0 strictly) → fill.
    assert len(obs.fills) == 1
    assert obs.fills[0].fill_price == pytest.approx(99.0, abs=1e-6)


def test_day_order_expires_at_day_boundary():
    """DAY limit order placed on day 1 does NOT carry into day 2."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce
    # Two trading days: Jan 2 and Jan 3
    clock = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2024-01-02 09:30", "2024-01-02 10:30",  # Day 1
            "2024-01-03 09:30", "2024-01-03 10:30",  # Day 2 — price crosses here
        ]).tz_localize("UTC"),
        "open":  [100.0, 100.0, 100.0, 100.0],
        "high":  [101.0, 101.0, 101.0, 101.0],
        "low":   [99.5,  99.5,  98.0,  98.0],  # Only day 2 crosses the limit
        "close": [100.0, 100.0, 99.0,  99.0],
        "volume": [1e6]*4,
    })

    class DayLimitAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=99.0,
                time_in_force=TimeInForce.DAY,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=DayLimitAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    # Signal on bar 0 (Jan 2 09:30). Scheduled for bar 1 (Jan 2 10:30) — low=99.5, no cross.
    # DAY order expires at end of Jan 2 — should NOT fill on Jan 3 bars.
    assert len(obs.fills) == 0
    assert any("day_expired" in r[2] for r in obs.rejected)
```

- [ ] **Step 2: Add day-boundary detection and DAY expiry logic**

In `coordinator/services/backtest_engine_v2.py`, add a helper and update the DAY branch:

```python
# coordinator/services/backtest_engine_v2.py — add to _PendingOrder dataclass:
    created_date: Optional[object] = None  # date when the order was placed (for DAY expiry)

# In _run_internal(), where pending orders are created (the `for sig in signals:` block),
# capture the order's creation date:
                    for leg in sig.legs:
                        pending.append(_PendingOrder(
                            signal_id=sig_id, leg=leg,
                            scheduled_for_bar_index=bar_idx + 1,
                            created_date=sim_time.date(),
                        ))

# In the DAY branch of the expiry logic, check if the current bar's date
# differs from the order's creation date:
                    elif tif == TimeInForce.DAY:
                        order_date = po.created_date
                        current_date = sim_time.date()
                        if order_date is not None and current_date > order_date:
                            observer.on_signal_rejected(
                                sim_time, Signal(legs=[po.leg]), "day_expired"
                            )
                        else:
                            still_pending.append(po)
```

- [ ] **Step 3: Run tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -v -k "day"
```

### Task 9: Implement GTC order semantics

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`
- Edit: `tests/coordinator/services/test_backtest_engine.py`

- [ ] **Step 1: Write failing tests for GTC semantics**

```python
# tests/coordinator/services/test_backtest_engine.py — append

def test_gtc_order_fills_across_days():
    """GTC limit order placed on day 1 fills when price crosses on day 3."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce

    class GTCAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=95.0,
                time_in_force=TimeInForce.GTC,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 6,
                  opens=[100]*6, highs=[101]*6,
                  lows=[99, 99, 99, 99, 94, 99],  # bar 4 dips to 94 → crosses 95
                  closes=[100]*6)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=GTCAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    # Signal on bar 0, scheduled for bar 1. Bars 1-3 don't cross.
    # Bar 4 crosses → fill.
    assert len(obs.fills) == 1
    assert obs.fills[0].fill_price == pytest.approx(95.0, abs=1e-6)
    # Fill timestamp should be bar 4's timestamp
    assert obs.fills[0].timestamp == clock.iloc[4]["timestamp"].to_pydatetime()


def test_gtc_order_persists_until_end_if_never_crossed():
    """GTC order that never crosses stays pending through all bars, then is rejected at engine stop."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce

    class GTCNeverFillAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                order_type=OrderType.LIMIT, limit_price=50.0,  # Will never cross (lows=99)
                time_in_force=TimeInForce.GTC,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 5)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=GTCNeverFillAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert len(obs.fills) == 0
    # Should be rejected at end of run with "gtc_expired_end_of_backtest"
    assert any("gtc_expired" in r[2] for r in obs.rejected)
```

- [ ] **Step 2: Update the GTC branch and add end-of-run cleanup**

The GTC branch in the expiry logic (from Task 7) already keeps the order pending with `still_pending.append(po)`. The key change is: GTC orders must be re-tried on every bar (not just their scheduled bar). Update the scheduled_for check:

```python
# coordinator/services/backtest_engine_v2.py — in _run_internal(), update the
# pending order processing loop. Currently orders with
# po.scheduled_for_bar_index > bar_idx are skipped. For GTC/DAY orders that
# were kept pending, their scheduled_for_bar_index was the original bar+1,
# so they need to be retried on subsequent bars. Fix: when keeping a pending
# order, set its scheduled_for_bar_index to the next bar:

                    elif tif == TimeInForce.DAY:
                        order_date = po.created_date
                        current_date = sim_time.date()
                        if order_date is not None and current_date > order_date:
                            observer.on_signal_rejected(
                                sim_time, Signal(legs=[po.leg]), "day_expired"
                            )
                        else:
                            po.scheduled_for_bar_index = bar_idx + 1
                            still_pending.append(po)
                    elif tif == TimeInForce.GTC:
                        po.scheduled_for_bar_index = bar_idx + 1
                        still_pending.append(po)
```

And after the main loop exits (after `algorithm.on_stop()`), reject any remaining GTC orders:

```python
        # Reject any remaining GTC pending orders at end of backtest
        if pending:
            from sdk.signals import TimeInForce
            final_time = sim_time if 'sim_time' in dir() else datetime.now(timezone.utc)
            for po in pending:
                observer.on_signal_rejected(
                    final_time, Signal(legs=[po.leg]), "gtc_expired_end_of_backtest"
                )
```

- [ ] **Step 3: Run tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -v -k "gtc or day or ioc"
```

### Task 10: Add `cancel_order()` method to TickContext for GTC cancellation

**Files:**
- Edit: `sdk/context.py`
- Edit: `coordinator/services/backtest_tick_context.py`
- Edit: `tests/coordinator/services/test_backtest_engine.py`

- [ ] **Step 1: Write failing test for cancel_order**

```python
# tests/coordinator/services/test_backtest_engine.py — append

def test_cancel_order_removes_gtc_pending():
    """Algorithm can cancel a GTC order by signal_id, preventing future fills."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType, TimeInForce

    class CancelAlgo:
        def __init__(self): self._tick = 0; self._signal_id = None
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            self._tick += 1
            if self._tick == 1:
                sig = Signal(legs=[SignalLeg(
                    symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                    order_type=OrderType.LIMIT, limit_price=95.0,
                    time_in_force=TimeInForce.GTC,
                )])
                return [sig]
            if self._tick == 3:
                # Cancel all pending orders
                ctx.cancel_all_orders()
            return []
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 8,
                  opens=[100]*8, highs=[101]*8,
                  lows=[99, 99, 99, 99, 99, 94, 94, 94],  # crosses after cancel
                  closes=[100]*8)
    ctx = BacktestTickContext(bars={("polygon", "SPY", "1day"): clock}, positions={}, cash=10_000.0)
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=CancelAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(), buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    # The algo cancels on tick 3. Even though price crosses on bars 5-7,
    # no fill should happen.
    assert len(obs.fills) == 0
    assert any("cancelled_by_algorithm" in r[2] for r in obs.rejected)
```

- [ ] **Step 2: Add `cancel_all_orders()` to TickContext interface**

```python
# sdk/context.py — add to TickContext abstract class:

    def cancel_all_orders(self) -> int:
        """Cancel all pending orders. Returns number cancelled.

        Only meaningful in backtest mode — in live mode, use broker API directly.
        """
        return 0  # Default no-op for live context

# coordinator/services/backtest_tick_context.py — add to BacktestTickContext:

    def __init__(self, ...):
        ...
        self._cancel_requested: bool = False

    def cancel_all_orders(self) -> int:
        """Request cancellation of all pending orders.

        The engine checks this flag each bar and clears matching orders.
        """
        self._cancel_requested = True
        return 0  # Actual count determined by engine
```

- [ ] **Step 3: Check cancel flag in engine loop**

In `coordinator/services/backtest_engine_v2.py`, in `_run_internal()`, after the algorithm `on_tick` call and before processing pending orders, add:

```python
            # Check if algorithm requested order cancellation
            if getattr(ctx, '_cancel_requested', False):
                for po in pending:
                    observer.on_signal_rejected(
                        sim_time, Signal(legs=[po.leg]), "cancelled_by_algorithm"
                    )
                pending = []
                ctx._cancel_requested = False
```

- [ ] **Step 4: Run full test suite**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py tests/sdk/test_time_in_force.py -v
```

---

## Final Verification

### Task 11: Full regression suite

**Files:** (none changed)

- [ ] **Step 1: Run all backtest-related tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/services/test_union_clock.py tests/coordinator/services/test_data_validation.py tests/sdk/test_manifest.py tests/sdk/test_time_in_force.py -v
```

- [ ] **Step 2: Run the broader test suite to check for regressions**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -x --timeout=60 -q
```

- [ ] **Step 3: Verify no import errors in changed modules**

```bash
cd /home/jkern/dev/quilt-trader && python -c "
from sdk.signals import SignalLeg, TimeInForce, Signal, SignalType, OrderType
from sdk.manifest import QuiltManifest
from coordinator.services.backtest_engine_v2 import BacktestEngine
from coordinator.services.backtest_runner import _validate_custom_data_deps
print('All imports OK')

# Verify TimeInForce enum
assert TimeInForce.DAY.value == 'DAY'
assert TimeInForce.GTC.value == 'GTC'
assert TimeInForce.IOC.value == 'IOC'

# Verify SignalLeg default
leg = SignalLeg(symbol='SPY', signal_type=SignalType.BUY, quantity=1)
assert leg.time_in_force == TimeInForce.DAY

# Verify backward compat
d = {'symbol': 'SPY', 'signal_type': 'buy', 'quantity': 1}
leg2 = SignalLeg.from_dict(d)
assert leg2.time_in_force == TimeInForce.DAY

print('Smoke checks passed')
"
```
