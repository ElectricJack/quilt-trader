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


from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np


def render_charts(*, equity: pd.Series, regimes: pd.Series, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(equity.index, equity.values, color="#1f77b4")
    ax.set_title("OOS Equity Curve")
    ax.set_ylabel("Equity")
    ax.grid(alpha=0.3)
    eq_path = out_dir / "equity.png"
    fig.savefig(eq_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    paths["equity"] = eq_path

    peak = equity.cummax()
    dd = (equity - peak) / peak
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.fill_between(dd.index, dd.values, 0, color="#d62728", alpha=0.5)
    ax.set_title("Drawdown")
    ax.set_ylabel("Drawdown")
    ax.grid(alpha=0.3)
    dd_path = out_dir / "drawdown.png"
    fig.savefig(dd_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    paths["drawdown"] = dd_path

    aligned = pd.concat([equity, regimes], axis=1, join="inner")
    aligned.columns = ["equity", "regime"]
    returns = aligned["equity"].pct_change().fillna(0)
    grouped = returns.groupby(aligned["regime"]).agg(["mean", "std", "count"])
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(grouped.index.astype(str), grouped["mean"] * 252, color=["#2ca02c", "#7f7f7f", "#d62728"][: len(grouped)])
    ax.set_title("Annualized Return by Regime")
    ax.set_ylabel("Annualized Mean Return")
    rr_path = out_dir / "regime_returns.png"
    fig.savefig(rr_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    paths["regime_returns"] = rr_path

    return paths
