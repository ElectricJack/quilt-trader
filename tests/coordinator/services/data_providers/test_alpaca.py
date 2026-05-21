import pytest
from unittest.mock import MagicMock, patch
from datetime import date, datetime, timezone

from coordinator.services.data_providers.alpaca import AlpacaProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(ts: str, open_: float, high: float, low: float, close: float, volume: float) -> MagicMock:
    """Build a mock alpaca bar object."""
    bar = MagicMock()
    bar.timestamp = datetime.fromisoformat(ts)
    bar.open = open_
    bar.high = high
    bar.low = low
    bar.close = close
    bar.volume = volume
    return bar


BARS = [
    _make_bar("2025-01-02T14:30:00+00:00", 100.0, 105.0, 99.0, 103.0, 1000000),
    _make_bar("2025-01-02T14:31:00+00:00", 103.0, 107.0, 102.0, 106.0, 1200000),
    _make_bar("2025-01-02T14:32:00+00:00", 106.0, 110.0, 105.5, 109.0, 900000),
]


def _make_bars_response(symbol: str, bars: list) -> MagicMock:
    """Build a mock StockBarsResponse-like object."""
    resp = MagicMock()
    resp.get = MagicMock(return_value=bars)
    # Also support dict-style indexing: resp[symbol]
    resp.__getitem__ = MagicMock(return_value=bars)
    return resp


def _make_empty_response() -> MagicMock:
    """Build a mock response where the symbol is absent."""
    resp = MagicMock()
    resp.get = MagicMock(return_value=[])
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_response_returns_correct_bar_dicts():
    """Normal response returns correct bar dicts with all required fields."""
    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = _make_bars_response("AAPL", BARS)

    with patch(
        "coordinator.services.data_providers.alpaca.StockHistoricalDataClient",
        return_value=mock_client,
    ):
        provider = AlpacaProvider(api_key="key", secret_key="secret")
        bars = await provider.fetch_bars(
            symbol="AAPL",
            timeframe="1min",
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )

    assert len(bars) == 3

    # First bar field mapping
    assert bars[0]["open"] == 100.0
    assert bars[0]["high"] == 105.0
    assert bars[0]["low"] == 99.0
    assert bars[0]["close"] == 103.0
    assert bars[0]["volume"] == 1000000
    assert "timestamp" in bars[0]
    assert "2025-01-02" in bars[0]["timestamp"]

    # Second bar
    assert bars[1]["close"] == 106.0

    # Third bar
    assert bars[2]["close"] == 109.0

    # All required keys present on every bar
    required_keys = {"timestamp", "open", "high", "low", "close", "volume"}
    for bar in bars:
        assert required_keys <= bar.keys()


@pytest.mark.asyncio
async def test_empty_result_returns_empty_list():
    """When symbol is not present in the response, an empty list is returned."""
    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = _make_empty_response()

    with patch(
        "coordinator.services.data_providers.alpaca.StockHistoricalDataClient",
        return_value=mock_client,
    ):
        provider = AlpacaProvider(api_key="key", secret_key="secret")
        bars = await provider.fetch_bars(
            symbol="ZZZZ",
            timeframe="1min",
            start=date(2025, 1, 2),
            end=date(2025, 1, 6),
        )

    assert bars == []


@pytest.mark.asyncio
async def test_unsupported_timeframe_raises_value_error():
    """Unsupported timeframes raise ValueError before making any API call."""
    mock_client = MagicMock()

    with patch(
        "coordinator.services.data_providers.alpaca.StockHistoricalDataClient",
        return_value=mock_client,
    ):
        provider = AlpacaProvider(api_key="key", secret_key="secret")

        with pytest.raises(ValueError, match="timeframe"):
            await provider.fetch_bars(
                symbol="AAPL",
                timeframe="4hour",
                start=date(2025, 1, 2),
                end=date(2025, 1, 6),
            )

    mock_client.get_stock_bars.assert_not_called()


@pytest.mark.asyncio
async def test_all_supported_timeframes_are_accepted():
    """All documented timeframes (1min, 5min, 15min, 1hour, 1day) are accepted."""
    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = _make_empty_response()

    with patch(
        "coordinator.services.data_providers.alpaca.StockHistoricalDataClient",
        return_value=mock_client,
    ):
        provider = AlpacaProvider(api_key="key", secret_key="secret", min_request_interval_s=0)
        for tf in ("1min", "5min", "15min", "1hour", "1day"):
            bars = await provider.fetch_bars(
                symbol="AAPL",
                timeframe=tf,
                start=date(2025, 1, 2),
                end=date(2025, 1, 6),
            )
            assert isinstance(bars, list)


@pytest.mark.asyncio
async def test_callbacks_invoked():
    """on_page, on_status, and on_bars callbacks are invoked during a successful fetch."""
    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = _make_bars_response("AAPL", BARS)

    with patch(
        "coordinator.services.data_providers.alpaca.StockHistoricalDataClient",
        return_value=mock_client,
    ):
        provider = AlpacaProvider(api_key="key", secret_key="secret")

        page_calls: list[tuple] = []
        status_msgs: list[str] = []
        bars_batches: list[list[dict]] = []

        async def on_page(page_idx: int, total: int, fraction) -> None:
            page_calls.append((page_idx, total, fraction))

        async def on_status(msg: str) -> None:
            status_msgs.append(msg)

        async def on_bars(bars: list[dict]) -> None:
            bars_batches.append(bars)

        bars = await provider.fetch_bars(
            symbol="AAPL",
            timeframe="1min",
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
            on_page=on_page,
            on_status=on_status,
            on_bars=on_bars,
        )

    assert len(bars) == 3
    assert len(page_calls) == 1
    assert page_calls[0][0] == 0
    assert page_calls[0][1] == 3
    assert len(bars_batches) == 1
    assert len(bars_batches[0]) == 3
