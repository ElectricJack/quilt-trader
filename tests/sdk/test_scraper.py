import pytest
import pandas as pd
from sdk.scraper import QuiltScraper


class DummyScraper(QuiltScraper):
    def on_run(self):
        return pd.DataFrame({"symbol": ["AAPL", "MSFT"], "score": [0.8, 0.6]})


class IncompleteScraper(QuiltScraper):
    pass


class TestQuiltScraper:
    def test_subclass_implements_on_run(self):
        scraper = DummyScraper()
        result = scraper.on_run()
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert list(result.columns) == ["symbol", "score"]

    def test_on_start_default_noop(self):
        scraper = DummyScraper()
        scraper.on_start({})

    def test_on_stop_default_noop(self):
        scraper = DummyScraper()
        scraper.on_stop()

    def test_incomplete_raises_on_run(self):
        scraper = IncompleteScraper()
        with pytest.raises(NotImplementedError):
            scraper.on_run()
