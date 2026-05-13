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
    keys = ["github_pat", "discord_bot_token", "polygon_api_key", "theta_data_username"]
    result = {}
    for key in keys:
        val = await _get_setting(db, key)
        result[f"{key}_set"] = val is not None
    result["theta_data_set"] = result.pop("theta_data_username_set")
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
