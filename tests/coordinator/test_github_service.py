import pytest
from unittest.mock import MagicMock
from coordinator.services.github_service import GitHubService, RepoInfo


def make_mock_repo(name, full_name, description="", has_quilt_yaml=True):
    repo = MagicMock()
    repo.name = name
    repo.full_name = full_name
    repo.description = description
    repo.html_url = f"https://github.com/{full_name}"
    repo.clone_url = f"https://github.com/{full_name}.git"
    if has_quilt_yaml:
        repo.get_contents.return_value = MagicMock()
    else:
        from github import GithubException
        repo.get_contents.side_effect = GithubException(404, {}, {})
    return repo


def test_list_quilt_repos():
    mock_github = MagicMock()
    mock_user = MagicMock()
    repos = [
        make_mock_repo("algo-1", "user/algo-1", "My algo", True),
        make_mock_repo("not-quilt", "user/not-quilt", "No manifest", False),
        make_mock_repo("scraper-1", "user/scraper-1", "A scraper", True),
    ]
    mock_user.get_repos.return_value = repos
    mock_github.get_user.return_value = mock_user
    service = GitHubService(github_client=mock_github)
    results = service.list_quilt_repos()
    assert len(results) == 2
    assert results[0].name == "algo-1"
    assert all(isinstance(r, RepoInfo) for r in results)


def test_list_quilt_repos_empty():
    mock_github = MagicMock()
    mock_user = MagicMock()
    mock_user.get_repos.return_value = []
    mock_github.get_user.return_value = mock_user
    service = GitHubService(github_client=mock_github)
    assert service.list_quilt_repos() == []


def test_repo_info_fields():
    mock_github = MagicMock()
    mock_user = MagicMock()
    mock_user.get_repos.return_value = [
        make_mock_repo("test-algo", "ElectricJack/test-algo", "A test algorithm")
    ]
    mock_github.get_user.return_value = mock_user
    service = GitHubService(github_client=mock_github)
    results = service.list_quilt_repos()
    assert results[0].full_name == "ElectricJack/test-algo"
    assert results[0].clone_url == "https://github.com/ElectricJack/test-algo.git"


def test_get_repo_clone_url():
    mock_github = MagicMock()
    mock_repo = MagicMock()
    mock_repo.clone_url = "https://github.com/ElectricJack/algo.git"
    mock_github.get_repo.return_value = mock_repo
    service = GitHubService(github_client=mock_github)
    assert service.get_clone_url("ElectricJack/algo") == "https://github.com/ElectricJack/algo.git"
