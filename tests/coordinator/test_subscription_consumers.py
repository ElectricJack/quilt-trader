import pytest
from sqlalchemy import select

from coordinator.database.models import LiveSubscription, SubscriptionConsumer


@pytest.mark.asyncio
async def test_subscription_consumer_basic_lifecycle(db_session):
    """A subscription can have multiple consumers; the relationship cascades on
    subscription delete."""
    sub = LiveSubscription(
        broker="alpaca", symbol="SPY", status="running",
        asset_class="equities", tick_retention_hours=168,
    )
    db_session.add(sub)
    await db_session.flush()

    manual = SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="manual", consumer_id=None,
    )
    algo = SubscriptionConsumer(
        subscription_id=sub.id, consumer_type="algo", consumer_id="deployment-abc",
    )
    db_session.add_all([manual, algo])
    await db_session.commit()

    consumers = (await db_session.execute(
        select(SubscriptionConsumer).where(
            SubscriptionConsumer.subscription_id == sub.id
        )
    )).scalars().all()
    assert len(consumers) == 2

    # Deleting the subscription cascade-deletes its consumer rows.
    await db_session.delete(sub)
    await db_session.commit()
    remaining = (await db_session.execute(
        select(SubscriptionConsumer).where(
            SubscriptionConsumer.subscription_id == sub.id
        )
    )).scalars().all()
    assert remaining == []
