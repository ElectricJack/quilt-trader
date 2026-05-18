import pytest

from coordinator.database.models import Algorithm


@pytest.mark.asyncio
async def test_algorithm_assets_field_exists_and_stores_list(db_session):
    """The algorithms table has an `assets` column holding the new
    {broker, symbol, asset_class} list format."""
    algo = Algorithm(
        repo_url="https://example.com/algo.git",
        name="test-algo",
        assets=[
            {"broker": "alpaca", "symbol": "SPY", "asset_class": "equities"},
            {"broker": "alpaca", "symbol": "BTCUSD", "asset_class": "crypto"},
        ],
    )
    db_session.add(algo)
    await db_session.commit()
    await db_session.refresh(algo)
    assert len(algo.assets) == 2
    assert algo.assets[0]["broker"] == "alpaca"
    assert algo.assets[1]["asset_class"] == "crypto"
