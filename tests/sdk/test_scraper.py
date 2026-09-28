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


class TestBrowserLaunchOptions:
    def test_default_is_empty(self):
        assert DummyScraper().browser_launch_options() == {}

    def test_subclass_override(self):
        class Launching(DummyScraper):
            def browser_launch_options(self):
                return {"user_agent": "UA", "viewport": {"width": 1280, "height": 800}}

        assert Launching().browser_launch_options()["user_agent"] == "UA"


class TestTypedAuthErrors:
    def test_kinds(self):
        from sdk.scraper import AuthRequired, BotBlocked, ScraperAuthError

        assert ScraperAuthError.kind == "auth"
        assert AuthRequired.kind == "auth_required"
        assert BotBlocked.kind == "bot_blocked"
        assert issubclass(AuthRequired, ScraperAuthError)
        assert issubclass(BotBlocked, ScraperAuthError)
        assert issubclass(ScraperAuthError, Exception)

    def test_package_adopts_by_subclassing(self):
        from sdk.scraper import AuthRequired, BotBlocked, ScraperAuthError

        class AuthExpiredError(AuthRequired):
            pass

        class BotBlockedError(BotBlocked):
            pass

        err = AuthExpiredError("session expired; sign in again")
        assert isinstance(err, ScraperAuthError)
        assert err.kind == "auth_required"
        assert str(err) == "session expired; sign in again"
        assert BotBlockedError("403").kind == "bot_blocked"

    def test_exported_from_sdk(self):
        import sdk

        assert sdk.AuthRequired.kind == "auth_required"
        assert sdk.BotBlocked.kind == "bot_blocked"
        assert sdk.ScraperAuthError.kind == "auth"
