import pytest
from unittest.mock import MagicMock, patch
from coordinator.services.github_service import RepoInfo


@pytest.mark.asyncio
async def test_list_repos_no_pat(client):
    response = await client.get("/api/github/repos")
    assert response.status_code == 400
    assert "GitHub PAT" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_repos_with_pat(client):
    await client.put("/api/settings/github-pat", json={"value": "ghp_test123"})
    with patch("coordinator.api.routes.github.GitHubService") as mock_cls:
        mock_service = MagicMock()
        mock_service.list_repos.return_value = [
            RepoInfo(
                name="algo-1",
                full_name="user/algo-1",
                description="Test algo",
                clone_url="https://github.com/user/algo-1.git",
                html_url="https://github.com/user/algo-1",
            ),
            RepoInfo(
                name="not-quilt",
                full_name="user/not-quilt",
                description="A repo without manifest",
                clone_url="https://github.com/user/not-quilt.git",
                html_url="https://github.com/user/not-quilt",
            ),
        ]
        mock_cls.return_value = mock_service
        response = await client.get("/api/github/repos")
        assert response.status_code == 200
        body = response.json()
        # All repos are returned; manifest filtering happens at install time.
        assert len(body) == 2
        assert {r["name"] for r in body} == {"algo-1", "not-quilt"}


@pytest.mark.asyncio
async def test_install_algorithm_from_github(client):
    await client.put("/api/settings/github-pat", json={"value": "ghp_test123"})
    with patch("coordinator.api.routes.github.GitHubService") as mock_gh, \
         patch("coordinator.api.routes.github.PackageManager") as mock_pm:
        mock_gh.return_value.get_clone_url.return_value = "https://github.com/user/algo.git"
        mock_pm_inst = MagicMock()
        mock_pm_inst.validate_package.return_value = {
            "name": "algo",
            "type": "algorithm",
            "version": "1.0.0",
        }
        mock_pm_inst.get_commit_hash.return_value = "abc123"
        mock_pm.return_value = mock_pm_inst
        response = await client.post("/api/github/install", json={"full_name": "user/algo"})
        assert response.status_code == 201
        assert response.json()["name"] == "algo"
        assert response.json()["install_status"] == "installed"
