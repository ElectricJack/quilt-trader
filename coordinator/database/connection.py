from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, **kwargs) -> AsyncEngine:
    engine = create_async_engine(
        url,
        echo=False,
        connect_args={"check_same_thread": False},
        **kwargs,
    )
    # Enable WAL mode on SQLite so concurrent readers + a single writer don't
    # contend on the database file lock. Without WAL, background sync tasks
    # and validation-lab backtest writes deadlock under load (observed during
    # the crypto-tsmom walk-forward run on 2026-05-27).
    if "sqlite" in url:
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")  # 30s instead of default 0
            cursor.close()
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
