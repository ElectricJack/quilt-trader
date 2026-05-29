import importlib
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock
from coordinator.services.datasets.providers.fmp import FMPAdapter
from coordinator.services.datasets.storage import DatasetService
from coordinator.services.datasets.registry import get, clear_registry


def _resp(status, body):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body)
    r.raise_for_status = MagicMock()
    return r


@pytest.fixture(autouse=True)
def _reset():
    import coordinator.services.datasets.providers.fmp_datasets as fd
    clear_registry()
    importlib.reload(fd)
    yield
    clear_registry()


@pytest.fixture
def svc(tmp_path):
    return DatasetService(data_root=tmp_path)


@pytest.fixture
def quota():
    q = MagicMock()
    q.acquire = AsyncMock()
    q.mark_exhausted = AsyncMock()
    return q


def _adapter(http, quota):
    return FMPAdapter(
        api_key="K",
        http_client=http,
        quota_tracker=quota,
        daily_limit=250,
        min_request_interval_s=0.0,
    )


@pytest.mark.asyncio
async def test_house_disclosures_round_trip(svc, quota, tmp_path):
    http = MagicMock()
    http.get = AsyncMock(side_effect=[
        _resp(200, [{
            "symbol": "NVDA", "name": "Pelosi, Nancy", "office": "CA-12",
            "district": "12", "transactionDate": "2024-01-15",
            "disclosureDate": "2024-02-12", "amount": "$1,000,001 - $5,000,000",
            "type": "Buy", "assetDescription": "NVIDIA stock", "link": "http://x",
        }]),
        _resp(200, []),
    ])
    spec = get("fmp.house_disclosures")
    rows = await _adapter(http, quota).fetch_dataset(spec, {})
    await svc.upsert(spec, rows)
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "house_disclosures.parquet")
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-01-15")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-02-12")
    assert df.iloc[0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_senate_disclosures_round_trip(svc, quota, tmp_path):
    http = MagicMock()
    http.get = AsyncMock(side_effect=[
        _resp(200, [{
            "symbol": "TSLA", "firstName": "Tommy", "lastName": "Tuberville",
            "office": "Senate AL", "transactionDate": "2024-03-01",
            "disclosureDate": "2024-04-10", "amount": "$15,001 - $50,000",
            "type": "Sale", "assetDescription": "Tesla stock", "link": "http://y",
        }]),
        _resp(200, []),
    ])
    spec = get("fmp.senate_disclosures")
    rows = await _adapter(http, quota).fetch_dataset(spec, {})
    await svc.upsert(spec, rows)
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "senate_disclosures.parquet")
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-03-01")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-04-10")


@pytest.mark.asyncio
async def test_insider_trading_round_trip(svc, quota, tmp_path):
    http = MagicMock()
    http.get = AsyncMock(side_effect=[
        _resp(200, [{
            "symbol": "AAPL", "reportingName": "Cook, Timothy",
            "typeOfOwner": "officer: CEO", "transactionType": "S-Sale",
            "transactionDate": "2024-05-01", "filingDate": "2024-05-03",
            "securitiesTransacted": 1000, "price": 175.5,
            "securityName": "Common Stock", "link": "http://z",
        }]),
        _resp(200, []),
    ])
    spec = get("fmp.insider_trading")
    rows = await _adapter(http, quota).fetch_dataset(spec, {"symbol": "AAPL"})
    await svc.upsert(spec, rows, symbol="AAPL")
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "insider_trading" / "AAPL.parquet")
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-05-01")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-05-03")
    assert df.iloc[0]["price"] == 175.5


@pytest.mark.asyncio
async def test_income_statement_round_trip(svc, quota, tmp_path):
    http = MagicMock()
    http.get = AsyncMock(return_value=_resp(200, [{
        "symbol": "AAPL", "date": "2024-09-30", "acceptedDate": "2024-10-29 18:06:25",
        "fillingDate": "2024-10-30", "period": "Q4", "calendarYear": "2024",
        "cik": "0000320193", "reportedCurrency": "USD",
        "revenue": 90000000000, "netIncome": 24000000000, "eps": 1.55, "epsDiluted": 1.54,
    }]))
    spec = get("fmp.income_statement")
    rows = await _adapter(http, quota).fetch_dataset(spec, {"symbol": "AAPL"})
    await svc.upsert(spec, rows, symbol="AAPL")
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "income_statement" / "AAPL.parquet")
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-09-30")
    # acceptedDate is the knowledge timestamp
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-10-29 18:06:25")
    assert "fillingDate" in df.columns  # FMP's typo, preserved as informational column


@pytest.mark.asyncio
async def test_earnings_calendar_round_trip_single_timestamp(svc, quota, tmp_path):
    from datetime import date
    http = MagicMock()
    http.get = AsyncMock(side_effect=[
        _resp(200, [{
            "date": "2024-04-25", "symbol": "AMZN",
            "eps": 1.23, "epsEstimated": 1.10,
            "revenue": 140000000000, "revenueEstimated": 138000000000,
            "time": "amc", "fiscalDateEnding": "2024-03-31",
        }]),
        _resp(200, []),
    ])
    spec = get("fmp.earnings_calendar")
    rows = await _adapter(http, quota).fetch_dataset(
        spec, {"from": date(2024, 4, 1), "to": date(2024, 4, 30)},
    )
    await svc.upsert(spec, rows)
    df = pd.read_parquet(tmp_path / "datasets" / "fmp" / "earnings_calendar.parquet")
    # Single-timestamp dataset: knowledge_date == event_date
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-04-25")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-04-25")
