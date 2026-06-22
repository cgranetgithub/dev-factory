"""
GitHub client — lazy singleton wrapper around PyGitHub.
Instantiated on first use so importing the module doesn't require a token.
"""

from __future__ import annotations

from github import Auth, Github
from github.Issue import Issue
from github.Repository import Repository

from devfactory.config import settings


class GitHubClient:
    def __init__(self):
        self._gh: Github | None = None

    def _client(self) -> Github:
        if self._gh is None:
            if not settings.github_token:
                raise RuntimeError(
                    "GITHUB_TOKEN is not set. Copy .env.example to .env and fill in your token."
                )
            self._gh = Github(auth=Auth.Token(settings.github_token))
        return self._gh

    def get_repo(self, full_name: str) -> Repository:
        return self._client().get_repo(full_name)

    def get_issue(self, repo: str, number: int) -> Issue:
        return self.get_repo(repo).get_issue(number=number)

    def get_authenticated_user(self) -> str:
        return self._client().get_user().login


# Lazy singleton
gh = GitHubClient()
