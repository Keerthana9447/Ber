from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.sales_documents import (
    _apply_document_totals,
    _audit,
    _fail_conflict,
    _next_doc_number,
    _order_output,
    _save_order_lines,
    _settings,
)
from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import Customer, Dealer, DealerPrice, GstRate, HsnSacCode, Product, SalesOrder, State, User
from berev_core.gst import GSTError, LineInput, compute_invoice

router = APIRouter(prefix="/dealer/portal", tags=["dealer portal"])


class DealerOrderLineWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class DealerOrderWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[DealerOrderLineWrite] = Field(min_length=1, max_length=500)


def _dealer(user: User, session: Session) -> Dealer:
    if user.role.name != "Dealer / Distributor":
        raise HTTPException(status_code=403, detail="Dealer portal access is restricted to dealer accounts")
    if user.dealer_id is None:
        raise HTTPException(status_code=403, detail="This user is not linked to a dealer")
    dealer = session.scalar(
        select(Dealer).where(Dealer.id == user.dealer_id, Dealer.status == "active")
    )
    if dealer is None:
        raise HTTPException(status_code=403, detail="Dealer account is inactive or unavailable")
    return dealer


def _dealer_customer(user: User, session: Session) -> tuple[Dealer, Customer]:
    dealer = _dealer(user, session)
    customer = session.scalar(
        select(Customer).where(
            Customer.id == dealer.customer_id,
            Customer.deleted_at.is_(None),
            Customer.is_active.is_(True),
        )
    )
    if customer is None:
        raise HTTPException(status_code=403, detail="Dealer profile is unavailable")
    return dealer, customer


@router.get("/profile")
def dealer_profile(
    user: Annotated[User, Depends(require_permissions("auth:self:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    dealer, customer = _dealer_customer(user, session)
    return {
        "dealer_code": dealer.dealer_code,
        "name": customer.name,
        "territory": dealer.territory,
        "status": dealer.status,
    }


@router.get("/products")
def dealer_products(
    user: Annotated[User, Depends(require_permissions("products:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
) -> dict[str, object]:
    dealer = _dealer(user, session)
    filters = [Product.is_active.is_(True), Product.deleted_at.is_(None)]
    if q.strip():
        term = f"%{q.strip()}%"
        filters.append((Product.name.ilike(term)) | (Product.sku.ilike(term)))
    dealer_price_filter = (
        (DealerPrice.product_id == Product.id) & (DealerPrice.dealer_id == dealer.id)
    )
    total = session.scalar(
        select(func.count(Product.id)).join(DealerPrice, dealer_price_filter).where(*filters)
    ) or 0
    rows = session.execute(
        select(Product, DealerPrice.price)
        .join(DealerPrice, dealer_price_filter)
        .where(*filters)
        .order_by(Product.name, Product.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "product_id": product.id,
                "sku": product.sku,
                "name": product.name,
                "hsn_sac": product.hsn_sac,
                "gst_rate": format(product.gst_rate, "f"),
                "unit": product.unit,
                "price": format(dealer_price if dealer_price is not None else product.selling_price, "f"),
                "has_dealer_price": dealer_price is not None,
            }
            for product, dealer_price in rows
        ],
        "total": total,
    }


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_dealer_order(
    body: DealerOrderWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("auth:self:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    dealer, customer = _dealer_customer(user, session)
    if not customer.state_code or session.get(State, customer.state_code) is None:
        raise HTTPException(status_code=422, detail="Dealer customer must have a valid state_code")

    settings = _settings(session)
    if not settings.state_code:
        raise HTTPException(status_code=422, detail="Company state_code must be configured")
    allowed_rates = set(session.scalars(select(GstRate.rate).where(GstRate.is_active.is_(True))).all())
    inputs: list[LineInput] = []
    details: list[dict[str, object]] = []
    for index, line in enumerate(body.lines):
        product = session.scalar(
            select(Product).where(
                Product.id == line.product_id,
                Product.is_active.is_(True),
                Product.deleted_at.is_(None),
            )
        )
        if product is None:
            raise HTTPException(
                status_code=422,
                detail=[{"loc": ["body", "lines", index, "product_id"], "msg": "Active product not found"}],
            )
        hsn = session.get(HsnSacCode, product.hsn_sac)
        if hsn is None or not hsn.is_active or hsn.kind != ("SAC" if product.is_service else "HSN"):
            raise HTTPException(status_code=422, detail=f"Product {product.name} has an invalid or inactive HSN/SAC")
        price = session.scalar(
            select(DealerPrice.price).where(
                DealerPrice.dealer_id == dealer.id,
                DealerPrice.product_id == product.id,
            )
        )
        if price is None:
            raise HTTPException(
                status_code=409,
                detail=f"No dealer-specific price is configured for product {product.sku}",
            )
        inputs.append(
            LineInput(
                description=product.name,
                hsn_sac=product.hsn_sac,
                quantity=line.quantity,
                unit_price=price,
                gst_rate=product.gst_rate,
                cess_rate=product.cess_rate,
                unit=product.unit,
            )
        )
        details.append({"product_id": product.id})

    try:
        result = compute_invoice(
            inputs,
            settings.state_code,
            customer.state_code,
            allowed_rates=allowed_rates,
        )
    except GSTError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

    order_date = date.today()
    order = SalesOrder(
        number=_next_doc_number(session, settings, "O", "sales_order", order_date),
        customer_id=customer.id,
        order_date=order_date,
        place_of_supply_code=customer.state_code,
        status="draft",
        salesperson_id=user.id,
        branch_id=user.branch_id,
    )
    _apply_document_totals(order, result)
    session.add(order)
    session.flush()
    _save_order_lines(session, order.id, result, details)
    _audit(session, user, request, "create", "sales_order", order.id, None, {
        "number": order.number,
        "customer_id": order.customer_id,
        "date": order.order_date.isoformat(),
        "status": order.status,
        "grand_total": format(order.grand_total, "f"),
    })
    _fail_conflict(session, "Dealer order conflicts with existing data")
    session.refresh(order)
    return _order_output(session, order)


@router.get("/orders")
def list_dealer_orders(
    user: Annotated[User, Depends(require_permissions("auth:self:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    _dealer, customer = _dealer_customer(user, session)
    filters = [SalesOrder.customer_id == customer.id]
    total = session.scalar(select(func.count(SalesOrder.id)).where(*filters)) or 0
    orders = session.scalars(
        select(SalesOrder)
        .where(*filters)
        .order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_order_output(session, order) for order in orders],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/orders/{order_id}")
def get_dealer_order(
    order_id: int,
    user: Annotated[User, Depends(require_permissions("auth:self:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    _dealer, customer = _dealer_customer(user, session)
    order = session.scalar(
        select(SalesOrder).where(
            SalesOrder.id == order_id,
            SalesOrder.customer_id == customer.id,
        )
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return _order_output(session, order)


@router.get("/statement")
def dealer_statement(
    user: Annotated[User, Depends(require_permissions("auth:self:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    _dealer, customer = _dealer_customer(user, session)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="Statement start date must not be after end date")
    from app.api.v1.payments import customer_ledger

    return customer_ledger(
        customer.id,
        user,
        session,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
