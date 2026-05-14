from unittest.mock import MagicMock
from coordinator.services.github_service import GitHubService, RepoInfo


def make_mock_repo(name, full_name, description=""):
    repo = MagicMock()
    repo.name = name
    repo.full_name = full_name
    repo.description = description
    repo.html_url = f"https://github.com/{full_name}"
    repo.clone_url = f"https://github.com/{full_name}.git"
    return repo


def test_list_repos_returns_all():
    """list_repos() returns every repo visible to the user, no manifest filter."""
    mock_github = MagicMock()
    mock_user = MagicMock()
    repos = [
        make_mock_repo("algo-1", "user/algo-1", "My algo"),
        make_mock_repo("not-quilt", "user/not-quilt", "No manifest"),
        make_mock_repo("scraper-1", "user/scraper-1", "A scraper"),
    ]
    mock_user.get_repos.return_value = repos
    mock_github.get_user.return_value = mock_user
    service = GitHubService(github_client=mock_github)
    results = service.list_repos()
    assert len(results) == 3
    assert {r.name for r in results} == {"algo-1", "not-quilt", "scraper-1"}
    assert all(isinstance(r, RepoInfo) for r in results)
    # Importantly: list_repos does NOT call get_contents per repo.
    for r in repos:
        r.get_contents.assert_not_called()


def test_list_repos_empty():
    mock_github = MagicMock()
    mock_user = MagicMock()
    mock_user.get_repos.return_value = []
    mock_github.get_user.return_value = mock_user
    service = GitHubService(github_client=mock_github)
    assert service.list_repos() == []


def test_repo_info_fields():
    mock_github = MagicMock()
    mock_user = MagicMock()
    mock_user.get_repos.return_value = [
        make_mock_repo("test-algo", "ElectricJack/test-algo", "A test algorithm")
    ]
    mock_github.get_user.return_value = mock_user
    service = GitHubService(github_client=mock_github)
    results = service.list_repos()
    assert results[0].full_name == "ElectricJack/test-algo"
    assert results[0].clone_url == "https://github.com/ElectricJack/test-algo.git"


def test_get_repo_clone_url():
    mock_github = MagicMock()
    mock_repo = MagicMock()
    mock_repo.clone_url = "https://github.com/ElectricJack/algo.git"
    mock_github.get_repo.return_value = mock_repo
    service = GitHubService(github_client=mock_github)
    assert service.get_clone_url("ElectricJack/algo") == "https://github.com/ElectricJack/algo.git"


def _mock_repo_with_head(default_branch: str, head_sha: str) -> MagicMock:
    mock_repo = MagicMock()
    mock_repo.default_branch = default_branch
    mock_branch = MagicMock()
    mock_branch.commit.sha = head_sha
    mock_repo.get_branch.return_value = mock_branch
    return mock_repo


def test_get_repo_status_up_to_date():
    mock_github = MagicMock()
    mock_github.get_repo.return_value = _mock_repo_with_head("main", "sha-head")
    service = GitHubService(github_client=mock_github)
    result = service.get_repo_status("user/algo", "sha-head")
    assert result == {
        "default_branch": "main",
        "head_sha": "sha-head",
        "commits_behind": 0,
        "current_sha": "sha-head",
    }


def test_get_repo_status_behind():
    mock_github = MagicMock()
    mock_repo = _mock_repo_with_head("main", "sha-head")
    mock_cmp = MagicMock()
    mock_cmp.ahead_by = 5
    mock_repo.compare.return_value = mock_cmp
    mock_github.get_repo.return_value = mock_repo
    service = GitHubService(github_client=mock_github)
    result = service.get_repo_status("user/algo", "sha-old")
    assert result["commits_behind"] == 5
    assert result["head_sha"] == "sha-head"
    assert result["current_sha"] == "sha-old"
    assert result["default_branch"] == "main"
    mock_repo.compare.assert_called_once_with("sha-old", "sha-head")


def test_get_repo_status_diverged_unknown():
    mock_github = MagicMock()
    mock_repo = _mock_repo_with_head("main", "sha-head")
    mock_repo.compare.side_effect = Exception("not in history")
    mock_github.get_repo.return_value = mock_repo
    service = GitHubService(github_client=mock_github)
    result = service.get_repo_status("user/algo", "sha-fork")
    assert result["commits_behind"] == -1


def test_get_repo_status_no_current_sha():
    mock_github = MagicMock()
    mock_github.get_repo.return_value = _mock_repo_with_head("main", "sha-head")
    service = GitHubService(github_client=mock_github)
    result = service.get_repo_status("user/algo", None)
    assert result["commits_behind"] == 0
    assert result["current_sha"] is None
