import json
import pytest
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import Base, OptimizationSession, Algorithm
from coordinator.services.validation.report import build_markdown_report, ReportInputs


def _seed_algorithm_sync(db, *, id="test-algo-fixture"):
    db.add(Algorithm(
        id=id, name=id,
        repo_url=f"https://github.com/test/{id}",
    ))
    db.flush()
    return id


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def test_build_markdown_report_contains_required_sections(db_session, tmp_path):
    algo_id = _seed_algorithm_sync(db_session, id="rpt-algo-001")
    sess = OptimizationSession(
        name="rpt-test-001",
        hypothesis="H: TSMOM works",
        parameter_space=json.dumps({"vol_target": [0.10, 0.15]}),
        pre_registered_criteria=json.dumps({"oos_sharpe_lci": 0.5}),
        status="completed",
        algorithm_id=algo_id, base_config={},
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


from coordinator.services.validation.report import render_charts


def test_render_charts_writes_files(db_session, tmp_path):
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    rng = np.random.default_rng(7)
    equity = pd.Series(1000.0 * np.cumprod(1.0 + rng.normal(0.0008, 0.015, 300)), index=idx)
    regimes = pd.Series(["bull"] * 150 + ["chop"] * 150, index=idx)

    paths = render_charts(
        equity=equity,
        regimes=regimes,
        out_dir=tmp_path,
    )
    assert (tmp_path / "equity.png").exists()
    assert (tmp_path / "drawdown.png").exists()
    assert (tmp_path / "regime_returns.png").exists()
    assert "equity" in paths and "drawdown" in paths and "regime_returns" in paths


from coordinator.services.validation.report import build_html_report


def test_build_html_report_embeds_charts(db_session, tmp_path):
    algo_id = _seed_algorithm_sync(db_session, id="html-algo-001")
    sess = OptimizationSession(
        name="html-test-001",
        hypothesis="H",
        parameter_space=json.dumps({}),
        pre_registered_criteria=json.dumps({"oos_sharpe_lci": 0.5}),
        status="completed",
        algorithm_id=algo_id, base_config={},
    )
    db_session.add(sess)
    db_session.commit()

    idx = pd.date_range("2024-01-01", periods=100, freq="D")
    equity = pd.Series(np.linspace(1000.0, 1100.0, 100), index=idx)
    regimes = pd.Series(["bull"] * 100, index=idx)

    inputs = ReportInputs(
        session=sess,
        oos_equity_curve=equity,
        regimes=regimes,
        bootstrap_metrics={"sharpe": {"point": 0.7, "lower": 0.5, "upper": 0.9}, "max_drawdown": {"point": -0.05, "lower": -0.10, "upper": -0.02}},
        regime_metrics={"bull": {"sharpe": 0.7, "total_return": 0.10, "win_rate": 0.55, "n_days": 100}},
        corrected_p_values=[{"raw_p": 0.01, "corrected_p": 0.05, "significant": True}],
    )

    paths = build_html_report(inputs, out_dir=tmp_path)
    assert paths["html"].exists()
    html = paths["html"].read_text()
    assert "html-test-001" in html
    assert "equity.png" in html or "data:image" in html
    assert "Bootstrap CIs" in html
    assert "<table" in html
