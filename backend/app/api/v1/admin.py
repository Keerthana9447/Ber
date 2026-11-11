from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.auth.dependencies import require_permissions
from app.auth.security import hash_password
from app.db.session import get_db
from app.models import AuditLog, LoginAudit, Notification, PasswordResetToken, RefreshToken, Role, User
from app.services.audit import record_audit, redact_audit_values

router = APIRouter(tags=["administration"])


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=1, max_length=255)
    role_id: int = Field(gt=0)
    temporary_password: str = Field(min_length=12, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("valid email address required")
        return normalized

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name is required")
        return normalized


class RoleAssignment(BaseModel):
    role_id: int = Field(gt=0)


class UserSummary(BaseModel):
    id: int
    email: str
    full_name: str
    role_id: int
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime


class UserPage(BaseModel):
    items: list[UserSummary]
    total: int


class RoleSummary(BaseModel):
    id: int
    name: str
    description: str | None
    permissions: list[str]


class RolePage(BaseModel):
    items: list[RoleSummary]


class AuditLogItem(BaseModel):
    id: int
    user_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    old_values: dict | None
    new_values: dict | None
    ip_address: str | None
    created_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogItem]


class LoginAuditItem(BaseModel):
    id: int
    user_id: int | None
    email_attempted: str | None
    success: bool
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


class LoginAuditPage(BaseModel):
    items: list[LoginAuditItem]


def _user_summary(user: User) -> UserSummary:
    return UserSummary(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role_id=user.role_id,
        role=user.role.name,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _has_admin(user: User) -> bool:
    return user.role.name in {"Admin", "Super Admin"}


def _other_active_admin_exists(session: Session, user_id: int) -> bool:
    return session.scalar(
        select(User.id)
        .join(Role, Role.id == User.role_id)
        .where(
            User.id != user_id,
            User.is_active.is_(True),
            Role.name.in_(("Admin", "Super Admin")),
        )
        .limit(1)
        .with_for_update()
    ) is not None


@router.get("/users", response_model=UserPage)
def list_users(
    _: Annotated[User, Depends(require_permissions("users:read"))],
    session: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> UserPage:
    total = session.scalar(select(func.count(User.id))) or 0
    users = session.scalars(
        select(User)
        .options(selectinload(User.role))
        .order_by(User.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return UserPage(items=[_user_summary(user) for user in users], total=total)


@router.get("/roles", response_model=RolePage)
def list_roles(
    _: Annotated[User, Depends(require_permissions("roles:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> RolePage:
    roles = session.scalars(
        select(Role).options(selectinload(Role.permissions)).order_by(Role.name)
    ).all()
    return RolePage(
        items=[
            RoleSummary(
                id=role.id,
                name=role.name,
                description=role.description,
                permissions=sorted(permission.code for permission in role.permissions),
            )
            for role in roles
        ]
    )


@router.post("/users", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    request: Request,
    actor: Annotated[User, Depends(require_permissions("users:create"))],
    session: Annotated[Session, Depends(get_db)],
) -> UserSummary:
    role = session.get(Role, body.role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if session.scalar(select(User.id).where(func.lower(User.email) == body.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists")

    user = User(
        email=body.email,
        full_name=body.full_name,
        password_hash=hash_password(body.temporary_password),
        role_id=role.id,
        must_change_password=True,
    )
    session.add(user)
    session.flush()
    record_audit(
        session,
        request,
        actor,
        "user_created",
        "user",
        user.id,
        new_values={"email": user.email, "full_name": user.full_name, "role": role.name},
    )
    session.add(
        Notification(
            user_id=user.id,
            kind="account_created",
            title="Your BEREV ERP account is ready",
            body="An administrator created your account. Change your temporary password after signing in.",
            link="/profile",
        )
    )
    session.commit()
    session.refresh(user)
    return _user_summary(user)


@router.post("/users/{user_id}/role", response_model=UserSummary)
def assign_user_role(
    user_id: int,
    body: RoleAssignment,
    request: Request,
    actor: Annotated[User, Depends(require_permissions("users:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> UserSummary:
    user = session.scalar(
        select(User).options(selectinload(User.role)).where(User.id == user_id).with_for_update()
    )
    role = session.get(Role, body.role_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    old_role = user.role.name
    if _has_admin(user) and role.name not in {"Admin", "Super Admin"}:
        if not _other_active_admin_exists(session, user.id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot remove the last active administrator")
    user.role_id = role.id
    user.updated_at = datetime.now(timezone.utc)
    record_audit(
        session,
        request,
        actor,
        "user_role_assigned",
        "user",
        user.id,
        old_values={"role": old_role},
        new_values={"role": role.name},
    )
    session.commit()
    session.refresh(user)
    return _user_summary(user)


@router.post("/users/{user_id}/deactivate", response_model=UserSummary)
def deactivate_user(
    user_id: int,
    request: Request,
    actor: Annotated[User, Depends(require_permissions("users:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> UserSummary:
    user = session.scalar(
        select(User).options(selectinload(User.role)).where(User.id == user_id).with_for_update()
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if actor.id == user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot deactivate your own account")
    if not user.is_active:
        return _user_summary(user)
    if _has_admin(user) and not _other_active_admin_exists(session, user.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot deactivate the last active administrator")

    now = datetime.now(timezone.utc)
    user.is_active = False
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
        actor,
        "user_deactivated",
        "user",
        user.id,
        old_values={"is_active": True},
        new_values={"is_active": False},
    )
    session.commit()
    session.refresh(user)
    return _user_summary(user)


@router.get("/audit-logs", response_model=AuditLogPage)
def list_audit_logs(
    _: Annotated[User, Depends(require_permissions("audit:read"))],
    session: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, gt=0),
    entity_type: str | None = Query(default=None, max_length=100),
    action: str | None = Query(default=None, max_length=100),
) -> AuditLogPage:
    query = select(AuditLog)
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if action:
        query = query.where(AuditLog.action == action)
    rows = session.scalars(query.order_by(AuditLog.id.desc()).limit(limit)).all()
    return AuditLogPage(
        items=[
            AuditLogItem(
                id=row.id,
                user_id=row.user_id,
                action=row.action,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                old_values=redact_audit_values(row.old_values),
                new_values=redact_audit_values(row.new_values),
                ip_address=str(row.ip_address) if row.ip_address is not None else None,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@router.get("/login-audit", response_model=LoginAuditPage)
def list_login_audit(
    _: Annotated[User, Depends(require_permissions("audit:read"))],
    session: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, gt=0),
) -> LoginAuditPage:
    query = select(LoginAudit)
    if before_id is not None:
        query = query.where(LoginAudit.id < before_id)
    rows = session.scalars(query.order_by(LoginAudit.id.desc()).limit(limit)).all()
    return LoginAuditPage(
        items=[
            LoginAuditItem(
                id=row.id,
                user_id=row.user_id,
                email_attempted=row.email_attempted,
                success=row.success,
                ip_address=str(row.ip_address) if row.ip_address is not None else None,
                user_agent=row.user_agent,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )
