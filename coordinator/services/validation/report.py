from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from coordinator.database.models import OptimizationSession


@dataclass
class ReportInputs:
    session: OptimizationSession
    oos_equity_curve: pd.Series
    regimes: pd.Series
    bootstrap_metrics: dict[str, Any]
    regime_metrics: dict[str, dict[str, float]]
    corrected_p_values: list[dict[str, Any]]


def _criteria_check(criteria: dict[str, Any], inputs: ReportInputs) -> list[dict[str, Any]]:
    """Evaluate each pre-registered criterion against the report inputs."""
    checks: list[dict[str, Any]] = []
    sharpe_ci = inputs.bootstrap_metrics.get("sharpe", {})

    if "oos_sharpe_lci" in criteria:
        threshold = criteria["oos_sharpe_lci"]
        actual = sharpe_ci.get("lower", float("nan"))
        checks.append(
            {
                "criterion": "OOS Sharpe lower-CI",
                "threshold": f"> {threshold}",
                "actual": actual,
                "pass": actual > threshold if actual == actual else False,
            }
        )
    if "max_dd_uci" in criteria:
        threshold = criteria["max_dd_uci"]
        dd_upper = inputs.bootstrap_metrics.get("max_drawdown", {}).get("upper", float("nan"))
        actual = abs(dd_upper)
        checks.append(
            {
                "criterion": "OOS MaxDD upper-CI (|...|)",
                "threshold": f"< {threshold}",
                "actual": actual,
                "pass": actual < threshold if actual == actual else False,
            }
        )
    return checks


def build_markdown_report(inputs: ReportInputs) -> str:
    sess = inputs.session
    criteria = json.loads(sess.pre_registered_criteria)
    param_space = json.loads(sess.parameter_space)
    checks = _criteria_check(criteria, inputs)

    lines: list[str] = []
    lines.append(f"# Optimization Session: {sess.name}\n")
    lines.append(f"**Status:** {sess.status}\n")
    lines.append(f"**Created:** {sess.created_at.isoformat() if sess.created_at else ''}\n")
    lines.append("\n## Hypothesis\n")
    lines.append(f"{sess.hypothesis}\n")

    lines.append("\n## Parameter Space\n")
    lines.append("```json\n")
    lines.append(json.dumps(param_space, indent=2))
    lines.append("\n```\n")

    lines.append("\n## Pre-Registered Criteria\n")
    lines.append("```json\n")
    lines.append(json.dumps(criteria, indent=2))
    lines.append("\n```\n")

    eq = inputs.oos_equity_curve
    lines.append("\n## OOS Equity Curve\n")
    if len(eq) > 0:
        lines.append(f"- **Start:** {eq.index[0]}  **End:** {eq.index[-1]}\n")
        lines.append(f"- **Initial:** {eq.iloc[0]:.2f}  **Final:** {eq.iloc[-1]:.2f}\n")
        lines.append(f"- **Total return:** {(eq.iloc[-1] / eq.iloc[0] - 1.0) * 100:.2f}%\n")

    lines.append("\n## Bootstrap CIs\n")
    lines.append("| Metric | Point | 95% Lower | 95% Upper |\n")
    lines.append("|---|---|---|---|\n")
    for metric, ci in inputs.bootstrap_metrics.items():
        lines.append(
            f"| {metric} | {ci.get('point', float('nan')):.3f} | {ci.get('lower', float('nan')):.3f} | {ci.get('upper', float('nan')):.3f} |\n"
        )

    lines.append("\n## Regime-Conditional Metrics\n")
    lines.append("| Regime | Sharpe | Total Return | Win Rate | N Days |\n")
    lines.append("|---|---|---|---|---|\n")
    for regime, m in inputs.regime_metrics.items():
        lines.append(
            f"| {regime} | {m.get('sharpe', 0):.3f} | {m.get('total_return', 0):.3f} | "
            f"{m.get('win_rate', 0):.3f} | {m.get('n_days', 0)} |\n"
        )

    lines.append("\n## Multi-Test-Corrected Significance\n")
    lines.append("| Raw p | Corrected p | Significant |\n")
    lines.append("|---|---|---|\n")
    for r in inputs.corrected_p_values:
        lines.append(f"| {r['raw_p']:.4f} | {r['corrected_p']:.4f} | {'YES' if r['significant'] else 'NO'} |\n")

    lines.append("\n## Deploy / Kill Decision\n")
    if checks:
        lines.append("| Criterion | Threshold | Actual | Pass |\n")
        lines.append("|---|---|---|---|\n")
        for c in checks:
            lines.append(f"| {c['criterion']} | {c['threshold']} | {c['actual']:.4f} | {'YES' if c['pass'] else 'NO'} |\n")
        all_pass = all(c["pass"] for c in checks)
        lines.append(f"\n**Decision:** {'DEPLOY' if all_pass else 'KILL'}\n")
    else:
        lines.append("No criteria defined.\n")

    return "".join(lines)
