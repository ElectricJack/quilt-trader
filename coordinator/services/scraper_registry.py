"""Auto-discover installed scrapers and bridge them to the scheduler.

Walks `packages_dir` for subdirectories containing `quilt.yaml`. For each
scraper-type manifest, registers a cron job with SchedulerService that
invokes ScraperEngine.run_scraper on the declared schedule.

Per-scraper config overrides (e.g. profile_dir, headless) are loaded from
`<scraper_configs_dir>/<name>.json` if present; otherwise the manifest's
defaults apply. The config file is the minimum-viable substitute for a
database-backed scraper-instance record (Spec B+ territory).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import yaml

from coordinator.services.scheduler import SchedulerService
from coordinator.services.scraper_engine import ScraperEngine, ScraperResult

logger = logging.getLogger(__name__)


@dataclass
class ScraperRecord:
    name: str
    schedule: str
    manifest: dict
    config: dict = field(default_factory=dict)
    jitter_seconds: Optional[int] = None
    last_status: Optional[str] = None
    last_run_at: Optional[str] = None
    last_output_path: Optional[str] = None
    last_error: Optional[str] = None


class ScraperRegistry:
    def __init__(
        self,
        *,
        engine: ScraperEngine,
        scheduler: SchedulerService,
        packages_dir: str,
        configs_dir: str,
    ) -> None:
        self._engine = engine
        self._scheduler = scheduler
        self._packages_dir = packages_dir
        self._configs_dir = configs_dir
        self._scrapers: dict[str, ScraperRecord] = {}

    def discover_and_register(self) -> list[ScraperRecord]:
        """Scan packages_dir, register cron jobs, return discovered records."""
        os.makedirs(self._configs_dir, exist_ok=True)
        if not os.path.isdir(self._packages_dir):
            logger.info("packages_dir does not exist: %s", self._packages_dir)
            return []

        for entry in sorted(os.listdir(self._packages_dir)):
            pkg_dir = os.path.join(self._packages_dir, entry)
            manifest_path = os.path.join(pkg_dir, "quilt.yaml")
            if not os.path.isfile(manifest_path):
                continue
            try:
                with open(manifest_path) as f:
                    manifest = yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning("failed to parse %s: %s", manifest_path, e)
                continue

            if manifest.get("type") != "scraper":
                continue

            name = manifest.get("name") or entry
            schedule = manifest.get("schedule")
            if not schedule:
                logger.warning("scraper %s has no schedule; skipping", name)
                continue

            jitter_seconds = manifest.get("jitter_seconds")
            if jitter_seconds is not None:
                try:
                    jitter_seconds = int(jitter_seconds)
                except (TypeError, ValueError):
                    logger.warning(
                        "scraper %s has non-integer jitter_seconds %r; ignoring",
                        name, jitter_seconds,
                    )
                    jitter_seconds = None

            config = self._load_overrides(name)
            record = ScraperRecord(
                name=name,
                schedule=schedule,
                manifest=manifest,
                config=config,
                jitter_seconds=jitter_seconds,
            )
            self._scrapers[name] = record

            job_id = f"scraper:{name}"
            self._scheduler.add_cron_job(
                job_id=job_id,
                func=lambda n=name: asyncio.create_task(self.run(n)),
                cron_expr=schedule,
                jitter=jitter_seconds,
            )
            logger.info(
                "registered scraper %s with schedule %r jitter=%s",
                name, schedule, jitter_seconds,
            )

        return list(self._scrapers.values())

    def _load_overrides(self, name: str) -> dict:
        path = os.path.join(self._configs_dir, f"{name}.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning("failed to load overrides %s: %s", path, e)
            return {}

    def list_records(self) -> list[ScraperRecord]:
        return list(self._scrapers.values())

    def get(self, name: str) -> Optional[ScraperRecord]:
        return self._scrapers.get(name)

    async def run(self, name: str) -> ScraperResult:
        """Trigger a scrape immediately. Returns the ScraperResult."""
        record = self._scrapers.get(name)
        if record is None:
            return ScraperResult(success=False, error=f"scraper {name!r} not registered")

        logger.info("running scraper %s", name)
        record.last_status = "running"
        record.last_run_at = datetime.utcnow().isoformat() + "Z"

        # ScraperEngine.run_scraper does subprocess.run which is blocking.
        # Push it to a thread so the event loop stays responsive.
        result = await asyncio.to_thread(
            self._engine.run_scraper, name, "csv", record.config
        )

        record.last_run_at = datetime.utcnow().isoformat() + "Z"
        if result.success:
            record.last_status = "ok"
            record.last_output_path = result.output_path
            record.last_error = None
            logger.info("scraper %s wrote %s", name, result.output_path)
        else:
            record.last_status = "failed"
            record.last_error = result.error
            logger.warning("scraper %s failed: %s", name, result.error)
        return result
