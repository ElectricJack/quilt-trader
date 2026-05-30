import pytest
from coordinator.services.datasets.adapter import (
    DatasetAdapter, AdapterAuthError, PageCallback, StatusCallback, RowsCallback,
)


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        DatasetAdapter()  # type: ignore[abstract]


def test_adapter_auth_error_has_message():
    e = AdapterAuthError("nope")
    assert str(e) == "nope"


def test_callback_types_exist():
    assert PageCallback is not None
    assert StatusCallback is not None
    assert RowsCallback is not None


def test_subclass_must_implement_fetch_dataset():
    class Bad(DatasetAdapter):
        provider = "x"
    with pytest.raises(TypeError):
        Bad()


def test_subclass_implementing_fetch_dataset_works():
    class Good(DatasetAdapter):
        provider = "x"
        async def fetch_dataset(self, spec, params, *, on_page=None, on_status=None, on_rows=None):
            return []
    g = Good()
    assert g.provider == "x"
