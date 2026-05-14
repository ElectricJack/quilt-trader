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

    async def connect_dashboard(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.dashboard_connections.append(websocket)

    async def disconnect_dashboard(self, websocket: WebSocket) -> None:
        if websocket in self.dashboard_connections:
            self.dashboard_connections.remove(websocket)

    async def connect_worker(self, websocket: WebSocket, worker_id: str = "unknown") -> None:
        await websocket.accept()
        self.worker_connections[worker_id] = websocket

    async def disconnect_worker(self, worker_id: str) -> None:
        self.worker_connections.pop(worker_id, None)

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


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    await manager.connect_dashboard(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "subscribe":
                await websocket.send_json({"type": "subscribed", "events": data.get("events", [])})
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
                container = get_container()
                async with container.session_factory() as session:
                    result = await session.execute(
                        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                    )
                    instance = result.scalar_one_or_none()
                    if instance:
                        instance.status = "running"
                        await session.commit()
            except Exception:
                logger.exception("Failed to update instance_started for instance %s", instance_id)

    elif msg_type == "instance_stopped":
        instance_id = data.get("instance_id")
        if instance_id is not None:
            try:
                container = get_container()
                async with container.session_factory() as session:
                    result = await session.execute(
                        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                    )
                    instance = result.scalar_one_or_none()
                    if instance:
                        instance.status = "stopped"
                        await session.commit()
            except Exception:
                logger.exception("Failed to update instance_stopped for instance %s", instance_id)

    elif msg_type == "instance_error":
        instance_id = data.get("instance_id")
        if instance_id is not None:
            try:
                container = get_container()
                async with container.session_factory() as session:
                    result = await session.execute(
                        select(AlgorithmInstance).where(AlgorithmInstance.id == instance_id)
                    )
                    instance = result.scalar_one_or_none()
                    if instance:
                        instance.status = "error"
                        await session.commit()
            except Exception:
                logger.exception("Failed to update instance_error for instance %s", instance_id)


@router.websocket("/ws/worker")
async def worker_websocket(websocket: WebSocket):
    await manager.connect_worker(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await handle_worker_message(websocket, data)
    except WebSocketDisconnect:
        logger.info("Worker disconnected")
