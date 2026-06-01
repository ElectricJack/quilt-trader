#!/usr/bin/env python3
"""One-off canonical-symbol migration. Backs up data/market first.

Renames provider-form parquet directories to canonical form, e.g.
data/market/yfinance/BTC-USD → data/market/yfinance/BTCUSD.

Refuses to clobber a pre-existing backup. Verifies post-backup file count.
Idempotent — re-runs are no-ops once everything is canonical.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from coordinator.services.asset_services.registry import get_default_registry


MARKET_DIR = Path("data/market")
BACKUP = MARKET_DIR.parent / f"market.bak.{datetime.now():%Y%m%d-%H%M%S}"


def main() -> int:
    if not MARKET_DIR.exists():
        print(f"nothing to do: {MARKET_DIR} does not exist")
        return 0

    if BACKUP.exists():
        print(f"refusing to clobber existing backup: {BACKUP}", file=sys.stderr)
        return 1

    print(f"Backing up {MARKET_DIR} → {BACKUP} ...")
    shutil.copytree(MARKET_DIR, BACKUP, copy_function=shutil.copy2)

    src_count = sum(1 for _ in MARKET_DIR.rglob("*.parquet"))
    bak_count = sum(1 for _ in BACKUP.rglob("*.parquet"))
    if src_count != bak_count:
        print(
            f"backup verification failed: src={src_count} bak={bak_count}",
            file=sys.stderr,
        )
        return 1
    print(f"Backup OK ({src_count} parquet files copied)")

    registry = get_default_registry()
    for provider_dir in sorted(MARKET_DIR.iterdir()):
        if not provider_dir.is_dir():
            continue
        provider = provider_dir.name
        for symbol_dir in sorted(provider_dir.iterdir()):
            if not symbol_dir.is_dir():
                continue
            provider_form = symbol_dir.name
            try:
                canonical = registry.canonicalize(provider_form, provider)
            except (ValueError, KeyError) as e:
                print(f"SKIP {provider}/{provider_form}: {e}")
                continue
            if canonical == provider_form:
                continue
            target = provider_dir / canonical
            if target.exists():
                print(
                    f"CONFLICT {provider}/{provider_form} → {canonical} (target exists)"
                )
                continue
            symbol_dir.rename(target)
            print(f"RENAMED {provider}/{provider_form} → {provider}/{canonical}")

    cov = Path("data/coverage_index.parquet")
    if cov.exists():
        cov.unlink()
        print(f"WIPED {cov}")

    print(f"\nDone. To restore: rm -rf {MARKET_DIR} && mv {BACKUP} {MARKET_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
