"""Small helper for stopping periodic services between ticks."""

import asyncio


async def wait_for_next_tick(interval_seconds: float, stop_event: asyncio.Event | None) -> None:
    if stop_event is None:
        await asyncio.sleep(interval_seconds)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
    except TimeoutError:
        pass
