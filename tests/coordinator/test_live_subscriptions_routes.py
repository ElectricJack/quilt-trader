import pytest
from httpx import AsyncClient
from sqlalchemy import select

from coordinator.database.models import LiveSubscription, SubscriptionConsumer


@pytest.mark.asyncio
async def test_create_subscription_inserts_manual_consumer(client: AsyncClient, db_session):
    body = {"broker": "alpaca", "symbol": "SPY",
            "asset_class": "equities", "tick_retention_hours": 168}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["asset_class"] == "equities"
    assert len(data["consumers"]) == 1
    assert data["consumers"][0]["consumer_type"] == "manual"

    # DB has matching consumer row.
    consumers = (await db_session.execute(
        select(SubscriptionConsumer).where(SubscriptionConsumer.subscription_id == data["id"])
    )).scalars().all()
    assert len(consumers) == 1


@pytest.mark.asyncio
async def test_unsubscribe_deletes_manual_consumer_and_auto_deletes_sub(
    client: AsyncClient, db_session,
):
    """Manual unsubscribe with no other consumers: row goes away."""
    body = {"broker": "alpaca", "symbol": "QQQ", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    sub_id = r.json()["id"]

    r = await client.post(f"/api/live-subscriptions/{sub_id}/unsubscribe")
    assert r.status_code == 200, r.text

    row = (await db_session.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub_id)
    )).scalar_one_or_none()
    assert row is None, "subscription should be auto-deleted when consumer count hits 0"


@pytest.mark.asyncio
async def test_delete_refuses_when_consumers_exist(client: AsyncClient, db_session):
    body = {"broker": "alpaca", "symbol": "AAPL", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    sub_id = r.json()["id"]

    # Add an algo consumer alongside the manual one.
    db_session.add(SubscriptionConsumer(
        subscription_id=sub_id, consumer_type="algo", consumer_id="deployment-X",
    ))
    await db_session.commit()

    r = await client.delete(f"/api/live-subscriptions/{sub_id}")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "algo" in detail or "consumer" in detail


@pytest.mark.asyncio
async def test_list_subscriptions_includes_consumers(client: AsyncClient, db_session):
    body = {"broker": "alpaca", "symbol": "NVDA", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    sub_id = r.json()["id"]

    r = await client.get("/api/live-subscriptions")
    rows = r.json()
    matching = [r for r in rows if r["id"] == sub_id]
    assert len(matching) == 1
    assert "consumers" in matching[0]
    assert len(matching[0]["consumers"]) == 1
