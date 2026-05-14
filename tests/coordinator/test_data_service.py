import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date
import pandas as pd
from coordinator.services.data_service import DataService

@pytest.fixture
def data_dir(tmp_path):
    return str(tmp_path / "data")

@pytest.fixture
def data_service(data_dir):
    return DataService(
        market_data_dir=os.path.join(data_dir, "market"),
        custom_data_dir=os.path.join(data_dir, "custom"),
    )

def test_market_data_path(data_service):
    path = data_service.market_data_path("polygon", "AAPL", "1day")
    assert "polygon" in path
    assert "AAPL" in path
    assert "1day" in path
    assert path.endswith(".parquet")

def test_save_and_load_market_data(data_service):
    df = pd.DataFrame({
        "timestamp": ["2025-01-01", "2025-01-02"],
        "open": [150.0, 151.0], "high": [151.0, 152.0],
        "low": [149.0, 150.0], "close": [150.5, 151.5], "volume": [1000, 1500],
    })
    data_service.save_market_data("polygon", "AAPL", "1day", df)
    loaded = data_service.load_market_data("polygon", "AAPL", "1day")
    assert len(loaded) == 2
    assert loaded.iloc[0]["close"] == 150.5

def test_load_market_data_not_found(data_service):
    result = data_service.load_market_data("polygon", "MISSING", "1day")
    assert result is None

def test_save_custom_data(data_service):
    df = pd.DataFrame({"symbol": ["TSLA", "NVDA"], "score": [0.95, 0.88]})
    data_service.save_custom_data("alpha-picks", df, "csv")
    path = data_service.custom_data_path("alpha-picks", "csv")
    assert os.path.exists(path)

def test_load_custom_data_csv(data_service):
    df = pd.DataFrame({"symbol": ["TSLA"], "score": [0.95]})
    data_service.save_custom_data("alpha-picks", df, "csv")
    loaded = data_service.load_custom_data("alpha-picks", "csv")
    assert len(loaded) == 1
    assert loaded.iloc[0]["symbol"] == "TSLA"

def test_load_custom_data_not_found(data_service):
    result = data_service.load_custom_data("missing", "csv")
    assert result is None

def test_list_available_market_data(data_service):
    df = pd.DataFrame({"timestamp": ["2025-01-01"], "close": [150.0]})
    data_service.save_market_data("polygon", "AAPL", "1day", df)
    data_service.save_market_data("polygon", "TSLA", "1day", df)
    available = data_service.list_available_market_data()
    assert len(available) >= 2

def test_save_market_data_merges_overlapping_downloads(tmp_path):
    ds = DataService(market_data_dir=str(tmp_path / "market"), custom_data_dir=str(tmp_path / "custom"))

    # First save: timestamps 1-10 with close = 100.x
    df_a = pd.DataFrame({
        "timestamp": [f"2025-01-{i:02d}T13:00:00+00:00" for i in range(1, 11)],
        "close": [100.0 + i for i in range(10)],
        "volume": [1000 + i for i in range(10)],
    })
    ds.save_market_data("polygon", "TEST", "1day", df_a)

    # Second save with overlap: timestamps 6-15 with close = 200.x
    df_b = pd.DataFrame({
        "timestamp": [f"2025-01-{i:02d}T13:00:00+00:00" for i in range(6, 16)],
        "close": [200.0 + i for i in range(10)],
        "volume": [2000 + i for i in range(10)],
    })
    ds.save_market_data("polygon", "TEST", "1day", df_b)

    merged = ds.load_market_data("polygon", "TEST", "1day")
    assert len(merged) == 15  # union of timestamps 1-15
    # Check that the OVERLAP region (timestamps 6-10) has B's values, not A's.
    # df_b uses enumerate index i=0..9 for close, so timestamp 2025-01-06 → i=0 → close=200.0.
    overlap = merged[merged["timestamp"] == "2025-01-06T13:00:00+00:00"]
    assert overlap["close"].iloc[0] == 200.0  # B's value (200.0 + 0)
    # Confirm B overwrote A: A's value for that timestamp would have been 100.0 + 5 = 105.0
    assert overlap["close"].iloc[0] != 105.0
    # First non-overlap (timestamp 1) should still be A's value: 100.0 + 0 = 100.0
    first = merged[merged["timestamp"] == "2025-01-01T13:00:00+00:00"]
    assert first["close"].iloc[0] == 100.0  # A's value (100.0 + 0)
