from __future__ import annotations

import pandas as pd


class ScraperAuthError(Exception):
    """A scrape failed because the site no longer accepts the scraper's session.

    The coordinator treats these apart from ordinary failures: it pauses
    scheduled runs until a person signs in again. Packages adopt the contract
    by subclassing AuthRequired or BotBlocked; nothing matches on class names.
    """

    kind = "auth"


class AuthRequired(ScraperAuthError):
    """Login wall, or an expired or missing session."""

    kind = "auth_required"


class BotBlocked(ScraperAuthError):
    """Anti-bot block page (PerimeterX, Cloudflare, ...)."""

    kind = "bot_blocked"


class QuiltScraper:
    """Base class that all data scrapers must implement."""

    def on_start(self, config: dict) -> None:
        pass

    def on_run(self) -> pd.DataFrame:
        raise NotImplementedError

    def on_stop(self) -> None:
        pass

    def browser_launch_options(self) -> dict:
        """kwargs for launch_persistent_context, minus user_data_dir and headless.

        A site trusts a session only for the browser fingerprint that created
        it, so the login helper launches with exactly these options. Packages
        with an `auth:` block return the same options their own fetch code uses.
        """
        return {}
