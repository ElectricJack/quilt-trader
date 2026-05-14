import pytest
import pytest_asyncio
from datetime import date
from unittest.mock import patch
from coordinator.database.models import Account
from worker.broker_adapter import OptionContract, OptionChainSnapshot

from coordinator.main import create_app
from coordinator.api.routes import options_chain as options_chain_routes
from coordinator.api.dependencies import get_container
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def test_app():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    # The static-files catch-all mount (dashboard) is registered last in
    # create_app. Remove it temporarily so include_router can insert the
    # options-chain route before the catch-all, then re-append it.
    static_mount = None
    for i, route in enumerate(app.routes):
        if getattr(route, "name", "") == "dashboard":
            static_mount = app.routes.pop(i)
            break
    app.include_router(options_chain_routes.router)
    if static_mount is not None:
        app.routes.append(static_mount)
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def db_session(test_app):
    container = get_container()
    async with container.session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_get_expiries_returns_dates(client, db_session, monkeypatch):
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["options"], pdt_mode="off")
    db_session.add(account); await db_session.flush()
    from coordinator.api.routes import options_chain
    async def fake_adapter(acct):
        class FA:
            def list_option_expiries(self, underlying):
                return [date(2026, 5, 16), date(2026, 6, 20)]
            def close(self): pass
        return FA()
    monkeypatch.setattr(options_chain, "_adapter_for_account", fake_adapter)

    r = await client.get(f"/api/accounts/{account.id}/options-chain/expiries",
                         params={"underlying": "SPY"})
    assert r.status_code == 200
    assert r.json() == {"expiries": ["2026-05-16", "2026-06-20"]}

@pytest.mark.asyncio
async def test_get_chain_returns_serialized_contracts(client, db_session, monkeypatch):
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["options"], pdt_mode="off")
    db_session.add(account); await db_session.flush()
    from coordinator.api.routes import options_chain
    async def fake_adapter(acct):
        class FA:
            def get_option_chain(self, underlying, expiry):
                return OptionChainSnapshot(
                    underlying="SPY", spot=565.0, expiry=expiry, as_of=None,
                    contracts=[
                        OptionContract(strike=560.0, right="call",
                            occ_symbol="SPY260620C00560000", bid=8.2, ask=8.4,
                            last=8.3, iv=0.30, delta=0.55, gamma=0.020,
                            theta=-14.1, vega=48.0, open_interest=2345, volume=789),
                    ],
                )
            def close(self): pass
        return FA()
    monkeypatch.setattr(options_chain, "_adapter_for_account", fake_adapter)

    r = await client.get(
        f"/api/accounts/{account.id}/options-chain/2026-06-20",
        params={"underlying": "SPY"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["underlying"] == "SPY"
    assert body["spot"] == 565.0
    assert len(body["contracts"]) == 1
    assert body["contracts"][0]["strike"] == 560.0

@pytest.mark.asyncio
async def test_chain_423_when_locked(client, db_session):
    account = Account(name="A", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["options"], pdt_mode="off",
                      locked_by="inst-1")
    db_session.add(account); await db_session.flush()
    r = await client.get(f"/api/accounts/{account.id}/options-chain/expiries",
                         params={"underlying": "SPY"})
    assert r.status_code == 423
