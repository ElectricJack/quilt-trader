"""Synchronous SQLAlchemy session factory.

The coordinator's HTTP API uses async sessions via `connection.py`. The
validation lab (sweep, walk-forward, report, CLI) operates on sync sessions,
so it consumes this factory instead. Both factories point at the same
underlying database; the only difference is async vs sync dialect.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


_DEFAULT_DB_PATH = Path("data") / "quilt_trader.db"
_cached_factory: Optional[sessionmaker[Session]] = None


def _resolve_db_url() -> str:
    raw = os.environ.get("QUILT_DB_URL")
    if raw:
        # Strip the aiosqlite driver prefix when present; the sync engine uses
        # the plain sqlite dialect.
        return raw.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return f"sqlite:///{_DEFAULT_DB_PATH}"


def get_session_factory() -> sessionmaker[Session]:
    """Return a process-wide cached sessionmaker bound to the configured DB."""
    global _cached_factory
    if _cached_factory is None:
        engine = create_engine(
            _resolve_db_url(),
            connect_args={"check_same_thread": False} if "sqlite" in _resolve_db_url() else {},
            future=True,
        )
        _cached_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _cached_factory
