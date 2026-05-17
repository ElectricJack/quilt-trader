import pytest


@pytest.mark.asyncio
async def test_diagnostics_returns_runtime_status(client, db_session):
    r = await client.get("/api/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    assert "checks" in body
    assert isinstance(body["checks"], list)
    for check in body["checks"]:
        assert "name" in check
        assert "status" in check
        assert check["status"] in ("PASS", "WARN", "FAIL")
        assert "message" in check


@pytest.mark.asyncio
async def test_diagnostics_includes_workers_check(client, db_session):
    r = await client.get("/api/diagnostics")
    body = r.json()
    names = [c["name"] for c in body["checks"]]
    assert "workers" in names
    assert "live_subscriptions" in names
    assert "live_finalizer" in names


@pytest.mark.asyncio
async def test_diagnostics_warns_when_no_workers(client, db_session):
    r = await client.get("/api/diagnostics")
    body = r.json()
    workers_check = next(c for c in body["checks"] if c["name"] == "workers")
    # No workers in fresh test DB
    assert workers_check["status"] == "WARN"
    assert "no workers" in workers_check["message"].lower() or "0" in workers_check["message"]
