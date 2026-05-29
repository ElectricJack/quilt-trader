import pytest
from coordinator.services.datasets.registry import (
    DatasetSpec, Pagination, register, get, list_all, clear_registry,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_registry()
    yield
    clear_registry()


def _spec(name="fmp.house_disclosures", knowledge_col="disclosureDate"):
    return DatasetSpec(
        name=name,
        provider="fmp",
        endpoint_path="/stable/house-latest",
        event_date_column="transactionDate",
        knowledge_date_column=knowledge_col,
        symbol_keyed=False,
        id_columns=("disclosureDate", "transactionDate", "name", "symbol"),
        columns={"symbol": "str", "transactionDate": "date", "disclosureDate": "date"},
        pagination=Pagination.PAGE,
    )


def test_register_then_get_round_trips():
    s = _spec()
    register(s)
    assert get("fmp.house_disclosures") is s


def test_register_duplicate_raises():
    register(_spec())
    with pytest.raises(ValueError, match="duplicate dataset: fmp.house_disclosures"):
        register(_spec())


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        get("nope.nada")


def test_list_all_returns_all_registered():
    register(_spec("a.one"))
    register(_spec("a.two"))
    assert {s.name for s in list_all()} == {"a.one", "a.two"}


def test_spec_is_frozen():
    s = _spec()
    with pytest.raises(Exception):  # FrozenInstanceError
        s.endpoint_path = "/different"


def test_knowledge_column_can_be_none():
    s = _spec(knowledge_col=None)
    assert s.knowledge_date_column is None


def test_pagination_enum_values():
    assert Pagination.SINGLE == "single"
    assert Pagination.PAGE == "page"
    assert Pagination.DATE_RANGE == "date_range"
