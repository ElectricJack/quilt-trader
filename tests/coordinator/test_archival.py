import os
import pytest
from datetime import datetime, timezone, timedelta
import pandas as pd
from coordinator.services.archival import ArchivalService

@pytest.fixture
def archive_dir(tmp_path):
    d = tmp_path / "archive"
    d.mkdir()
    return str(d)

def test_archive_path(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    path = svc.archive_path("decision_log", "2025-01-01", "2025-01-31")
    assert "decision_log" in path
    assert "2025-01-01" in path
    assert path.endswith(".parquet")

def test_export_to_parquet(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame({"id": ["1", "2", "3"], "timestamp": ["2025-01-01", "2025-01-02", "2025-01-03"], "data": ["a", "b", "c"]})
    path = svc.export_to_parquet("decision_log", "2025-01-01", "2025-01-31", df)
    assert os.path.exists(path)
    loaded = pd.read_parquet(path)
    assert len(loaded) == 3

def test_export_empty_dataframe(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame()
    path = svc.export_to_parquet("trade_log", "2025-01-01", "2025-01-31", df)
    assert path is None

def test_list_archives(archive_dir):
    svc = ArchivalService(archive_dir=archive_dir)
    df = pd.DataFrame({"id": ["1"], "data": ["a"]})
    svc.export_to_parquet("decision_log", "2025-01-01", "2025-01-31", df)
    svc.export_to_parquet("trade_log", "2025-02-01", "2025-02-28", df)
    archives = svc.list_archives()
    assert len(archives) == 2
