"""Failing tests for data-layer audit findings F13, F14, F15.

See docs/superpowers/research/2026-06-11-correctness-audit-findings.md §5.
Each test asserts the CORRECT behavior; it fails today because the bug exists
and must pass once the finding is fixed.
"""
import pytest
import pandas as pd
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from coordinator.services.datasets.registry import DatasetSpec, Pagination, register, clear_registry
from coordinator.services.datasets.storage import DatasetService, load_dataset, set_default_service
from coordinator.services.data_providers.theta import ThetaDataProvider
from coordinator.services.data_providers.tradier import TradierProvider


# ---------------------------------------------------------------------------
# F13 — bitemporal dataset store
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()
    set_default_service(None)


@pytest.fixture
def service(tmp_path):
    svc = DatasetService(data_root=tmp_path)
    set_default_service(svc)
    return svc


def _restatable_spec() -> DatasetSpec:
    """A dataset whose id_columns identify the LOGICAL row (event + ticker),
    deliberately excluding the knowledge date — the shape under which a
    source restatement must ADD a bitemporal row, never replace one."""
    spec = DatasetSpec(
        name="vendor.fundamentals",
        provider="vendor",
        endpoint_path="/x",
        event_date_column="eventDate",
        knowledge_date_column="knowledgeDate",
        symbol_keyed=False,
        id_columns=("eventDate", "ticker"),
        columns={"eventDate": "date", "knowledgeDate": "date",
                 "ticker": "str", "value": "float"},
        pagination=Pagination.PAGE,
    )
    register(spec)
    return spec


@pytest.mark.asyncio
async def test_f13_revision_preserves_original_observation_for_earlier_as_of(service):
    """F13: coordinator/services/datasets/storage.py:78 — upsert() dedupes with
    drop_duplicates(subset=id_cols, keep="last"), deleting the ORIGINAL
    observation when a source restates a row, so load_dataset(as_of=<before
    restatement>) leaks the revised value (look-ahead)."""
    spec = _restatable_spec()
    # Original observation: event 2024-01-01, known 2024-01-10, value 100.
    await service.upsert(spec, [
        {"eventDate": "2024-01-01", "knowledgeDate": "2024-01-10",
         "ticker": "ACME", "value": 100.0},
    ])
    # Restatement a month later: same logical row, value revised to 120.
    await service.upsert(spec, [
        {"eventDate": "2024-01-01", "knowledgeDate": "2024-02-10",
         "ticker": "ACME", "value": 120.0},
    ])

    # As-of BEFORE the restatement: only the original observation was known.
    df = load_dataset(
        "vendor.fundamentals",
        as_of=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    assert len(df) == 1, (
        "load_dataset(as_of=2024-01-15) must return the original 2024-01-10 "
        f"observation; got {len(df)} rows — the original row was destroyed by "
        "upsert's keep='last' dedupe"
    )
    assert df.iloc[0]["value"] == 100.0, (
        f"as-of read before the restatement must see the ORIGINAL value 100.0, "
        f"got {df.iloc[0]['value']} (revised value leaked back in time)"
    )


# ---------------------------------------------------------------------------
# F14 — Theta provider intraday timestamps
# ---------------------------------------------------------------------------

def _make_response(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=body)
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


def _theta_provider_with(intraday_rows: list[dict]) -> ThetaDataProvider:
    http = AsyncMock()
    http.post.return_value = _make_response({"token": "tok"})
    http.get.return_value = _make_response({"response": intraday_rows})
    return ThetaDataProvider(username="u", password="p", http_client=http)


@pytest.mark.asyncio
async def test_f14_intraday_ms_of_day_is_eastern_not_utc():
    """F14: coordinator/services/data_providers/theta.py:101-108 —
    _fetch_intraday treats ThetaData's ms_of_day (US/Eastern ms-since-midnight)
    as UTC, so every intraday bar lands 4-5 hours early."""
    provider = _theta_provider_with([
        # 2024-01-02 is winter (EST = UTC-5); 34200000 ms = 09:30 Eastern.
        {"date": "2024-01-02", "ms_of_day": 34200000,
         "open": 10000, "high": 10100, "low": 9900, "close": 10050, "volume": 5000},
    ])
    bars = await provider.fetch_bars(
        symbol="AAPL", timeframe="1min",
        start=date(2024, 1, 2), end=date(2024, 1, 2),
    )
    assert len(bars) == 1
    ts = datetime.fromisoformat(bars[0]["timestamp"])
    expected = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)  # 09:30 ET
    assert ts == expected, (
        f"ms_of_day=34200000 on 2024-01-02 is 09:30 US/Eastern = "
        f"2024-01-02T14:30:00+00:00; got {ts.isoformat()} (Eastern ms applied "
        "to UTC midnight)"
    )


@pytest.mark.asyncio
async def test_f14_intraday_handles_2400_print_without_crashing():
    """F14: coordinator/services/data_providers/theta.py:104-108 —
    ms_of_day=86400000 (a 24:00 print) makes hour=ms//3600000 == 24, so
    ts.replace(hour=24) raises ValueError and the whole fetch crashes."""
    provider = _theta_provider_with([
        {"date": "2024-01-02", "ms_of_day": 86400000,
         "open": 10000, "high": 10100, "low": 9900, "close": 10050, "volume": 5000},
    ])
    try:
        bars = await provider.fetch_bars(
            symbol="AAPL", timeframe="1min",
            start=date(2024, 1, 2), end=date(2024, 1, 2),
        )
    except ValueError as exc:
        pytest.fail(
            f"fetch_bars must not crash on a 24:00 ms_of_day row; raised "
            f"ValueError: {exc}"
        )
    assert isinstance(bars, list)


# ---------------------------------------------------------------------------
# F15 — Tradier daily bars stamped at midnight UTC
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f15_daily_bars_stamped_at_market_open_not_midnight():
    """F15: coordinator/services/data_providers/tradier.py:116-120 — daily bars
    are stamped datetime.combine(date, min.time()) in UTC (midnight) instead of
    the repo's open-time-stamp convention (09:30 ET = 14:30 UTC in January)."""
    http = AsyncMock()
    http.get.return_value = _make_response({
        "history": {
            "day": {"date": "2024-01-02", "open": 100.0, "high": 105.0,
                    "low": 99.0, "close": 103.0, "volume": 1000000},
        }
    })
    provider = TradierProvider(
        access_token="tok", http_client=http, min_request_interval_s=0,
    )
    bars = await provider.fetch_bars(
        symbol="AAPL", timeframe="1day",
        start=date(2024, 1, 2), end=date(2024, 1, 2),
    )
    assert len(bars) == 1
    ts = datetime.fromisoformat(bars[0]["timestamp"])
    expected = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)  # 09:30 ET open
    assert ts == expected, (
        f"daily bars must be stamped at the US-equity market open "
        f"(2024-01-02T14:30:00+00:00); got {ts.isoformat()} (midnight UTC)"
    )
