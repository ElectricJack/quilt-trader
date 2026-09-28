import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from coordinator.main import create_app


def test_dashboard_websocket_connects():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    client = TestClient(app)
    with client.websocket_connect("/ws/dashboard") as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data["type"] == "pong"


def test_worker_websocket_connects():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    client = TestClient(app)
    with client.websocket_connect("/ws/worker") as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data["type"] == "pong"


def test_dashboard_websocket_receives_events():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    client = TestClient(app)
    with client.websocket_connect("/ws/dashboard") as ws:
        ws.send_json({"type": "subscribe", "events": ["trade_executed"]})
        data = ws.receive_json()
        assert data["type"] == "subscribed"


@pytest.mark.asyncio
async def test_worker_websocket_stops_receiving_after_handled_send_disconnect(monkeypatch):
    from coordinator.api import websocket as websocket_module

    received = 0
    cleaned_up = []

    async def receive():
        nonlocal received
        received += 1
        if received == 1:
            return {"type": "websocket.connect"}
        if received == 2:
            return {"type": "websocket.receive", "text": '{"type":"heartbeat"}'}
        raise AssertionError("received again after the socket disconnected")

    async def send(message):
        if message["type"] == "websocket.send":
            raise OSError("connection closed")

    websocket = WebSocket({"type": "websocket"}, receive, send)

    async def handle_message(ws, data):
        assert data == {"type": "heartbeat"}
        # A failed send in reconciliation is caught by the handler, but
        # Starlette has already marked the application side disconnected.
        try:
            await ws.send_json({"type": "start_instance"})
        except WebSocketDisconnect:
            pass

    async def disconnect(ws):
        cleaned_up.append(ws)

    monkeypatch.setattr(websocket_module, "handle_worker_message", handle_message)
    monkeypatch.setattr(websocket_module, "handle_worker_disconnect", disconnect)

    await websocket_module.worker_websocket(websocket)

    assert websocket.application_state == WebSocketState.DISCONNECTED
    assert received == 2
    assert cleaned_up == [websocket]
