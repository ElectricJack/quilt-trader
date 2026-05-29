"""Hypothesis property test: load_dataset() NEVER returns forward-biased rows.

The invariant under test: for any dataset content and any as_of value,
``load_dataset()`` must not return rows where ``knowledge_date > as_of``.

The test is synchronous (asyncio.run wraps the upsert call) so that
hypothesis can drive it without requiring pytest-asyncio integration
on the @given-decorated function.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings as hsettings
from hypothesis import strategies as st

from coordinator.services.datasets.registry import (
    DatasetSpec,
    Pagination,
    clear_registry,
    get as registry_get,
    register,
)
from coordinator.services.datasets.storage import (
    DatasetService,
    load_dataset,
    set_default_service,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def configured(tmp_path):
    clear_registry()
    svc = DatasetService(data_root=tmp_path)
    set_default_service(svc)
    register(DatasetSpec(
        name="test.fixture",
        provider="test",
        endpoint_path="/x",
        event_date_column="ev",
        knowledge_date_column="kn",
        symbol_keyed=False,
        id_columns=("ev", "kn", "id"),
        columns={"ev": "date", "kn": "date", "id": "str"},
        pagination=Pagination.PAGE,
    ))
    yield svc
    clear_registry()
    set_default_service(None)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_dates = st.dates(
    min_value=pd.Timestamp("2000-01-01").date(),
    max_value=pd.Timestamp("2030-12-31").date(),
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------

@given(
    rows=st.lists(
        st.tuples(_dates, _dates, st.text(min_size=1, max_size=8)),
        min_size=0,
        max_size=100,
    ),
    as_of_date=_dates,
)
@hsettings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_load_dataset_never_returns_future_knowledge(configured, rows, as_of_date):
    """No row returned by load_dataset() may have knowledge_date > as_of.

    The interesting cases are those where *some* rows have a knowledge_date
    AFTER as_of — those must be filtered out.  Rows with knowledge_date on or
    before as_of must pass through unchanged.
    """
    svc = configured
    spec = registry_get("test.fixture")

    # Reset the parquet file so each hypothesis example starts from scratch.
    # Without this, rows from previous examples accumulate on disk and the
    # dedup-by-id semantics make the test state undefined across iterations.
    parquet_path = svc._path_for(spec, None)
    if parquet_path.exists():
        parquet_path.unlink()

    if rows:
        payload = [
            {"ev": str(ev), "kn": str(kn), "id": f"{i}-{tag}"}
            for i, (ev, kn, tag) in enumerate(rows)
        ]
        asyncio.run(svc.upsert(spec, payload))

    as_of_dt = pd.Timestamp(as_of_date).to_pydatetime()
    df = load_dataset("test.fixture", as_of=as_of_dt)

    if not df.empty:
        # storage is tz-naive UTC; compare against naive Timestamp
        cutoff = pd.Timestamp(as_of_date)
        violating = df[df["knowledge_date"] > cutoff]
        assert violating.empty, (
            f"Forward-biased rows returned for as_of={as_of_date}:\n"
            f"{violating[['event_date', 'knowledge_date']].to_string()}"
        )


# ---------------------------------------------------------------------------
# Regression: as_of must be a required keyword (no default)
# ---------------------------------------------------------------------------

def test_load_dataset_without_as_of_raises_type_error(configured):
    with pytest.raises(TypeError):
        load_dataset("test.fixture")  # type: ignore[call-arg]
