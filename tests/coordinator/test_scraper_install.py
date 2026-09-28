"""Tests for the scraper install flow + DataSource upsert."""
import os
from unittest.mock import MagicMock, patch

import pytest
import yaml

from coordinator.services.scheduler import SchedulerService
from coordinator.services.scraper_engine import ScraperEngine, ScraperResult
from coordinator.services.scraper_registry import ScraperRegistry


def _write_manifest(pkg_dir: str, *, name="my-scraper", schedule="0 14 * * 1-5",
                    type_="scraper", entry_point="scraper.py", description="A test scraper",
                    auth=None) -> None:
    os.makedirs(pkg_dir, exist_ok=True)
    manifest = {
        "type": type_,
        "name": name,
        "schedule": schedule,
        "entry_point": entry_point,
        "description": description,
        "version": "0.1.0",
    }
    if auth is not None:
        manifest["config"] = {"parameters": [
            {"name": "profile_dir", "type": "string", "default": "~/.cache/my-profile"},
        ]}
        manifest["auth"] = auth
    with open(os.path.join(pkg_dir, "quilt.yaml"), "w") as f:
        yaml.safe_dump(manifest, f)
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


GOOD_AUTH = {
    "kind": "browser_profile",
    "engine": "patchright",
    "profile_dir_param": "profile_dir",
    "login_url": "https://example.com/login",
    "verify": {"url": "https://example.com/picks", "selector": "table.picks"},
}
BAD_AUTH = {**GOOD_AUTH, "kind": "oauth"}


class TestAuthBlockParsing:
    """ScraperRecord.auth (review rev-nimble-bridge, section 5)."""

    def test_register_without_auth_block(self, tmp_path):
        reg, _, packages_dir, _ = _make_registry(tmp_path)
        _write_manifest(str(packages_dir / "my-scraper"))
        assert reg.register_scraper("my-scraper").auth is None

    def test_register_parses_auth_block(self, tmp_path):
        reg, _, packages_dir, _ = _make_registry(tmp_path)
        _write_manifest(str(packages_dir / "my-scraper"), auth=GOOD_AUTH)
        record = reg.register_scraper("my-scraper")
        assert record.auth is not None
        assert record.auth.kind == "browser_profile"
        assert record.auth.engine == "patchright"
        assert record.auth.verify.selectors == ("table.picks",)

    def test_register_bad_auth_block_warns_and_registers_without_auth(self, tmp_path, caplog):
        reg, scheduler, packages_dir, _ = _make_registry(tmp_path)
        _write_manifest(str(packages_dir / "my-scraper"), auth=BAD_AUTH)
        with caplog.at_level("WARNING", logger="coordinator.services.scraper_registry"):
            record = reg.register_scraper("my-scraper")
        assert record.auth is None
        scheduler.add_cron_job.assert_called_once()  # the scrape itself still runs
        assert "invalid auth: block" in caplog.text
        assert "auth.kind" in caplog.text

    def test_discover_parses_and_tolerates_auth_blocks(self, tmp_path, caplog):
        reg, scheduler, packages_dir, _ = _make_registry(tmp_path)
        _write_manifest(str(packages_dir / "good"), name="good", auth=GOOD_AUTH)
        _write_manifest(str(packages_dir / "bad"), name="bad", auth=BAD_AUTH)
        _write_manifest(str(packages_dir / "plain"), name="plain")
        with caplog.at_level("WARNING", logger="coordinator.services.scraper_registry"):
            records = {r.name: r for r in reg.discover_and_register()}
        assert set(records) == {"good", "bad", "plain"}
        assert records["good"].auth.login_url == "https://example.com/login"
        assert records["bad"].auth is None
        assert records["plain"].auth is None
        assert scheduler.add_cron_job.call_count == 3
        assert "scraper bad has an invalid auth: block" in caplog.text

    def _install(self, reg, packages_dir, auth):
        from coordinator.services import scraper_registry as sr_mod

        def fake_clone(_self, _url, name):
            _write_manifest(str(packages_dir / name), auth=auth)

        with patch.object(sr_mod.PackageManager, "clone_repo", new=fake_clone), \
             patch.object(sr_mod.PackageManager, "create_venv", new=lambda *a, **kw: None), \
             patch.object(sr_mod.PackageManager, "install_requirements", new=lambda *a, **kw: None):
            return reg.install_scraper("https://github.com/owner/my-scraper.git")

    def test_install_with_good_auth_block(self, tmp_path):
        reg, _, packages_dir, _ = _make_registry(tmp_path)
        record = self._install(reg, packages_dir, GOOD_AUTH)
        assert record.auth is not None and record.auth.profile_dir_param == "profile_dir"

    def test_install_refuses_bad_auth_block(self, tmp_path):
        from coordinator.services.package_manager import PackageError

        reg, scheduler, packages_dir, _ = _make_registry(tmp_path)
        with pytest.raises(PackageError, match="invalid auth: block in quilt.yaml: auth.kind"):
            self._install(reg, packages_dir, BAD_AUTH)
        assert not (packages_dir / "my-scraper").exists()  # rolled back
        assert reg.get("my-scraper") is None
        scheduler.add_cron_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_install_route_answers_422_for_bad_auth_block(self, client, tmp_path):
        from coordinator.api.routes import scrapers as routes

        reg, _, packages_dir, _ = _make_registry(tmp_path)
        from coordinator.services import scraper_registry as sr_mod

        def fake_clone(_self, _url, name):
            _write_manifest(str(packages_dir / name), auth=BAD_AUTH)

        with patch.object(routes, "_require_registry", return_value=reg), \
             patch.object(sr_mod.PackageManager, "clone_repo", new=fake_clone), \
             patch.object(sr_mod.PackageManager, "create_venv", new=lambda *a, **kw: None), \
             patch.object(sr_mod.PackageManager, "install_requirements", new=lambda *a, **kw: None):
            resp = await client.post(
                "/api/scrapers", json={"repo_url": "https://github.com/owner/my-scraper.git"},
            )
        assert resp.status_code == 422
        assert "auth.kind" in resp.json()["detail"]


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
