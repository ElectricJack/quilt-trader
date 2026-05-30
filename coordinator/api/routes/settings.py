from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from coordinator.api.dependencies import get_db, get_container
from coordinator.database.models import Setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SingleValueBody(BaseModel):
    value: str


class ThetaDataBody(BaseModel):
    username: str
    password: str


async def _get_setting(db: AsyncSession, key: str) -> Optional[str]:
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        db.add(Setting(key=key, value=value))


async def _delete_setting(db: AsyncSession, key: str) -> None:
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        await db.delete(setting)


async def _settings_status(db: AsyncSession) -> dict:
    keys = ["github_pat", "discord_bot_token", "polygon_api_key", "theta_data_username",
            "tailscale_authkey", "fmp_api_key"]
    result = {}
    for key in keys:
        val = await _get_setting(db, key)
        result[f"{key}_set"] = val is not None
    result["theta_data_set"] = result.pop("theta_data_username_set")
    # Visible (non-secret) settings: return the actual value.
    result["coordinator_ip"] = await _get_setting(db, "coordinator_ip")
    # Polygon tier/concurrency overrides — visible plaintext. Defaults match
    # the free-tier rate limits applied in coordinator/main.py.
    result["polygon_min_request_interval_s"] = await _get_setting(db, "polygon_min_request_interval_s")
    result["polygon_concurrency"] = await _get_setting(db, "polygon_concurrency")
    # FMP tier overrides — visible plaintext. Defaults match free tier
    # (250 calls/day, no inter-request pacing) applied in coordinator/main.py.
    result["fmp_daily_quota_limit"] = await _get_setting(db, "fmp_daily_quota_limit")
    result["fmp_min_request_interval_s"] = await _get_setting(db, "fmp_min_request_interval_s")
    result["dataset_quota_reset_tz"] = await _get_setting(db, "dataset_quota_reset_tz")
    return result


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    return await _settings_status(db)


@router.put("/github-pat")
async def set_github_pat(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted = container.encryption.encrypt(body.value)
    await _set_setting(db, "github_pat", encrypted)
    return await _settings_status(db)


@router.delete("/github-pat")
async def delete_github_pat(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "github_pat")
    return await _settings_status(db)


@router.put("/discord-token")
async def set_discord_token(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted = container.encryption.encrypt(body.value)
    await _set_setting(db, "discord_bot_token", encrypted)
    return await _settings_status(db)


@router.put("/polygon-key")
async def set_polygon_key(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted = container.encryption.encrypt(body.value)
    await _set_setting(db, "polygon_api_key", encrypted)
    return await _settings_status(db)


@router.put("/theta-data")
async def set_theta_data(body: ThetaDataBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    await _set_setting(db, "theta_data_username", container.encryption.encrypt(body.username))
    await _set_setting(db, "theta_data_password", container.encryption.encrypt(body.password))
    return await _settings_status(db)


@router.delete("/discord-token")
async def delete_discord_token(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "discord_bot_token")
    return await _settings_status(db)

@router.delete("/polygon-key")
async def delete_polygon_key(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "polygon_api_key")
    return await _settings_status(db)


class PolygonTierBody(BaseModel):
    """Polygon paid-tier overrides. Free tier is 5 calls/min so the default
    in coordinator/main.py is 13s interval + 1 concurrency. Stocks Starter
    is unlimited per-min with a per-second cap (10–100 depending on plan).
    """
    min_request_interval_s: Optional[float] = None
    concurrency: Optional[int] = None


@router.put("/polygon-tier")
async def set_polygon_tier(body: PolygonTierBody, db: AsyncSession = Depends(get_db)):
    """Set polygon rate-limit overrides. Either field can be omitted to clear
    it; both stored plaintext (non-secret). Restart the coordinator to apply."""
    if body.min_request_interval_s is not None:
        if body.min_request_interval_s < 0:
            from fastapi import HTTPException
            raise HTTPException(400, "min_request_interval_s must be >= 0")
        await _set_setting(db, "polygon_min_request_interval_s", str(body.min_request_interval_s))
    else:
        await _delete_setting(db, "polygon_min_request_interval_s")
    if body.concurrency is not None:
        if body.concurrency < 1:
            from fastapi import HTTPException
            raise HTTPException(400, "concurrency must be >= 1")
        await _set_setting(db, "polygon_concurrency", str(body.concurrency))
    else:
        await _delete_setting(db, "polygon_concurrency")
    return await _settings_status(db)


@router.delete("/polygon-tier")
async def delete_polygon_tier(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "polygon_min_request_interval_s")
    await _delete_setting(db, "polygon_concurrency")
    return await _settings_status(db)

@router.delete("/theta-data")
async def delete_theta_data(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "theta_data_username")
    await _delete_setting(db, "theta_data_password")
    return await _settings_status(db)


@router.put("/coordinator-ip")
async def set_coordinator_ip(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    # Stored plaintext — this is the coordinator's Tailscale IP that the Pi uses to reach it.
    # The full URL is constructed at use-time (http://<ip>:8000).
    await _set_setting(db, "coordinator_ip", body.value.strip())
    return await _settings_status(db)


@router.delete("/coordinator-ip")
async def delete_coordinator_ip(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "coordinator_ip")
    return await _settings_status(db)


@router.put("/tailscale-authkey")
async def set_tailscale_authkey(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted = container.encryption.encrypt(body.value)
    await _set_setting(db, "tailscale_authkey", encrypted)
    return await _settings_status(db)


@router.delete("/tailscale-authkey")
async def delete_tailscale_authkey(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "tailscale_authkey")
    return await _settings_status(db)


@router.put("/fmp-key")
async def set_fmp_key(body: SingleValueBody, db: AsyncSession = Depends(get_db)):
    container = get_container()
    encrypted = container.encryption.encrypt(body.value)
    await _set_setting(db, "fmp_api_key", encrypted)
    return await _settings_status(db)


@router.delete("/fmp-key")
async def delete_fmp_key(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "fmp_api_key")
    return await _settings_status(db)


class FMPTierBody(BaseModel):
    """FMP daily quota + pacing overrides. Free tier is 250 calls/day with no
    documented per-second cap. Paid tiers raise the daily limit; some plans
    may want a small inter-request pacing floor."""
    daily_quota_limit: Optional[int] = None
    min_request_interval_s: Optional[float] = None
    quota_reset_tz: Optional[str] = None


@router.put("/fmp-tier")
async def set_fmp_tier(body: FMPTierBody, db: AsyncSession = Depends(get_db)):
    """Set FMP rate-limit overrides. Each field can be omitted to clear it;
    all stored plaintext (non-secret). Restart the coordinator to apply."""
    if body.daily_quota_limit is not None:
        if body.daily_quota_limit < 1:
            from fastapi import HTTPException
            raise HTTPException(400, "daily_quota_limit must be >= 1")
        await _set_setting(db, "fmp_daily_quota_limit", str(body.daily_quota_limit))
    else:
        await _delete_setting(db, "fmp_daily_quota_limit")
    if body.min_request_interval_s is not None:
        if body.min_request_interval_s < 0:
            from fastapi import HTTPException
            raise HTTPException(400, "min_request_interval_s must be >= 0")
        await _set_setting(db, "fmp_min_request_interval_s", str(body.min_request_interval_s))
    else:
        await _delete_setting(db, "fmp_min_request_interval_s")
    if body.quota_reset_tz is not None:
        # Validate tz string by attempting to construct ZoneInfo
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(body.quota_reset_tz)
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(400, f"invalid quota_reset_tz: {e}")
        await _set_setting(db, "dataset_quota_reset_tz", body.quota_reset_tz)
    else:
        await _delete_setting(db, "dataset_quota_reset_tz")
    return await _settings_status(db)


@router.delete("/fmp-tier")
async def delete_fmp_tier(db: AsyncSession = Depends(get_db)):
    await _delete_setting(db, "fmp_daily_quota_limit")
    await _delete_setting(db, "fmp_min_request_interval_s")
    await _delete_setting(db, "dataset_quota_reset_tz")
    return await _settings_status(db)
