"""Websocket tail helper for `quilt <thing> --follow` commands.

Connects to `/ws/dashboard`, subscribes to a target (e.g. `deployment:<id>`),
renders incoming activity_event and algo_log messages, reconnects with
exponential backoff if the connection drops.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from typing import Optional

import httpx
import websockets

from sdk.cli.output import print_status


SEVERITY_ORDER = {"debug": 0, "info": 1, "warn": 2, "error": 3}


async def follow_target(
    ws_url: str,
    target: str,
    *,
    severity_floor: str = "info",
    kind: str = "all",
    json_mode: bool = False,
    print_history: bool = True,
    history_url: Optional[str] = None,
) -> int:
    """Open ws to `ws_url`, subscribe to `target`, render incoming events.

    Returns exit code: 0 on Ctrl+C, 3 if reconnect fails persistently.
    """
    floor = SEVERITY_ORDER.get(severity_floor, 1)

    if print_history and history_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(history_url)
                if 200 <= r.status_code < 300:
                    items = r.json().get("items", [])
                    for row in reversed(items):
                        _render(row, json_mode)
        except Exception:
            pass

    backoff = 1.0
    consecutive_failures = 0
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                await ws.send(json.dumps({"type": "subscribe", "target": target}))
                consecutive_failures = 0
                backoff = 1.0
                while True:
                    msg = await ws.recv()
                    try:
                        data = json.loads(msg)
                    except Exception:
                        continue
                    msg_type = data.get("type")
                    if msg_type not in ("activity_event", "algo_log"):
                        continue
                    sev = data.get("severity", "info")
                    if SEVERITY_ORDER.get(sev, 1) < floor:
                        continue
                    if kind != "all":
                        msg_kind = "event" if msg_type == "activity_event" else "log"
                        if msg_kind != kind:
                            continue
                    _render(data, json_mode)
        except (asyncio.CancelledError, KeyboardInterrupt):
            return 0
        except Exception as e:
            consecutive_failures += 1
            if consecutive_failures > 50:
                print_status(f"reconnect failed after {consecutive_failures} attempts")
                return 3
            print_status(f"connection lost — reconnecting in {backoff:.1f}s ({e})")
            try:
                await asyncio.sleep(backoff)
            except (asyncio.CancelledError, KeyboardInterrupt):
                return 0
            backoff = min(backoff * 2, 30.0)


def _summarize_payload(payload) -> str:
    """Turn an activity_event payload into one human-readable line.

    Rules:
      - If `error` is present, that's the most important field — show it (first line).
      - Otherwise concatenate key=value pairs (truncating long values).
      - None / non-dict → empty string.
    """
    if not isinstance(payload, dict):
        return ""
    if "error" in payload:
        err = str(payload["error"]).strip().splitlines()[0]
        return err[:200]
    parts = []
    for k, v in payload.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return " ".join(parts)[:200]


def _render(row: dict, json_mode: bool) -> None:
    if json_mode:
        sys.stdout.write(json.dumps(row, default=str))
        sys.stdout.write("\n")
        sys.stdout.flush()
        return
    ts = row.get("timestamp")
    try:
        ts_short = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%H:%M:%S")
    except Exception:
        ts_short = str(ts) if ts else "?"
    sev = row.get("severity", "info")
    kind_label = row.get("event_type") or row.get("logger_name") or "?"
    inst_id = (row.get("instance_id") or "")[:8]
    msg = row.get("message") or _summarize_payload(row.get("payload"))
    line = f"[{ts_short}] {sev:<5}  {kind_label:<24}  {inst_id:<8}  {msg}"
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
