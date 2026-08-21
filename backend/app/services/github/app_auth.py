"""GitHub App authentication primitives.

Two separate credential types, per GitHub App design:

1. App JWT   - signed with the App's private key (RS256), proves "I am
               this App," valid for at most 10 minutes. Used only to mint
               installation tokens; never used to call data endpoints
               directly.
2. Installation access token - minted server-to-server using the App JWT,
               scoped to exactly the repos + permissions the installation
               was granted, expires in 1 hour. This is what actually reads
               Actions data. Never persisted - re-minted on demand and
               cached in memory for its lifetime.

The user-to-server OAuth token (who is logged in) is handled separately in
`app.api.v1.auth` / `app.core.security` and stored encrypted in
`GitHubToken`; it is deliberately not minted here.
"""
from __future__ import annotations

import time

import httpx
import jwt

from app.config import Settings
from app.services.github.exceptions import GitHubAuthError


def build_app_jwt(settings: Settings, *, now: int | None = None) -> str:
    if not settings.github_app_id:
        raise GitHubAuthError("GITHUB_APP_ID is not configured")
    private_key = settings.github_app_private_key_pem
    if not private_key:
        raise GitHubAuthError("GITHUB_APP_PRIVATE_KEY(_PATH) is not configured")

    issued_at = now if now is not None else int(time.time())
    payload = {
        "iat": issued_at - 60,  # allow for clock drift
        "exp": issued_at + (9 * 60),  # GitHub allows up to 10 minutes
        "iss": settings.github_app_id,
    }
    try:
        return jwt.encode(payload, private_key, algorithm="RS256")
    except (ValueError, jwt.InvalidKeyError) as exc:
        raise GitHubAuthError(f"invalid GitHub App private key: {exc}") from exc


class InstallationTokenCache:
    """In-memory cache of installation access tokens, keyed by installation
    id. GitHub tokens are valid for 1 hour; we refresh 5 minutes early."""

    _REFRESH_MARGIN_SECONDS = 300

    def __init__(self) -> None:
        self._tokens: dict[int, tuple[str, float]] = {}

    def get(self, installation_id: int) -> str | None:
        entry = self._tokens.get(installation_id)
        if entry is None:
            return None
        token, expires_at = entry
        if time.time() >= expires_at - self._REFRESH_MARGIN_SECONDS:
            return None
        return token

    def set(self, installation_id: int, token: str, expires_at_epoch: float) -> None:
        self._tokens[installation_id] = (token, expires_at_epoch)


_installation_token_cache = InstallationTokenCache()


def get_installation_token(
    settings: Settings, installation_id: int, *, http_client: httpx.Client | None = None
) -> str:
    """Returns a valid installation access token, minting a new one via the
    GitHub API if the cached one is missing or near expiry."""
    cached = _installation_token_cache.get(installation_id)
    if cached is not None:
        return cached

    app_jwt = build_app_jwt(settings)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=15.0)
    try:
        response = client.post(
            f"{settings.github_api_base_url}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    finally:
        if owns_client:
            client.close()

    if response.status_code == 401:
        raise GitHubAuthError("GitHub rejected the App JWT (check GITHUB_APP_ID/private key)")
    if response.status_code == 404:
        raise GitHubAuthError(f"installation {installation_id} not found or App was uninstalled")
    if response.status_code >= 400:
        raise GitHubAuthError(
            f"failed to mint installation token: {response.status_code} {response.text[:300]}"
        )

    data = response.json()
    token = data["token"]
    # expires_at is an ISO8601 string; convert to epoch for the cache.
    from datetime import datetime

    expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
    _installation_token_cache.set(installation_id, token, expires_at)
    return token
