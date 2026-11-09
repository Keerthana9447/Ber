from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.auth.dependencies import require_permissions
from app.auth.security import (
    create_token_pair,
    decode_token,
    dummy_password_verify,
    failed_login_state,
    hash_password,
    token_fingerprint,
    verify_password,
)
from app.config import Settings
from app.db.session import get_db
from app.models import LoginAudit, PasswordResetToken, RefreshToken, Role, User
from app.services.audit import record_audit
from app.services.notifications import send_password_reset_email

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("valid email address required")
        return normalized


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=8192)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("valid email address required")
        return normalized


class PasswordResetCompleteRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=12, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    permissions: list[str]
    must_change_password: bool


def _token_response(access_token: str, refresh_token: str, expires_at: datetime) -> TokenResponse:
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


def _record_login(
    session: Session,
    user: User | None,
    email: str,
    succeeded: bool,
    request: Request,
) -> None:
    session.add(
        LoginAudit(
            user_id=user.id if user else None,
            email_attempted=email,
            success=succeeded,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:1024],
        )
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    settings: Settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    user = session.scalar(
        select(User)
        .options(selectinload(User.role).selectinload(Role.permissions))
        .where(func.lower(User.email) == body.email)
        .with_for_update()
    )
    if user is None:
        dummy_password_verify(body.password)
        _record_login(session, None, body.email, False, request)
        session.commit()
        raise _unauthorized()

    if user.locked_until is not None and user.locked_until > now:
        _record_login(session, user, body.email, False, request)
        session.commit()
        raise _unauthorized()

    password_matches = verify_password(body.password, user.password_hash)
    if not user.is_active or not password_matches:
        attempts_before = 0 if user.locked_until is not None else user.failed_login_count
        user.failed_login_count, user.locked_until = failed_login_state(
            attempts_before,
            now,
            settings.login_max_attempts,
            timedelta(minutes=settings.login_lockout_minutes),
        )
        _record_login(session, user, body.email, False, request)
        session.commit()
        raise _unauthorized()

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    pair = create_token_pair(settings, user.id, now)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_fingerprint(pair.refresh_token),
            expires_at=pair.refresh_expires_at,
        )
    )
    _record_login(session, user, body.email, True, request)
    session.commit()
    return _token_response(pair.access_token, pair.refresh_token, pair.access_expires_at)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    settings: Settings = request.app.state.settings
    payload = decode_token(settings, body.refresh_token, "refresh")
    if payload is None:
        raise _unauthorized()
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise _unauthorized() from None
    now = datetime.now(timezone.utc)
    old_token = session.scalar(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.token_hash == token_fingerprint(body.refresh_token),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        .with_for_update()
    )
    user = session.get(User, user_id)
    if old_token is None or user is None or not user.is_active:
        raise _unauthorized()
    old_token.revoked_at = now
    pair = create_token_pair(settings, user.id, now)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_fingerprint(pair.refresh_token),
            expires_at=pair.refresh_expires_at,
        )
    )
    session.commit()
    return _token_response(pair.access_token, pair.refresh_token, pair.access_expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    body: RefreshRequest,
    request: Request,
    user: Annotated[User, Depends(require_permissions("auth:self:logout"))],
    session: Annotated[Session, Depends(get_db)],
) -> None:
    payload = decode_token(request.app.state.settings, body.refresh_token, "refresh")
    if payload is None or payload.get("sub") != str(user.id):
        raise _unauthorized()
    token = session.scalar(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.token_hash == token_fingerprint(body.refresh_token),
            RefreshToken.revoked_at.is_(None),
        )
    )
    if token is not None:
        token.revoked_at = datetime.now(timezone.utc)
        session.commit()


@router.get("/me", response_model=UserResponse)
def me(
    user: Annotated[User, Depends(require_permissions("auth:self:read"))],
) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.name,
        permissions=sorted(permission.code for permission in user.role.permissions),
        must_change_password=user.must_change_password,
    )


@router.post("/password-change", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    user: Annotated[User, Depends(require_permissions("auth:self:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> None:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    now = datetime.now(timezone.utc)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.updated_at = now
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    record_audit(
        session,
        request,
        user,
        "password_changed",
        "user",
        user.id,
        new_values={"password_changed": True},
    )
    session.commit()


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    generic_response = {"message": "If the account is eligible, password reset instructions will be sent."}
    if not settings.smtp_configured:
        return generic_response

    user = session.scalar(
        select(User).where(func.lower(User.email) == body.email, User.is_active.is_(True))
    )
    if user is None:
        dummy_password_verify(secrets.token_urlsafe(24))
        return generic_response

    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_fingerprint(raw_token),
            expires_at=now + timedelta(minutes=settings.password_reset_minutes),
        )
    )
    session.commit()
    try:
        send_password_reset_email(settings, user.email, raw_token)
    except Exception:
        session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.token_hash == token_fingerprint(raw_token),
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(timezone.utc))
        )
        session.commit()
    return generic_response


@router.post("/password-reset/complete", status_code=status.HTTP_204_NO_CONTENT)
def complete_password_reset(
    body: PasswordResetCompleteRequest,
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> None:
    now = datetime.now(timezone.utc)
    token = session.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == token_fingerprint(body.token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .with_for_update()
    )
    if token is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid or expired")
    user = session.scalar(
        select(User).where(User.id == token.user_id, User.is_active.is_(True)).with_for_update()
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid or expired")
    token.used_at = now
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None
    user.updated_at = now
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != token.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    record_audit(
        session,
        request,
        None,
        "password_reset",
        "user",
        user.id,
        new_values={"password_reset": True},
    )
    session.commit()
