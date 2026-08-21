from __future__ import annotations


class GitHubError(Exception):
    """Base class for all GitHub integration failures."""


class GitHubAuthError(GitHubError):
    """App/installation/user credentials are missing, invalid, or expired."""


class GitHubRateLimitError(GitHubError):
    """The GitHub API's primary or secondary rate limit was hit and retries
    were exhausted."""

    def __init__(self, message: str, reset_at: int | None = None):
        super().__init__(message)
        self.reset_at = reset_at


class GitHubAPIError(GitHubError):
    """GitHub returned a non-2xx response that isn't a rate-limit response."""

    def __init__(self, message: str, status_code: int, url: str):
        super().__init__(message)
        self.status_code = status_code
        self.url = url
