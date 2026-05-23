import pytest
import pandas as pd
from datetime import date
from coordinator.services.data_service import DataService

@pytest.fixture
def data_service(tmp_path):
    return DataService(
        market_data_dir=str(tmp_path / "market"),
        custom_data_dir=str(tmp_path / "custom"),
    )

def _sample_chain_df():
    return pd.DataFrame([
        {"ticker": "O:SPY250620C00450000", "strike": 450.0, "option_type": "call",
         "bid": 5.1, "ask": 5.3, "last": 5.2, "volume": 1200,
         "open_interest": 8000, "implied_volatility": 0.25},
        {"ticker": "O:SPY250620P00450000", "strike": 450.0, "option_type": "put",
         "bid": 4.1, "ask": 4.3, "last": 4.2, "volume": 900,
         "open_interest": 6000, "implied_volatility": 0.27},
    ])

def test_option_chain_path(data_service):
    path = data_service.option_chain_path("polygon", "SPY", date(2025, 6, 20))
    assert path.endswith("polygon/SPY/options/2025-06-20/chain.parquet")

def test_save_and_load_option_chain(data_service):
    df = _sample_chain_df()
    data_service.save_option_chain("polygon", "SPY", date(2025, 6, 20), df)
    loaded = data_service.load_option_chain("polygon", "SPY", date(2025, 6, 20))
    assert loaded is not None
    assert len(loaded) == 2
    assert set(loaded.columns) == set(df.columns)

def test_load_option_chain_missing_returns_none(data_service):
    result = data_service.load_option_chain("polygon", "SPY", date(2025, 6, 20))
    assert result is None

def test_list_option_chain_expirations(data_service):
    df = _sample_chain_df()
    data_service.save_option_chain("polygon", "SPY", date(2025, 6, 20), df)
    data_service.save_option_chain("polygon", "SPY", date(2025, 7, 18), df)
    expirations = data_service.list_option_chain_expirations("polygon", "SPY")
    assert sorted(expirations) == [date(2025, 6, 20), date(2025, 7, 18)]
