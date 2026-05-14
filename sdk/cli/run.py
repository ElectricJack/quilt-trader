from __future__ import annotations
import importlib.util
import signal as signal_mod
import sys
import time
from pathlib import Path
from typing import Any, Optional
import click
from sdk.cli.validate import validate_package
from sdk.manifest import QuiltManifest

class LocalPaperRunner:
    def __init__(self, package_path: Path):
        self.package_path = package_path
        self.manifest = QuiltManifest.from_file(package_path / "quilt.yaml")
        self.algo_instance = self._load_instance()
        self.running = False

    def _load_instance(self) -> Any:
        entry_file = self.package_path / self.manifest.entry_point
        module_name = f"_quilt_run_{self.manifest.name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, entry_file)
        module = importlib.util.module_from_spec(spec)
        old_path = sys.path.copy()
        sys.path.insert(0, str(self.package_path))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path = old_path
        cls = getattr(module, self.manifest.class_name)
        return cls()

    def start(self, config: Optional[dict] = None, restored_state: Any = None):
        self.algo_instance.on_start(config or {}, restored_state)
        self.running = True

    def tick(self) -> list:
        return self.algo_instance.on_tick(None)

    def stop(self) -> Any:
        state = self.algo_instance.on_stop()
        self.running = False
        return state

    def save_state(self) -> Any:
        return self.algo_instance.save_state()

@click.command("run")
@click.option("--path", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".", help="Path to the algorithm package directory.")
@click.option("--max-ticks", type=int, default=None, help="Stop after N ticks (default: run until interrupted).")
@click.option("--interval", type=float, default=1.0, help="Seconds between ticks (default: 1.0).")
def run_cmd(path: Path, max_ticks: Optional[int], interval: float):
    """Run a local paper-trading session."""
    errors = validate_package(path)
    if errors:
        for error in errors:
            click.echo(f"FAIL: {error}", err=True)
        raise SystemExit(1)
    runner = LocalPaperRunner(path)
    click.echo(f"Starting: {runner.manifest.name} v{runner.manifest.version}")
    click.echo(f"Interval: {interval}s | Max ticks: {max_ticks or 'unlimited'}")
    click.echo("Press Ctrl+C to stop.\n")
    stopped = False
    def handle_sigint(signum, frame):
        nonlocal stopped
        stopped = True
    signal_mod.signal(signal_mod.SIGINT, handle_sigint)
    runner.start()
    tick_count = 0
    while not stopped:
        signals = runner.tick()
        tick_count += 1
        if signals:
            for sig in signals:
                click.echo(f"  Tick {tick_count}: Signal -> {sig}")
        else:
            click.echo(f"  Tick {tick_count}: no signals")
        if max_ticks is not None and tick_count >= max_ticks:
            break
        if not stopped and (max_ticks is None or tick_count < max_ticks):
            time.sleep(interval)
    state = runner.stop()
    click.echo(f"\nStopped after {tick_count} ticks.")
    if state:
        click.echo(f"Final state: {state}")
