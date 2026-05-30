# tests/sdk/cli/test_data_datasets.py
import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from sdk.cli.main import quilt


def _resp(json_body, status=200):
    r = MagicMock()
    r.status_code = status
    r.headers = {"content-type": "application/json"}
    r.content = _json.dumps(json_body).encode() if json_body is not None else b""
    r.json.return_value = json_body
    return r


# ── datasets list ─────────────────────────────────────────────────────

def test_datasets_list_hits_correct_endpoint():
    runner = CliRunner()
    rows = [
        {"name": "fmp.house_disclosures", "provider": "fmp",
         "pagination": "page", "symbol_keyed": False},
        {"name": "fmp.insider_trading", "provider": "fmp",
         "pagination": "date_range", "symbol_keyed": True},
    ]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))) as m:
        result = runner.invoke(quilt, ["data", "datasets", "list"])
    assert result.exit_code == 0, result.output
    assert "fmp.house_disclosures" in result.output
    assert "fmp.insider_trading" in result.output
    called_url = m.call_args[0][0]
    assert called_url.endswith("/api/datasets")


def test_datasets_list_json_mode():
    runner = CliRunner()
    rows = [{"name": "fmp.house_disclosures", "provider": "fmp",
             "pagination": "page", "symbol_keyed": False}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["--json", "data", "datasets", "list"])
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.output)
    assert parsed[0]["name"] == "fmp.house_disclosures"


# ── datasets show ─────────────────────────────────────────────────────

def test_datasets_show_hits_correct_endpoint():
    runner = CliRunner()
    spec = {
        "name": "fmp.insider_trading",
        "provider": "fmp",
        "pagination": "date_range",
        "symbol_keyed": True,
        "columns": {"symbol": "str", "transaction_date": "date"},
    }
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(spec))) as m:
        result = runner.invoke(quilt, ["data", "datasets", "show", "fmp.insider_trading"])
    assert result.exit_code == 0, result.output
    called_url = m.call_args[0][0]
    assert called_url.endswith("/api/datasets/fmp.insider_trading")
    assert "fmp.insider_trading" in result.output


# ── datasets download ─────────────────────────────────────────────────

def test_datasets_download_posts_correct_endpoint_and_body():
    runner = CliRunner()
    body = {"id": 1, "status": "queued"}
    with patch("httpx.AsyncClient.post",
               new=AsyncMock(return_value=_resp(body))) as m:
        result = runner.invoke(quilt, [
            "data", "datasets", "download", "fmp.insider_trading",
            "--symbol", "AAPL",
            "--from", "2024-01-01",
            "--to", "2024-06-01",
        ])
    assert result.exit_code == 0, result.output
    called_url = m.call_args[0][0]
    assert called_url.endswith("/api/datasets/downloads")
    posted_json = m.call_args.kwargs["json"]
    assert posted_json["name"] == "fmp.insider_trading"
    assert posted_json["params"]["symbol"] == "AAPL"
    assert posted_json["params"]["from"] == "2024-01-01"
    assert posted_json["params"]["to"] == "2024-06-01"


def test_datasets_download_queued_message():
    runner = CliRunner()
    with patch("httpx.AsyncClient.post",
               new=AsyncMock(return_value=_resp({"id": 42, "status": "queued"}))):
        result = runner.invoke(quilt, [
            "data", "datasets", "download", "fmp.house_disclosures",
        ])
    assert result.exit_code == 0, result.output
    assert "42" in result.output
    assert "queued" in result.output


def test_datasets_download_extra_params():
    runner = CliRunner()
    with patch("httpx.AsyncClient.post",
               new=AsyncMock(return_value=_resp({"id": 5, "status": "queued"}))) as m:
        result = runner.invoke(quilt, [
            "data", "datasets", "download", "fmp.some_dataset",
            "--param", "limit=100",
            "--param", "page=2",
        ])
    assert result.exit_code == 0, result.output
    posted_json = m.call_args.kwargs["json"]
    assert posted_json["params"]["limit"] == "100"
    assert posted_json["params"]["page"] == "2"


# ── datasets downloads ────────────────────────────────────────────────

def test_datasets_downloads_list_hits_correct_endpoint():
    runner = CliRunner()
    rows = [
        {"id": 10, "dataset_name": "fmp.insider_trading", "status": "complete",
         "rows_fetched": 5000, "progress_pct": 1.0},
    ]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))) as m:
        result = runner.invoke(quilt, ["data", "datasets", "downloads"])
    assert result.exit_code == 0, result.output
    called_url = m.call_args[0][0]
    assert called_url.endswith("/api/datasets/downloads")
    assert "fmp.insider_trading" in result.output


def test_datasets_downloads_filter_by_status():
    runner = CliRunner()
    rows = [{"id": 7, "dataset_name": "fmp.house_disclosures", "status": "running",
             "rows_fetched": 100, "progress_pct": 0.4}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))) as m:
        result = runner.invoke(quilt, [
            "data", "datasets", "downloads", "--status", "running",
        ])
    assert result.exit_code == 0, result.output
    call_kwargs = m.call_args.kwargs
    assert call_kwargs.get("params", {}).get("status") == "running"


def test_datasets_downloads_filter_by_provider():
    runner = CliRunner()
    rows = []
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))) as m:
        result = runner.invoke(quilt, [
            "data", "datasets", "downloads", "--provider", "fmp",
        ])
    assert result.exit_code == 0, result.output
    call_kwargs = m.call_args.kwargs
    assert call_kwargs.get("params", {}).get("provider") == "fmp"


# ── datasets quota ────────────────────────────────────────────────────

def test_datasets_quota_prints_summary():
    runner = CliRunner()
    rows = [
        {"provider": "fmp", "calls_used": 147, "daily_limit": 250, "exhausted": False},
        {"provider": "polygon", "calls_used": 250, "daily_limit": 250, "exhausted": True},
    ]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))) as m:
        result = runner.invoke(quilt, ["data", "datasets", "quota"])
    assert result.exit_code == 0, result.output
    called_url = m.call_args[0][0]
    assert called_url.endswith("/api/datasets/quota")
    assert "147" in result.output
    assert "250" in result.output
    assert "EXHAUSTED" in result.output


def test_datasets_quota_empty_shows_no_usage_message():
    runner = CliRunner()
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp([]))):
        result = runner.invoke(quilt, ["data", "datasets", "quota"])
    assert result.exit_code == 0, result.output
    assert "no usage" in result.output


def test_datasets_quota_json_mode():
    runner = CliRunner()
    rows = [{"provider": "fmp", "calls_used": 10, "daily_limit": 250, "exhausted": False}]
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_resp(rows))):
        result = runner.invoke(quilt, ["--json", "data", "datasets", "quota"])
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.output)
    assert parsed[0]["provider"] == "fmp"
