"""Secret handling: encrypting GitHub tokens at rest and signing the
session cookie. Nothing here ever logs a secret value."""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings

SESSION_COOKIE_NAME = "adpo_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14  # 14 days


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.token_encryption_key
    if not key:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("stored token could not be decrypted") from exc


@lru_cache
def _session_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt="adpo-session")


def create_session_cookie(user_id: int) -> str:
    return _session_serializer().dumps({"user_id": user_id})


def read_session_cookie(cookie_value: str) -> int | None:
    """Returns the user_id encoded in a valid, unexpired session cookie, or
    None if the cookie is missing, tampered with, or expired."""
    try:
        data = _session_serializer().loads(cookie_value, max_age=SESSION_MAX_AGE_SECONDS)
    except BadSignature:
        return None
    return data.get("user_id")
