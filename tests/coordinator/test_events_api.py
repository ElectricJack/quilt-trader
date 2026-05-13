import pytest


@pytest.mark.asyncio
async def test_list_events_empty(client):
    response = await client.get("/api/events")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_create_and_list_events(client):
    for i in range(3):
        await client.post("/api/events", json={
            "source_type": "system",
            "event_type": "algo_started",
            "severity": "info",
            "payload": {"index": i},
        })
    response = await client.get("/api/events")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


@pytest.mark.asyncio
async def test_filter_events_by_type(client):
    await client.post("/api/events", json={
        "source_type": "algorithm",
        "event_type": "trade_executed",
        "severity": "info",
    })
    await client.post("/api/events", json={
        "source_type": "system",
        "event_type": "algo_error",
        "severity": "error",
    })
    response = await client.get("/api/events?event_type=trade_executed")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "trade_executed"


@pytest.mark.asyncio
async def test_filter_events_by_severity(client):
    await client.post("/api/events", json={
        "source_type": "system",
        "event_type": "info_event",
        "severity": "info",
    })
    await client.post("/api/events", json={
        "source_type": "system",
        "event_type": "error_event",
        "severity": "error",
    })
    response = await client.get("/api/events?severity=error")
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_events_pagination(client):
    for i in range(25):
        await client.post("/api/events", json={
            "source_type": "system",
            "event_type": "bulk_event",
            "severity": "info",
            "payload": {"index": i},
        })
    response = await client.get("/api/events?limit=10&offset=0")
    body = response.json()
    assert len(body["items"]) == 10
    assert body["total"] == 25

    response2 = await client.get("/api/events?limit=10&offset=20")
    body2 = response2.json()
    assert len(body2["items"]) == 5
