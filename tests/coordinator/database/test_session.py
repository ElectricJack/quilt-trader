from sqlalchemy.orm import Session

from coordinator.database.session import get_session_factory
from coordinator.database.models import OptimizationSession


def test_get_session_factory_returns_sync_factory(tmp_path, monkeypatch):
    """The factory creates real sync Sessions backed by the configured DB URL."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("QUILT_DB_URL", f"sqlite:///{db_path}")

    # Reset the cached factory so the env var change takes effect
    import coordinator.database.session as session_module
    session_module._cached_factory = None

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        assert isinstance(db, Session)

        # Create tables and round-trip an OptimizationSession to prove the engine works
        from coordinator.database.models import Base
        Base.metadata.create_all(db.get_bind())

        import json
        sess = OptimizationSession(
            name="t",
            hypothesis="H",
            parameter_space=json.dumps({}),
            pre_registered_criteria=json.dumps({}),
            status="open",
        )
        db.add(sess)
        db.commit()
        assert sess.id is not None


def test_get_session_factory_strips_aiosqlite_driver(tmp_path, monkeypatch):
    """If QUILT_DB_URL uses the aiosqlite driver (from the async coordinator),
    the sync factory must strip it so SQLAlchemy's sync dialect is used."""
    db_path = tmp_path / "test2.db"
    monkeypatch.setenv("QUILT_DB_URL", f"sqlite+aiosqlite:///{db_path}")

    import coordinator.database.session as session_module
    session_module._cached_factory = None

    SessionLocal = get_session_factory()
    # If the driver wasn't stripped, this would raise ModuleNotFoundError on aiosqlite
    # because we're using a sync engine here.
    with SessionLocal() as db:
        assert isinstance(db, Session)


def test_get_session_factory_caches():
    """Repeated calls with the same DB URL return the same factory."""
    import coordinator.database.session as session_module
    session_module._cached_factory = None
    f1 = get_session_factory()
    f2 = get_session_factory()
    assert f1 is f2
