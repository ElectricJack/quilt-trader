import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

from worker.runner import AlgorithmRunner

logger = logging.getLogger(__name__)
EventHandler = Callable[[dict], Coroutine[Any, Any, None]]


class MessageRouter:
    def __init__(self) -> None:
        self._handlers: dict[str, EventHandler] = {}

    def register(self, message_type: str, handler: EventHandler) -> None:
        self._handlers[message_type] = handler

    async def dispatch(self, message: dict) -> None:
        msg_type = message.get("type")
        handler = self._handlers.get(msg_type)
        if handler:
            await handler(message)
        else:
            logger.debug("No handler for message type: %s", msg_type)


class WorkerAgent:
    def __init__(self, worker_name: str, websocket: Any) -> None:
        self.worker_name = worker_name
        self._ws = websocket
        self.router = MessageRouter()
        self._running_instances: dict[str, Any] = {}
        self.register_handlers()

    async def _send(self, data: dict) -> None:
        await self._ws.send(json.dumps(data))

    async def _recv(self) -> dict:
        raw = await self._ws.recv()
        return json.loads(raw)

    async def send_heartbeat(self) -> None:
        await self._send({"type": "heartbeat", "worker_name": self.worker_name,
                         "timestamp": datetime.now(timezone.utc).isoformat()})

    async def send_event(self, event_type: str, instance_id: str, payload: Optional[dict] = None) -> None:
        await self._send({"type": event_type, "instance_id": instance_id, "payload": payload or {},
                         "timestamp": datetime.now(timezone.utc).isoformat()})

    async def request_signal_approval(self, instance_id: str, signal: dict) -> dict:
        await self._send({"type": "signal_request", "instance_id": instance_id, "signal": signal,
                         "timestamp": datetime.now(timezone.utc).isoformat()})
        return await self._recv()

    async def send_state_checkpoint(self, instance_id: str, state: dict) -> None:
        await self._send({"type": "state_checkpoint", "instance_id": instance_id, "state": state,
                         "timestamp": datetime.now(timezone.utc).isoformat()})

    async def send_decision_log(self, instance_id: str, log_entry: dict) -> None:
        await self._send({"type": "decision_log", "instance_id": instance_id, "log_entry": log_entry,
                         "timestamp": datetime.now(timezone.utc).isoformat()})

    def register_handlers(self) -> None:
        self.router.register("start_instance", self._handle_start_instance)
        self.router.register("stop_instance", self._handle_stop_instance)
        self.router.register("heartbeat_ack", self._handle_heartbeat_ack)

    async def _handle_start_instance(self, message: dict) -> None:
        instance_id = message["instance_id"]
        config = message.get("config", {})
        persisted_state = message.get("persisted_state")
        self._running_instances[instance_id] = {
            "status": "starting",
            "config": config,
            "persisted_state": persisted_state,
        }
        await self.send_event("instance_started", instance_id)
        logger.info("Started instance %s", instance_id)

    async def _handle_stop_instance(self, message: dict) -> None:
        instance_id = message["instance_id"]
        instance_info = self._running_instances.pop(instance_id, None)
        if instance_info and isinstance(instance_info.get("runner"), AlgorithmRunner):
            final_state = instance_info["runner"].stop()
            await self.send_state_checkpoint(instance_id, final_state)
        await self.send_event("instance_stopped", instance_id)
        logger.info("Stopped instance %s", instance_id)

    async def _handle_heartbeat_ack(self, message: dict) -> None:
        pass  # No action needed
