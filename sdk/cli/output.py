"""CLI output helpers: tables, JSON, status lines, fail with exit code."""
from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

_err_console = Console(stderr=True)
_out_console = Console()


def print_json(payload: Any) -> None:
    """Print JSON-serialized payload to stdout with indent=2."""
    sys.stdout.write(json.dumps(payload, default=str, indent=2))
    sys.stdout.write("\n")
    sys.stdout.flush()


def print_table(rows: list[dict], columns: list[str]) -> None:
    """Print a Rich table of `rows` with the given column order. Empty rows
    prints '(no rows)' instead."""
    if not rows:
        _out_console.print("[dim](no rows)[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(c, "")) for c in columns])
    _out_console.print(table)


def print_status(message: str) -> None:
    """Status message — goes to stderr so it doesn't pollute piped stdout."""
    _err_console.print(message)


def fail(code: int, message: str) -> None:
    """Print error to stderr and exit with the given code."""
    _err_console.print(f"[red]error:[/red] {message}")
    raise SystemExit(code)
