from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import Customer, Dealer, User
from app.services.audit import record_audit

router = APIRouter(tags=["dealers"])
DealerStatus = Literal["pending", "active", "suspended", "terminated"]
DEALER_CUSTOMER_TYPES = ("dealer", "distributor", "franchise")


class DealerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dealer_code: str = Field(min_length=1, max_length=64)
    customer_id: int = Field(gt=0)
    territory: str | None = Field(default=None, max_length=255)
    status: DealerStatus = "pending"

    @field_validator("dealer_code")
    @classmethod
    def normalize_dealer_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("dealer_code must not be blank")
        return normalized

    @field_validator("territory")
    @classmethod
    def normalize_territory(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class DealerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dealer_code: str = Field(min_length=1, max_length=64)
    territory: str | None = Field(default=None, max_length=255)
    status: DealerStatus

    @field_validator("dealer_code")
    @classmethod
    def normalize_dealer_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("dealer_code must not be blank")
        return normalized

    @field_validator("territory")
    @classmethod
    def normalize_territory(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class DealerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dealer_code: str
    customer_id: int
    customer_code: str
    name: str
    customer_type: str
    phone: str | None
    email: str | None
    city: str | None
    territory: str | None
    status: DealerStatus
    created_at: datetime


class DealerPage(BaseModel):
    items: list[DealerResponse]
    total: int


class EligibleCustomer(BaseModel):
    id: int
    code: str
    name: str
    customer_type: str
    city: str | None


def _dealer_response(dealer: Dealer, customer: Customer) -> DealerResponse:
    return DealerResponse(
        id=dealer.id,
        dealer_code=dealer.dealer_code,
        customer_id=customer.id,
        customer_code=customer.code,
        name=customer.name,
        customer_type=customer.customer_type,
        phone=customer.phone,
        email=customer.email,
        city=customer.city,
        territory=dealer.territory,
        status=dealer.status,
        created_at=dealer.created_at,
    )


def _snapshot(dealer: Dealer) -> dict[str, object]:
    return {
        "dealer_code": dealer.dealer_code,
        "customer_id": dealer.customer_id,
        "territory": dealer.territory,
        "status": dealer.status,
    }


def _get_dealer(session: Session, dealer_id: int) -> tuple[Dealer, Customer]:
    row = session.execute(
        select(Dealer, Customer)
        .join(Customer, Customer.id == Dealer.customer_id)
        .where(Dealer.id == dealer_id, Customer.deleted_at.is_(None))
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dealer not found")
    return row


def _commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dealer code or customer is already assigned",
        ) from None


@router.get("/dealers", response_model=DealerPage)
def list_dealers(
    _: Annotated[User, Depends(require_permissions("dealers:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    dealer_status: DealerStatus | None = Query(default=None, alias="status"),
) -> DealerPage:
    filters = [Customer.deleted_at.is_(None)]
    search = q.strip()
    if search:
        term = f"%{search}%"
        filters.append(
            or_(
                Dealer.dealer_code.ilike(term),
                Customer.code.ilike(term),
                Customer.name.ilike(term),
                Dealer.territory.ilike(term),
            )
        )
    if dealer_status is not None:
        filters.append(Dealer.status == dealer_status)

    base = select(Dealer).join(Customer, Customer.id == Dealer.customer_id).where(*filters)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.execute(
        select(Dealer, Customer)
        .join(Customer, Customer.id == Dealer.customer_id)
        .where(*filters)
        .order_by(Customer.name, Dealer.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return DealerPage(items=[_dealer_response(dealer, customer) for dealer, customer in rows], total=total)


@router.get("/dealers/eligible-customers", response_model=list[EligibleCustomer])
def list_eligible_customers(
    _: Annotated[User, Depends(require_permissions("dealers:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> list[EligibleCustomer]:
    customers = session.scalars(
        select(Customer)
        .where(
            Customer.deleted_at.is_(None),
            Customer.is_active.is_(True),
            Customer.customer_type.in_(DEALER_CUSTOMER_TYPES),
            ~select(Dealer.id).where(Dealer.customer_id == Customer.id).exists(),
        )
        .order_by(Customer.name, Customer.id)
    ).all()
    return [
        EligibleCustomer(
            id=customer.id,
            code=customer.code,
            name=customer.name,
            customer_type=customer.customer_type,
            city=customer.city,
        )
        for customer in customers
    ]


@router.post("/dealers", response_model=DealerResponse, status_code=status.HTTP_201_CREATED)
def create_dealer(
    body: DealerCreate,
    request: Request,
    user: Annotated[User, Depends(require_permissions("dealers:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> DealerResponse:
    customer = session.get(Customer, body.customer_id)
    if (
        customer is None
        or customer.deleted_at is not None
        or not customer.is_active
        or customer.customer_type not in DEALER_CUSTOMER_TYPES
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Choose an active dealer, distributor, or franchise customer",
        )
    if session.scalar(select(Dealer.id).where(Dealer.customer_id == customer.id)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Customer already has a dealer profile")

    dealer = Dealer(
        dealer_code=body.dealer_code,
        customer_id=customer.id,
        territory=body.territory,
        status=body.status,
    )
    session.add(dealer)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dealer code or customer is already assigned",
        ) from None
    record_audit(
        session,
        request,
        user,
        "create",
        "dealer",
        dealer.id,
        new_values=_snapshot(dealer),
    )
    _commit_or_conflict(session)
    session.refresh(dealer)
    return _dealer_response(dealer, customer)


@router.put("/dealers/{dealer_id}", response_model=DealerResponse)
def update_dealer(
    dealer_id: int,
    body: DealerUpdate,
    request: Request,
    user: Annotated[User, Depends(require_permissions("dealers:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> DealerResponse:
    dealer, customer = _get_dealer(session, dealer_id)
    old_values = _snapshot(dealer)
    dealer.dealer_code = body.dealer_code
    dealer.territory = body.territory
    dealer.status = body.status
    record_audit(
        session,
        request,
        user,
        "update",
        "dealer",
        dealer.id,
        old_values=old_values,
        new_values=_snapshot(dealer),
    )
    _commit_or_conflict(session)
    session.refresh(dealer)
    return _dealer_response(dealer, customer)
