"""Thin, typed wrapper around the GitHub REST API endpoints ADPO needs.

Design rules (see architecture note in the project instructions):
  * This module ONLY talks to GitHub and returns plain dicts/lists shaped
    exactly like the GitHub REST API responses. It knows nothing about our
    database or about `adpo` analyzer models - normalization happens one
    layer up, in `app.services.ingestion.sync_service`.
  * No endpoint here is invented: every path below is a documented GitHub
    REST API endpoint (https://docs.github.com/en/rest).
  * Structured metadata only - raw workflow logs are never downloaded.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.core.logging import get_logger, log_extra
from app.services.github.exceptions import GitHubAPIError, GitHubRateLimitError

logger = get_logger("adpo.github.client")

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_RETRIES = 3
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
# Hard ceiling on how long we will sleep to honor a primary rate-limit
# reset before giving up and surfacing an error to the caller.
MAX_RATE_LIMIT_WAIT_SECONDS = 120


class GitHubClient:
    """One client per request/token: `token` is either a user-to-server
    OAuth token or a GitHub App installation token - both authenticate the
    same way (`Authorization: Bearer <token>`), so one client class serves
    both."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.github.com",
        http_client: httpx.Client | None = None,
        sleep_fn=time.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
        self._owns_client = http_client is None
        self._sleep = sleep_fn

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- low-level request handling -----------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._client.request(method, url, headers=self._headers(), params=params)
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "github request timed out, retrying",
                    extra=log_extra(url=url, attempt=attempt, max_retries=MAX_RETRIES),
                )
                self._sleep(_backoff_seconds(attempt))
                continue
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "github request transport error, retrying",
                    extra=log_extra(url=url, attempt=attempt, error=str(exc)),
                )
                self._sleep(_backoff_seconds(attempt))
                continue

            if self._is_rate_limited(response):
                self._handle_rate_limit(response, attempt)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                logger.warning(
                    "github returned retryable status, retrying",
                    extra=log_extra(url=url, status=response.status_code, attempt=attempt),
                )
                self._sleep(_backoff_seconds(attempt))
                continue

            if response.status_code >= 400:
                raise GitHubAPIError(
                    f"GitHub API error {response.status_code} for {method} {url}: {response.text[:300]}",
                    status_code=response.status_code,
                    url=url,
                )

            return response

        assert last_exc is not None
        raise GitHubAPIError(f"GitHub API request failed after retries: {last_exc}", status_code=0, url=url)

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            return True
        if response.status_code == 429:  # secondary rate limit
            return True
        return False

    def _handle_rate_limit(self, response: httpx.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            wait_seconds = float(retry_after)
        else:
            reset_header = response.headers.get("X-RateLimit-Reset")
            reset_at = int(reset_header) if reset_header else int(time.time()) + 60
            wait_seconds = max(0.0, reset_at - time.time())

        if wait_seconds > MAX_RATE_LIMIT_WAIT_SECONDS:
            reset_header = response.headers.get("X-RateLimit-Reset")
            raise GitHubRateLimitError(
                f"GitHub rate limit exceeded; reset in {wait_seconds:.0f}s, "
                f"exceeds max wait of {MAX_RATE_LIMIT_WAIT_SECONDS}s",
                reset_at=int(reset_header) if reset_header else None,
            )
        logger.warning(
            "github rate limited, sleeping before retry",
            extra=log_extra(wait_seconds=wait_seconds, attempt=attempt),
        )
        self._sleep(wait_seconds)

    # Defensive cap on Link-header pagination. At per_page=100 this covers
    # 500,000 items - far beyond any realistic single repository's history -
    # while guaranteeing `_paginate` always terminates even against a
    # misbehaving server (or a self-referencing "next" link) instead of
    # looping forever one fast, individually-successful request at a time.
    MAX_PAGES = 5000

    def _paginate(self, path: str, *, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Follows RFC 5988 `Link: rel="next"` headers, yielding items across
        both plain list responses and `{"total_count": N, "<key>": [...]}`
        envelope responses (e.g. `/repos/.../actions/runs`)."""
        query = dict(params or {})
        query.setdefault("per_page", 100)
        next_url: str | None = path
        next_params: dict[str, Any] | None = query

        for _page in range(self.MAX_PAGES):
            if next_url is None:
                return
            response = self._request("GET", next_url, params=next_params)
            payload = response.json()
            items = payload if isinstance(payload, list) else _first_list_value(payload)
            for item in items:
                yield item

            next_url = response.links.get("next", {}).get("url")
            next_params = None  # the "next" Link URL already carries the query string

        if next_url is not None:
            logger.error(
                "pagination exceeded MAX_PAGES, stopping early",
                extra=log_extra(path=path, max_pages=self.MAX_PAGES),
            )

    # -- public API -------------------------------------------------------

    def get_authenticated_user(self) -> dict[str, Any]:
        return self._request("GET", "/user").json()

    def list_user_installations(self) -> list[dict[str, Any]]:
        """GET /user/installations - App installations the signed-in user
        can access, using their user-to-server token."""
        return list(self._paginate("/user/installations"))

    def list_installation_repositories(self) -> list[dict[str, Any]]:
        """GET /installation/repositories - repos reachable by *this*
        client's token, which must be an installation access token."""
        return list(self._paginate("/installation/repositories"))

    def list_workflows(self, owner: str, repo: str) -> list[dict[str, Any]]:
        return list(self._paginate(f"/repos/{owner}/{repo}/actions/workflows"))

    def list_workflow_runs(
        self, owner: str, repo: str, *, workflow_id: int | None = None, per_page: int = 100
    ) -> Iterator[dict[str, Any]]:
        if workflow_id is not None:
            path = f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        else:
            path = f"/repos/{owner}/{repo}/actions/runs"
        yield from self._paginate(path, params={"per_page": per_page})

    def list_jobs_for_run(self, owner: str, repo: str, run_id: int, *, run_attempt: int | None = None) -> list[dict[str, Any]]:
        if run_attempt is not None:
            path = f"/repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{run_attempt}/jobs"
        else:
            path = f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        return list(self._paginate(path))

    def get_workflow_run_attempt(self, owner: str, repo: str, run_id: int, run_attempt: int) -> dict[str, Any]:
        """GET /repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{run_attempt}
        - run-level metadata (status/conclusion/timestamps) for one specific,
        possibly-superseded attempt. `list_workflow_runs` only ever reflects
        each run's *current* (latest) attempt, so a rerun's earlier attempt(s)
        can only be recovered through this per-attempt endpoint."""
        return self._request(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{run_attempt}"
        ).json()


def _first_list_value(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for value in payload.values():
        if isinstance(value, list):
            return value
    return []


def _backoff_seconds(attempt: int) -> float:
    return min(2 ** (attempt - 1) * 0.5, 4.0)
