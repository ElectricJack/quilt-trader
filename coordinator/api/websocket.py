import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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


@router.websocket("/ws/worker")
async def worker_websocket(websocket: WebSocket):
    await manager.connect_worker(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "heartbeat":
                await websocket.send_json({"type": "heartbeat_ack"})
    except WebSocketDisconnect:
        logger.info("Worker disconnected")
