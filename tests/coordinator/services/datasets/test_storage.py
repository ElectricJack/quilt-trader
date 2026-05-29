import pytest
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from coordinator.services.datasets.registry import DatasetSpec, Pagination, register, clear_registry
from coordinator.services.datasets.storage import DatasetService, load_dataset, set_default_service


@pytest.fixture(autouse=True)
def _clean():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def service(tmp_path):
    return DatasetService(data_root=tmp_path)


def _bitemporal_spec(name="fmp.house_disclosures", symbol_keyed=False, knowledge_col="disclosureDate"):
    spec = DatasetSpec(
        name=name, provider="fmp", endpoint_path="/x",
        event_date_column="transactionDate",
        knowledge_date_column=knowledge_col,
        symbol_keyed=symbol_keyed,
        id_columns=("transactionDate", "disclosureDate", "name", "symbol") if knowledge_col
                   else ("date", "symbol"),
        columns={"symbol": "str", "transactionDate": "date", "disclosureDate": "date",
                 "name": "str", "amount": "str"},
        pagination=Pagination.PAGE,
    )
    register(spec)
    return spec


@pytest.mark.asyncio
async def test_upsert_creates_file_with_normalized_columns(service, tmp_path):
    spec = _bitemporal_spec()
    rows = [
        {"transactionDate": "2024-01-15", "disclosureDate": "2024-02-12",
         "symbol": "NVDA", "name": "Pelosi", "amount": "$1M-$5M"},
    ]
    n = await service.upsert(spec, rows)
    assert n == 1
    path = tmp_path / "datasets" / "fmp" / "house_disclosures.parquet"
    assert path.exists()
    df = pd.read_parquet(path)
    assert "event_date" in df.columns
    assert "knowledge_date" in df.columns
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-01-15")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-02-12")
    assert df.iloc[0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_upsert_dedups_by_id_columns_keeping_latest(service):
    spec = _bitemporal_spec()
    base = {"transactionDate": "2024-01-15", "disclosureDate": "2024-02-12",
            "symbol": "NVDA", "name": "Pelosi"}
    await service.upsert(spec, [{**base, "amount": "old"}])
    await service.upsert(spec, [{**base, "amount": "new"}])
    df = pd.read_parquet(spec_path(service, spec))
    assert len(df) == 1
    assert df.iloc[0]["amount"] == "new"


@pytest.mark.asyncio
async def test_upsert_keeps_amendments_as_separate_rows(service):
    spec = _bitemporal_spec()
    base = {"transactionDate": "2024-01-15", "symbol": "NVDA", "name": "Pelosi"}
    await service.upsert(spec, [{**base, "disclosureDate": "2024-02-12", "amount": "$1M"}])
    await service.upsert(spec, [{**base, "disclosureDate": "2024-02-20", "amount": "$2M"}])
    df = pd.read_parquet(spec_path(service, spec))
    assert len(df) == 2


@pytest.mark.asyncio
async def test_upsert_symbol_keyed_writes_per_symbol(service, tmp_path):
    spec = _bitemporal_spec(name="fmp.insider_trading", symbol_keyed=True)
    await service.upsert(spec, [{"transactionDate": "2024-01-01", "disclosureDate": "2024-01-15",
                                  "symbol": "AAPL", "name": "X"}], symbol="AAPL")
    await service.upsert(spec, [{"transactionDate": "2024-01-02", "disclosureDate": "2024-01-16",
                                  "symbol": "NVDA", "name": "Y"}], symbol="NVDA")
    aapl = tmp_path / "datasets" / "fmp" / "insider_trading" / "AAPL.parquet"
    nvda = tmp_path / "datasets" / "fmp" / "insider_trading" / "NVDA.parquet"
    assert aapl.exists() and nvda.exists()


@pytest.mark.asyncio
async def test_upsert_single_timestamp_dataset_copies_event_to_knowledge(service):
    spec = DatasetSpec(
        name="fmp.earnings_calendar", provider="fmp", endpoint_path="/x",
        event_date_column="date", knowledge_date_column=None,
        symbol_keyed=False, id_columns=("date", "symbol"),
        columns={"date": "date", "symbol": "str"}, pagination=Pagination.DATE_RANGE,
    )
    register(spec)
    await service.upsert(spec, [{"date": "2024-03-01", "symbol": "AAPL"}])
    df = pd.read_parquet(spec_path(service, spec))
    assert df.iloc[0]["event_date"] == pd.Timestamp("2024-03-01")
    assert df.iloc[0]["knowledge_date"] == pd.Timestamp("2024-03-01")


@pytest.mark.asyncio
async def test_upsert_schema_evolution_adds_new_column_as_nan(service):
    spec = _bitemporal_spec()
    await service.upsert(spec, [{"transactionDate": "2024-01-15", "disclosureDate": "2024-02-12",
                                  "symbol": "X", "name": "A"}])
    await service.upsert(spec, [{"transactionDate": "2024-01-16", "disclosureDate": "2024-02-13",
                                  "symbol": "X", "name": "B", "amount": "$1M",
                                  "newField": "hello"}])
    df = pd.read_parquet(spec_path(service, spec))
    assert "newField" in df.columns
    old_row = df[df["name"] == "A"].iloc[0]
    assert pd.isna(old_row["newField"])


def spec_path(service, spec):
    short = spec.name.split(".", 1)[1]
    if spec.symbol_keyed:
        return service._data_root / "datasets" / spec.provider / short  # caller picks symbol file
    return service._data_root / "datasets" / spec.provider / f"{short}.parquet"


# ---------------------------------------------------------------------------
# Task 3: load_dataset() tests
# ---------------------------------------------------------------------------

@pytest.fixture
def configured_service(service):
    set_default_service(service)
    yield service
    set_default_service(None)


@pytest.mark.asyncio
async def test_load_dataset_requires_as_of_keyword(configured_service):
    _bitemporal_spec()
    with pytest.raises(TypeError):
        load_dataset("fmp.house_disclosures")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_load_dataset_filters_knowledge_after_as_of(configured_service):
    spec = _bitemporal_spec()
    await configured_service.upsert(spec, [
        {"transactionDate": "2024-01-15", "disclosureDate": "2024-02-12",
         "symbol": "NVDA", "name": "P"},  # knowledge 2024-02-12 — visible at as_of=2024-03-01
        {"transactionDate": "2024-03-01", "disclosureDate": "2024-04-01",
         "symbol": "TSLA", "name": "G"},  # knowledge 2024-04-01 — hidden at as_of=2024-03-01
    ])
    df = load_dataset("fmp.house_disclosures", as_of=datetime(2024, 3, 1, tzinfo=timezone.utc))
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_load_dataset_returns_empty_when_path_missing(configured_service):
    _bitemporal_spec()
    df = load_dataset("fmp.house_disclosures", as_of=datetime(2024, 3, 1, tzinfo=timezone.utc))
    assert df.empty


@pytest.mark.asyncio
async def test_load_dataset_applies_event_date_window(configured_service):
    spec = _bitemporal_spec()
    await configured_service.upsert(spec, [
        {"transactionDate": "2023-12-01", "disclosureDate": "2023-12-15",
         "symbol": "A", "name": "X"},
        {"transactionDate": "2024-01-15", "disclosureDate": "2024-01-30",
         "symbol": "B", "name": "Y"},
        {"transactionDate": "2024-06-01", "disclosureDate": "2024-06-15",
         "symbol": "C", "name": "Z"},
    ])
    df = load_dataset(
        "fmp.house_disclosures",
        as_of=datetime(2024, 12, 31, tzinfo=timezone.utc),
        start=pd.Timestamp("2024-01-01").date(),
        end=pd.Timestamp("2024-05-31").date(),
    )
    assert list(df["symbol"]) == ["B"]


@pytest.mark.asyncio
async def test_load_dataset_symbol_keyed_reads_correct_file(configured_service, tmp_path):
    spec = _bitemporal_spec(name="fmp.insider_trading", symbol_keyed=True)
    await configured_service.upsert(spec, [
        {"transactionDate": "2024-01-01", "disclosureDate": "2024-01-15",
         "symbol": "AAPL", "name": "X"}
    ], symbol="AAPL")
    df = load_dataset("fmp.insider_trading", as_of=datetime(2024, 12, 31, tzinfo=timezone.utc),
                      symbol="AAPL")
    assert len(df) == 1
