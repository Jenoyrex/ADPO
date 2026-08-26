"""GitHub App authentication: user login (who is signed in) and App
installation (which repos are reachable) are two separate flows, per the
GitHub App architecture described in backend/README.md."""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import Settings, get_settings
from app.core.logging import get_logger, log_extra
from app.core.security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_session_cookie,
    encrypt_token,
)
from app.models.github_account import GitHubInstallation, GitHubToken
from app.models.user import User
from app.schemas.user import UserOut
from app.services.github.app_auth import build_app_jwt, get_installation_token
from app.services.github.client import GitHubClient
from app.services.github.exceptions import GitHubError

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger("adpo.auth")

STATE_COOKIE_NAME = "adpo_oauth_state"
STATE_MAX_AGE_SECONDS = 600


def _state_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="adpo-oauth-state")


@router.get("/github/login")
def github_login(settings: Settings = Depends(get_settings)) -> Response:
    """Starts the GitHub App user-to-server OAuth flow (identity/login)."""
    if not settings.github_app_client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub App is not configured")

    state = secrets.token_urlsafe(24)
    signed_state = _state_serializer(settings).dumps(state)
    params = {
        "client_id": settings.github_app_client_id,
        "redirect_uri": settings.github_oauth_redirect_uri,
        "state": state,
    }
    redirect = Response(status_code=status.HTTP_302_FOUND)
    redirect.headers["Location"] = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    redirect.set_cookie(
        STATE_COOKIE_NAME, signed_state, max_age=STATE_MAX_AGE_SECONDS, httponly=True, samesite="lax"
    )
    return redirect


@router.get("/github/callback")
def github_callback(
    request: Request,
    code: str,
    state: str | None = None,
    installation_id: int | None = None,
    setup_action: str = "install",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Handles two distinct redirects GitHub sends to this one Callback URL:

    A. Normal login, initiated by our own /github/login: carries `code` +
       `state`, and the browser carries the `adpo_oauth_state` cookie that
       /github/login set. Validated with the existing signed-state check.

    B. GitHub App installation, when "Request user authorization (OAuth)
       during installation" is enabled and no separate Setup URL is
       configured: GitHub appends `installation_id` + `setup_action` to
       this same Callback URL. This leg never went through /github/login,
       so there is no `state` and no `adpo_oauth_state` cookie - by design,
       not by omission.

    Flow B deliberately does NOT use its `code` to establish or switch who
    is logged in: doing so would let an attacker complete their own
    installation/OAuth grant, then trick a *different*, already-logged-in
    victim into opening the resulting callback URL, silently switching the
    victim's session to the attacker's identity (the exact login-CSRF hole
    `state` exists to close). Instead, flow B requires the browser's
    existing ADPO session - the same trust anchor the standalone
    /github/installation/callback endpoint already relies on - and only
    links the installation to that already-authenticated user.
    """
    cookie_state = request.cookies.get(STATE_COOKIE_NAME)

    if installation_id is not None and state is None and cookie_state is None:
        user = get_current_user(request, db, settings)
        _link_installation(db, settings, installation_id=installation_id, setup_action=setup_action, acting_user=user)
        redirect = Response(status_code=status.HTTP_302_FOUND)
        redirect.headers["Location"] = settings.frontend_success_redirect_url
        return redirect

    if not cookie_state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing oauth state cookie")
    if not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing oauth state parameter")
    try:
        expected_state = _state_serializer(settings).loads(cookie_state, max_age=STATE_MAX_AGE_SECONDS)
    except BadSignature as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid oauth state") from exc
    if not secrets.compare_digest(expected_state, state):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "oauth state mismatch (possible CSRF)")

    with httpx.Client(timeout=15.0) as http:
        try:
            token_response = http.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_app_client_id,
                    "client_secret": settings.github_app_client_secret,
                    "code": code,
                    "redirect_uri": settings.github_oauth_redirect_uri,
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("github oauth token exchange request failed", extra=log_extra(error=str(exc)))
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "could not reach GitHub to complete sign-in"
            ) from exc

    try:
        token_data = token_response.json()
    except ValueError as exc:
        logger.warning(
            "github oauth token exchange returned a non-JSON response",
            extra=log_extra(status_code=token_response.status_code),
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "GitHub returned an unexpected response during sign-in"
        ) from exc
    if "error" in token_data:
        logger.warning("github oauth exchange failed", extra=log_extra(error=token_data.get("error")))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"GitHub OAuth error: {token_data['error']}")

    access_token = token_data["access_token"]

    try:
        with GitHubClient(access_token, base_url=settings.github_api_base_url) as client:
            gh_user = client.get_authenticated_user()
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"failed to fetch GitHub user: {exc}") from exc

    user = db.execute(select(User).where(User.github_user_id == gh_user["id"])).scalar_one_or_none()
    if user is None:
        user = User(github_user_id=gh_user["id"], github_login=gh_user["login"])
        db.add(user)
        db.flush()
    user.github_login = gh_user["login"]
    user.avatar_url = gh_user.get("avatar_url")
    user.email = gh_user.get("email")

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    expires_in = token_data.get("expires_in")
    refresh_expires_in = token_data.get("refresh_token_expires_in")

    if user.github_token is None:
        user.github_token = GitHubToken(user_id=user.id, access_token_encrypted="")
    token_row = user.github_token
    token_row.access_token_encrypted = encrypt_token(access_token)
    token_row.refresh_token_encrypted = (
        encrypt_token(token_data["refresh_token"]) if token_data.get("refresh_token") else None
    )
    token_row.token_type = token_data.get("token_type", "bearer")
    token_row.scope = token_data.get("scope")
    token_row.expires_at = now + timedelta(seconds=expires_in) if expires_in else None
    token_row.refresh_token_expires_at = now + timedelta(seconds=refresh_expires_in) if refresh_expires_in else None

    db.commit()

    # GitHub also appends installation_id to *this* branch's redirect when a
    # user installs the App from a fresh, not-yet-authenticated browser (no
    # prior /github/login) - in that case the just-resolved OAuth identity
    # above is who should own the installation.
    if installation_id is not None:
        _link_installation(db, settings, installation_id=installation_id, setup_action=setup_action, acting_user=user)

    session_cookie = create_session_cookie(user.id)
    redirect = Response(status_code=status.HTTP_302_FOUND)
    redirect.headers["Location"] = settings.frontend_success_redirect_url
    redirect.delete_cookie(STATE_COOKIE_NAME)
    redirect.set_cookie(
        SESSION_COOKIE_NAME,
        session_cookie,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        # Frontend and backend live on different registrable domains in
        # production (Vercel / Render), so every API call is a cross-site
        # fetch - SameSite=Lax would never be attached to those. None
        # requires Secure, which is also only valid over the HTTPS
        # production deploys. Local dev keeps Lax/non-Secure so the cookie
        # still works over plain http://localhost.
        samesite="none" if settings.environment == "production" else "lax",
        secure=settings.environment == "production",
    )
    return redirect


@router.get("/github/install")
def github_install(
    settings: Settings = Depends(get_settings), _: User = Depends(get_current_user)
) -> Response:
    """Sends a signed-in user to install/configure the GitHub App on an
    account or org. GitHub redirects back to `/github/installation/callback`
    once they finish."""
    if not settings.github_app_name:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GITHUB_APP_NAME is not configured")
    redirect = Response(status_code=status.HTTP_302_FOUND)
    redirect.headers["Location"] = f"https://github.com/apps/{settings.github_app_name}/installations/new"
    return redirect


@router.get("/github/installation/callback")
def github_installation_callback(
    installation_id: int,
    setup_action: str = "install",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> Response:
    _link_installation(db, settings, installation_id=installation_id, setup_action=setup_action, acting_user=user)
    redirect = Response(status_code=status.HTTP_302_FOUND)
    redirect.headers["Location"] = settings.frontend_success_redirect_url
    return redirect


def _link_installation(
    db: Session,
    settings: Settings,
    *,
    installation_id: int,
    setup_action: str,
    acting_user: User,
) -> GitHubInstallation | None:
    """Verifies a GitHub App installation via the App JWT / installation-token
    mechanisms and upserts the corresponding `GitHubInstallation` row, linked
    to `acting_user`. Shared by both the standalone installation callback and
    the installation-triggered leg of the combined OAuth callback, so the
    verify/link logic exists in exactly one place.

    Returns `None` without touching the database for `setup_action` values
    other than "install"/"update" (e.g. "request", which just means an org
    member asked an owner to approve the install - nothing to link yet).
    Never persists the installation access token minted to verify access.
    """
    if setup_action not in ("install", "update"):
        return None

    installation = db.execute(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    ).scalar_one_or_none()

    try:
        with GitHubClient(get_installation_token(settings, installation_id), base_url=settings.github_api_base_url):
            pass  # token mint succeeding is enough to prove the installation is valid/reachable
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not verify installation: {exc}") from exc

    with httpx.Client(timeout=15.0) as http:
        try:
            app_jwt_response = http.get(
                f"{settings.github_api_base_url}/app/installations/{installation_id}",
                headers={
                    "Authorization": f"Bearer {build_app_jwt(settings)}",
                    "Accept": "application/vnd.github+json",
                },
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "github installation lookup request failed",
                extra=log_extra(installation_id=installation_id, error=str(exc)),
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "could not reach GitHub to verify the installation"
            ) from exc

    try:
        account = app_jwt_response.json().get("account", {})
    except ValueError as exc:
        logger.warning(
            "github installation lookup returned a non-JSON response",
            extra=log_extra(installation_id=installation_id, status_code=app_jwt_response.status_code),
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "GitHub returned an unexpected response while verifying the installation"
        ) from exc

    if installation is None:
        installation = GitHubInstallation(
            installation_id=installation_id,
            account_login=account.get("login", "unknown"),
            account_type=account.get("type", "User"),
            connected_by_user_id=acting_user.id,
        )
        db.add(installation)
    else:
        installation.account_login = account.get("login", installation.account_login)
        installation.account_type = account.get("type", installation.account_type)

    db.commit()
    return installation


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout")
def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
