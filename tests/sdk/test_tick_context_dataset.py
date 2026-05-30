from datetime import datetime, date, timedelta, timezone
import pandas as pd
import pytest
from unittest.mock import patch
from sdk.context import TickContext


class _FakeCtx(TickContext):
    """Minimal TickContext subclass for testing the default dataset() implementation."""

    def __init__(self, timestamp):
        self._ts = timestamp

    @property
    def timestamp(self):
        return self._ts

    # Stub the other abstracts so the class is concrete
    @property
    def mode(self): return "backtest"
    @property
    def cash(self): return 0
    @property
    def account_value(self): return 0
    @property
    def buying_power(self): return 0
    @property
    def positions(self): return {}
    def market_data(self, *a, **kw): return pd.DataFrame()
    def data(self, *a, **kw): return pd.DataFrame()
    def option_chain(self, *a, **kw): return pd.DataFrame()


@pytest.fixture
def ctx():
    return _FakeCtx(datetime(2024, 6, 1, tzinfo=timezone.utc))


# Lazy import path: load_dataset lives in the coordinator module, not re-bound
# at sdk.context module level, so we patch the source.
_PATCH_TARGET = "coordinator.services.datasets.storage.load_dataset"


def test_dataset_rejects_negative_lag(ctx):
    with pytest.raises(ValueError, match="lag must be non-negative"):
        ctx.dataset("fmp.house_disclosures", lag=timedelta(seconds=-1))


def test_dataset_rejects_lookback_with_start(ctx):
    with pytest.raises(ValueError, match="mutually exclusive"):
        ctx.dataset("fmp.x", lookback_days=30, start=date(2024, 1, 1))


def test_dataset_passes_effective_as_of_to_load_dataset(ctx):
    with patch(_PATCH_TARGET) as mock_load:
        mock_load.return_value = pd.DataFrame()
        ctx.dataset("fmp.x")
        kwargs = mock_load.call_args.kwargs
        assert kwargs["as_of"] == datetime(2024, 6, 1, tzinfo=timezone.utc)


def test_dataset_lag_subtracts_from_timestamp(ctx):
    with patch(_PATCH_TARGET) as mock_load:
        mock_load.return_value = pd.DataFrame()
        ctx.dataset("fmp.x", lag=timedelta(days=1))
        kwargs = mock_load.call_args.kwargs
        assert kwargs["as_of"] == datetime(2024, 5, 31, tzinfo=timezone.utc)


def test_dataset_lookback_days_derives_window(ctx):
    with patch(_PATCH_TARGET) as mock_load:
        mock_load.return_value = pd.DataFrame()
        ctx.dataset("fmp.x", lookback_days=30)
        kwargs = mock_load.call_args.kwargs
        assert kwargs["end"] == date(2024, 6, 1)
        assert kwargs["start"] == date(2024, 5, 2)


def test_dataset_has_no_as_of_parameter():
    """Algorithm-facing API must NOT accept as_of — runtime clock is sole source of truth."""
    import inspect
    sig = inspect.signature(TickContext.dataset)
    assert "as_of" not in sig.parameters


def test_live_tick_context_inherits_tick_context():
    from worker.context import LiveTickContext
    from sdk.context import TickContext
    assert issubclass(LiveTickContext, TickContext)
