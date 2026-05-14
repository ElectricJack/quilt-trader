import pytest
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_install_from_url_rejects_scraper_manifest(client):
    yaml_text = """
name: my-scraper
type: scraper
version: 1.0.0
schedule: "0 12 * * *"
"""
    with patch("coordinator.api.routes.algorithms._fetch_manifest_yaml",
               return_value=yaml_text):
        r = await client.post("/api/algorithms/install-from-url",
                              json={"repo_url": "https://github.com/foo/bar"})
    assert r.status_code == 422
    assert "not an algorithm" in r.json()["detail"]

@pytest.mark.asyncio
async def test_install_from_url_rejects_invalid_url(client):
    r = await client.post("/api/algorithms/install-from-url",
                          json={"repo_url": "not-a-url"})
    assert r.status_code == 400
