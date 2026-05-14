import pytest

from coordinator.services.live_feed_manager import LiveFeedManager


def test_register_and_dependent_count():
    m = LiveFeedManager()
    m.register("alpaca", "SPY")
    m.add_dependent("alpaca", "SPY", "inst-1")
    m.add_dependent("alpaca", "SPY", "inst-2")
    assert m.dependent_count("alpaca", "SPY") == 2


def test_release_returns_true_when_last_dependent_leaves():
    m = LiveFeedManager()
    m.register("alpaca", "SPY")
    m.add_dependent("alpaca", "SPY", "inst-1")
    assert m.release("alpaca", "SPY", "inst-1") is True
    assert m.dependent_count("alpaca", "SPY") == 0


def test_ensure_running_starts_subscription():
    m = LiveFeedManager()
    m.register("alpaca", "SPY")
    m.ensure_running("alpaca", "SPY", "inst-1")
    assert m.is_running("alpaca", "SPY") is True


def test_unknown_key_returns_zero_count():
    m = LiveFeedManager()
    assert m.dependent_count("alpaca", "ZZZZ") == 0
