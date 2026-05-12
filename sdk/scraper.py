from __future__ import annotations

import pandas as pd


class QuiltScraper:
    """Base class that all data scrapers must implement."""

    def on_start(self, config: dict) -> None:
        pass

    def on_run(self) -> pd.DataFrame:
        raise NotImplementedError

    def on_stop(self) -> None:
        pass
