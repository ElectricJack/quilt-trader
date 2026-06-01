"""Canonical-form contract tests for AssetService implementations."""
import pytest
from coordinator.services.asset_services.crypto import CryptoAssetService


@pytest.fixture
def crypto():
    return CryptoAssetService()


class TestCryptoCanonical:
    def test_classify_accepts_canonical_forms(self, crypto):
        assert crypto.classify("BTCUSD") is True
        assert crypto.classify("ETHUSD") is True
        assert crypto.classify("BTCUSDT") is True
        assert crypto.classify("SOLUSDT") is True

    def test_classify_rejects_non_canonical(self, crypto):
        assert crypto.classify("BTC") is False         # missing suffix
        assert crypto.classify("BTC-USD") is False     # dashed
        assert crypto.classify("BTC/USD") is False     # slashed
        assert crypto.classify("X:BTCUSD") is False    # polygon prefix
        assert crypto.classify("") is False
        assert crypto.classify("AAPL") is False        # equity

    @pytest.mark.parametrize("provider,expected", [
        ("polygon", "X:BTCUSD"),
        ("yfinance", "BTC-USD"),
        ("alpaca", "BTC/USD"),
        ("alpaca_stream", "BTC/USD"),
        ("coinbase", "BTC-USD"),
    ])
    def test_resolve_symbol_canonical_inputs(self, crypto, provider, expected):
        assert crypto.resolve_symbol("BTCUSD", provider) == expected

    def test_resolve_symbol_raises_on_non_canonical(self, crypto):
        with pytest.raises(ValueError, match="not a canonical crypto"):
            crypto.resolve_symbol("BTC", "polygon")
        with pytest.raises(ValueError, match="not a canonical crypto"):
            crypto.resolve_symbol("BTC-USD", "yfinance")

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("BTC-USD", "yfinance", "BTCUSD"),
        ("BTC/USD", "alpaca", "BTCUSD"),
        ("BTC/USD", "alpaca_stream", "BTCUSD"),
        ("BTC-USD", "coinbase", "BTCUSD"),
        ("X:BTCUSD", "polygon", "BTCUSD"),
        ("BTCUSD", "polygon", "BTCUSD"),          # already canonical
        ("BTCUSDT", "alpaca", "BTCUSDT"),
    ])
    def test_canonicalize_provider_forms(self, crypto, provider_form, provider, expected):
        assert crypto.canonicalize(provider_form, provider) == expected

    def test_canonicalize_rejects_bare_ambiguous(self, crypto):
        # bare "BTC" could mean equity (real iBIT-like tickers) — refuse to guess
        with pytest.raises(ValueError, match="ambiguous"):
            crypto.canonicalize("BTC", "polygon")

    @pytest.mark.parametrize("canonical,provider", [
        ("BTCUSD", "polygon"),
        ("BTCUSD", "yfinance"),
        ("BTCUSD", "alpaca"),
        ("BTCUSD", "coinbase"),
        ("ETHUSD", "polygon"),
        ("BTCUSDT", "alpaca"),
    ])
    def test_round_trip(self, crypto, canonical, provider):
        provider_form = crypto.resolve_symbol(canonical, provider)
        assert crypto.canonicalize(provider_form, provider) == canonical


from coordinator.services.asset_services.equity import EquityAssetService


@pytest.fixture
def equity():
    return EquityAssetService()


class TestEquityCanonical:
    def test_classify_accepts_canonical(self, equity):
        assert equity.classify("AAPL") is True
        assert equity.classify("SPY") is True
        assert equity.classify("QQQ") is True
        assert equity.classify("BRK.B") is True
        assert equity.classify("BF.B") is True
        assert equity.classify("F") is True             # 1-char (Ford)
        assert equity.classify("BRKB") is True          # 4 chars no dot

    def test_classify_rejects_non_canonical(self, equity):
        assert equity.classify("") is False
        assert equity.classify("BRK-B") is False        # dash not dot
        assert equity.classify("aapl") is False         # lowercase
        assert equity.classify("AAPL.US") is False      # multi-letter suffix
        assert equity.classify("^GSPC") is False        # yfinance prefix
        assert equity.classify("I:SPX") is False        # polygon prefix

    def test_classify_excludes_bare_crypto(self, equity):
        # Well-known crypto bare-tickers fall through to validate() raising
        # with the multi-class hint pointing the user at BTCUSD.
        for s in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "LTC", "BCH", "XRP", "ADA", "ETC"):
            assert equity.classify(s) is False, f"{s} should not classify as equity"

    @pytest.mark.parametrize("canonical,provider,expected", [
        ("AAPL", "polygon", "AAPL"),
        ("AAPL", "yfinance", "AAPL"),
        ("BRK.B", "yfinance", "BRK-B"),
        ("BRK.A", "yfinance", "BRK-A"),
        ("BF.B", "yfinance", "BF-B"),
        ("BRK.B", "polygon", "BRK.B"),
        ("SPY", "alpaca", "SPY"),
    ])
    def test_resolve_symbol(self, equity, canonical, provider, expected):
        assert equity.resolve_symbol(canonical, provider) == expected

    def test_resolve_symbol_raises_on_non_canonical(self, equity):
        with pytest.raises(ValueError, match="not a canonical equity"):
            equity.resolve_symbol("BRK-B", "polygon")
        with pytest.raises(ValueError, match="not a canonical equity"):
            equity.resolve_symbol("BTC", "polygon")

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("AAPL", "polygon", "AAPL"),
        ("AAPL", "yfinance", "AAPL"),
        ("BRK-B", "yfinance", "BRK.B"),
        ("BRK-A", "yfinance", "BRK.A"),
        ("BF-B", "yfinance", "BF.B"),
        ("BRK.B", "polygon", "BRK.B"),
    ])
    def test_canonicalize(self, equity, provider_form, provider, expected):
        assert equity.canonicalize(provider_form, provider) == expected

    def test_canonicalize_rejects_unknown(self, equity):
        with pytest.raises(ValueError, match="not a recognized equity"):
            equity.canonicalize("BTC", "polygon")  # bare crypto, not equity
