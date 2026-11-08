from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as PdfImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse
from xml.sax.saxutils import escape

from app.auth.dependencies import get_current_user, require_permissions
from app.auth.scoping import dealer_customer_scope
from app.api.v1.payments import invoice_balance
from app.db.session import get_db
from app.models import (
    AuditLog,
    CompanySettings,
    Customer,
    CreditNote,
    GstRate,
    HsnSacCode,
    Inventory,
    Invoice,
    InvoiceItem,
    JournalEntry,
    JournalEntryLine,
    PaymentAllocation,
    Payment,
    Product,
    ProductSerial,
    StockMovement,
    State,
    User,
    Warehouse,
)
from berev_core.accounting import (
    JournalEntry as CoreJournalEntry,
    Line as CoreLine,
    post_sales_invoice,
    reverse_entry,
)
from berev_core.gst import GSTError, InvoiceResult, LineInput, compute_invoice
from berev_core.numbering import financial_year_start, format_invoice_number, next_sequence, series_key
from berev_core.words import amount_in_words

router = APIRouter(tags=["invoices"])
DocType = Literal["tax_invoice", "bill_of_supply", "proforma"]
DOC_TYPE_MARKERS = {"tax_invoice": "T", "bill_of_supply": "B"}


class InvoiceLineWrite(BaseModel):
    product_id: int | None = Field(default=None, gt=0)
    serial_id: int | None = Field(default=None, gt=0)
    warehouse_id: int | None = Field(default=None, gt=0)
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


class CancelRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("Cancellation reason must be at least 10 characters")
        return normalized


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
        elif line.serial_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{"loc": ["body", "lines", index, "serial_id"], "msg": "Only serialized products can use serials"}],
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

        warehouse_id = line.warehouse_id
        if product is None or product.is_service:
            if warehouse_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "warehouse_id"], "msg": "Free-description lines and services do not use a warehouse"}],
                )
        elif product.is_serialized:
            if serial is None or serial.warehouse_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "warehouse_id"], "msg": "The selected serial has no current warehouse"}],
                )
            if warehouse_id is not None and warehouse_id != serial.warehouse_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "warehouse_id"], "msg": "Serialized line warehouse must match the serial's current warehouse"}],
                )
            warehouse_id = serial.warehouse_id
        elif warehouse_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{"loc": ["body", "lines", index, "warehouse_id"], "msg": "Select a warehouse for each stocked product"}],
            )
        if warehouse_id is not None:
            warehouse = session.scalar(
                select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.is_active.is_(True))
            )
            if warehouse is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=[{"loc": ["body", "lines", index, "warehouse_id"], "msg": "Active warehouse not found"}],
                )

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
                "warehouse_id": warehouse_id,
                "unit_cost": None,
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
                "warehouse_id": detail["warehouse_id"],
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
    product_ids = {item.product_id for item in items if item.product_id is not None}
    products = session.scalars(select(Product).where(Product.id.in_(product_ids))).all() if product_ids else []
    service_by_product = {product.id: product.is_service for product in products}
    customer = session.get(Customer, invoice.customer_id)
    if customer is None:
        credit_limit_warning = None
    else:
        open_invoices = session.scalars(
            select(Invoice).where(
                Invoice.customer_id == customer.id,
                Invoice.status == "issued",
                Invoice.doc_type.in_(("tax_invoice", "bill_of_supply")),
            )
        ).all()
        exposure = Decimal("0")
        for row in open_invoices:
            balance = invoice_balance(session, row)
            exposure += Decimal(balance["outstanding"])
        if invoice.status in {"draft", "approved"}:
            exposure += Decimal(invoice.grand_total)
        credit_limit_warning = (
            f"Draft exceeds customer credit limit ({exposure} > {customer.credit_limit})"
            if customer.credit_limit > 0 and exposure > customer.credit_limit
            else None
        )
    payment_summary = (
        invoice_balance(session, invoice)
        if invoice.status in {"issued", "cancelled"}
        else {}
    )
    return {
        **_invoice_snapshot(invoice),
        "id": invoice.id,
        "fy_start_year": invoice.fy_start_year,
        "customer_gstin": invoice.customer_gstin,
        "seller_gstin": invoice.seller_gstin,
        "billing_address": invoice.billing_address,
        "shipping_address": invoice.shipping_address,
        "customer_name": invoice.customer_name,
        "seller_legal_name": invoice.seller_legal_name,
        "seller_trade_name": invoice.seller_trade_name,
        "seller_pan": invoice.seller_pan,
        "seller_address": invoice.seller_address,
        "seller_bank_name": invoice.seller_bank_name,
        "seller_bank_account_no": invoice.seller_bank_account_no,
        "seller_bank_ifsc": invoice.seller_bank_ifsc,
        "seller_upi_id": invoice.seller_upi_id,
        "seller_terms_and_conditions": invoice.seller_terms_and_conditions,
        "seller_authorized_signatory": invoice.seller_authorized_signatory,
        "hsn_summary": invoice.hsn_summary,
        "payment_terms": invoice.payment_terms,
        "notes": invoice.notes,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
        **payment_summary,
        "credit_limit_warning": credit_limit_warning,
        "lines": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "serial_id": item.serial_id,
                "warehouse_id": item.warehouse_id,
                "is_service": service_by_product.get(item.product_id, False),
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
                sales_order_item_id=detail.get("sales_order_item_id"),
                warehouse_id=detail["warehouse_id"],
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
                unit_cost=None,
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


def _snapshot_parties(invoice: Invoice, settings: CompanySettings, customer: Customer) -> None:
    invoice.customer_name = customer.name
    invoice.customer_gstin = customer.gstin
    invoice.billing_address = customer.billing_address
    invoice.shipping_address = customer.shipping_address
    invoice.seller_gstin = settings.gstin
    invoice.seller_state_code = settings.state_code
    invoice.seller_legal_name = settings.legal_name
    invoice.seller_trade_name = settings.trade_name
    invoice.seller_pan = settings.pan
    invoice.seller_address = settings.address
    invoice.seller_logo_path = settings.logo_path
    invoice.seller_bank_name = settings.bank_name
    invoice.seller_bank_account_no = settings.bank_account_no
    invoice.seller_bank_ifsc = settings.bank_ifsc
    invoice.seller_upi_id = settings.upi_id
    invoice.seller_terms_and_conditions = settings.terms_and_conditions
    invoice.seller_authorized_signatory = settings.authorized_signatory


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
    invoice_status: Literal["draft", "approved", "issued", "cancelled"] | None = Query(default=None, alias="status"),
) -> dict[str, object]:
    filters = [dealer_customer_scope(user, Invoice.customer_id)]
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
    invoice = session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            dealer_customer_scope(user, Invoice.customer_id),
        )
    )
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
    customer, settings, result, details, preview = _calculate(
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
        place_of_supply_code=body.place_of_supply_code,
        reverse_charge=body.reverse_charge,
        status="draft",
        payment_terms=body.payment_terms,
        notes=body.notes,
        created_by=user.id,
    )
    _snapshot_parties(invoice, settings, customer)
    invoice.hsn_summary = preview["hsn_summary"]
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
    customer, settings, result, details, preview = _calculate(
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
    _snapshot_parties(invoice, settings, customer)
    invoice.place_of_supply_code = body.place_of_supply_code
    invoice.reverse_charge = body.reverse_charge
    invoice.payment_terms = body.payment_terms
    invoice.notes = body.notes
    invoice.updated_at = datetime.now(timezone.utc)
    _apply_result(invoice, result)
    invoice.hsn_summary = preview["hsn_summary"]
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


def _result_from_draft(
    session: Session,
    invoice: Invoice,
) -> tuple[Customer, CompanySettings, InvoiceResult, list[dict[str, object]], dict[str, object], list[InvoiceItem]]:
    items = session.scalars(
        select(InvoiceItem)
        .where(InvoiceItem.invoice_id == invoice.id)
        .order_by(InvoiceItem.id)
    ).all()
    lines = [
        InvoiceLineWrite(
            product_id=item.product_id,
            serial_id=item.serial_id,
            warehouse_id=item.warehouse_id,
            description=item.description,
            hsn_sac=item.hsn_sac,
            unit=item.unit,
            quantity=item.quantity,
            unit_price=item.unit_price,
            discount_amount=item.discount,
            gst_rate=item.gst_rate,
            cess_rate=item.cess_rate,
        )
        for item in items
    ]
    customer, settings, result, details, preview = _calculate(
        session,
        invoice.customer_id,
        invoice.place_of_supply_code,
        invoice.doc_type,
        invoice.reverse_charge,
        lines,
    )
    return customer, settings, result, details, preview, items


def _draft_matches_result(
    invoice: Invoice,
    result: InvoiceResult,
    items: list[InvoiceItem],
    preview: dict[str, object],
) -> bool:
    headers = (
        (invoice.is_inter_state, result.is_inter_state),
        (invoice.taxable_total, result.taxable_total),
        (invoice.cgst_total, result.cgst_total),
        (invoice.sgst_total, result.sgst_total),
        (invoice.igst_total, result.igst_total),
        (invoice.cess_total, result.cess_total),
        (invoice.round_off, result.round_off),
        (invoice.grand_total, result.grand_total),
    )
    if any(stored != calculated for stored, calculated in headers):
        return False
    if len(items) != len(result.lines):
        return False
    summary_fields = ("gst_rate", "cess_rate", "taxable", "cgst", "sgst", "igst", "cess")
    stored_summary = sorted(
        (row["hsn_sac"], *(Decimal(str(row[field])) for field in summary_fields))
        for row in (invoice.hsn_summary or [])
    )
    computed_summary = sorted(
        (row["hsn_sac"], *(Decimal(str(row[field])) for field in summary_fields))
        for row in preview["hsn_summary"]
    )
    if stored_summary != computed_summary:
        return False
    for item, calculated in zip(items, result.lines, strict=True):
        stored_line = (
            item.description, item.hsn_sac, item.unit, item.quantity, item.unit_price,
            item.discount, item.taxable_value, item.gst_rate, item.cess_rate,
            item.cgst, item.sgst, item.igst, item.cess,
        )
        computed_line = (
            calculated.description, calculated.hsn_sac, calculated.unit, calculated.quantity,
            calculated.unit_price, calculated.discount, calculated.taxable, calculated.gst_rate,
            calculated.cess_rate, calculated.cgst, calculated.sgst, calculated.igst, calculated.cess,
        )
        if stored_line != computed_line:
            return False
    return True


def _require_issue_readiness(settings: CompanySettings) -> None:
    required = ("state_code", "gstin", "invoice_prefix", "address")
    missing = [field for field in required if not getattr(settings, field)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Company settings are not ready to issue tax invoices", "missing": missing},
        )


def _persist_journal_entry(
    session: Session,
    entry: CoreJournalEntry,
    user: User,
    source_id: int,
    reverses_entry_id: int | None = None,
    customer_id: int | None = None,
) -> JournalEntry:
    persisted = JournalEntry(
        entry_date=entry.entry_date,
        reference=entry.reference,
        narration=entry.narration,
        source_type="invoice",
        source_id=source_id,
        reverses_entry_id=reverses_entry_id,
        posted_by=user.id,
    )
    session.add(persisted)
    session.flush()
    session.add_all(
        [
            JournalEntryLine(
                entry_id=persisted.id,
                account_code=line.account,
                debit=line.debit,
                credit=line.credit,
                customer_id=customer_id if line.account == "1100" else None,
            )
            for line in entry.lines
        ]
    )
    return persisted


def _invoice_line_inputs(items: list[InvoiceItem]) -> list[InvoiceLineWrite]:
    return [
        InvoiceLineWrite(
            product_id=item.product_id,
            serial_id=item.serial_id,
            warehouse_id=item.warehouse_id,
            description=item.description,
            hsn_sac=item.hsn_sac,
            unit=item.unit,
            quantity=item.quantity,
            unit_price=item.unit_price,
            discount_amount=item.discount,
            gst_rate=item.gst_rate,
            cess_rate=item.cess_rate,
        )
        for item in items
    ]


@router.post("/invoices/{invoice_id}/approve")
def approve_invoice(
    invoice_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:approve"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    invoice = session.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if invoice.doc_type == "proforma":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Proforma documents cannot be approved")
    if invoice.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft invoices can be approved")
    old_values = _invoice_snapshot(invoice)
    invoice.status = "approved"
    invoice.approved_by = user.id
    invoice.updated_at = datetime.now(timezone.utc)
    _audit(session, user, request, "approve", invoice.id, old_values, _invoice_snapshot(invoice))
    session.commit()
    session.refresh(invoice)
    return _invoice_output(session, invoice)


@router.post("/invoices/{invoice_id}/issue")
def issue_invoice(
    invoice_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:issue"))],
    session: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> dict[str, object]:
    try:
        invoice = session.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
        if invoice is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        old_values = _invoice_snapshot(invoice)
        if invoice.doc_type == "proforma":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Proforma documents are draft-only and cannot be issued")
        if invoice.status == "issued" or invoice.status == "cancelled":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Invoice has already been issued as {invoice.invoice_number}",
            )
        settings = session.scalar(select(CompanySettings).where(CompanySettings.id == 1).with_for_update())
        if settings is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Company settings are not configured")
        if invoice.status != "approved":
            permissions = {permission.code for permission in user.role.permissions}
            may_skip = (
                invoice.status == "draft"
                and settings.allow_invoice_issue_without_approval
                and {"sales:approve", "sales:issue"}.issubset(permissions)
            )
            if not may_skip:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice must be approved before issue")
        _require_issue_readiness(settings)
        customer, _settings, result, details, preview, items = _result_from_draft(session, invoice)
        if not _draft_matches_result(invoice, result, items, preview):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invoice totals no longer match current tax rules or master data; re-save the draft before issuing",
            )

        fy_start_year = financial_year_start(invoice.invoice_date, settings.fy_start_month)
        current_fy_start_year = financial_year_start(date.today(), settings.fy_start_month)
        if fy_start_year != current_fy_start_year:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invoice date must be within the current financial year; confirm this date rule with a tax professional",
            )
        granted = {permission.code for permission in user.role.permissions}
        if "sales:backdate" not in granted:
            number_prefix = settings.invoice_prefix + DOC_TYPE_MARKERS[invoice.doc_type]
            latest_date = session.scalar(
                select(func.max(Invoice.invoice_date)).where(
                    Invoice.doc_type == invoice.doc_type,
                    Invoice.fy_start_year == fy_start_year,
                    Invoice.status.in_(("issued", "cancelled")),
                    Invoice.invoice_number.like(f"{number_prefix}/%"),
                )
            )
            if latest_date is not None and invoice.invoice_date < latest_date:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Invoice date is earlier than the latest date in this series; sales:backdate permission is required",
                )
        if idempotency_key:
            duplicate_key = session.scalar(
                select(Invoice.id).where(
                    Invoice.issue_idempotency_key == idempotency_key,
                    Invoice.id != invoice.id,
                )
            )
            if duplicate_key is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency-Key has already been used")

        number_prefix = settings.invoice_prefix + DOC_TYPE_MARKERS[invoice.doc_type]
        series = series_key(invoice.doc_type, settings.invoice_prefix, invoice.invoice_date, settings.fy_start_month)
        raw_connection = session.connection().connection.driver_connection
        with raw_connection.cursor() as cursor:
            sequence = next_sequence(cursor, series, "format")
        invoice_number = format_invoice_number(
            number_prefix,
            invoice.invoice_date,
            sequence,
            fy_start_month=settings.fy_start_month,
        )

        stock_lines = [
            (item, session.get(Product, item.product_id))
            for item in items
            if item.product_id is not None
        ]
        inventory_keys = sorted(
            {
                (item.product_id, item.warehouse_id)
                for item, product in stock_lines
                if product is not None and not product.is_service
            }
        )
        for product_id, warehouse_id in inventory_keys:
            if warehouse_id is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A stocked invoice line has no warehouse")
            warehouse = session.scalar(
                select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.is_active.is_(True))
            )
            if warehouse is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Warehouse {warehouse_id} is no longer active")
            session.execute(
                pg_insert(Inventory)
                .values(product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal("0"))
                .on_conflict_do_nothing(index_elements=[Inventory.product_id, Inventory.warehouse_id])
            )
        locked_inventory = session.scalars(
            select(Inventory)
            .where(
                tuple_(Inventory.product_id, Inventory.warehouse_id).in_(inventory_keys)
            )
            .order_by(Inventory.product_id, Inventory.warehouse_id)
            .with_for_update()
        ).all() if inventory_keys else []
        inventory_by_key = {(row.product_id, row.warehouse_id): row for row in locked_inventory}
        required_by_key: dict[tuple[int, int], Decimal] = {}
        for item, product in stock_lines:
            if product is None or product.is_service:
                continue
            key = (product.id, item.warehouse_id)
            required_by_key[key] = required_by_key.get(key, Decimal("0")) + item.quantity
        shortages = []
        for key, quantity in required_by_key.items():
            available = inventory_by_key[key].quantity
            if available < quantity:
                product = session.get(Product, key[0])
                shortages.append(
                    {"product_id": key[0], "product_name": product.name if product else str(key[0]),
                     "warehouse_id": key[1], "required": _text(quantity), "available": _text(available)}
                )
        unknown_cost_products = sorted(
            {
                product.name
                for item, product in stock_lines
                if product is not None
                and not product.is_service
                and inventory_by_key[(product.id, item.warehouse_id)].quantity > 0
                and inventory_by_key[(product.id, item.warehouse_id)].average_unit_cost is None
            }
        )
        if unknown_cost_products:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Opening valuation is required before issuing stock for these products",
                    "products": unknown_cost_products,
                },
            )
        if shortages:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Insufficient stock to issue invoice", "shortages": shortages},
            )

        serial_ids = sorted({item.serial_id for item in items if item.serial_id is not None})
        if serial_ids:
            serials = session.scalars(
                select(ProductSerial)
                .where(ProductSerial.id.in_(serial_ids))
                .order_by(ProductSerial.id)
                .with_for_update()
            ).all()
            serial_by_id = {serial.id: serial for serial in serials}
            unavailable = [
                serial_id for serial_id in serial_ids
                if serial_id not in serial_by_id or serial_by_id[serial_id].status != "available"
                or serial_by_id[serial_id].warehouse_id != next(
                    item.warehouse_id for item in items if item.serial_id == serial_id
                )
            ]
            if unavailable:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"message": "Serials are no longer available in their saved warehouses", "serial_ids": unavailable},
                )

        for item, product in stock_lines:
            if product is None or product.is_service:
                continue
            inventory = inventory_by_key[(product.id, item.warehouse_id)]
            item.unit_cost = inventory.average_unit_cost
            inventory.quantity -= item.quantity
            session.add(
                StockMovement(
                    product_id=product.id,
                    warehouse_id=item.warehouse_id,
                    serial_id=item.serial_id,
                    movement_type="sale_out",
                    quantity_change=-item.quantity,
                    unit_cost=item.unit_cost,
                    ref_type="invoice",
                    ref_id=invoice.id,
                    created_by=user.id,
                )
            )
            if item.serial_id is not None:
                serial = session.get(ProductSerial, item.serial_id)
                if serial is None or serial.status != "available":
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A serialized product is no longer available")
                serial.status = "sold"
                serial.updated_at = datetime.now(timezone.utc)

        session.flush()

        invoice.invoice_number = invoice_number
        invoice.fy_start_year = fy_start_year
        invoice.issue_idempotency_key = idempotency_key
        invoice.issued_at = datetime.now(timezone.utc)
        invoice.issued_by = user.id
        invoice.status = "issued"
        invoice.updated_at = invoice.issued_at
        _snapshot_parties(invoice, settings, customer)
        invoice.hsn_summary = preview["hsn_summary"]

        cogs = sum(
            (
                (item.unit_cost or Decimal("0")) * item.quantity
                for item, product in stock_lines
                if product is not None and not product.is_service
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        for entry in post_sales_invoice(invoice.invoice_date, invoice_number, result, cogs):
            _persist_journal_entry(session, entry, user, invoice.id, customer_id=customer.id)
        _audit(session, user, request, "issue", invoice.id, old_values, _invoice_snapshot(invoice))
        session.commit()
        session.refresh(invoice)
        return _invoice_output(session, invoice)
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Invoice issue conflicts with current data: {error.orig}") from None
    except Exception:
        session.rollback()
        raise


@router.post("/invoices/{invoice_id}/cancel")
def cancel_invoice(
    invoice_id: int,
    body: CancelRequest,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:cancel"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    try:
        invoice = session.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
        if invoice is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        if invoice.doc_type == "proforma":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Proforma documents cannot be cancelled")
        if invoice.status != "issued":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only issued invoices can be cancelled")
        if session.scalar(
            select(PaymentAllocation.id)
            .join(Payment, Payment.id == PaymentAllocation.payment_id)
            .where(
                PaymentAllocation.invoice_id == invoice.id,
                Payment.direction == "in",
                Payment.status == "recorded",
            )
            .limit(1)
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice has payment allocations and cannot be cancelled")
        if session.scalar(
            select(CreditNote.id)
            .where(CreditNote.invoice_id == invoice.id, CreditNote.status == "issued")
            .limit(1)
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice has issued credit notes and cannot be cancelled")
        old_values = _invoice_snapshot(invoice)
        items = session.scalars(
            select(InvoiceItem).where(InvoiceItem.invoice_id == invoice.id).order_by(InvoiceItem.id)
        ).all()
        stock_items = []
        for item in items:
            if item.product_id is None:
                continue
            product = session.get(Product, item.product_id)
            if product is not None and not product.is_service:
                stock_items.append((item, product))
        inventory_keys = sorted({(item.product_id, item.warehouse_id) for item, _product in stock_items})
        locked_inventory = session.scalars(
            select(Inventory)
            .where(tuple_(Inventory.product_id, Inventory.warehouse_id).in_(inventory_keys))
            .order_by(Inventory.product_id, Inventory.warehouse_id)
            .with_for_update()
        ).all() if inventory_keys else []
        inventory_by_key = {(row.product_id, row.warehouse_id): row for row in locked_inventory}
        for item, product in stock_items:
            key = (product.id, item.warehouse_id)
            inventory = inventory_by_key.get(key)
            if inventory is None or item.warehouse_id is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Inventory row missing for product {product.name}")
            inventory.quantity += item.quantity
            session.add(
                StockMovement(
                    product_id=product.id,
                    warehouse_id=item.warehouse_id,
                    serial_id=item.serial_id,
                    movement_type="sales_return",
                    quantity_change=item.quantity,
                    unit_cost=item.unit_cost,
                    ref_type="invoice",
                    ref_id=invoice.id,
                    notes=body.reason,
                    created_by=user.id,
                )
            )
            if item.serial_id is not None:
                serial = session.scalar(
                    select(ProductSerial).where(ProductSerial.id == item.serial_id).with_for_update()
                )
                if serial is None or serial.status != "sold":
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A sold serial could not be restored")
                serial.status = "available"
                serial.updated_at = datetime.now(timezone.utc)

        original_entries = session.scalars(
            select(JournalEntry)
            .where(
                JournalEntry.source_type == "invoice",
                JournalEntry.source_id == invoice.id,
                JournalEntry.reverses_entry_id.is_(None),
            )
            .order_by(JournalEntry.id)
        ).all()
        for original in original_entries:
            lines = session.scalars(
                select(JournalEntryLine).where(JournalEntryLine.entry_id == original.id).order_by(JournalEntryLine.id)
            ).all()
            core_entry = CoreJournalEntry(
                entry_date=original.entry_date,
                reference=original.reference,
                narration=original.narration or "",
                lines=[CoreLine(line.account_code, line.debit, line.credit) for line in lines],
            )
            reversal = reverse_entry(core_entry, date.today(), body.reason)
            _persist_journal_entry(
                session,
                reversal,
                user,
                invoice.id,
                reverses_entry_id=original.id,
                customer_id=invoice.customer_id,
            )

        invoice.status = "cancelled"
        invoice.cancelled_at = datetime.now(timezone.utc)
        invoice.cancel_reason = body.reason
        invoice.updated_at = invoice.cancelled_at
        _audit(
            session,
            user,
            request,
            "cancel",
            invoice.id,
            old_values,
            {**_invoice_snapshot(invoice), "cancel_reason": body.reason},
        )
        session.commit()
        session.refresh(invoice)
        return _invoice_output(session, invoice)
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise


@router.post("/invoices/{invoice_id}/convert-to-tax-invoice")
def convert_proforma_to_tax_invoice(
    invoice_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:create"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    original = session.get(Invoice, invoice_id)
    if original is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if original.doc_type != "proforma" or original.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft proforma documents can be converted")
    customer = _customer(session, original.customer_id)
    settings = session.get(CompanySettings, 1)
    if settings is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Company settings are not configured")
    original_items = session.scalars(
        select(InvoiceItem).where(InvoiceItem.invoice_id == original.id).order_by(InvoiceItem.id)
    ).all()
    lines = _invoice_line_inputs(original_items)
    _active_customer, _settings, result, details, preview = _calculate(
        session,
        original.customer_id,
        original.place_of_supply_code,
        "tax_invoice",
        original.reverse_charge,
        lines,
    )
    converted = Invoice(
        doc_type="tax_invoice",
        customer_id=customer.id,
        salesperson_id=user.id,
        branch_id=user.branch_id,
        invoice_date=original.invoice_date,
        due_date=original.due_date,
        place_of_supply_code=original.place_of_supply_code,
        reverse_charge=original.reverse_charge,
        status="draft",
        payment_terms=original.payment_terms,
        notes=original.notes,
        created_by=user.id,
    )
    _snapshot_parties(converted, settings, customer)
    _apply_result(converted, result)
    converted.hsn_summary = preview["hsn_summary"]
    session.add(converted)
    session.flush()
    _save_items(session, converted, result, details)
    _audit(
        session,
        user,
        request,
        "convert_proforma",
        converted.id,
        {"source_invoice_id": original.id, "source_doc_type": original.doc_type},
        _invoice_snapshot(converted),
    )
    session.commit()
    session.refresh(converted)
    return _invoice_output(session, converted)


@router.get("/invoices/{invoice_id}/pdf")
def download_invoice_pdf(
    invoice_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    invoice = session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            dealer_customer_scope(user, Invoice.customer_id),
        )
    )
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if invoice.status not in {"issued", "cancelled"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PDF is available only for issued or cancelled invoices")
    items = session.scalars(
        select(InvoiceItem).where(InvoiceItem.invoice_id == invoice.id).order_by(InvoiceItem.id)
    ).all()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="InvoiceTitle", parent=styles["Title"], textColor=colors.HexColor("#174c3c"), fontSize=17, leading=20))
    styles.add(ParagraphStyle(name="SmallCell", parent=styles["BodyText"], fontSize=6, leading=7))
    styles.add(ParagraphStyle(name="Right", parent=styles["BodyText"], alignment=2))
    buffer = BytesIO()

    def page_decor(canvas, document) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#b79752"))
        canvas.setLineWidth(0.7)
        canvas.line(12 * mm, height - 10 * mm, width - 12 * mm, height - 10 * mm)
        canvas.setFillColor(colors.HexColor("#174c3c"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(12 * mm, 7 * mm, invoice.invoice_number or "")
        canvas.drawRightString(width - 12 * mm, 7 * mm, f"Page {document.page}")
        if invoice.status == "cancelled":
            canvas.saveState()
            canvas.translate(width / 2, height / 2)
            canvas.rotate(35)
            canvas.setFillColorRGB(0.75, 0.1, 0.1, alpha=0.16)
            canvas.setFont("Helvetica-Bold", 52)
            canvas.drawCentredString(0, 0, "CANCELLED")
            canvas.restoreState()
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=15 * mm,
        bottomMargin=14 * mm,
        title=invoice.invoice_number or "Invoice",
        author=invoice.seller_legal_name or "",
    )
    story = []
    logo_path = Path(invoice.seller_logo_path) if invoice.seller_logo_path else None
    if logo_path is not None and logo_path.is_file():
        story.append(PdfImage(str(logo_path), width=25 * mm, height=18 * mm, kind="proportional"))
    seller_title = invoice.seller_trade_name or invoice.seller_legal_name
    if seller_title:
        story.append(Paragraph(escape(seller_title), styles["InvoiceTitle"]))
    if invoice.seller_address:
        story.append(Paragraph(escape(invoice.seller_address).replace("\n", "<br/>"), styles["BodyText"]))
    if invoice.seller_gstin:
        story.append(Paragraph(f"GSTIN: {escape(invoice.seller_gstin)}", styles["BodyText"]))
    if invoice.seller_pan:
        story.append(Paragraph(f"PAN: {escape(invoice.seller_pan)}", styles["BodyText"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("TAX INVOICE" if invoice.doc_type == "tax_invoice" else "BILL OF SUPPLY", styles["Heading2"]))
    story.append(Paragraph(
        f"Invoice No: {escape(invoice.invoice_number or '')} &nbsp;&nbsp; "
        f"Date: {invoice.invoice_date.isoformat()} &nbsp;&nbsp; "
        f"Place of supply: {escape((session.get(State, invoice.place_of_supply_code).name if session.get(State, invoice.place_of_supply_code) else '') + ' (' + invoice.place_of_supply_code + ')')} "
        f"&nbsp;&nbsp; Reverse charge: {'Yes' if invoice.reverse_charge else 'No'}",
        styles["BodyText"],
    ))
    story.append(Spacer(1, 6))
    customer_lines = [f"Customer: {escape(invoice.customer_name or '')}"]
    if invoice.billing_address:
        customer_lines.append(f"Billing address: {escape(invoice.billing_address).replace(chr(10), '<br/>')}")
    if invoice.shipping_address:
        customer_lines.append(f"Shipping address: {escape(invoice.shipping_address).replace(chr(10), '<br/>')}")
    if invoice.customer_gstin:
        customer_lines.append(f"GSTIN: {escape(invoice.customer_gstin)}")
    story.append(Paragraph("<br/>".join(customer_lines), styles["BodyText"]))
    story.append(Spacer(1, 7))

    columns = ["Description / HSN", "Qty / unit", "Rate", "Discount", "Taxable", "GST / Cess %", "CGST", "SGST", "IGST", "Cess"]
    data: list[list[object]] = [[Paragraph(escape(value), styles["SmallCell"]) for value in columns]]
    for item in items:
        data.append(
            [
                Paragraph(f"{escape(item.description)}<br/>{escape(item.hsn_sac)}", styles["SmallCell"]),
                f"{_text(item.quantity)} {escape(item.unit or '')}",
                _text(item.unit_price),
                _text(item.discount),
                _text(item.taxable_value),
                f"{_text(item.gst_rate)} / {_text(item.cess_rate)}",
                _text(item.cgst),
                _text(item.sgst),
                _text(item.igst),
                _text(item.cess),
            ]
        )
    widths = [47 * mm, 12 * mm, 18 * mm, 16 * mm, 18 * mm, 12 * mm, 14 * mm, 14 * mm, 14 * mm, 12 * mm]
    table = Table(data, colWidths=widths, repeatRows=1, splitByRow=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174c3c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c6c6c6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f3ec")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))
    summary = invoice.hsn_summary or []
    if summary:
        story.append(Paragraph("HSN/SAC Summary", styles["Heading3"]))
        summary_data = [["HSN/SAC", "GST %", "Taxable", "CGST", "SGST", "IGST", "Cess"]]
        summary_data.extend([
            [row["hsn_sac"], row["gst_rate"], row["taxable"], row["cgst"], row["sgst"], row["igst"], row["cess"]]
            for row in summary
        ])
        summary_table = Table(summary_data, repeatRows=1, hAlign="LEFT")
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7e1d1")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c6c6c6")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
        ]))
        story.append(summary_table)
    story.append(Spacer(1, 8))
    for label, amount in (
        ("Taxable total", invoice.taxable_total),
        ("CGST", invoice.cgst_total),
        ("SGST", invoice.sgst_total),
        ("IGST", invoice.igst_total),
        ("Cess", invoice.cess_total),
        ("Round off", invoice.round_off),
        ("Grand total", invoice.grand_total),
    ):
        story.append(Paragraph(f"{label}: {_text(amount)}", styles["Right"]))
    story.append(Paragraph(f"Amount in words: {escape(amount_in_words(invoice.grand_total))}", styles["BodyText"]))
    if invoice.payment_terms:
        story.append(Paragraph(f"Payment terms: {escape(invoice.payment_terms)}", styles["BodyText"]))
    bank_values = [
        ("Bank", invoice.seller_bank_name),
        ("Account", invoice.seller_bank_account_no),
        ("IFSC", invoice.seller_bank_ifsc),
        ("UPI", invoice.seller_upi_id),
    ]
    bank_lines = [f"{label}: {escape(value)}" for label, value in bank_values if value]
    if bank_lines:
        story.append(Paragraph("<br/>".join(bank_lines), styles["BodyText"]))
    if invoice.seller_terms_and_conditions:
        story.append(Paragraph(escape(invoice.seller_terms_and_conditions).replace("\n", "<br/>"), styles["BodyText"]))
    if invoice.seller_authorized_signatory:
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"Authorized signatory: {escape(invoice.seller_authorized_signatory)}", styles["Right"]))

    doc.build(story, onFirstPage=page_decor, onLaterPages=page_decor)
    _audit(
        session,
        user,
        request,
        "download_pdf",
        invoice.id,
        None,
        {"invoice_number": invoice.invoice_number, "grand_total": _text(invoice.grand_total)},
    )
    session.commit()
    buffer.seek(0)
    filename = f"{invoice.invoice_number or invoice.id}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
