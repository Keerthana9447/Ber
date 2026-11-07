from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permissions
from app.db.session import get_db
from app.models import (
    AuditLog,
    CompanySettings,
    Customer,
    GstRate,
    HsnSacCode,
    Invoice,
    InvoiceItem,
    Product,
    ProductSerial,
    State,
    User,
)
from berev_core.gst import GSTError, InvoiceResult, LineInput, compute_invoice
from berev_core.words import amount_in_words

router = APIRouter(tags=["invoices"])
DocType = Literal["tax_invoice", "bill_of_supply", "proforma"]


class InvoiceLineWrite(BaseModel):
    product_id: int | None = Field(default=None, gt=0)
    serial_id: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=1000)
    hsn_sac: str | None = Field(default=None, min_length=4, max_length=8, pattern=r"^\d{4,8}$")
    unit: str | None = Field(default=None, max_length=32)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    gst_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    cess_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)

    @field_validator("description", "unit")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_line_source(self) -> InvoiceLineWrite:
        if self.product_id is None and not self.description:
            raise ValueError("A free-description line requires a description")
        if self.discount_pct and self.discount_amount:
            raise ValueError("Provide discount_pct or discount_amount, not both")
        if self.serial_id is not None and self.product_id is None:
            raise ValueError("A serial can only be used with a product")
        return self


class InvoiceDraftWrite(BaseModel):
    customer_id: int = Field(gt=0)
    place_of_supply_code: str = Field(min_length=2, max_length=2)
    doc_type: DocType = "tax_invoice"
    reverse_charge: bool = False
    invoice_date: date = Field(default_factory=date.today)
    due_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=5000)
    lines: list[InvoiceLineWrite] = Field(min_length=1, max_length=500)

    @field_validator("place_of_supply_code")
    @classmethod
    def normalize_state_code(cls, value: str) -> str:
        return value.strip().upper()


class PreviewRequest(BaseModel):
    customer_id: int = Field(gt=0)
    place_of_supply_code: str = Field(min_length=2, max_length=2)
    doc_type: DocType = "tax_invoice"
    reverse_charge: bool = False
    lines: list[InvoiceLineWrite] = Field(min_length=1, max_length=500)

    @field_validator("place_of_supply_code")
    @classmethod
    def normalize_state_code(cls, value: str) -> str:
        return value.strip().upper()


def _require_sales_preview_permission(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    granted = {permission.code for permission in user.role.permissions}
    if not {"sales:create", "sales:update"}.intersection(granted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return user


def _text(value: Decimal | int) -> str:
    return format(value, "f")


def _customer(session: Session, customer_id: int) -> Customer:
    customer = session.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
            Customer.is_active.is_(True),
        )
    )
    if customer is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Active customer not found")
    return customer


def _normalize_lines(
    session: Session,
    lines: list[InvoiceLineWrite],
    allowed_rates: set[Decimal],
) -> tuple[list[LineInput], list[dict[str, object]]]:
    calculated_inputs: list[LineInput] = []
    details: list[dict[str, object]] = []
    serial_ids: set[int] = set()
    for index, line in enumerate(lines):
        product = None
        if line.product_id is not None:
            product = session.scalar(
                select(Product).where(
                    Product.id == line.product_id,
                    Product.deleted_at.is_(None),
                    Product.is_active.is_(True),
                )
            )
            if product is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "product_id"], "msg": "Active product not found"}],
                )
            if product.is_service and line.serial_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "serial_id"], "msg": "Services cannot use serials"}],
                )

        hsn_code = line.hsn_sac or (product.hsn_sac if product is not None else None)
        if hsn_code is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{"loc": ["body", "lines", index, "hsn_sac"], "msg": "HSN/SAC is required"}],
            )
        hsn = session.get(HsnSacCode, hsn_code)
        if hsn is None or not hsn.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{"loc": ["body", "lines", index, "hsn_sac"], "msg": "Unknown or inactive HSN/SAC code"}],
            )
        if product is not None and hsn.kind != ("SAC" if product.is_service else "HSN"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{"loc": ["body", "lines", index, "hsn_sac"], "msg": "HSN/SAC kind does not match product"}],
            )

        unit_price = line.unit_price if line.unit_price is not None else product.selling_price if product is not None else None
        if unit_price is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{"loc": ["body", "lines", index, "unit_price"], "msg": "Unit price is required"}],
            )
        gst_rate = line.gst_rate if line.gst_rate is not None else product.gst_rate if product is not None else hsn.default_gst_rate
        cess_rate = line.cess_rate if line.cess_rate is not None else product.cess_rate if product is not None else hsn.default_cess_rate
        if gst_rate is None or gst_rate not in allowed_rates:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{"loc": ["body", "lines", index, "gst_rate"], "msg": "GST rate is not configured as active"}],
            )
        if cess_rate is None:
            cess_rate = Decimal("0")
        description = line.description or (product.name if product is not None else None)
        if description is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{"loc": ["body", "lines", index, "description"], "msg": "Description is required"}],
            )

        serial = None
        if product is not None and product.is_serialized:
            if line.serial_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "serial_id"], "msg": "An available serial is required"}],
                )
            if line.quantity != 1:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "quantity"], "msg": "Serialized product quantity must be one per serial"}],
                )
        if line.serial_id is not None:
            if line.serial_id in serial_ids:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "serial_id"], "msg": "The same serial cannot appear on two lines"}],
                )
            serial = session.scalar(
                select(ProductSerial).where(
                    ProductSerial.id == line.serial_id,
                    ProductSerial.status == "available",
                    ProductSerial.product_id == line.product_id,
                )
            )
            if serial is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "serial_id"], "msg": "Serial is unavailable or does not match product"}],
                )
            serial_ids.add(line.serial_id)

        unit = line.unit or (product.unit if product is not None else None)
        input_line = LineInput(
            description=description,
            hsn_sac=hsn_code,
            quantity=line.quantity,
            unit_price=unit_price,
            gst_rate=gst_rate,
            discount_pct=line.discount_pct,
            discount_amount=line.discount_amount,
            cess_rate=cess_rate,
            unit=unit or "NOS",
        )
        calculated_inputs.append(input_line)
        details.append(
            {
                "product_id": product.id if product is not None else None,
                "serial_id": serial.id if serial is not None else None,
                "unit_cost": product.purchase_price if product is not None else None,
            }
        )
    return calculated_inputs, details


def _calculate(
    session: Session,
    customer_id: int,
    place_of_supply_code: str,
    doc_type: DocType,
    reverse_charge: bool,
    lines: list[InvoiceLineWrite],
) -> tuple[Customer, CompanySettings, InvoiceResult, list[dict[str, object]], dict[str, object]]:
    customer = _customer(session, customer_id)
    settings = session.get(CompanySettings, 1)
    if settings is None or not settings.state_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Company state_code must be configured before invoice calculations",
        )
    if session.get(State, place_of_supply_code) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown place_of_supply_code")
    allowed_rates = {
        row for row in session.scalars(
            select(GstRate.rate).where(GstRate.is_active.is_(True))
        ).all()
    }
    inputs, details = _normalize_lines(session, lines, allowed_rates)
    try:
        result = compute_invoice(
            inputs,
            settings.state_code,
            place_of_supply_code,
            tax_applicable=doc_type != "bill_of_supply",
            allowed_rates=allowed_rates,
        )
    except GSTError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from None
    serialized = _serialize_result(
        result,
        details,
        customer_id,
        place_of_supply_code,
        settings.state_code,
        doc_type,
        reverse_charge,
    )
    return customer, settings, result, details, serialized


def _serialize_result(
    result: InvoiceResult,
    details: list[dict[str, object]],
    customer_id: int,
    place_of_supply_code: str,
    seller_state_code: str,
    doc_type: DocType,
    reverse_charge: bool,
) -> dict[str, object]:
    lines = []
    for calculated, detail in zip(result.lines, details, strict=True):
        lines.append(
            {
                "product_id": detail["product_id"],
                "serial_id": detail["serial_id"],
                "description": calculated.description,
                "hsn_sac": calculated.hsn_sac,
                "unit": calculated.unit,
                "quantity": _text(calculated.quantity),
                "unit_price": _text(calculated.unit_price),
                "gross": _text(calculated.gross),
                "discount": _text(calculated.discount),
                "taxable_value": _text(calculated.taxable),
                "gst_rate": _text(calculated.gst_rate),
                "cess_rate": _text(calculated.cess_rate),
                "cgst": _text(calculated.cgst),
                "sgst": _text(calculated.sgst),
                "igst": _text(calculated.igst),
                "cess": _text(calculated.cess),
                "line_total": _text(calculated.total),
            }
        )
    return {
        "customer_id": customer_id,
        "doc_type": doc_type,
        "reverse_charge": reverse_charge,
        "seller_state_code": seller_state_code,
        "place_of_supply_code": place_of_supply_code,
        "is_inter_state": result.is_inter_state,
        "tax_applicable": result.tax_applicable,
        "lines": lines,
        "taxable_total": _text(result.taxable_total),
        "cgst_total": _text(result.cgst_total),
        "sgst_total": _text(result.sgst_total),
        "igst_total": _text(result.igst_total),
        "cess_total": _text(result.cess_total),
        "tax_total": _text(result.tax_total),
        "total_before_round": _text(result.total_before_round),
        "round_off": _text(result.round_off),
        "grand_total": _text(result.grand_total),
        "amount_in_words": amount_in_words(result.grand_total),
        "hsn_summary": [
            {
                "hsn_sac": row.hsn_sac,
                "gst_rate": _text(row.gst_rate),
                "cess_rate": _text(row.cess_rate),
                "taxable": _text(row.taxable),
                "cgst": _text(row.cgst),
                "sgst": _text(row.sgst),
                "igst": _text(row.igst),
                "cess": _text(row.cess),
            }
            for row in result.hsn_summary
        ],
    }


def _audit(
    session: Session,
    user: User,
    request: Request,
    action: str,
    invoice_id: int,
    old_values: dict[str, object] | None,
    new_values: dict[str, object] | None,
) -> None:
    session.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity_type="invoice",
            entity_id=invoice_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=request.client.host if request.client else None,
        )
    )


def _invoice_snapshot(invoice: Invoice) -> dict[str, object]:
    return {
        "doc_type": invoice.doc_type,
        "invoice_number": invoice.invoice_number,
        "customer_id": invoice.customer_id,
        "invoice_date": invoice.invoice_date.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "place_of_supply_code": invoice.place_of_supply_code,
        "is_inter_state": invoice.is_inter_state,
        "reverse_charge": invoice.reverse_charge,
        "status": invoice.status,
        "taxable_total": _text(invoice.taxable_total),
        "cgst_total": _text(invoice.cgst_total),
        "sgst_total": _text(invoice.sgst_total),
        "igst_total": _text(invoice.igst_total),
        "cess_total": _text(invoice.cess_total),
        "round_off": _text(invoice.round_off),
        "grand_total": _text(invoice.grand_total),
    }


def _invoice_output(session: Session, invoice: Invoice) -> dict[str, object]:
    items = session.scalars(
        select(InvoiceItem).where(InvoiceItem.invoice_id == invoice.id).order_by(InvoiceItem.id)
    ).all()
    return {
        **_invoice_snapshot(invoice),
        "id": invoice.id,
        "fy_start_year": invoice.fy_start_year,
        "customer_gstin": invoice.customer_gstin,
        "seller_gstin": invoice.seller_gstin,
        "billing_address": invoice.billing_address,
        "shipping_address": invoice.shipping_address,
        "payment_terms": invoice.payment_terms,
        "notes": invoice.notes,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
        "lines": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "serial_id": item.serial_id,
                "description": item.description,
                "hsn_sac": item.hsn_sac,
                "unit": item.unit,
                "quantity": _text(item.quantity),
                "unit_price": _text(item.unit_price),
                "discount": _text(item.discount),
                "taxable_value": _text(item.taxable_value),
                "gst_rate": _text(item.gst_rate),
                "cess_rate": _text(item.cess_rate),
                "cgst": _text(item.cgst),
                "sgst": _text(item.sgst),
                "igst": _text(item.igst),
                "cess": _text(item.cess),
            }
            for item in items
        ],
        "amount_in_words": amount_in_words(invoice.grand_total),
    }


def _save_items(session: Session, invoice: Invoice, result: InvoiceResult, details: list[dict[str, object]]) -> None:
    for calculated, detail in zip(result.lines, details, strict=True):
        session.add(
            InvoiceItem(
                invoice_id=invoice.id,
                product_id=detail["product_id"],
                serial_id=detail["serial_id"],
                description=calculated.description,
                hsn_sac=calculated.hsn_sac,
                unit=calculated.unit,
                quantity=calculated.quantity,
                unit_price=calculated.unit_price,
                discount=calculated.discount,
                taxable_value=calculated.taxable,
                gst_rate=calculated.gst_rate,
                cess_rate=calculated.cess_rate,
                cgst=calculated.cgst,
                sgst=calculated.sgst,
                igst=calculated.igst,
                cess=calculated.cess,
                unit_cost=detail["unit_cost"],
            )
        )


def _apply_result(invoice: Invoice, result: InvoiceResult) -> None:
    invoice.is_inter_state = result.is_inter_state
    invoice.taxable_total = result.taxable_total
    invoice.cgst_total = result.cgst_total
    invoice.sgst_total = result.sgst_total
    invoice.igst_total = result.igst_total
    invoice.cess_total = result.cess_total
    invoice.round_off = result.round_off
    invoice.grand_total = result.grand_total


@router.post("/invoices/preview")
def preview_invoice(
    body: PreviewRequest,
    user: Annotated[User, Depends(_require_sales_preview_permission)],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    del user
    _customer_obj, _settings, _result, _details, response = _calculate(
        session,
        body.customer_id,
        body.place_of_supply_code,
        body.doc_type,
        body.reverse_charge,
        body.lines,
    )
    return response


@router.get("/invoices")
def list_invoices(
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    invoice_status: Literal["draft", "issued"] | None = Query(default=None, alias="status"),
) -> dict[str, object]:
    del user
    filters = []
    if invoice_status is not None:
        filters.append(Invoice.status == invoice_status)
    search = q.strip()
    if search:
        pattern = f"%{search}%"
        filters.append(Invoice.invoice_number.ilike(pattern))
    total = session.scalar(select(func.count(Invoice.id)).where(*filters)) or 0
    rows = session.execute(
        select(Invoice, Customer.name)
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(*filters)
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "customer_id": invoice.customer_id,
                "customer_name": customer_name,
                "doc_type": invoice.doc_type,
                "invoice_date": invoice.invoice_date.isoformat(),
                "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
                "status": invoice.status,
                "grand_total": _text(invoice.grand_total),
            }
            for invoice, customer_name in rows
        ],
        "total": total,
    }


@router.get("/invoices/{invoice_id}")
def get_invoice(
    invoice_id: int,
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    del user
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return _invoice_output(session, invoice)


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def create_invoice_draft(
    body: InvoiceDraftWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:create"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    customer, settings, result, details, _preview = _calculate(
        session,
        body.customer_id,
        body.place_of_supply_code,
        body.doc_type,
        body.reverse_charge,
        body.lines,
    )
    invoice = Invoice(
        doc_type=body.doc_type,
        invoice_number=None,
        customer_id=customer.id,
        salesperson_id=user.id,
        branch_id=user.branch_id,
        invoice_date=body.invoice_date,
        due_date=body.due_date,
        seller_gstin=settings.gstin,
        customer_gstin=customer.gstin,
        billing_address=customer.billing_address,
        shipping_address=customer.shipping_address,
        place_of_supply_code=body.place_of_supply_code,
        reverse_charge=body.reverse_charge,
        status="draft",
        payment_terms=body.payment_terms,
        notes=body.notes,
        created_by=user.id,
    )
    _apply_result(invoice, result)
    session.add(invoice)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Draft invoice could not be saved") from None
    _save_items(session, invoice, result, details)
    _audit(session, user, request, "create_draft", invoice.id, None, _invoice_snapshot(invoice))
    session.commit()
    session.refresh(invoice)
    return _invoice_output(session, invoice)


@router.put("/invoices/{invoice_id}")
def update_invoice_draft(
    invoice_id: int,
    body: InvoiceDraftWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    invoice = session.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if invoice.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft invoices can be edited")
    customer, settings, result, details, _preview = _calculate(
        session,
        body.customer_id,
        body.place_of_supply_code,
        body.doc_type,
        body.reverse_charge,
        body.lines,
    )
    old_values = _invoice_snapshot(invoice)
    invoice.doc_type = body.doc_type
    invoice.invoice_number = None
    invoice.customer_id = customer.id
    invoice.invoice_date = body.invoice_date
    invoice.due_date = body.due_date
    invoice.seller_gstin = settings.gstin
    invoice.customer_gstin = customer.gstin
    invoice.billing_address = customer.billing_address
    invoice.shipping_address = customer.shipping_address
    invoice.place_of_supply_code = body.place_of_supply_code
    invoice.reverse_charge = body.reverse_charge
    invoice.payment_terms = body.payment_terms
    invoice.notes = body.notes
    invoice.updated_at = datetime.now(timezone.utc)
    _apply_result(invoice, result)
    for item in session.scalars(select(InvoiceItem).where(InvoiceItem.invoice_id == invoice.id)).all():
        session.delete(item)
    session.flush()
    _save_items(session, invoice, result, details)
    _audit(session, user, request, "update_draft", invoice.id, old_values, _invoice_snapshot(invoice))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Draft invoice conflicts with current data") from None
    session.refresh(invoice)
    return _invoice_output(session, invoice)


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice_draft(
    invoice_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> None:
    invoice = session.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if invoice.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft invoices can be deleted")
    old_values = _invoice_snapshot(invoice)
    _audit(session, user, request, "delete_draft", invoice.id, old_values, None)
    session.delete(invoice)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Draft invoice could not be deleted") from None
