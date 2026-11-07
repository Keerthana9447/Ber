from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permissions
from app.db.session import get_db
from app.models import AuditLog, Branch, Customer, State, User
from berev_core.gst import is_valid_gstin

router = APIRouter(tags=["customers"])
CustomerType = Literal["individual", "business", "dealer", "distributor", "franchise", "corporate"]
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
PHONE_PATTERN = re.compile(r"^\+?[0-9().\-\s]{7,24}$")


class CustomerWrite(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    customer_type: CustomerType
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    gstin: str | None = Field(default=None, max_length=32)
    pan: str | None = Field(default=None, max_length=16)
    billing_address: str | None = None
    shipping_address: str | None = None
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
    city: str | None = Field(default=None, max_length=128)
    opening_balance: Decimal = Field(default=Decimal("0"), max_digits=14, decimal_places=2)
    credit_limit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    payment_terms_days: int = Field(default=0, ge=0)
    branch_id: int | None = Field(default=None, gt=0)
    is_active: bool = True

    @field_validator("code", "name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        return normalized or None

    @field_validator("gstin", "pan", "state_code")
    @classmethod
    def normalize_identifiers(cls, value: str | None) -> str | None:
        return value.strip().upper() or None if value is not None else None

    @field_validator("phone", "billing_address", "shipping_address", "city")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, value: str | None) -> str | None:
        if value is not None and not PAN_PATTERN.fullmatch(value):
            raise ValueError("PAN must be 10 characters in the format AAAAA9999A")
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is not None and (
            not PHONE_PATTERN.fullmatch(value) or sum(character.isdigit() for character in value) < 7
        ):
            raise ValueError("phone must contain at least 7 digits and only standard phone characters")
        return value

    @model_validator(mode="after")
    def validate_gstin_state(self) -> CustomerWrite:
        if self.gstin is not None and (
            self.state_code is None or not is_valid_gstin(self.gstin, self.state_code)
        ):
            raise ValueError("GSTIN is invalid or its state prefix does not match state_code")
        return self


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    customer_type: CustomerType
    phone: str | None
    email: str | None
    gstin: str | None
    pan: str | None
    billing_address: str | None
    shipping_address: str | None
    state_code: str | None
    city: str | None
    opening_balance: Decimal
    credit_limit: Decimal
    payment_terms_days: int
    branch_id: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("opening_balance", "credit_limit")
    def serialize_money(self, value: Decimal) -> str:
        return f"{value:.2f}"


class CustomerPage(BaseModel):
    items: list[CustomerResponse]
    total: int


def _visible_to(user: User):
    if user.role.name == "Branch Manager":
        if user.branch_id is None:
            return Customer.id == -1
        return Customer.branch_id == user.branch_id
    return Customer.id.is_not(None)


def _snapshot(customer: Customer) -> dict[str, object]:
    return {
        "code": customer.code,
        "name": customer.name,
        "customer_type": customer.customer_type,
        "phone": customer.phone,
        "email": customer.email,
        "gstin": customer.gstin,
        "pan": customer.pan,
        "billing_address": customer.billing_address,
        "shipping_address": customer.shipping_address,
        "state_code": customer.state_code,
        "city": customer.city,
        "opening_balance": str(customer.opening_balance),
        "credit_limit": str(customer.credit_limit),
        "payment_terms_days": customer.payment_terms_days,
        "branch_id": customer.branch_id,
        "is_active": customer.is_active,
    }


def _audit(
    session: Session,
    user: User,
    request: Request,
    action: str,
    customer: Customer,
    old_values: dict[str, object] | None,
) -> None:
    session.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity_type="customer",
            entity_id=customer.id,
            old_values=old_values,
            new_values=_snapshot(customer),
            ip_address=request.client.host if request.client else None,
        )
    )


def _validate_references(session: Session, values: CustomerWrite) -> None:
    if values.state_code is not None and session.get(State, values.state_code) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown state_code")
    if values.branch_id is not None and session.get(Branch, values.branch_id) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown branch_id")


def _require_state_read_permission(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    granted = {permission.code for permission in user.role.permissions}
    if not {"customers:read", "settings:read"}.intersection(granted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return user


def _commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer conflicts with an existing code, GSTIN, or related record",
        ) from None


@router.get("/customers", response_model=CustomerPage)
def list_customers(
    user: Annotated[User, Depends(require_permissions("customers:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    customer_type: CustomerType | None = None,
    state_code: str | None = Query(default=None, min_length=2, max_length=2),
    is_active: bool | None = None,
) -> CustomerPage:
    filters = [Customer.deleted_at.is_(None), _visible_to(user)]
    if customer_type is not None:
        filters.append(Customer.customer_type == customer_type)
    if state_code is not None:
        filters.append(Customer.state_code == state_code.strip().upper())
    if is_active is not None:
        filters.append(Customer.is_active.is_(is_active))
    search = q.strip()
    if search:
        pattern = f"%{search}%"
        filters.append(
            or_(
                Customer.code.ilike(pattern),
                Customer.name.ilike(pattern),
                Customer.phone.ilike(pattern),
                Customer.email.ilike(pattern),
                Customer.gstin.ilike(pattern),
                Customer.city.ilike(pattern),
            )
        )
    total = session.scalar(select(func.count(Customer.id)).where(*filters)) or 0
    items = session.scalars(
        select(Customer)
        .where(*filters)
        .order_by(Customer.name, Customer.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return CustomerPage(items=[CustomerResponse.model_validate(item) for item in items], total=total)


@router.get("/states")
def list_states(
    user: Annotated[User, Depends(_require_state_read_permission)],
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, str]]:
    del user
    states = session.scalars(select(State).order_by(State.name)).all()
    return [{"code": state.code.strip(), "name": state.name} for state in states]


@router.post(
    "/customers",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_customer(
    body: CustomerWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("customers:create"))],
    session: Annotated[Session, Depends(get_db)],
) -> CustomerResponse:
    values = body.model_dump()
    if user.role.name == "Branch Manager":
        if user.branch_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="A branch must be assigned")
        values["branch_id"] = user.branch_id
    _validate_references(session, body.model_copy(update={"branch_id": values["branch_id"]}))
    customer = Customer(**values)
    session.add(customer)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer conflicts with an existing code, GSTIN, or related record",
        ) from None
    _audit(session, user, request, "create", customer, None)
    _commit_or_conflict(session)
    session.refresh(customer)
    return CustomerResponse.model_validate(customer)


@router.get("/customers/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: int,
    user: Annotated[User, Depends(require_permissions("customers:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> CustomerResponse:
    customer = session.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
            _visible_to(user),
        )
    )
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    return CustomerResponse.model_validate(customer)


@router.put("/customers/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: int,
    body: CustomerWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("customers:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> CustomerResponse:
    customer = session.scalar(
        select(Customer)
        .where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
            _visible_to(user),
        )
        .with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    values = body.model_dump()
    if user.role.name == "Branch Manager":
        values["branch_id"] = user.branch_id
    _validate_references(session, body.model_copy(update={"branch_id": values["branch_id"]}))
    old_values = _snapshot(customer)
    for field, value in values.items():
        setattr(customer, field, value)
    customer.updated_at = datetime.now(timezone.utc)
    _audit(session, user, request, "update", customer, old_values)
    _commit_or_conflict(session)
    session.refresh(customer)
    return CustomerResponse.model_validate(customer)


@router.delete("/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(
    customer_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("customers:delete"))],
    session: Annotated[Session, Depends(get_db)],
) -> Response:
    customer = session.scalar(
        select(Customer)
        .where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
            _visible_to(user),
        )
        .with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    old_values = _snapshot(customer)
    now = datetime.now(timezone.utc)
    customer.deleted_at = now
    customer.updated_at = now
    customer.is_active = False
    _audit(session, user, request, "delete", customer, old_values)
    _commit_or_conflict(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
