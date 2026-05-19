import pytest
from sqlalchemy import select

from coordinator.database.models import Account, LiveSubscription


@pytest.mark.asyncio
async def test_live_subscription_has_account_id_fk(db_session):
    acct = Account(
        name="A", broker_type="alpaca", credentials="{}",
        supported_asset_types=["equities"],
    )
    db_session.add(acct)
    await db_session.flush()

    sub = LiveSubscription(
        account_id=acct.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    )
    db_session.add(sub)
    await db_session.commit()

    refetched = (await db_session.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub.id)
    )).scalar_one()
    assert refetched.account_id == acct.id


@pytest.mark.asyncio
async def test_two_accounts_can_subscribe_to_same_symbol(db_session):
    a1 = Account(name="A1", broker_type="alpaca", credentials="{}",
                 supported_asset_types=["equities"])
    a2 = Account(name="A2", broker_type="alpaca", credentials="{}",
                 supported_asset_types=["equities"])
    db_session.add_all([a1, a2])
    await db_session.flush()
    db_session.add(LiveSubscription(
        account_id=a1.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    ))
    db_session.add(LiveSubscription(
        account_id=a2.id, broker="alpaca", symbol="SPY",
        asset_class="equities", status="running",
    ))
    await db_session.commit()
    rows = (await db_session.execute(select(LiveSubscription))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_provider_subscription_has_null_account_id(db_session):
    """Provider-based subscriptions store account_id=None and provider_type set."""
    sub = LiveSubscription(
        account_id=None, provider_type="polygon",
        broker="polygon", symbol="AAPL",
        asset_class="equities", status="running",
    )
    db_session.add(sub)
    await db_session.commit()

    refetched = (await db_session.execute(
        select(LiveSubscription).where(LiveSubscription.id == sub.id)
    )).scalar_one()
    assert refetched.account_id is None
    assert refetched.provider_type == "polygon"


@pytest.mark.asyncio
async def test_multiple_provider_subs_for_same_symbol_allowed(db_session):
    """Two provider-based subs for same symbol can coexist (no DB constraint)."""
    db_session.add(LiveSubscription(
        account_id=None, provider_type="polygon",
        broker="polygon", symbol="SPY",
        asset_class="equities", status="running",
    ))
    db_session.add(LiveSubscription(
        account_id=None, provider_type="thetadata",
        broker="thetadata", symbol="SPY",
        asset_class="equities", status="running",
    ))
    await db_session.commit()
    rows = (await db_session.execute(select(LiveSubscription))).scalars().all()
    assert len(rows) == 2
