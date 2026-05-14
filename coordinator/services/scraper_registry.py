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
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import yaml

from coordinator.services.package_manager import PackageError, PackageManager
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
        session_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._engine = engine
        self._scheduler = scheduler
        self._packages_dir = packages_dir
        self._configs_dir = configs_dir
        self._scrapers: dict[str, ScraperRecord] = {}
        self._session_factory = session_factory

    @property
    def packages_dir(self) -> str:
        return self._packages_dir

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

    def register_scraper(self, package_dirname: str) -> ScraperRecord:
        """Load a single freshly-installed scraper from disk and add it to the registry.

        Mirrors the per-package logic in `discover_and_register` so newly cloned
        scrapers can be brought online without a coordinator restart.
        """
        pkg_dir = os.path.join(self._packages_dir, package_dirname)
        manifest_path = os.path.join(pkg_dir, "quilt.yaml")
        if not os.path.isfile(manifest_path):
            raise ValueError(f"quilt.yaml not found in {pkg_dir}")
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f) or {}
        if manifest.get("type") != "scraper":
            raise ValueError(
                f"manifest type is {manifest.get('type')!r}; expected 'scraper'"
            )

        name = manifest.get("name") or package_dirname
        schedule = manifest.get("schedule")
        if not schedule:
            raise ValueError(f"scraper {name} has no schedule in quilt.yaml")

        jitter_seconds = manifest.get("jitter_seconds")
        if jitter_seconds is not None:
            try:
                jitter_seconds = int(jitter_seconds)
            except (TypeError, ValueError):
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
        logger.info("registered scraper %s with schedule %r", name, schedule)
        return record

    def unregister_scraper(self, name: str) -> None:
        """Remove a scraper from the registry and cancel its scheduled job."""
        if name not in self._scrapers:
            return
        del self._scrapers[name]
        try:
            self._scheduler.remove_job(f"scraper:{name}")
        except Exception as e:
            logger.warning("failed to remove cron job for %s: %s", name, e)

    def install_scraper(self, repo_url: str, name: Optional[str] = None) -> ScraperRecord:
        """Clone, set up venv, install deps, validate, and register a scraper.

        `name` selects the on-disk directory under packages_dir. If omitted, derived
        from the repo URL.
        """
        if not name:
            name = repo_url.rstrip("/").rsplit("/", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
        if not name:
            raise ValueError("Could not derive package name from repo_url")

        pm = PackageManager(packages_dir=self._packages_dir)
        target = pm.package_path(name)
        if os.path.exists(target):
            raise PackageError(f"package directory already exists: {target}")

        pm.clone_repo(repo_url, name)
        try:
            pm.create_venv(name)
            pm.install_requirements(name)
            manifest = pm.validate_package(name)
            if manifest.get("type") != "scraper":
                raise PackageError(
                    f"package manifest type is {manifest.get('type')!r}, expected 'scraper'"
                )
            return self.register_scraper(name)
        except Exception:
            # Roll back the clone so retries don't fail with "directory already exists".
            try:
                pm.remove_package(name)
            except Exception as cleanup_err:  # noqa: BLE001
                logger.warning("failed to clean up after install failure: %s", cleanup_err)
            raise

    def uninstall_scraper(self, name: str) -> None:
        """Unregister + delete the package directory."""
        self.unregister_scraper(name)
        pm = PackageManager(packages_dir=self._packages_dir)
        try:
            pm.remove_package(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("failed to remove package %s: %s", name, e)

    async def _upsert_data_source(self, record: ScraperRecord, result: ScraperResult) -> None:
        """Write a DataSource row reflecting the most recent successful scrape."""
        if self._session_factory is None or not result.output_path:
            return
        # Best-effort row count (cheap for small CSVs; degrade silently on error).
        row_count: Optional[int] = None
        try:
            if os.path.exists(result.output_path):
                with open(result.output_path) as f:
                    row_count = max(0, sum(1 for _ in f) - 1)  # subtract header
        except Exception:
            row_count = None

        from sqlalchemy import select
        from coordinator.database.models import DataSource

        try:
            async with self._session_factory() as session:
                existing = (await session.execute(
                    select(DataSource)
                    .where(DataSource.type == "scraper")
                    .where(DataSource.source == record.name)
                )).scalar_one_or_none()
                metadata = {
                    "row_count": row_count,
                    "schedule": record.schedule,
                    "manifest_version": record.manifest.get("version"),
                }
                if existing is None:
                    session.add(DataSource(
                        type="scraper",
                        source=record.name,
                        name=record.name,
                        description=record.manifest.get("description"),
                        file_path=result.output_path,
                        last_updated=datetime.now(timezone.utc),
                        metadata_=metadata,
                    ))
                else:
                    existing.file_path = result.output_path
                    existing.last_updated = datetime.now(timezone.utc)
                    existing.description = record.manifest.get("description") or existing.description
                    existing.metadata_ = metadata
                await session.commit()
        except Exception as e:  # noqa: BLE001 — never let DB issues kill a scrape result
            logger.warning("failed to upsert DataSource for %s: %s", record.name, e)

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
            await self._upsert_data_source(record, result)
        else:
            record.last_status = "failed"
            record.last_error = result.error
            logger.warning("scraper %s failed: %s", name, result.error)
        return result
