import json
import pytest
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import Base, OptimizationSession
from coordinator.services.validation.report import build_markdown_report, ReportInputs


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def test_build_markdown_report_contains_required_sections(db_session, tmp_path):
    sess = OptimizationSession(
        name="rpt-test-001",
        hypothesis="H: TSMOM works",
        parameter_space=json.dumps({"vol_target": [0.10, 0.15]}),
        pre_registered_criteria=json.dumps({"oos_sharpe_lci": 0.5}),
        status="completed",
    )
    db_session.add(sess)
    db_session.commit()

    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    rng = np.random.default_rng(0)
    equity = pd.Series(1000.0 * np.cumprod(1.0 + rng.normal(0.001, 0.02, 200)), index=idx)
    regimes = pd.Series(["bull"] * 100 + ["chop"] * 100, index=idx)

    inputs = ReportInputs(
        session=sess,
        oos_equity_curve=equity,
        regimes=regimes,
        bootstrap_metrics={"sharpe": {"point": 0.6, "lower": 0.4, "upper": 0.9}},
        regime_metrics={"bull": {"sharpe": 0.8}, "chop": {"sharpe": 0.3}},
        corrected_p_values=[{"raw_p": 0.01, "corrected_p": 0.05, "significant": True}],
    )

    md = build_markdown_report(inputs)
    assert "# Optimization Session: rpt-test-001" in md
    assert "## Hypothesis" in md
    assert "## OOS Equity Curve" in md
    assert "## Bootstrap CIs" in md
    assert "## Regime-Conditional Metrics" in md
    assert "## Multi-Test-Corrected Significance" in md
    assert "## Deploy / Kill Decision" in md
