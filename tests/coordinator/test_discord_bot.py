import pytest
from coordinator.services.discord_bot import DiscordNotifier, format_trade_event, format_algo_event, format_pdt_event


def test_format_trade_event():
    payload = {"symbol": "AAPL", "side": "buy", "quantity": 100, "filled_price": 150.50, "fees": 1.00, "pnl": 500.0}
    msg = format_trade_event(payload)
    assert "AAPL" in msg
    assert "buy" in msg.lower()
    assert "150.50" in msg


def test_format_algo_event():
    payload = {"algorithm_name": "momentum-scalper", "account_name": "Alpaca Main", "old_status": "running", "new_status": "stopped"}
    msg = format_algo_event(payload)
    assert "momentum-scalper" in msg
    assert "stopped" in msg


def test_format_pdt_event():
    payload = {"account_name": "Alpaca Main", "day_trade_count": 3, "remaining": 1}
    msg = format_pdt_event(payload)
    assert "3" in msg
    assert "Alpaca Main" in msg


def test_notifier_channel_routing():
    notifier = DiscordNotifier()
    notifier.set_route("trade_executed", "trades-channel")
    notifier.set_route("algo_error", "alerts-channel")
    assert notifier.get_channel("trade_executed") == "trades-channel"
    assert notifier.get_channel("algo_error") == "alerts-channel"
    assert notifier.get_channel("unknown") is None


def test_notifier_severity_filter():
    notifier = DiscordNotifier()
    notifier.set_route("algo_started", "status-channel", min_severity="warning")
    assert notifier.should_send("algo_started", "info") is False
    assert notifier.should_send("algo_started", "warning") is True
    assert notifier.should_send("algo_started", "error") is True


def test_notifier_disable_event():
    notifier = DiscordNotifier()
    notifier.set_route("trade_executed", "trades-channel")
    notifier.disable_route("trade_executed")
    assert notifier.get_channel("trade_executed") is None
