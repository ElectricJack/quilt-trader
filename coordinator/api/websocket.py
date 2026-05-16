import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from coordinator.api.dependencies import get_container

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.dashboard_connections: list[WebSocket] = []
        self.worker_connections: dict[str, WebSocket] = {}
        self.subscriptions: dict[str, set[WebSocket]] = {}

    async def connect_dashboard(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.dashboard_connections.append(websocket)

    async def disconnect_dashboard(self, websocket: WebSocket) -> None:
        self.unsubscribe_all(websocket)
        if websocket in self.dashboard_connections:
            self.dashboard_connections.remove(websocket)

    def subscribe(self, ws: WebSocket, target: str) -> None:
        self.subscriptions.setdefault(target, set()).add(ws)

    def unsubscribe(self, ws: WebSocket, target: str) -> None:
        if target in self.subscriptions:
            self.subscriptions[target].discard(ws)
            if not self.subscriptions[target]:
                self.subscriptions.pop(target, None)

    def unsubscribe_all(self, ws: WebSocket) -> None:
        for target in list(self.subscriptions.keys()):
            self.unsubscribe(ws, target)

    async def broadcast_to_target(self, target: str, message: dict) -> None:
        for ws in list(self.subscriptions.get(target, ())):
            try:
                await ws.send_json(message)
            except Exception:
                self.unsubscribe(ws, target)

    async def accept_worker(self, websocket: WebSocket) -> None:
        # The worker_id is not known until the first heartbeat, so we
        # only accept here. `register_worker` adds it to the lookup map.
        await websocket.accept()

    def register_worker(self, worker_id: str, websocket: WebSocket) -> None:
        self.worker_connections[worker_id] = websocket

    def disconnect_worker_by_socket(self, websocket: WebSocket) -> None:
        for wid, ws in list(self.worker_connections.items()):
            if ws is websocket:
                self.worker_connections.pop(wid, None)

    async def broadcast_to_dashboards(self, message: dict) -> None:
        disconnected = []
        for ws in self.dashboard_connections:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            await self.disconnect_dashboard(ws)


manager = ConnectionManager()


async def handle_dashboard_message(websocket, data: dict) -> None:
    """Handle a single dashboard WebSocket message. Separated for testability."""
    from sqlalchemy import select
    from coordinator.database.models import AlgorithmInstance

    msg_type = data.get("type")

    if msg_type == "ping":
        await websocket.send_json({"type": "pong"})
        return

    if msg_type == "subscribe" and "target" in data:
        target = data.get("target")
        if target:
            manager.subscribe(websocket, target)
            await websocket.send_json({"type": "subscribed", "target": target})
        return

    if msg_type == "unsubscribe":
        target = data.get("target")
        if target:
            manager.unsubscribe(websocket, target)
            await websocket.send_json({"type": "unsubscribed", "target": target})
        return

    if msg_type == "subscribe":
        await websocket.send_json({"type": "subscribed", "events": data.get("events", [])})
        return

    if msg_type in ("start_instance", "stop_instance"):
        instance_id = data.get("instance_id")
        if not instance_id:
            await websocket.send_json({
                "type": "error",
                "related_to": msg_type,
                "error": "missing instance_id",
            })
            return

        try:
            container = get_container()
            async with container.session_factory() as session:
                result = await session.execute(
                    select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                )
                instance = result.scalar_one_or_none()
        except Exception:
            logger.exception("Failed to load instance %s for %s", instance_id, msg_type)
            await websocket.send_json({
                "type": "error",
                "related_to": msg_type,
                "instance_id": instance_id,
                "error": "database error",
            })
            return

        if instance is None:
            await websocket.send_json({
                "type": "error",
                "related_to": msg_type,
                "instance_id": instance_id,
                "error": "instance not found",
            })
            return

        worker_ws = manager.worker_connections.get(instance.worker_id)
        if worker_ws is None:
            await websocket.send_json({
                "type": "error",
                "related_to": msg_type,
                "instance_id": instance_id,
                "error": "worker offline",
            })
            return

        payload: dict = {"type": msg_type, "instance_id": instance_id}
        if msg_type == "start_instance":
            payload["config"] = instance.config_values or {}
            payload["persisted_state"] = instance.persisted_state
        try:
            await worker_ws.send_json(payload)
        except Exception:
            logger.exception("Failed to forward %s to worker %s", msg_type, instance.worker_id)
            await websocket.send_json({
                "type": "error",
                "related_to": msg_type,
                "instance_id": instance_id,
                "error": "failed to reach worker",
            })


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    await manager.connect_dashboard(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await handle_dashboard_message(websocket, data)
    except WebSocketDisconnect:
        await manager.disconnect_dashboard(websocket)


async def handle_worker_message(websocket: WebSocket, data: dict) -> None:
    """Handle a single worker WebSocket message. Separated for testability."""
    from sqlalchemy import select
    from coordinator.database.models import AlgorithmInstance, DecisionLog, Worker, Event

    msg_type = data.get("type")

    if msg_type == "ping":
        await websocket.send_json({"type": "pong"})

    elif msg_type == "heartbeat":
        worker_id = data.get("worker_id")
        await websocket.send_json({"type": "heartbeat_ack"})
        if not worker_id:
            return
        manager.register_worker(worker_id, websocket)
        try:
            container = get_container()
            async with container.session_factory() as session:
                result = await session.execute(select(Worker).where(Worker.id == worker_id))
                worker = result.scalar_one_or_none()
                if worker is None:
                    return
                prior_status = worker.status
                worker.last_heartbeat = datetime.now(timezone.utc)
                worker.status = "online"
                if data.get("tailscale_ip"):
                    worker.tailscale_ip = data["tailscale_ip"]
                await session.commit()

                if prior_status != "online":
                    await manager.broadcast_to_dashboards({
                        "type": "worker_connected",
                        "worker_id": worker.id,
                        "name": worker.name,
                        "tailscale_ip": worker.tailscale_ip,
                        "install_status": worker.install_status,
                    })
        except Exception:
            logger.exception("Failed to update heartbeat for worker %s", worker_id)

    elif msg_type == "state_checkpoint":
        instance_id = data.get("instance_id")
        state = data.get("state")
        if instance_id is not None:
            try:
                container = get_container()
                async with container.session_factory() as session:
                    result = await session.execute(
                        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                    )
                    instance = result.scalar_one_or_none()
                    if instance:
                        instance.persisted_state = state
                        await session.commit()
            except Exception:
                logger.exception("Failed to save state_checkpoint for instance %s", instance_id)

    elif msg_type == "decision_log":
        instance_id = data.get("instance_id")
        if instance_id is not None:
            try:
                container = get_container()
                async with container.session_factory() as session:
                    raw_ts = data.get("timestamp")
                    if isinstance(raw_ts, str):
                        ts = datetime.fromisoformat(raw_ts)
                    elif isinstance(raw_ts, (int, float)):
                        ts = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
                    else:
                        ts = datetime.now(timezone.utc)

                    log_entry = DecisionLog(
                        instance_id=instance_id,
                        timestamp=ts,
                        mode=data.get("mode", "live"),
                        tick_data=data.get("tick_data"),
                        signals_produced=data.get("signals_produced"),
                        reasoning=data.get("reasoning"),
                        data_sources_used=data.get("data_sources_used"),
                    )
                    session.add(log_entry)
                    await session.commit()
            except Exception:
                logger.exception("Failed to store decision_log for instance %s", instance_id)

    elif msg_type == "signal_request":
        instance_id = data.get("instance_id")
        signal = data.get("signal")
        # Auto-approve for now
        await websocket.send_json({
            "type": "signal_response",
            "approved": True,
            "instance_id": instance_id,
            "signal": signal,
        })

    elif msg_type == "instance_started":
        instance_id = data.get("instance_id")
        if instance_id is not None:
            try:
                from coordinator.database.models import AlgorithmRun
                container = get_container()
                async with container.session_factory() as session:
                    result = await session.execute(
                        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                    )
                    instance = result.scalar_one_or_none()
                    if instance:
                        instance.status = "running"
                        await session.commit()
                        await manager.broadcast_to_dashboards({
                            "type": "deployment_status_changed",
                            "deployment_id": instance_id,
                            "status": "running",
                            "active_run_id": instance.active_run_id,
                        })
            except Exception:
                logger.exception("Failed to update instance_started for instance %s", instance_id)

    elif msg_type == "instance_stopped":
        instance_id = data.get("instance_id")
        if instance_id is not None:
            try:
                from coordinator.database.models import AlgorithmRun
                container = get_container()
                async with container.session_factory() as session:
                    result = await session.execute(
                        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                    )
                    instance = result.scalar_one_or_none()
                    if instance:
                        instance.status = "stopped"
                        if instance.active_run_id is not None:
                            run_result = await session.execute(
                                select(AlgorithmRun).where(AlgorithmRun.id == instance.active_run_id)
                            )
                            run = run_result.scalar_one_or_none()
                            if run:
                                run.status = "stopped"
                                run.stopped_at = datetime.now(timezone.utc)
                        instance.active_run_id = None
                        await session.commit()
                        await manager.broadcast_to_dashboards({
                            "type": "deployment_status_changed",
                            "deployment_id": instance_id,
                            "status": "stopped",
                            "active_run_id": None,
                        })
            except Exception:
                logger.exception("Failed to update instance_stopped for instance %s", instance_id)

    elif msg_type == "instance_error":
        instance_id = data.get("instance_id")
        if instance_id is not None:
            try:
                from coordinator.database.models import AlgorithmRun
                container = get_container()
                async with container.session_factory() as session:
                    result = await session.execute(
                        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                    )
                    instance = result.scalar_one_or_none()
                    if instance:
                        instance.status = "error"
                        if instance.active_run_id is not None:
                            run_result = await session.execute(
                                select(AlgorithmRun).where(AlgorithmRun.id == instance.active_run_id)
                            )
                            run = run_result.scalar_one_or_none()
                            if run:
                                run.status = "error"
                                run.stopped_at = datetime.now(timezone.utc)
                        await session.commit()
                        await manager.broadcast_to_dashboards({
                            "type": "deployment_status_changed",
                            "deployment_id": instance_id,
                            "status": "error",
                            "active_run_id": instance.active_run_id,
                        })
            except Exception:
                logger.exception("Failed to update instance_error for instance %s", instance_id)

    elif msg_type in ("equity_sample", "trade_sample"):
        container = get_container()
        sink = getattr(container, "live_sample_sink", None)
        if sink is None:
            return
        dep_id = data.get("instance_id")
        run_id = data.get("run_id")
        if not dep_id or not run_id:
            return
        try:
            if msg_type == "equity_sample":
                await sink.add_equity_sample(dep_id, run_id, {
                    "timestamp": data.get("timestamp"),
                    "portfolio_value": data.get("portfolio_value"),
                    "cash": data.get("cash", 0.0),
                })
            else:
                await sink.add_trade_sample(dep_id, run_id, data)
        except Exception:
            logger.exception("Failed to route %s for deployment %s run %s", msg_type, dep_id, run_id)

    elif msg_type in ("activity_event", "algo_log"):
        from coordinator.database.models import WorkerActivity
        worker_id = data.get("worker_id")
        instance_id = data.get("instance_id")
        kind = "event" if msg_type == "activity_event" else "log"
        severity = data.get("severity", "info").lower()
        if kind == "log":
            level = (data.get("level") or "INFO").upper()
            severity = {"DEBUG": "debug", "INFO": "info", "WARNING": "warn",
                        "ERROR": "error", "CRITICAL": "error"}.get(level, "info")
        raw_ts = data.get("timestamp")
        try:
            ts = (
                datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if isinstance(raw_ts, str)
                else datetime.now(timezone.utc)
            )
        except Exception:
            ts = datetime.now(timezone.utc)
        try:
            container = get_container()
            async with container.session_factory() as session:
                row = WorkerActivity(
                    worker_id=worker_id,
                    instance_id=instance_id,
                    timestamp=ts,
                    kind=kind,
                    severity=severity,
                    event_type=data.get("event_type") if kind == "event" else None,
                    logger_name=data.get("logger_name") if kind == "log" else None,
                    message=data.get("message"),
                    payload=data.get("payload") if kind == "event" else None,
                )
                session.add(row)
                await session.commit()
        except Exception:
            logger.exception("Failed to persist worker_activity for worker %s", worker_id)

        broadcast_msg = {
            "type": msg_type,
            "worker_id": worker_id,
            "instance_id": instance_id,
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "severity": severity,
        }
        if kind == "event":
            broadcast_msg["event_type"] = data.get("event_type")
            broadcast_msg["payload"] = data.get("payload")
        else:
            broadcast_msg["logger_name"] = data.get("logger_name")
            broadcast_msg["level"] = data.get("level")
            broadcast_msg["message"] = data.get("message")

        if worker_id:
            await manager.broadcast_to_target(f"worker:{worker_id}", broadcast_msg)
        if instance_id:
            await manager.broadcast_to_target(f"deployment:{instance_id}", broadcast_msg)


async def handle_worker_disconnect(websocket: WebSocket) -> None:
    """Mark a worker offline when its websocket disconnects and broadcast."""
    from sqlalchemy import select
    from coordinator.database.models import Worker
    # Find the worker id from the connection map *before* removing
    worker_id = None
    for wid, ws in list(manager.worker_connections.items()):
        if ws is websocket:
            worker_id = wid
            break
    manager.disconnect_worker_by_socket(websocket)
    if worker_id is None:
        return
    try:
        container = get_container()
        async with container.session_factory() as session:
            worker = (await session.execute(
                select(Worker).where(Worker.id == worker_id)
            )).scalar_one_or_none()
            if worker is not None and worker.status != "offline":
                worker.status = "offline"
                await session.commit()
                await manager.broadcast_to_dashboards({
                    "type": "worker_disconnected",
                    "worker_id": worker_id,
                })
    except Exception:
        logger.exception("Failed to mark worker %s offline on disconnect", worker_id)


@router.websocket("/ws/worker")
async def worker_websocket(websocket: WebSocket):
    await manager.accept_worker(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await handle_worker_message(websocket, data)
    except WebSocketDisconnect:
        logger.info("Worker disconnected")
    finally:
        await handle_worker_disconnect(websocket)
