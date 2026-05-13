import pytest
from starlette.testclient import TestClient

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
