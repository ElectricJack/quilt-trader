"""Tests for the scraper install flow + DataSource upsert."""
import os
from unittest.mock import MagicMock, patch

import pytest
import yaml

from coordinator.services.scheduler import SchedulerService
from coordinator.services.scraper_engine import ScraperEngine, ScraperResult
from coordinator.services.scraper_registry import ScraperRegistry


def _write_manifest(pkg_dir: str, *, name="my-scraper", schedule="0 14 * * 1-5",
                    type_="scraper", entry_point="scraper.py", description="A test scraper") -> None:
    os.makedirs(pkg_dir, exist_ok=True)
    with open(os.path.join(pkg_dir, "quilt.yaml"), "w") as f:
        yaml.safe_dump({
            "type": type_,
            "name": name,
            "schedule": schedule,
            "entry_point": entry_point,
            "description": description,
            "version": "0.1.0",
        }, f)
    # An empty entry_point file so manifest validation passes.
    with open(os.path.join(pkg_dir, entry_point), "w") as f:
        f.write("# entry point\n")


def _make_registry(tmp_path):
    packages_dir = tmp_path / "packages"
    configs_dir = tmp_path / "scraper_configs"
    custom_dir = tmp_path / "custom"
    packages_dir.mkdir()
    configs_dir.mkdir()
    custom_dir.mkdir()

    scheduler = MagicMock(spec=SchedulerService)
    engine = ScraperEngine(packages_dir=str(packages_dir), output_dir=str(custom_dir))
    reg = ScraperRegistry(
        engine=engine,
        scheduler=scheduler,
        packages_dir=str(packages_dir),
        configs_dir=str(configs_dir),
    )
    return reg, scheduler, packages_dir, custom_dir


class TestRegisterScraper:
    def test_register_scraper_happy_path(self, tmp_path):
        reg, scheduler, packages_dir, _ = _make_registry(tmp_path)
        _write_manifest(str(packages_dir / "my-scraper"))
        record = reg.register_scraper("my-scraper")
        assert record.name == "my-scraper"
        assert record.schedule == "0 14 * * 1-5"
        scheduler.add_cron_job.assert_called_once()

    def test_register_scraper_rejects_non_scraper_manifest(self, tmp_path):
        reg, _, packages_dir, _ = _make_registry(tmp_path)
        _write_manifest(str(packages_dir / "wrong-type"), type_="algorithm")
        with pytest.raises(ValueError, match="expected 'scraper'"):
            reg.register_scraper("wrong-type")

    def test_register_scraper_missing_schedule(self, tmp_path):
        reg, _, packages_dir, _ = _make_registry(tmp_path)
        _write_manifest(str(packages_dir / "no-schedule"), schedule="")
        with pytest.raises(ValueError, match="no schedule"):
            reg.register_scraper("no-schedule")

    def test_register_scraper_missing_manifest(self, tmp_path):
        reg, _, packages_dir, _ = _make_registry(tmp_path)
        (packages_dir / "no-manifest").mkdir()
        with pytest.raises(ValueError, match="quilt.yaml not found"):
            reg.register_scraper("no-manifest")


class TestInstallScraper:
    def test_install_scraper_invokes_package_manager_and_registers(self, tmp_path):
        reg, scheduler, packages_dir, _ = _make_registry(tmp_path)

        # Patch PackageManager to skip the real git clone but materialize a manifest.
        from coordinator.services import scraper_registry as sr_mod

        def fake_clone(_self, _url, name):
            _write_manifest(str(packages_dir / name))

        with patch.object(sr_mod.PackageManager, "clone_repo", new=fake_clone), \
             patch.object(sr_mod.PackageManager, "create_venv", new=lambda *a, **kw: None), \
             patch.object(sr_mod.PackageManager, "install_requirements", new=lambda *a, **kw: None):
            record = reg.install_scraper("https://github.com/owner/my-scraper.git")
        assert record.name == "my-scraper"
        scheduler.add_cron_job.assert_called_once()
        assert (packages_dir / "my-scraper" / "quilt.yaml").exists()

    def test_install_scraper_rolls_back_on_validation_failure(self, tmp_path):
        reg, _, packages_dir, _ = _make_registry(tmp_path)
        from coordinator.services import scraper_registry as sr_mod

        def fake_clone(_self, _url, name):
            # Plant an algorithm-typed manifest so validation fails.
            _write_manifest(str(packages_dir / name), type_="algorithm")

        with patch.object(sr_mod.PackageManager, "clone_repo", new=fake_clone), \
             patch.object(sr_mod.PackageManager, "create_venv", new=lambda *a, **kw: None), \
             patch.object(sr_mod.PackageManager, "install_requirements", new=lambda *a, **kw: None):
            with pytest.raises(Exception, match="expected 'scraper'"):
                reg.install_scraper("https://github.com/owner/wrong-type.git")
        # Directory should have been removed by the rollback.
        assert not (packages_dir / "wrong-type").exists()

    def test_install_scraper_rejects_existing_package(self, tmp_path):
        reg, _, packages_dir, _ = _make_registry(tmp_path)
        (packages_dir / "my-scraper").mkdir()
        from coordinator.services import scraper_registry as sr_mod
        with patch.object(sr_mod.PackageManager, "clone_repo", new=lambda *a, **kw: None):
            with pytest.raises(Exception, match="already exists"):
                reg.install_scraper("https://github.com/owner/my-scraper.git")


class TestUninstallScraper:
    def test_uninstall_removes_registry_and_directory(self, tmp_path):
        reg, scheduler, packages_dir, _ = _make_registry(tmp_path)
        _write_manifest(str(packages_dir / "my-scraper"))
        reg.register_scraper("my-scraper")
        assert (packages_dir / "my-scraper").exists()

        reg.uninstall_scraper("my-scraper")
        assert reg.get("my-scraper") is None
        scheduler.remove_job.assert_called_once_with("scraper:my-scraper")
        # Package dir should be gone.
        assert not (packages_dir / "my-scraper").exists()


class TestDataSourceUpsert:
    @pytest.mark.asyncio
    async def test_run_upserts_data_source_on_success(self, tmp_path, db_session, test_app):
        """End-to-end: stub engine to return success → registry writes DataSource row."""
        from coordinator.api.dependencies import get_container
        from coordinator.database.models import DataSource
        from sqlalchemy import select

        reg, _, packages_dir, custom_dir = _make_registry(tmp_path)
        # Inject a real session factory so the upsert can run.
        container = get_container()
        reg._session_factory = container.session_factory

        # Set up a registered scraper + a fake CSV output.
        _write_manifest(str(packages_dir / "my-scraper"))
        reg.register_scraper("my-scraper")
        out_path = custom_dir / "my-scraper.csv"
        out_path.write_text("col_a,col_b\n1,2\n3,4\n")

        # Stub the engine to return success pointing at the CSV.
        def stub_run_scraper(name, fmt, cfg):
            return ScraperResult(success=True, output_path=str(out_path))

        reg._engine.run_scraper = stub_run_scraper  # type: ignore[assignment]
        result = await reg.run("my-scraper")
        assert result.success

        rows = (await db_session.execute(
            select(DataSource).where(DataSource.source == "my-scraper")
        )).scalars().all()
        assert len(rows) == 1
        ds = rows[0]
        assert ds.type == "scraper"
        assert ds.file_path == str(out_path)
        assert ds.last_updated is not None
        assert (ds.metadata_ or {}).get("row_count") == 2  # 2 data rows


class TestListDataSources:
    @pytest.mark.asyncio
    async def test_list_returns_filtered_by_type(self, client, db_session):
        from coordinator.database.models import DataSource

        db_session.add_all([
            DataSource(type="scraper", source="a", name="a", file_path="/tmp/a.csv"),
            DataSource(type="scraper", source="b", name="b", file_path="/tmp/b.csv"),
            DataSource(type="market", source="polygon", name="x", file_path="/tmp/x.parquet"),
        ])
        await db_session.commit()

        resp = await client.get("/api/data/sources?type=scraper")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        assert {it["source"] for it in items} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_list_all_when_no_filter(self, client, db_session):
        from coordinator.database.models import DataSource

        db_session.add_all([
            DataSource(type="scraper", source="a", name="a"),
            DataSource(type="market", source="polygon", name="x"),
        ])
        await db_session.commit()

        resp = await client.get("/api/data/sources")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
