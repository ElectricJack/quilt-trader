import os
import pytest
import pandas as pd

from coordinator.services.data_service import DataService


@pytest.fixture
def ds(tmp_path):
    return DataService(
        market_data_dir=str(tmp_path / "market"),
        custom_data_dir=str(tmp_path / "custom"),
    )


def _df():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-01", "2024-01-02"], utc=True),
        "open": [1.0, 1.1], "high": [1.2, 1.3], "low": [0.9, 1.0],
        "close": [1.1, 1.2], "volume": [100, 200],
    })


class TestSymbolValidation:
    def test_save_market_data_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.save_market_data("polygon", "BTC", "1min", _df())

    def test_save_market_data_accepts_canonical(self, ds):
        path = ds.save_market_data("polygon", "BTCUSD", "1min", _df())
        assert "BTCUSD" in path
        assert os.path.exists(path)

    def test_load_market_data_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.load_market_data("polygon", "BTC", "1min")

    def test_market_data_path_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.market_data_path("polygon", "BTC", "1min")

    def test_delete_market_data_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.delete_market_data("polygon", "BTC", "1min")

    def test_latest_market_data_timestamp_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.latest_market_data_timestamp("polygon", "BTC", "1min")

    def test_earliest_market_data_timestamp_rejects_non_canonical(self, ds):
        with pytest.raises(ValueError, match="not a canonical"):
            ds.earliest_market_data_timestamp("polygon", "BTC", "1min")

    def test_canonical_path_uses_canonical_symbol(self, ds):
        # The on-disk path uses canonical form, not provider-form
        path = ds.market_data_path("polygon", "BTCUSD", "1min")
        assert path.endswith("polygon/BTCUSD/1min.parquet")
        assert "X:" not in path

    def test_share_class_equity_path(self, ds):
        # BRK.B is canonical (not BRK-B)
        path = ds.market_data_path("yfinance", "BRK.B", "1day")
        assert path.endswith("yfinance/BRK.B/1day.parquet")
