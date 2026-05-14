import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RepoInfo:
    name: str
    full_name: str
    description: str
    clone_url: str
    html_url: str


class GitHubService:
    def __init__(self, github_client: Any = None, pat: str | None = None) -> None:
        if github_client:
            self._github = github_client
        else:
            from github import Github
            self._github = Github(pat)

    def list_repos(self) -> list[RepoInfo]:
        """Return all repositories visible to the authenticated user.

        Previously this filtered to only repos containing a `quilt.yaml`
        manifest, but that filter is expensive (one extra API call per
        repo) and hid valid repos from the install picker. The install
        endpoint already validates manifests by cloning + parsing, so
        we let the user pick from the full list here.
        """
        user = self._github.get_user()
        results = []
        for repo in user.get_repos():
            results.append(RepoInfo(
                name=repo.name,
                full_name=repo.full_name,
                description=repo.description or "",
                clone_url=repo.clone_url,
                html_url=repo.html_url,
            ))
        return results

    def get_clone_url(self, full_name: str) -> str:
        repo = self._github.get_repo(full_name)
        return repo.clone_url

    def get_repo_status(self, full_name: str, current_sha: str | None) -> dict:
        """Return default branch info + commits behind.

        Result shape:
          {
            "default_branch": "main",
            "head_sha": "abc1234...",
            "commits_behind": 5,   # 0 if up to date or unknown
            "current_sha": "..."
          }
        """
        repo = self._github.get_repo(full_name)
        default_branch = repo.default_branch
        head = repo.get_branch(default_branch).commit
        head_sha = head.sha
        behind = 0
        if current_sha and current_sha != head_sha:
            # PyGithub: repo.compare(base, head) — "ahead_by" is from base to head
            try:
                cmp = repo.compare(current_sha, head_sha)
                behind = cmp.ahead_by
            except Exception as e:
                # If the current_sha isn't reachable in the default branch's history,
                # PyGithub raises GithubException. Report behind=-1 to flag "unknown"
                # (e.g., when the user is on a fork or a deleted branch).
                logger.warning("Could not compare commits for %s: %s", full_name, e)
                behind = -1
        return {
            "default_branch": default_branch,
            "head_sha": head_sha,
            "commits_behind": behind,
            "current_sha": current_sha,
        }
