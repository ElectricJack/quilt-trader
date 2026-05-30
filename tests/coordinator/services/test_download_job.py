import pytest
from coordinator.services.download_job import JobDispatcher, BarsJobDispatcher
from coordinator.database.models import MarketDataDownload


def test_jobdispatcher_is_abstract():
    with pytest.raises(TypeError):
        JobDispatcher()


def test_bars_dispatcher_declares_job_model():
    assert BarsJobDispatcher.job_model is MarketDataDownload


def test_bars_dispatcher_is_concrete():
    d = BarsJobDispatcher()
    # should not raise
    assert d.job_model is MarketDataDownload


def test_subclass_must_implement_execute():
    class Bad(JobDispatcher):
        job_model = MarketDataDownload

    with pytest.raises(TypeError):
        Bad()
