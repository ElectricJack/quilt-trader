import asyncio
from datetime import date
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.api.routes.settings import _get_setting
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import Account, DataSource
from coordinator.services.data_service import DataService
from coordinator.services.download_manager import DownloadManager

router = APIRouter(prefix="/api/data", tags=["data"])

_data_service: Optional[DataService] = None
_download_manager: Optional[DownloadManager] = None


def set_data_service(svc: DataService) -> None:
    global _data_service
    _data_service = svc


def get_data_service() -> DataService:
    if _data_service is None:
        return DataService(market_data_dir="data/market", custom_data_dir="data/custom")
    return _data_service


def set_download_manager(mgr: DownloadManager) -> None:
    global _download_manager
    _download_manager = mgr


def get_download_manager() -> DownloadManager:
    if _download_manager is None:
        raise HTTPException(status_code=503, detail="Download manager not initialized")
    return _download_manager


def get_coverage_index():
    """Return the CoverageIndex from the service container, or None if not yet initialized."""
    try:
        container = get_container()
        return container.coverage_index
    except AssertionError:
        return None


class DownloadRequest(BaseModel):
    symbols: list[str]
    date_range_start: date
    date_range_end: date
    provider: str = "polygon"
    data_type: str = "bars"
    timeframe: str = "1min"


@router.get("/market/{symbol}/meta")
async def get_market_data_meta(
    symbol: str,
    timeframe: str = Query("1day"),
    provider: str = Query("polygon"),
    source: Optional[str] = Query(None),
):
    resolved_provider = source or provider
    svc = get_data_service()
    df = svc.load_market_data(resolved_provider, symbol, timeframe)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {resolved_provider}/{symbol}/{timeframe}")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return {
            "total_bars": len(df),
            "first_timestamp": df["timestamp"].min().isoformat() if len(df) > 0 else None,
            "last_timestamp": df["timestamp"].max().isoformat() if len(df) > 0 else None,
        }
    return {"total_bars": len(df), "first_timestamp": None, "last_timestamp": None}


@router.get("/market/{symbol}")
async def get_market_data(
    symbol: str,
    timeframe: str = Query("1day"),
    provider: str = Query("polygon"),
    source: Optional[str] = Query(None),
    start: Optional[str] = Query(None, description="ISO timestamp — include bars at or after this time"),
    end: Optional[str] = Query(None, description="ISO timestamp — include bars at or before this time"),
    limit: int = Query(5000, description="Maximum number of rows to return (most-recent N after filtering)"),
):
    resolved_provider = source or provider
    svc = get_data_service()
    df = svc.load_market_data(resolved_provider, symbol, timeframe)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {resolved_provider}/{symbol}/{timeframe}")

    # Filter by time window when a timestamp column is present.
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        if start:
            df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]

    total = len(df)
    # Return the most-recent `limit` rows so the browser sees recent data first.
    df = df.tail(limit)
    return {
        "data": df.to_dict(orient="records"),
        "total": total,
        "truncated": total > limit,
    }


@router.get("/custom/{source_name}")
async def get_custom_data(source_name: str, fmt: str = Query("csv")):
    svc = get_data_service()
    df = svc.load_custom_data(source_name, fmt)
    if df is None:
        raise HTTPException(status_code=404, detail=f"No data for {source_name}")
    return {"data": df.to_dict(orient="records")}


@router.get("/providers/timeframes")
async def list_providers_timeframes():
    """Return configured providers with their supported timeframes."""
    try:
        mgr = get_download_manager()
    except Exception:
        return {"providers": []}

    result = []
    for name in sorted(mgr._providers.keys()):
        prov = mgr._providers[name]
        # Derive supported timeframes from the provider's timeframe map
        timeframes = None
        if hasattr(prov, "supported_timeframes"):
            timeframes = prov.supported_timeframes
        if timeframes is None:
            mod = __import__(prov.__class__.__module__, fromlist=["TIMEFRAME_MAP", "_TF_MAP"])
            for attr in ("TIMEFRAME_MAP", "_TF_MAP"):
                tf_map = getattr(mod, attr, None)
                if tf_map and isinstance(tf_map, dict):
                    timeframes = sorted(tf_map.keys())
                    break
        if timeframes is None:
            timeframes = ["1day"]
        result.append({"name": name, "timeframes": timeframes})
    return {"providers": result}


async def _provider_availability(db: AsyncSession) -> list[dict]:
    """Return the per-provider availability matrix derived from Settings + Accounts.

    Order: alphabetical, stable. Each entry: {name, available, reason}.
    `reason` is None when available, otherwise an explanatory string.
    """
    polygon_key = await _get_setting(db, "polygon_api_key")
    theta_user = await _get_setting(db, "theta_data_username")
    theta_pw = await _get_setting(db, "theta_data_password")

    accounts_by_broker: dict[str, int] = {}
    rows = (await db.execute(select(Account))).scalars().all()
    for a in rows:
        accounts_by_broker[a.broker_type] = accounts_by_broker.get(a.broker_type, 0) + 1

    def _entry(name: str, available: bool, reason: str | None) -> dict:
        return {"name": name, "available": available, "reason": None if available else reason}

    matrix = [
        _entry("alpaca",   accounts_by_broker.get("alpaca", 0) > 0,
               "no alpaca account configured"),
        _entry("coinbase", accounts_by_broker.get("coinbase", 0) > 0,
               "no coinbase account configured"),
        _entry("polygon",  polygon_key is not None, "polygon_api_key not configured"),
        _entry("theta",    bool(theta_user and theta_pw),
               "theta credentials not configured"),
        _entry("tradier",  accounts_by_broker.get("tradier", 0) > 0,
               "no tradier account configured"),
        _entry("yfinance", True, None),
    ]
    return sorted(matrix, key=lambda e: e["name"])


@router.get("/providers")
async def list_providers(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Provider availability matrix for the dashboard's benchmark dropdown
    (and the create-run validator). See _provider_availability for the rules.
    """
    return await _provider_availability(db)


@router.get("/search-symbols")
async def search_symbols(
    q: str = Query("", min_length=1, description="Search query"),
    provider: str = Query("polygon"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search for symbols via a provider's ticker search API."""
    try:
        mgr = get_download_manager()
    except Exception:
        return {"results": []}
    prov = mgr._providers.get(provider)
    if prov is None or not hasattr(prov, "search_symbols"):
        return {"results": [], "error": f"Provider '{provider}' does not support symbol search"}
    results = await prov.search_symbols(q, limit=limit)
    return {"results": results, "provider": provider}


def _build_storage_summary_payload(svc: DataService) -> dict:
    """Compute the /api/data/storage-summary response.

    Pure function of disk state. Runs under asyncio.to_thread because it walks
    data/market/ and data/custom/ (~58k files).
    """
    import os

    market_dir = svc._market_dir
    custom_dir = svc._custom_dir

    def walk_with_attribution(root: str) -> tuple[int, dict[str, int]]:
        """Single pass over the tree: total bytes + per-top-level-subdir bytes."""
        total = 0
        per_top: dict[str, int] = {}
        if not os.path.isdir(root):
            return 0, {}
        root_abs = os.path.abspath(root)
        for dirpath, _, filenames in os.walk(root):
            rel = os.path.relpath(os.path.abspath(dirpath), root_abs)
            top = rel.split(os.sep, 1)[0] if rel and rel != "." else None
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                total += sz
                if top:
                    per_top[top] = per_top.get(top, 0) + sz
        return total, per_top

    market_bytes, by_provider = walk_with_attribution(market_dir)
    custom_bytes, _ = walk_with_attribution(custom_dir)
    total_bytes = market_bytes + custom_bytes

    def fmt(b: int) -> str:
        if b >= 1 << 30:
            return f"{b / (1 << 30):.1f} GB"
        if b >= 1 << 20:
            return f"{b / (1 << 20):.1f} MB"
        return f"{b / (1 << 10):.1f} KB"

    return {
        "market_data_path": os.path.abspath(market_dir),
        "custom_data_path": os.path.abspath(custom_dir),
        "total_bytes": total_bytes,
        "total_formatted": fmt(total_bytes),
        "market_bytes": market_bytes,
        "market_formatted": fmt(market_bytes),
        "custom_bytes": custom_bytes,
        "custom_formatted": fmt(custom_bytes),
        "by_provider": {
            k: {"bytes": v, "formatted": fmt(v)}
            for k, v in sorted(by_provider.items(), key=lambda x: -x[1])
        },
    }


@router.get("/storage-summary")
async def storage_summary():
    """Return data storage path and total disk usage."""
    return await asyncio.to_thread(_build_storage_summary_payload, get_data_service())


@router.get("/sources")
async def list_data_sources(
    type: Optional[str] = Query(None, description="Filter by source type, e.g. 'scraper'"),
    db: AsyncSession = Depends(get_db),
):
    """List DataSource rows (scraper outputs, custom datasets registered via the API)."""
    q = select(DataSource).order_by(DataSource.last_updated.desc().nullslast())
    if type:
        q = q.where(DataSource.type == type)
    rows = (await db.execute(q)).scalars().all()
    return [{
        "id": r.id,
        "type": r.type,
        "source": r.source,
        "name": r.name,
        "description": r.description,
        "file_path": r.file_path,
        "last_updated": to_iso_utc(r.last_updated),
        "metadata": r.metadata_,
    } for r in rows]


@router.post("/downloads", status_code=201)
async def create_download(body: DownloadRequest):
    mgr = get_download_manager()
    try:
        result = await mgr.create_download(
            symbols=body.symbols,
            date_range_start=body.date_range_start,
            date_range_end=body.date_range_end,
            provider=body.provider,
            data_type=body.data_type,
            timeframe=body.timeframe,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/downloads")
async def list_downloads(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
):
    mgr = get_download_manager()
    return await mgr.list_downloads(limit=limit, offset=offset, status=status)


@router.get("/downloads/{download_id}")
async def get_download(download_id: str):
    mgr = get_download_manager()
    dl = await mgr.get_download(download_id)
    if dl is None:
        raise HTTPException(status_code=404, detail="Download not found")
    return dl


ACTIVE_STATUSES = {"queued", "running"}


@router.delete("/downloads/{download_id}", status_code=204)
async def delete_download(download_id: str):
    mgr = get_download_manager()
    dl = await mgr.get_download(download_id)
    if dl is None:
        raise HTTPException(status_code=404, detail="Download not found")
    if dl["status"] in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete an active download (status={dl['status']}). Cancel it first."
        )
    deleted = await mgr.delete_download(download_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Download not found")


@router.delete("/downloads")
async def clear_downloads(status: Optional[str] = Query(None)):
    mgr = get_download_manager()
    statuses = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
    n = await mgr.clear_downloads(statuses=statuses)
    return {"deleted": n}


@router.post("/downloads/{download_id}/cancel")
async def cancel_download(download_id: str):
    mgr = get_download_manager()
    cancelled = await mgr.cancel_download(download_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Download not found or already completed")
    return {"status": "cancelled"}


@router.post("/downloads/{download_id}/retry")
async def retry_download(download_id: str):
    """Retry a failed download using ensure_coverage to skip already-fetched data."""
    from datetime import date as date_type
    from coordinator.services.coverage_utils import ensure_coverage

    mgr = get_download_manager()
    dl = await mgr.get_download(download_id)
    if dl is None:
        raise HTTPException(status_code=404, detail="Download not found")
    if dl["status"] not in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Download is {dl['status']}, not failed/cancelled")

    # Extract the original download parameters
    symbols = dl.get("symbols") or []
    provider = dl.get("provider", "polygon")
    timeframe = dl.get("timeframe", "1min")
    start = dl.get("date_range_start")  # may be string or date
    end = dl.get("date_range_end")

    if not symbols or not start or not end:
        raise HTTPException(status_code=422, detail="Original download is missing required fields for retry")

    # Parse dates
    if isinstance(start, str):
        start = date_type.fromisoformat(start[:10])
    elif hasattr(start, 'date'):
        start = start.date()
    if isinstance(end, str):
        end = date_type.fromisoformat(end[:10])
    elif hasattr(end, 'date'):
        end = end.date()

    # Use ensure_coverage for each symbol — skips what's already on disk
    coverage = get_coverage_index()
    if coverage is None:
        raise HTTPException(status_code=503, detail="Coverage index not initialized")

    new_download_ids = []
    skipped_symbols = []
    for symbol in symbols:
        dl_ids = await ensure_coverage(
            provider, symbol, start, end,
            mgr, coverage, timeframe=timeframe,
        )
        if dl_ids:
            new_download_ids.extend(dl_ids)
        else:
            skipped_symbols.append(symbol)

    return {
        "original_download_id": download_id,
        "new_download_ids": new_download_ids,
        "new_download_count": len(new_download_ids),
        "skipped_symbols": skipped_symbols,
        "skipped_count": len(skipped_symbols),
        "message": (
            f"Retried {len(symbols)} symbol(s): "
            f"{len(new_download_ids)} new download(s), "
            f"{len(skipped_symbols)} already complete"
        ),
    }


# ─── Options contract listing ────────────────────────────────────────────────

@router.get("/options/{underlying}/contracts")
async def list_option_contracts(
    underlying: str,
    provider: str = Query("polygon"),
):
    """List option contracts on disk for an underlying, grouped by expiration."""
    from coordinator.services.chain_builder import parse_occ_symbol
    import os
    svc = get_data_service()
    expirations = svc.list_option_expirations(provider, underlying)
    groups = []
    for exp in expirations:
        contracts = svc.list_option_contracts(provider, underlying, exp)
        children = []
        for sym in contracts:
            parsed = parse_occ_symbol(sym)
            if parsed:
                bar_count = 0
                path = svc.market_data_path(provider, sym, "1day")
                if os.path.exists(path):
                    import pyarrow.parquet as pq
                    bar_count = pq.read_metadata(path).num_rows
                children.append({
                    "symbol": sym,
                    "strike": parsed["strike"],
                    "option_type": parsed["option_type"],
                    "expiration": parsed["expiration"],
                    "bars": bar_count,
                })
        groups.append({
            "expiration": exp.isoformat(),
            "contracts": children,
            "count": len(children),
        })
    return {"underlying": underlying, "provider": provider, "expirations": groups}


# ─── Coverage endpoints ───────────────────────────────────────────────────────

@router.get("/coverage")
async def get_coverage():
    """Return coverage ranges for all assets on disk, grouped by provider."""
    svc = get_data_service()
    coverage = get_coverage_index()

    available = svc.list_available_market_data()

    # Deduplicate to one entry per (provider, symbol) — collect unique timeframes on disk.
    # Collapse OCC option contracts into a single grouped entry per underlying
    # to avoid sending 2000+ rows to the frontend.
    from coordinator.services.chain_builder import parse_occ_symbol

    seen: dict[str, dict] = {}
    options_groups: dict[str, dict] = {}  # "provider/underlying" -> summary

    for item in available:
        provider = item["provider"]
        symbol = item["symbol"]

        parsed = parse_occ_symbol(symbol)
        if parsed:
            group_key = f"{provider}/{parsed['underlying']}"
            if group_key not in options_groups:
                options_groups[group_key] = {
                    "provider": provider,
                    "symbol": parsed["underlying"],
                    "contracts": [],
                    "expirations": set(),
                }
            options_groups[group_key]["contracts"].append(symbol)
            options_groups[group_key]["expirations"].add(parsed["expiration"])
            continue

        key = f"{provider}/{symbol}"
        if key not in seen:
            ranges = coverage.get_ranges(provider, symbol) if coverage else []
            seen[key] = {
                "provider": provider,
                "symbol": symbol,
                "ranges": [{"start": str(s), "end": str(e)} for s, e in ranges],
                "timeframes_on_disk": [],
            }
        seen[key]["timeframes_on_disk"].append(item["timeframe"])

    for group_key, group in options_groups.items():
        exps = sorted(group["expirations"])
        seen[group_key + "/options"] = {
            "provider": group["provider"],
            "symbol": group["symbol"],
            "ranges": [{"start": exps[0], "end": exps[-1]}] if exps else [],
            "timeframes_on_disk": ["options"],
            "option_contracts": len(group["contracts"]),
            "option_expirations": len(exps),
        }

    # Group by provider
    grouped: dict[str, list] = {}
    for v in seen.values():
        grouped.setdefault(v["provider"], []).append(v)
    return {"providers": grouped}


class DeleteDatasetRequest(BaseModel):
    provider: str
    symbol: str
    timeframe: str


@router.post("/delete-datasets")
async def delete_datasets(body: list[DeleteDatasetRequest]):
    """Delete one or more market data parquet files."""
    svc = get_data_service()
    coverage = get_coverage_index()
    deleted = 0
    for item in body:
        if svc.delete_market_data(item.provider, item.symbol, item.timeframe):
            deleted += 1
            if coverage:
                coverage.invalidate(item.provider, item.symbol)
    return {"deleted": deleted}


class FillGapsRequest(BaseModel):
    provider: str
    symbol: str
    start: date
    end: date
    timeframe: str = "1min"


@router.post("/fill-gaps")
async def fill_gaps(body: FillGapsRequest):
    """Download only what's missing for a given asset + date range."""
    from coordinator.services.coverage_utils import ensure_coverage

    coverage = get_coverage_index()
    if coverage is None:
        raise HTTPException(status_code=503, detail="Coverage index not initialized")

    mgr = get_download_manager()
    dl_ids = await ensure_coverage(
        body.provider, body.symbol,
        body.start, body.end,
        mgr, coverage,
        timeframe=body.timeframe,
    )
    return {"download_ids": dl_ids, "gap_count": len(dl_ids)}
