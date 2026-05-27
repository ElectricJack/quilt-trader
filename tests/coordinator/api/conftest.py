"""Conftest for coordinator API route tests.

The research endpoints mix an async session (for GET reads via get_db) with a
sync session (for POST writes via get_session_factory).  In tests both paths
must hit the same database, so this conftest:

1. Creates a temporary SQLite file for each test.
2. Runs Base.metadata.create_all synchronously against it (schema bootstrap).
3. Patches coordinator.database.session._cached_factory to None so the next
   call to get_session_factory() builds a fresh sync engine pointed at the
   temp file.
4. Sets QUILT_DB_URL so get_session_factory() picks up the temp file URL.
5. Yields a test_app (from create_app) that also uses the same file URL
   (sqlite+aiosqlite:///...) so async reads see the same rows.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import coordinator.database.session as _sync_session_module
from coordinator.database.models import Base
from coordinator.main import create_app


@pytest_asyncio.fixture
async def test_app(tmp_path, monkeypatch):
    db_file = tmp_path / "test_research.db"
    sync_url = f"sqlite:///{db_file}"
    async_url = f"sqlite+aiosqlite:///{db_file}"

    # Bootstrap schema synchronously so it exists before the async app starts.
    engine = create_engine(sync_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    engine.dispose()

    # Reset the cached sync factory so get_session_factory() rebuilds it
    # using the new QUILT_DB_URL.
    monkeypatch.setattr(_sync_session_module, "_cached_factory", None)
    monkeypatch.setenv("QUILT_DB_URL", sync_url)

    app = create_app(database_url=async_url)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        yield app

    # Cleanup: reset the cached factory so other test modules are unaffected.
    monkeypatch.setattr(_sync_session_module, "_cached_factory", None)
