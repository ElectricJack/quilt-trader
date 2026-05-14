# Plan 6: Data Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the coordinator's data layer — market data downloading (Polygon + Theta Data) with progress tracking, scraper execution engine, data serving API for workers and the SDK CLI, and data archival to Parquet files.

**Architecture:** The data service manages market data downloads as background tasks with progress tracking via the MarketDataDownloads table. Market data is stored as Parquet files organized by provider/symbol/timeframe. The scraper engine runs scraper packages as subprocesses on their cron schedule, storing output to the coordinator's filesystem. REST API endpoints serve both market and custom data to workers and local SDK development. The archival service exports old decision/trade log rows to Parquet and removes them from SQLite.

**Tech Stack:** Python 3.11+, pandas, pyarrow, APScheduler, httpx (for Polygon/Theta Data APIs), subprocess, FastAPI, pytest

---

## File Map

| File | Responsibility |
|------|---------------|
| `coordinator/services/data_service.py` | Market data download orchestration, provider abstraction |
| `coordinator/services/data_providers/__init__.py` | Package marker |
| `coordinator/services/data_providers/polygon.py` | Polygon API client for market data downloads |
| `coordinator/services/data_providers/theta_data.py` | Theta Data API client for market data downloads |
| `coordinator/services/scraper_engine.py` | Runs scraper subprocess on schedule, captures output |
| `coordinator/services/archival.py` | Export old logs to Parquet, delete from SQLite |
| `coordinator/services/scheduler.py` | APScheduler setup for scraper cron, nightly backtest, archival |
| `coordinator/api/routes/data.py` | Data serving API (market data + custom data endpoints) |
| `coordinator/api/routes/downloads.py` | Market data download management endpoints |
| `coordinator/api/routes/runs.py` | Algorithm run query endpoints |
| `coordinator/api/routes/cash_flows.py` | Account cash flow CRUD endpoints |
| `tests/coordinator/test_data_service.py` | Data service tests |
| `tests/coordinator/test_polygon_provider.py` | Polygon provider tests (mocked HTTP) |
| `tests/coordinator/test_scraper_engine.py` | Scraper engine tests |
| `tests/coordinator/test_archival.py` | Archival service tests |
| `tests/coordinator/test_data_api.py` | Data API endpoint tests |
| `tests/coordinator/test_downloads_api.py` | Download management API tests |
| `tests/coordinator/test_runs_api.py` | Runs API tests |
| `tests/coordinator/test_cash_flows_api.py` | Cash flow API tests |

---

### Task 1: Polygon Data Provider

**Files:**
- Create: `coordinator/services/data_providers/__init__.py`
- Create: `coordinator/services/data_providers/polygon.py`
- Create: `tests/coordinator/test_polygon_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_polygon_provider.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import date

from coordinator.services.data_providers.polygon import PolygonProvider


@pytest.fixture
def mock_http():
    http = AsyncMock()
    http.get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={
            "results": [
                {"t": 1704067200000, "o": 150.0, "h": 151.0, "l": 149.0, "c": 150.5, "v": 1000},
                {"t": 1704067260000, "o": 150.5, "h": 152.0, "l": 150.0, "c": 151.0, "v": 1500},
            ],
            "resultsCount": 2,
        }),
    )
    return http


@pytest.mark.asyncio
async def test_fetch_bars(mock_http):
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    bars = await provider.fetch_bars(
        symbol="AAPL",
        timeframe="1min",
        start=date(2025, 1, 1),
        end=date(2025, 1, 2),
    )
    assert len(bars) == 2
    assert bars[0]["open"] == 150.0
    assert bars[0]["close"] == 150.5
    assert "timestamp" in bars[0]
    mock_http.get.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_bars_empty_response(mock_http):
    mock_http.get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"results": [], "resultsCount": 0}),
    )
    provider = PolygonProvider(api_key="test-key", http_client=mock_http)
    bars = await provider.fetch_bars("AAPL", "1day", date(2025, 1, 1), date(2025, 1, 1))
    assert bars == []


def test_timeframe_to_polygon_multiplier():
    provider = PolygonProvider(api_key="test")
    assert provider._timeframe_params("1min") == ("1", "minute")
    assert provider._timeframe_params("5min") == ("5", "minute")
    assert provider._timeframe_params("1hour") == ("1", "hour")
    assert provider._timeframe_params("1day") == ("1", "day")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_polygon_provider.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/data_providers/__init__.py
# (empty)
```

```python
# coordinator/services/data_providers/polygon.py
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "1min": ("1", "minute"),
    "5min": ("5", "minute"),
    "15min": ("15", "minute"),
    "1hour": ("1", "hour"),
    "1day": ("1", "day"),
}


class PolygonProvider:
    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str, http_client: Any = None) -> None:
        self._api_key = api_key
        self._http = http_client

    def _timeframe_params(self, timeframe: str) -> tuple[str, str]:
        if timeframe in TIMEFRAME_MAP:
            return TIMEFRAME_MAP[timeframe]
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
    ) -> list[dict]:
        multiplier, span = self._timeframe_params(timeframe)
        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/range"
            f"/{multiplier}/{span}/{start.isoformat()}/{end.isoformat()}"
        )
        response = await self._http.get(
            url,
            params={"apiKey": self._api_key, "limit": 50000, "sort": "asc"},
        )
        data = response.json()
        results = data.get("results", [])
        return [
            {
                "timestamp": datetime.fromtimestamp(
                    r["t"] / 1000, tz=timezone.utc
                ).isoformat(),
                "open": r["o"],
                "high": r["h"],
                "low": r["l"],
                "close": r["c"],
                "volume": r["v"],
            }
            for r in results
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_polygon_provider.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/data_providers/ tests/coordinator/test_polygon_provider.py
git commit -m "feat(coordinator): add Polygon market data provider"
```

---

### Task 2: Data Service (Download Orchestration)

**Files:**
- Create: `coordinator/services/data_service.py`
- Create: `tests/coordinator/test_data_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_data_service.py
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date

import pandas as pd

from coordinator.services.data_service import DataService


@pytest.fixture
def data_dir(tmp_path):
    return str(tmp_path / "data")


@pytest.fixture
def data_service(data_dir):
    return DataService(
        market_data_dir=os.path.join(data_dir, "market"),
        custom_data_dir=os.path.join(data_dir, "custom"),
    )


def test_market_data_path(data_service):
    path = data_service.market_data_path("polygon", "AAPL", "1day")
    assert "polygon" in path
    assert "AAPL" in path
    assert "1day" in path
    assert path.endswith(".parquet")


def test_save_and_load_market_data(data_service):
    df = pd.DataFrame({
        "timestamp": ["2025-01-01", "2025-01-02"],
        "open": [150.0, 151.0],
        "high": [151.0, 152.0],
        "low": [149.0, 150.0],
        "close": [150.5, 151.5],
        "volume": [1000, 1500],
    })
    data_service.save_market_data("polygon", "AAPL", "1day", df)
    loaded = data_service.load_market_data("polygon", "AAPL", "1day")
    assert len(loaded) == 2
    assert loaded.iloc[0]["close"] == 150.5


def test_load_market_data_not_found(data_service):
    result = data_service.load_market_data("polygon", "MISSING", "1day")
    assert result is None


def test_save_custom_data(data_service):
    df = pd.DataFrame({"symbol": ["TSLA", "NVDA"], "score": [0.95, 0.88]})
    data_service.save_custom_data("alpha-picks", df, "csv")
    path = data_service.custom_data_path("alpha-picks", "csv")
    assert os.path.exists(path)


def test_load_custom_data_csv(data_service):
    df = pd.DataFrame({"symbol": ["TSLA"], "score": [0.95]})
    data_service.save_custom_data("alpha-picks", df, "csv")
    loaded = data_service.load_custom_data("alpha-picks", "csv")
    assert len(loaded) == 1
    assert loaded.iloc[0]["symbol"] == "TSLA"


def test_load_custom_data_not_found(data_service):
    result = data_service.load_custom_data("missing", "csv")
    assert result is None


def test_list_available_market_data(data_service):
    df = pd.DataFrame({"timestamp": ["2025-01-01"], "close": [150.0]})
    data_service.save_market_data("polygon", "AAPL", "1day", df)
    data_service.save_market_data("polygon", "TSLA", "1day", df)
    available = data_service.list_available_market_data()
    assert len(available) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_data_service.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/data_service.py
import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class DataService:
    def __init__(self, market_data_dir: str, custom_data_dir: str) -> None:
        self._market_dir = market_data_dir
        self._custom_dir = custom_data_dir

    def market_data_path(self, provider: str, symbol: str, timeframe: str) -> str:
        return os.path.join(self._market_dir, provider, symbol, f"{timeframe}.parquet")

    def custom_data_path(self, name: str, fmt: str) -> str:
        return os.path.join(self._custom_dir, f"{name}.{fmt}")

    def save_market_data(
        self, provider: str, symbol: str, timeframe: str, df: pd.DataFrame
    ) -> str:
        path = self.market_data_path(provider, symbol, timeframe)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_parquet(path, index=False)
        logger.info("Saved market data: %s", path)
        return path

    def load_market_data(
        self, provider: str, symbol: str, timeframe: str
    ) -> Optional[pd.DataFrame]:
        path = self.market_data_path(provider, symbol, timeframe)
        if not os.path.exists(path):
            return None
        return pd.read_parquet(path)

    def save_custom_data(self, name: str, df: pd.DataFrame, fmt: str) -> str:
        path = self.custom_data_path(name, fmt)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if fmt == "csv":
            df.to_csv(path, index=False)
        elif fmt == "json":
            df.to_json(path, orient="records")
        elif fmt == "parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False)
        return path

    def load_custom_data(self, name: str, fmt: str) -> Optional[pd.DataFrame]:
        path = self.custom_data_path(name, fmt)
        if not os.path.exists(path):
            return None
        if fmt == "csv":
            return pd.read_csv(path)
        elif fmt == "json":
            return pd.read_json(path)
        elif fmt == "parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def list_available_market_data(self) -> list[dict]:
        results = []
        if not os.path.exists(self._market_dir):
            return results
        for provider in os.listdir(self._market_dir):
            provider_dir = os.path.join(self._market_dir, provider)
            if not os.path.isdir(provider_dir):
                continue
            for symbol in os.listdir(provider_dir):
                symbol_dir = os.path.join(provider_dir, symbol)
                if not os.path.isdir(symbol_dir):
                    continue
                for f in os.listdir(symbol_dir):
                    if f.endswith(".parquet"):
                        timeframe = f.replace(".parquet", "")
                        path = os.path.join(symbol_dir, f)
                        size = os.path.getsize(path)
                        results.append({
                            "provider": provider,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "file_path": path,
                            "size_bytes": size,
                        })
        return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_data_service.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/data_service.py tests/coordinator/test_data_service.py
git commit -m "feat(coordinator): add data service for market and custom data storage"
```

---

### Task 3: Scraper Engine

**Files:**
- Create: `coordinator/services/scraper_engine.py`
- Create: `tests/coordinator/test_scraper_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_scraper_engine.py
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from coordinator.services.scraper_engine import ScraperEngine, ScraperResult


@pytest.fixture
def packages_dir(tmp_path):
    pkg = tmp_path / "packages" / "alpha-picks-scraper"
    pkg.mkdir(parents=True)
    (pkg / "quilt.yaml").write_text(
        "name: alpha-picks-scraper\n"
        "type: scraper\n"
        "schedule: '*/30 * * * *'\n"
        "output:\n"
        "  format: csv\n"
        "  filename: alpha-picks.csv\n"
    )
    (pkg / "scraper.py").write_text(
        "from sdk.scraper import QuiltScraper\n"
        "import pandas as pd\n"
        "class AlphaPicksScraper(QuiltScraper):\n"
        "    def on_run(self):\n"
        "        return pd.DataFrame({'symbol': ['TSLA'], 'score': [0.9]})\n"
    )
    return str(tmp_path / "packages")


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "custom"
    d.mkdir()
    return str(d)


def test_scraper_engine_init(packages_dir, output_dir):
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    assert engine is not None


def test_parse_scraper_manifest(packages_dir, output_dir):
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    manifest = engine.parse_manifest("alpha-picks-scraper")
    assert manifest["name"] == "alpha-picks-scraper"
    assert manifest["type"] == "scraper"


def test_output_path(packages_dir, output_dir):
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    path = engine.output_path("alpha-picks-scraper", "csv")
    assert path.endswith("alpha-picks-scraper.csv")


@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.return_value = MagicMock(
        returncode=0,
        stdout="",
        stderr="",
    )
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv", "alpha-picks-scraper.csv")
    assert isinstance(result, ScraperResult)
    assert result.success is True


@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_failure(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="ImportError: No module named 'selenium'",
    )
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv", "alpha-picks-scraper.csv")
    assert result.success is False
    assert "selenium" in result.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_scraper_engine.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/scraper_engine.py
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ScraperResult:
    success: bool
    output_path: Optional[str] = None
    error: Optional[str] = None


class ScraperEngine:
    def __init__(self, packages_dir: str, output_dir: str) -> None:
        self._packages_dir = packages_dir
        self._output_dir = output_dir

    def parse_manifest(self, name: str) -> dict:
        manifest_path = os.path.join(self._packages_dir, name, "quilt.yaml")
        with open(manifest_path) as f:
            return yaml.safe_load(f)

    def output_path(self, name: str, fmt: str) -> str:
        return os.path.join(self._output_dir, f"{name}.{fmt}")

    def run_scraper(
        self, name: str, output_format: str, output_filename: str
    ) -> ScraperResult:
        pkg_dir = os.path.join(self._packages_dir, name)
        venv_python = os.path.join(pkg_dir, ".venv", "bin", "python")
        python = venv_python if os.path.exists(venv_python) else "python"

        out_path = self.output_path(name, output_format)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        runner_script = (
            f"import sys; sys.path.insert(0, '{pkg_dir}'); "
            f"import yaml; "
            f"manifest = yaml.safe_load(open('{pkg_dir}/quilt.yaml')); "
            f"entry = manifest.get('entry_point', 'scraper.py'); "
            f"class_name = manifest.get('class_name', 'Scraper'); "
            f"import importlib.util; "
            f"spec = importlib.util.spec_from_file_location('mod', '{pkg_dir}/' + entry); "
            f"mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
            f"scraper = getattr(mod, class_name)(); "
            f"scraper.on_start({{}}); "
            f"df = scraper.on_run(); "
            f"df.to_csv('{out_path}', index=False); "
            f"scraper.on_stop(); "
        )

        result = subprocess.run(
            [python, "-c", runner_script],
            capture_output=True,
            text=True,
            cwd=pkg_dir,
        )

        if result.returncode == 0:
            return ScraperResult(success=True, output_path=out_path)
        else:
            return ScraperResult(success=False, error=result.stderr)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_scraper_engine.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/scraper_engine.py tests/coordinator/test_scraper_engine.py
git commit -m "feat(coordinator): add scraper engine for subprocess execution"
```

---

### Task 4: Archival Service

**Files:**
- Create: `coordinator/services/archival.py`
- Create: `tests/coordinator/test_archival.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_archival.py
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pandas as pd

from coordinator.services.archival import ArchivalService


@pytest.fixture
def archive_dir(tmp_path):
    d = tmp_path / "archive"
    d.mkdir()
    return str(d)


def test_archive_path(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    path = svc.archive_path("decision_log", "2025-01-01", "2025-01-31")
    assert "decision_log" in path
    assert "2025-01-01" in path
    assert path.endswith(".parquet")


def test_export_to_parquet(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame({
        "id": ["1", "2", "3"],
        "timestamp": ["2025-01-01", "2025-01-02", "2025-01-03"],
        "data": ["a", "b", "c"],
    })
    path = svc.export_to_parquet("decision_log", "2025-01-01", "2025-01-31", df)
    assert os.path.exists(path)
    loaded = pd.read_parquet(path)
    assert len(loaded) == 3


def test_export_empty_dataframe(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame()
    path = svc.export_to_parquet("trade_log", "2025-01-01", "2025-01-31", df)
    assert path is None


def test_list_archives(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame({"id": ["1"], "data": ["a"]})
    svc.export_to_parquet("decision_log", "2025-01-01", "2025-01-31", df)
    svc.export_to_parquet("trade_log", "2025-02-01", "2025-02-28", df)
    archives = svc.list_archives()
    assert len(archives) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_archival.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/archival.py
import logging
import os
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class ArchivalService:
    def __init__(self, archive_dir: str) -> None:
        self._archive_dir = archive_dir

    def archive_path(
        self, table_name: str, start: str, end: str
    ) -> str:
        return os.path.join(
            self._archive_dir, table_name, f"{start}_to_{end}.parquet"
        )

    def export_to_parquet(
        self,
        table_name: str,
        start: str,
        end: str,
        df: pd.DataFrame,
    ) -> Optional[str]:
        if df.empty:
            return None
        path = self.archive_path(table_name, start, end)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_parquet(path, index=False)
        logger.info("Archived %d rows to %s", len(df), path)
        return path

    def list_archives(self) -> list[dict]:
        results = []
        if not os.path.exists(self._archive_dir):
            return results
        for table_name in os.listdir(self._archive_dir):
            table_dir = os.path.join(self._archive_dir, table_name)
            if not os.path.isdir(table_dir):
                continue
            for f in os.listdir(table_dir):
                if f.endswith(".parquet"):
                    path = os.path.join(table_dir, f)
                    results.append({
                        "table_name": table_name,
                        "file_name": f,
                        "file_path": path,
                        "size_bytes": os.path.getsize(path),
                    })
        return results

    def load_archive(self, path: str) -> pd.DataFrame:
        return pd.read_parquet(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_archival.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/archival.py tests/coordinator/test_archival.py
git commit -m "feat(coordinator): add archival service for Parquet export"
```

---

### Task 5: Data API Routes

**Files:**
- Create: `coordinator/api/routes/data.py`
- Create: `tests/coordinator/test_data_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_data_api.py
import os
import pytest
from unittest.mock import patch, MagicMock

import pandas as pd


@pytest.mark.asyncio
async def test_get_market_data(client, tmp_path):
    market_dir = tmp_path / "market" / "polygon" / "AAPL"
    market_dir.mkdir(parents=True)
    df = pd.DataFrame({
        "timestamp": ["2025-01-01", "2025-01-02"],
        "open": [150.0, 151.0],
        "high": [151.0, 152.0],
        "low": [149.0, 150.0],
        "close": [150.5, 151.5],
        "volume": [1000, 1500],
    })
    df.to_parquet(str(market_dir / "1day.parquet"), index=False)

    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.load_market_data.return_value = df
        mock.return_value = svc

        response = await client.get("/api/data/market/AAPL?timeframe=1day")
        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 2
        assert body["data"][0]["close"] == 150.5


@pytest.mark.asyncio
async def test_get_market_data_not_found(client):
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.load_market_data.return_value = None
        mock.return_value = svc

        response = await client.get("/api/data/market/MISSING?timeframe=1day")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_custom_data(client):
    df = pd.DataFrame({"symbol": ["TSLA"], "score": [0.95]})
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.load_custom_data.return_value = df
        mock.return_value = svc

        response = await client.get("/api/data/custom/alpha-picks")
        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["symbol"] == "TSLA"


@pytest.mark.asyncio
async def test_list_available_data(client):
    with patch("coordinator.api.routes.data.get_data_service") as mock:
        svc = MagicMock()
        svc.list_available_market_data.return_value = [
            {"provider": "polygon", "symbol": "AAPL", "timeframe": "1day", "size_bytes": 1024},
        ]
        mock.return_value = svc

        response = await client.get("/api/data/available")
        assert response.status_code == 200
        assert len(response.json()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_data_api.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/api/routes/data.py
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from coordinator.services.data_service import DataService

router = APIRouter(prefix="/api/data", tags=["data"])

_data_service: Optional[DataService] = None


def set_data_service(svc: DataService) -> None:
    global _data_service
    _data_service = svc


def get_data_service() -> DataService:
    if _data_service is None:
        return DataService(market_data_dir="data/market", custom_data_dir="data/custom")
    return _data_service


@router.get("/market/{symbol}")
async def get_market_data(
    symbol: str,
    timeframe: str = Query("1day"),
    provider: str = Query("polygon"),
):
    svc = get_data_service()
    df = svc.load_market_data(provider, symbol, timeframe)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}/{timeframe}")
    return {"data": df.to_dict(orient="records")}


@router.get("/custom/{source_name}")
async def get_custom_data(source_name: str, fmt: str = Query("csv")):
    svc = get_data_service()
    df = svc.load_custom_data(source_name, fmt)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {source_name}")
    return {"data": df.to_dict(orient="records")}


@router.get("/available")
async def list_available():
    svc = get_data_service()
    return svc.list_available_market_data()
```

- [ ] **Step 4: Register router in create_app**

Add to `coordinator/main.py`:

```python
    from coordinator.api.routes.data import router as data_router
    app.include_router(data_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_data_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/data.py coordinator/main.py tests/coordinator/test_data_api.py
git commit -m "feat(coordinator): add data serving API for market and custom data"
```

---

### Task 6: Runs + Cash Flows API Routes

**Files:**
- Create: `coordinator/api/routes/runs.py`
- Create: `coordinator/api/routes/cash_flows.py`
- Create: `tests/coordinator/test_runs_api.py`
- Create: `tests/coordinator/test_cash_flows_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/coordinator/test_runs_api.py
import pytest


@pytest.fixture
async def seed_instance(client):
    acct = await client.post("/api/accounts", json={
        "name": "Run Acct", "broker_type": "alpaca",
        "credentials": {"k": "v"}, "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    worker = await client.post("/api/workers", json={
        "name": "Run Pi", "tailscale_ip": "100.64.0.10",
    })
    algo = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/run-algo", "name": "run-algo",
    })
    inst = await client.post(f"/api/algorithms/{algo.json()['id']}/instances", json={
        "account_id": acct.json()["id"], "worker_id": worker.json()["id"],
    })
    return inst.json()["id"]


@pytest.mark.asyncio
async def test_list_runs_empty(client, seed_instance):
    response = await client.get(f"/api/instances/{seed_instance}/runs")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_and_list_run(client, seed_instance):
    create_resp = await client.post(f"/api/instances/{seed_instance}/runs", json={
        "starting_equity": 50000.0,
    })
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["run_number"] == 1
    assert body["status"] == "running"
    assert body["starting_equity"] == 50000.0

    list_resp = await client.get(f"/api/instances/{seed_instance}/runs")
    assert len(list_resp.json()) == 1
```

```python
# tests/coordinator/test_cash_flows_api.py
import pytest


@pytest.fixture
async def seed_account(client):
    resp = await client.post("/api/accounts", json={
        "name": "CF Acct", "broker_type": "alpaca",
        "credentials": {"k": "v"}, "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_cash_flow(client, seed_account):
    response = await client.post(f"/api/accounts/{seed_account}/cash-flows", json={
        "type": "deposit",
        "amount": 10000.0,
        "notes": "Initial deposit",
    })
    assert response.status_code == 201
    assert response.json()["type"] == "deposit"
    assert response.json()["amount"] == 10000.0


@pytest.mark.asyncio
async def test_list_cash_flows(client, seed_account):
    await client.post(f"/api/accounts/{seed_account}/cash-flows", json={
        "type": "deposit", "amount": 10000.0,
    })
    await client.post(f"/api/accounts/{seed_account}/cash-flows", json={
        "type": "withdrawal", "amount": -2000.0,
    })
    response = await client.get(f"/api/accounts/{seed_account}/cash-flows")
    assert response.status_code == 200
    assert len(response.json()) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_runs_api.py tests/coordinator/test_cash_flows_api.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write implementations**

```python
# coordinator/api/routes/runs.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import AlgorithmRun, AlgorithmInstance

router = APIRouter(tags=["runs"])


class RunCreate(BaseModel):
    starting_equity: Optional[float] = None


def _to_response(run: AlgorithmRun) -> dict:
    return {
        "id": run.id,
        "instance_id": run.instance_id,
        "run_number": run.run_number,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "stopped_at": run.stopped_at.isoformat() if run.stopped_at else None,
        "starting_equity": run.starting_equity,
        "ending_equity": run.ending_equity,
        "net_pnl": run.net_pnl,
        "unrealized_pnl": run.unrealized_pnl,
        "total_fees": run.total_fees,
        "total_slippage": run.total_slippage,
        "trade_count": run.trade_count,
        "metrics": run.metrics,
        "equity_curve": run.equity_curve,
    }


@router.get("/api/instances/{instance_id}/runs")
async def list_runs(instance_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlgorithmRun)
        .where(AlgorithmRun.instance_id == instance_id)
        .order_by(AlgorithmRun.run_number.desc())
    )
    return [_to_response(r) for r in result.scalars().all()]


@router.post("/api/instances/{instance_id}/runs", status_code=201)
async def create_run(
    instance_id: str, body: RunCreate, db: AsyncSession = Depends(get_db)
):
    count_result = await db.execute(
        select(func.count(AlgorithmRun.id))
        .where(AlgorithmRun.instance_id == instance_id)
    )
    next_num = (count_result.scalar() or 0) + 1

    run = AlgorithmRun(
        instance_id=instance_id,
        run_number=next_num,
        status="running",
        starting_equity=body.starting_equity,
    )
    db.add(run)
    await db.flush()
    return _to_response(run)


@router.get("/api/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlgorithmRun).where(AlgorithmRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _to_response(run)
```

```python
# coordinator/api/routes/cash_flows.py
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import AccountCashFlow

router = APIRouter(tags=["cash_flows"])


class CashFlowCreate(BaseModel):
    type: str
    amount: float
    notes: Optional[str] = None


def _to_response(cf: AccountCashFlow) -> dict:
    return {
        "id": cf.id,
        "account_id": cf.account_id,
        "type": cf.type,
        "amount": cf.amount,
        "timestamp": cf.timestamp.isoformat() if cf.timestamp else None,
        "notes": cf.notes,
    }


@router.get("/api/accounts/{account_id}/cash-flows")
async def list_cash_flows(account_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AccountCashFlow)
        .where(AccountCashFlow.account_id == account_id)
        .order_by(AccountCashFlow.timestamp.desc())
    )
    return [_to_response(cf) for cf in result.scalars().all()]


@router.post("/api/accounts/{account_id}/cash-flows", status_code=201)
async def create_cash_flow(
    account_id: str, body: CashFlowCreate, db: AsyncSession = Depends(get_db)
):
    cf = AccountCashFlow(
        account_id=account_id,
        type=body.type,
        amount=body.amount,
        notes=body.notes,
    )
    db.add(cf)
    await db.flush()
    return _to_response(cf)
```

- [ ] **Step 4: Register routers in create_app**

Add to `coordinator/main.py`:

```python
    from coordinator.api.routes.runs import router as runs_router
    from coordinator.api.routes.cash_flows import router as cash_flows_router
    app.include_router(runs_router)
    app.include_router(cash_flows_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_runs_api.py tests/coordinator/test_cash_flows_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add coordinator/api/routes/runs.py coordinator/api/routes/cash_flows.py coordinator/main.py tests/coordinator/test_runs_api.py tests/coordinator/test_cash_flows_api.py
git commit -m "feat(coordinator): add runs and cash flows API routes"
```

---

### Task 7: Scheduler Setup

**Files:**
- Create: `coordinator/services/scheduler.py`
- Create: `tests/coordinator/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/coordinator/test_scheduler.py
from coordinator.services.scheduler import SchedulerService


def test_scheduler_creates():
    scheduler = SchedulerService()
    assert scheduler is not None


def test_add_cron_job():
    scheduler = SchedulerService()
    called = []

    def job():
        called.append(True)

    scheduler.add_cron_job("test-job", job, "*/5 * * * *")
    jobs = scheduler.list_jobs()
    assert any(j["id"] == "test-job" for j in jobs)


def test_remove_job():
    scheduler = SchedulerService()
    scheduler.add_cron_job("removable", lambda: None, "0 * * * *")
    scheduler.remove_job("removable")
    jobs = scheduler.list_jobs()
    assert not any(j["id"] == "removable" for j in jobs)


def test_list_empty():
    scheduler = SchedulerService()
    assert scheduler.list_jobs() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_scheduler.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# coordinator/services/scheduler.py
import logging
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def add_cron_job(
        self, job_id: str, func: Callable, cron_expr: str
    ) -> None:
        parts = cron_expr.split()
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
        self._scheduler.add_job(
            func, trigger=trigger, id=job_id, replace_existing=True
        )
        logger.info("Added cron job: %s (%s)", job_id, cron_expr)

    def remove_job(self, job_id: str) -> None:
        self._scheduler.remove_job(job_id)

    def list_jobs(self) -> list[dict]:
        return [
            {"id": job.id, "next_run": str(job.next_run_time)}
            for job in self._scheduler.get_jobs()
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/coordinator/test_scheduler.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add coordinator/services/scheduler.py tests/coordinator/test_scheduler.py
git commit -m "feat(coordinator): add APScheduler-based scheduler service"
```

---

### Task 8: Update pyproject.toml + Final Verification

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add data pipeline dependencies**

Add to the coordinator optional dependencies in `pyproject.toml`:

```
"pyarrow>=18.0.0",
"APScheduler>=3.10.0",
```

- [ ] **Step 2: Install and verify**

Run: `cd /home/jkern/dev/quilt-trader && pip install -e ".[coordinator,dev]"`
Expected: All dependencies install

- [ ] **Step 3: Run full test suite**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add pyarrow and APScheduler dependencies"
```
