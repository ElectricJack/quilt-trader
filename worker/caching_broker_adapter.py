"""Thin TTL-caching wrapper around BrokerAdapter for hot-path account state reads.

get_account_info and get_positions are called every tick. Without caching,
each tick incurs 1-2 HTTPS round-trips to the broker. With a 30s default TTL,
multiple algorithms on the same account naturally share state and broker
rate limits stay safe.

submit_order and other write paths pass through unchanged. Call sites should
invoke .invalidate() after an order succeeds so the next tick reads fresh
positions.
"""
from __future__ import annotations

import time
from typing import Any, Optional


class CachingBrokerAdapter:
    def __init__(self, inner: Any, account_state_ttl: float = 30.0) -> None:
        self._inner = inner
        self._ttl = account_state_ttl
        self._cache_account: Optional[tuple[float, dict]] = None
        self._cache_positions: Optional[tuple[float, dict]] = None

    def get_account_info(self) -> dict:
        now = time.monotonic()
        if self._cache_account is not None and now - self._cache_account[0] < self._ttl:
            return self._cache_account[1]
        v = self._inner.get_account_info()
        self._cache_account = (now, v)
        return v

    def get_positions(self) -> dict:
        now = time.monotonic()
        if self._cache_positions is not None and now - self._cache_positions[0] < self._ttl:
            return self._cache_positions[1]
        v = self._inner.get_positions()
        self._cache_positions = (now, v)
        return v

    def invalidate(self) -> None:
        self._cache_account = None
        self._cache_positions = None

    def submit_order(self, *args, **kwargs):
        return self._inner.submit_order(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Anything not explicitly overridden (transactions, multileg, etc.)
        # passes through to the inner adapter.
        return getattr(self._inner, name)
