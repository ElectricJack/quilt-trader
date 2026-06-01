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


from coordinator.services.asset_services.index import IndexAssetService, _KNOWN_INDEXES


@pytest.fixture
def index():
    return IndexAssetService()


class TestIndexCanonical:
    def test_known_indexes_has_37_entries(self):
        assert len(_KNOWN_INDEXES) == 37
        # spot-check a few from each category
        assert {"SPX", "OEX", "MID", "NDX", "COMP", "DJI", "RUT", "VLG"} <= _KNOWN_INDEXES
        assert {"VIX", "VIX1D", "VIX3M", "VVIX", "SKEW"} <= _KNOWN_INDEXES
        assert {"VXN", "RVX", "VXD", "GVZ", "OVX"} <= _KNOWN_INDEXES
        assert {"IRX", "FVX", "TNX", "TYX"} <= _KNOWN_INDEXES
        assert {"SOX", "XAU", "HGX", "OSX", "DXY"} <= _KNOWN_INDEXES

    def test_known_indexes_excludes_yfinance_aliases(self):
        # GSPC and IXIC are yfinance-specific aliases for SPX and COMP, not canonicals.
        assert "GSPC" not in _KNOWN_INDEXES
        assert "IXIC" not in _KNOWN_INDEXES

    def test_classify(self, index):
        assert index.classify("VIX") is True
        assert index.classify("SPX") is True
        assert index.classify("COMP") is True       # was IXIC
        assert index.classify("VIX1D") is True
        assert index.classify("AAPL") is False      # equity, not in set
        assert index.classify("GSPC") is False      # yfinance alias, not canonical
        assert index.classify("^VIX") is False      # has prefix
        assert index.classify("I:SPX") is False     # has prefix

    @pytest.mark.parametrize("canonical,provider,expected", [
        ("VIX", "polygon", "I:VIX"),
        ("SPX", "polygon", "I:SPX"),
        ("VIX1D", "polygon", "I:VIX1D"),
        ("SOX", "polygon", "I:SOX"),
        ("VIX", "yfinance", "^VIX"),
        ("SPX", "yfinance", "^GSPC"),          # explicit override
        ("COMP", "yfinance", "^IXIC"),         # explicit override
        ("DJI", "yfinance", "^DJI"),
        ("VIX3M", "yfinance", "^VIX3M"),       # default rule applies
    ])
    def test_resolve_symbol(self, index, canonical, provider, expected):
        assert index.resolve_symbol(canonical, provider) == expected

    def test_resolve_symbol_raises_on_non_canonical(self, index):
        with pytest.raises(ValueError, match="not a canonical index"):
            index.resolve_symbol("GSPC", "yfinance")    # yfinance alias, not canonical
        with pytest.raises(ValueError, match="not a canonical index"):
            index.resolve_symbol("^VIX", "polygon")

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("I:VIX", "polygon", "VIX"),
        ("I:SPX", "polygon", "SPX"),
        ("VIX", "polygon", "VIX"),             # already canonical
        ("^VIX", "yfinance", "VIX"),
        ("^GSPC", "yfinance", "SPX"),
        ("^IXIC", "yfinance", "COMP"),
        ("VIX", "yfinance", "VIX"),            # already canonical
    ])
    def test_canonicalize(self, index, provider_form, provider, expected):
        assert index.canonicalize(provider_form, provider) == expected

    def test_canonicalize_rejects_unknown(self, index):
        with pytest.raises(ValueError, match="not a recognized index"):
            index.canonicalize("AAPL", "polygon")


from coordinator.services.asset_services.options import OptionsAssetService


@pytest.fixture
def options():
    return OptionsAssetService()


class TestOptionsCanonical:
    def test_classify(self, options):
        assert options.classify("AAPL240119C00150000") is True
        assert options.classify("SPY240731P00340000") is True
        assert options.classify("BRK240119C00100000") is True  # 3-char underlying
        assert options.classify("AAPL") is False               # not OCC
        assert options.classify("BTCUSD") is False             # not OCC
        assert options.classify("") is False
        assert options.classify("O:AAPL240119C00150000") is False  # has prefix

    @pytest.mark.parametrize("canonical,provider,expected", [
        ("AAPL240119C00150000", "polygon", "O:AAPL240119C00150000"),
        ("SPY240731P00340000", "polygon", "O:SPY240731P00340000"),
        ("AAPL240119C00150000", "alpaca", "AAPL240119C00150000"),
    ])
    def test_resolve_symbol(self, options, canonical, provider, expected):
        assert options.resolve_symbol(canonical, provider) == expected

    def test_resolve_symbol_raises_on_non_canonical(self, options):
        with pytest.raises(ValueError, match="not a canonical option"):
            options.resolve_symbol("O:AAPL240119C00150000", "polygon")  # prefix present
        with pytest.raises(ValueError, match="not a canonical option"):
            options.resolve_symbol("AAPL", "polygon")

    @pytest.mark.parametrize("provider_form,provider,expected", [
        ("O:AAPL240119C00150000", "polygon", "AAPL240119C00150000"),
        ("AAPL240119C00150000", "polygon", "AAPL240119C00150000"),  # already canonical
        ("AAPL240119C00150000", "alpaca", "AAPL240119C00150000"),
    ])
    def test_canonicalize(self, options, provider_form, provider, expected):
        assert options.canonicalize(provider_form, provider) == expected

    def test_canonicalize_rejects_unknown(self, options):
        with pytest.raises(ValueError, match="not a recognized option"):
            options.canonicalize("AAPL", "polygon")
