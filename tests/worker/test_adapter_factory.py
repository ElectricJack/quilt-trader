import pytest

from worker.adapter_factory import CredentialError, make_broker_adapter
from worker.alpaca_adapter import AlpacaAdapter
from worker.tradier_adapter import TradierAdapter


class TestAdapterFactory:
    def test_alpaca_paper(self):
        adapter = make_broker_adapter(
            "alpaca", "paper", {"api_key": "k", "secret_key": "s"}
        )
        assert isinstance(adapter, AlpacaAdapter)
        assert adapter._paper is True

    def test_alpaca_live(self):
        adapter = make_broker_adapter(
            "alpaca", "live", {"api_key": "k", "secret_key": "s"}
        )
        assert isinstance(adapter, AlpacaAdapter)
        assert adapter._paper is False

    def test_tradier_paper_is_sandbox(self):
        adapter = make_broker_adapter(
            "tradier", "paper", {"access_token": "t", "account_id": "A1"}
        )
        assert isinstance(adapter, TradierAdapter)
        assert adapter._sandbox is True
        assert "sandbox" in adapter._base_url

    def test_tradier_live(self):
        adapter = make_broker_adapter(
            "tradier", "live", {"access_token": "t", "account_id": "A1"}
        )
        assert isinstance(adapter, TradierAdapter)
        assert adapter._sandbox is False

    def test_missing_alpaca_credentials(self):
        with pytest.raises(CredentialError, match="api_key"):
            make_broker_adapter("alpaca", "paper", {"secret_key": "s"})
        with pytest.raises(CredentialError, match="secret_key"):
            make_broker_adapter("alpaca", "paper", {"api_key": "k"})

    def test_missing_tradier_credentials(self):
        with pytest.raises(CredentialError, match="access_token"):
            make_broker_adapter("tradier", "paper", {"account_id": "A1"})
        with pytest.raises(CredentialError, match="account_id"):
            make_broker_adapter("tradier", "paper", {"access_token": "t"})

    def test_unknown_broker(self):
        with pytest.raises(ValueError, match="Unknown broker_type"):
            make_broker_adapter("etrade", "paper", {})

    def test_unsupported_environment(self):
        with pytest.raises(ValueError, match="environment"):
            make_broker_adapter("alpaca", "production", {"api_key": "k", "secret_key": "s"})

    def test_ib_not_implemented(self):
        with pytest.raises(NotImplementedError):
            make_broker_adapter("interactive_brokers", "paper", {})
