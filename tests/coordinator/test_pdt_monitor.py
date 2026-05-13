import pytest
from datetime import date
from coordinator.services.pdt_monitor import PDTMonitor, PDTResult


def test_check_signal_no_day_trades():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block", existing_day_trades=[],
        signal_legs=[{"symbol": "AAPL", "asset_type": "equities", "side": "sell"}],
        open_positions={"AAPL": {"opened_today": True}}, today=date(2025, 3, 15))
    assert result.approved is True
    assert result.day_trade_count == 1
    assert result.warning is False


def test_check_signal_crypto_exempt():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[{"day_trade_date": date(2025, 3, 13)}, {"day_trade_date": date(2025, 3, 14)}, {"day_trade_date": date(2025, 3, 15)}],
        signal_legs=[{"symbol": "BTC/USD", "asset_type": "crypto", "side": "sell"}],
        open_positions={"BTC/USD": {"opened_today": True}}, today=date(2025, 3, 15))
    assert result.approved is True
    assert result.would_be_day_trade is False


def test_check_signal_warning_at_3():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="warn",
        existing_day_trades=[{"day_trade_date": date(2025, 3, 12)}, {"day_trade_date": date(2025, 3, 13)}],
        signal_legs=[{"symbol": "TSLA", "asset_type": "equities", "side": "sell"}],
        open_positions={"TSLA": {"opened_today": True}}, today=date(2025, 3, 15))
    assert result.approved is True
    assert result.warning is True
    assert result.day_trade_count == 3


def test_check_signal_blocked_at_4():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[{"day_trade_date": date(2025, 3, 12)}, {"day_trade_date": date(2025, 3, 13)}, {"day_trade_date": date(2025, 3, 14)}],
        signal_legs=[{"symbol": "NVDA", "asset_type": "equities", "side": "sell"}],
        open_positions={"NVDA": {"opened_today": True}}, today=date(2025, 3, 15))
    assert result.approved is False
    assert result.reason == "PDT limit reached"
    assert result.day_trade_count == 4


def test_check_signal_warn_mode_does_not_block():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="warn",
        existing_day_trades=[{"day_trade_date": date(2025, 3, 12)}, {"day_trade_date": date(2025, 3, 13)}, {"day_trade_date": date(2025, 3, 14)}],
        signal_legs=[{"symbol": "NVDA", "asset_type": "equities", "side": "sell"}],
        open_positions={"NVDA": {"opened_today": True}}, today=date(2025, 3, 15))
    assert result.approved is True
    assert result.warning is True


def test_check_signal_off_mode():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="off",
        existing_day_trades=[{"day_trade_date": date(2025, 3, 12)}, {"day_trade_date": date(2025, 3, 13)}, {"day_trade_date": date(2025, 3, 14)}],
        signal_legs=[{"symbol": "NVDA", "asset_type": "equities", "side": "sell"}],
        open_positions={"NVDA": {"opened_today": True}}, today=date(2025, 3, 15))
    assert result.approved is True
    assert result.warning is False


def test_not_a_day_trade_if_not_closing_same_day():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[{"day_trade_date": date(2025, 3, 12)}, {"day_trade_date": date(2025, 3, 13)}, {"day_trade_date": date(2025, 3, 14)}],
        signal_legs=[{"symbol": "AAPL", "asset_type": "equities", "side": "buy"}],
        open_positions={}, today=date(2025, 3, 15))
    assert result.approved is True
    assert result.would_be_day_trade is False


def test_multi_leg_with_mixed_crypto_equity():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[{"day_trade_date": date(2025, 3, 12)}, {"day_trade_date": date(2025, 3, 13)}, {"day_trade_date": date(2025, 3, 14)}],
        signal_legs=[
            {"symbol": "BTC/USD", "asset_type": "crypto", "side": "sell"},
            {"symbol": "AAPL", "asset_type": "equities", "side": "sell"},
        ],
        open_positions={"BTC/USD": {"opened_today": True}, "AAPL": {"opened_today": True}},
        today=date(2025, 3, 15))
    assert result.approved is False


def test_rolling_window_excludes_old_trades():
    monitor = PDTMonitor()
    result = monitor.check_signal(
        pdt_mode="block",
        existing_day_trades=[{"day_trade_date": date(2025, 3, 7)}, {"day_trade_date": date(2025, 3, 8)}, {"day_trade_date": date(2025, 3, 9)}],
        signal_legs=[{"symbol": "AAPL", "asset_type": "equities", "side": "sell"}],
        open_positions={"AAPL": {"opened_today": True}}, today=date(2025, 3, 15))
    assert result.approved is True
