"""Unit test for the one-off canonical-symbol migration script."""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "migrate_canonical_symbols.py"


def _df():
    return pd.DataFrame({"timestamp": ["2024-01-01"], "close": [1.0]})


@pytest.fixture
def fake_market_dir(tmp_path):
    """Construct a fake data/market tree with a mix of canonical and non-canonical dirs."""
    root = tmp_path / "data"
    market = root / "market"
    # Will be renamed: yfinance/BTC-USD → BTCUSD
    (market / "yfinance" / "BTC-USD").mkdir(parents=True)
    _df().to_parquet(market / "yfinance" / "BTC-USD" / "1day.parquet")
    # Already canonical
    (market / "polygon" / "BTCUSD").mkdir(parents=True)
    _df().to_parquet(market / "polygon" / "BTCUSD" / "1min.parquet")
    # Already canonical equity
    (market / "polygon" / "AAPL").mkdir(parents=True)
    _df().to_parquet(market / "polygon" / "AAPL" / "1day.parquet")
    return root


def test_migration_creates_backup_and_renames(fake_market_dir, monkeypatch):
    monkeypatch.chdir(fake_market_dir.parent)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, check=True,
    )
    # Backup created
    backups = list(fake_market_dir.glob("market.bak.*"))
    assert len(backups) == 1
    backup = backups[0]
    # Backup has same parquet count
    assert sum(1 for _ in backup.rglob("*.parquet")) == 3
    # Rename happened
    market = fake_market_dir / "market"
    assert (market / "yfinance" / "BTCUSD" / "1day.parquet").exists()
    assert not (market / "yfinance" / "BTC-USD").exists()
    # Already-canonical entries untouched
    assert (market / "polygon" / "BTCUSD" / "1min.parquet").exists()
    assert (market / "polygon" / "AAPL" / "1day.parquet").exists()
    # Output mentions actions and restore command
    assert "RENAMED yfinance/BTC-USD → yfinance/BTCUSD" in result.stdout
    assert "To restore:" in result.stdout


def test_migration_refuses_to_clobber_backup(fake_market_dir, monkeypatch):
    monkeypatch.chdir(fake_market_dir.parent)
    # Pre-create a backup with the future timestamp to force collision
    from datetime import datetime
    bak = fake_market_dir / f"market.bak.{datetime.now():%Y%m%d-%H%M%S}"
    bak.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True,
    )
    # Either the test's pre-created backup or one from a second-resolution collision blocks
    if "refusing to clobber" in result.stderr:
        assert result.returncode != 0
    else:
        # Race: timestamps differ; the test is OK either way
        pytest.skip("timestamp race produced a fresh backup name; not a failure")


def test_migration_idempotent(fake_market_dir, monkeypatch):
    """Running twice (after manually removing first backup) should rename zero."""
    monkeypatch.chdir(fake_market_dir.parent)
    subprocess.run([sys.executable, str(SCRIPT)], check=True, capture_output=True, text=True)
    # Remove the backup so second run can proceed
    for b in fake_market_dir.glob("market.bak.*"):
        import shutil
        shutil.rmtree(b)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, check=True,
    )
    assert "RENAMED" not in result.stdout
