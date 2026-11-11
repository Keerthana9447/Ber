from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import Settings

_PASSWORDS = PasswordHasher()
_DUMMY_PASSWORD_HASH = _PASSWORDS.hash(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


def hash_password(password: str) -> str:
    if not password or len(password) > 1024:
        raise ValueError("password must contain 1 to 1024 characters")
    return _PASSWORDS.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password or len(password) > 1024:
        return False
    try:
        return _PASSWORDS.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def dummy_password_verify(password: str) -> None:
    verify_password(password, _DUMMY_PASSWORD_HASH)


def _encode_token(
    settings: Settings,
    subject: int,
    token_type: str,
    expires_at: datetime,
    now: datetime,
) -> str:
    return jwt.encode(
        {
            "sub": str(subject),
            "typ": token_type,
            "jti": str(uuid4()),
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": expires_at,
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def create_token_pair(
    settings: Settings,
    subject: int,
    now: datetime | None = None,
) -> TokenPair:
    issued_at = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    access_expiry = issued_at + timedelta(minutes=settings.access_token_minutes)
    refresh_expiry = issued_at + timedelta(days=settings.refresh_token_days)
    return TokenPair(
        access_token=_encode_token(settings, subject, "access", access_expiry, issued_at),
        refresh_token=_encode_token(settings, subject, "refresh", refresh_expiry, issued_at),
        access_expires_at=access_expiry,
        refresh_expires_at=refresh_expiry,
    )


def decode_token(
    settings: Settings,
    token: str,
    expected_type: str,
) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "iss", "jti", "sub", "typ"]},
        )
    except jwt.InvalidTokenError:
        return None
    if payload.get("typ") != expected_type:
        return None
    return payload


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def failed_login_state(
    prior_attempts: int,
    now: datetime,
    max_attempts: int,
    lockout: timedelta,
) -> tuple[int, datetime | None]:
    attempts = prior_attempts + 1
    locked_until = now + lockout if attempts >= max_attempts else None
    return attempts, locked_until


def permissions_satisfy(granted: set[str], required: set[str]) -> bool:
    return required.issubset(granted)
