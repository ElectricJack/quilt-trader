import json
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import Algorithm, Base, OptimizationSession
from coordinator.services.validation.optimization_session import create_session


def _seed_algorithm(db, *, id: str = "test-algo-fixture") -> Algorithm:
    """Insert a minimal Algorithm row so the OptimizationSession FK resolves."""
    algo = Algorithm(
        id=id,
        name=id,
        repo_url=f"https://github.com/test/{id}",
    )
    db.add(algo)
    db.flush()
    return algo


def test_optimization_session_basic_fields():
    sess = OptimizationSession(
        name="tsmom-2026-05-27",
        hypothesis="Daily ensemble TSMOM on BTC/ETH produces OOS Sharpe lower-CI > 0.5",
        algorithm_id="test-algo-fixture",
        base_config={},
        parameter_space=json.dumps({"vol_target": [0.10, 0.15, 0.20]}),
        pre_registered_criteria=json.dumps({"oos_sharpe_lci": 0.5, "max_dd_uci": 0.35}),
        status="open",
    )
    assert sess.name == "tsmom-2026-05-27"
    assert "OOS Sharpe" in sess.hypothesis
    assert sess.status == "open"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


def test_create_session_persists(db_session):
    _seed_algorithm(db_session)
    sess = create_session(
        db_session,
        name="crypto-tsmom-001",
        hypothesis="H1: ensemble TSMOM produces edge",
        algorithm_id="test-algo-fixture",
        base_config={},
        parameter_space={"vol_target": [0.10, 0.15, 0.20]},
        pre_registered_criteria={"oos_sharpe_lci": 0.5},
    )
    db_session.commit()

    fetched = db_session.query(OptimizationSession).filter_by(name="crypto-tsmom-001").one()
    assert fetched.id == sess.id
    assert "TSMOM" in fetched.hypothesis
    assert fetched.status == "open"


def test_create_session_rejects_duplicate_name(db_session):
    _seed_algorithm(db_session)
    create_session(
        db_session,
        name="dup",
        hypothesis="H",
        algorithm_id="test-algo-fixture",
        base_config={},
        parameter_space={},
        pre_registered_criteria={},
    )
    db_session.commit()
    with pytest.raises(Exception):  # IntegrityError under the hood
        create_session(
            db_session,
            name="dup",
            hypothesis="H2",
            algorithm_id="test-algo-fixture",
            base_config={},
            parameter_space={},
            pre_registered_criteria={},
        )
        db_session.commit()


def test_create_session_persists_algorithm_id_and_base_config(db_session):
    _seed_algorithm(db_session)
    sess = create_session(
        db_session,
        name="t1",
        hypothesis="h",
        algorithm_id="test-algo-fixture",
        base_config={"vol_target": 0.10},
        parameter_space={"lookback": [20, 50]},
        pre_registered_criteria={"min_sharpe": 1.0},
    )
    db_session.flush()
    assert sess.algorithm_id == "test-algo-fixture"
    assert sess.base_config == {"vol_target": 0.10}


def test_create_session_accepts_empty_base_config(db_session):
    _seed_algorithm(db_session, id="empty-base-fixture")
    sess = create_session(
        db_session,
        name="t2",
        hypothesis="h",
        algorithm_id="empty-base-fixture",
        base_config={},
        parameter_space={"x": [1]},
        pre_registered_criteria={"min_sharpe": 0.0},
    )
    assert sess.base_config == {}
