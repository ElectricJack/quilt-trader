import pytest
from httpx import AsyncClient
from sqlalchemy import select

from coordinator.database.models import LiveSubscription, SubscriptionConsumer


async def _make_account(db_session, *, name="Test Account", broker_type="alpaca",
                         supported_asset_types=None):
    from coordinator.database.models import Account
    if supported_asset_types is None:
        supported_asset_types = ["equities"]
    acct = Account(
        name=name, broker_type=broker_type,
        credentials="{}", supported_asset_types=supported_asset_types,
    )
    db_session.add(acct)
    await db_session.flush()
    return acct


@pytest.mark.asyncio
async def test_create_subscription_inserts_manual_consumer(client: AsyncClient, db_session):
    acct = await _make_account(db_session)
    await db_session.commit()

    body = {"account_id": acct.id, "symbol": "SPY",
            "asset_class": "equities", "tick_retention_hours": 168}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["asset_class"] == "equities"
    assert len(data["consumers"]) == 1
    assert data["consumers"][0]["consumer_type"] == "manual"

    # DB has matching consumer row.
    # expire_all so this session sees data committed by the API's session.
    await db_session.rollback()
    consumers = (await db_session.execute(
        select(SubscriptionConsumer).where(SubscriptionConsumer.subscription_id == data["id"])
    )).scalars().all()
    assert len(consumers) == 1


@pytest.mark.asyncio
async def test_unsubscribe_deletes_manual_consumer_and_auto_deletes_sub(
    client: AsyncClient, db_session,
):
    """Manual unsubscribe with no other consumers: row goes away."""
    acct = await _make_account(db_session)
    await db_session.commit()

    body = {"account_id": acct.id, "symbol": "QQQ", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    sub_id = r.json()["id"]

    r = await client.post(f"/api/live-subscriptions/{sub_id}/unsubscribe")
    assert r.status_code == 200, r.text

    # expire_all so this session sees data committed by the API's session.
    await db_session.rollback()
    row = (await db_session.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub_id)
    )).scalar_one_or_none()
    assert row is None, "subscription should be auto-deleted when consumer count hits 0"


@pytest.mark.asyncio
async def test_delete_refuses_when_consumers_exist(client: AsyncClient, db_session):
    acct = await _make_account(db_session)
    await db_session.commit()

    body = {"account_id": acct.id, "symbol": "AAPL", "asset_class": "equities"}
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
    acct = await _make_account(db_session)
    await db_session.commit()

    body = {"account_id": acct.id, "symbol": "NVDA", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    sub_id = r.json()["id"]

    r = await client.get("/api/live-subscriptions")
    rows = r.json()
    matching = [r for r in rows if r["id"] == sub_id]
    assert len(matching) == 1
    assert "consumers" in matching[0]
    assert len(matching[0]["consumers"]) == 1


@pytest.mark.asyncio
async def test_create_subscription_requires_account_id(client, db_session):
    from coordinator.database.models import Account
    acct = Account(name="Alpaca Test", broker_type="alpaca",
                   credentials="{}", supported_asset_types=["equities"])
    db_session.add(acct)
    await db_session.commit()

    body = {"account_id": acct.id, "symbol": "SPY",
            "asset_class": "equities", "tick_retention_hours": 168}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["account_id"] == acct.id
    assert data["account_name"] == "Alpaca Test"
    assert data["broker"] == "alpaca"


@pytest.mark.asyncio
async def test_provider_subscription_requires_neither_or_both_raises_422(client, db_session):
    """Providing neither account_id nor provider_type should return 422."""
    body = {"symbol": "SPY", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_provider_subscription_both_raises_422(client, db_session):
    """Providing both account_id and provider_type should return 422."""
    from coordinator.database.models import Account
    acct = Account(name="A", broker_type="alpaca", credentials="{}",
                   supported_asset_types=["equities"])
    db_session.add(acct)
    await db_session.commit()

    body = {"account_id": acct.id, "provider_type": "polygon",
            "symbol": "SPY", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_provider_subscription_polygon_no_key_returns_422(client, db_session):
    """Creating a polygon subscription without polygon_api_key in Settings returns 422."""
    body = {"provider_type": "polygon", "symbol": "SPY", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 422, r.text
    assert "Polygon API key" in r.json()["detail"]


@pytest.mark.asyncio
async def test_provider_subscription_polygon_with_key_creates_sub(client, db_session):
    """With polygon_api_key configured, provider-based sub creates a row."""
    from coordinator.database.models import Setting
    db_session.add(Setting(key="polygon_api_key", value="fake-key", encrypted=False))
    await db_session.commit()

    body = {"provider_type": "polygon", "symbol": "AAPL", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["account_id"] is None
    assert data["provider_type"] == "polygon"
    assert data["broker"] == "polygon"
    assert data["symbol"] == "AAPL"
    assert len(data["consumers"]) == 1
    assert data["consumers"][0]["consumer_type"] == "manual"


@pytest.mark.asyncio
async def test_provider_subscription_response_has_null_account_name(client, db_session):
    """Provider-based subscriptions should have account_name=None."""
    from coordinator.database.models import Setting
    db_session.add(Setting(key="polygon_api_key", value="fake-key", encrypted=False))
    await db_session.commit()

    body = {"provider_type": "polygon", "symbol": "TSLA", "asset_class": "equities"}
    r = await client.post("/api/live-subscriptions", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["account_name"] is None
    assert data["provider_type"] == "polygon"


@pytest.mark.asyncio
async def test_response_includes_algorithm_name_on_algo_consumer(
    client, db_session,
):
    from coordinator.database.models import (
        Account, Algorithm, AlgorithmInstance, Worker,
        LiveSubscription, SubscriptionConsumer,
    )
    worker = Worker(name="W", status="online")
    acct = Account(name="A", broker_type="alpaca", credentials="{}",
                   supported_asset_types=["equities"])
    algo = Algorithm(repo_url="x", name="trend-bot")
    db_session.add_all([worker, acct, algo])
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
        status="running",
    )
    db_session.add(inst)
    await db_session.flush()
    sub = LiveSubscription(
        account_id=acct.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    )
    db_session.add(sub)
    await db_session.flush()
    db_session.add(SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="algo", consumer_id=inst.id,
    ))
    await db_session.commit()

    r = await client.get("/api/live-subscriptions")
    rows = r.json()
    matching = next(row for row in rows if row["id"] == sub.id)
    algo_consumer = next(c for c in matching["consumers"] if c["consumer_type"] == "algo")
    assert algo_consumer["algorithm_id"] == algo.id
    assert algo_consumer["algorithm_name"] == "trend-bot"
