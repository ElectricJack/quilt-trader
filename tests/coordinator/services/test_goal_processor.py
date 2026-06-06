"""Tests for the options goal's incremental download phase.

Covers the design in
``docs/superpowers/specs/2026-05-27-options-goal-incremental-download-design.md``:
in-flight cap, event-driven enqueue, exponential backoff for failed
contracts, completion-phase transition, and disk-cache freshness.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import (
    Base,
    DataGoal,
    MarketDataDownload,
)
from coordinator.services.goal_processor import GoalProcessor


CONTRACTS = [
    {"symbol": f"SPY240603C00{500 + i:03d}000", "expiration": "2024-06-03"}
    for i in range(10)
]


@pytest_asyncio.fixture
async def engine_and_factory():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)
    yield engine, sf
    await engine.dispose()


class _FakeDownloadManager:
    """Records create_download calls; exposes a configurable concurrency cap."""

    def __init__(self, polygon_concurrency: int = 1) -> None:
        self._polygon_concurrency = polygon_concurrency
        self.created: list[dict] = []

    def concurrency_for(self, provider: str) -> int:
        return self._polygon_concurrency if provider == "polygon" else 1

    async def create_download(self, **kwargs):
        self.created.append(kwargs)
        return {"id": f"fake-{len(self.created)}", "status": "queued"}


class _FakeDataService:
    """In-memory data service: writes nothing, but answers list_option_contracts
    and load_market_data from an explicit set of "on-disk" symbols.
    """

    def __init__(self, on_disk: set[str] | None = None, market_dir: str | None = None) -> None:
        self.on_disk: set[str] = set(on_disk or [])
        self._market_dir = market_dir or "/tmp/test-market"

    def list_option_contracts(self, provider, underlying, exp):
        return []

    def load_market_data(self, provider, symbol, timeframe):
        return None  # we deliberately don't expose loaded bars; cache drives "on disk"


async def _make_goal(sf, contracts=None, provider="polygon"):
    contracts = contracts or CONTRACTS
    g = DataGoal(
        name="SPY options test",
        goal_type="options",
        config={
            "underlying": "SPY",
            "provider": provider,
            "date_start": "2024-06-01",
            "date_end": "2024-06-30",
            "frequencies": ["daily"],
            "strike_range": "all",
            "max_contracts_per_exp": 60,
        },
        status="active",
        phase="downloading",
        discovered_contracts=contracts,
        total_items=len(contracts),
        completed_items=0,
    )
    async with sf() as session:
        session.add(g)
        await session.commit()
        return g.id


def _make_processor(sf, dm, ds, market_dir):
    gp = GoalProcessor(
        session_factory=sf,
        download_manager=dm,
        data_service=ds,
        providers={"polygon": object()},
    )
    gp._market_dir = market_dir  # override default; goal_processor reads from this
    return gp


# ─── tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_caps_in_flight_at_two(tmp_path, engine_and_factory):
    """One tick with no in-flight rows should enqueue exactly (concurrency + 1)
    = 2 downloads. Running a second tick with those still queued should
    enqueue nothing further."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    Path(market_dir, "polygon").mkdir(parents=True)

    goal_id = await _make_goal(sf)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)
    assert len(dm.created) == 2

    # Mark those 2 rows as 'queued' in the downloads table so the next tick
    # counts them as in-flight.
    async with sf() as s:
        for c in dm.created:
            sym = c["symbols"][0]
            s.add(MarketDataDownload(
                symbols=[sym], date_range_start=date(2024, 6, 1), date_range_end=date(2024, 6, 3),
                provider="polygon", data_type="bars", timeframe="1day",
                status="queued", progress_current=0, progress_total=1,
            ))
        await s.commit()

    dm.created.clear()
    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)
    assert dm.created == []


@pytest.mark.asyncio
async def test_completion_event_triggers_next_enqueue(tmp_path, engine_and_factory):
    """Firing on_download_complete should re-enqueue immediately, not wait
    for the next tick."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    poly = Path(market_dir) / "polygon"
    poly.mkdir(parents=True)

    goal_id = await _make_goal(sf)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    # First tick fills the in-flight slots up to cap=2.
    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)
    assert len(dm.created) == 2
    first_two = [c["symbols"][0] for c in dm.created]
    # Persist them as 'running' so the in-flight set sees them.
    async with sf() as s:
        for sym in first_two:
            s.add(MarketDataDownload(
                symbols=[sym], date_range_start=date(2024, 6, 1), date_range_end=date(2024, 6, 3),
                provider="polygon", data_type="bars", timeframe="1day",
                status="running", progress_current=0, progress_total=1,
            ))
        await s.commit()

    dm.created.clear()

    # Simulate one download completing: mark the row 'completed' and create
    # the parquet stub so the disk cache will include it after refresh.
    completed_sym = first_two[0]
    async with sf() as s:
        rows = (await s.execute(select(MarketDataDownload))).scalars().all()
        for row in rows:
            if completed_sym in (row.symbols or []):
                row.status = "completed"
                row.completed_at = datetime.now(timezone.utc)
        await s.commit()
    sym_dir = poly / completed_sym
    sym_dir.mkdir()
    (sym_dir / "1day.parquet").write_bytes(b"x")

    # Event handler should enqueue one more (to restore in-flight = 2).
    await gp.on_download_complete("polygon", [completed_sym])
    assert len(dm.created) == 1
    new_sym = dm.created[0]["symbols"][0]
    assert new_sym not in first_two


@pytest.mark.asyncio
async def test_failed_symbol_is_backed_off(tmp_path, engine_and_factory):
    """A symbol with a recent failed row must be excluded from the eligible
    pending set."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    Path(market_dir, "polygon").mkdir(parents=True)

    goal_id = await _make_goal(sf)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    # Pin the first two symbols as "recently failed."
    blocked = [CONTRACTS[0]["symbol"], CONTRACTS[1]["symbol"]]
    async with sf() as s:
        for sym in blocked:
            s.add(MarketDataDownload(
                symbols=[sym], date_range_start=date(2024, 6, 1), date_range_end=date(2024, 6, 3),
                provider="polygon", data_type="bars", timeframe="1day",
                status="failed", progress_current=0, progress_total=1,
                completed_at=datetime.now(timezone.utc),
                error_message="boom",
            ))
        await s.commit()

    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)

    assert len(dm.created) == 2
    picked = {c["symbols"][0] for c in dm.created}
    assert picked.isdisjoint(set(blocked))


@pytest.mark.asyncio
async def test_no_data_returned_is_terminal_not_retried(tmp_path, engine_and_factory):
    """A 'no data returned by polygon' failure should NEVER be retried.
    The contract is counted toward failed_items and excluded from pending."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    Path(market_dir, "polygon").mkdir(parents=True)

    contracts = CONTRACTS[:3]
    goal_id = await _make_goal(sf, contracts=contracts)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    # Symbol 1 has a transient failure 10 seconds ago in the downloads log —
    # backed off (will retry after 1 min). Set up first so it's visible to any
    # subsequent enqueue triggered by the terminal-failure completion event.
    # Symbol 0 had a terminal "no data" completion event — recorded on the
    # goal's terminal_symbols, never retried.
    # Symbol 2 is clean — should be picked.
    async with sf() as s:
        s.add(MarketDataDownload(
            symbols=[contracts[1]["symbol"]], date_range_start=date(2024, 6, 1),
            date_range_end=date(2024, 6, 3), provider="polygon", data_type="bars",
            timeframe="1day", status="failed", progress_current=0, progress_total=1,
            completed_at=datetime.now(timezone.utc) - timedelta(seconds=10),
            error_message=f"{contracts[1]['symbol']}: HTTPError 500 from polygon",
        ))
        await s.commit()
    await gp.on_download_complete(
        "polygon",
        [contracts[0]["symbol"]],
        status="failed",
        error_message=f"{contracts[0]['symbol']}: no data returned by polygon for 2024-06-01 to 2024-06-03",
    )

    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)

    # Only the clean symbol gets enqueued. Cap is 2 but only 1 is eligible.
    picked = {c["symbols"][0] for c in dm.created}
    assert picked == {contracts[2]["symbol"]}

    # The terminal failure is counted into failed_items.
    async with sf() as s:
        g = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        assert g.failed_items == 1


@pytest.mark.asyncio
async def test_goal_completes_when_terminal_plus_ondisk_equals_total(tmp_path, engine_and_factory):
    """If every discovered symbol is either on-disk OR has a terminal failure,
    the goal transitions to completed even without retrying the terminals."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    poly = Path(market_dir) / "polygon"
    poly.mkdir(parents=True)

    contracts = CONTRACTS[:3]
    goal_id = await _make_goal(sf, contracts=contracts)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    # Two on disk, one recorded as terminal directly on the goal.
    for c in contracts[:2]:
        d = poly / c["symbol"]; d.mkdir()
        (d / "1day.parquet").write_bytes(b"x")
    async with sf() as s:
        g = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        g.terminal_symbols = [contracts[2]["symbol"]]
        await s.commit()

    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)

    async with sf() as s:
        g = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        assert g.phase == "completed"
        assert g.status == "completed"
        assert g.completed_items == 2
        assert g.failed_items == 1
    assert dm.created == []


def test_exponential_backoff_grows_with_count():
    """min(60 * 2**(count-1), 86_400) — 1m, 2m, 4m, ... 24h cap."""
    from coordinator.services.goal_processor import _backoff_seconds
    assert _backoff_seconds(1) == 60         # 1 min
    assert _backoff_seconds(2) == 120        # 2 min
    assert _backoff_seconds(3) == 240        # 4 min
    assert _backoff_seconds(4) == 480        # 8 min
    assert _backoff_seconds(11) == 60 * 2**10  # 61_440s, still under cap
    assert _backoff_seconds(12) == 86_400    # 60 * 2**11 = 122_880, capped
    assert _backoff_seconds(20) == 86_400


@pytest.mark.asyncio
async def test_phase_transitions_to_completed(tmp_path, engine_and_factory):
    """All symbols on disk + no in-flight ⇒ phase=status=completed."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    poly = Path(market_dir) / "polygon"
    poly.mkdir(parents=True)
    for c in CONTRACTS:
        d = poly / c["symbol"]
        d.mkdir()
        (d / "1day.parquet").write_bytes(b"x")

    goal_id = await _make_goal(sf)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)

    async with sf() as s:
        g = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        assert g.phase == "completed"
        assert g.status == "completed"
        assert g.completed_items == len(CONTRACTS)
    assert dm.created == []


@pytest.mark.asyncio
async def test_disk_cache_refreshes_at_ttl(tmp_path, engine_and_factory, monkeypatch):
    """os.scandir is only called once per 60s window."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    Path(market_dir, "polygon").mkdir(parents=True)

    goal_id = await _make_goal(sf)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    scan_calls = {"n": 0}

    import os as _os
    real_scandir = _os.scandir

    def counting_scandir(path):
        scan_calls["n"] += 1
        return real_scandir(path)

    monkeypatch.setattr("coordinator.services.goal_processor.os.scandir", counting_scandir)

    # Pin time so we can advance it deterministically.
    fake_now = [datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(
        "coordinator.services.goal_processor._utcnow",
        lambda: fake_now[0],
    )

    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)

    await gp._download_options(goal)
    assert scan_calls["n"] == 1

    # 30s later — still within TTL.
    fake_now[0] += timedelta(seconds=30)
    await gp._download_options(goal)
    assert scan_calls["n"] == 1

    # 90s after the original — TTL has expired.
    fake_now[0] += timedelta(seconds=60)
    await gp._download_options(goal)
    assert scan_calls["n"] == 2


@pytest.mark.asyncio
async def test_terminal_state_survives_downloads_table_clear(tmp_path, engine_and_factory):
    """Bug: clearing the market_data_downloads table must not cause the goal
    to re-attempt contracts that Polygon already authoritatively answered
    'no data' for. The terminal set must live on the DataGoal row, not be
    derived from the transient downloads log."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    Path(market_dir, "polygon").mkdir(parents=True)

    contracts = CONTRACTS[:3]
    goal_id = await _make_goal(sf, contracts=contracts)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    terminal_sym = contracts[0]["symbol"]

    # Simulate the download manager firing a completion event for a 'no data'
    # terminal failure on symbol 0.
    await gp.on_download_complete(
        "polygon",
        [terminal_sym],
        status="failed",
        error_message=f"{terminal_sym}: no data returned by polygon for 2024-06-01 to 2024-06-03",
    )

    # The goal row now records the terminal symbol.
    async with sf() as s:
        g = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        assert terminal_sym in (g.terminal_symbols or [])

    # User clears the downloads log (cleanup). The goal's terminal state must
    # survive this.
    async with sf() as s:
        for row in (await s.execute(select(MarketDataDownload))).scalars().all():
            await s.delete(row)
        await s.commit()

    # Next processor tick: terminal_sym must NOT be re-enqueued, and
    # failed_items must remain 1.
    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)

    picked = {c["symbols"][0] for c in dm.created}
    assert terminal_sym not in picked, (
        f"terminal symbol {terminal_sym} was re-enqueued after downloads clear"
    )
    async with sf() as s:
        g = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        assert g.failed_items == 1


@pytest.mark.asyncio
async def test_disk_cache_updated_incrementally_on_completion(tmp_path, engine_and_factory, monkeypatch):
    """on_download_complete must add the symbol to the in-memory cache without
    a full os.scandir refresh."""
    engine, sf = engine_and_factory
    market_dir = str(tmp_path / "market")
    poly = Path(market_dir) / "polygon"
    poly.mkdir(parents=True)

    goal_id = await _make_goal(sf)
    dm = _FakeDownloadManager(polygon_concurrency=1)
    ds = _FakeDataService(market_dir=market_dir)
    gp = _make_processor(sf, dm, ds, market_dir)

    scan_calls = {"n": 0}
    import os as _os
    real_scandir = _os.scandir

    def counting_scandir(path):
        scan_calls["n"] += 1
        return real_scandir(path)

    monkeypatch.setattr("coordinator.services.goal_processor.os.scandir", counting_scandir)

    # Prime the cache once.
    async with sf() as s:
        goal = (await s.execute(select(DataGoal).where(DataGoal.id == goal_id))).scalar_one()
        s.expunge(goal)
    await gp._download_options(goal)
    assert scan_calls["n"] == 1
    assert gp._disk_cache.get("polygon") == set()

    # Simulate a download finishing for one symbol — drop the parquet, fire
    # the event, observe the cache update without another scandir call.
    sym = CONTRACTS[0]["symbol"]
    d = poly / sym
    d.mkdir()
    (d / "1day.parquet").write_bytes(b"x")

    await gp.on_download_complete("polygon", [sym])
    assert scan_calls["n"] == 1  # no extra full scan
    assert sym in gp._disk_cache["polygon"]
