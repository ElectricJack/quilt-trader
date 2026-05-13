from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
from coordinator.database.models import Event

router = APIRouter(prefix="/api/events", tags=["events"])


class EventCreate(BaseModel):
    source_type: str
    source_id: Optional[str] = None
    event_type: str
    severity: str = "info"
    payload: Optional[dict] = None


def _to_response(event: Event) -> dict:
    return {
        "id": event.id,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "event_type": event.event_type,
        "severity": event.severity,
        "payload": event.payload,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "routed_to_discord": event.routed_to_discord,
        "discord_channel": event.discord_channel,
    }


@router.post("", status_code=201)
async def create_event(body: EventCreate, db: AsyncSession = Depends(get_db)):
    event = Event(
        source_type=body.source_type,
        source_id=body.source_id,
        event_type=body.event_type,
        severity=body.severity,
        payload=body.payload,
    )
    db.add(event)
    await db.flush()
    return _to_response(event)


@router.get("")
async def list_events(
    event_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Event)
    count_query = select(func.count(Event.id))

    if event_type:
        query = query.where(Event.event_type == event_type)
        count_query = count_query.where(Event.event_type == event_type)
    if severity:
        query = query.where(Event.severity == severity)
        count_query = count_query.where(Event.severity == severity)
    if source_type:
        query = query.where(Event.source_type == source_type)
        count_query = count_query.where(Event.source_type == source_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(desc(Event.timestamp)).offset(offset).limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()

    return {
        "items": [_to_response(e) for e in events],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
