from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permissions
from app.db.session import get_db
from app.models import AuditLog, CompanySettings, State, User
from berev_core.gst import is_valid_gstin
from berev_core.numbering import format_invoice_number

router = APIRouter(tags=["settings"])
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


class SettingsWrite(BaseModel):
    legal_name: str = Field(min_length=1, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=2000)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    gstin: str | None = Field(default=None, max_length=32)
    pan: str | None = Field(default=None, max_length=16)
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
    bank_name: str | None = Field(default=None, max_length=255)
    bank_account_no: str | None = Field(default=None, max_length=128)
    bank_ifsc: str | None = Field(default=None, max_length=32)
    upi_id: str | None = Field(default=None, max_length=255)
    logo_path: str | None = Field(default=None, max_length=1000)
    invoice_prefix: str = Field(min_length=1, max_length=16)
    fy_start_month: int = Field(ge=1, le=12)
    allow_invoice_issue_without_approval: bool = False
    terms_and_conditions: str | None = Field(default=None, max_length=5000)
    authorized_signatory: str | None = Field(default=None, max_length=255)

    @field_validator(
        "trade_name",
        "address",
        "phone",
        "email",
        "gstin",
        "pan",
        "state_code",
        "bank_name",
        "bank_account_no",
        "bank_ifsc",
        "upi_id",
        "logo_path",
        "terms_and_conditions",
        "authorized_signatory",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("legal_name")
    @classmethod
    def normalize_legal_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("legal_name must not be blank")
        return normalized

    @field_validator("invoice_prefix")
    @classmethod
    def normalize_invoice_prefix(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("invoice_prefix must not be blank")
        if not re.fullmatch(r"[A-Za-z0-9/-]+", normalized):
            raise ValueError("invoice_prefix may contain only letters, digits, '-' and '/'")
        return normalized

    @field_validator("gstin", "pan", "state_code", "bank_ifsc")
    @classmethod
    def normalize_identifiers(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        return value.casefold() if value is not None else None

    @model_validator(mode="after")
    def validate_company_identifiers(self) -> SettingsWrite:
        if self.pan is not None and not PAN_PATTERN.fullmatch(self.pan):
            raise ValueError("PAN must be 10 characters in the format AAAAA9999A")
        if self.gstin is not None and (
            self.state_code is None or not is_valid_gstin(self.gstin, self.state_code)
        ):
            raise ValueError("GSTIN is invalid or its state prefix does not match state_code")
        try:
            for marker in ("T", "B", "P", "C"):
                format_invoice_number(
                    self.invoice_prefix + marker,
                    date(2026, 4, 1),
                    1,
                    fy_start_month=self.fy_start_month,
                )
        except ValueError as error:
            raise ValueError(f"invoice_prefix cannot produce an invoice number within 16 characters: {error}") from None
        return self


class SettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    legal_name: str
    trade_name: str | None
    address: str | None
    phone: str | None
    email: str | None
    gstin: str | None
    pan: str | None
    state_code: str | None
    bank_name: str | None
    bank_account_no: str | None
    bank_ifsc: str | None
    upi_id: str | None
    logo_path: str | None
    invoice_prefix: str
    fy_start_month: int
    allow_invoice_issue_without_approval: bool
    terms_and_conditions: str | None
    authorized_signatory: str | None
    updated_at: datetime | None


def _values(settings: CompanySettings | None) -> dict[str, object]:
    if settings is None:
        return {
            "id": 1,
            "legal_name": "",
            "trade_name": None,
            "address": None,
            "phone": None,
            "email": None,
            "gstin": None,
            "pan": None,
            "state_code": None,
            "bank_name": None,
            "bank_account_no": None,
            "bank_ifsc": None,
            "upi_id": None,
            "logo_path": None,
            "invoice_prefix": "",
            "fy_start_month": 4,
            "allow_invoice_issue_without_approval": False,
            "terms_and_conditions": None,
            "authorized_signatory": None,
            "updated_at": None,
        }
    return SettingsResponse.model_validate(settings).model_dump()


def _audit_values(settings: CompanySettings) -> dict[str, object]:
    excluded = {"bank_name", "bank_account_no", "bank_ifsc", "upi_id"}
    return {
        field: getattr(settings, field)
        for field in SettingsWrite.model_fields
        if field not in excluded
    }


def _require_readiness_permission(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    granted = {permission.code for permission in user.role.permissions}
    allowed = {"settings:read", "sales:read", "sales:create", "sales:update"}
    if not allowed.intersection(granted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return user


@router.get("/settings", response_model=SettingsResponse)
def get_settings(
    user: Annotated[User, Depends(require_permissions("settings:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    del user
    settings = session.get(CompanySettings, 1)
    return _values(settings)


@router.put("/settings", response_model=SettingsResponse)
def update_settings(
    body: SettingsWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("settings:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> SettingsResponse:
    if body.state_code is not None and session.get(State, body.state_code) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown state_code")
    settings = session.scalar(select(CompanySettings).where(CompanySettings.id == 1).with_for_update())
    old_values = _audit_values(settings) if settings is not None else None
    values = body.model_dump()
    now = datetime.now(timezone.utc)
    if settings is None:
        settings = CompanySettings(id=1, **values)
        session.add(settings)
        action = "create"
    else:
        for field, value in values.items():
            setattr(settings, field, value)
        action = "update"
    settings.updated_at = now
    audit_values = {
        field: value
        for field, value in values.items()
        if field not in {"bank_name", "bank_account_no", "bank_ifsc", "upi_id"}
    }
    session.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity_type="company_settings",
            entity_id=1,
            old_values=old_values,
            new_values=audit_values,
            ip_address=request.client.host if request.client else None,
        )
    )
    session.commit()
    session.refresh(settings)
    return SettingsResponse.model_validate(settings)


@router.get("/settings/readiness")
def settings_readiness(
    user: Annotated[User, Depends(_require_readiness_permission)],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    del user
    settings = session.get(CompanySettings, 1)
    required = ("state_code", "gstin", "invoice_prefix", "address")
    missing = [
        field for field in required
        if settings is None or not getattr(settings, field)
    ]
    return {"ready": not missing, "missing_fields": missing}
