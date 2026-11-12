"""Auth-adjacent unit tests: session cookie signing, token encryption at
rest, GitHub App JWT/installation-token minting (mocked HTTP), and the
combined OAuth-login / installation callback route."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.config import Settings, get_settings
from app.core.security import create_session_cookie, decrypt_token, encrypt_token, read_session_cookie
from app.main import app
from app.models.github_account import GitHubInstallation
from app.services.github.app_auth import build_app_jwt, get_installation_token
from app.services.github.exceptions import GitHubAuthError


def _generate_test_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("utf-8")


PRIVATE_KEY_PEM = _generate_test_private_key_pem()


def _settings(**overrides) -> Settings:
    base = dict(
        github_app_id="12345",
        github_app_private_key=PRIVATE_KEY_PEM,
        database_url="sqlite:///:memory:",
        token_encryption_key="kY2erqEBpZ8vP9dvdxrKWCbAV3jRz0v3g9k3O1_gL1I=",
        secret_key="test-secret",
    )
    base.update(overrides)
    return Settings(**base)


# -- session cookie ---------------------------------------------------------


def test_session_cookie_roundtrip():
    cookie = create_session_cookie(user_id=42)
    assert read_session_cookie(cookie) == 42


def test_tampered_session_cookie_rejected():
    cookie = create_session_cookie(user_id=42)
    tampered = cookie[:-1] + ("a" if cookie[-1] != "a" else "b")
    assert read_session_cookie(tampered) is None


def test_garbage_session_cookie_rejected():
    assert read_session_cookie("not-a-cookie") is None


# -- token encryption at rest ------------------------------------------------


def test_token_encryption_roundtrip(monkeypatch):
    import app.core.security as security

    security._fernet.cache_clear()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "kY2erqEBpZ8vP9dvdxrKWCbAV3jRz0v3g9k3O1_gL1I=")
    from app.config import get_settings

    get_settings.cache_clear()

    ciphertext = encrypt_token("gho_supersecrettoken")
    assert "gho_supersecrettoken" not in ciphertext
    assert decrypt_token(ciphertext) == "gho_supersecrettoken"
    security._fernet.cache_clear()
    get_settings.cache_clear()


# -- GitHub App JWT -----------------------------------------------------------


def test_build_app_jwt_is_signed_and_scoped_to_app_id():
    settings = _settings()
    token = build_app_jwt(settings, now=1_700_000_000)

    public_key = serialization.load_pem_private_key(PRIVATE_KEY_PEM.encode(), password=None).public_key()
    # `now` is a fixed, deliberately past timestamp so the test is
    # reproducible - decode with exp verification off, since we're
    # checking the claims/signature, not real-time freshness.
    decoded = jwt.decode(token, public_key, algorithms=["RS256"], options={"verify_exp": False})

    assert decoded["iss"] == "12345"
    assert decoded["exp"] - decoded["iat"] <= 600 + 60


def test_build_app_jwt_missing_app_id_raises():
    settings = _settings(github_app_id="")
    with pytest.raises(GitHubAuthError):
        build_app_jwt(settings)


def test_build_app_jwt_missing_private_key_raises():
    settings = _settings(github_app_private_key="", github_app_private_key_path="")
    with pytest.raises(GitHubAuthError):
        build_app_jwt(settings)


# -- installation token minting ----------------------------------------------


@respx.mock
def test_get_installation_token_mints_and_caches(monkeypatch):
    import app.services.github.app_auth as app_auth_module

    app_auth_module._installation_token_cache = app_auth_module.InstallationTokenCache()

    settings = _settings()
    route = respx.post(f"{settings.github_api_base_url}/app/installations/42/access_tokens")
    route.mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_minted", "expires_at": "2099-01-01T00:00:00Z"},
        )
    )

    token = get_installation_token(settings, 42)
    assert token == "ghs_minted"
    assert route.call_count == 1

    # second call within the cache window must not hit the network again
    token_again = get_installation_token(settings, 42)
    assert token_again == "ghs_minted"
    assert route.call_count == 1


@respx.mock
def test_get_installation_token_raises_on_404(monkeypatch):
    import app.services.github.app_auth as app_auth_module

    app_auth_module._installation_token_cache = app_auth_module.InstallationTokenCache()

    settings = _settings()
    respx.post(f"{settings.github_api_base_url}/app/installations/999/access_tokens").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )

    with pytest.raises(GitHubAuthError):
        get_installation_token(settings, 999)


# -- combined OAuth-login / installation callback route ---------------------
#
# GitHub App installs, when "Request user authorization (OAuth) during
# installation" is enabled and no separate Setup URL is configured, redirect
# to the same Callback URL as a normal login, appending installation_id and
# setup_action to it (no `state`, since that leg never visited /login). See
# backend/README.md and app/api/v1/auth.py::github_callback for the full
# rationale.


def _oauth_settings(**overrides) -> Settings:
    base = dict(
        github_app_id="12345",
        github_app_client_id="test-client-id",
        github_app_client_secret="test-client-secret",
        github_app_private_key=PRIVATE_KEY_PEM,
        github_app_name="adpo-test-app",
        github_oauth_redirect_uri="http://localhost:8000/api/v1/auth/github/callback",
        frontend_success_redirect_url="http://localhost:3000/dashboard",
        secret_key="test-oauth-secret",
        database_url="sqlite:///:memory:",
        token_encryption_key="kY2erqEBpZ8vP9dvdxrKWCbAV3jRz0v3g9k3O1_gL1I=",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def oauth_settings(client) -> Settings:
    """Overrides get_settings with a fully-configured, hermetic GitHub App
    config so /github/login doesn't 503 and the state cookie is signed with
    a known key - independent of whatever is in the developer's real .env."""
    settings = _oauth_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    return settings


def _start_login(client) -> str:
    """Drives GET /github/login and returns the `state` GitHub would echo
    back on a real redirect, leaving the signed `adpo_oauth_state` cookie in
    the client's jar - exactly like a real browser mid-flow."""
    resp = client.get("/api/v1/auth/github/login", follow_redirects=False)
    assert resp.status_code == 302
    query = parse_qs(urlparse(resp.headers["location"]).query)
    return query["state"][0]


def _mock_oauth_exchange(settings: Settings, *, github_user_id: int = 12345, login: str = "octocat") -> None:
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "gho_test_token", "token_type": "bearer", "scope": "repo"}
        )
    )
    respx.get(f"{settings.github_api_base_url}/user").mock(
        return_value=httpx.Response(200, json={"id": github_user_id, "login": login, "avatar_url": None, "email": None})
    )


def _fake_installation_deps(monkeypatch, *, token: str = "fake-installation-token") -> None:
    monkeypatch.setattr("app.api.v1.auth.get_installation_token", lambda settings, inst_id, **kw: token)
    monkeypatch.setattr("app.api.v1.auth.build_app_jwt", lambda settings: "fake-app-jwt")

    class FakeGitHubClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("app.api.v1.auth.GitHubClient", FakeGitHubClient)


# -- A: normal OAuth login leg ------------------------------------------------


@respx.mock
def test_oauth_callback_with_valid_state_succeeds(client, oauth_settings):
    state = _start_login(client)
    _mock_oauth_exchange(oauth_settings)

    resp = client.get(f"/api/v1/auth/github/callback?code=abc123&state={state}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == oauth_settings.frontend_success_redirect_url
    assert "adpo_session" in resp.cookies


def test_oauth_callback_missing_state_cookie_is_rejected(client, oauth_settings):
    # No prior /github/login call in this test, so there is no
    # adpo_oauth_state cookie - simulates hitting the callback URL cold.
    resp = client.get("/api/v1/auth/github/callback?code=abc123&state=whatever", follow_redirects=False)
    assert resp.status_code == 400


def test_oauth_callback_state_mismatch_is_rejected(client, oauth_settings):
    _start_login(client)  # sets a real signed cookie

    resp = client.get("/api/v1/auth/github/callback?code=abc123&state=not-the-real-state", follow_redirects=False)

    assert resp.status_code == 400


def test_installation_id_cannot_bypass_state_validation_when_cookie_present(client, oauth_settings):
    # An unrelated installation_id/setup_action tacked onto a request that
    # otherwise carries a valid oauth-state cookie must NOT switch this
    # request onto the state-free installation branch - `state` is still
    # required and still validated.
    _start_login(client)

    resp = client.get(
        "/api/v1/auth/github/callback?code=abc123&installation_id=999&setup_action=install",
        follow_redirects=False,
    )

    assert resp.status_code == 400


# -- B: installation-triggered leg (no state) --------------------------------


@respx.mock
def test_installation_triggered_callback_creates_and_links_installation(authed_client, db_session, user, monkeypatch):
    _fake_installation_deps(monkeypatch)
    settings = get_settings()
    respx.get(f"{settings.github_api_base_url}/app/installations/555000").mock(
        return_value=httpx.Response(200, json={"account": {"login": "Jenoyrex", "type": "User"}})
    )

    resp = authed_client.get(
        "/api/v1/auth/github/callback?code=irrelevant&installation_id=555000&setup_action=install",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == settings.frontend_success_redirect_url

    row = db_session.execute(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == 555000)
    ).scalar_one()
    assert row.account_login == "Jenoyrex"
    assert row.account_type == "User"
    assert row.connected_by_user_id == user.id


@respx.mock
def test_installation_triggered_callback_upserts_existing_installation(
    authed_client, db_session, installation, user, monkeypatch
):
    _fake_installation_deps(monkeypatch)
    settings = get_settings()
    respx.get(f"{settings.github_api_base_url}/app/installations/{installation.installation_id}").mock(
        return_value=httpx.Response(200, json={"account": {"login": "renamed-org", "type": "Organization"}})
    )

    resp = authed_client.get(
        f"/api/v1/auth/github/callback?code=irrelevant&installation_id={installation.installation_id}"
        "&setup_action=update",
        follow_redirects=False,
    )

    assert resp.status_code == 302

    rows = (
        db_session.execute(
            select(GitHubInstallation).where(GitHubInstallation.installation_id == installation.installation_id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].account_login == "renamed-org"
    assert rows[0].connected_by_user_id == user.id


def test_installation_triggered_callback_requires_existing_session(client):
    # No adpo_session cookie: this leg must not use `code` to establish a
    # new identity, so an unauthenticated browser is rejected outright.
    resp = client.get(
        "/api/v1/auth/github/callback?code=irrelevant&installation_id=1&setup_action=install",
        follow_redirects=False,
    )
    assert resp.status_code == 401


def test_installation_link_failure_is_handled_safely(authed_client, monkeypatch):
    def raise_auth_error(settings, inst_id, **kw):
        raise GitHubAuthError("installation not found or App was uninstalled")

    monkeypatch.setattr("app.api.v1.auth.get_installation_token", raise_auth_error)

    resp = authed_client.get(
        "/api/v1/auth/github/callback?code=irrelevant&installation_id=404404&setup_action=install",
        follow_redirects=False,
    )

    assert resp.status_code == 502


@respx.mock
def test_installation_link_never_persists_installation_token(authed_client, db_session, user, monkeypatch):
    _fake_installation_deps(monkeypatch, token="SECRET-INSTALLATION-TOKEN")
    settings = get_settings()
    respx.get(f"{settings.github_api_base_url}/app/installations/777888").mock(
        return_value=httpx.Response(200, json={"account": {"login": "Jenoyrex", "type": "User"}})
    )

    authed_client.get(
        "/api/v1/auth/github/callback?code=irrelevant&installation_id=777888&setup_action=install",
        follow_redirects=False,
    )

    row = db_session.execute(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == 777888)
    ).scalar_one()
    stored_values = [str(v) for v in row.__dict__.values()]
    assert not any("SECRET-INSTALLATION-TOKEN" in v for v in stored_values)


# -- standalone installation callback (unchanged route) ----------------------


@respx.mock
def test_standalone_installation_callback_still_works(authed_client, db_session, user, monkeypatch):
    _fake_installation_deps(monkeypatch)
    settings = get_settings()
    respx.get(f"{settings.github_api_base_url}/app/installations/222333").mock(
        return_value=httpx.Response(200, json={"account": {"login": "Jenoyrex", "type": "User"}})
    )

    resp = authed_client.get(
        "/api/v1/auth/github/installation/callback?installation_id=222333&setup_action=install",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    row = db_session.execute(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == 222333)
    ).scalar_one()
    assert row.connected_by_user_id == user.id
