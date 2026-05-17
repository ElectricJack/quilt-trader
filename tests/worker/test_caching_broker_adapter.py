from unittest.mock import MagicMock


def test_get_account_info_caches_within_ttl():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.get_account_info.return_value = {"cash": 100, "portfolio_value": 200, "buying_power": 100}
    wrapper = CachingBrokerAdapter(inner, account_state_ttl=60)
    wrapper.get_account_info()
    wrapper.get_account_info()
    assert inner.get_account_info.call_count == 1


def test_get_account_info_refreshes_after_ttl(monkeypatch):
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.get_account_info.return_value = {"cash": 1}
    wrapper = CachingBrokerAdapter(inner, account_state_ttl=10)
    fake_time = [1000.0]
    monkeypatch.setattr("worker.caching_broker_adapter.time.monotonic", lambda: fake_time[0])
    wrapper.get_account_info()
    fake_time[0] += 11
    wrapper.get_account_info()
    assert inner.get_account_info.call_count == 2


def test_invalidate_forces_refresh():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.get_account_info.return_value = {"cash": 1}
    wrapper = CachingBrokerAdapter(inner, account_state_ttl=60)
    wrapper.get_account_info()
    wrapper.invalidate()
    wrapper.get_account_info()
    assert inner.get_account_info.call_count == 2


def test_get_positions_cached_independently():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.get_account_info.return_value = {"cash": 1}
    inner.get_positions.return_value = {"AAPL": {"qty": 10}}
    wrapper = CachingBrokerAdapter(inner, account_state_ttl=60)
    wrapper.get_account_info()
    wrapper.get_positions()
    wrapper.get_positions()
    assert inner.get_account_info.call_count == 1
    assert inner.get_positions.call_count == 1


def test_submit_order_delegates_to_inner():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.submit_order.return_value = {"ok": True}
    wrapper = CachingBrokerAdapter(inner)
    result = wrapper.submit_order(
        symbol="AAPL", side="buy", quantity=10, order_type="market",
    )
    assert result == {"ok": True}
    inner.submit_order.assert_called_once()


def test_unknown_attribute_passthrough():
    from worker.caching_broker_adapter import CachingBrokerAdapter
    inner = MagicMock()
    inner.something_custom.return_value = 42
    wrapper = CachingBrokerAdapter(inner)
    assert wrapper.something_custom() == 42
