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

    def list_quilt_repos(self) -> list[RepoInfo]:
        user = self._github.get_user()
        results = []
        for repo in user.get_repos():
            try:
                repo.get_contents("quilt.yaml")
                results.append(RepoInfo(
                    name=repo.name,
                    full_name=repo.full_name,
                    description=repo.description or "",
                    clone_url=repo.clone_url,
                    html_url=repo.html_url,
                ))
            except Exception:
                continue
        return results

    def get_clone_url(self, full_name: str) -> str:
        repo = self._github.get_repo(full_name)
        return repo.clone_url
