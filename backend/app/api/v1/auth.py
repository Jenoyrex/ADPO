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
from app.core.security import SESSION_COOKIE_NAME, create_session_cookie, encrypt_token
from app.models.github_account import GitHubInstallation, GitHubToken
from app.models.user import User
from app.schemas.user import UserOut
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
    code: str,
    state: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    cookie_state = request.cookies.get(STATE_COOKIE_NAME)
    if not cookie_state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing oauth state cookie")
    try:
        expected_state = _state_serializer(settings).loads(cookie_state, max_age=STATE_MAX_AGE_SECONDS)
    except BadSignature as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid oauth state") from exc
    if not secrets.compare_digest(expected_state, state):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "oauth state mismatch (possible CSRF)")

    with httpx.Client(timeout=15.0) as http:
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
    token_data = token_response.json()
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

    session_cookie = create_session_cookie(user.id)
    redirect = Response(status_code=status.HTTP_302_FOUND)
    redirect.headers["Location"] = settings.frontend_success_redirect_url
    redirect.delete_cookie(STATE_COOKIE_NAME)
    redirect.set_cookie(SESSION_COOKIE_NAME, session_cookie, httponly=True, samesite="lax", secure=False)
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
    if setup_action not in ("install", "update"):
        redirect = Response(status_code=status.HTTP_302_FOUND)
        redirect.headers["Location"] = settings.frontend_success_redirect_url
        return redirect

    installation = db.execute(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    ).scalar_one_or_none()

    try:
        with GitHubClient(
            _mint_installation_token_for_setup(settings, installation_id), base_url=settings.github_api_base_url
        ):
            pass  # token mint succeeding is enough to prove the installation is valid
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not verify installation: {exc}") from exc

    with httpx.Client(timeout=15.0) as http:
        app_jwt_response = http.get(
            f"{settings.github_api_base_url}/app/installations/{installation_id}",
            headers={"Authorization": f"Bearer {_app_jwt(settings)}", "Accept": "application/vnd.github+json"},
        )
    account = app_jwt_response.json().get("account", {})

    if installation is None:
        installation = GitHubInstallation(
            installation_id=installation_id,
            account_login=account.get("login", "unknown"),
            account_type=account.get("type", "User"),
            connected_by_user_id=user.id,
        )
        db.add(installation)
    else:
        installation.account_login = account.get("login", installation.account_login)
        installation.account_type = account.get("type", installation.account_type)

    db.commit()

    redirect = Response(status_code=status.HTTP_302_FOUND)
    redirect.headers["Location"] = settings.frontend_success_redirect_url
    return redirect


def _app_jwt(settings: Settings) -> str:
    from app.services.github.app_auth import build_app_jwt

    return build_app_jwt(settings)


def _mint_installation_token_for_setup(settings: Settings, installation_id: int) -> str:
    from app.services.github.app_auth import get_installation_token

    return get_installation_token(settings, installation_id)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout")
def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
