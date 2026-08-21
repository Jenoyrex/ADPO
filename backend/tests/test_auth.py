"""Auth-adjacent unit tests: session cookie signing, token encryption at
rest, and GitHub App JWT/installation-token minting (mocked HTTP)."""
from __future__ import annotations

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from app.config import Settings
from app.core.security import create_session_cookie, decrypt_token, encrypt_token, read_session_cookie
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
    settings = _settings(github_app_private_key="")
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
