from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.api.v1.invoices import (
    InvoiceDraftWrite,
    InvoiceLineWrite,
    _audit as _invoice_audit,
    _calculate as _calculate_invoice,
    _save_items,
    _snapshot_parties,
    _apply_result,
    _invoice_snapshot,
)
from app.auth.dependencies import require_permissions
from app.auth.scoping import dealer_customer_scope
from app.db.session import get_db
from app.models import (
    AuditLog,
    CompanySettings,
    Customer,
    DeliveryChallan,
    DeliveryChallanItem,
    GstRate,
    HsnSacCode,
    Invoice,
    InvoiceItem,
    Product,
    ProductSerial,
    Quotation,
    QuotationItem,
    SalesOrder,
    SalesOrderItem,
    State,
    User,
)
from berev_core.gst import InvoiceResult
from berev_core.numbering import format_invoice_number, next_sequence, series_key
from berev_core.words import amount_in_words

router = APIRouter(tags=["sales documents"])


class SalesLineWrite(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    gst_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    cess_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    description: str | None = Field(default=None, max_length=1000)
    unit: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_discount(self) -> SalesLineWrite:
        if self.discount_pct and self.discount_amount:
            raise ValueError("Provide discount_pct or discount_amount, not both")
        return self


class QuotationWrite(BaseModel):
    customer_id: int = Field(gt=0)
    quote_date: date = Field(default_factory=date.today)
    valid_until: date | None = None
    place_of_supply_code: str = Field(min_length=2, max_length=2)
    salesperson_id: int | None = Field(default=None, gt=0)
    branch_id: int | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=5000)
    lines: list[SalesLineWrite] = Field(min_length=1, max_length=500)


class ChallanLineWrite(BaseModel):
    sales_order_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    serial_id: int | None = Field(default=None, gt=0)


class ChallanWrite(BaseModel):
    challan_date: date = Field(default_factory=date.today)
    lines: list[ChallanLineWrite] = Field(min_length=1, max_length=500)


def _fail_conflict(session: Session, detail: str) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from None


def _audit(
    session: Session,
    user: User,
    request: Request,
    action: str,
    entity: str,
    entity_id: int,
    old: dict[str, object] | None,
    new: dict[str, object] | None,
) -> None:
    session.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity_type=entity,
            entity_id=entity_id,
            old_values=old,
            new_values=new,
            ip_address=request.client.host if request.client else None,
        )
    )


def _customer(session: Session, customer_id: int) -> Customer:
    customer = session.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
            Customer.is_active.is_(True),
        )
    )
    if customer is None:
        raise HTTPException(status_code=422, detail="Active customer not found")
    return customer


def _settings(session: Session) -> CompanySettings:
    settings = session.get(CompanySettings, 1)
    if settings is None or not settings.state_code:
        raise HTTPException(status_code=422, detail="Company state_code must be configured")
    return settings


def _snapshot(entity: Quotation | SalesOrder) -> dict[str, object]:
    is_quote = isinstance(entity, Quotation)
    return {
        "number": entity.number,
        "customer_id": entity.customer_id,
        "date": (entity.quote_date if is_quote else entity.order_date).isoformat(),
        "status": entity.status,
        "grand_total": format(entity.grand_total, "f"),
    }


def _next_doc_number(session: Session, settings: CompanySettings, prefix_marker: str, kind: str, on_date: date) -> str:
    prefix = f"{settings.invoice_prefix}{prefix_marker}"
    series = series_key(kind, settings.invoice_prefix, on_date, settings.fy_start_month)
    connection = session.connection().connection.driver_connection
    with connection.cursor() as cursor:
        sequence = next_sequence(cursor, series, "format")
    try:
        return format_invoice_number(prefix, on_date, sequence, fy_start_month=settings.fy_start_month)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Configured invoice prefix is too long for {kind} numbers: {error}") from None


def _calculate_lines(
    session: Session,
    customer_id: int,
    place_of_supply_code: str,
    lines: list[SalesLineWrite],
) -> tuple[InvoiceResult, list[dict[str, object]]]:
    from berev_core.gst import GSTError, LineInput, compute_invoice

    customer = _customer(session, customer_id)
    settings = _settings(session)
    if session.get(State, place_of_supply_code.upper()) is None:
        raise HTTPException(status_code=422, detail="Unknown place_of_supply_code")
    rates = set(session.scalars(select(GstRate.rate).where(GstRate.is_active.is_(True))).all())
    inputs: list[LineInput] = []
    details: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        product = session.scalar(
            select(Product).where(
                Product.id == line.product_id,
                Product.deleted_at.is_(None),
                Product.is_active.is_(True),
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
        rate = line.gst_rate if line.gst_rate is not None else product.gst_rate
        cess = line.cess_rate if line.cess_rate is not None else product.cess_rate
        if rate not in rates:
            raise HTTPException(status_code=422, detail=f"GST rate {rate} for {product.name} is not active")
        inputs.append(
            LineInput(
                description=line.description or product.name,
                hsn_sac=product.hsn_sac,
                quantity=line.quantity,
                unit_price=line.unit_price if line.unit_price is not None else product.selling_price,
                gst_rate=rate,
                discount_pct=line.discount_pct,
                discount_amount=line.discount_amount,
                cess_rate=cess,
                unit=line.unit or product.unit,
            )
        )
        details.append(
            {
                "product_id": product.id,
                "description": line.description or product.name,
                "hsn_sac": product.hsn_sac,
                "unit": line.unit or product.unit,
            }
        )
    try:
        result = compute_invoice(
            inputs,
            settings.state_code,
            place_of_supply_code.upper(),
            allowed_rates=rates,
        )
    except GSTError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return result, details


def _line_response(item: QuotationItem | SalesOrderItem) -> dict[str, object]:
    product = item.product_id
    return {
        "id": item.id,
        "product_id": product,
        "description": item.description,
        "hsn_sac": item.hsn_sac,
        "unit": item.unit,
        "quantity": format(item.quantity, "f"),
        "unit_price": format(item.unit_price, "f"),
        "discount": format(item.discount, "f"),
        "taxable_value": format(item.taxable_value, "f"),
        "gst_rate": format(item.gst_rate, "f"),
        "cess_rate": format(item.cess_rate, "f"),
    }


def _quote_output(session: Session, quote: Quotation) -> dict[str, object]:
    customer = session.get(Customer, quote.customer_id)
    lines = session.scalars(
        select(QuotationItem).where(QuotationItem.quotation_id == quote.id).order_by(QuotationItem.id)
    ).all()
    return {
        **_snapshot(quote),
        "id": quote.id,
        "number": quote.number,
        "quote_date": quote.quote_date.isoformat(),
        "valid_until": quote.valid_until.isoformat() if quote.valid_until else None,
        "place_of_supply_code": quote.place_of_supply_code,
        "customer_name": customer.name if customer else None,
        "salesperson_id": quote.salesperson_id,
        "branch_id": quote.branch_id,
        "taxable_total": format(quote.taxable_total, "f"),
        "tax_total": format(quote.tax_total, "f"),
        "round_off": format(quote.round_off, "f"),
        "grand_total": format(quote.grand_total, "f"),
        "amount_in_words": amount_in_words(quote.grand_total),
        "notes": quote.notes,
        "lines": [_line_response(item) for item in lines],
    }


def _order_output(session: Session, order: SalesOrder) -> dict[str, object]:
    customer = session.get(Customer, order.customer_id)
    lines = session.scalars(
        select(SalesOrderItem).where(SalesOrderItem.sales_order_id == order.id).order_by(SalesOrderItem.id)
    ).all()
    products = {
        product.id: product
        for product in session.scalars(
            select(Product).where(Product.id.in_({line.product_id for line in lines}))
        ).all()
    } if lines else {}
    assigned_rows = session.execute(
        select(DeliveryChallanItem.sales_order_item_id, func.sum(DeliveryChallanItem.quantity))
        .join(DeliveryChallan, DeliveryChallan.id == DeliveryChallanItem.challan_id)
        .where(
            DeliveryChallan.sales_order_id == order.id,
            DeliveryChallan.status != "cancelled",
        )
        .group_by(DeliveryChallanItem.sales_order_item_id)
    ).all()
    assigned = {int(item_id): quantity for item_id, quantity in assigned_rows}
    return {
        **_snapshot(order),
        "id": order.id,
        "number": order.number,
        "order_date": order.order_date.isoformat(),
        "quotation_id": order.quotation_id,
        "place_of_supply_code": order.place_of_supply_code,
        "customer_name": customer.name if customer else None,
        "salesperson_id": order.salesperson_id,
        "branch_id": order.branch_id,
        "taxable_total": format(order.taxable_total, "f"),
        "tax_total": format(order.tax_total, "f"),
        "round_off": format(order.round_off, "f"),
        "grand_total": format(order.grand_total, "f"),
        "amount_in_words": amount_in_words(order.grand_total),
        "lines": [
            {
                **_line_response(item),
                "is_serialized": products[item.product_id].is_serialized,
                "is_service": products[item.product_id].is_service,
                "remaining_quantity": format(item.quantity - assigned.get(item.id, Decimal("0")), "f"),
            }
            for item in lines
        ],
    }


def _challan_output(session: Session, challan: DeliveryChallan) -> dict[str, object]:
    customer = session.get(Customer, challan.customer_id)
    rows = session.execute(
        select(DeliveryChallanItem, SalesOrderItem.description)
        .join(SalesOrderItem, SalesOrderItem.id == DeliveryChallanItem.sales_order_item_id)
        .where(DeliveryChallanItem.challan_id == challan.id)
        .order_by(DeliveryChallanItem.id)
    ).all()
    return {
        "id": challan.id,
        "number": challan.number,
        "sales_order_id": challan.sales_order_id,
        "customer_id": challan.customer_id,
        "customer_name": customer.name if customer else None,
        "challan_date": challan.challan_date.isoformat(),
        "status": challan.status,
        "lines": [
            {
                "sales_order_item_id": item.sales_order_item_id,
                "product_id": item.product_id,
                "serial_id": item.serial_id,
                "description": description,
                "quantity": format(item.quantity, "f"),
            }
            for item, description in rows
        ],
    }


def _save_quote_lines(
    session: Session,
    quote_id: int,
    result: InvoiceResult,
    details: list[dict[str, object]],
) -> None:
    for calculated, detail in zip(result.lines, details, strict=True):
        session.add(
            QuotationItem(
                quotation_id=quote_id,
                product_id=detail["product_id"],
                description=calculated.description,
                hsn_sac=calculated.hsn_sac,
                unit=calculated.unit,
                quantity=calculated.quantity,
                unit_price=calculated.unit_price,
                discount=calculated.discount,
                taxable_value=calculated.taxable,
                gst_rate=calculated.gst_rate,
                cess_rate=calculated.cess_rate,
            )
        )


def _save_order_lines(
    session: Session,
    order_id: int,
    result: InvoiceResult,
    details: list[dict[str, object]],
) -> None:
    for calculated, detail in zip(result.lines, details, strict=True):
        session.add(
            SalesOrderItem(
                sales_order_id=order_id,
                product_id=detail["product_id"],
                description=calculated.description,
                hsn_sac=calculated.hsn_sac,
                unit=calculated.unit,
                quantity=calculated.quantity,
                unit_price=calculated.unit_price,
                discount=calculated.discount,
                taxable_value=calculated.taxable,
                gst_rate=calculated.gst_rate,
                cess_rate=calculated.cess_rate,
            )
        )


def _apply_document_totals(document: Quotation | SalesOrder, result: InvoiceResult) -> None:
    document.taxable_total = result.taxable_total
    document.tax_total = result.tax_total
    document.round_off = result.round_off
    document.grand_total = result.grand_total


@router.get("/quotations")
def list_quotations(
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    status_filter: str | None = Query(default=None, alias="status"),
) -> dict[str, object]:
    query = select(Quotation).where(dealer_customer_scope(user, Quotation.customer_id))
    if q:
        query = query.where(Quotation.number.ilike(f"%{q}%"))
    if status_filter:
        query = query.where(Quotation.status == status_filter)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(Quotation.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_quote_output(session, row) for row in rows], "page": page, "page_size": page_size, "total": total}


@router.post("/quotations", status_code=status.HTTP_201_CREATED)
def create_quotation(
    body: QuotationWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:create"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    _customer(session, body.customer_id)
    result, details = _calculate_lines(session, body.customer_id, body.place_of_supply_code, body.lines)
    settings = _settings(session)
    quote = Quotation(
        number=_next_doc_number(session, settings, "Q", "quotation", body.quote_date),
        customer_id=body.customer_id,
        quote_date=body.quote_date,
        valid_until=body.valid_until,
        place_of_supply_code=body.place_of_supply_code.upper(),
        salesperson_id=body.salesperson_id or user.id,
        branch_id=body.branch_id or user.branch_id,
        status="draft",
        notes=body.notes,
    )
    _apply_document_totals(quote, result)
    session.add(quote)
    session.flush()
    _save_quote_lines(session, quote.id, result, details)
    _audit(session, user, request, "create", "quotation", quote.id, None, _snapshot(quote))
    _fail_conflict(session, "Quotation conflicts with existing data")
    session.refresh(quote)
    return _quote_output(session, quote)


@router.get("/quotations/{quotation_id}")
def get_quotation(
    quotation_id: int,
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    quote = session.scalar(
        select(Quotation).where(
            Quotation.id == quotation_id,
            dealer_customer_scope(user, Quotation.customer_id),
        )
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return _quote_output(session, quote)


@router.put("/quotations/{quotation_id}")
def update_quotation(
    quotation_id: int,
    body: QuotationWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    quote = session.scalar(select(Quotation).where(Quotation.id == quotation_id).with_for_update())
    if quote is None:
        raise HTTPException(status_code=404, detail="Quotation not found")
    if quote.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft quotations can be edited")
    _customer(session, body.customer_id)
    result, details = _calculate_lines(session, body.customer_id, body.place_of_supply_code, body.lines)
    old = _snapshot(quote)
    quote.customer_id = body.customer_id
    quote.quote_date = body.quote_date
    quote.valid_until = body.valid_until
    quote.place_of_supply_code = body.place_of_supply_code.upper()
    quote.salesperson_id = body.salesperson_id or user.id
    quote.branch_id = body.branch_id or user.branch_id
    quote.notes = body.notes
    quote.taxable_total = result.taxable_total
    quote.tax_total = result.tax_total
    quote.round_off = result.round_off
    quote.grand_total = result.grand_total
    for item in session.scalars(select(QuotationItem).where(QuotationItem.quotation_id == quote.id)).all():
        session.delete(item)
    session.flush()
    _save_quote_lines(session, quote.id, result, details)
    _audit(session, user, request, "update", "quotation", quote.id, old, _snapshot(quote))
    _fail_conflict(session, "Quotation conflicts with existing data")
    session.refresh(quote)
    return _quote_output(session, quote)


@router.delete("/quotations/{quotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quotation(
    quotation_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> None:
    quote = session.scalar(select(Quotation).where(Quotation.id == quotation_id).with_for_update())
    if quote is None:
        raise HTTPException(status_code=404, detail="Quotation not found")
    if quote.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft quotations can be deleted")
    _audit(session, user, request, "delete", "quotation", quote.id, _snapshot(quote), None)
    session.delete(quote)
    _fail_conflict(session, "Quotation is linked to an order and cannot be deleted")


def _quotation_transition(
    quotation_id: int,
    action: Literal["send", "accept", "reject"],
    request: Request,
    user: User,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    quote = session.scalar(select(Quotation).where(Quotation.id == quotation_id).with_for_update())
    if quote is None:
        raise HTTPException(status_code=404, detail="Quotation not found")
    allowed = {"send": ("draft", "sent"), "accept": ("sent", "accepted"), "reject": ("sent", "rejected")}
    expected, target = allowed[action]
    if quote.status != expected:
        raise HTTPException(status_code=409, detail=f"Cannot {action} a quotation in {quote.status} status")
    old = _snapshot(quote)
    quote.status = target
    _audit(session, user, request, action, "quotation", quote.id, old, _snapshot(quote))
    _fail_conflict(session, f"Quotation could not be {action}ed")
    return _quote_output(session, quote)


@router.post("/quotations/{quotation_id}/send")
def send_quotation(
    quotation_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:approve"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    return _quotation_transition(quotation_id, "send", request, user, session)


@router.post("/quotations/{quotation_id}/accept")
def accept_quotation(
    quotation_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:approve"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    return _quotation_transition(quotation_id, "accept", request, user, session)


@router.post("/quotations/{quotation_id}/reject")
def reject_quotation(
    quotation_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:approve"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    return _quotation_transition(quotation_id, "reject", request, user, session)


@router.post("/quotations/{quotation_id}/convert-to-order", status_code=status.HTTP_201_CREATED)
def convert_quotation_to_order(
    quotation_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:approve"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    quote = session.scalar(select(Quotation).where(Quotation.id == quotation_id).with_for_update())
    if quote is None:
        raise HTTPException(status_code=404, detail="Quotation not found")
    if quote.status != "accepted":
        raise HTTPException(status_code=409, detail="Only accepted quotations can be converted")
    _customer(session, quote.customer_id)
    quote_items = session.scalars(
        select(QuotationItem).where(QuotationItem.quotation_id == quote.id).order_by(QuotationItem.id)
    ).all()
    for item in quote_items:
        product = session.get(Product, item.product_id)
        hsn = session.get(HsnSacCode, item.hsn_sac)
        if product is None or not product.is_active or product.deleted_at is not None or hsn is None or not hsn.is_active:
            raise HTTPException(status_code=409, detail=f"Quotation line {item.id} references an inactive product or HSN/SAC")
    settings = _settings(session)
    order = SalesOrder(
        number=_next_doc_number(session, settings, "O", "sales_order", date.today()),
        customer_id=quote.customer_id,
        quotation_id=quote.id,
        order_date=date.today(),
        place_of_supply_code=quote.place_of_supply_code,
        status="draft",
        salesperson_id=quote.salesperson_id,
        branch_id=quote.branch_id,
        taxable_total=quote.taxable_total,
        tax_total=quote.tax_total,
        round_off=quote.round_off,
        grand_total=quote.grand_total,
    )
    session.add(order)
    session.flush()
    for item in quote_items:
        session.add(
            SalesOrderItem(
                sales_order_id=order.id,
                product_id=item.product_id,
                description=item.description,
                hsn_sac=item.hsn_sac,
                unit=item.unit,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount=item.discount,
                taxable_value=item.taxable_value,
                gst_rate=item.gst_rate,
                cess_rate=item.cess_rate,
            )
        )
    old = _snapshot(quote)
    quote.status = "converted"
    _audit(session, user, request, "convert", "quotation", quote.id, old, _snapshot(quote))
    _audit(session, user, request, "create", "sales_order", order.id, None, _snapshot(order))
    _fail_conflict(session, "Quotation could not be converted to an order")
    session.refresh(order)
    return _order_output(session, order)


@router.get("/orders")
def list_orders(
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    status_filter: str | None = Query(default=None, alias="status"),
) -> dict[str, object]:
    query = select(SalesOrder).where(dealer_customer_scope(user, SalesOrder.customer_id))
    if q:
        query = query.where(SalesOrder.number.ilike(f"%{q}%"))
    if status_filter:
        query = query.where(SalesOrder.status == status_filter)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(SalesOrder.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_order_output(session, row) for row in rows], "page": page, "page_size": page_size, "total": total}


@router.get("/orders/{order_id}")
def get_order(
    order_id: int,
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    order = session.scalar(
        select(SalesOrder).where(
            SalesOrder.id == order_id,
            dealer_customer_scope(user, SalesOrder.customer_id),
        )
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return _order_output(session, order)


@router.post("/orders/{order_id}/confirm")
def confirm_order(
    order_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:approve"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    order = session.scalar(select(SalesOrder).where(SalesOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    if order.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft sales orders can be confirmed")
    _customer(session, order.customer_id)
    products_active = session.scalar(
        select(func.count())
        .select_from(SalesOrderItem)
        .join(Product, Product.id == SalesOrderItem.product_id)
        .where(
            SalesOrderItem.sales_order_id == order.id,
            (Product.is_active.is_(False)) | (Product.deleted_at.is_not(None)),
        )
    ) or 0
    if products_active:
        raise HTTPException(status_code=409, detail="Sales order contains inactive products")
    old = _snapshot(order)
    order.status = "confirmed"
    _audit(session, user, request, "confirm", "sales_order", order.id, old, _snapshot(order))
    _fail_conflict(session, "Sales order could not be confirmed")
    return _order_output(session, order)


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:approve"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    order = session.scalar(select(SalesOrder).where(SalesOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    if order.status not in {"draft", "confirmed", "partially_delivered"}:
        raise HTTPException(status_code=409, detail=f"Cannot cancel an order in {order.status} status")
    delivered = session.scalar(
        select(func.count()).select_from(DeliveryChallan).where(
            DeliveryChallan.sales_order_id == order.id,
            DeliveryChallan.status == "delivered",
        )
    ) or 0
    if delivered:
        raise HTTPException(status_code=409, detail="Order has delivered quantities; use an invoice credit note for returns")
    pending_challans = session.scalar(
        select(func.count()).select_from(DeliveryChallan).where(
            DeliveryChallan.sales_order_id == order.id,
            DeliveryChallan.status.in_(("draft", "dispatched")),
        )
    ) or 0
    if pending_challans:
        raise HTTPException(status_code=409, detail="Cancel undelivered challans before cancelling this order")
    old = _snapshot(order)
    order.status = "cancelled"
    _audit(session, user, request, "cancel", "sales_order", order.id, old, _snapshot(order))
    _fail_conflict(session, "Sales order could not be cancelled")
    return _order_output(session, order)


@router.get("/delivery-challans")
def list_challans(
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
) -> dict[str, object]:
    query = (
        select(DeliveryChallan)
        .join(SalesOrder, SalesOrder.id == DeliveryChallan.sales_order_id)
        .where(dealer_customer_scope(user, SalesOrder.customer_id))
    )
    if q:
        query = query.where(DeliveryChallan.number.ilike(f"%{q}%"))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(DeliveryChallan.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_challan_output(session, row) for row in rows], "page": page, "page_size": page_size, "total": total}


@router.get("/delivery-challans/{challan_id}")
def get_challan(
    challan_id: int,
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    challan = session.scalar(
        select(DeliveryChallan)
        .join(SalesOrder, SalesOrder.id == DeliveryChallan.sales_order_id)
        .where(
            DeliveryChallan.id == challan_id,
            dealer_customer_scope(user, SalesOrder.customer_id),
        )
    )
    if challan is None:
        raise HTTPException(status_code=404, detail="Delivery challan not found")
    return _challan_output(session, challan)


@router.post("/orders/{order_id}/delivery-challans", status_code=status.HTTP_201_CREATED)
def create_challan(
    order_id: int,
    body: ChallanWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    order = session.scalar(select(SalesOrder).where(SalesOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    if order.status not in {"confirmed", "partially_delivered"}:
        raise HTTPException(status_code=409, detail="Only confirmed or partially delivered orders can be dispatched")
    _customer(session, order.customer_id)
    order_items = {
        item.id: item
        for item in session.scalars(
            select(SalesOrderItem).where(SalesOrderItem.sales_order_id == order.id).order_by(SalesOrderItem.id).with_for_update()
        ).all()
    }
    outstanding_rows = session.execute(
        select(DeliveryChallanItem.sales_order_item_id, func.sum(DeliveryChallanItem.quantity))
        .join(DeliveryChallan, DeliveryChallan.id == DeliveryChallanItem.challan_id)
        .where(
            DeliveryChallan.sales_order_id == order.id,
            DeliveryChallan.status != "cancelled",
        )
        .group_by(DeliveryChallanItem.sales_order_item_id)
    ).all()
    assigned = {int(item_id): quantity for item_id, quantity in outstanding_rows}
    assigned_current = assigned.copy()
    seen_nonserialized: set[int] = set()
    settings = _settings(session)
    challan = DeliveryChallan(
        number=_next_doc_number(session, settings, "D", "delivery_challan", body.challan_date),
        sales_order_id=order.id,
        customer_id=order.customer_id,
        challan_date=body.challan_date,
        status="draft",
    )
    session.add(challan)
    session.flush()
    for line in body.lines:
        order_item = order_items.get(line.sales_order_item_id)
        if order_item is None:
            raise HTTPException(status_code=422, detail="Order line does not belong to this order")
        if line.quantity + assigned_current.get(order_item.id, Decimal("0")) > order_item.quantity:
            raise HTTPException(status_code=409, detail=f"Quantity exceeds undelivered order quantity for line {order_item.id}")
        product = session.get(Product, order_item.product_id)
        if product is None or not product.is_active or product.deleted_at is not None:
            raise HTTPException(status_code=422, detail=f"Product on order line {order_item.id} is inactive")
        serial_id = line.serial_id
        if product.is_serialized:
            if line.quantity != 1 or serial_id is None:
                raise HTTPException(status_code=422, detail="Serialized order lines require quantity one and a serial")
            serial = session.scalar(
                select(ProductSerial)
                .where(ProductSerial.id == serial_id, ProductSerial.product_id == product.id, ProductSerial.status == "available")
                .with_for_update()
            )
            if serial is None:
                raise HTTPException(status_code=409, detail="Serial is not currently available")
            reused = session.scalar(
                select(DeliveryChallanItem.id)
                .join(DeliveryChallan, DeliveryChallan.id == DeliveryChallanItem.challan_id)
                .where(
                    DeliveryChallanItem.serial_id == serial_id,
                    DeliveryChallan.status != "cancelled",
                )
                .limit(1)
            )
            if reused is not None:
                raise HTTPException(status_code=409, detail="Serial is already listed on an active delivery challan")
        elif serial_id is not None:
            raise HTTPException(status_code=422, detail="Only serialized products can use a serial")
        elif order_item.id in seen_nonserialized:
            raise HTTPException(status_code=422, detail="A non-serialized order line may appear only once per challan")
        if not product.is_serialized:
            seen_nonserialized.add(order_item.id)
        assigned_current[order_item.id] = assigned_current.get(order_item.id, Decimal("0")) + line.quantity
        session.add(
            DeliveryChallanItem(
                challan_id=challan.id,
                sales_order_item_id=order_item.id,
                product_id=product.id,
                serial_id=serial_id,
                quantity=line.quantity,
            )
        )
    _audit(session, user, request, "create", "delivery_challan", challan.id, None, {"number": challan.number, "status": "draft"})
    _fail_conflict(session, "Delivery challan conflicts with existing data")
    session.refresh(challan)
    return _challan_output(session, challan)


@router.post("/delivery-challans/{challan_id}/dispatch")
def dispatch_challan(
    challan_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    challan = session.scalar(select(DeliveryChallan).where(DeliveryChallan.id == challan_id).with_for_update())
    if challan is None:
        raise HTTPException(status_code=404, detail="Delivery challan not found")
    if challan.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft delivery challans can be dispatched")
    old = {"status": challan.status}
    challan.status = "dispatched"
    _audit(session, user, request, "dispatch", "delivery_challan", challan.id, old, {"status": "dispatched"})
    _fail_conflict(session, "Delivery challan could not be dispatched")
    return _challan_output(session, challan)


@router.post("/delivery-challans/{challan_id}/deliver")
def deliver_challan(
    challan_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    challan = session.scalar(select(DeliveryChallan).where(DeliveryChallan.id == challan_id).with_for_update())
    if challan is None:
        raise HTTPException(status_code=404, detail="Delivery challan not found")
    if challan.status != "dispatched":
        raise HTTPException(status_code=409, detail="Only dispatched delivery challans can be marked delivered")
    order = session.scalar(select(SalesOrder).where(SalesOrder.id == challan.sales_order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=409, detail="Sales order no longer exists")
    old = {"status": challan.status}
    challan.status = "delivered"
    delivered_rows = session.execute(
        select(DeliveryChallanItem.sales_order_item_id, func.sum(DeliveryChallanItem.quantity))
        .join(DeliveryChallan, DeliveryChallan.id == DeliveryChallanItem.challan_id)
        .where(DeliveryChallan.sales_order_id == order.id, DeliveryChallan.status == "delivered")
        .group_by(DeliveryChallanItem.sales_order_item_id)
    ).all()
    delivered = {int(item_id): quantity for item_id, quantity in delivered_rows}
    ordered = session.scalars(select(SalesOrderItem).where(SalesOrderItem.sales_order_id == order.id)).all()
    order.status = "delivered" if all(delivered.get(item.id, Decimal("0")) >= item.quantity for item in ordered) else "partially_delivered"
    _audit(session, user, request, "deliver", "delivery_challan", challan.id, old, {"status": "delivered"})
    _audit(session, user, request, "update", "sales_order", order.id, None, _snapshot(order))
    _fail_conflict(session, "Delivery status could not be saved")
    return _challan_output(session, challan)


@router.post("/delivery-challans/{challan_id}/cancel")
def cancel_challan(
    challan_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    challan = session.scalar(select(DeliveryChallan).where(DeliveryChallan.id == challan_id).with_for_update())
    if challan is None:
        raise HTTPException(status_code=404, detail="Delivery challan not found")
    if challan.status not in {"draft", "dispatched"}:
        raise HTTPException(status_code=409, detail="Only undelivered challans can be cancelled")
    old = {"status": challan.status}
    challan.status = "cancelled"
    _audit(session, user, request, "cancel", "delivery_challan", challan.id, old, {"status": "cancelled"})
    _fail_conflict(session, "Delivery challan could not be cancelled")
    return _challan_output(session, challan)


@router.post("/orders/{order_id}/convert-to-invoice", status_code=status.HTTP_201_CREATED)
def convert_order_to_invoice(
    order_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:create"))],
    session: Annotated[Session, Depends(get_db)],
    warehouse_id: int | None = Query(default=None, gt=0),
) -> dict[str, object]:
    order = session.scalar(select(SalesOrder).where(SalesOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    if order.status not in {"partially_delivered", "delivered"}:
        raise HTTPException(status_code=409, detail="Only delivered quantities can be converted to an invoice")
    delivered_rows = session.execute(
        select(DeliveryChallanItem, Product.is_service, Product.is_serialized)
        .join(DeliveryChallan, DeliveryChallan.id == DeliveryChallanItem.challan_id)
        .join(Product, Product.id == DeliveryChallanItem.product_id)
        .where(
            DeliveryChallan.sales_order_id == order.id,
            DeliveryChallan.status == "delivered",
        )
        .order_by(DeliveryChallanItem.sales_order_item_id, DeliveryChallanItem.id)
    ).all()
    already_invoiced_rows = session.execute(
        select(InvoiceItem.sales_order_item_id, InvoiceItem.serial_id, func.sum(InvoiceItem.quantity))
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .where(
            Invoice.sales_order_id == order.id,
            Invoice.status != "cancelled",
            InvoiceItem.sales_order_item_id.is_not(None),
        )
        .group_by(InvoiceItem.sales_order_item_id, InvoiceItem.serial_id)
    ).all()
    invoiced = {
        (int(item_id), serial_id): quantity
        for item_id, serial_id, quantity in already_invoiced_rows
    }
    grouped: dict[tuple[int, int | None], Decimal] = {}
    types: dict[int, tuple[bool, bool]] = {}
    for challan_line, is_service, is_serialized in delivered_rows:
        key = (challan_line.sales_order_item_id, challan_line.serial_id if is_serialized else None)
        grouped[key] = grouped.get(key, Decimal("0")) + challan_line.quantity
        types[challan_line.sales_order_item_id] = (is_service, is_serialized)
    lines: list[InvoiceLineWrite] = []
    line_order_item_ids: list[int] = []
    order_items = {
        item.id: item
        for item in session.scalars(select(SalesOrderItem).where(SalesOrderItem.sales_order_id == order.id)).all()
    }
    for (order_item_id, serial_id), delivered_quantity in grouped.items():
        order_item = order_items[order_item_id]
        available = delivered_quantity - invoiced.get((order_item_id, serial_id), Decimal("0"))
        if available <= 0:
            continue
        is_service, is_serialized = types[order_item_id]
        if is_serialized and serial_id is None:
            raise HTTPException(status_code=409, detail=f"Delivered serialized order line {order_item_id} has no serial")
        chosen_warehouse = None
        if not is_service:
            chosen_warehouse = warehouse_id
            if serial_id is not None:
                serial = session.get(ProductSerial, serial_id)
                if serial is None or serial.warehouse_id is None:
                    raise HTTPException(status_code=409, detail="Delivered serial has no warehouse")
                chosen_warehouse = serial.warehouse_id
            if chosen_warehouse is None:
                from app.models import Warehouse
                chosen_warehouse = session.scalar(
                    select(Warehouse.id).where(Warehouse.is_active.is_(True)).order_by(Warehouse.id).limit(1)
                )
            if chosen_warehouse is None:
                raise HTTPException(status_code=422, detail="An active warehouse is required to convert stocked items")
        lines.append(
            InvoiceLineWrite(
                product_id=order_item.product_id,
                serial_id=serial_id,
                warehouse_id=chosen_warehouse,
                description=order_item.description,
                hsn_sac=order_item.hsn_sac,
                unit=order_item.unit,
                quantity=available,
                unit_price=order_item.unit_price,
                discount_amount=order_item.discount * available / order_item.quantity,
                gst_rate=order_item.gst_rate,
                cess_rate=order_item.cess_rate,
            )
        )
        line_order_item_ids.append(order_item_id)
    if not lines:
        raise HTTPException(status_code=409, detail="There are no delivered quantities left to invoice")
    body = InvoiceDraftWrite(
        customer_id=order.customer_id,
        place_of_supply_code=order.place_of_supply_code,
        doc_type="tax_invoice",
        invoice_date=date.today(),
        lines=lines,
    )
    customer, settings, result, details, preview = _calculate_invoice(
        session,
        body.customer_id,
        body.place_of_supply_code,
        body.doc_type,
        body.reverse_charge,
        body.lines,
    )
    for detail, order_item_id in zip(details, line_order_item_ids, strict=True):
        detail["sales_order_item_id"] = order_item_id
    invoice = Invoice(
        doc_type=body.doc_type,
        customer_id=customer.id,
        sales_order_id=order.id,
        salesperson_id=user.id,
        branch_id=order.branch_id,
        invoice_date=body.invoice_date,
        place_of_supply_code=body.place_of_supply_code,
        reverse_charge=False,
        status="draft",
        created_by=user.id,
    )
    _snapshot_parties(invoice, settings, customer)
    invoice.hsn_summary = preview["hsn_summary"]
    _apply_result(invoice, result)
    session.add(invoice)
    session.flush()
    _save_items(session, invoice, result, details)
    _invoice_audit(session, user, request, "create_draft_from_order", invoice.id, None, _invoice_snapshot(invoice))
    _audit(session, user, request, "convert", "sales_order", order.id, None, {"invoice_id": invoice.id})
    _fail_conflict(session, "Order conversion conflicts with existing invoice data")
    session.refresh(invoice)
    from app.api.v1.invoices import _invoice_output
    return _invoice_output(session, invoice)


@router.get("/reports/sales")
def sales_report(
    user: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
    from_date: date,
    to_date: date,
    group_by: Literal["day", "month", "product", "customer", "salesperson", "gst", "branch"] = "day",
) -> dict[str, object]:
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to_date must be on or after from_date")
    filters = (
        Invoice.status == "issued",
        Invoice.doc_type.in_(("tax_invoice", "bill_of_supply")),
        Invoice.invoice_date >= from_date,
        Invoice.invoice_date <= to_date,
    )
    date_key = func.date_trunc("day" if group_by == "day" else "month", Invoice.invoice_date)
    if group_by in {"day", "month"}:
        rows = session.execute(
            select(date_key.label("bucket"), func.count(Invoice.id), func.sum(Invoice.grand_total))
            .where(*filters)
            .group_by(date_key)
            .order_by(date_key)
        ).all()
        items = [{"key": bucket.date().isoformat(), "count": count, "grand_total": format(total or 0, "f")} for bucket, count, total in rows]
    else:
        query = select(func.count(Invoice.id), func.sum(Invoice.grand_total))
        joins = []
        key_expression = None
        line_totals = False
        if group_by == "customer":
            from app.models import Customer
            joins.append((Customer, Customer.id == Invoice.customer_id))
            key_expression = Customer.name
        elif group_by == "salesperson":
            joins.append((User, User.id == Invoice.salesperson_id))
            key_expression = User.full_name
        elif group_by == "branch":
            from app.models import Branch
            joins.append((Branch, Branch.id == Invoice.branch_id))
            key_expression = Branch.name
        elif group_by == "product":
            joins.extend([(InvoiceItem, InvoiceItem.invoice_id == Invoice.id), (Product, Product.id == InvoiceItem.product_id)])
            key_expression = func.coalesce(Product.name, InvoiceItem.description)
            line_totals = True
        else:
            joins.append((InvoiceItem, InvoiceItem.invoice_id == Invoice.id))
            key_expression = InvoiceItem.gst_rate
            line_totals = True
        total_expression = (
            func.sum(InvoiceItem.taxable_value + InvoiceItem.cgst + InvoiceItem.sgst + InvoiceItem.igst + InvoiceItem.cess)
            if line_totals else func.sum(Invoice.grand_total)
        )
        query = select(key_expression.label("key"), func.count(func.distinct(Invoice.id)), total_expression).where(*filters)
        for model, condition in joins:
            query = query.outerjoin(model, condition) if group_by == "product" and model is Product else query.join(model, condition)
        query = query.group_by(key_expression).order_by(key_expression)
        rows = session.execute(query).all()
        items = [{"key": str(key), "count": count, "grand_total": format(total or 0, "f")} for key, count, total in rows]
    return {"from_date": from_date.isoformat(), "to_date": to_date.isoformat(), "group_by": group_by, "items": items}


def _document_pdf(kind: str, number: str, on_date: date, customer_name: str, status_text: str, lines: list[list[str]], total: Decimal) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{kind}</b>", styles["Title"]),
        Paragraph(f"Number: {number} &nbsp;&nbsp; Date: {on_date.isoformat()} &nbsp;&nbsp; Status: {status_text}", styles["Normal"]),
        Paragraph(f"Customer: {customer_name}", styles["Normal"]),
        Spacer(1, 8 * mm),
    ]
    data = [["Description", "HSN/SAC", "Qty", "Rate", "Taxable", "GST %"], *lines]
    table = Table(data, repeatRows=1, colWidths=[57 * mm, 24 * mm, 18 * mm, 25 * mm, 28 * mm, 20 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174c3c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c5d3c7")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7f4")]),
    ]))
    story.extend([table, Spacer(1, 6 * mm), Paragraph(f"Grand total: {total:.2f}", styles["Heading2"]), Paragraph(amount_in_words(total), styles["Normal"])])
    doc.build(story)
    return buffer.getvalue()


def _pdf_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(BytesIO(data), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/quotations/{quotation_id}/pdf")
def quotation_pdf(
    quotation_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    quote = session.scalar(
        select(Quotation).where(
            Quotation.id == quotation_id,
            dealer_customer_scope(user, Quotation.customer_id),
        )
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="Quotation not found")
    customer = session.get(Customer, quote.customer_id)
    rows = session.scalars(select(QuotationItem).where(QuotationItem.quotation_id == quote.id).order_by(QuotationItem.id)).all()
    lines = [[r.description or "", r.hsn_sac, format(r.quantity, "f"), format(r.unit_price, "f"), format(r.taxable_value, "f"), format(r.gst_rate, "f")] for r in rows]
    data = _document_pdf("Quotation", quote.number, quote.quote_date, customer.name if customer else "", quote.status, lines, quote.grand_total)
    _audit(session, user, request, "download_pdf", "quotation", quote.id, None, {"number": quote.number})
    session.commit()
    return _pdf_response(data, f"{quote.number}.pdf")


@router.get("/orders/{order_id}/pdf")
def order_pdf(
    order_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    order = session.scalar(
        select(SalesOrder).where(
            SalesOrder.id == order_id,
            dealer_customer_scope(user, SalesOrder.customer_id),
        )
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    customer = session.get(Customer, order.customer_id)
    rows = session.scalars(select(SalesOrderItem).where(SalesOrderItem.sales_order_id == order.id).order_by(SalesOrderItem.id)).all()
    lines = [[r.description or "", r.hsn_sac, format(r.quantity, "f"), format(r.unit_price, "f"), format(r.taxable_value, "f"), format(r.gst_rate, "f")] for r in rows]
    data = _document_pdf("Sales order", order.number, order.order_date, customer.name if customer else "", order.status, lines, order.grand_total)
    _audit(session, user, request, "download_pdf", "sales_order", order.id, None, {"number": order.number})
    session.commit()
    return _pdf_response(data, f"{order.number}.pdf")
