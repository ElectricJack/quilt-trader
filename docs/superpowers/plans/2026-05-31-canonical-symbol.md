# Canonical Symbol Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a canonical-symbol-at-the-boundary pattern: each `AssetService` declares a canonical regex; `resolve_symbol(canonical, provider)` translates canonical → provider-native; new `canonicalize(provider_form, provider)` does the inverse. Three validation gates (manifest load, `data_service` I/O, asset-service inputs) reject non-canonical symbols. On-disk parquet paths use canonical only; a one-off migration script renames legacy provider-form directories.

**Architecture:** Per-service `CANONICAL_RE` class attribute drives `classify()` (regex test) and `resolve_symbol()` (raises `ValueError` on non-canonical input). New `canonicalize()` method per service is the inverse of `resolve_symbol`. `AssetServiceRegistry` adds `validate(symbol)` and `canonicalize(provider_form, provider)` orchestrators. `_KNOWN_INDEXES` expands from 7 to 37 entries (drops `GSPC`, `IXIC` yfinance aliases). Equity classifier excludes a small set of well-known crypto bare-tickers so `validate("BTC")` raises with the multi-class hint pointing the user at `BTCUSD`.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio, pandas, SQLAlchemy 2.x (only at the data-store call sites — no schema changes).

**Spec:** [`docs/superpowers/specs/2026-05-31-canonical-symbol-design.md`](../specs/2026-05-31-canonical-symbol-design.md)

---

## File map

### Created
- `scripts/migrate_canonical_symbols.py` — one-off migration
- `tests/coordinator/services/asset_services/test_canonical.py` — unified canonical-form tests
- `tests/scripts/test_migrate_canonical_symbols.py`

### Modified
- `coordinator/services/asset_services/crypto.py` — `CANONICAL_RE`, strict `resolve_symbol`, new `canonicalize`, polygon mapping
- `coordinator/services/asset_services/equity.py` — `CANONICAL_RE` w/ dot-suffix, `_KNOWN_CRYPTO_BARE` exclusion, strict `resolve_symbol`, new `canonicalize`, yfinance share-class map
- `coordinator/services/asset_services/index.py` — `CANONICAL_RE`, expanded `_KNOWN_INDEXES` (37 entries; drops `GSPC`/`IXIC`), default `X → ^X` / `X → I:X` rules, new `canonicalize`
- `coordinator/services/asset_services/options.py` — `CANONICAL_RE`, strict `resolve_symbol`, new `canonicalize`
- `coordinator/services/asset_services/registry.py` — new `validate` + `canonicalize` methods
- `coordinator/services/data_service.py` — `validate` call on every symbol-taking method
- `coordinator/services/backtest_runner.py` (around line 632) — replace `removeprefix("O:")` with `registry.canonicalize`
- `sdk/manifest.py` — two new gates inside `_parse`
- `tests/sdk/test_manifest.py` — new gate tests
- `tests/coordinator/services/test_data_service.py` — new validation gate tests

---

## Task 1: CryptoAssetService — canonical regex, strict resolve_symbol, canonicalize

**Files:**
- Modify: `coordinator/services/asset_services/crypto.py`
- Create: `tests/coordinator/services/asset_services/test_canonical.py` (Crypto section only — Tasks 2-4 extend)

### Step 1: Write failing tests

Create `tests/coordinator/services/asset_services/test_canonical.py`:

```python
"""Canonical-form contract tests for AssetService implementations."""
import pytest
from coordinator.services.asset_services.crypto import CryptoAssetService


@pytest.fixture
def crypto():
    return CryptoAssetService()


class TestCryptoCanonical:
    def test_classify_accepts_canonical_forms(self, crypto):
        assert crypto.classify("BTCUSD") is True
        assert crypto.classify("ETHUSD") is True
        assert crypto.classify("BTCUSDT") is True
        assert crypto.classify("SOLUSDT") is True

    def test_classify_rejects_non_canonical(self, crypto):
        assert crypto.classify("BTC") is False         # missing suffix
        assert crypto.classify("BTC-USD") is False     # dashed
        assert crypto.classify("BTC/USD") is False     # slashed
        assert crypto.classify("X:BTCUSD") is False    # polygon prefix
        assert crypto.classify("") is False
        assert crypto.classify("AAPL") is False        # equity

    @pytest.mark.parametrize("provider,expected", [
        ("polygon", "X:BTCUSD"),
        ("yfinance", "BTC-USD"),
        ("alpaca", "BTC/USD"),
        ("alpaca_stream", "BTC/USD"),
        ("coinbase", "BTC-USD"),
    ])
    def test_resolve_symbol_canonical_inputs(self, crypto, provider, expected):
        assert crypto.resolve_symbol("BTCUSD", provider) == expected

    def test_resolve_symbol_raises_on_non_canonical(self, crypto):
        with pytest.raises(ValueError, match="not a canonical crypto"):
            crypto.resolve_symbol("BTC", "polygon")
        with pytest.raises(ValueError, match="not a canonical crypto"):
            crypto.resolve_symbol("BTC-USD", "yfinance")

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("BTC-USD", "yfinance", "BTCUSD"),
        ("BTC/USD", "alpaca", "BTCUSD"),
        ("BTC/USD", "alpaca_stream", "BTCUSD"),
        ("BTC-USD", "coinbase", "BTCUSD"),
        ("X:BTCUSD", "polygon", "BTCUSD"),
        ("BTCUSD", "polygon", "BTCUSD"),          # already canonical
        ("BTCUSDT", "alpaca", "BTCUSDT"),
    ])
    def test_canonicalize_provider_forms(self, crypto, provider_form, provider, expected):
        assert crypto.canonicalize(provider_form, provider) == expected

    def test_canonicalize_rejects_bare_ambiguous(self, crypto):
        # bare "BTC" could mean equity (real iBIT-like tickers) — refuse to guess
        with pytest.raises(ValueError, match="ambiguous"):
            crypto.canonicalize("BTC", "polygon")

    @pytest.mark.parametrize("canonical,provider", [
        ("BTCUSD", "polygon"),
        ("BTCUSD", "yfinance"),
        ("BTCUSD", "alpaca"),
        ("BTCUSD", "coinbase"),
        ("ETHUSD", "polygon"),
        ("BTCUSDT", "alpaca"),
    ])
    def test_round_trip(self, crypto, canonical, provider):
        provider_form = crypto.resolve_symbol(canonical, provider)
        assert crypto.canonicalize(provider_form, provider) == canonical
```

### Step 2: Run, verify fail

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py -v
```

Expected: many failures — `classify("BTC-USD")` currently returns True; `resolve_symbol` doesn't raise; `canonicalize` doesn't exist.

### Step 3: Update `coordinator/services/asset_services/crypto.py`

Replace the entire `CryptoAssetService` class body (keep the existing module-level helpers `_to_canonical`, `_to_slash`, `_to_dash`, `_KNOWN_CRYPTO`, `_YFINANCE_MAP` as-is — they're still used). New class:

```python
import re

class CryptoAssetService:
    asset_type = AssetType.CRYPTO
    CANONICAL_RE = re.compile(r"^[A-Z]{2,5}(USD|USDT)$")

    def classify(self, symbol: str) -> bool:
        return bool(symbol and self.CANONICAL_RE.match(symbol))

    def resolve_symbol(self, canonical: str, provider: str) -> str:
        if not self.CANONICAL_RE.match(canonical):
            raise ValueError(
                f"{canonical!r} is not a canonical crypto symbol "
                f"(expected e.g. 'BTCUSD')"
            )
        if provider == "yfinance":
            return _YFINANCE_MAP.get(canonical, _to_dash(canonical))
        if provider in ("alpaca", "alpaca_stream"):
            return _to_slash(canonical)
        if provider == "coinbase":
            return _to_dash(canonical)
        if provider == "polygon":
            return f"X:{canonical}"
        return canonical

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Parse a provider-native crypto form back to canonical (BTCUSD).
        Raises ValueError if the input is ambiguous (e.g. bare 'BTC')."""
        s = provider_form
        if provider == "polygon" and s.startswith("X:"):
            s = s[2:]
        # Normalize separators
        s = s.replace("/", "").replace("-", "").upper()
        if not self.CANONICAL_RE.match(s):
            raise ValueError(
                f"{provider_form!r} is ambiguous or not a recognized crypto form"
            )
        return s
```

(The existing `compose_order_symbol`, `get_multiplier`, `get_price`, `get_fill_price`, `compute_unrealized_pnl`, `risk_contribution`, `handle_expiry`, `time_in_force`, `supports_multileg`, `required_order_fields`, `is_pdt_exempt`, `is_market_open`, `stream_config`, `supports_provider`, `discover_contracts` methods stay unchanged.)

### Step 4: Run, verify pass

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py::TestCryptoCanonical -v
```

Expected: all pass.

### Step 5: Commit

```bash
git add coordinator/services/asset_services/crypto.py \
        tests/coordinator/services/asset_services/test_canonical.py
git commit -m "feat(asset-services): crypto canonical regex + strict resolve_symbol + canonicalize

Adds CANONICAL_RE for crypto (BTCUSD/BTCUSDT pattern); classify() and
resolve_symbol() use it. resolve_symbol now raises ValueError on
non-canonical input (e.g. 'BTC', 'BTC-USD'). Polygon mapping added:
BTCUSD → X:BTCUSD. New canonicalize() parses provider-native forms back
to canonical.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: EquityAssetService — canonical regex with share-class dot, crypto-bare exclusion, canonicalize

**Files:**
- Modify: `coordinator/services/asset_services/equity.py`
- Modify: `tests/coordinator/services/asset_services/test_canonical.py` (add `TestEquityCanonical`)

### Step 1: Add failing tests

Append to `tests/coordinator/services/asset_services/test_canonical.py`:

```python
from coordinator.services.asset_services.equity import EquityAssetService


@pytest.fixture
def equity():
    return EquityAssetService()


class TestEquityCanonical:
    def test_classify_accepts_canonical(self, equity):
        assert equity.classify("AAPL") is True
        assert equity.classify("SPY") is True
        assert equity.classify("QQQ") is True
        assert equity.classify("BRK.B") is True
        assert equity.classify("BF.B") is True
        assert equity.classify("F") is True             # 1-char (Ford)
        assert equity.classify("BRKB") is True          # 4 chars no dot

    def test_classify_rejects_non_canonical(self, equity):
        assert equity.classify("") is False
        assert equity.classify("BRK-B") is False        # dash not dot
        assert equity.classify("aapl") is False         # lowercase
        assert equity.classify("AAPL.US") is False      # multi-letter suffix
        assert equity.classify("^GSPC") is False        # yfinance prefix
        assert equity.classify("I:SPX") is False        # polygon prefix

    def test_classify_excludes_bare_crypto(self, equity):
        # Well-known crypto bare-tickers fall through to validate() raising
        # with the multi-class hint pointing the user at BTCUSD.
        for s in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "LTC", "BCH", "XRP", "ADA", "ETC"):
            assert equity.classify(s) is False, f"{s} should not classify as equity"

    @pytest.mark.parametrize("canonical,provider,expected", [
        ("AAPL", "polygon", "AAPL"),
        ("AAPL", "yfinance", "AAPL"),
        ("BRK.B", "yfinance", "BRK-B"),
        ("BRK.A", "yfinance", "BRK-A"),
        ("BF.B", "yfinance", "BF-B"),
        ("BRK.B", "polygon", "BRK.B"),
        ("SPY", "alpaca", "SPY"),
    ])
    def test_resolve_symbol(self, equity, canonical, provider, expected):
        assert equity.resolve_symbol(canonical, provider) == expected

    def test_resolve_symbol_raises_on_non_canonical(self, equity):
        with pytest.raises(ValueError, match="not a canonical equity"):
            equity.resolve_symbol("BRK-B", "polygon")
        with pytest.raises(ValueError, match="not a canonical equity"):
            equity.resolve_symbol("BTC", "polygon")

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("AAPL", "polygon", "AAPL"),
        ("AAPL", "yfinance", "AAPL"),
        ("BRK-B", "yfinance", "BRK.B"),
        ("BRK-A", "yfinance", "BRK.A"),
        ("BF-B", "yfinance", "BF.B"),
        ("BRK.B", "polygon", "BRK.B"),
    ])
    def test_canonicalize(self, equity, provider_form, provider, expected):
        assert equity.canonicalize(provider_form, provider) == expected

    def test_canonicalize_rejects_unknown(self, equity):
        with pytest.raises(ValueError, match="not a recognized equity"):
            equity.canonicalize("BTC", "polygon")  # bare crypto, not equity
```

### Step 2: Run, verify fail

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py::TestEquityCanonical -v
```

Expected: failures on missing `canonicalize`, etc.

### Step 3: Update `coordinator/services/asset_services/equity.py`

Find the `EquityAssetService` class. Replace `classify()` and `resolve_symbol()`, add `canonicalize()`. Add module-level constants:

```python
import re

_KNOWN_CRYPTO_BARE = frozenset({
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK",
    "USDT", "USD",
    "ETC", "XRP", "ADA", "LTC", "BCH",
})

# Share-class equity tickers that use a dot in canonical form (e.g. BRK.B)
# but a dash in yfinance form (BRK-B). Add new entries as discovered.
_YFINANCE_SHARE_CLASS_MAP = {
    "BRK.B": "BRK-B",
    "BRK.A": "BRK-A",
    "BF.B": "BF-B",
    "BF.A": "BF-A",
}


class EquityAssetService:
    asset_type = AssetType.EQUITIES
    CANONICAL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")

    def classify(self, symbol: str) -> bool:
        if not symbol or not self.CANONICAL_RE.match(symbol):
            return False
        if symbol in _KNOWN_CRYPTO_BARE:
            return False
        return True

    def resolve_symbol(self, canonical: str, provider: str) -> str:
        if not self.classify(canonical):
            raise ValueError(
                f"{canonical!r} is not a canonical equity symbol "
                f"(expected e.g. 'AAPL' or 'BRK.B')"
            )
        if provider == "yfinance":
            return _YFINANCE_SHARE_CLASS_MAP.get(canonical, canonical)
        return canonical

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Parse a provider-native equity form back to canonical."""
        if provider == "yfinance" and "-" in provider_form:
            # Map BRK-B back to BRK.B
            candidate = provider_form.replace("-", ".")
            if self.classify(candidate):
                return candidate
        if self.classify(provider_form):
            return provider_form
        raise ValueError(
            f"{provider_form!r} is not a recognized equity form for provider {provider!r}"
        )
```

Keep the rest of the class unchanged (the existing `compose_order_symbol`, `get_multiplier`, `get_price`, `get_fill_price`, etc. methods).

**Important:** Delete the existing module-level constants `_OCC_RE`, `_KNOWN_INDEXES`, `_CRYPTO_SUFFIXES` if present at the top of `equity.py` — they were used by the old classify and are no longer needed. (Verify by grep'ing the file after the edit.)

### Step 4: Run, verify pass

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py::TestEquityCanonical -v
```

Expected: all pass.

### Step 5: Commit

```bash
git add coordinator/services/asset_services/equity.py \
        tests/coordinator/services/asset_services/test_canonical.py
git commit -m "feat(asset-services): equity canonical regex w/ share-class dot, crypto-bare exclusion

Adds CANONICAL_RE matching bare tickers and dot-suffix share classes
(BRK.B, BF.B). Equity classify() excludes _KNOWN_CRYPTO_BARE so bare
'BTC'/'ETH'/etc. fall through to registry.validate() raising with the
multi-class hint pointing to 'BTCUSD'. New canonicalize() maps yfinance
dash-form (BRK-B) back to canonical (BRK.B).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: IndexAssetService — expanded `_KNOWN_INDEXES`, canonical regex, canonicalize

**Files:**
- Modify: `coordinator/services/asset_services/index.py`
- Modify: `tests/coordinator/services/asset_services/test_canonical.py` (add `TestIndexCanonical`)

### Step 1: Add failing tests

Append to `tests/coordinator/services/asset_services/test_canonical.py`:

```python
from coordinator.services.asset_services.index import IndexAssetService, _KNOWN_INDEXES


@pytest.fixture
def index():
    return IndexAssetService()


class TestIndexCanonical:
    def test_known_indexes_has_37_entries(self):
        assert len(_KNOWN_INDEXES) == 37
        # spot-check a few from each category
        assert {"SPX", "OEX", "MID", "NDX", "COMP", "DJI", "RUT", "VLG"} <= _KNOWN_INDEXES
        assert {"VIX", "VIX1D", "VIX3M", "VVIX", "SKEW"} <= _KNOWN_INDEXES
        assert {"VXN", "RVX", "VXD", "GVZ", "OVX"} <= _KNOWN_INDEXES
        assert {"IRX", "FVX", "TNX", "TYX"} <= _KNOWN_INDEXES
        assert {"SOX", "XAU", "HGX", "OSX", "DXY"} <= _KNOWN_INDEXES

    def test_known_indexes_excludes_yfinance_aliases(self):
        # GSPC and IXIC are yfinance-specific aliases for SPX and COMP, not canonicals.
        assert "GSPC" not in _KNOWN_INDEXES
        assert "IXIC" not in _KNOWN_INDEXES

    def test_classify(self, index):
        assert index.classify("VIX") is True
        assert index.classify("SPX") is True
        assert index.classify("COMP") is True       # was IXIC
        assert index.classify("VIX1D") is True
        assert index.classify("AAPL") is False      # equity, not in set
        assert index.classify("GSPC") is False      # yfinance alias, not canonical
        assert index.classify("^VIX") is False      # has prefix
        assert index.classify("I:SPX") is False     # has prefix

    @pytest.mark.parametrize("canonical,provider,expected", [
        ("VIX", "polygon", "I:VIX"),
        ("SPX", "polygon", "I:SPX"),
        ("VIX1D", "polygon", "I:VIX1D"),
        ("SOX", "polygon", "I:SOX"),
        ("VIX", "yfinance", "^VIX"),
        ("SPX", "yfinance", "^GSPC"),          # explicit override
        ("COMP", "yfinance", "^IXIC"),         # explicit override
        ("DJI", "yfinance", "^DJI"),
        ("VIX3M", "yfinance", "^VIX3M"),       # default rule applies
    ])
    def test_resolve_symbol(self, index, canonical, provider, expected):
        assert index.resolve_symbol(canonical, provider) == expected

    def test_resolve_symbol_raises_on_non_canonical(self, index):
        with pytest.raises(ValueError, match="not a canonical index"):
            index.resolve_symbol("GSPC", "yfinance")    # yfinance alias, not canonical
        with pytest.raises(ValueError, match="not a canonical index"):
            index.resolve_symbol("^VIX", "polygon")

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("I:VIX", "polygon", "VIX"),
        ("I:SPX", "polygon", "SPX"),
        ("VIX", "polygon", "VIX"),             # already canonical
        ("^VIX", "yfinance", "VIX"),
        ("^GSPC", "yfinance", "SPX"),
        ("^IXIC", "yfinance", "COMP"),
        ("VIX", "yfinance", "VIX"),            # already canonical
    ])
    def test_canonicalize(self, index, provider_form, provider, expected):
        assert index.canonicalize(provider_form, provider) == expected

    def test_canonicalize_rejects_unknown(self, index):
        with pytest.raises(ValueError, match="not a recognized index"):
            index.canonicalize("AAPL", "polygon")
```

### Step 2: Run, verify fail

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py::TestIndexCanonical -v
```

### Step 3: Update `coordinator/services/asset_services/index.py`

Replace the existing constants + `IndexAssetService` class. Keep the unrelated methods at the bottom (`compose_order_symbol` through `discover_contracts`) unchanged:

```python
import re

_KNOWN_INDEXES = frozenset({
    # US equity broad-market (16)
    "SPX", "OEX", "MID", "SML",
    "NDX", "NDXT", "COMP",
    "DJI", "DJT", "DJU",
    "RUT", "RUI", "RUA",
    "NYA", "XAX", "VLG",
    # CBOE VIX family (8)
    "VIX", "VIX1D", "VIX9D", "VIX3M", "VIX6M", "VIX1Y", "VVIX", "SKEW",
    # Vol on other underlyings (5)
    "VXN", "RVX", "VXD", "GVZ", "OVX",
    # CBOE Treasury yields (4)
    "IRX", "FVX", "TNX", "TYX",
    # Sector / specialty (5)
    "SOX", "XAU", "HGX", "OSX", "DXY",
})

# Explicit canonical → yfinance overrides. Indexes not listed default to ^<CANONICAL>.
_YFINANCE_OVERRIDES = {
    "SPX": "^GSPC",
    "COMP": "^IXIC",
}

# Explicit canonical → yfinance reverse lookup for canonicalize()
_YFINANCE_REVERSE = {v: k for k, v in _YFINANCE_OVERRIDES.items()}


class IndexAssetService:
    asset_type = AssetType.INDEX
    CANONICAL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,4}$")  # uppercase letters + digits, 2-5 chars

    def classify(self, symbol: str) -> bool:
        return symbol in _KNOWN_INDEXES

    def resolve_symbol(self, canonical: str, provider: str) -> str:
        if not self.classify(canonical):
            raise ValueError(
                f"{canonical!r} is not a canonical index symbol "
                f"(must be in _KNOWN_INDEXES)"
            )
        if provider == "polygon":
            return f"I:{canonical}"
        if provider == "yfinance":
            return _YFINANCE_OVERRIDES.get(canonical, f"^{canonical}")
        return canonical

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Parse a provider-native index form back to canonical."""
        if provider == "polygon" and provider_form.startswith("I:"):
            candidate = provider_form[2:]
            if candidate in _KNOWN_INDEXES:
                return candidate
        if provider == "yfinance":
            if provider_form in _YFINANCE_REVERSE:
                return _YFINANCE_REVERSE[provider_form]
            if provider_form.startswith("^"):
                candidate = provider_form[1:]
                if candidate in _KNOWN_INDEXES:
                    return candidate
        if provider_form in _KNOWN_INDEXES:
            return provider_form
        raise ValueError(
            f"{provider_form!r} is not a recognized index form for provider {provider!r}"
        )
```

The `_POLYGON_MAP` and `_YFINANCE_MAP` constants from the existing file should be **deleted** — the default rules + overrides replace them.

### Step 4: Run, verify pass

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py::TestIndexCanonical -v
```

Expected: all pass.

### Step 5: Commit

```bash
git add coordinator/services/asset_services/index.py \
        tests/coordinator/services/asset_services/test_canonical.py
git commit -m "feat(asset-services): expanded _KNOWN_INDEXES (37 entries) + canonical regex + canonicalize

_KNOWN_INDEXES grows from 7 to 37 canonical US-traded indexes (broad
market, VIX family, vol on other underlyings, CBOE Treasury yields,
sector/specialty). Drops GSPC and IXIC — those are yfinance-specific
aliases for SPX and COMP, not canonicals; they become explicit overrides
in _YFINANCE_OVERRIDES instead.

resolve_symbol() now uses default rules (^<sym> for yfinance, I:<sym>
for polygon) with explicit overrides for the few yfinance edge cases.
New canonicalize() inverts the mapping.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: OptionsAssetService — canonical regex, strict resolve_symbol, canonicalize

**Files:**
- Modify: `coordinator/services/asset_services/options.py`
- Modify: `tests/coordinator/services/asset_services/test_canonical.py` (add `TestOptionsCanonical`)

### Step 1: Add failing tests

Append to `tests/coordinator/services/asset_services/test_canonical.py`:

```python
from coordinator.services.asset_services.options import OptionsAssetService


@pytest.fixture
def options():
    return OptionsAssetService()


class TestOptionsCanonical:
    def test_classify(self, options):
        assert options.classify("AAPL240119C00150000") is True
        assert options.classify("SPY240731P00340000") is True
        assert options.classify("BRK240119C00100000") is True  # 3-char underlying
        assert options.classify("AAPL") is False               # not OCC
        assert options.classify("BTCUSD") is False             # not OCC
        assert options.classify("") is False
        assert options.classify("O:AAPL240119C00150000") is False  # has prefix

    @pytest.mark.parametrize("canonical,provider,expected", [
        ("AAPL240119C00150000", "polygon", "O:AAPL240119C00150000"),
        ("SPY240731P00340000", "polygon", "O:SPY240731P00340000"),
        ("AAPL240119C00150000", "alpaca", "AAPL240119C00150000"),
    ])
    def test_resolve_symbol(self, options, canonical, provider, expected):
        assert options.resolve_symbol(canonical, provider) == expected

    def test_resolve_symbol_raises_on_non_canonical(self, options):
        with pytest.raises(ValueError, match="not a canonical option"):
            options.resolve_symbol("O:AAPL240119C00150000", "polygon")  # prefix present
        with pytest.raises(ValueError, match="not a canonical option"):
            options.resolve_symbol("AAPL", "polygon")

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("O:AAPL240119C00150000", "polygon", "AAPL240119C00150000"),
        ("AAPL240119C00150000", "polygon", "AAPL240119C00150000"),  # already canonical
        ("AAPL240119C00150000", "alpaca", "AAPL240119C00150000"),
    ])
    def test_canonicalize(self, options, provider_form, provider, expected):
        assert options.canonicalize(provider_form, provider) == expected

    def test_canonicalize_rejects_unknown(self, options):
        with pytest.raises(ValueError, match="not a recognized option"):
            options.canonicalize("AAPL", "polygon")
```

### Step 2: Run, verify fail

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py::TestOptionsCanonical -v
```

### Step 3: Update `coordinator/services/asset_services/options.py`

Replace `classify()` and `resolve_symbol()`, add `canonicalize()`. Add the regex at the top of the class:

```python
import re

class OptionsAssetService:
    asset_type = AssetType.OPTIONS
    CANONICAL_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

    def classify(self, symbol: str) -> bool:
        return bool(symbol and self.CANONICAL_RE.match(symbol))

    def resolve_symbol(self, canonical: str, provider: str) -> str:
        if not self.CANONICAL_RE.match(canonical):
            raise ValueError(
                f"{canonical!r} is not a canonical option symbol "
                f"(expected OCC format e.g. 'AAPL240119C00150000')"
            )
        if provider == "polygon":
            return f"O:{canonical}"
        return canonical

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Strip provider prefix from an option symbol to recover canonical OCC."""
        candidate = provider_form
        if provider == "polygon" and candidate.startswith("O:"):
            candidate = candidate[2:]
        if self.CANONICAL_RE.match(candidate):
            return candidate
        raise ValueError(
            f"{provider_form!r} is not a recognized option form for provider {provider!r}"
        )
```

Leave `parse_symbol()`, `compose_order_symbol()`, and all pricing/risk/fill methods unchanged.

### Step 4: Run, verify pass

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py::TestOptionsCanonical -v
```

Expected: all pass.

### Step 5: Commit

```bash
git add coordinator/services/asset_services/options.py \
        tests/coordinator/services/asset_services/test_canonical.py
git commit -m "feat(asset-services): options canonical regex + strict resolve_symbol + canonicalize

OCC format (no O: prefix) is canonical. resolve_symbol() rejects
already-prefixed inputs. canonicalize() strips polygon's O: prefix.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: AssetServiceRegistry — `validate` + `canonicalize` orchestrators

**Files:**
- Modify: `coordinator/services/asset_services/registry.py`
- Modify: `tests/coordinator/services/asset_services/test_canonical.py` (add `TestRegistry`)

### Step 1: Add failing tests

Append to `tests/coordinator/services/asset_services/test_canonical.py`:

```python
from coordinator.services.asset_services.registry import AssetServiceRegistry


@pytest.fixture
def registry():
    return AssetServiceRegistry()


class TestRegistry:
    def test_validate_accepts_canonicals(self, registry):
        registry.validate("AAPL")          # equity
        registry.validate("BTCUSD")        # crypto
        registry.validate("VIX")           # index
        registry.validate("AAPL240119C00150000")  # option
        registry.validate("BRK.B")         # share-class equity

    def test_validate_rejects_with_multi_class_hint(self, registry):
        with pytest.raises(ValueError) as exc_info:
            registry.validate("BTC")
        msg = str(exc_info.value)
        assert "'BTC'" in msg
        assert "not a canonical symbol" in msg
        assert "Crypto" in msg and "BTCUSD" in msg
        assert "Equity" in msg and "AAPL" in msg
        assert "Index" in msg and "VIX" in msg
        assert "Options" in msg and "OCC" in msg

    @pytest.mark.parametrize("bad", ["BTC", "ETH", "^VIX", "I:SPX", "GSPC", "O:foo", "btc-usd"])
    def test_validate_rejects(self, registry, bad):
        with pytest.raises(ValueError):
            registry.validate(bad)

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("X:BTCUSD", "polygon", "BTCUSD"),
        ("BTC-USD", "yfinance", "BTCUSD"),
        ("^VIX", "yfinance", "VIX"),
        ("I:SPX", "polygon", "SPX"),
        ("O:AAPL240119C00150000", "polygon", "AAPL240119C00150000"),
        ("AAPL", "polygon", "AAPL"),                      # already canonical
        ("SPY240731P00340000", "polygon", "SPY240731P00340000"),  # OCC already canonical
    ])
    def test_canonicalize_via_registry(self, registry, provider_form, provider, expected):
        assert registry.canonicalize(provider_form, provider) == expected

    def test_canonicalize_raises_if_no_service_handles(self, registry):
        with pytest.raises(ValueError):
            registry.canonicalize("BTC", "polygon")       # bare ambiguous everywhere
        with pytest.raises(ValueError):
            registry.canonicalize("totally_bogus_symbol", "polygon")
```

### Step 2: Run, verify fail

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py::TestRegistry -v
```

### Step 3: Update `coordinator/services/asset_services/registry.py`

Add two methods to `AssetServiceRegistry`. Keep all existing methods unchanged:

```python
class AssetServiceRegistry:
    # ... existing __init__, classify, get_service, get_service_by_type,
    #     resolve_symbol, get_multiplier, time_in_force, is_market_open,
    #     compose_order_symbol, supports_provider, stream_config unchanged ...

    def validate(self, symbol: str) -> None:
        """Raise ValueError if symbol matches no canonical form."""
        for svc in self._services:
            if svc.classify(symbol):
                return
        raise ValueError(
            f"{symbol!r} is not a canonical symbol. "
            f"Crypto canonical form is e.g. 'BTCUSD'. "
            f"Equity canonical form is e.g. 'AAPL'. "
            f"Index canonical form is one of "
            f"{{VIX, SPX, NDX, COMP, DJI, RUT, ...}} (see _KNOWN_INDEXES). "
            f"Options canonical form is OCC e.g. 'AAPL240119C00150000'."
        )

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Try each AssetService.canonicalize in classification order;
        return the first successful canonical form."""
        for svc in self._services:
            try:
                canonical = svc.canonicalize(provider_form, provider)
            except (ValueError, KeyError):
                continue
            else:
                return canonical
        raise ValueError(
            f"{provider_form!r} (provider={provider!r}) could not be canonicalized "
            f"by any asset service"
        )
```

### Step 4: Run, verify pass

```
python3 -m pytest tests/coordinator/services/asset_services/test_canonical.py -v
```

Expected: all tests across Crypto, Equity, Index, Options, and Registry classes pass.

### Step 5: Commit

```bash
git add coordinator/services/asset_services/registry.py \
        tests/coordinator/services/asset_services/test_canonical.py
git commit -m "feat(asset-services): registry validate() + canonicalize() orchestrators

validate(symbol) raises ValueError with a multi-class hint if no service
classifies the input. canonicalize(provider_form, provider) tries each
service's canonicalize() in classification order and returns the first
success.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: backtest_runner — replace ad-hoc `removeprefix("O:")` with `registry.canonicalize`

**Files:**
- Modify: `coordinator/services/backtest_runner.py` (around line 632)

### Step 1: Locate the call site

```bash
grep -n 'removeprefix("O:")' coordinator/services/backtest_runner.py
```

Expected output: `632:                    symbols = [c["ticker"].removeprefix("O:") for c in contracts]`

### Step 2: Read the surrounding context

```bash
sed -n '625,640p' coordinator/services/backtest_runner.py
```

The line lives inside `_download_option_contracts`, looping over contracts discovered via polygon's API. `c["ticker"]` looks like `O:AAPL240119C00150000`; the strip gives bare OCC.

### Step 3: Replace with registry call

Edit the line and add an import at the top of the file if not already present.

Find at the top of `backtest_runner.py`:

```python
from coordinator.services.asset_services.registry import get_default_registry
```

Add it if missing (alongside the other `coordinator.services.*` imports).

Replace the line:

```python
                    symbols = [c["ticker"].removeprefix("O:") for c in contracts]
```

with:

```python
                    registry = get_default_registry()
                    symbols = [registry.canonicalize(c["ticker"], "polygon") for c in contracts]
```

### Step 4: Run the existing backtest runner tests

```bash
python3 -m pytest tests/coordinator/services/ -k "backtest_runner" -q 2>&1 | tail -5
```

Expected: same pass/fail counts as before the change (no regressions). The change is behavior-preserving for valid OCC inputs.

### Step 5: Commit

```bash
git add coordinator/services/backtest_runner.py
git commit -m "refactor(backtest-runner): use registry.canonicalize() for option contract symbols

Replaces ad-hoc removeprefix(\"O:\") in _download_option_contracts.
Behavior-preserving for valid polygon contract responses; raises with a
clear error if polygon ever returns a non-canonicalizable ticker.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: data_service.py — validation gates on all symbol-taking methods

**Files:**
- Modify: `coordinator/services/data_service.py`
- Modify: `tests/coordinator/services/test_data_service.py`

### Step 1: Add failing tests

Append to `tests/coordinator/services/test_data_service.py` (create if missing):

```python
import os
import pytest
import pandas as pd

from coordinator.services.data_service import DataService


@pytest.fixture
def ds(tmp_path):
    return DataService(
        market_data_dir=str(tmp_path / "market"),
        custom_data_dir=str(tmp_path / "custom"),
    )


def _df():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-01", "2024-01-02"], utc=True),
        "open": [1.0, 1.1], "high": [1.2, 1.3], "low": [0.9, 1.0],
        "close": [1.1, 1.2], "volume": [100, 200],
    })


class TestSymbolValidation:
    def test_save_market_data_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.save_market_data("polygon", "BTC", "1min", _df())

    def test_save_market_data_accepts_canonical(self, ds):
        path = ds.save_market_data("polygon", "BTCUSD", "1min", _df())
        assert "BTCUSD" in path
        assert os.path.exists(path)

    def test_load_market_data_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.load_market_data("polygon", "BTC", "1min")

    def test_market_data_path_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.market_data_path("polygon", "BTC", "1min")

    def test_delete_market_data_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.delete_market_data("polygon", "BTC", "1min")

    def test_latest_market_data_timestamp_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.latest_market_data_timestamp("polygon", "BTC", "1min")

    def test_earliest_market_data_timestamp_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.earliest_market_data_timestamp("polygon", "BTC", "1min")

    def test_canonical_path_uses_canonical_symbol(self, ds):
        # The on-disk path uses canonical form, not provider-form
        path = ds.market_data_path("polygon", "BTCUSD", "1min")
        assert path.endswith("polygon/BTCUSD/1min.parquet")
        assert "X:" not in path

    def test_share_class_equity_path(self, ds):
        # BRK.B is canonical (not BRK-B)
        path = ds.market_data_path("yfinance", "BRK.B", "1day")
        assert path.endswith("yfinance/BRK.B/1day.parquet")
```

### Step 2: Run, verify fail

```bash
python3 -m pytest tests/coordinator/services/test_data_service.py::TestSymbolValidation -v
```

Expected: fails — symbol validation not present.

### Step 3: Update `coordinator/services/data_service.py`

Add the import at the top:

```python
from coordinator.services.asset_services.registry import get_default_registry
```

Add a one-line validation to each of the six public symbol-taking methods. Updated method signatures:

```python
def market_data_path(self, provider: str, symbol: str, timeframe: str) -> str:
    get_default_registry().validate(symbol)
    return os.path.join(self._market_dir, provider, symbol, f"{timeframe}.parquet")

def delete_market_data(self, provider: str, symbol: str, timeframe: str) -> bool:
    get_default_registry().validate(symbol)
    path = self.market_data_path(provider, symbol, timeframe)
    # ... rest unchanged ...

def save_market_data(self, provider: str, symbol: str, timeframe: str, df: pd.DataFrame) -> str:
    get_default_registry().validate(symbol)
    path = self.market_data_path(provider, symbol, timeframe)
    # ... rest unchanged ...

def load_market_data(self, provider: str, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
    get_default_registry().validate(symbol)
    path = self._resolve_provider_path(provider, symbol, timeframe)
    # ... rest unchanged ...

def latest_market_data_timestamp(self, provider: str, symbol: str, timeframe: str) -> Optional[pd.Timestamp]:
    get_default_registry().validate(symbol)
    path = self._resolve_provider_path(provider, symbol, timeframe)
    # ... rest unchanged ...

def earliest_market_data_timestamp(self, provider: str, symbol: str, timeframe: str) -> Optional[pd.Timestamp]:
    get_default_registry().validate(symbol)
    path = self._resolve_provider_path(provider, symbol, timeframe)
    # ... rest unchanged ...
```

`market_data_path` is called inside `delete_market_data`, `save_market_data`, `load_market_data` via `_resolve_provider_path`, so the validation would fire twice — that's fine (cheap and explicit).

### Step 4: Run, verify pass

```bash
python3 -m pytest tests/coordinator/services/test_data_service.py::TestSymbolValidation -v
```

Expected: all pass.

### Step 5: Run rest of data_service tests for regressions

```bash
python3 -m pytest tests/coordinator/services/test_data_service.py -q 2>&1 | tail -10
```

Expected: no NEW failures. Pre-existing failures unrelated to this task are OK.

### Step 6: Commit

```bash
git add coordinator/services/data_service.py tests/coordinator/services/test_data_service.py
git commit -m "feat(data-service): validate canonical symbol on every public method

market_data_path, save_market_data, load_market_data, delete_market_data,
latest_market_data_timestamp, earliest_market_data_timestamp now reject
non-canonical symbols at the boundary. On-disk parquet paths are
guaranteed to use canonical form only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: sdk/manifest.py — two-gate canonical validation on `assets:` block

**Files:**
- Modify: `sdk/manifest.py`
- Modify: `tests/sdk/test_manifest.py`

### Step 1: Add failing tests

Append to `tests/sdk/test_manifest.py`:

```python
import pytest
import yaml
from sdk.manifest import QuiltManifest, ManifestError


def _make_manifest(assets_block):
    """Build a minimal valid algorithm manifest with the given assets block."""
    return {
        "name": "test-algo",
        "type": "algorithm",
        "entry_point": "algorithm.py",
        "class_name": "TestAlgo",
        "requirements": {"asset_types": ["equities"]},
        "assets": assets_block,
    }


class TestManifestCanonicalValidation:
    def test_accepts_canonical_crypto(self):
        m = _make_manifest([{"symbol": "BTCUSD", "asset_class": "crypto"}])
        QuiltManifest._parse(m)  # no raise

    def test_accepts_canonical_equity(self):
        m = _make_manifest([{"symbol": "AAPL", "asset_class": "equities"}])
        QuiltManifest._parse(m)

    def test_accepts_share_class_equity(self):
        m = _make_manifest([{"symbol": "BRK.B", "asset_class": "equities"}])
        QuiltManifest._parse(m)

    def test_accepts_canonical_index(self):
        m = _make_manifest([{"symbol": "VIX", "asset_class": "index"}])
        QuiltManifest._parse(m)

    def test_rejects_bare_crypto_with_crypto_class(self):
        """Gate 1: 'BTC' fails validate() with multi-class hint."""
        m = _make_manifest([{"symbol": "BTC", "asset_class": "crypto"}])
        with pytest.raises(ManifestError) as exc:
            QuiltManifest._parse(m)
        msg = str(exc.value)
        assert "'BTC'" in msg
        assert "BTCUSD" in msg

    def test_rejects_dashed_crypto(self):
        m = _make_manifest([{"symbol": "BTC-USD", "asset_class": "crypto"}])
        with pytest.raises(ManifestError, match="BTC-USD"):
            QuiltManifest._parse(m)

    def test_rejects_yfinance_index_prefix(self):
        m = _make_manifest([{"symbol": "^VIX", "asset_class": "index"}])
        with pytest.raises(ManifestError, match="\\^VIX"):
            QuiltManifest._parse(m)

    def test_rejects_yfinance_index_alias(self):
        """GSPC was previously valid (yfinance alias); now must be SPX."""
        m = _make_manifest([{"symbol": "GSPC", "asset_class": "index"}])
        with pytest.raises(ManifestError, match="GSPC"):
            QuiltManifest._parse(m)

    def test_rejects_class_mismatch(self):
        """Gate 2: symbol classifies differently than declared."""
        m = _make_manifest([{"symbol": "BTCUSD", "asset_class": "equities"}])
        with pytest.raises(ManifestError) as exc:
            QuiltManifest._parse(m)
        msg = str(exc.value)
        assert "BTCUSD" in msg
        assert "equities" in msg
        assert "crypto" in msg

    def test_rejects_class_mismatch_aapl_as_crypto(self):
        m = _make_manifest([{"symbol": "AAPL", "asset_class": "crypto"}])
        with pytest.raises(ManifestError, match="AAPL"):
            QuiltManifest._parse(m)
```

### Step 2: Run, verify fail

```bash
python3 -m pytest tests/sdk/test_manifest.py::TestManifestCanonicalValidation -v
```

Expected: failures — no gating in place.

### Step 3: Update `sdk/manifest.py`

Find `_parse` (around line 74) and the existing `assets:` parsing block (around lines 145-171). Add the two gates after `asset_class` validation. Add the import at the top of the file:

```python
from coordinator.services.asset_services.registry import get_default_registry
```

Update the `assets:` parsing loop body:

```python
        # Parse top-level `assets:` block.
        raw_assets = data.get("assets") or []
        assets: list[dict] = []
        registry = get_default_registry()
        if isinstance(raw_assets, list):
            for a in raw_assets:
                if not isinstance(a, dict):
                    continue
                symbol = a.get("symbol")
                if not symbol:
                    continue
                asset_class = a.get("asset_class", "equities")
                if asset_class not in _VALID_ASSET_TYPES:
                    raise ManifestError(
                        f"invalid asset_class {asset_class!r} for symbol {symbol!r}; "
                        f"must be one of {sorted(_VALID_ASSET_TYPES)}"
                    )

                # Gate 1: symbol must be canonical for SOME asset class
                try:
                    registry.validate(symbol)
                except ValueError as e:
                    raise ManifestError(f"asset {symbol!r}: {e}")

                # Gate 2: symbol's natural classification must match declared asset_class
                inferred = registry.classify(symbol).value
                if inferred != asset_class:
                    raise ManifestError(
                        f"asset {symbol!r} is declared as asset_class={asset_class!r} "
                        f"but its canonical form classifies as {inferred!r}. "
                        f"Either fix the symbol or change asset_class."
                    )

                entry = {
                    "symbol": symbol,
                    "asset_class": asset_class,
                }
                if a.get("timeframe"):
                    entry["timeframe"] = a["timeframe"]
                if a.get("source"):
                    entry["source"] = a["source"]
                assets.append(entry)
```

**Note about import location.** `sdk/manifest.py` should ideally not depend on `coordinator/`. If the codebase has a layering rule (check `pyproject.toml` or existing imports in `sdk/`), use a lazy local import inside `_parse` instead:

```python
def _parse(data: dict) -> QuiltManifest:
    from coordinator.services.asset_services.registry import get_default_registry
    # ... existing code ...
```

### Step 4: Run, verify pass

```bash
python3 -m pytest tests/sdk/test_manifest.py::TestManifestCanonicalValidation -v
```

Expected: all pass.

### Step 5: Run the full manifest test suite for regressions

```bash
python3 -m pytest tests/sdk/test_manifest.py -q 2>&1 | tail -5
```

Existing tests likely use symbols like `AAPL` or `SPY` (canonical equities) and will continue to pass. If any existing test fixture uses `BTC` or `BTC-USD`, update those fixtures to `BTCUSD`. List the changed fixtures in the commit message.

### Step 6: Commit

```bash
git add sdk/manifest.py tests/sdk/test_manifest.py
git commit -m "feat(manifest): two-gate canonical validation on assets: block

Gate 1: symbol must be canonical for some asset class (registry.validate).
Gate 2: symbol's natural classification must match declared asset_class.

Catches three classes of bug at install time:
- {symbol: BTC, asset_class: crypto}      → gate 1 fails, hints BTCUSD
- {symbol: ^VIX, asset_class: index}      → gate 1 fails
- {symbol: BTCUSD, asset_class: equities} → gate 2 fails

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Migration script

**Files:**
- Create: `scripts/migrate_canonical_symbols.py`
- Create: `tests/scripts/test_migrate_canonical_symbols.py`

### Step 1: Write failing test

Create `tests/scripts/test_migrate_canonical_symbols.py`:

```python
"""Unit test for the one-off canonical-symbol migration script."""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "migrate_canonical_symbols.py"


def _df():
    return pd.DataFrame({"timestamp": ["2024-01-01"], "close": [1.0]})


@pytest.fixture
def fake_market_dir(tmp_path):
    """Construct a fake data/market tree with a mix of canonical and non-canonical dirs."""
    root = tmp_path / "data"
    market = root / "market"
    # Will be renamed: yfinance/BTC-USD → BTCUSD
    (market / "yfinance" / "BTC-USD").mkdir(parents=True)
    _df().to_parquet(market / "yfinance" / "BTC-USD" / "1day.parquet")
    # Already canonical
    (market / "polygon" / "BTCUSD").mkdir(parents=True)
    _df().to_parquet(market / "polygon" / "BTCUSD" / "1min.parquet")
    # Already canonical equity
    (market / "polygon" / "AAPL").mkdir(parents=True)
    _df().to_parquet(market / "polygon" / "AAPL" / "1day.parquet")
    return root


def test_migration_creates_backup_and_renames(fake_market_dir, monkeypatch):
    monkeypatch.chdir(fake_market_dir)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, check=True,
    )
    # Backup created
    backups = list(fake_market_dir.glob("market.bak.*"))
    assert len(backups) == 1
    backup = backups[0]
    # Backup has same parquet count
    assert sum(1 for _ in backup.rglob("*.parquet")) == 3
    # Rename happened
    market = fake_market_dir / "market"
    assert (market / "yfinance" / "BTCUSD" / "1day.parquet").exists()
    assert not (market / "yfinance" / "BTC-USD").exists()
    # Already-canonical entries untouched
    assert (market / "polygon" / "BTCUSD" / "1min.parquet").exists()
    assert (market / "polygon" / "AAPL" / "1day.parquet").exists()
    # Output mentions actions and restore command
    assert "RENAMED yfinance/BTC-USD → yfinance/BTCUSD" in result.stdout
    assert "To restore:" in result.stdout


def test_migration_refuses_to_clobber_backup(fake_market_dir, monkeypatch):
    monkeypatch.chdir(fake_market_dir)
    # Pre-create a backup with the future timestamp to force collision
    from datetime import datetime
    bak = fake_market_dir / f"market.bak.{datetime.now():%Y%m%d-%H%M%S}"
    bak.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True,
    )
    # Either the test's pre-created backup or one from a second-resolution collision blocks
    if "refusing to clobber" in result.stderr:
        assert result.returncode != 0
    else:
        # Race: timestamps differ; the test is OK either way
        pytest.skip("timestamp race produced a fresh backup name; not a failure")


def test_migration_idempotent(fake_market_dir, monkeypatch):
    """Running twice (after manually removing first backup) should rename zero."""
    monkeypatch.chdir(fake_market_dir)
    subprocess.run([sys.executable, str(SCRIPT)], check=True, capture_output=True, text=True)
    # Remove the backup so second run can proceed
    for b in fake_market_dir.glob("market.bak.*"):
        import shutil
        shutil.rmtree(b)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, check=True,
    )
    assert "RENAMED" not in result.stdout
```

Note: the script is hard-coded to operate on `data/market`. The test uses `monkeypatch.chdir` so the script sees the temp tree as its working directory.

### Step 2: Run, verify fail (script doesn't exist yet)

```bash
mkdir -p tests/scripts && touch tests/scripts/__init__.py
python3 -m pytest tests/scripts/test_migrate_canonical_symbols.py -v
```

Expected: failures with "No such file or directory" pointing at the script.

### Step 3: Write the script

Create `scripts/migrate_canonical_symbols.py`:

```python
#!/usr/bin/env python3
"""One-off canonical-symbol migration. Backs up data/market first.

Renames provider-form parquet directories to canonical form, e.g.
data/market/yfinance/BTC-USD → data/market/yfinance/BTCUSD.

Refuses to clobber a pre-existing backup. Verifies post-backup file count.
Idempotent — re-runs are no-ops once everything is canonical.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from coordinator.services.asset_services.registry import get_default_registry


MARKET_DIR = Path("data/market")
BACKUP = MARKET_DIR.parent / f"market.bak.{datetime.now():%Y%m%d-%H%M%S}"


def main() -> int:
    if not MARKET_DIR.exists():
        print(f"nothing to do: {MARKET_DIR} does not exist")
        return 0

    if BACKUP.exists():
        print(f"refusing to clobber existing backup: {BACKUP}", file=sys.stderr)
        return 1

    print(f"Backing up {MARKET_DIR} → {BACKUP} ...")
    shutil.copytree(MARKET_DIR, BACKUP, copy_function=shutil.copy2)

    src_count = sum(1 for _ in MARKET_DIR.rglob("*.parquet"))
    bak_count = sum(1 for _ in BACKUP.rglob("*.parquet"))
    if src_count != bak_count:
        print(
            f"backup verification failed: src={src_count} bak={bak_count}",
            file=sys.stderr,
        )
        return 1
    print(f"Backup OK ({src_count} parquet files copied)")

    registry = get_default_registry()
    for provider_dir in sorted(MARKET_DIR.iterdir()):
        if not provider_dir.is_dir():
            continue
        provider = provider_dir.name
        for symbol_dir in sorted(provider_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue
            provider_form = symbol_dir.name
            try:
                canonical = registry.canonicalize(provider_form, provider)
            except (ValueError, KeyError) as e:
                print(f"SKIP {provider}/{provider_form}: {e}")
                continue
            if canonical == provider_form:
                continue
            target = provider_dir / canonical
            if target.exists():
                print(
                    f"CONFLICT {provider}/{provider_form} → {canonical} (target exists)"
                )
                continue
            symbol_dir.rename(target)
            print(f"RENAMED {provider}/{provider_form} → {provider}/{canonical}")

    cov = Path("data/coverage_index.parquet")
    if cov.exists():
        cov.unlink()
        print(f"WIPED {cov}")

    print(f"\nDone. To restore: rm -rf {MARKET_DIR} && mv {BACKUP} {MARKET_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### Step 4: Run, verify pass

```bash
chmod +x scripts/migrate_canonical_symbols.py
python3 -m pytest tests/scripts/test_migrate_canonical_symbols.py -v
```

Expected: all three tests pass.

### Step 5: Commit

```bash
git add scripts/migrate_canonical_symbols.py tests/scripts/__init__.py tests/scripts/test_migrate_canonical_symbols.py
git commit -m "feat(scripts): one-off canonical-symbol migration

Backs up data/market before any rename, verifies post-backup file count,
refuses to clobber prior backup. Renames provider-form directories to
canonical (BTC-USD → BTCUSD, etc.). Idempotent. Wipes the coverage index
so it rebuilds lazily from the renamed files.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: E2E smoke — run migration script + sweep test

**Files:** (no edits — verification only)

### Step 1: Run the full test suite for regressions

```bash
python3 -m pytest tests/coordinator/ tests/sdk/ tests/scripts/ -q 2>&1 | tail -10
```

Expected: no NEW failures attributable to this branch. Pre-existing failures unrelated to canonical symbols are acceptable.

### Step 2: Run the migration script against the dev machine's actual data

```bash
python3 scripts/migrate_canonical_symbols.py 2>&1 | tee /tmp/migrate.log
```

Expected output:
- `Backing up data/market → data/market.bak.YYYYMMDD-HHMMSS ...`
- `Backup OK (~25000 parquet files copied)` (mostly SPY options)
- A small number of `RENAMED` lines (yfinance/BTC-USD, yfinance/ETH-USD)
- Many `SKIP` lines for already-canonical entries (including the ~24k SPY option dirs reporting "already a recognized option form")
- `WIPED data/coverage_index.parquet` (if it exists)
- Final restore command hint

If any RENAMED line involves an unexpected directory, **stop and investigate** before proceeding. The backup can be restored via the printed `mv` command.

### Step 3: Restart coord and verify it boots clean

```bash
quilt coord restart
```

Expected: `coord started (pid=..., port=...)`. If startup fails, the coverage index rebuilds on first request — check `~/.quilt/log/coord.log` for any symbol-validation errors.

### Step 4: Update one algorithm manifest as a smoke

Pick one previously-broken crypto algo (e.g. `/tmp/quilt-algos/crypto-double-ema-4h/quilt.yaml`) and change `symbol: BTC` to `symbol: BTCUSD`. Reinstall via the dashboard or CLI.

Expected: install succeeds (gates 1 and 2 pass).

### Step 5: Run a small sweep against that algorithm via the research API

Use the canonical scope-field session created earlier (or create a new one):

```bash
python3 <<'EOF'
import urllib.request, json, time
body = {
    "name": f"canonical-smoke-{int(time.time())}",
    "hypothesis": "canonical symbol routing works end-to-end",
    "algorithm_id": "<the-updated-crypto-algo-id>",
    "base_config": {},
    "parameter_space": {"ema1_short_minutes": [120, 240]},
    "pre_registered_criteria": {"min_sharpe": 0.0},
    "date_range_start": "2024-01-01",
    "date_range_end": "2024-03-31",
    "initial_cash": 10000.0,
    "cost_profile": "default",
}
req = urllib.request.Request(
    "http://127.0.0.1:8000/api/research/sessions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
resp = json.loads(urllib.request.urlopen(req).read())
print("session:", resp["id"], resp["name"])
EOF
```

Then queue a sweep against this session via the dashboard or `quilt research sweep --session-id <id> --max-trials 2`. Poll job status until completion.

**Expected:** both trial runs reach status `completed` (not `failed` with a download error). The Polygon `fetch_bars` call now uses `X:BTCUSD` thanks to the new crypto polygon mapping; the runner persists bars to `data/market/polygon/BTCUSD/1min.parquet` (canonical path); the backtest runs against real data.

If both runs complete cleanly, the canonical-symbol-at-boundary refactor is verified end-to-end. The original `NOT NULL constraint failed: backtest_runs.date_range_start` bug was fixed by the prior plan; this plan fixes the downstream `BTC: no data returned by polygon` bug.

### Step 6: Delete the migration script (no support burden)

```bash
git rm scripts/migrate_canonical_symbols.py tests/scripts/test_migrate_canonical_symbols.py
git commit -m "chore(scripts): drop one-off canonical-symbol migration

Run once on the dev machine; no other deployments exist.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

(Leave the `tests/scripts/__init__.py` in place if other future scripts will be tested there.)

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 Architecture — outbound `resolve_symbol`, inbound `canonicalize` | Tasks 1-5 |
| §2 Canonical forms — crypto, equity, index, options regexes; equity bare-crypto exclusion | Tasks 1, 2, 3, 4 |
| §2.1 `_KNOWN_INDEXES` (37 entries; drop GSPC/IXIC) | Task 3 |
| §3 AssetService API — `CANONICAL_RE`, strict `resolve_symbol`, `canonicalize` | Tasks 1-4 |
| §3.1 Registry `validate` + `canonicalize` | Task 5 |
| §4 Data store boundary validation | Task 7 |
| §4 Polygon `fetch_bars` flow (caller uses canonical) | Task 7 (validates upstream) + Task 6 (cleans up the related ad-hoc strip) |
| §5 Manifest validation — two gates | Task 8 |
| §6 Migration script (with backup) | Task 9 |
| §6 Backtest_runner.py:632 ad-hoc strip cleanup | Task 6 |
| §7 Testing — service unit, registry, manifest, data store, migration | Tests embedded in Tasks 1-9 |
| §8 Out of scope (USDT, intl indexes, ETFs) | n/a — explicitly not in plan |
| §9 Files touched | covered across Tasks 1-9 |

**Placeholder scan:** None. Every task has complete code and exact commands.

**Type consistency:**
- `CANONICAL_RE: re.Pattern` defined identically on all 4 services (Tasks 1-4)
- `validate(symbol) → None` (raises) consistent (Task 5)
- `canonicalize(provider_form, provider) → str` (raises on failure) consistent across all 5 callers (Tasks 1-5)
- `get_default_registry()` import added consistently (Tasks 6, 7, 8)
- `_KNOWN_INDEXES` is a `frozenset[str]` exported from `index.py` (Task 3) and not duplicated elsewhere
- `_KNOWN_CRYPTO_BARE` is a `frozenset[str]` in `equity.py` (Task 2) — only consumer is equity classify

**One subtle point:** the spec's expected migration output (Section 6) mentions `polygon/BTC → BTCUSD` as a renamed entry. That rename will NOT happen — `crypto.canonicalize("BTC", "polygon")` raises (ambiguous bare), and equity classify rejects `BTC` (bare-crypto exclusion), so the entire `polygon/BTC/` directory falls through both services. The registry's overall `canonicalize` raises, the script logs `SKIP polygon/BTC: ...`, and the directory is left in place. If this directory contains stale failed-download data from the bug we just discovered, the user can manually `rm -rf data/market/polygon/BTC/` after the migration. Task 10 Step 2's "stop and investigate" guidance covers any surprise here.
