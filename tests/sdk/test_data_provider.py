import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from sdk.cli.data_provider import DataProvider, StandaloneDataProvider, ConnectedDataProvider
from sdk.cli.config import QuiltDevConfig

class TestStandaloneDataProvider:
    def test_get_market_data_from_local_file(self, tmp_path):
        data_dir = tmp_path / "data" / "market"
        data_dir.mkdir(parents=True)
        csv_file = data_dir / "AAPL_1day.csv"
        csv_file.write_text("timestamp,open,high,low,close,volume\n2026-01-02,150.0,155.0,149.0,154.0,1000000\n2026-01-03,154.0,156.0,153.0,155.5,900000\n")
        config = QuiltDevConfig(data_mode="standalone")
        provider = StandaloneDataProvider(config, data_dir=tmp_path / "data")
        df = provider.get_market_data("AAPL", "1day")
        assert len(df) == 2
        assert "close" in df.columns
        assert df.iloc[0]["close"] == 154.0

    def test_get_market_data_file_not_found(self, tmp_path):
        config = QuiltDevConfig(data_mode="standalone")
        provider = StandaloneDataProvider(config, data_dir=tmp_path / "data")
        df = provider.get_market_data("MISSING", "1day")
        assert df is None

    def test_get_custom_data_csv(self, tmp_path):
        data_dir = tmp_path / "data" / "custom"
        data_dir.mkdir(parents=True)
        (data_dir / "alpha-picks.csv").write_text("symbol,score\nAAPL,95\nGOOG,88\n")
        config = QuiltDevConfig(data_mode="standalone")
        provider = StandaloneDataProvider(config, data_dir=tmp_path / "data")
        df = provider.get_custom_data("alpha-picks.csv")
        assert len(df) == 2
        assert df.iloc[0]["symbol"] == "AAPL"

    def test_get_custom_data_json(self, tmp_path):
        data_dir = tmp_path / "data" / "custom"
        data_dir.mkdir(parents=True)
        (data_dir / "signals.json").write_text(json.dumps([{"symbol": "TSLA", "action": "buy"}]))
        config = QuiltDevConfig(data_mode="standalone")
        provider = StandaloneDataProvider(config, data_dir=tmp_path / "data")
        df = provider.get_custom_data("signals.json")
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "TSLA"

class TestConnectedDataProvider:
    def test_get_market_data_from_coordinator(self):
        config = QuiltDevConfig(data_mode="connected", coordinator_url="http://100.1.2.3:8000")
        provider = ConnectedDataProvider(config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"timestamp": "2026-01-02", "open": 150.0, "high": 155.0, "low": 149.0, "close": 154.0, "volume": 1000000}]}
        with patch("sdk.cli.data_provider.httpx") as mock_httpx:
            mock_httpx.get.return_value = mock_response
            df = provider.get_market_data("AAPL", "1day")
        assert len(df) == 1
        assert df.iloc[0]["close"] == 154.0
        mock_httpx.get.assert_called_once_with("http://100.1.2.3:8000/api/data/market/AAPL", params={"timeframe": "1day"}, timeout=30)

    def test_get_market_data_coordinator_error(self):
        config = QuiltDevConfig(data_mode="connected", coordinator_url="http://100.1.2.3:8000")
        provider = ConnectedDataProvider(config)
        mock_response = MagicMock()
        mock_response.status_code = 404
        with patch("sdk.cli.data_provider.httpx") as mock_httpx:
            mock_httpx.get.return_value = mock_response
            df = provider.get_market_data("MISSING", "1day")
        assert df is None

    def test_get_custom_data_from_coordinator(self):
        config = QuiltDevConfig(data_mode="connected", coordinator_url="http://100.1.2.3:8000")
        provider = ConnectedDataProvider(config)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"symbol": "AAPL", "score": 95}]}
        with patch("sdk.cli.data_provider.httpx") as mock_httpx:
            mock_httpx.get.return_value = mock_response
            df = provider.get_custom_data("alpha-picks")
        assert len(df) == 1
        mock_httpx.get.assert_called_once_with("http://100.1.2.3:8000/api/data/custom/alpha-picks", timeout=30)
