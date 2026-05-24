# Options Data Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the separate option chain storage/download path — store all options data as standard contract bar files and build chain views on the fly.

**Architecture:** Today there are two storage formats (chain snapshots under `options/{exp}/chain.parquet` and individual contract bar files under `{OCC_SYMBOL}/1day.parquet`) and three download paths. After this work, there is one storage format (contract bars, same as equities), one download path (`fetch_bars`), and a new `build_chain_from_bars()` function that reconstructs chain snapshots on the fly from stored bars. Contract discovery uses Polygon's `/v3/reference/options/contracts` endpoint, then each discovered contract is downloaded via the standard `fetch_bars` → `save_market_data` pipeline.

**Tech Stack:** Python, pandas, Polygon REST API, FastAPI, React/TypeScript

---

## File Map

### Modified Files
| File | Responsibility |
|------|---------------|
| `coordinator/services/data_service.py` | Remove `save_option_chain`, `load_option_chain`, `list_option_chain_expirations`, `option_chain_path`. Add `list_option_contracts()` and `build_chain_from_bars()`. |
| `coordinator/services/data_providers/polygon.py` | Replace `fetch_option_chain()` with `discover_option_contracts()` (contract listing only, no bar fetching). |
| `coordinator/services/download_manager.py` | Remove `_download_option_chain_symbol()` and `data_type="option_chain"` dispatch. Add option contract discovery + batch bar download in the standard bars path. |
| `coordinator/services/backtest_runner.py` | Replace `_download_option_chains()` with contract-discovery + standard download. Replace cache warming with `build_chain_from_bars()`. |
| `coordinator/services/backtest_tick_context.py` | Replace `load_option_chain` calls in `option_chain()` with `build_chain_from_bars()`. |
| `coordinator/services/backtest_engine_v2.py` | Replace `load_option_chain` fallback in `_lookup_option_price()` with `build_chain_from_bars()`. |
| `coordinator/services/account_lifecycle.py` | Fix `_fetch_prices_inline` to route through download manager instead of writing parquet directly. |
| `coordinator/api/routes/data.py` | Remove chain-specific coverage logic. OCC contract bars show up via existing `list_available_market_data` already. |
| `sdk/manifest.py` | No changes needed — already preserves `timeframe` and `source` (fixed in prior session). |
| `dashboard/src/hooks/useProcessedCoverage.ts` | Remove the `option_expirations` chain-snapshot path added earlier. The existing OCC grouping logic already handles contract bars. |
| `dashboard/src/api/client.ts` | Remove `option_expirations` from `CoverageAsset`. |
| `dashboard/src/lib/occ.ts` | Add support for `O:` prefix that Polygon uses. |

### New Files
| File | Responsibility |
|------|---------------|
| `coordinator/services/chain_builder.py` | Pure function: given a list of contract bar DataFrames + their OCC symbols, build a chain DataFrame with columns matching what `BacktestTickContext.option_chain()` expects. |
| `tests/coordinator/services/test_chain_builder.py` | Tests for chain building logic. |

### Delete
| Target | Reason |
|--------|--------|
| `data/market/polygon/QQQ/options/` | Migrate existing chain data to contract bars, then remove directory. |

---

## OCC Symbol Convention

Polygon uses `O:SPY241029C00586000`, Tradier uses `SPY241029C00586000` (no prefix). On disk, we'll store **without** the `O:` prefix to match the existing Tradier convention and the frontend's `isOCCSymbol` regex. The Polygon provider will strip the `O:` prefix before passing symbols to `save_market_data`.

---

### Task 1: Create `chain_builder.py` — build chain from bars

**Files:**
- Create: `coordinator/services/chain_builder.py`
- Create: `tests/coordinator/services/test_chain_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/test_chain_builder.py
import pandas as pd
import pytest
from coordinator.services.chain_builder import build_chain_from_bars, parse_occ_symbol


def test_parse_occ_symbol_with_prefix():
    result = parse_occ_symbol("O:SPY241029C00586000")
    assert result == {
        "underlying": "SPY",
        "expiration": "2024-10-29",
        "option_type": "call",
        "strike": 586.0,
        "raw_symbol": "SPY241029C00586000",
    }


def test_parse_occ_symbol_without_prefix():
    result = parse_occ_symbol("SPY241029P00570000")
    assert result == {
        "underlying": "SPY",
        "expiration": "2024-10-29",
        "option_type": "put",
        "strike": 570.0,
        "raw_symbol": "SPY241029P00570000",
    }


def test_parse_occ_symbol_invalid():
    assert parse_occ_symbol("SPY") is None
    assert parse_occ_symbol("AAPL") is None


def test_build_chain_from_bars_basic():
    bars = {
        "SPY241029C00580000": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28"]),
            "open": [6.0, 5.5], "high": [6.5, 6.0],
            "low": [5.5, 5.0], "close": [6.2, 5.8],
            "volume": [100, 150],
        }),
        "SPY241029P00580000": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28"]),
            "open": [4.0, 4.5], "high": [4.5, 5.0],
            "low": [3.5, 4.0], "close": [4.2, 4.8],
            "volume": [80, 120],
        }),
    }
    chain = build_chain_from_bars(bars, as_of=pd.Timestamp("2024-10-28"))
    assert len(chain) == 2
    assert set(chain.columns) >= {
        "symbol", "strike", "option_type", "bid", "ask",
        "last", "volume", "open_interest", "implied_volatility",
    }
    call_row = chain[chain["option_type"] == "call"].iloc[0]
    assert call_row["strike"] == 580.0
    assert call_row["last"] == pytest.approx(5.8)
    assert call_row["symbol"] == "SPY241029C00580000"


def test_build_chain_from_bars_filters_by_as_of():
    """Only bars at or before as_of should be used."""
    bars = {
        "SPY241029C00580000": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28", "2024-10-29"]),
            "open": [6.0, 5.5, 7.0], "high": [6.5, 6.0, 7.5],
            "low": [5.5, 5.0, 6.5], "close": [6.2, 5.8, 7.2],
            "volume": [100, 150, 200],
        }),
    }
    chain = build_chain_from_bars(bars, as_of=pd.Timestamp("2024-10-28"))
    assert len(chain) == 1
    assert chain.iloc[0]["last"] == pytest.approx(5.8)


def test_build_chain_from_bars_empty():
    chain = build_chain_from_bars({}, as_of=pd.Timestamp("2024-10-28"))
    assert chain.empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/services/test_chain_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coordinator.services.chain_builder'`

- [ ] **Step 3: Implement chain_builder.py**

```python
# coordinator/services/chain_builder.py
"""Build an option chain DataFrame from individual contract bar files.

The chain is a point-in-time cross-section: for each contract, take the
most recent bar at or before `as_of` and extract pricing.  Bid/ask are
estimated from the bar's high-low spread, matching the Polygon provider's
existing convention.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

import pandas as pd

_OCC_RE = re.compile(r"^(?:O:)?([A-Z]{1,6})(\d{6})([CP])(\d{8})$")

CHAIN_COLUMNS = [
    "symbol", "strike", "option_type", "bid", "ask",
    "last", "volume", "open_interest", "implied_volatility",
]


def parse_occ_symbol(symbol: str) -> Optional[dict]:
    m = _OCC_RE.match(symbol)
    if not m:
        return None
    underlying, date_str, cp, strike_raw = m.groups()
    yy, mm, dd = int(date_str[:2]), int(date_str[2:4]), int(date_str[4:6])
    raw = symbol.removeprefix("O:")
    return {
        "underlying": underlying,
        "expiration": f"20{yy:02d}-{mm:02d}-{dd:02d}",
        "option_type": "call" if cp == "C" else "put",
        "strike": int(strike_raw) / 1000.0,
        "raw_symbol": raw,
    }


def build_chain_from_bars(
    bars: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=CHAIN_COLUMNS)

    rows: list[dict] = []
    for symbol, df in bars.items():
        parsed = parse_occ_symbol(symbol)
        if parsed is None:
            continue
        if df is None or df.empty:
            continue
        ts = pd.to_datetime(df["timestamp"])
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        cutoff = as_of.tz_localize(None) if as_of.tz is not None else as_of
        visible = df[ts <= cutoff]
        if visible.empty:
            continue
        last_bar = visible.iloc[-1]
        close = float(last_bar["close"])
        high = float(last_bar["high"])
        low = float(last_bar["low"])
        vol = int(last_bar.get("volume", 0))
        spread = max((high - low) * 0.1, close * 0.02) if close > 0 else 0.1
        rows.append({
            "symbol": parsed["raw_symbol"],
            "strike": parsed["strike"],
            "option_type": parsed["option_type"],
            "bid": max(0.0, close - spread / 2),
            "ask": close + spread / 2,
            "last": close,
            "volume": vol,
            "open_interest": 0,
            "implied_volatility": 0.0,
        })

    if not rows:
        return pd.DataFrame(columns=CHAIN_COLUMNS)
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/services/test_chain_builder.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/chain_builder.py tests/coordinator/services/test_chain_builder.py
git commit -m "feat: add chain_builder — build option chain from contract bars"
```

---

### Task 2: Add `discover_option_contracts()` to Polygon provider

**Files:**
- Modify: `coordinator/services/data_providers/polygon.py`
- Create: `tests/coordinator/services/test_polygon_discover.py`

This extracts the contract discovery logic (Step 0 + Step 1) from the existing `fetch_option_chain` into a standalone method that returns a list of OCC symbols. The bar-fetching loop (Step 2) is deleted — callers will use `fetch_bars` per contract instead.

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/test_polygon_discover.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import date
from coordinator.services.data_providers.polygon import PolygonProvider


@pytest.mark.asyncio
async def test_discover_option_contracts_returns_occ_symbols():
    mock_http = AsyncMock()
    # Mock the underlying price response
    price_resp = MagicMock()
    price_resp.status_code = 200
    price_resp.json.return_value = {"results": [{"c": 500.0}]}
    # Mock the contracts response
    contracts_resp = MagicMock()
    contracts_resp.status_code = 200
    contracts_resp.json.return_value = {
        "results": [
            {"ticker": "O:SPY250620C00490000", "strike_price": 490.0, "contract_type": "call"},
            {"ticker": "O:SPY250620P00490000", "strike_price": 490.0, "contract_type": "put"},
            {"ticker": "O:SPY250620C00510000", "strike_price": 510.0, "contract_type": "call"},
        ],
        "next_url": None,
    }
    mock_http.get = AsyncMock(side_effect=[price_resp, contracts_resp])

    provider = PolygonProvider(api_key="test", http_client=mock_http)
    result = await provider.discover_option_contracts("SPY", date(2025, 6, 20))

    assert len(result) == 3
    assert result[0]["ticker"] == "O:SPY250620C00490000"
    assert result[0]["strike_price"] == 490.0


@pytest.mark.asyncio
async def test_discover_option_contracts_respects_max_contracts():
    mock_http = AsyncMock()
    price_resp = MagicMock()
    price_resp.status_code = 200
    price_resp.json.return_value = {"results": [{"c": 500.0}]}
    contracts = [
        {"ticker": f"O:SPY250620C00{480+i:03d}000", "strike_price": 480.0 + i, "contract_type": "call"}
        for i in range(20)
    ]
    contracts_resp = MagicMock()
    contracts_resp.status_code = 200
    contracts_resp.json.return_value = {"results": contracts, "next_url": None}
    mock_http.get = AsyncMock(side_effect=[price_resp, contracts_resp])

    provider = PolygonProvider(api_key="test", http_client=mock_http)
    result = await provider.discover_option_contracts(
        "SPY", date(2025, 6, 20), max_contracts=5,
    )
    assert len(result) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/services/test_polygon_discover.py -v`
Expected: FAIL with `AttributeError: 'PolygonProvider' object has no attribute 'discover_option_contracts'`

- [ ] **Step 3: Extract `discover_option_contracts` from `fetch_option_chain`**

Add this method to `PolygonProvider` in `coordinator/services/data_providers/polygon.py`. It is Steps 0 and 1 of the existing `fetch_option_chain`, returning the raw contract list instead of fetching bars:

```python
async def discover_option_contracts(
    self,
    underlying: str,
    expiration: date,
    on_status: StatusCallback | None = None,
    strike_range_pct: float = 0.05,
    max_contracts: int = 0,
) -> list[dict]:
    """Discover option contracts for a given underlying + expiration.

    Returns a list of contract dicts with keys: ticker, strike_price, contract_type.
    Does NOT fetch bars — callers use fetch_bars() per contract.
    """
    from datetime import timedelta

    # Step 0: Get underlying price for ATM strike filtering
    await self._safe_status(on_status, f"Fetching {underlying} price for ATM filtering")
    underlying_price = None
    try:
        price_url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{underlying}/range"
            f"/1/day/{(expiration - timedelta(days=7)).isoformat()}/{expiration.isoformat()}"
        )
        price_resp = await self._request_with_retry(
            price_url, {"apiKey": self._api_key, "limit": 5, "sort": "desc"},
            on_status=on_status,
        )
        price_bars = price_resp.json().get("results") or []
        if price_bars:
            underlying_price = price_bars[0].get("c")
    except Exception:
        pass

    # Step 1: Discover contracts via reference endpoint
    await self._safe_status(on_status, f"Discovering {underlying} contracts for {expiration}")
    contracts: list[dict] = []
    url = f"{self.BASE_URL}/v3/reference/options/contracts"
    params: dict = {
        "apiKey": self._api_key,
        "underlying_ticker": underlying,
        "expiration_date": expiration.isoformat(),
        "as_of": expiration.isoformat(),
        "limit": 1000,
        "sort": "strike_price",
        "order": "asc",
    }
    if underlying_price is not None:
        params["strike_price.gte"] = round(underlying_price * (1 - strike_range_pct))
        params["strike_price.lte"] = round(underlying_price * (1 + strike_range_pct))
    while True:
        resp = await self._request_with_retry(url, params, on_status=on_status)
        data = resp.json()
        for c in data.get("results") or []:
            contracts.append(c)
        next_url = data.get("next_url")
        if not next_url:
            break
        url = next_url
        params = {"apiKey": self._api_key}

    if max_contracts > 0 and underlying_price is not None and len(contracts) > max_contracts:
        contracts.sort(key=lambda c: abs(c.get("strike_price", 0) - underlying_price))
        contracts = contracts[:max_contracts]

    return contracts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/services/test_polygon_discover.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/data_providers/polygon.py tests/coordinator/services/test_polygon_discover.py
git commit -m "feat: add discover_option_contracts to PolygonProvider"
```

---

### Task 3: Add `list_option_contracts()` and `build_chain()` to DataService

**Files:**
- Modify: `coordinator/services/data_service.py`

These methods let callers discover which OCC contract bars exist on disk for a given underlying + expiration, and build a chain from them.

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/services/test_data_service_options.py
import os
import tempfile
import pandas as pd
import pytest
from datetime import date
from coordinator.services.data_service import DataService


@pytest.fixture
def svc(tmp_path):
    market = tmp_path / "market"
    custom = tmp_path / "custom"
    market.mkdir()
    custom.mkdir()
    return DataService(market_data_dir=str(market), custom_data_dir=str(custom))


def _write_contract_bars(svc, provider, symbol, df):
    svc.save_market_data(provider, symbol, "1day", df)


def test_list_option_contracts_finds_matching_contracts(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-10-25"]),
        "open": [5.0], "high": [6.0], "low": [4.0], "close": [5.5], "volume": [100],
    })
    _write_contract_bars(svc, "polygon", "SPY241029C00580000", df)
    _write_contract_bars(svc, "polygon", "SPY241029P00580000", df)
    _write_contract_bars(svc, "polygon", "SPY241115C00580000", df)  # different expiration
    _write_contract_bars(svc, "polygon", "QQQ241029C00400000", df)  # different underlying

    contracts = svc.list_option_contracts("polygon", "SPY", date(2024, 10, 29))
    assert sorted(contracts) == ["SPY241029C00580000", "SPY241029P00580000"]


def test_list_option_contracts_empty(svc):
    assert svc.list_option_contracts("polygon", "SPY", date(2024, 10, 29)) == []


def test_list_option_expirations(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-10-25"]),
        "open": [5.0], "high": [6.0], "low": [4.0], "close": [5.5], "volume": [100],
    })
    _write_contract_bars(svc, "polygon", "SPY241029C00580000", df)
    _write_contract_bars(svc, "polygon", "SPY241115C00580000", df)
    _write_contract_bars(svc, "polygon", "SPY241115P00580000", df)

    exps = svc.list_option_expirations("polygon", "SPY")
    assert exps == [date(2024, 10, 29), date(2024, 11, 15)]


def test_build_chain_loads_and_builds(svc):
    df1 = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28"]),
        "open": [5.0, 5.5], "high": [6.0, 6.5], "low": [4.0, 4.5],
        "close": [5.5, 6.0], "volume": [100, 150],
    })
    df2 = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28"]),
        "open": [3.0, 3.5], "high": [4.0, 4.5], "low": [2.0, 2.5],
        "close": [3.5, 4.0], "volume": [80, 120],
    })
    _write_contract_bars(svc, "polygon", "SPY241029C00580000", df1)
    _write_contract_bars(svc, "polygon", "SPY241029P00580000", df2)

    chain = svc.build_chain("polygon", "SPY", date(2024, 10, 29), as_of=pd.Timestamp("2024-10-28"))
    assert len(chain) == 2
    call = chain[chain["option_type"] == "call"].iloc[0]
    assert call["last"] == pytest.approx(6.0)
    assert call["strike"] == 580.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/coordinator/services/test_data_service_options.py -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement the methods in DataService**

Add to `coordinator/services/data_service.py`:

```python
def list_option_contracts(
    self, provider: str, underlying: str, expiration: date,
) -> list[str]:
    """List OCC symbols on disk for a given underlying + expiration."""
    from coordinator.services.chain_builder import parse_occ_symbol
    provider_dir = os.path.join(self._market_dir, provider)
    if not os.path.isdir(provider_dir):
        return []
    exp_str = expiration.isoformat()
    result = []
    for name in os.listdir(provider_dir):
        parsed = parse_occ_symbol(name)
        if parsed and parsed["underlying"] == underlying and parsed["expiration"] == exp_str:
            if os.path.exists(os.path.join(provider_dir, name, "1day.parquet")):
                result.append(name)
    return sorted(result)

def list_option_expirations(self, provider: str, underlying: str) -> list:
    """List unique expiration dates for an underlying from OCC bar files on disk."""
    from datetime import date as _date
    from coordinator.services.chain_builder import parse_occ_symbol
    provider_dir = os.path.join(self._market_dir, provider)
    if not os.path.isdir(provider_dir):
        return []
    expirations = set()
    for name in os.listdir(provider_dir):
        parsed = parse_occ_symbol(name)
        if parsed and parsed["underlying"] == underlying:
            if os.path.exists(os.path.join(provider_dir, name, "1day.parquet")):
                expirations.add(_date.fromisoformat(parsed["expiration"]))
    return sorted(expirations)

def build_chain(
    self, provider: str, underlying: str, expiration, as_of=None,
) -> pd.DataFrame:
    """Build an option chain from stored contract bar files."""
    from datetime import date as _date
    from coordinator.services.chain_builder import build_chain_from_bars
    if isinstance(expiration, str):
        expiration = _date.fromisoformat(expiration)
    contracts = self.list_option_contracts(provider, underlying, expiration)
    if not contracts:
        return pd.DataFrame()
    bars = {}
    for sym in contracts:
        df = self.load_market_data(provider, sym, "1day")
        if df is not None and not df.empty:
            bars[sym] = df
    if as_of is None:
        as_of = pd.Timestamp.now()
    return build_chain_from_bars(bars, as_of=pd.Timestamp(as_of))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/coordinator/services/test_data_service_options.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/data_service.py tests/coordinator/services/test_data_service_options.py
git commit -m "feat: add list_option_contracts, list_option_expirations, build_chain to DataService"
```

---

### Task 4: Update `occ.ts` to handle `O:` prefix

**Files:**
- Modify: `dashboard/src/lib/occ.ts`
- Modify: `dashboard/src/lib/occ.test.ts` (if exists, otherwise create)

Polygon symbols have an `O:` prefix. The frontend should handle both formats.

- [ ] **Step 1: Update regex and functions**

In `dashboard/src/lib/occ.ts`, change line 2:

```typescript
const OCC_RE = /^(?:O:)?([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;
```

No other changes needed — `isOCCSymbol`, `parseOCC`, and `formatOCCReadable` all use this regex.

- [ ] **Step 2: Run existing tests**

Run: `cd dashboard && npx vitest run src/lib/occ.test.ts` (if test file exists)

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/lib/occ.ts
git commit -m "fix: handle O: prefix in OCC symbol parsing"
```

---

### Task 5: Replace `_download_option_chains` in backtest_runner.py

**Files:**
- Modify: `coordinator/services/backtest_runner.py`

Replace the chain-based download with: discover contracts → download bars for each via the standard `fetch_bars` path → warm cache with `build_chain`.

- [ ] **Step 1: Replace `_download_option_chains` method**

Replace the existing `_download_option_chains` method (lines ~491-539) with:

```python
async def _download_option_contracts(self, underlyings, date_start, date_end, run_id) -> list[str]:
    """Pre-download option contract bars for options algorithms.

    Discovers contracts via the provider's reference endpoint, then downloads
    bars for each through the standard fetch_bars pipeline.
    """
    from coordinator.database.models import BacktestRun
    errors: list[str] = []

    start_d = date_start.date() if hasattr(date_start, "date") else date_start
    end_d = date_end.date() if hasattr(date_end, "date") else date_end
    provider_name = "polygon"
    provider = self._dm._providers.get(provider_name)
    if provider is None or not hasattr(provider, "discover_option_contracts"):
        return errors

    for underlying in underlyings:
        try:
            expirations = self._monthly_expirations(start_d, end_d)
            for exp in expirations:
                existing = self._ds.list_option_contracts(provider_name, underlying, exp)
                if existing:
                    continue

                async with self._sf() as session:
                    r = (await session.execute(
                        select(BacktestRun).where(BacktestRun.id == run_id)
                    )).scalar_one()
                    r.progress_message = f"Discovering {underlying} contracts for {exp}"
                    await session.commit()

                contracts = await provider.discover_option_contracts(underlying, exp)
                if not contracts:
                    continue

                # Strip O: prefix for storage
                symbols = [c["ticker"].removeprefix("O:") for c in contracts]

                async with self._sf() as session:
                    r = (await session.execute(
                        select(BacktestRun).where(BacktestRun.id == run_id)
                    )).scalar_one()
                    r.progress_message = f"Downloading {len(symbols)} {underlying} contracts for {exp}"
                    await session.commit()

                dl = await self._dm.create_download(
                    symbols=symbols,
                    date_range_start=exp,
                    date_range_end=exp,
                    provider=provider_name,
                    timeframe="1day",
                )
                try:
                    await self._wait_for_download(dl["id"])
                except RuntimeError as exc:
                    errors.append(f"Contract bars download failed for {underlying} {exp}: {exc}")
        except Exception as exc:
            errors.append(f"Failed option contract download for {underlying}: {exc}")

    return errors
```

- [ ] **Step 2: Update the caller in `run()`**

Replace the call to `_download_option_chains` with `_download_option_contracts`:

```python
# Was:
options_chain_errors = await self._download_option_chains(
    option_underlyings, date_range_start, date_range_end, run_id,
)
# Now:
options_chain_errors = await self._download_option_contracts(
    option_underlyings, date_range_start, date_range_end, run_id,
)
```

- [ ] **Step 3: Replace cache warming with `build_chain`**

Replace the existing cache warming block (lines ~325-348) with:

```python
# Warm option chain cache for options algorithms
if "options" in (manifest.requirements.asset_types or []):
    for dep in deps:
        symbol = dep.get("symbol")
        if symbol and self._ds:
            expirations = self._ds.list_option_expirations("polygon", symbol)
            for exp in expirations:
                chain_df = self._ds.build_chain("polygon", symbol, exp, as_of=date_range_end)
                if chain_df is not None and not chain_df.empty:
                    ctx._option_chain_cache[("polygon", symbol, exp)] = chain_df
```

- [ ] **Step 4: Run existing backtest engine tests**

Run: `pytest tests/coordinator/services/test_backtest_engine.py -v`
Expected: PASS (all 13 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/backtest_runner.py
git commit -m "refactor: replace chain download with contract bar download in runner"
```

---

### Task 6: Update BacktestTickContext to use `build_chain`

**Files:**
- Modify: `coordinator/services/backtest_tick_context.py`

Replace all `load_option_chain` and `list_option_chain_expirations` calls in `option_chain()` with `build_chain` and `list_option_expirations`.

- [ ] **Step 1: Update the `option_chain()` method**

Replace the disk-loading block (lines ~322-342) — the section between the cache check and the empty-chain guard. The new version uses `build_chain` and `list_option_expirations` from the data service:

```python
# In option_chain(), replace the load_option_chain path:
# Old:
#   elif self._data_service is not None and hasattr(self._data_service, "load_option_chain"):
#       df = self._data_service.load_option_chain(source, symbol, exp)
# New:
elif self._data_service is not None and hasattr(self._data_service, "build_chain"):
    df = self._data_service.build_chain(source, symbol, exp, as_of=self._sim_time_now)
    if df is not None and not df.empty:
        self._option_chain_cache[cache_key] = df
    else:
        df = pd.DataFrame()
```

And replace the nearest-expiration fallback:

```python
# Old:
#   if ... hasattr(self._data_service, "list_option_chain_expirations"):
#       available = self._data_service.list_option_chain_expirations(source, symbol)
# New:
if (df is None or df.empty) and self._data_service is not None and hasattr(self._data_service, "list_option_expirations"):
    available = self._data_service.list_option_expirations(source, symbol)
    if available:
        nearest = min(available, key=lambda d: abs((d - exp).days))
        if abs((nearest - exp).days) <= 45:
            df = self._data_service.build_chain(source, symbol, nearest, as_of=self._sim_time_now)
            exp = nearest
            if df is not None and not df.empty:
                self._option_chain_cache[cache_key] = df
```

- [ ] **Step 2: Run backtest engine tests**

Run: `pytest tests/coordinator/services/test_backtest_engine.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add coordinator/services/backtest_tick_context.py
git commit -m "refactor: use build_chain instead of load_option_chain in tick context"
```

---

### Task 7: Update BacktestEngine `_lookup_option_price` fallback

**Files:**
- Modify: `coordinator/services/backtest_engine_v2.py`

Replace the `load_option_chain` fallback in `_lookup_option_price()` with `build_chain`.

- [ ] **Step 1: Update the fallback block**

In `_lookup_option_price` (around line 396-414), replace:

```python
# Old:
# if ctx._data_service is not None and hasattr(ctx._data_service, "load_option_chain"):
#     ...
#     chain_df = ctx._data_service.load_option_chain(source, underlying, exp)

# New:
if ctx._data_service is not None and hasattr(ctx._data_service, "build_chain"):
    underlying = self._extract_underlying(contract_symbol)
    if underlying:
        exp = ctx._sim_time_now.date() if ctx._sim_time_now else None
        source = ctx._default_source or "polygon"
        try:
            chain_df = ctx._data_service.build_chain(source, underlying, exp, as_of=ctx._sim_time_now)
            if chain_df is not None and not chain_df.empty:
                cache_key = (source, underlying, exp)
                ctx._option_chain_cache[cache_key] = chain_df
                for col in ("ticker", "symbol"):
                    if col in chain_df.columns:
                        match_rows = chain_df[chain_df[col] == contract_symbol]
                        if not match_rows.empty:
                            row = match_rows.iloc[0]
                            return float(row.get("ask", 0)) if side == "buy" else float(row.get("bid", 0))
        except Exception:
            logger.debug("Failed to build chain for %s", underlying, exc_info=True)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/coordinator/services/test_backtest_engine.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add coordinator/services/backtest_engine_v2.py
git commit -m "refactor: use build_chain instead of load_option_chain in engine"
```

---

### Task 8: Remove old chain storage code from DataService

**Files:**
- Modify: `coordinator/services/data_service.py`

- [ ] **Step 1: Delete the old methods**

Remove these methods from `DataService`:
- `option_chain_path()`
- `save_option_chain()`
- `load_option_chain()`
- `list_option_chain_expirations()`

- [ ] **Step 2: Verify no remaining callers**

Run: `grep -rn "save_option_chain\|load_option_chain\|list_option_chain_expirations\|option_chain_path" coordinator/ sdk/ tests/ --include="*.py"`

Expected: No matches (all callers were updated in Tasks 5-7).

- [ ] **Step 3: Commit**

```bash
git add coordinator/services/data_service.py
git commit -m "refactor: remove deprecated chain snapshot storage methods"
```

---

### Task 9: Remove `fetch_option_chain` and chain download path from download manager

**Files:**
- Modify: `coordinator/services/download_manager.py`
- Modify: `coordinator/services/data_providers/polygon.py`

- [ ] **Step 1: Remove `data_type="option_chain"` dispatch**

In `download_manager.py`, remove the `if data_type == "option_chain":` block (lines ~252-258) and the entire `_download_option_chain_symbol()` method (lines ~362-416).

- [ ] **Step 2: Delete `fetch_option_chain` from Polygon provider**

In `polygon.py`, delete the entire `fetch_option_chain()` method (lines ~229-364). Keep `discover_option_contracts()` which was added in Task 2.

- [ ] **Step 3: Verify no remaining callers**

Run: `grep -rn "fetch_option_chain\|_download_option_chain_symbol\|data_type.*option_chain" coordinator/ --include="*.py"`

Expected: No matches.

- [ ] **Step 4: Run all tests**

Run: `pytest tests/coordinator/services/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/download_manager.py coordinator/services/data_providers/polygon.py
git commit -m "refactor: remove fetch_option_chain and chain download path"
```

---

### Task 10: Fix account backfill to use download manager

**Files:**
- Modify: `coordinator/services/account_lifecycle.py`

The `_fetch_prices_inline` method bypasses the download manager and writes parquet directly. It also downloads OCC option symbols without filtering. Fix both: route through the download manager and skip expired options.

- [ ] **Step 1: Replace `_fetch_prices_inline`**

```python
async def _fetch_prices_inline(
    self, symbols: set[str], start: date, end: date, account_id: str,
) -> None:
    """Download daily bars through the download manager."""
    if self._download_manager is None:
        return
    # Filter out expired options (no point downloading bars for expired contracts)
    from coordinator.services.chain_builder import parse_occ_symbol
    active = []
    for sym in sorted(symbols):
        parsed = parse_occ_symbol(sym)
        if parsed:
            exp = date.fromisoformat(parsed["expiration"])
            if exp < start:
                continue
        # Check if already on disk
        path = self._data_service.market_data_path(self._default_provider, sym, "1day")
        if os.path.exists(path):
            continue
        active.append(sym)

    if not active:
        return

    await self._push_progress(
        account_id,
        f"Downloading price data for {len(active)} symbol(s)...",
    )
    dl = await self._download_manager.create_download(
        symbols=active,
        date_range_start=start,
        date_range_end=end,
        provider=self._default_provider,
        timeframe="1day",
    )
    # Wait for completion
    while True:
        status = await self._download_manager.get_download(dl["id"])
        if status and status.get("status") in ("completed", "failed", "cancelled"):
            break
        await asyncio.sleep(1.0)
```

- [ ] **Step 2: Add missing import**

Add `import os` at the top of the file if not already present.

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -k "backfill or lifecycle" -v` (if tests exist)

- [ ] **Step 4: Commit**

```bash
git add coordinator/services/account_lifecycle.py
git commit -m "refactor: route account backfill downloads through download manager"
```

---

### Task 11: Clean up frontend — remove chain snapshot path

**Files:**
- Modify: `dashboard/src/hooks/useProcessedCoverage.ts`
- Modify: `dashboard/src/api/client.ts`
- Modify: `coordinator/api/routes/data.py`
- Modify: `coordinator/services/data_service.py`

- [ ] **Step 1: Remove `option_expirations` from CoverageAsset**

In `dashboard/src/api/client.ts`, remove the `option_expirations?: string[];` field from `CoverageAsset`.

- [ ] **Step 2: Remove chain-snapshot options group in useProcessedCoverage.ts**

Remove the "Option chain groups" block that was added in the prior session (the block starting with `// Option chain groups (underlying symbols with cached chain snapshots)`). Also remove `optionExpirations?: string[];` from `OptionsGroupRow`. Also remove the `option_expirations` dedup merge in step 1b.

The existing OCC detection via `parseOCC` (lines 140-148) already groups contract bars correctly — that's the only path we need.

- [ ] **Step 3: Remove chain-specific logic from coverage endpoint**

In `coordinator/api/routes/data.py`, remove the `option_chains` dict tracking and `option_expirations` attachment logic from `get_coverage()`. Revert to the simple loop that was there before.

- [ ] **Step 4: Remove chain listing from `list_available_market_data`**

In `coordinator/services/data_service.py`, remove the `options/` directory scanning block from `list_available_market_data()`. Contract bars are already listed as regular market data files.

- [ ] **Step 5: Rebuild dashboard**

Run: `cd dashboard && npm run build`
Expected: Build succeeds with no type errors.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/hooks/useProcessedCoverage.ts dashboard/src/api/client.ts coordinator/api/routes/data.py coordinator/services/data_service.py
git commit -m "cleanup: remove chain snapshot UI and API code"
```

---

### Task 12: Migrate existing chain data to contract bars

**Files:**
- No code changes — data migration script

The existing chain snapshots at `data/market/polygon/QQQ/options/{exp}/chain.parquet` contain pricing data that should be preserved as contract bar files. Each row in the chain becomes a single-bar file.

- [ ] **Step 1: Write and run migration script**

```bash
python3 << 'PYEOF'
"""Migrate chain snapshots to individual contract bar files."""
import os
import pandas as pd
from pathlib import Path

market_dir = Path("data/market")
for chain_path in market_dir.rglob("options/*/chain.parquet"):
    provider = chain_path.parts[2]  # e.g., "polygon"
    underlying = chain_path.parts[3]  # e.g., "QQQ"
    exp_str = chain_path.parts[5]  # e.g., "2026-03-20"

    df = pd.read_parquet(chain_path)
    print(f"Migrating {chain_path}: {len(df)} contracts")

    for _, row in df.iterrows():
        occ = str(row.get("symbol", ""))
        occ = occ.removeprefix("O:")
        if not occ or len(occ) < 15:
            continue
        close = float(row.get("last", 0))
        bid = float(row.get("bid", 0))
        ask = float(row.get("ask", 0))
        vol = int(row.get("volume", 0))
        high = ask if ask > 0 else close
        low = bid if bid > 0 else close
        open_ = close

        bar_df = pd.DataFrame([{
            "timestamp": pd.Timestamp(exp_str),
            "open": open_, "high": high, "low": low, "close": close,
            "volume": vol,
        }])
        out_dir = market_dir / provider / occ
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "1day.parquet"
        if out_path.exists():
            print(f"  Skipping {occ} — already exists")
            continue
        bar_df.to_parquet(out_path, index=False)
        print(f"  Created {occ}/1day.parquet")

print("Migration complete.")
PYEOF
```

- [ ] **Step 2: Verify migration**

```bash
# Should find new contract bar dirs
find data/market/polygon -maxdepth 1 -type d -name "QQQ*" | head -10
# Should show contract count matching chain
python3 -c "
from coordinator.services.data_service import DataService
ds = DataService('data/market', 'data/custom')
from datetime import date
print('Contracts for 2026-03-20:', len(ds.list_option_contracts('polygon', 'QQQ', date(2026, 3, 20))))
print('Expirations:', ds.list_option_expirations('polygon', 'QQQ'))
"
```

- [ ] **Step 3: Remove old chain directories**

```bash
rm -rf data/market/polygon/QQQ/options/
```

- [ ] **Step 4: Commit**

```bash
git commit -m "data: migrate chain snapshots to contract bar files"
```

---

### Task 13: End-to-end test — run options-straddle backtest

**Files:** None (manual verification)

- [ ] **Step 1: Restart coordinator**

```bash
pkill -f "uvicorn.*coordinator" 2>/dev/null
sleep 2
nohup .venv/bin/python -m uvicorn --factory coordinator.main:create_app --host 0.0.0.0 --port 8000 --log-level warning > /tmp/coord_test.log 2>&1 &
sleep 8
```

- [ ] **Step 2: Queue a backtest**

```bash
curl -s -X POST http://localhost:8000/api/backtest-runs \
  -H "Content-Type: application/json" \
  -d '{
    "algorithm_id": "<options-straddle-id>",
    "date_range_start": "2026-03-01T00:00:00Z",
    "date_range_end": "2026-05-01T00:00:00Z",
    "initial_cash": 100000
  }'
```

- [ ] **Step 3: Verify it completes with trades**

Poll until complete. Check:
- Status = "completed"
- Trade count > 0
- Total return is a realistic number (not +1000% or exactly 0)

- [ ] **Step 4: Check dashboard**

Open the Data page → Available Data tab. Verify:
- QQQ option contracts appear grouped under "QQQ Options (N)"
- SPY option contracts still appear grouped under "SPY Options (N)"
- No "options" timeframe entries under equities
- Clicking a contract shows bar data

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: end-to-end verification fixes"
```
