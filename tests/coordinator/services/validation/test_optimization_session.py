import json
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import Base, OptimizationSession
from coordinator.services.validation.optimization_session import create_session


def test_optimization_session_basic_fields():
    sess = OptimizationSession(
        name="tsmom-2026-05-27",
        hypothesis="Daily ensemble TSMOM on BTC/ETH produces OOS Sharpe lower-CI > 0.5",
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
    sess = create_session(
        db_session,
        name="crypto-tsmom-001",
        hypothesis="H1: ensemble TSMOM produces edge",
        parameter_space={"vol_target": [0.10, 0.15, 0.20]},
        pre_registered_criteria={"oos_sharpe_lci": 0.5},
    )
    db_session.commit()

    fetched = db_session.query(OptimizationSession).filter_by(name="crypto-tsmom-001").one()
    assert fetched.id == sess.id
    assert "TSMOM" in fetched.hypothesis
    assert fetched.status == "open"


def test_create_session_rejects_duplicate_name(db_session):
    create_session(
        db_session,
        name="dup",
        hypothesis="H",
        parameter_space={},
        pre_registered_criteria={},
    )
    db_session.commit()
    with pytest.raises(Exception):  # IntegrityError under the hood
        create_session(
            db_session,
            name="dup",
            hypothesis="H2",
            parameter_space={},
            pre_registered_criteria={},
        )
        db_session.commit()
