import argparse
import asyncio
import logging
import subprocess
import sys
from typing import Optional

from worker.config import WorkerConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _discover_tailscale_ip() -> Optional[str]:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return None


async def run_worker(config: WorkerConfig) -> None:
    import websockets
    from worker.agent import WorkerAgent
    from worker.data_client import DataClient

    tailscale_ip = _discover_tailscale_ip()
    logger.info("Starting worker '%s' (id=%s, ts_ip=%s), connecting to %s",
                config.worker_name, config.worker_id, tailscale_ip, config.coordinator_url)
    data_client = DataClient(base_url=config.coordinator_http_url, cache_ttl=config.data_cache_ttl)
    ws_url = f"{config.coordinator_url}/ws/worker"

    async for websocket in websockets.connect(ws_url):
        try:
            agent = WorkerAgent(
                worker_id=config.worker_id,
                worker_name=config.worker_name,
                websocket=websocket,
                tailscale_ip=tailscale_ip,
            )
            logger.info("Connected to coordinator")

            async def heartbeat_loop():
                while True:
                    await agent.send_heartbeat()
                    await asyncio.sleep(config.heartbeat_interval)

            heartbeat_task = asyncio.create_task(heartbeat_loop())
            try:
                async for raw_message in websocket:
                    import json
                    message = json.loads(raw_message)
                    await agent.router.dispatch(message)
            finally:
                heartbeat_task.cancel()
        except websockets.ConnectionClosed:
            logger.warning("Connection to coordinator lost, reconnecting...")
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="QuiltTrader Worker Agent")
    parser.add_argument("--coordinator-url", help="WebSocket URL of the coordinator")
    parser.add_argument("--name", help="Worker name")
    args = parser.parse_args()
    config = WorkerConfig()
    if args.coordinator_url:
        config.coordinator_url = args.coordinator_url
    if args.name:
        config.worker_name = args.name
    try:
        asyncio.run(run_worker(config))
    except KeyboardInterrupt:
        logger.info("Worker shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
