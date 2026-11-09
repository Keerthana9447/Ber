from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User

_SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "refresh_token",
    "reset_token",
    "token",
    "token_hash",
    "secret",
    "smtp_password",
}


def redact_audit_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.casefold() in _SENSITIVE_KEYS else redact_audit_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_audit_values(item) for item in value]
    return value


def record_audit(
    session: Session,
    request: Request,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: int | None,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=redact_audit_values(old_values),
            new_values=redact_audit_values(new_values),
            ip_address=request.client.host if request.client else None,
        )
    )
