import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_entities(client):
    """Create account and worker needed for algorithm instances."""
    acct_resp = await client.post("/api/accounts", json={
        "name": "Test Acct",
        "broker_type": "alpaca",
        "credentials": {"api_key": "k"},
        "supported_asset_types": ["equities"],
        "pdt_mode": "off",
    })
    worker_resp = await client.post("/api/workers", json={
        "name": "Test Pi",
        "tailscale_ip": "100.64.0.1",
    })
    return {
        "account_id": acct_resp.json()["id"],
        "worker_id": worker_resp.json()["id"],
    }


@pytest.mark.asyncio
async def test_create_algorithm(client):
    response = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/ElectricJack/momentum-scalper",
        "name": "momentum-scalper",
        "description": "Intraday momentum",
        "version": "1.0.0",
        "commit_hash": "abc123",
        "required_asset_types": ["equities"],
        "config_schema": {"parameters": []},
    })
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "momentum-scalper"
    assert body["install_status"] == "installed"


@pytest.mark.asyncio
async def test_list_algorithms(client):
    await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo1",
        "name": "algo-1",
    })
    await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo2",
        "name": "algo-2",
    })
    response = await client.get("/api/algorithms")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_algorithm(client):
    create_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/algo",
        "name": "test-algo",
    })
    algo_id = create_resp.json()["id"]
    response = await client.get(f"/api/algorithms/{algo_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "test-algo"


@pytest.mark.asyncio
async def test_delete_algorithm(client):
    create_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/delete-me",
        "name": "delete-me",
    })
    algo_id = create_resp.json()["id"]
    response = await client.delete(f"/api/algorithms/{algo_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_algorithm_with_dependents(client, db_session):
    """DELETE /api/algorithms/:id should cascade-delete instances, runs,
    decision_log, backtest_comparisons, positions, trades, and pdt_tracking."""
    from datetime import datetime, timezone
    from coordinator.database.models import (
        Account,
        Algorithm,
        AlgorithmInstance,
        AlgorithmRun,
        BacktestComparison,
        DecisionLog,
        PDTTracking,
        Position,
        TradeLog,
        Worker,
    )
    from sqlalchemy import select

    # Seed supporting rows.
    algo = Algorithm(
        repo_url="https://github.com/example/cascade-algo",
        name="CascadeAlgo",
        install_status="installed",
    )
    other_algo = Algorithm(
        repo_url="https://github.com/example/other",
        name="OtherAlgo",
        install_status="installed",
    )
    worker = Worker(name="w-cascade", tailscale_ip="100.64.0.50", status="online")
    db_session.add_all([algo, other_algo, worker])
    await db_session.flush()

    acct = Account(
        name="Algo Cascade",
        broker_type="alpaca",
        supported_asset_types=["equities"],
        pdt_mode="off",
        credentials="{}",
    )
    db_session.add(acct)
    await db_session.flush()

    instance = AlgorithmInstance(
        algorithm_id=algo.id,
        account_id=acct.id,
        worker_id=worker.id,
        status="running",
    )
    db_session.add(instance)
    await db_session.flush()

    # Lock the account to this instance to exercise the locked_by clear path.
    acct.locked_by = instance.id

    run = AlgorithmRun(instance_id=instance.id, run_number=1, status="completed")
    db_session.add(run)
    await db_session.flush()

    instance.active_run_id = run.id

    decision = DecisionLog(
        instance_id=instance.id,
        mode="live",
        tick_data={},
        signals_produced=[],
    )
    backtest = BacktestComparison(
        instance_id=instance.id,
        algorithm_id=algo.id,
        time_range_start=datetime.now(timezone.utc),
        time_range_end=datetime.now(timezone.utc),
        total_ticks=10,
        matching_ticks=10,
        match_percentage=100.0,
    )
    position = Position(
        instance_id=instance.id,
        account_id=acct.id,
        legs=[],
        status="open",
        net_cost=1500.0,
    )
    trade = TradeLog(
        account_id=acct.id,
        instance_id=instance.id,
        source="algo",
        symbol="AAPL",
        side="buy",
        quantity=10.0,
        filled_price=150.0,
    )
    db_session.add_all([decision, backtest, position, trade])
    await db_session.flush()

    pdt = PDTTracking(
        account_id=acct.id,
        trade_id=trade.id,
        symbol="AAPL",
        open_timestamp=datetime.now(timezone.utc),
        close_timestamp=datetime.now(timezone.utc),
        day_trade_date=datetime.now(timezone.utc).date(),
    )
    db_session.add(pdt)
    await db_session.commit()

    algo_id = algo.id
    other_algo_id = other_algo.id
    account_id = acct.id
    instance_id = instance.id
    trade_id = trade.id

    response = await client.delete(f"/api/algorithms/{algo_id}")
    assert response.status_code == 204

    # The algorithm is gone.
    get_resp = await client.get(f"/api/algorithms/{algo_id}")
    assert get_resp.status_code == 404

    # All dependents are gone.
    assert (await db_session.execute(
        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(AlgorithmRun).where(AlgorithmRun.instance_id == instance_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(DecisionLog).where(DecisionLog.instance_id == instance_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(BacktestComparison).where(BacktestComparison.algorithm_id == algo_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(Position).where(Position.instance_id == instance_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(TradeLog).where(TradeLog.id == trade_id)
    )).scalar_one_or_none() is None

    assert (await db_session.execute(
        select(PDTTracking).where(PDTTracking.trade_id == trade_id)
    )).scalar_one_or_none() is None

    # The account survives, but its locked_by has been cleared.
    # Expire the session cache so we see the API session's writes.
    db_session.expire_all()
    acct_after = (await db_session.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    assert acct_after is not None
    assert acct_after.locked_by is None

    # The unrelated algorithm survives.
    assert (await db_session.execute(
        select(Algorithm).where(Algorithm.id == other_algo_id)
    )).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_create_instance(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/inst-algo",
        "name": "inst-algo",
    })
    algo_id = algo_resp.json()["id"]

    response = await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
        "config_values": {"risk_per_trade": 0.02},
    })
    assert response.status_code == 201
    body = response.json()
    assert body["algorithm_id"] == algo_id
    assert body["account_id"] == seed_entities["account_id"]
    assert body["status"] == "stopped"
    assert body["config_values"] == {"risk_per_trade": 0.02}


@pytest.mark.asyncio
async def test_list_instances_for_algorithm(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/list-inst",
        "name": "list-inst",
    })
    algo_id = algo_resp.json()["id"]

    await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
    })
    response = await client.get(f"/api/algorithms/{algo_id}/instances")
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_get_instance(client, seed_entities):
    algo_resp = await client.post("/api/algorithms", json={
        "repo_url": "https://github.com/test/get-inst",
        "name": "get-inst",
    })
    algo_id = algo_resp.json()["id"]

    inst_resp = await client.post(f"/api/algorithms/{algo_id}/instances", json={
        "account_id": seed_entities["account_id"],
        "worker_id": seed_entities["worker_id"],
    })
    inst_id = inst_resp.json()["id"]
    response = await client.get(f"/api/instances/{inst_id}")
    assert response.status_code == 200
    assert response.json()["id"] == inst_id


class TestInstancePatchDelete:
    @pytest.mark.asyncio
    async def test_update_instance_config(self, client):
        # Create algo, then instance, then patch config
        algo = await client.post("/api/algorithms", json={
            "repo_url": "https://github.com/test/algo", "name": "Test"
        })
        algo_id = algo.json()["id"]
        # Need account + worker IDs - create them first
        acct = await client.post("/api/accounts", json={
            "name": "Test", "broker_type": "mock", "credentials": {},
            "supported_asset_types": ["equities"]
        })
        worker = await client.post("/api/workers", json={
            "name": "w1", "tailscale_ip": "10.0.0.1"
        })
        inst = await client.post(f"/api/algorithms/{algo_id}/instances", json={
            "account_id": acct.json()["id"], "worker_id": worker.json()["id"],
            "config_values": {"old": "value"}
        })
        inst_id = inst.json()["id"]

        resp = await client.patch(f"/api/instances/{inst_id}", json={
            "config_values": {"new": "config"}
        })
        assert resp.status_code == 200
        assert resp.json()["config_values"] == {"new": "config"}

    @pytest.mark.asyncio
    async def test_update_instance_not_found(self, client):
        resp = await client.patch("/api/instances/nonexistent", json={"status": "running"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_instance(self, client):
        algo = await client.post("/api/algorithms", json={
            "repo_url": "https://github.com/test/algo", "name": "Test"
        })
        acct = await client.post("/api/accounts", json={
            "name": "Test", "broker_type": "mock", "credentials": {},
            "supported_asset_types": ["equities"]
        })
        worker = await client.post("/api/workers", json={
            "name": "w1", "tailscale_ip": "10.0.0.1"
        })
        inst = await client.post(f"/api/algorithms/{algo.json()['id']}/instances", json={
            "account_id": acct.json()["id"], "worker_id": worker.json()["id"]
        })
        resp = await client.delete(f"/api/instances/{inst.json()['id']}")
        assert resp.status_code == 204
        get_resp = await client.get(f"/api/instances/{inst.json()['id']}")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_instance_not_found(self, client):
        resp = await client.delete("/api/instances/nonexistent")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_instances_enriched_fields(client, db_session):
    from datetime import datetime, timezone
    from coordinator.database.models import (
        Account, Worker, Algorithm, AlgorithmInstance, AlgorithmRun, TradeLog,
    )

    acct = Account(name="Alpaca Main", broker_type="alpaca", supported_asset_types=["equities"], pdt_mode="off", credentials="{}")
    db_session.add(acct)
    worker = Worker(name="pi-alpha", tailscale_ip="1.1.1.1", status="active", max_algorithms=2)
    db_session.add(worker)
    algo = Algorithm(repo_url="x", name="momentum-btc", install_status="installed")
    db_session.add(algo)
    await db_session.flush()
    inst = AlgorithmInstance(
        algorithm_id=algo.id, account_id=acct.id, worker_id=worker.id,
        status="running", state_stale=False,
    )
    db_session.add(inst)
    await db_session.flush()
    db_session.add(AlgorithmRun(
        instance_id=inst.id, run_number=1, status="running",
        equity_curve=[
            {"timestamp": "2026-01-01T00:00:00Z", "equity": 10000.0},
            {"timestamp": "2026-01-02T00:00:00Z", "equity": 10100.0},
            {"timestamp": "2026-01-03T00:00:00Z", "equity": 10250.0},
        ],
    ))
    now = datetime.now(timezone.utc)
    db_session.add(TradeLog(
        account_id=acct.id, instance_id=inst.id, source="alpaca",
        symbol="BTC", side="buy", quantity=0.01, filled_price=70000.0,
        timestamp=now,
    ))
    await db_session.commit()

    response = await client.get("/api/instances")
    body = response.json()
    assert len(body) >= 1
    row = next(r for r in body if r["id"] == inst.id)
    assert row["algorithm_name"] == "momentum-btc"
    assert row["account_name"] == "Alpaca Main"
    assert row["pnl_sparkline"] is not None
    assert len(row["pnl_sparkline"]) <= 20
    assert row["today_pnl"] is not None
