from __future__ import annotations
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
import click
from sdk.cli.validate import validate_package
from sdk.manifest import QuiltManifest

def run_lumibot_backtest(*, algo_class: type, start_date: str, end_date: str,
                         initial_cash: float = 100_000.0, data_provider: Any = None) -> dict:
    from lumibot.backtesting import YahooDataBacktesting
    from lumibot.strategies import Strategy

    class LumibotWrapper(Strategy):
        def initialize(self):
            self._quilt_algo = algo_class()
            self._quilt_algo.on_start({}, None)
        def on_trading_iteration(self):
            pass

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    results = LumibotWrapper.backtest(YahooDataBacktesting, start, end, budget=initial_cash,
                                      show_plot=False, show_tearsheet=False, save_tearsheet=False)
    return {
        "total_return": getattr(results, "total_return", 0.0),
        "sharpe_ratio": getattr(results, "sharpe", 0.0),
        "max_drawdown": getattr(results, "max_drawdown", 0.0),
        "total_trades": getattr(results, "total_trades", 0),
    }

def _load_algo_class(package_path: Path, manifest: QuiltManifest) -> type:
    entry_file = package_path / manifest.entry_point
    module_name = f"_quilt_bt_{manifest.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry_file)
    module = importlib.util.module_from_spec(spec)
    old_path = sys.path.copy()
    sys.path.insert(0, str(package_path))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path = old_path
    return getattr(module, manifest.class_name)

@click.command("backtest")
@click.option("--path", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".", help="Path to the algorithm package directory.")
@click.option("--start", required=True, help="Backtest start date (YYYY-MM-DD).")
@click.option("--end", required=True, help="Backtest end date (YYYY-MM-DD).")
@click.option("--cash", type=float, default=100_000.0, help="Initial cash balance (default: 100000).")
def backtest_cmd(path: Path, start: str, end: str, cash: float):
    """Run a backtest against historical data."""
    errors = validate_package(path)
    if errors:
        for error in errors:
            click.echo(f"FAIL: {error}", err=True)
        raise SystemExit(1)
    manifest = QuiltManifest.from_file(path / "quilt.yaml")
    algo_class = _load_algo_class(path, manifest)
    click.echo(f"Running backtest: {manifest.name} v{manifest.version}")
    click.echo(f"Period: {start} to {end} | Cash: ${cash:,.2f}")
    click.echo()
    results = run_lumibot_backtest(algo_class=algo_class, start_date=start, end_date=end, initial_cash=cash)
    click.echo("--- Backtest Results ---")
    total_ret = results["total_return"]
    if isinstance(total_ret, float) and abs(total_ret) < 1:
        click.echo(f"Total Return:  {total_ret * 100:.1f}%")
    else:
        click.echo(f"Total Return:  {total_ret}")
    click.echo(f"Sharpe Ratio:  {results['sharpe_ratio']}")
    click.echo(f"Max Drawdown:  {results['max_drawdown']}")
    click.echo(f"Total Trades:  {results['total_trades']}")
