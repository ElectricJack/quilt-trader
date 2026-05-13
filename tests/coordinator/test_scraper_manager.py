import pytest
from coordinator.services.scraper_manager import ScraperManager


@pytest.fixture
def scraper_manager():
    return ScraperManager()


def test_register_scraper(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    assert scraper_manager.is_registered("alpha-picks")
    assert not scraper_manager.is_running("alpha-picks")


def test_start_scraper(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.start("alpha-picks")
    assert scraper_manager.is_running("alpha-picks")


def test_stop_scraper(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.start("alpha-picks")
    scraper_manager.stop("alpha-picks")
    assert not scraper_manager.is_running("alpha-picks")


def test_add_dependent(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.add_dependent("alpha-picks", "instance-1")
    assert scraper_manager.dependent_count("alpha-picks") == 1


def test_remove_dependent(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.add_dependent("alpha-picks", "instance-1")
    scraper_manager.add_dependent("alpha-picks", "instance-2")
    scraper_manager.remove_dependent("alpha-picks", "instance-1")
    assert scraper_manager.dependent_count("alpha-picks") == 1


def test_should_stop_when_no_dependents(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.start("alpha-picks")
    scraper_manager.add_dependent("alpha-picks", "instance-1")
    scraper_manager.remove_dependent("alpha-picks", "instance-1")
    assert scraper_manager.should_stop("alpha-picks") is True


def test_should_not_stop_with_dependents(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.start("alpha-picks")
    scraper_manager.add_dependent("alpha-picks", "instance-1")
    assert scraper_manager.should_stop("alpha-picks") is False


def test_ensure_running_starts_if_needed(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    assert scraper_manager.is_running("alpha-picks")
    assert scraper_manager.dependent_count("alpha-picks") == 1


def test_ensure_running_idempotent(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    assert scraper_manager.dependent_count("alpha-picks") == 1


def test_release_and_stop_if_no_dependents(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    assert scraper_manager.release("alpha-picks", "instance-1") is True
    assert not scraper_manager.is_running("alpha-picks")


def test_release_keeps_running_if_other_dependents(scraper_manager):
    scraper_manager.register("alpha-picks", schedule="*/30 * * * *")
    scraper_manager.ensure_running("alpha-picks", "instance-1")
    scraper_manager.ensure_running("alpha-picks", "instance-2")
    assert scraper_manager.release("alpha-picks", "instance-1") is False
    assert scraper_manager.is_running("alpha-picks")
