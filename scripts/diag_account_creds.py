"""
Diagnostic: decrypt each account's credentials and report which field
names are present (no values printed). Helps distinguish:

  - decrypt succeeds but the JSON is missing the required keys, vs.
  - decrypt fails silently and live_feed_aggregator gets an empty dict.

Usage (from repo root, with the same env the coordinator uses):
    .venv/bin/python scripts/diag_account_creds.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select

from coordinator.database.connection import create_engine
from coordinator.database.models import Account
from coordinator.services.encryption import EncryptionService
from sqlalchemy.ext.asyncio import async_sessionmaker


REQUIRED = {
    "alpaca": ("api_key", "secret_key"),
    "tradier": ("access_token", "account_id"),
}

# Mirrors coordinator/main.py:create_app default.
DEV_DEFAULT_KEY = "default-dev-key-32-bytes-long!!!"
DB_URL = "sqlite+aiosqlite:///data/quilt_trader.db"


async def main() -> int:
    env_key = os.environ.get("QT_ENCRYPTION_KEY")
    if env_key:
        keys_to_try = [("QT_ENCRYPTION_KEY env", env_key)]
    else:
        keys_to_try = [("default-dev-key (create_app default)", DEV_DEFAULT_KEY)]
    print(f"Trying encryption key source: {keys_to_try[0][0]}\n")

    enc = EncryptionService(keys_to_try[0][1])

    engine = create_engine(DB_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        rows = (await session.execute(select(Account))).scalars().all()

    print(f"Found {len(rows)} account(s)\n")
    for a in rows:
        print(f"[{a.broker_type}/{a.environment}] {a.name}  id={a.id}")
        print(f"  ciphertext length: {len(a.credentials) if a.credentials else 0}")
        try:
            data = enc.decrypt_json(a.credentials)
        except Exception as e:
            print(f"  DECRYPT FAILED: {type(e).__name__}: {e}")
            print()
            continue
        if not isinstance(data, dict):
            print(f"  DECRYPTED but not a dict: type={type(data).__name__}")
            print()
            continue
        keys = sorted(data.keys())
        print(f"  decrypted keys: {keys}")
        required = REQUIRED.get(a.broker_type, ())
        missing = [f for f in required if not data.get(f)]
        if missing:
            print(f"  MISSING required: {missing}")
        else:
            print(f"  OK — all required fields present and non-empty")
        print()

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
