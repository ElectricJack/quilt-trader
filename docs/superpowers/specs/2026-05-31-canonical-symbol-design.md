# Canonical Symbol Normalization at the Data Provider Boundary

**Status:** Design approved 2026-05-31. Pending implementation plan.

**Motivation:** A research sweep failed because `quilt.yaml` declared `symbol: BTC, asset_class: crypto`, the runner passed bare `"BTC"` to Polygon as if it were an equity ticker, and the download returned no data. Root cause: the framework has no agreed canonical symbol form. Provider-native strings (`BTC-USD` for yfinance, `BTC` for polygon attempts, `X:BTCUSD` Polygon actually requires, `BTC/USD` for Alpaca) leak into algorithm manifests, stored parquet paths, and database records. `CryptoAssetService.classify` requires a `USD`/`USDT` suffix, so bare `BTC` falls through to the equity service, and even with `BTCUSD` the crypto service has no mapping for `polygon` and passes the symbol through unchanged. The boundary between "framework speaks one name" and "provider speaks its own name" is broken in several places at once.

**Goal:** One canonical symbol per asset, framework-wide. Every layer above the data-provider adapters speaks canonical only. Provider-native strings exist only inside the HTTP requests to providers and inside broker stream subscriptions. Three validation gates (manifest load, data-store I/O, asset-service inputs) reject any non-canonical symbol with a clear error pointing at the fix.

---

## 1. Architecture

The translation boundary lives at two seams:

- **Outbound (canonical → provider-native):** `AssetService.resolve_symbol(canonical, provider)` is the only place that produces provider-shaped strings. Called by `coordinator/services/data_providers/polygon.py`, `worker/alpaca_adapter.py`, and the data-store layer when constructing parquet paths.
- **Inbound (provider-native → canonical):** new `AssetService.canonicalize(provider_form, provider)` method handles cases where a provider hands the framework a string that needs normalizing (Polygon's options-discovery returning `O:AAPL240119C00150000`, for example). Replaces the ad-hoc `removeprefix("O:")` in `coordinator/services/backtest_runner.py:632`.

Internal state (parquet paths, BacktestRun rows, ResearchJob payloads, algorithm manifests, dashboard display) is canonical. A non-canonical string crossing an internal boundary is a bug, surfaced at three gates:

1. Manifest load (`sdk/manifest.py`)
2. Data-store I/O (`coordinator/services/data_service.py`)
3. AssetService inputs (every call to `resolve_symbol`)

---

## 2. Canonical Forms

Each `AssetService` declares its canonical form as a class-level compiled regex.

| Asset class | Regex | Examples | Notes |
|---|---|---|---|
| Equities | `^[A-Z]{1,5}(\.[A-Z])?$` AND `symbol ∉ _KNOWN_CRYPTO_BARE` | `AAPL`, `SPY`, `QQQ`, `BRK.B`, `BF.B` | Dot-suffix for share classes; bare crypto names like `BTC`/`ETH`/`SOL` excluded so `validate("BTC")` raises (the multi-class hint guides the user to `BTCUSD`) |
| Crypto | `^[A-Z]{2,5}(USD\|USDT)$` | `BTCUSD`, `ETHUSD`, `BTCUSDT`, `SOLUSDT` | Quote currency in suffix |
| Index | `^[A-Z]{2,5}$` AND `symbol ∈ _KNOWN_INDEXES` | `VIX`, `SPX`, `NDX`, `VIX3M`, `SOX` | Closed set (see §2.1) |
| Options | `^[A-Z]{1,6}\d{6}[CP]\d{8}$` | `AAPL240119C00150000` | OCC, no prefix, no spaces |

The four canonical forms share: uppercase ASCII, single regex per class. Classification ordering (existing in `AssetServiceRegistry`): options first, crypto second, index third, equity as fallback.

Conflict cases:

- **Index vs equity:** Both regexes accept bare uppercase strings. Index wins if symbol is in `_KNOWN_INDEXES`; otherwise equity. Same as today's logic.
- **Crypto vs equity:** Crypto regex requires `USD`/`USDT` suffix, so unambiguous.
- **Crypto vs index:** Some indexes happen to end in `USD` only in theory — not in practice for the 37-entry closed set.
- **Bare `BTC` (or any unknown):** No regex matches. `AssetServiceRegistry.validate(symbol)` raises `ValueError` with the message:
  ```
  'BTC' is not a canonical symbol. Crypto canonical form is e.g. 'BTCUSD'.
  Equity canonical form is e.g. 'AAPL'. Index canonical form is one of
  {VIX, SPX, NDX, ...}. Options canonical form is OCC e.g. 'AAPL240119C00150000'.
  ```

### 2.1. `_KNOWN_INDEXES` (37 entries)

```python
_KNOWN_INDEXES = frozenset({
    # US equity broad-market (15)
    "SPX", "OEX", "MID", "SML",
    "NDX", "COMP",
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
```

Current `_KNOWN_INDEXES` includes `GSPC` and `IXIC`. Both are yfinance-specific aliases (`^GSPC` = SPX, `^IXIC` = COMP), NOT canonicals. They are removed; corresponding yfinance mapping entries are added (see §3).

---

## 3. AssetService API Changes

Each `AssetService` gains one class attribute, one new method, and stricter validation on `resolve_symbol`.

```python
class CryptoAssetService:
    asset_type = AssetType.CRYPTO
    CANONICAL_RE = re.compile(r"^[A-Z]{2,5}(USD|USDT)$")

    def classify(self, symbol: str) -> bool:
        return bool(self.CANONICAL_RE.match(symbol))

    def resolve_symbol(self, canonical: str, provider: str) -> str:
        if not self.CANONICAL_RE.match(canonical):
            raise ValueError(f"{canonical!r} is not a canonical crypto symbol")
        # ... provider-specific mapping ...

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Inverse of resolve_symbol — parse provider-native form back to canonical.
        Raises ValueError if the input is ambiguous (e.g. bare 'BTC')."""
```

Per-provider mapping table updates:

| Service | Provider | Today | After |
|---|---|---|---|
| Crypto | `polygon` | missing — passes `BTCUSD` through unchanged | `BTCUSD → X:BTCUSD` |
| Crypto | `yfinance` | `BTCUSD → BTC-USD` | unchanged |
| Crypto | `alpaca` / `alpaca_stream` | `BTCUSD → BTC/USD` | unchanged |
| Crypto | `coinbase` | `BTCUSD → BTC-USD` | unchanged |
| Equities | all providers | pass-through | pass-through; yfinance map has explicit entries for share-class dotted tickers (`BRK.B → BRK-B`, `BRK.A → BRK-A`, `BF.B → BF-B`); ordinary tickers pass through unchanged. Equity `classify()` excludes `_KNOWN_CRYPTO_BARE = {"BTC","ETH","SOL","DOGE","AVAX","LINK","USDT","USD","ETC","XRP","ADA","LTC","BCH"}` so those fall through to `validate()` raising with the multi-class hint |
| Index | `_KNOWN_INDEXES` | 7 entries incl. `GSPC`, `IXIC` | 37 entries; `GSPC`, `IXIC` removed |
| Index | `yfinance` map | `VIX → ^VIX`, `SPX → ^GSPC` | add `COMP → ^IXIC`; default rule `X → ^X` for all 37 unless explicit override |
| Index | `polygon` map | `VIX → I:VIX` | default rule `X → I:X` for all 37 |
| Options | `polygon` | `OCC → O:OCC` | unchanged |

### 3.1. AssetServiceRegistry additions

```python
class AssetServiceRegistry:
    def validate(self, symbol: str) -> None:
        """Raise ValueError if symbol matches no canonical form."""

    def canonicalize(self, provider_form: str, provider: str) -> str:
        """Try each AssetService.canonicalize in classification order;
        return the first successful canonical form."""
```

---

## 4. Data Store Boundary

`coordinator/services/data_service.py` is the second validation gate. Every public method that takes `symbol` adds a one-line validation:

```python
def save_market_data(self, provider: str, symbol: str, timeframe: str, df: pd.DataFrame) -> str:
    get_default_registry().validate(symbol)
    path = self.market_data_path(provider, symbol, timeframe)
    ...
```

Applied to: `save_market_data`, `load_market_data`, `delete_market_data`, `market_data_path`, `latest_market_data_timestamp`, `earliest_market_data_timestamp`.

**On-disk path layout becomes canonical:** `data/market/<provider>/<canonical_symbol>/<timeframe>.parquet`. Polygon BTC bars live at `data/market/polygon/BTCUSD/1min.parquet`, not `polygon/BTC/...` and not `polygon/X:BTCUSD/...`. The `X:` prefix exists only inside the Polygon HTTP request.

The polygon `fetch_bars` flow becomes symmetric across all providers:

1. Caller passes canonical `BTCUSD`.
2. `resolve_symbol("BTCUSD", "polygon")` returns `X:BTCUSD` for the URL.
3. Response bars are saved to `data/market/polygon/BTCUSD/1min.parquet`.

**Coverage index.** `coverage_index` (queried in `backtest_runner.py:262–279`) maps `(provider, symbol, timeframe)` to cached date ranges. All keys are canonical. The migration script wipes the index post-rename; the runner rebuilds it lazily.

**`MarketDataDownload` rows.** The `symbols` JSON column stores what was queued. New rows are canonical (because the runner now passes canonical). Old rows remain unchanged — they're historical records of completed downloads.

---

## 5. Manifest Validation

`sdk/manifest.py` — `QuiltManifest._parse` already validates `asset_class` against `_VALID_ASSET_TYPES`. Two checks are added per asset entry:

```python
for asset in parsed_assets:
    symbol = asset["symbol"]
    declared_class = asset["asset_class"]

    # Gate 1: symbol must be canonical for some asset class
    try:
        registry.validate(symbol)
    except ValueError as e:
        raise ManifestError(f"asset '{symbol}' in {manifest_path}: {e}")

    # Gate 2: symbol's natural classification must match declared asset_class
    inferred = registry.classify(symbol).value
    if inferred != declared_class:
        raise ManifestError(
            f"asset '{symbol}' is declared as asset_class={declared_class!r} "
            f"but its canonical form classifies as {inferred!r}. "
            f"Either fix the symbol or change asset_class."
        )
```

Catches three classes of bug at install time, before any download or backtest:

1. `{symbol: BTC, asset_class: crypto}` → fails gate 1.
2. `{symbol: BTCUSD, asset_class: equities}` → fails gate 2.
3. `{symbol: ^VIX, asset_class: index}` → fails gate 1.

**Legacy `requirements.data_dependencies` path.** Same validation; the runner's fallback at `backtest_runner.py:245` still works.

**Algorithms requiring updates.** Five algorithm `quilt.yaml` files in the install tree (`/tmp/quilt-algos/*/quilt.yaml`) use non-canonical symbols today (`BTC`, `ETH`, `LTC`, `BTC-USD`, `ETH-USD`). All will fail install with this change. Each needs a one-line PR to its upstream repo. Implementation plan lists them so rollout-day breakage is expected and accounted for.

---

## 6. Migration Script

`scripts/migrate_canonical_symbols.py` — one-off. Backs up `data/market/` before any rename. Refuses to clobber a prior backup. Verifies post-backup file count.

```python
#!/usr/bin/env python3
"""One-off canonical-symbol migration. Backs up data/market first."""
import shutil, sys
from datetime import datetime
from pathlib import Path
from coordinator.services.asset_services.registry import get_default_registry

MARKET_DIR = Path("data/market")
BACKUP = MARKET_DIR.parent / f"market.bak.{datetime.now():%Y%m%d-%H%M%S}"

if BACKUP.exists():
    sys.exit(f"refusing to clobber existing backup: {BACKUP}")

print(f"Backing up {MARKET_DIR} → {BACKUP} ...")
shutil.copytree(MARKET_DIR, BACKUP, copy_function=shutil.copy2)

src_count = sum(1 for _ in MARKET_DIR.rglob("*.parquet"))
bak_count = sum(1 for _ in BACKUP.rglob("*.parquet"))
if src_count != bak_count:
    sys.exit(f"backup verification failed: src={src_count} bak={bak_count}")
print(f"Backup OK ({src_count} parquet files copied)")

registry = get_default_registry()
for provider_dir in MARKET_DIR.iterdir():
    if not provider_dir.is_dir():
        continue
    provider = provider_dir.name
    for symbol_dir in provider_dir.iterdir():
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
            print(f"CONFLICT {provider}/{provider_form} → {canonical} (target exists)")
            continue
        symbol_dir.rename(target)
        print(f"RENAMED {provider}/{provider_form} → {provider}/{canonical}")

cov = Path("data/coverage_index.parquet")
if cov.exists():
    cov.unlink()
    print(f"WIPED {cov}")

print(f"\nDone. To restore: rm -rf {MARKET_DIR} && mv {BACKUP} {MARKET_DIR}")
```

Expected output on the current development machine (per the survey):

- 4 RENAMED lines: `yfinance/BTC-USD → BTCUSD`, `yfinance/ETH-USD → ETHUSD`, `polygon/BTC → BTCUSD`, possibly `polygon/BRK.B` (no-op — already canonical under the updated equity regex).
- ~24,000 SKIP-or-no-op lines (SPY option directories already canonical).
- 1 WIPED line for `coverage_index.parquet`.
- Final restore-command hint.

Script is deleted from the repo after a single successful run on the dev machine — no support burden.

**Note on backup duration:** `shutil.copytree` of ~24,000 SPY option parquet files plus the rest of `data/market/` will take 30–60 seconds on SSD. The script prints `Backing up ...` before starting so the user knows to wait.

---

## 7. Testing

Five test groupings, one per validation gate plus the migration script:

### 7.1. `AssetService` unit tests (`tests/coordinator/services/asset_services/`)

Per service:

- `classify(canonical_form)` → True; `classify(non_canonical_form)` → False
- `resolve_symbol(canonical, provider)` → expected provider-native string for each supported provider
- `resolve_symbol(non_canonical, provider)` → `ValueError`
- `canonicalize(provider_form, provider)` → canonical; `canonicalize(ambiguous_form, provider)` → `ValueError`
- Round-trip table: for every (canonical, provider) entry in the service's mapping, assert `canonicalize(resolve_symbol(canonical, provider), provider) == canonical`

### 7.2. `AssetServiceRegistry` tests

- `validate("BTCUSD")` → no raise; `validate("BTC")` → `ValueError` with multi-class hint string
- `classify("BRK.B")` → equity service (validates dot-suffix regex)
- `canonicalize("SPY240731P00340000", "polygon")` → OCC unchanged

### 7.3. Manifest validation tests (`tests/sdk/test_manifest.py`)

- `{symbol: BTC, asset_class: crypto}` → `ManifestError` mentioning canonical form
- `{symbol: BTCUSD, asset_class: equities}` → `ManifestError` mentioning class-vs-symbol mismatch
- `{symbol: BTCUSD, asset_class: crypto}` → loads cleanly
- `{symbol: ^VIX, asset_class: index}` → `ManifestError`

### 7.4. Data store boundary tests (`tests/coordinator/services/test_data_service.py`)

- `save_market_data("polygon", "BTC", "1min", df)` → `ValueError`
- `save_market_data("polygon", "BTCUSD", "1min", df)` → writes to `data/market/polygon/BTCUSD/1min.parquet`
- Same for read, delete, latest/earliest timestamp methods

### 7.5. Migration script test (`tests/scripts/test_migrate_canonical_symbols.py`)

- Construct temp `data/market/<provider>/<non-canonical>/file.parquet` tree
- Override `MARKET_DIR` (env var or module-level constant patch)
- Run script
- Assert: backup directory created with same file count; non-canonical directory renamed to canonical; restore-command printed to stdout

No end-to-end test against live data — the script is one-off and audited via its own per-action stdout.

---

## 8. Out of Scope

- USDT pair migration. `BTCUSDT`/`ETHUSDT`/`SOLUSDT` are valid canonicals (covered by the crypto regex); no on-disk data uses them today, so no rename needed. Code support remains.
- International indexes (FTSE, DAX, Nikkei, etc.). Not used in this framework today; can be added to `_KNOWN_INDEXES` later with a one-line PR.
- ETF / sector-fund tickers (XLK, XLF, etc.). These are equity tickers, already canonical under the equity regex.
- Renaming any database-stored historical symbol values (e.g. `MarketDataDownload.symbols` JSON on already-completed rows). Historical, not load-bearing.
- Updating external algorithm repos (the 5 affected `quilt.yaml` files). Listed in the implementation plan as a rollout-day task but not part of this framework spec.

---

## 9. Files Touched

**Modified:**
- `coordinator/services/asset_services/crypto.py` — `CANONICAL_RE`, strict `resolve_symbol`, new `canonicalize`, add polygon mapping
- `coordinator/services/asset_services/equity.py` — `CANONICAL_RE` with share-class dot, strict `resolve_symbol`, new `canonicalize`
- `coordinator/services/asset_services/index.py` — `CANONICAL_RE`, expanded `_KNOWN_INDEXES` (37 entries, drop `GSPC`/`IXIC`), default `X → ^X` and `X → I:X` mapping rules, new `canonicalize`
- `coordinator/services/asset_services/options.py` — `CANONICAL_RE`, strict `resolve_symbol`, new `canonicalize`
- `coordinator/services/asset_services/registry.py` — new `validate` and `canonicalize` methods
- `coordinator/services/data_service.py` — `validate` call on every symbol-taking method
- `coordinator/services/backtest_runner.py:632` — replace ad-hoc `removeprefix("O:")` with `registry.canonicalize(ticker, "polygon")`
- `sdk/manifest.py` — two new manifest-load gates

**Created:**
- `scripts/migrate_canonical_symbols.py` — one-off migration script (deleted post-run)
- Test files: `tests/coordinator/services/asset_services/test_canonical.py`, `tests/scripts/test_migrate_canonical_symbols.py`, additions to `tests/sdk/test_manifest.py` and `tests/coordinator/services/test_data_service.py`
