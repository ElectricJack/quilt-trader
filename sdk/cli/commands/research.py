from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import click

from coordinator.database.session import get_session_factory

logger = logging.getLogger(__name__)


def _make_cli_runner_factory():
    """Construct a RunnerFactory for use in CLI sweep / walk-forward commands.

    LIMITATION: BacktestRunner requires an *async* session_factory
    (async_sessionmaker[AsyncSession]), a DownloadManager wired with real
    providers, and a DataService — all of which are only available from the
    coordinator's startup context (coordinator/main.py).  The CLI's
    get_session_factory() returns a *sync* sessionmaker, which is incompatible.

    Until the CLI is given its own async-session bootstrap (or the coordinator
    exposes a lightweight async-session helper), the runner_factory raises at
    call time to surface the gap rather than silently skipping execution.

    See backlog: "CLI runner_factory needs real async DI from coordinator startup."
    """
    async def _runner_factory(run_id: int) -> None:
        raise NotImplementedError(
            f"CLI runner_factory cannot execute BacktestRun id={run_id}: "
            "BacktestRunner requires an async session factory and a wired "
            "DownloadManager/DataService that are not available in the CLI "
            "context.  Start the coordinator API and trigger the run via the "
            "HTTP endpoint, or wire the CLI with an async session bootstrap."
        )

    return _runner_factory


@click.group("research")
def research_group() -> None:
    """Strategy Validation Lab — sessions, sweeps, walk-forward, reports."""


@research_group.group("session")
def session_group() -> None:
    """Manage OptimizationSession records."""


@session_group.command("create")
@click.option("--name", required=True, help="Unique session name.")
@click.option("--hypothesis", required=True, help="Pre-registered hypothesis text.")
@click.option(
    "--parameter-space",
    required=True,
    help="JSON parameter space, e.g., '{\"vol_target\": [0.10, 0.15]}'.",
)
@click.option(
    "--criteria",
    required=True,
    help="JSON pre-registered criteria, e.g., '{\"oos_sharpe_lci\": 0.5}'.",
)
@click.option("--notes", default="", help="Free-form notes.")
def session_create(name: str, hypothesis: str, parameter_space: str, criteria: str, notes: str) -> None:
    """Create a new OptimizationSession (pre-registration step)."""
    from coordinator.services.validation.optimization_session import create_session

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        sess = create_session(
            db,
            name=name,
            hypothesis=hypothesis,
            parameter_space=json.loads(parameter_space),
            pre_registered_criteria=json.loads(criteria),
            notes=notes,
        )
        db.commit()
        click.echo(f"Created OptimizationSession id={sess.id} name={sess.name}")


@session_group.command("list")
def session_list() -> None:
    """List all OptimizationSessions."""
    from coordinator.database.models import OptimizationSession

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        rows = db.query(OptimizationSession).order_by(OptimizationSession.created_at.desc()).all()
        for r in rows:
            click.echo(f"{r.id}\t{r.name}\t{r.status}\t{r.created_at.isoformat() if r.created_at else ''}")


@research_group.command("sweep")
@click.option("--session-id", type=int, required=True)
@click.option("--manifest", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--base-config", type=click.Path(exists=True, dir_okay=False), required=True, help="JSON file with base BacktestConfig.")
@click.option("--search", type=click.Choice(["grid", "random", "latin"]), default="grid")
@click.option("--max-trials", type=int, default=50)
@click.option("--parallelism", type=int, default=1)
@click.option("--seed", type=int, default=0)
def cmd_sweep(session_id: int, manifest: str, base_config: str, search: str, max_trials: int, parallelism: int, seed: int) -> None:
    """Run a hyperparameter sweep under an existing session."""
    from coordinator.database.models import OptimizationSession
    from coordinator.services.validation.sweep import run_sweep

    base_cfg = json.loads(Path(base_config).read_text())

    async def _go() -> None:
        SessionLocal = get_session_factory()
        runner_factory = _make_cli_runner_factory()
        with SessionLocal() as db:
            sess = db.query(OptimizationSession).get(session_id)
            if sess is None:
                raise click.ClickException(f"Session {session_id} not found")
            param_space = json.loads(sess.parameter_space)
            result = await run_sweep(
                db,
                runner_factory,
                session_id=session_id,
                manifest_path=manifest,
                base_config=base_cfg,
                parameter_space=param_space,
                search=search,
                max_trials=max_trials,
                parallelism=parallelism,
                seed=seed,
            )
            db.commit()
            click.echo(f"Sweep done: {result.n_configs} configs, {len(result.run_ids)} runs.")

    asyncio.run(_go())


@research_group.command("walk-forward")
@click.option("--session-id", type=int, required=True)
@click.option("--manifest", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--base-config", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--train-years", type=float, default=4.0)
@click.option("--test-years", type=float, default=1.0)
@click.option("--step-months", type=float, default=6.0)
@click.option("--objective", type=click.Choice(["sharpe", "calmar", "sortino"]), default="sharpe")
@click.option("--parallelism", type=int, default=1)
def cmd_walk_forward(session_id: int, manifest: str, base_config: str, train_years: float, test_years: float, step_months: float, objective: str, parallelism: int) -> None:
    """Run a walk-forward optimization under an existing session."""
    from coordinator.database.models import OptimizationSession
    from coordinator.services.validation.walk_forward import run_walk_forward

    base_cfg = json.loads(Path(base_config).read_text())

    async def _go() -> None:
        SessionLocal = get_session_factory()
        runner_factory = _make_cli_runner_factory()
        with SessionLocal() as db:
            sess = db.query(OptimizationSession).get(session_id)
            if sess is None:
                raise click.ClickException(f"Session {session_id} not found")
            result = await run_walk_forward(
                db,
                runner_factory,
                session_id=session_id,
                manifest_path=manifest,
                base_config=base_cfg,
                parameter_space=json.loads(sess.parameter_space),
                train_years=train_years,
                test_years=test_years,
                step_months=step_months,
                objective=objective,
                parallelism=parallelism,
            )
            db.commit()
            click.echo(f"Walk-forward done: {result.n_folds} folds, OOS runs: {result.oos_run_ids}")

    asyncio.run(_go())


@research_group.command("report")
@click.option("--session-id", type=int, required=True)
@click.option("--out-dir", type=click.Path(file_okay=False), default="data/research_reports")
def cmd_report(session_id: int, out_dir: str) -> None:
    """Build the markdown + HTML report for a completed session."""
    from coordinator.database.models import OptimizationSession
    from coordinator.services.validation.report import ReportInputs, build_html_report
    from coordinator.services.validation.walk_forward import concatenate_oos_curves
    from coordinator.services.validation.regime import tag_regimes, regime_conditional_metrics
    from coordinator.services.validation.bootstrap import bootstrap_metrics
    from coordinator.services.validation.optimization_session import get_session_runs

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        sess = db.query(OptimizationSession).get(session_id)
        if sess is None:
            raise click.ClickException(f"Session {session_id} not found")

        runs = get_session_runs(db, session_id)
        oos_paths = []
        for r in runs:
            overrides = r.config_overrides or {}
            if isinstance(overrides, str):
                import json as _json
                try:
                    overrides = _json.loads(overrides)
                except Exception:
                    overrides = {}
            if overrides.get("_oos") is True:
                path = Path(f"data/backtests/{r.id}/equity_native.parquet")
                if path.exists():
                    oos_paths.append(path)
        if not oos_paths:
            raise click.ClickException("No OOS runs found for this session.")

        equity = concatenate_oos_curves(oos_paths)
        regimes = tag_regimes(equity)
        boot = bootstrap_metrics(equity, n_resamples=1000)
        regime_m = regime_conditional_metrics(equity, regimes)

        inputs = ReportInputs(
            session=sess,
            oos_equity_curve=equity,
            regimes=regimes,
            bootstrap_metrics={k: v.__dict__ for k, v in boot.items()},
            regime_metrics=regime_m,
            corrected_p_values=[],
        )

        target = Path(out_dir) / str(session_id)
        result = build_html_report(inputs, out_dir=target)
        click.echo(f"Report written: {result['html']}")
