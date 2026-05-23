# Options Backtesting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable backtesting of options algorithms by providing historical options chain data and options-aware fill simulation.

**Architecture:** Three-layer design. (1) A Polygon data fetcher downloads historical options chain snapshots (contract discovery + end-of-day quotes per expiration) and stores them as parquet files keyed by `data/market/polygon/{underlying}/options/{expiration}/chain.parquet`. (2) `BacktestTickContext.option_chain()` is upgraded from an empty-chain stub to a real lookup that loads cached chain data with no-look-ahead enforcement. (3) The `BacktestEngine` fill model gains an options-aware path that fills at contract bid/ask (not underlying OHLCV), applies the 100x contract multiplier, and tracks options positions keyed by OCC contract symbol. The manifest parser already supports `asset_class: options` in the assets block; the backtest runner is extended to pre-download option chain snapshots during Stage 1.

**Tech Stack:** Python 3.12, pandas, pyarrow (parquet), Polygon REST API (`/v3/reference/options/contracts`, `/v3/snapshot/options/{underlyingAsset}`), pytest, existing `DataService` / `PolygonProvider` / `BacktestEngine` / `BacktestTickContext` classes.

---

## Sub-project 1: Options Chain Data Source

### Task 1: PolygonProvider.fetch_option_chain() -- Tests

**Files:**
- Create: `tests/coordinator/services/test_polygon_options.py`

- [ ] **Step 1: Write failing tests for the new fetch_option_chain method**

```python
# tests/coordinator/services/test_polygon_options.py
"""Tests for PolygonProvider.fetch_option_chain()."""
import pytest
import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_http():
    """HTTP client mock that returns Polygon-shaped responses."""
    client = AsyncMock()
    return client


def _contracts_response(contracts, next_url=None):
    """Build a mock Polygon /v3/reference/options/contracts response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": contracts,
        "status": "OK",
        "next_url": next_url,
    }
    return resp


def _snapshot_response(results):
    """Build a mock Polygon /v3/snapshot/options/{underlying} response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": results,
        "status": "OK",
    }
    return resp


def test_fetch_option_chain_returns_dataframe(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    contracts = [
        {
            "ticker": "O:SPY250620C00450000",
            "underlying_ticker": "SPY",
            "expiration_date": "2025-06-20",
            "strike_price": 450.0,
            "contract_type": "call",
        },
        {
            "ticker": "O:SPY250620P00450000",
            "underlying_ticker": "SPY",
            "expiration_date": "2025-06-20",
            "strike_price": 450.0,
            "contract_type": "put",
        },
    ]

    snapshot_results = [
        {
            "details": {
                "ticker": "O:SPY250620C00450000",
                "strike_price": 450.0,
                "contract_type": "call",
                "expiration_date": "2025-06-20",
            },
            "day": {"open": 5.0, "high": 5.5, "low": 4.8, "close": 5.2, "volume": 1200},
            "last_quote": {"bid": 5.1, "ask": 5.3},
            "greeks": {"delta": 0.55, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
            "implied_volatility": 0.25,
            "open_interest": 8000,
        },
        {
            "details": {
                "ticker": "O:SPY250620P00450000",
                "strike_price": 450.0,
                "contract_type": "put",
                "expiration_date": "2025-06-20",
            },
            "day": {"open": 4.0, "high": 4.5, "low": 3.8, "close": 4.2, "volume": 900},
            "last_quote": {"bid": 4.1, "ask": 4.3},
            "greeks": {"delta": -0.45, "gamma": 0.03, "theta": -0.04, "vega": 0.11},
            "implied_volatility": 0.27,
            "open_interest": 6000,
        },
    ]

    mock_http.get = AsyncMock(side_effect=[
        _contracts_response(contracts),
        _snapshot_response(snapshot_results),
    ])

    provider = PolygonProvider(api_key="test-key", http_client=mock_http)

    df = asyncio.get_event_loop().run_until_complete(
        provider.fetch_option_chain(
            underlying="SPY",
            expiration=date(2025, 6, 20),
            as_of_date=date(2025, 6, 15),
        )
    )

    assert len(df) == 2
    assert set(df.columns) >= {
        "ticker", "strike", "option_type", "bid", "ask", "last",
        "volume", "open_interest", "implied_volatility",
        "delta", "gamma", "theta", "vega",
    }
    call_row = df[df["option_type"] == "call"].iloc[0]
    assert call_row["strike"] == 450.0
    assert call_row["bid"] == 5.1
    assert call_row["ask"] == 5.3


def test_fetch_option_chain_empty_contracts_returns_empty_df(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    mock_http.get = AsyncMock(return_value=_contracts_response([]))
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)

    df = asyncio.get_event_loop().run_until_complete(
        provider.fetch_option_chain(
            underlying="XYZ",
            expiration=date(2025, 6, 20),
            as_of_date=date(2025, 6, 15),
        )
    )
    assert df.empty


def test_fetch_option_chain_paginates_contracts(mock_http):
    from coordinator.services.data_providers.polygon import PolygonProvider

    page1_contracts = [
        {"ticker": f"O:SPY250620C0040{i}000", "underlying_ticker": "SPY",
         "expiration_date": "2025-06-20", "strike_price": 400 + i, "contract_type": "call"}
        for i in range(3)
    ]
    page2_contracts = [
        {"ticker": f"O:SPY250620C0040{i}000", "underlying_ticker": "SPY",
         "expiration_date": "2025-06-20", "strike_price": 403 + i, "contract_type": "call"}
        for i in range(2)
    ]

    snapshot_results = [
        {
            "details": {"ticker": c["ticker"], "strike_price": c["strike_price"],
                        "contract_type": "call", "expiration_date": "2025-06-20"},
            "day": {"open": 5.0, "high": 5.5, "low": 4.8, "close": 5.2, "volume": 100},
            "last_quote": {"bid": 5.0, "ask": 5.2},
            "greeks": {"delta": 0.5, "gamma": 0.02, "theta": -0.03, "vega": 0.1},
            "implied_volatility": 0.25,
            "open_interest": 500,
        }
        for c in page1_contracts + page2_contracts
    ]

    mock_http.get = AsyncMock(side_effect=[
        _contracts_response(page1_contracts, next_url="https://api.polygon.io/v3/next-page"),
        _contracts_response(page2_contracts),
        _snapshot_response(snapshot_results),
    ])

    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    df = asyncio.get_event_loop().run_until_complete(
        provider.fetch_option_chain(
            underlying="SPY",
            expiration=date(2025, 6, 20),
            as_of_date=date(2025, 6, 15),
        )
    )
    assert len(df) == 5
```

- [ ] **Step 2: Run the tests -- confirm they fail (method does not exist)**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_polygon_options.py -x -q 2>&1 | head -20
```

### Task 2: PolygonProvider.fetch_option_chain() -- Implementation

**Files:**
- Edit: `coordinator/services/data_providers/polygon.py`

- [ ] **Step 1: Add the fetch_option_chain method to PolygonProvider**

Add this method after `fetch_bars()` in `coordinator/services/data_providers/polygon.py`:

```python
    async def fetch_option_chain(
        self,
        underlying: str,
        expiration: date,
        as_of_date: date,
        on_status: StatusCallback | None = None,
    ) -> "pd.DataFrame":
        """Fetch historical options chain for a given underlying + expiration date.

        Uses two Polygon endpoints:
        1. /v3/reference/options/contracts — discover all contracts for the
           underlying with the given expiration.
        2. /v3/snapshot/options/{underlying} — get quotes, greeks, OI for
           each discovered contract as of ``as_of_date``.

        Returns a DataFrame with one row per contract and columns:
            ticker, strike, option_type, bid, ask, last, volume,
            open_interest, implied_volatility, delta, gamma, theta, vega
        """
        import pandas as _pd

        # --- Step 1: Discover contracts via reference endpoint ---
        contracts: list[dict] = []
        url = f"{self.BASE_URL}/v3/reference/options/contracts"
        params = {
            "apiKey": self._api_key,
            "underlying_ticker": underlying,
            "expiration_date": expiration.isoformat(),
            "limit": 1000,
            "order": "asc",
            "sort": "strike_price",
        }
        # If as_of_date is in the past, use it so Polygon returns contracts
        # that existed on that date (some may have been delisted since).
        if as_of_date < date.today():
            params["as_of"] = as_of_date.isoformat()

        while True:
            await self._safe_status(on_status, f"Fetching contracts for {underlying} {expiration}")
            response = await self._request_with_retry(url, params, on_status=on_status)
            data = response.json()
            results = data.get("results") or []
            contracts.extend(results)
            next_url = data.get("next_url")
            if not next_url:
                break
            url = next_url
            params = {"apiKey": self._api_key}

        if not contracts:
            return _pd.DataFrame()

        # --- Step 2: Get snapshot quotes/greeks for discovered contracts ---
        # The snapshot endpoint returns all contracts for the underlying in one call.
        # We filter to the expiration we care about.
        tickers = {c["ticker"] for c in contracts}

        snapshot_url = f"{self.BASE_URL}/v3/snapshot/options/{underlying}"
        snapshot_params = {
            "apiKey": self._api_key,
            "expiration_date": expiration.isoformat(),
            "limit": 250,
        }

        snapshot_results: list[dict] = []
        while True:
            await self._safe_status(
                on_status, f"Fetching snapshot for {underlying} {expiration} ({len(snapshot_results)} so far)"
            )
            resp = await self._request_with_retry(snapshot_url, snapshot_params, on_status=on_status)
            snap_data = resp.json()
            for r in snap_data.get("results") or []:
                details = r.get("details", {})
                if details.get("ticker") in tickers:
                    snapshot_results.append(r)
            snap_next = snap_data.get("next_url")
            if not snap_next:
                break
            snapshot_url = snap_next
            snapshot_params = {"apiKey": self._api_key}

        # --- Step 3: Merge into a DataFrame ---
        rows: list[dict] = []
        snap_by_ticker = {r["details"]["ticker"]: r for r in snapshot_results if "details" in r}

        for c in contracts:
            ticker = c["ticker"]
            snap = snap_by_ticker.get(ticker, {})
            quote = snap.get("last_quote", {})
            day = snap.get("day", {})
            greeks = snap.get("greeks", {})
            rows.append({
                "ticker": ticker,
                "strike": c["strike_price"],
                "option_type": c["contract_type"],
                "bid": quote.get("bid", 0.0),
                "ask": quote.get("ask", 0.0),
                "last": day.get("close", 0.0),
                "volume": day.get("volume", 0),
                "open_interest": snap.get("open_interest", 0),
                "implied_volatility": snap.get("implied_volatility", 0.0),
                "delta": greeks.get("delta", 0.0),
                "gamma": greeks.get("gamma", 0.0),
                "theta": greeks.get("theta", 0.0),
                "vega": greeks.get("vega", 0.0),
            })

        return _pd.DataFrame(rows)
```

- [ ] **Step 2: Add `from datetime import date` import if not already present (it is -- verify)**

The file already imports `date` at line 4. Confirmed.

- [ ] **Step 3: Run the tests to confirm they pass**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_polygon_options.py -x -q
```

### Task 3: DataService Options Chain Storage

**Files:**
- Edit: `coordinator/services/data_service.py`
- Create: `tests/coordinator/services/test_data_service_options.py`

- [ ] **Step 1: Write failing tests for save/load option chain**

```python
# tests/coordinator/services/test_data_service_options.py
"""Tests for DataService option chain save/load."""
import pytest
import tempfile
import os
import pandas as pd
from datetime import date
from coordinator.services.data_service import DataService


@pytest.fixture
def data_service(tmp_path):
    return DataService(
        market_data_dir=str(tmp_path / "market"),
        custom_data_dir=str(tmp_path / "custom"),
    )


def _sample_chain_df():
    return pd.DataFrame([
        {"ticker": "O:SPY250620C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.1, "ask": 5.3, "last": 5.2, "volume": 1200,
         "open_interest": 8000, "implied_volatility": 0.25,
         "delta": 0.55, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
        {"ticker": "O:SPY250620P00450000", "strike": 450.0, "option_type": "put",
         "bid": 4.1, "ask": 4.3, "last": 4.2, "volume": 900,
         "open_interest": 6000, "implied_volatility": 0.27,
         "delta": -0.45, "gamma": 0.03, "theta": -0.04, "vega": 0.11},
    ])


def test_option_chain_path(data_service):
    path = data_service.option_chain_path("polygon", "SPY", date(2025, 6, 20))
    assert path.endswith("polygon/SPY/options/2025-06-20/chain.parquet")


def test_save_and_load_option_chain(data_service):
    df = _sample_chain_df()
    data_service.save_option_chain("polygon", "SPY", date(2025, 6, 20), df)
    loaded = data_service.load_option_chain("polygon", "SPY", date(2025, 6, 20))
    assert loaded is not None
    assert len(loaded) == 2
    assert set(loaded.columns) == set(df.columns)


def test_load_option_chain_missing_returns_none(data_service):
    result = data_service.load_option_chain("polygon", "SPY", date(2025, 6, 20))
    assert result is None


def test_list_option_chain_expirations(data_service):
    df = _sample_chain_df()
    data_service.save_option_chain("polygon", "SPY", date(2025, 6, 20), df)
    data_service.save_option_chain("polygon", "SPY", date(2025, 7, 18), df)

    expirations = data_service.list_option_chain_expirations("polygon", "SPY")
    assert sorted(expirations) == [date(2025, 6, 20), date(2025, 7, 18)]
```

- [ ] **Step 2: Implement option chain storage methods in DataService**

Add these methods to `coordinator/services/data_service.py`:

```python
    def option_chain_path(self, provider: str, symbol: str, expiration: date) -> str:
        """Path for an options chain snapshot: data/market/{provider}/{symbol}/options/{expiration}/chain.parquet"""
        return os.path.join(
            self._market_dir, provider, symbol, "options",
            expiration.isoformat(), "chain.parquet",
        )

    def save_option_chain(self, provider: str, symbol: str, expiration: date, df: pd.DataFrame) -> str:
        """Persist an options chain snapshot to parquet."""
        path = self.option_chain_path(provider, symbol, expiration)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_parquet(path, index=False)
        return path

    def load_option_chain(self, provider: str, symbol: str, expiration: date) -> Optional[pd.DataFrame]:
        """Load an options chain snapshot from disk. Returns None if not found."""
        path = self.option_chain_path(provider, symbol, expiration)
        if not os.path.exists(path):
            return None
        return pd.read_parquet(path)

    def list_option_chain_expirations(self, provider: str, symbol: str) -> list[date]:
        """List all cached expiration dates for a given underlying."""
        from datetime import date as _date
        options_dir = os.path.join(self._market_dir, provider, symbol, "options")
        if not os.path.isdir(options_dir):
            return []
        expirations = []
        for name in os.listdir(options_dir):
            chain_path = os.path.join(options_dir, name, "chain.parquet")
            if os.path.exists(chain_path):
                try:
                    expirations.append(_date.fromisoformat(name))
                except ValueError:
                    continue
        return sorted(expirations)
```

Also add `from datetime import date` to the imports at the top of `data_service.py` (it currently imports only from `typing`).

- [ ] **Step 3: Run the tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_data_service_options.py -x -q
```

---

## Sub-project 2: BacktestTickContext.option_chain() Implementation

### Task 4: Option Chain Loader in BacktestTickContext -- Tests

**Files:**
- Edit: `tests/coordinator/services/test_backtest_tick_context.py`

- [ ] **Step 1: Add option chain tests to the existing test file**

Append these tests to `tests/coordinator/services/test_backtest_tick_context.py`:

```python
# ---- option_chain tests ----

from datetime import date
from sdk.models import OptionChain, OptionContract


def _make_mock_data_service_with_chains():
    """Mock DataService that returns option chain DataFrames."""
    import pandas as _pd

    chains = {
        ("polygon", "SPY", date(2026, 1, 17)): _pd.DataFrame([
            {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
             "bid": 5.1, "ask": 5.3, "last": 5.2, "volume": 1200,
             "open_interest": 8000, "implied_volatility": 0.25,
             "delta": 0.55, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
            {"ticker": "O:SPY260117P00450000", "strike": 450.0, "option_type": "put",
             "bid": 4.1, "ask": 4.3, "last": 4.2, "volume": 900,
             "open_interest": 6000, "implied_volatility": 0.27,
             "delta": -0.45, "gamma": 0.03, "theta": -0.04, "vega": 0.11},
        ]),
    }

    class MockDS:
        def load_market_data(self, src, sym, tf):
            return None
        def load_option_chain(self, provider, symbol, expiration):
            return chains.get((provider, symbol, expiration))
        def list_option_chain_expirations(self, provider, symbol):
            return [exp for (p, s, exp) in chains if p == provider and s == symbol]

    return MockDS()


def test_option_chain_returns_populated_chain():
    """option_chain() returns real data when chain parquet exists on disk."""
    ds = _make_mock_data_service_with_chains()
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=100_000.0,
        data_service=ds, default_source="polygon",
    )
    ctx.set_sim_time(datetime(2026, 1, 17, 12, 0, tzinfo=timezone.utc))

    chain = ctx.option_chain("SPY", expiration=date(2026, 1, 17))
    assert isinstance(chain, OptionChain)
    assert chain.underlying == "SPY"
    assert chain.expiration == date(2026, 1, 17)
    assert len(chain.calls) == 1
    assert len(chain.puts) == 1
    assert chain.calls[0].strike == 450.0
    assert chain.calls[0].bid == 5.1


def test_option_chain_returns_empty_when_no_data():
    """option_chain() returns empty chain when no data exists."""
    mock_ds = type("DS", (), {
        "load_market_data": lambda self, s, sym, tf: None,
        "load_option_chain": lambda self, p, s, e: None,
        "list_option_chain_expirations": lambda self, p, s: [],
    })()
    ctx = BacktestTickContext(
        bars={}, positions={}, cash=100_000.0,
        data_service=mock_ds, default_source="polygon",
    )
    ctx.set_sim_time(datetime(2026, 1, 15, tzinfo=timezone.utc))

    chain = ctx.option_chain("SPY", expiration=date(2026, 1, 17))
    assert isinstance(chain, OptionChain)
    assert chain.calls == []
    assert chain.puts == []


def test_option_chain_no_lookahead():
    """option_chain() must not return data from future expirations / future dates."""
    import pandas as _pd

    # Chain data is for Jan 17 but sim time is Jan 10
    chains = {
        ("polygon", "SPY", date(2026, 1, 17)): _pd.DataFrame([
            {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
             "bid": 5.1, "ask": 5.3, "last": 5.2, "volume": 1200,
             "open_interest": 8000, "implied_volatility": 0.25,
             "delta": 0.55, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
        ]),
    }

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chains.get((p, s, e))
        def list_option_chain_expirations(self, p, s):
            return [exp for (pr, sy, exp) in chains if pr == p and sy == s]

    ctx = BacktestTickContext(
        bars={}, positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    # Sim time is Jan 10 -- requesting expiration Jan 17 is allowed
    # (the expiration is in the future, but the chain data represents
    # what was available on or before sim_time). This should work.
    ctx.set_sim_time(datetime(2026, 1, 10, tzinfo=timezone.utc))
    chain = ctx.option_chain("SPY", expiration=date(2026, 1, 17))
    # Chain data should be returned since the algorithm can see
    # available expirations; the data itself is a snapshot.
    assert isinstance(chain, OptionChain)


def test_option_chain_caches_loaded_data():
    """Repeated calls should not re-read from disk."""
    call_count = [0]

    import pandas as _pd
    chain_df = _pd.DataFrame([
        {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.1, "ask": 5.3, "last": 5.2, "volume": 1200,
         "open_interest": 8000, "implied_volatility": 0.25,
         "delta": 0.55, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
    ])

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e):
            call_count[0] += 1
            return chain_df
        def list_option_chain_expirations(self, p, s): return [date(2026, 1, 17)]

    ctx = BacktestTickContext(
        bars={}, positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx.set_sim_time(datetime(2026, 1, 17, 12, 0, tzinfo=timezone.utc))

    ctx.option_chain("SPY", expiration=date(2026, 1, 17))
    ctx.option_chain("SPY", expiration=date(2026, 1, 17))  # second call

    assert call_count[0] == 1, "DataService.load_option_chain should be called only once (cached)"
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_tick_context.py -k "option_chain" -x -q 2>&1 | head -20
```

### Task 5: BacktestTickContext.option_chain() -- Implementation

**Files:**
- Edit: `coordinator/services/backtest_tick_context.py`

- [ ] **Step 1: Add the option chain cache dict to `__init__`**

In `BacktestTickContext.__init__`, add after `self._custom_data_cache`:

```python
        # Cached option chain DataFrames: (source, symbol, expiration) -> pd.DataFrame
        self._option_chain_cache: dict[tuple, pd.DataFrame] = {}
```

- [ ] **Step 2: Replace the empty-chain stub with real chain lookup**

Replace the existing `option_chain` method (lines 278-284) with:

```python
    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        """Return the option chain for ``symbol`` at ``expiration``.

        Loads chain data from the DataService (parquet on disk) and caches it
        in memory.  If no data exists, returns an empty OptionChain gracefully.
        """
        from sdk.models import OptionContract

        exp = expiration or (self._sim_time_now.date() if self._sim_time_now else date.today())
        source = self._default_source or "polygon"

        # Check in-memory cache first
        cache_key = (source, symbol, exp)
        if cache_key in self._option_chain_cache:
            df = self._option_chain_cache[cache_key]
        elif self._data_service is not None and hasattr(self._data_service, "load_option_chain"):
            df = self._data_service.load_option_chain(source, symbol, exp)
            if df is not None and not df.empty:
                self._option_chain_cache[cache_key] = df
            else:
                # Cache the miss so we don't re-read on every tick
                self._option_chain_cache[cache_key] = pd.DataFrame()
                df = pd.DataFrame()
        else:
            df = pd.DataFrame()

        if df is None or df.empty:
            return OptionChain(underlying=symbol, expiration=exp, calls=[], puts=[])

        # Build OptionContract objects from the DataFrame rows
        calls: list[OptionContract] = []
        puts: list[OptionContract] = []
        for _, row in df.iterrows():
            contract = OptionContract(
                symbol=str(row.get("ticker", "")),
                underlying=symbol,
                expiration=exp,
                strike=float(row.get("strike", 0)),
                option_type=str(row.get("option_type", "")),
                bid=float(row.get("bid", 0)),
                ask=float(row.get("ask", 0)),
                last=float(row.get("last", 0)),
                volume=int(row.get("volume", 0)),
                open_interest=int(row.get("open_interest", 0)),
                implied_volatility=float(row.get("implied_volatility", 0)),
            )
            if contract.option_type == "call":
                calls.append(contract)
            elif contract.option_type == "put":
                puts.append(contract)

        return OptionChain(underlying=symbol, expiration=exp, calls=calls, puts=puts)
```

- [ ] **Step 3: Run all option chain tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_tick_context.py -x -q
```

### Task 6: Nearest-Expiration Fallback

**Files:**
- Edit: `coordinator/services/backtest_tick_context.py`
- Edit: `tests/coordinator/services/test_backtest_tick_context.py`

- [ ] **Step 1: Add test for nearest-expiration logic**

When an algorithm requests `option_chain("SPY")` with no specific expiration, or requests an expiration that doesn't exist on disk, find the nearest available expiration. Append to the test file:

```python
def test_option_chain_finds_nearest_expiration():
    """When requested expiration is not on disk, find the nearest one."""
    import pandas as _pd

    # Only Jan 17 data exists on disk
    chain_df = _pd.DataFrame([
        {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.1, "ask": 5.3, "last": 5.2, "volume": 1200,
         "open_interest": 8000, "implied_volatility": 0.25,
         "delta": 0.55, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
    ])

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e):
            if e == date(2026, 1, 17):
                return chain_df
            return None
        def list_option_chain_expirations(self, p, s):
            return [date(2026, 1, 17)]

    ctx = BacktestTickContext(
        bars={}, positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    ctx.set_sim_time(datetime(2026, 1, 10, tzinfo=timezone.utc))

    # Request Jan 20 -- should fall back to the nearest available (Jan 17)
    chain = ctx.option_chain("SPY", expiration=date(2026, 1, 20))
    assert len(chain.calls) == 1
    assert chain.expiration == date(2026, 1, 17)
```

- [ ] **Step 2: Add nearest-expiration fallback to option_chain()**

In `BacktestTickContext.option_chain()`, after the initial `load_option_chain` returns None, add this fallback block before the final "return empty" path:

```python
        # Fallback: if exact expiration not found, try nearest available
        if (df is None or df.empty) and self._data_service is not None and hasattr(self._data_service, "list_option_chain_expirations"):
            available = self._data_service.list_option_chain_expirations(source, symbol)
            if available:
                nearest = min(available, key=lambda d: abs((d - exp).days))
                if abs((nearest - exp).days) <= 7:  # within 1 week tolerance
                    fallback_key = (source, symbol, nearest)
                    if fallback_key not in self._option_chain_cache:
                        fb_df = self._data_service.load_option_chain(source, symbol, nearest)
                        self._option_chain_cache[fallback_key] = fb_df if fb_df is not None else pd.DataFrame()
                    df = self._option_chain_cache[fallback_key]
                    exp = nearest  # Use the actual expiration for the OptionChain object
```

- [ ] **Step 3: Run the tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_tick_context.py -k "option_chain" -x -q
```

---

## Sub-project 3: Options Fill Model

### Task 7: Options Fill Model -- Tests

**Files:**
- Edit: `tests/coordinator/services/test_backtest_engine.py`

- [ ] **Step 1: Add options fill tests**

Append these tests to `tests/coordinator/services/test_backtest_engine.py`:

```python
def test_options_fill_uses_contract_bid_ask():
    """Options legs fill at contract mid price, not underlying OHLCV open."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType
    from sdk.models import OptionContract, OptionChain

    class OptionsBuyAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:SPY260117C00450000",
                signal_type=SignalType.BUY, quantity=1,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 5, opens=[450, 452, 455, 460, 465])

    # Build option chain data for the context
    import pandas as _pd
    chain_df = _pd.DataFrame([
        {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.0, "ask": 5.4, "last": 5.2, "volume": 1000,
         "open_interest": 5000, "implied_volatility": 0.25,
         "delta": 0.5, "gamma": 0.02, "theta": -0.03, "vega": 0.1},
    ])

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return []

    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={}, cash=10_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=OptionsBuyAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0),
        buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 1
    fill = obs.fills[0]
    # Fill should be at the ask price (worst case for buyer), not at underlying open
    assert fill.fill_price == pytest.approx(5.4, abs=0.01)
    assert fill.asset_type == "options"
    # Cash deducted = ask * quantity * 100 (contract multiplier)
    assert fill.symbol == "O:SPY260117C00450000"


def test_options_position_uses_contract_multiplier():
    """Options PnL uses 100x multiplier: (sell - buy) * qty * 100."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType

    class BuyThenSellOptionsAlgo:
        def __init__(self): self._step = 0
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            self._step += 1
            if self._step == 1:
                return [Signal(legs=[SignalLeg(
                    symbol="O:SPY260117C00450000",
                    signal_type=SignalType.BUY, quantity=2,
                    asset_type="options", order_type=OrderType.MARKET,
                )])]
            elif self._step == 3:
                return [Signal(legs=[SignalLeg(
                    symbol="O:SPY260117C00450000",
                    signal_type=SignalType.SELL, quantity=2,
                    asset_type="options", order_type=OrderType.MARKET,
                )])]
            return []
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 6, opens=[450]*6)

    import pandas as _pd
    chain_df = _pd.DataFrame([
        {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.0, "ask": 5.4, "last": 5.2, "volume": 1000,
         "open_interest": 5000, "implied_volatility": 0.25,
         "delta": 0.5, "gamma": 0.02, "theta": -0.03, "vega": 0.1},
    ])

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return []

    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={}, cash=10_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=BuyThenSellOptionsAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0),
        buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 2
    buy_fill = obs.fills[0]
    sell_fill = obs.fills[1]
    # Buy at ask = 5.4, Sell at bid = 5.0
    # PnL = (5.0 - 5.4) * 2 * 100 = -80
    assert sell_fill.realized_pnl is not None
    assert sell_fill.realized_pnl == pytest.approx(-80.0, abs=1.0)
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -k "options_fill or options_position" -x -q 2>&1 | head -20
```

### Task 8: Options Contract Price Lookup Helper

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`

- [ ] **Step 1: Add helper method to look up options contract price from chain data**

Add this method to `BacktestEngine` class, before `_try_fill`:

```python
    def _lookup_option_price(self, contract_symbol: str, side: str, ctx: BacktestTickContext) -> Optional[float]:
        """Look up the bid/ask for an options contract from cached chain data.

        For buys, returns the ask (worst case). For sells, returns the bid.
        Searches all cached option chains in the context.
        Returns None if the contract is not found.
        """
        for key, df in ctx._option_chain_cache.items():
            if df is None or df.empty:
                continue
            match = df[df["ticker"] == contract_symbol]
            if not match.empty:
                row = match.iloc[0]
                if side == "buy":
                    return float(row.get("ask", 0))
                else:
                    return float(row.get("bid", 0))
        return None
```

- [ ] **Step 2: Verify the helper compiles by running existing engine tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -k "test_market_order" -x -q
```

### Task 9: Options Fill Path in _fill_market and _apply_fill

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`

- [ ] **Step 1: Modify _fill_market to handle options legs**

In `_fill_market`, add an early check for options after the `leg = po.leg` line:

```python
    def _fill_market(self, po, bar, side, slippage, fees_list, rng, sim_time, ctx=None) -> FillRecord:
        leg = po.leg

        # Options-specific fill: use contract bid/ask, not bar OHLCV
        if leg.asset_type == "options" and ctx is not None:
            option_price = self._lookup_option_price(leg.symbol, side, ctx)
            if option_price is not None and option_price > 0:
                # Apply slippage on top of bid/ask
                if slippage.market_bps > 0:
                    sign = 1 if side == "buy" else -1
                    option_price += option_price * (slippage.market_bps / 10000) * sign
                fees, breakdown = self._compute_fees(leg, option_price, fees_list, order_type=OrderType.MARKET)
                return FillRecord(
                    timestamp=bar["timestamp"].to_pydatetime(), symbol=leg.symbol,
                    asset_type="options", side=side, quantity=leg.quantity,
                    requested_price=option_price, fill_price=option_price,
                    slippage_dollars=0.0, slippage_bps_applied=slippage.market_bps,
                    fees=fees, fee_breakdown=breakdown, signal_id=po.signal_id,
                )

        # Original equity path (unchanged)
        if slippage.use_bar_range:
            ...  # (keep existing code)
```

- [ ] **Step 2: Update all call sites of _fill_market to pass ctx**

In `_try_fill`, modify the `_fill_market` calls to include `ctx`:

```python
    def _try_fill(
        self, po: _PendingOrder, *, bar, slippage: SlippageModel,
        buy_fees, sell_fees, cash, positions, rng, sim_time, ctx=None,
    ) -> tuple[Optional[FillRecord], bool]:
```

And in the MARKET/stop-triggered branch:

```python
        if ot == OrderType.MARKET or po.is_stop_triggered:
            return self._fill_market(po, bar, side, slippage, fees_list, rng, sim_time, ctx=ctx), False
```

In the `_run_internal` method, pass `ctx` to `_try_fill`:

```python
                fill, advance_for_stop = self._try_fill(
                    po, bar=fill_bar, slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                    cash=cash, positions=positions, rng=rng, sim_time=bar["timestamp"].to_pydatetime(),
                    ctx=ctx,
                )
```

- [ ] **Step 3: Modify _apply_fill for options contract multiplier**

In `_apply_fill`, after `key = (fill.symbol,)`, add the multiplier logic:

```python
    def _apply_fill(self, cash: float, positions: dict, fill: FillRecord) -> float:
        key = (fill.symbol,)
        ps = positions.get(key) or _PositionState(asset_type=fill.asset_type)

        # Options use 100x contract multiplier for cash impact
        multiplier = 100 if fill.asset_type == "options" else 1
        notional = fill.fill_price * fill.quantity * multiplier

        if fill.side == "buy":
            total_qty = ps.quantity + fill.quantity
            if total_qty == 0:
                ps.avg_price = 0.0
            else:
                ps.avg_price = (ps.avg_price * ps.quantity + fill.fill_price * fill.quantity) / total_qty
            ps.quantity = total_qty
            cash -= notional + fill.fees
        else:  # sell
            realized = (fill.fill_price - ps.avg_price) * fill.quantity * multiplier - fill.fees
            fill.realized_pnl = realized
            ps.quantity -= fill.quantity
            if ps.quantity == 0:
                ps.avg_price = 0.0
            cash += notional - fill.fees
        positions[key] = ps
        if ps.quantity == 0:
            del positions[key]
        return cash
```

- [ ] **Step 4: Update MTM valuation to handle options positions**

In `_positions_market_value_from_cache` and `_price_cache_for_bar`, the position value for options needs the 100x multiplier. Update `_positions_market_value_from_cache`:

```python
    def _positions_market_value_from_cache(self, positions: dict, price_cache: dict[str, float]) -> float:
        total = 0.0
        for (sym,), ps in positions.items():
            multiplier = 100 if ps.asset_type == "options" else 1
            total += ps.quantity * price_cache.get(sym, 0.0) * multiplier
        return total
```

Similarly update `_positions_snapshot_from_cache`:

```python
    def _positions_snapshot_from_cache(self, positions: dict, price_cache: dict[str, float]) -> list[dict]:
        return [
            {"symbol": k[0], "quantity": ps.quantity, "avg_price": ps.avg_price,
             "current_price": price_cache.get(k[0], 0.0),
             "market_value": ps.quantity * price_cache.get(k[0], 0.0) * (100 if ps.asset_type == "options" else 1),
             "asset_type": ps.asset_type}
            for k, ps in positions.items()
        ]
```

- [ ] **Step 5: Run all engine tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -x -q
```

### Task 10: Options Price Lookup for MTM

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`

- [ ] **Step 1: Handle options symbols in _lookup_symbol_close_fast**

Options contracts won't have their own bar data in `ctx._bars`. For MTM, use the chain cache:

```python
    def _lookup_symbol_close_fast(self, sym: str, sim_time, ctx, fallback_bar) -> float:
        """O(log N) price lookup using pre-built nanosecond index via searchsorted."""
        import numpy as np

        # Options contracts: look up mid price from chain cache
        if ctx is not None and hasattr(ctx, "_option_chain_cache"):
            for key, df in ctx._option_chain_cache.items():
                if df is None or df.empty:
                    continue
                match = df[df["ticker"] == sym]
                if not match.empty:
                    row = match.iloc[0]
                    return (float(row.get("bid", 0)) + float(row.get("ask", 0))) / 2

        if ctx is not None:
            for (src, s, tf), df in ctx._bars.items():
                if s == sym and not df.empty:
                    # ... (keep existing code)
```

- [ ] **Step 2: Run the full engine test suite including options tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -x -q
```

### Task 11: Buying Power Check for Options

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`

- [ ] **Step 1: Update buying power check in _run_internal for options multiplier**

In the `_run_internal` method, the buying power check for buys must account for the 100x multiplier:

```python
                    if fill.side == "buy":
                        multiplier = 100 if fill.asset_type == "options" else 1
                        notional_plus_fees = fill.fill_price * fill.quantity * multiplier + fill.fees
                        if notional_plus_fees > cash + 1e-6:
                            observer.on_signal_rejected(
                                sim_time,
                                Signal(legs=[po.leg]),
                                f"insufficient_buying_power: order needs "
                                f"${notional_plus_fees:,.2f} but cash is ${cash:,.2f}",
                            )
                            continue
```

- [ ] **Step 2: Add test for buying power rejection with contract multiplier**

Append to `tests/coordinator/services/test_backtest_engine.py`:

```python
def test_options_buying_power_accounts_for_multiplier():
    """Options buy of 1 contract at $5.40 ask needs $540 (5.40 * 1 * 100), not $5.40."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType

    class OptionsBuyAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:SPY260117C00450000",
                signal_type=SignalType.BUY, quantity=1,
                asset_type="options", order_type=OrderType.MARKET,
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 3, opens=[450]*3)

    import pandas as _pd
    chain_df = _pd.DataFrame([
        {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.0, "ask": 5.4, "last": 5.2, "volume": 1000,
         "open_interest": 5000, "implied_volatility": 0.25,
         "delta": 0.5, "gamma": 0.02, "theta": -0.03, "vega": 0.1},
    ])

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return []

    # $400 cash is NOT enough for 1 contract at $5.40 * 100 = $540
    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={}, cash=400.0,
        data_service=MockDS(), default_source="polygon",
    )
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=OptionsBuyAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0),
        buy_fees=[], sell_fees=[],
        initial_cash=400.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 0
    assert len(obs.rejected) == 1
    assert "insufficient_buying_power" in obs.rejected[0][2]
```

- [ ] **Step 3: Run all engine tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -x -q
```

---

## Sub-project 4: Manifest & Data Pipeline Integration

### Task 12: Pre-download Option Chains in BacktestRunner

**Files:**
- Edit: `coordinator/services/backtest_runner.py`

- [ ] **Step 1: Add options chain download logic to Stage 1**

After the existing data dependency download loop in `BacktestRunner.run()`, add option chain downloading. Insert after the `for dep in deps:` loop (around line 259):

```python
            # Stage 1b: download option chain data for options algorithms
            options_underlyings = set()
            for dep in deps:
                asset_class = dep.get("asset_class", "equities")
                if asset_class == "options":
                    options_underlyings.add(dep.get("symbol", ""))
            # Also check requirements.asset_types
            if "options" in (manifest.requirements.asset_types or []):
                # For all equity symbols, also download their options chains
                for dep in deps:
                    symbol = dep.get("symbol")
                    if symbol:
                        options_underlyings.add(symbol)

            options_underlyings.discard("")
            if options_underlyings:
                await self._download_option_chains(
                    underlyings=list(options_underlyings),
                    date_start=date_range_start,
                    date_end=date_range_end,
                    run_id=run_id,
                )
```

- [ ] **Step 2: Add the _download_option_chains helper method**

Add to `BacktestRunner`:

```python
    async def _download_option_chains(
        self,
        underlyings: list[str],
        date_start,
        date_end,
        run_id: str,
    ) -> None:
        """Download monthly option chain snapshots for each underlying across the date range.

        For efficiency, downloads one chain snapshot per monthly expiration (3rd Friday)
        that falls within the date range. Uses the Polygon provider directly.
        """
        from datetime import date, timedelta
        from coordinator.database.models import BacktestRun

        provider = self._dm._providers.get("polygon") if hasattr(self._dm, "_providers") else None
        if provider is None:
            logger.warning("No polygon provider available for options chain download")
            return

        for underlying in underlyings:
            # Find monthly expirations (approximate: 3rd Friday of each month)
            start_d = date_start.date() if hasattr(date_start, "date") else date_start
            end_d = date_end.date() if hasattr(date_end, "date") else date_end

            # Generate monthly expirations within the range
            expirations = self._monthly_expirations(start_d, end_d)

            for exp in expirations:
                # Skip if already cached
                existing = self._ds.load_option_chain("polygon", underlying, exp)
                if existing is not None and not existing.empty:
                    continue

                async with self._sf() as session:
                    r = (await session.execute(
                        select(BacktestRun).where(BacktestRun.id == run_id)
                    )).scalar_one()
                    r.progress_message = f"Downloading options chain: {underlying} exp {exp}"
                    await session.commit()

                try:
                    # Download the chain as of 1 day before expiration
                    as_of = exp - timedelta(days=1)
                    df = await provider.fetch_option_chain(
                        underlying=underlying,
                        expiration=exp,
                        as_of_date=as_of,
                    )
                    if df is not None and not df.empty:
                        self._ds.save_option_chain("polygon", underlying, exp, df)
                        logger.info(
                            "Downloaded %d contracts for %s exp %s",
                            len(df), underlying, exp,
                        )
                except Exception:
                    logger.exception(
                        "Failed to download option chain for %s exp %s; continuing",
                        underlying, exp,
                    )

    @staticmethod
    def _monthly_expirations(start: date, end: date) -> list[date]:
        """Generate 3rd-Friday-of-month expiration dates within [start, end]."""
        from datetime import date, timedelta
        import calendar
        expirations = []
        current = start.replace(day=1)
        while current <= end:
            # Find the 3rd Friday of this month
            cal = calendar.monthcalendar(current.year, current.month)
            # Each week is [Mon..Sun]; Friday = index 4
            fridays = [week[4] for week in cal if week[4] != 0]
            if len(fridays) >= 3:
                third_friday = date(current.year, current.month, fridays[2])
                if start <= third_friday <= end:
                    expirations.append(third_friday)
            # Advance to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        return expirations
```

- [ ] **Step 3: Pass data_service to context with option chain support**

The `BacktestTickContext` already receives `data_service=self._ds` in the runner. Verify that the DataService instance passed has the new `load_option_chain` and `list_option_chain_expirations` methods (they were added in Task 3). No code change needed here -- just verify.

### Task 13: Monthly Expirations Helper -- Tests

**Files:**
- Create: `tests/coordinator/services/test_backtest_runner_options.py`

- [ ] **Step 1: Write tests for the monthly expirations helper**

```python
# tests/coordinator/services/test_backtest_runner_options.py
"""Tests for BacktestRunner options-related helpers."""
import pytest
from datetime import date
from coordinator.services.backtest_runner import BacktestRunner


def test_monthly_expirations_basic():
    """Generate 3rd Fridays within a date range."""
    exps = BacktestRunner._monthly_expirations(date(2025, 1, 1), date(2025, 3, 31))
    # Jan 2025: 3rd Friday = Jan 17
    # Feb 2025: 3rd Friday = Feb 21
    # Mar 2025: 3rd Friday = Mar 21
    assert len(exps) == 3
    assert date(2025, 1, 17) in exps
    assert date(2025, 2, 21) in exps
    assert date(2025, 3, 21) in exps


def test_monthly_expirations_partial_month():
    """If start/end cut through a month, only include if 3rd Friday is in range."""
    exps = BacktestRunner._monthly_expirations(date(2025, 1, 20), date(2025, 2, 20))
    # Jan 17 is before start (excluded), Feb 21 is after end (excluded)
    assert len(exps) == 0


def test_monthly_expirations_single_month():
    exps = BacktestRunner._monthly_expirations(date(2025, 6, 1), date(2025, 6, 30))
    # June 2025: 3rd Friday = June 20
    assert len(exps) == 1
    assert exps[0] == date(2025, 6, 20)
```

- [ ] **Step 2: Run tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_runner_options.py -x -q
```

### Task 14: Manifest asset_class: options Handling

**Files:**
- Edit: `coordinator/services/backtest_runner.py`
- Edit: `tests/coordinator/services/test_backtest_runner_options.py`

- [ ] **Step 1: Verify existing manifest parsing handles asset_class: options**

The manifest parser at `sdk/manifest.py` already parses `asset_class` from the `assets:` block (line 141: `"asset_class": a.get("asset_class", "equities")`). The backtest runner reads `manifest.assets` at line 198 and uses them as deps. Verify that the existing flow works for options assets by adding a test:

```python
def test_manifest_assets_with_options_class():
    """Verify that manifest parsing preserves asset_class: options."""
    from sdk.manifest import QuiltManifest
    manifest = QuiltManifest.from_string("""
name: test-options-algo
type: algorithm
version: 1.0.0
entry_point: algorithm.py
class_name: TestAlgo
trigger: bar:1day
requirements:
  asset_types:
    - options
    - equities
  options_level: 1
  data_dependencies:
    - symbol: SPY
      timeframe: 1day
assets:
  - symbol: SPY
    asset_class: options
    timeframe: 1day
""")
    assert len(manifest.assets) == 1
    assert manifest.assets[0]["asset_class"] == "options"
    assert manifest.assets[0]["symbol"] == "SPY"
    assert manifest.requirements.options_level == 1
    assert "options" in manifest.requirements.asset_types
```

- [ ] **Step 2: Ensure asset_class flows through to options chain download**

The runner at line 198 reads `manifest.assets or manifest.requirements.data_dependencies`. The `_download_option_chains` method (added in Task 12) checks `dep.get("asset_class")` against `"options"`. The `assets` list entries include `asset_class` from the manifest parser. This completes the loop -- no additional code needed beyond what was added in Task 12.

- [ ] **Step 3: Run the test**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_runner_options.py -x -q
```

### Task 15: Options Limit Fill Path

**Files:**
- Edit: `coordinator/services/backtest_engine_v2.py`
- Edit: `tests/coordinator/services/test_backtest_engine.py`

- [ ] **Step 1: Add options handling to _fill_limit**

Options limit orders should also use contract bid/ask as the market reference. Modify `_fill_limit` to use option prices for range checks:

```python
    def _fill_limit(self, po, bar, side, slippage, fees_list, sim_time, ctx=None) -> Optional[FillRecord]:
        leg = po.leg
        limit = leg.limit_price
        if limit is None:
            return None

        # Options: use contract bid/ask for cross check
        if leg.asset_type == "options" and ctx is not None:
            option_price = self._lookup_option_price(leg.symbol, side, ctx)
            if option_price is not None:
                # For buy limit: fill if ask <= limit (we can buy at or below our limit)
                # For sell limit: fill if bid >= limit (we can sell at or above our limit)
                if side == "buy" and option_price <= limit:
                    fill_price = min(option_price, limit)
                elif side == "sell" and option_price >= limit:
                    fill_price = max(option_price, limit)
                else:
                    return None  # Limit not met

                fees, breakdown = self._compute_fees(leg, fill_price, fees_list, order_type=OrderType.LIMIT)
                return FillRecord(
                    timestamp=bar["timestamp"].to_pydatetime(), symbol=leg.symbol,
                    asset_type="options", side=side, quantity=leg.quantity,
                    requested_price=limit, fill_price=fill_price,
                    slippage_dollars=0.0, slippage_bps_applied=0.0,
                    fees=fees, fee_breakdown=breakdown, signal_id=po.signal_id,
                )

        # Original equity path
        low, high = float(bar["low"]), float(bar["high"])
        ...  # (keep existing code)
```

Update `_try_fill` to pass `ctx` to `_fill_limit`:

```python
        if ot == OrderType.LIMIT:
            return self._fill_limit(po, bar, side, slippage, fees_list, sim_time, ctx=ctx), False
```

- [ ] **Step 2: Add test for options limit order**

```python
def test_options_limit_order_fill():
    """Options limit buy fills when ask is at or below limit price."""
    from sdk.signals import Signal, SignalLeg, SignalType, OrderType

    class LimitOptionsAlgo:
        def __init__(self): self._fired = False
        def on_start(self, c, s): pass
        def on_tick(self, ctx):
            if self._fired: return []
            self._fired = True
            return [Signal(legs=[SignalLeg(
                symbol="O:SPY260117C00450000",
                signal_type=SignalType.BUY, quantity=1,
                asset_type="options", order_type=OrderType.LIMIT,
                limit_price=6.0,  # Willing to pay up to $6
            )])]
        def on_stop(self): return {}
        def save_state(self): return {}

    clock = _bars("2024-01-01", 3, opens=[450]*3)

    import pandas as _pd
    chain_df = _pd.DataFrame([
        {"ticker": "O:SPY260117C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.0, "ask": 5.4, "last": 5.2, "volume": 1000,
         "open_interest": 5000, "implied_volatility": 0.25,
         "delta": 0.5, "gamma": 0.02, "theta": -0.03, "vega": 0.1},
    ])

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e): return chain_df
        def list_option_chain_expirations(self, p, s): return []

    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={}, cash=10_000.0,
        data_service=MockDS(), default_source="polygon",
    )
    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=LimitOptionsAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0),
        buy_fees=[], sell_fees=[],
        initial_cash=10_000.0, observer=obs, cancel_token=CancelToken(),
    )
    assert obs.error is None
    assert len(obs.fills) == 1
    # Ask = 5.4 which is below limit of 6.0, so fills at 5.4 (actual ask)
    assert obs.fills[0].fill_price == pytest.approx(5.4, abs=0.01)
```

- [ ] **Step 3: Run all engine tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py -x -q
```

### Task 16: Populate Option Chain Cache Before Engine Run

**Files:**
- Edit: `coordinator/services/backtest_runner.py`

- [ ] **Step 1: Pre-populate the context's option chain cache before engine.run()**

After building the `BacktestTickContext` and before running the engine, warm the option chain cache for all downloaded expirations. Add after `ctx = BacktestTickContext(...)` (around line 333):

```python
            # Pre-warm option chain cache for options algorithms
            if options_underlyings:
                for underlying in options_underlyings:
                    expirations = self._ds.list_option_chain_expirations("polygon", underlying)
                    for exp in expirations:
                        chain_df = self._ds.load_option_chain("polygon", underlying, exp)
                        if chain_df is not None and not chain_df.empty:
                            ctx._option_chain_cache[("polygon", underlying, exp)] = chain_df
                logger.info(
                    "Pre-warmed %d option chain snapshots into context cache",
                    len(ctx._option_chain_cache),
                )
```

- [ ] **Step 2: Verify no regressions in existing backtest runner tests**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_runner.py -x -q
```

### Task 17: End-to-End Options Backtest Integration Test

**Files:**
- Create: `tests/coordinator/services/test_options_backtest_e2e.py`

- [ ] **Step 1: Write an integration test that simulates a full options backtest flow**

```python
# tests/coordinator/services/test_options_backtest_e2e.py
"""End-to-end test: options algorithm backtest with real chain data and fill model."""
import pytest
import pandas as pd
from datetime import date, datetime, timezone
from coordinator.services.backtest_engine_v2 import (
    BacktestEngine, CancelToken, FillRecord, EngineSummary,
)
from coordinator.services.backtest_tick_context import BacktestTickContext
from coordinator.services.backtest_config import SlippageModel, TradingFee
from sdk.signals import Signal, SignalLeg, SignalType, OrderType
from sdk.models import OptionChain, OptionContract


class RecordingObserver:
    def __init__(self):
        self.fills = []
        self.rejected = []
        self.equity = []
        self.complete = False
        self.error = None
    def on_tick(self, sim_time, ctx_snapshot): pass
    def on_signals_emitted(self, sim_time, signals): pass
    def on_fill(self, fill): self.fills.append(fill)
    def on_signal_rejected(self, sim_time, signal, reason): self.rejected.append((sim_time, signal, reason))
    def on_equity_point(self, sim_time, pv, cash, positions):
        self.equity.append({"sim_time": sim_time, "pv": pv, "cash": cash, "positions": positions})
    def on_complete(self, summary): self.complete = True; self.summary = summary
    def on_error(self, exc): self.error = exc


class SimpleOptionAlgo:
    """Buy an ATM call on tick 1, sell it on tick 3."""
    def __init__(self): self._step = 0
    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx):
        self._step += 1
        if self._step == 1:
            # Look up the chain to find a contract
            chain = ctx.option_chain("SPY", expiration=date(2025, 6, 20))
            if chain.calls:
                contract = chain.calls[0]
                return [Signal.simple(
                    symbol=contract.symbol,
                    signal_type=SignalType.BUY,
                    quantity=1,
                    asset_type="options",
                    order_type=OrderType.MARKET,
                    reasoning="Buy ATM call",
                )]
        elif self._step == 3:
            if ctx.positions:
                sym = list(ctx.positions.keys())[0]
                pos = ctx.positions[sym]
                return [Signal.simple(
                    symbol=sym,
                    signal_type=SignalType.SELL,
                    quantity=pos.quantity,
                    asset_type="options",
                    order_type=OrderType.MARKET,
                    reasoning="Close position",
                )]
        return []
    def on_stop(self): return {}
    def save_state(self): return {}


def test_e2e_options_backtest():
    """Full lifecycle: algorithm calls option_chain(), buys a contract, sells it."""
    clock = pd.DataFrame({
        "timestamp": pd.date_range("2025-06-10", periods=6, freq="D", tz="UTC"),
        "open":  [450.0] * 6,
        "high":  [452.0] * 6,
        "low":   [448.0] * 6,
        "close": [451.0] * 6,
        "volume": [1_000_000] * 6,
    })

    chain_df = pd.DataFrame([
        {"ticker": "O:SPY250620C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.0, "ask": 5.4, "last": 5.2, "volume": 1200,
         "open_interest": 8000, "implied_volatility": 0.25,
         "delta": 0.55, "gamma": 0.03, "theta": -0.05, "vega": 0.12},
        {"ticker": "O:SPY250620P00450000", "strike": 450.0, "option_type": "put",
         "bid": 4.0, "ask": 4.4, "last": 4.2, "volume": 800,
         "open_interest": 6000, "implied_volatility": 0.27,
         "delta": -0.45, "gamma": 0.03, "theta": -0.04, "vega": 0.11},
    ])

    class MockDS:
        def load_market_data(self, s, sym, tf): return None
        def load_option_chain(self, p, s, e):
            if s == "SPY" and e == date(2025, 6, 20):
                return chain_df
            return None
        def list_option_chain_expirations(self, p, s):
            return [date(2025, 6, 20)] if s == "SPY" else []

    ctx = BacktestTickContext(
        bars={("polygon", "SPY", "1day"): clock},
        positions={}, cash=100_000.0,
        data_service=MockDS(), default_source="polygon",
    )

    obs = RecordingObserver()
    BacktestEngine().run(
        algorithm=SimpleOptionAlgo(), ctx=ctx, clock_series=clock,
        clock_timeframe="1day", clock_source="polygon", clock_symbol="SPY",
        slippage=SlippageModel(market_bps=0),
        buy_fees=[], sell_fees=[],
        initial_cash=100_000.0, observer=obs, cancel_token=CancelToken(),
    )

    assert obs.error is None
    assert obs.complete
    assert len(obs.fills) == 2

    buy_fill = obs.fills[0]
    sell_fill = obs.fills[1]

    # Buy fills at ask ($5.40), sell fills at bid ($5.00)
    assert buy_fill.fill_price == pytest.approx(5.4, abs=0.01)
    assert buy_fill.asset_type == "options"
    assert sell_fill.fill_price == pytest.approx(5.0, abs=0.01)

    # Cash impact: buy cost = 5.4 * 1 * 100 = $540; sell proceeds = 5.0 * 1 * 100 = $500
    # Net loss = $40
    expected_final_cash = 100_000.0 - 540.0 + 500.0  # = $99,960
    assert obs.summary.final_cash == pytest.approx(expected_final_cash, abs=1.0)

    # Realized PnL on the sell
    assert sell_fill.realized_pnl == pytest.approx(-40.0, abs=1.0)
```

- [ ] **Step 2: Run the e2e test**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_options_backtest_e2e.py -x -v
```

### Task 18: Existing Test Suite Regression Check

**Files:** None (verification only)

- [ ] **Step 1: Run the full backtest test suite to verify no regressions**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/services/test_backtest_engine.py tests/coordinator/services/test_backtest_tick_context.py tests/coordinator/services/test_backtest_runner.py -x -q
```

- [ ] **Step 2: Run all tests to check for broader regressions**

```bash
cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -x -q --timeout=60 2>&1 | tail -20
```

---

## Summary of Files Changed

| File | Action | Sub-project |
|------|--------|-------------|
| `coordinator/services/data_providers/polygon.py` | Edit: add `fetch_option_chain()` | 1 |
| `coordinator/services/data_service.py` | Edit: add `option_chain_path()`, `save_option_chain()`, `load_option_chain()`, `list_option_chain_expirations()` | 1 |
| `coordinator/services/backtest_tick_context.py` | Edit: replace empty-chain stub, add cache, add nearest-expiration fallback | 2 |
| `coordinator/services/backtest_engine_v2.py` | Edit: add `_lookup_option_price()`, modify `_fill_market()`, `_fill_limit()`, `_try_fill()`, `_apply_fill()`, `_lookup_symbol_close_fast()`, MTM methods | 3 |
| `coordinator/services/backtest_runner.py` | Edit: add `_download_option_chains()`, `_monthly_expirations()`, pre-warm cache | 4 |
| `tests/coordinator/services/test_polygon_options.py` | Create | 1 |
| `tests/coordinator/services/test_data_service_options.py` | Create | 1 |
| `tests/coordinator/services/test_backtest_tick_context.py` | Edit: add option chain tests | 2 |
| `tests/coordinator/services/test_backtest_engine.py` | Edit: add options fill/multiplier/limit tests | 3 |
| `tests/coordinator/services/test_backtest_runner_options.py` | Create | 4 |
| `tests/coordinator/services/test_options_backtest_e2e.py` | Create | 4 |

## Risks & Future Work

- **Data volume:** A single underlying like SPY can have 1000+ contracts per expiration. The parquet storage handles this well, but downloading chains for daily expirations (0DTE/1DTE strategies) across multi-year backtests will be slow. Consider a "lazy download on first `option_chain()` call" mode in a follow-up.
- **Greeks evolution:** This v1 stores a single snapshot per expiration. Real Greeks change daily. A follow-up could store daily chain snapshots for more accurate delta-hedging backtests.
- **Exercise/assignment:** Not modeled in v1. Options positions simply open and close via buy/sell signals. Exercise at expiration is a follow-up.
- **Multi-leg spread fills:** v1 fills each leg independently. Spread-level net debit/credit fills (using `Signal.net_debit_limit`) are deferred.
- **Per-contract OHLCV bars:** For intraday options backtests, we'd need per-contract bar data from Polygon's `/v2/aggs/ticker/{optionTicker}/range/...` endpoint. This is deferred to a follow-up since it requires significantly more data storage and API calls.
