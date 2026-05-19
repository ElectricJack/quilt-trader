import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

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
    def __init__(self, worker_id: str, worker_name: str, websocket: Any,
                 tailscale_ip: Optional[str] = None,
                 coordinator_http_url: str = "",
                 worker_install_token: str = "",
                 data_client: Any = None) -> None:
        self.worker_id = worker_id
        self.worker_name = worker_name
        self.tailscale_ip = tailscale_ip
        self.coordinator_http_url = coordinator_http_url
        self.worker_install_token = worker_install_token
        self._ws = websocket
        self._data_client = data_client
        self.router = MessageRouter()
        self._running_instances: dict[str, Any] = {}
        self.register_handlers()

    async def _send(self, data: dict) -> None:
        await self._ws.send(json.dumps(data))

    async def _recv(self) -> dict:
        raw = await self._ws.recv()
        return json.loads(raw)

    @staticmethod
    def _get_git_sha() -> str | None:
        import subprocess, os
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            r = subprocess.run(
                ["git", "-C", repo_root, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    async def send_heartbeat(self) -> None:
        payload: dict = {
            "type": "heartbeat",
            "worker_id": self.worker_id,
            "worker_name": self.worker_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": self._get_git_sha(),
        }
        if self.tailscale_ip:
            payload["tailscale_ip"] = self.tailscale_ip
        await self._send(payload)

    async def send_event(self, event_type: str, instance_id: str, payload: Optional[dict] = None) -> None:
        await self._send({"type": event_type, "instance_id": instance_id, "payload": payload or {},
                         "timestamp": datetime.now(timezone.utc).isoformat()})

    async def send_activity_event(
        self, instance_id: Optional[str], event_type: str,
        severity: str = "info", payload: Optional[dict] = None,
    ) -> None:
        await self._send({
            "type": "activity_event",
            "worker_id": self.worker_id,
            "instance_id": instance_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "severity": severity,
            "payload": payload or {},
        })

    async def send_algo_log(
        self, instance_id: str, logger_name: str, level: str, message: str,
    ) -> None:
        await self._send({
            "type": "algo_log",
            "worker_id": self.worker_id,
            "instance_id": instance_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger_name": logger_name,
            "level": level,
            "message": message,
        })

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
        self.router.register("tick_batch", self._handle_tick_batch)
        self.router.register("update_worker", self._handle_update_worker)

    async def _handle_start_instance(self, message: dict) -> None:
        from worker.live_instance_runtime import LiveInstanceRuntime

        instance_id = message["instance_id"]
        existing = self._running_instances.get(instance_id)
        if existing is not None and getattr(existing, "is_healthy", lambda: False)():
            logger.info("Ignoring duplicate start_instance for %s (already healthy)", instance_id)
            return
        if existing is not None:
            try:
                await existing.shut_down()
            except Exception:
                logger.exception("Failed to shut down zombie runtime for %s", instance_id)
            self._running_instances.pop(instance_id, None)

        try:
            runtime = await LiveInstanceRuntime.bring_up(
                agent=self,
                instance_id=instance_id,
                run_id=message["run_id"],
                algorithm_id=message["algorithm_id"],
                algorithm_commit_sha=message["algorithm_commit_sha"],
                manifest=message["manifest"],
                config=message.get("config") or {},
                persisted_state=message.get("persisted_state"),
                broker_type=message["broker_type"],
                environment=message["environment"],
                credentials=message["credentials"],
                data_client=self._data_client,
            )
        except Exception as e:
            logger.exception("Failed to bring up instance %s", instance_id)
            await self.send_event("instance_error", instance_id, payload={"error": str(e)})
            await self.send_activity_event(
                instance_id, "instance_error", severity="error",
                payload={"error": str(e)},
            )
            return

        self._running_instances[instance_id] = runtime
        await self.send_event("instance_started", instance_id)
        await self.send_activity_event(instance_id, "instance_started", severity="info")
        logger.info("Started instance %s", instance_id)

    async def _handle_stop_instance(self, message: dict) -> None:
        instance_id = message["instance_id"]
        runtime = self._running_instances.pop(instance_id, None)
        if runtime is not None:
            try:
                final_state = await runtime.shut_down()
                await self.send_state_checkpoint(instance_id, final_state)
            except Exception:
                logger.exception("Error shutting down instance %s", instance_id)
        await self.send_event("instance_stopped", instance_id)
        await self.send_activity_event(instance_id, "instance_stopped", severity="info")
        logger.info("Stopped instance %s", instance_id)

    async def _handle_heartbeat_ack(self, message: dict) -> None:
        pass  # No action needed

    async def _handle_tick_batch(self, message: dict) -> None:
        for entry in (message.get("ticks") or []):
            inst_id = entry.get("instance_id")
            runtime = self._running_instances.get(inst_id)
            if runtime is None:
                logger.debug("tick_batch entry for unknown instance %s; ignoring", inst_id)
                continue
            # Per-instance task: a slow algorithm doesn't block sibling instances.
            asyncio.create_task(runtime.on_tick_batch_entry(entry))

    async def _handle_update_worker(self, _message: dict) -> None:
        """Pull latest code from git, reinstall deps, then exit for systemd restart."""
        logger.info("Received update_worker command — pulling latest code")

        # worker/ is a subdirectory of the repo root
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        try:
            # git pull
            result = subprocess.run(
                ["git", "-C", repo_root, "pull", "origin", "main"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or result.stdout.strip()
                logger.error("git pull failed: %s", error)
                await self.send_event("update_complete", "", payload={
                    "success": False, "error": f"git pull failed: {error}",
                })
                return

            # Get new SHA
            sha_result = subprocess.run(
                ["git", "-C", repo_root, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True,
            )
            new_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else "unknown"

            # pip install requirements if they exist
            req_file = os.path.join(repo_root, "requirements.txt")
            if os.path.exists(req_file):
                pip_result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"],
                    capture_output=True, text=True, timeout=120,
                )
                if pip_result.returncode != 0:
                    logger.warning("pip install failed: %s", pip_result.stderr)

            logger.info("Update complete — new SHA: %s. Exiting for systemd restart.", new_sha)

            # Send success event before exiting
            await self.send_event("update_complete", "", payload={
                "success": True, "new_sha": new_sha,
            })

            # Brief delay so the WS message has time to send
            await asyncio.sleep(0.5)

            # Exit cleanly — systemd Restart=always will restart the process
            os._exit(0)

        except subprocess.TimeoutExpired:
            logger.error("Update timed out")
            await self.send_event("update_complete", "", payload={
                "success": False, "error": "Update timed out",
            })
        except Exception as e:
            logger.exception("Update failed")
            await self.send_event("update_complete", "", payload={
                "success": False, "error": str(e),
            })
