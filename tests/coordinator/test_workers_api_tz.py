"""Verify that worker API responses emit UTC Z-suffixed timestamps."""
import pytest
from datetime import datetime, timezone

from coordinator.database.models import Worker


@pytest.mark.asyncio
async def test_worker_response_emits_utc_z_suffix(client, db_session):
    w = Worker(
        name="tz-test",
        status="online",
        last_heartbeat=datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(w)
    await db_session.flush()
    wid = w.id

    r = await client.get(f"/api/workers/{wid}")
    assert r.status_code == 200
    body = r.json()
    assert body["last_heartbeat"].endswith("Z"), repr(body["last_heartbeat"])
    assert body["created_at"] is None or body["created_at"].endswith("Z"), repr(body.get("created_at"))
