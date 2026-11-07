from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Literal, Protocol
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import (
    AuditLog,
    CompanySettings,
    CreditNote,
    CreditNoteItem,
    Customer,
    GstRate,
    Inventory,
    Invoice,
    InvoiceItem,
    JournalEntry,
    JournalEntryLine,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    Product,
    ProductSerial,
    StockMovement,
    User,
)
from berev_core.accounting import (
    JournalEntry as CoreJournalEntry,
    Line as CoreLine,
    post_credit_note,
    post_customer_receipt,
    post_customer_refund,
    reverse_entry,
)
from berev_core.gst import GSTError, InvoiceResult, LineInput, compute_invoice, q2
from berev_core.numbering import format_invoice_number, next_sequence, series_key
from berev_core.payments import (
    OpenInvoice,
    PaymentError,
    allocate_fifo,
    invoice_status,
    validate_allocations,
)
from berev_core.words import amount_in_words

router = APIRouter(tags=["payments"])
ZERO = Decimal("0.00")


class AllocationInput(BaseModel):
    invoice_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class ReceiptWrite(BaseModel):
    customer_id: int = Field(gt=0)
    method_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_date: date
    reference_no: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)
    allocations: list[AllocationInput] | None = Field(default=None, max_length=100)


class RefundWrite(BaseModel):
    customer_id: int = Field(gt=0)
    method_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_date: date
    source: Literal["advance", "invoice_credit"]
    invoice_id: int | None = Field(default=None, gt=0)
    reference_no: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)


class CreditNoteLineWrite(BaseModel):
    invoice_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class CreditNoteWrite(BaseModel):
    invoice_id: int = Field(gt=0)
    note_date: date
    reason: str = Field(min_length=10, max_length=2000)
    restock: bool = False
    lines: list[CreditNoteLineWrite] = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("Credit note reason must be at least 10 characters")
        return normalized


class GatewayReceipt(BaseModel):
    customer_id: int = Field(gt=0)
    method_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_date: date
    gateway_txn_id: str = Field(min_length=1, max_length=255)
    reference_no: str | None = Field(default=None, max_length=255)
    allocations: list[AllocationInput] | None = Field(default=None, max_length=100)


class SignatureVerifier(Protocol):
    def verify(self, payload: bytes, signature: str, secret: str) -> bool: ...


class HmacSHA256Verifier:
    def verify(self, payload: bytes, signature: str, secret: str) -> bool:
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        supplied = signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, supplied)


def _text(value: Decimal | int) -> str:
    return format(value, "f")


def _audit(
    session: Session,
    user_id: int | None,
    request: Request,
    action: str,
    entity_type: str,
    entity_id: int,
    old_values: dict[str, object] | None,
    new_values: dict[str, object] | None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=request.client.host if request.client else None,
        )
    )


def _persist_entry(
    session: Session,
    entry: CoreJournalEntry,
    user_id: int | None,
    source_type: str,
    source_id: int,
    customer_id: int | None = None,
    reverses_entry_id: int | None = None,
) -> JournalEntry:
    row = JournalEntry(
        entry_date=entry.entry_date,
        reference=entry.reference,
        narration=entry.narration,
        source_type=source_type,
        source_id=source_id,
        reverses_entry_id=reverses_entry_id,
        posted_by=user_id,
    )
    session.add(row)
    session.flush()
    session.add_all(
        [
            JournalEntryLine(
                entry_id=row.id,
                account_code=line.account,
                debit=line.debit,
                credit=line.credit,
                customer_id=customer_id if line.account in {"1100", "2101"} else None,
            )
            for line in entry.lines
        ]
    )
    return row


def _number(session: Session, settings: CompanySettings, kind: str, marker: str, on_date: date) -> str:
    prefix = settings.invoice_prefix + marker
    series = series_key(kind, settings.invoice_prefix, on_date, settings.fy_start_month)
    raw_connection = session.connection().connection.driver_connection
    with raw_connection.cursor() as cursor:
        sequence = next_sequence(cursor, series, "format")
    try:
        return format_invoice_number(prefix, on_date, sequence, fy_start_month=settings.fy_start_month)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


def _invoice_paid(session: Session, invoice_id: int) -> Decimal:
    return session.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.invoice_id == invoice_id,
            Payment.direction == "in",
            Payment.status == "recorded",
        )
    ) or ZERO


def _invoice_credits(session: Session, invoice_id: int) -> Decimal:
    return session.scalar(
        select(func.coalesce(func.sum(CreditNote.grand_total), 0)).where(
            CreditNote.invoice_id == invoice_id,
            CreditNote.status == "issued",
        )
    ) or ZERO


def _invoice_refunds(session: Session, invoice_id: int) -> Decimal:
    return session.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.invoice_id == invoice_id,
            Payment.direction == "out",
            Payment.status == "recorded",
            Payment.is_advance.is_(False),
            Payment.reverses_payment_id.is_(None),
        )
    ) or ZERO


def invoice_balance(session: Session, invoice: Invoice) -> dict[str, str]:
    paid = Decimal(_invoice_paid(session, invoice.id))
    credited = Decimal(_invoice_credits(session, invoice.id))
    refunded = Decimal(_invoice_refunds(session, invoice.id))
    outstanding = Decimal(invoice.grand_total) - paid - credited + refunded
    is_fully_credited = credited >= Decimal(invoice.grand_total)
    try:
        payment_status = invoice_status(
            invoice.grand_total,
            min(Decimal(invoice.grand_total), paid + credited),
            invoice.due_date,
            date.today(),
            refunded=is_fully_credited,
        )
    except PaymentError as error:
        raise HTTPException(status_code=409, detail=f"Invoice balance is inconsistent: {error}") from None
    return {
        "amount_received": _text(paid),
        "credited_amount": _text(credited),
        "outstanding": _text(max(ZERO, outstanding)),
        "customer_credit": _text(max(ZERO, -outstanding)),
        "payment_status": payment_status,
    }


def _active_invoice_balances(session: Session, customer_id: int) -> list[OpenInvoice]:
    invoices = session.scalars(
        select(Invoice)
        .where(
            Invoice.customer_id == customer_id,
            Invoice.status == "issued",
            Invoice.doc_type.in_(("tax_invoice", "bill_of_supply")),
        )
        .order_by(Invoice.invoice_date, Invoice.id)
        .with_for_update()
    ).all()
    result = []
    for invoice in invoices:
        due = Decimal(invoice.grand_total) - _invoice_paid(session, invoice.id) - _invoice_credits(session, invoice.id)
        if due > 0:
            result.append(OpenInvoice(invoice.id, invoice.invoice_date, due))
    return result


def _record_receipt(
    session: Session,
    request: Request,
    body: ReceiptWrite,
    user_id: int | None,
    gateway_txn_id: str | None = None,
    verified_gateway: bool = False,
) -> Payment:
    customer = session.scalar(
        select(Customer).where(
            Customer.id == body.customer_id,
            Customer.deleted_at.is_(None),
            Customer.is_active.is_(True),
        )
    )
    if customer is None:
        raise HTTPException(status_code=422, detail="Active customer not found")
    method = session.scalar(
        select(PaymentMethod).where(PaymentMethod.id == body.method_id, PaymentMethod.is_active.is_(True))
    )
    if method is None:
        raise HTTPException(status_code=422, detail="Active payment method not found")
    if gateway_txn_id and session.scalar(select(Payment.id).where(Payment.gateway_txn_id == gateway_txn_id)):
        raise HTTPException(status_code=409, detail="Gateway transaction has already been processed")

    requested_ids = [item.invoice_id for item in (body.allocations or [])]
    if len(set(requested_ids)) != len(requested_ids):
        raise HTTPException(status_code=422, detail="Duplicate invoice allocations are not allowed")
    open_invoices = _active_invoice_balances(session, customer.id)
    balances = {item.invoice_id: item.balance for item in open_invoices}
    if body.allocations is None:
        try:
            allocations, advance = allocate_fifo(body.amount, open_invoices)
        except PaymentError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    else:
        explicit = {item.invoice_id: item.amount for item in body.allocations}
        if any(invoice_id not in balances for invoice_id in explicit):
            raise HTTPException(status_code=409, detail="Allocation target must be an issued invoice for this customer with an outstanding balance")
        try:
            advance = validate_allocations(body.amount, explicit, balances)
        except PaymentError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        allocations = sorted(explicit.items())

    settings = session.scalar(select(CompanySettings).where(CompanySettings.id == 1).with_for_update())
    if settings is None:
        raise HTTPException(status_code=409, detail="Company settings are not configured")
    payment = Payment(
        payment_number=_number(session, settings, "payment", "P", body.payment_date),
        direction="in",
        customer_id=customer.id,
        method_id=method.id,
        amount=body.amount,
        payment_date=body.payment_date,
        reference_no=body.reference_no,
        is_advance=advance > 0,
        status="recorded",
        gateway_txn_id=gateway_txn_id,
        gateway_signature_verified=verified_gateway,
        notes=body.notes,
        created_by=user_id,
    )
    session.add(payment)
    session.flush()
    for invoice_id, amount in allocations:
        session.add(PaymentAllocation(payment_id=payment.id, invoice_id=invoice_id, amount=amount))

    allocated_total = sum((amount for _, amount in allocations), ZERO)
    if allocated_total > 0:
        entry = post_customer_receipt(body.payment_date, payment.payment_number, allocated_total, method.ledger_kind)
        _persist_entry(session, entry, user_id, "payment", payment.id, customer.id)
    if advance > 0:
        entry = post_customer_receipt(body.payment_date, payment.payment_number, advance, method.ledger_kind, advance=True)
        _persist_entry(session, entry, user_id, "payment", payment.id, customer.id)
    _audit(
        session,
        user_id,
        request,
        "record_gateway_receipt" if verified_gateway else "record_receipt",
        "payment",
        payment.id,
        None,
        {
            "payment_number": payment.payment_number,
            "customer_id": customer.id,
            "amount": _text(payment.amount),
            "allocations": [{"invoice_id": invoice_id, "amount": _text(amount)} for invoice_id, amount in allocations],
            "advance": _text(advance),
            "gateway_signature_verified": verified_gateway,
        },
    )
    session.commit()
    session.refresh(payment)
    return payment


def _payment_output(session: Session, payment: Payment) -> dict[str, object]:
    customer = session.get(Customer, payment.customer_id) if payment.customer_id else None
    method = session.get(PaymentMethod, payment.method_id)
    allocations = session.scalars(
        select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id).order_by(PaymentAllocation.id)
    ).all()
    return {
        "id": payment.id,
        "payment_number": payment.payment_number,
        "direction": payment.direction,
        "customer_id": payment.customer_id,
        "customer_name": customer.name if customer else None,
        "method_id": payment.method_id,
        "method": method.name if method else None,
        "amount": _text(payment.amount),
        "payment_date": payment.payment_date.isoformat(),
        "reference_no": payment.reference_no,
        "status": payment.status,
        "is_advance": payment.is_advance,
        "reverses_payment_id": payment.reverses_payment_id,
        "gateway_signature_verified": payment.gateway_signature_verified,
        "allocations": [
            {"invoice_id": item.invoice_id, "amount": _text(item.amount)}
            for item in allocations
            if item.invoice_id is not None
        ],
    }


@router.get("/payment-methods")
def payment_methods(
    _: Annotated[User, Depends(require_permissions("payments:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    return [
        {"id": item.id, "code": item.code, "name": item.name, "ledger_kind": item.ledger_kind}
        for item in session.scalars(
            select(PaymentMethod).where(PaymentMethod.is_active.is_(True)).order_by(PaymentMethod.name)
        ).all()
    ]


@router.get("/payments")
def list_payments(
    _: Annotated[User, Depends(require_permissions("payments:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
) -> dict[str, object]:
    filters = []
    if q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(Payment.payment_number.ilike(pattern))
    total = session.scalar(select(func.count(Payment.id)).where(*filters)) or 0
    rows = session.scalars(
        select(Payment)
        .where(*filters)
        .order_by(Payment.payment_date.desc(), Payment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"items": [_payment_output(session, row) for row in rows], "total": total}


@router.get("/invoices/{invoice_id}/payments")
def invoice_payments(
    invoice_id: int,
    _: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    payment_ids = session.scalars(
        select(PaymentAllocation.payment_id)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.invoice_id == invoice_id,
        )
        .distinct()
        .order_by(PaymentAllocation.payment_id)
    ).all()
    payments = session.scalars(
        select(Payment).where(Payment.id.in_(payment_ids)).order_by(Payment.payment_date, Payment.id)
    ).all() if payment_ids else []
    return {
        **invoice_balance(session, invoice),
        "items": [_payment_output(session, payment) for payment in payments],
    }


@router.post("/payments", status_code=status.HTTP_201_CREATED)
def create_payment(
    body: ReceiptWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("payments:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    try:
        payment = _record_receipt(session, request, body, user.id)
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Payment conflicts with an existing transaction") from None
    return _payment_output(session, payment)


def _available_customer_credit(
    session: Session,
    customer_id: int,
    source: Literal["advance", "invoice_credit"],
    invoice_id: int | None,
) -> Decimal:
    if source == "advance":
        receipts = session.scalars(
            select(Payment).where(
                Payment.customer_id == customer_id,
                Payment.direction == "in",
                Payment.status == "recorded",
                Payment.reverses_payment_id.is_(None),
            )
        ).all()
        available = ZERO
        for payment in receipts:
            allocated = session.scalar(
                select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(
                    PaymentAllocation.payment_id == payment.id
                )
            ) or ZERO
            available += Decimal(payment.amount) - Decimal(allocated)
        refunded = session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.customer_id == customer_id,
                Payment.direction == "out",
                Payment.status == "recorded",
                Payment.is_advance.is_(True),
                Payment.reverses_payment_id.is_(None),
            )
        ) or ZERO
        return max(ZERO, available - Decimal(refunded))

    if invoice_id is None:
        raise HTTPException(status_code=422, detail="An invoice is required when refunding invoice credit")
    invoice = session.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.customer_id == customer_id,
            Invoice.status == "issued",
            Invoice.doc_type.in_(("tax_invoice", "bill_of_supply")),
        )
    )
    if invoice is None:
        raise HTTPException(status_code=409, detail="Invoice credit refunds require an issued invoice for this customer")
    return Decimal(invoice_balance(session, invoice)["customer_credit"])


@router.post("/payments/refunds", status_code=status.HTTP_201_CREATED)
def create_refund(
    body: RefundWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("payments:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    try:
        customer = session.scalar(
            select(Customer).where(
                Customer.id == body.customer_id,
                Customer.deleted_at.is_(None),
                Customer.is_active.is_(True),
            )
        )
        if customer is None:
            raise HTTPException(status_code=422, detail="Active customer not found")
        method = session.scalar(
            select(PaymentMethod).where(
                PaymentMethod.id == body.method_id,
                PaymentMethod.is_active.is_(True),
            )
        )
        if method is None:
            raise HTTPException(status_code=422, detail="Active payment method not found")

        locked_customer = session.scalar(
            select(Customer).where(Customer.id == customer.id).with_for_update()
        )
        if locked_customer is None:
            raise HTTPException(status_code=409, detail="Customer changed while refund was being recorded")
        session.scalars(
            select(Invoice)
            .where(Invoice.customer_id == customer.id)
            .order_by(Invoice.id)
            .with_for_update()
        ).all()
        if body.source == "advance" and body.invoice_id is not None:
            raise HTTPException(status_code=422, detail="Advance refunds cannot include an invoice")
        available = _available_customer_credit(session, customer.id, body.source, body.invoice_id)
        if body.amount > available:
            source_label = "customer advance" if body.source == "advance" else "invoice credit for the selected invoice"
            raise HTTPException(
                status_code=409,
                detail=f"Refund exceeds available {source_label} ({available})",
            )
        settings = session.scalar(select(CompanySettings).where(CompanySettings.id == 1).with_for_update())
        if settings is None:
            raise HTTPException(status_code=409, detail="Company settings are not configured")
        payment = Payment(
            payment_number=_number(session, settings, "payment", "P", body.payment_date),
            direction="out",
            customer_id=customer.id,
            method_id=method.id,
            amount=body.amount,
            payment_date=body.payment_date,
            reference_no=body.reference_no,
            is_advance=body.source == "advance",
            status="recorded",
            notes=body.notes,
            created_by=user.id,
        )
        session.add(payment)
        session.flush()
        if body.source == "invoice_credit":
            session.add(PaymentAllocation(payment_id=payment.id, invoice_id=body.invoice_id, amount=body.amount))
        entry = post_customer_refund(
            body.payment_date,
            payment.payment_number,
            body.amount,
            method.ledger_kind,
            advance=body.source == "advance",
        )
        _persist_entry(session, entry, user.id, "payment", payment.id, customer.id)
        _audit(
            session,
            user.id,
            request,
            "record_customer_refund",
            "payment",
            payment.id,
            None,
            {
                "payment_number": payment.payment_number,
                "customer_id": customer.id,
                "amount": _text(payment.amount),
                "source": body.source,
                "invoice_id": body.invoice_id,
            },
        )
        session.commit()
        session.refresh(payment)
        return _payment_output(session, payment)
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Refund conflicts with an existing transaction") from None


@router.post("/payments/gateway/webhook", status_code=status.HTTP_201_CREATED)
async def gateway_webhook(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    signature: Annotated[str | None, Header(alias="X-Gateway-Signature")] = None,
) -> dict[str, object]:
    configured_secret = request.app.state.settings.gateway_webhook_secret
    if configured_secret is None or not configured_secret.get_secret_value():
        raise HTTPException(status_code=503, detail="Gateway payments are disabled")
    raw = await request.body()
    if not signature or not HmacSHA256Verifier().verify(raw, signature, configured_secret.get_secret_value()):
        raise HTTPException(status_code=401, detail="Invalid gateway signature")
    try:
        payload = GatewayReceipt.model_validate_json(raw)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    try:
        body = ReceiptWrite(
            customer_id=payload.customer_id,
            method_id=payload.method_id,
            amount=payload.amount,
            payment_date=payload.payment_date,
            reference_no=payload.reference_no,
            allocations=payload.allocations,
        )
        payment = _record_receipt(
            session,
            request,
            body,
            None,
            gateway_txn_id=payload.gateway_txn_id,
            verified_gateway=True,
        )
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Gateway transaction conflicts with existing data") from None
    return _payment_output(session, payment)


@router.post("/payments/{payment_id}/reverse")
def reverse_payment(
    payment_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("payments:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    try:
        original = session.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
        if original is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if original.status != "recorded" or original.direction not in {"in", "out"}:
            raise HTTPException(status_code=409, detail="Only recorded customer payments can be reversed")
        if session.scalar(select(Payment.id).where(Payment.reverses_payment_id == original.id)):
            raise HTTPException(status_code=409, detail="Payment has already been reversed")
        allocations = session.scalars(
            select(PaymentAllocation).where(PaymentAllocation.payment_id == original.id).order_by(PaymentAllocation.invoice_id)
        ).all()
        invoice_ids = sorted(item.invoice_id for item in allocations if item.invoice_id is not None)
        if invoice_ids:
            session.scalars(
                select(Invoice).where(Invoice.id.in_(invoice_ids)).order_by(Invoice.id).with_for_update()
            ).all()
        settings = session.scalar(select(CompanySettings).where(CompanySettings.id == 1).with_for_update())
        if settings is None:
            raise HTTPException(status_code=409, detail="Company settings are not configured")
        reversal = Payment(
            payment_number=_number(session, settings, "payment", "P", date.today()),
            direction="out" if original.direction == "in" else "in",
            customer_id=original.customer_id,
            method_id=original.method_id,
            amount=original.amount,
            payment_date=date.today(),
            reference_no=original.reference_no,
            is_advance=original.is_advance,
            status="recorded",
            reverses_payment_id=original.id,
            notes=f"Reversal of {original.payment_number}",
            created_by=user.id,
        )
        session.add(reversal)
        session.flush()
        original.status = "reversed"
        original_journals = session.scalars(
            select(JournalEntry)
            .where(
                JournalEntry.source_type == "payment",
                JournalEntry.source_id == original.id,
                JournalEntry.reverses_entry_id.is_(None),
            )
            .order_by(JournalEntry.id)
        ).all()
        for entry in original_journals:
            lines = session.scalars(
                select(JournalEntryLine).where(JournalEntryLine.entry_id == entry.id).order_by(JournalEntryLine.id)
            ).all()
            reversal_entry = reverse_entry(
                CoreJournalEntry(
                    entry_date=entry.entry_date,
                    reference=entry.reference,
                    narration=entry.narration or "",
                    lines=[CoreLine(item.account_code, item.debit, item.credit) for item in lines],
                ),
                date.today(),
                f"Payment reversal {original.payment_number}",
            )
            _persist_entry(
                session,
                reversal_entry,
                user.id,
                "payment",
                reversal.id,
                original.customer_id,
                entry.id,
            )
        _audit(
            session,
            user.id,
            request,
            "reverse_customer_payment",
            "payment",
            original.id,
            {"status": "recorded"},
            {"status": "reversed", "reversal_payment_id": reversal.id, "amount": _text(original.amount)},
        )
        session.commit()
        session.refresh(reversal)
        return _payment_output(session, reversal)
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Payment reversal conflicts with current data: {error.orig}") from None


@router.get("/payments/{payment_id}/pdf")
def payment_pdf(
    payment_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("payments:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    output = _payment_output(session, payment)
    story = [
        Paragraph("BEREV ERP — Receipt", getSampleStyleSheet()["Title"]),
        Spacer(1, 8 * mm),
        Paragraph(f"Receipt: {escape(payment.payment_number)}", getSampleStyleSheet()["BodyText"]),
        Paragraph(f"Date: {payment.payment_date.isoformat()}", getSampleStyleSheet()["BodyText"]),
        Paragraph(f"Customer: {escape(str(output['customer_name'] or ''))}", getSampleStyleSheet()["BodyText"]),
        Paragraph(f"Method: {escape(str(output['method'] or ''))}", getSampleStyleSheet()["BodyText"]),
        Paragraph(f"Reference: {escape(payment.reference_no or '')}", getSampleStyleSheet()["BodyText"]),
        Paragraph(f"Amount: INR {_text(payment.amount)}", getSampleStyleSheet()["BodyText"]),
    ]
    rows = [["Invoice ID", "Allocated amount"]]
    rows.extend([[str(item["invoice_id"]), str(item["amount"])] for item in output["allocations"]])
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174c3c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5d1")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([Spacer(1, 8 * mm), table])
    if payment.status == "reversed":
        story.append(Paragraph("REVERSED", getSampleStyleSheet()["Heading2"]))
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm)
    doc.build(story)
    _audit(session, user.id, request, "download_receipt_pdf", "payment", payment.id, None, {"payment_number": payment.payment_number})
    session.commit()
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{payment.payment_number}.pdf"'},
    )


@router.get("/credit-notes")
def list_credit_notes(
    _: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    invoice_id: int | None = Query(default=None, gt=0),
) -> dict[str, object]:
    filters = [CreditNote.status == "issued"]
    if invoice_id is not None:
        filters.append(CreditNote.invoice_id == invoice_id)
    total = session.scalar(select(func.count(CreditNote.id)).where(*filters)) or 0
    notes = session.scalars(
        select(CreditNote).where(*filters).order_by(CreditNote.note_date.desc(), CreditNote.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": note.id,
                "note_number": note.note_number,
                "invoice_id": note.invoice_id,
                "note_date": note.note_date.isoformat(),
                "reason": note.reason,
                "grand_total": _text(note.grand_total),
                "restock": note.restock,
            }
            for note in notes
        ],
        "total": total,
    }


def _credit_note_output(session: Session, note: CreditNote) -> dict[str, object]:
    items = session.scalars(
        select(CreditNoteItem).where(CreditNoteItem.credit_note_id == note.id).order_by(CreditNoteItem.id)
    ).all()
    return {
        "id": note.id,
        "note_number": note.note_number,
        "invoice_id": note.invoice_id,
        "note_date": note.note_date.isoformat(),
        "reason": note.reason,
        "status": note.status,
        "restock": note.restock,
        "taxable_total": _text(note.taxable_total),
        "cgst_total": _text(note.cgst_total),
        "sgst_total": _text(note.sgst_total),
        "igst_total": _text(note.igst_total),
        "cess_total": _text(note.cess_total),
        "round_off": _text(note.round_off),
        "grand_total": _text(note.grand_total),
        "amount_in_words": amount_in_words(note.grand_total),
        "hsn_summary": note.hsn_summary or [],
        "lines": [
            {
                "invoice_item_id": item.invoice_item_id,
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
    }


@router.get("/credit-notes/{credit_note_id}")
def get_credit_note(
    credit_note_id: int,
    _: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    note = session.get(CreditNote, credit_note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Credit note not found")
    return _credit_note_output(session, note)


@router.post("/credit-notes", status_code=status.HTTP_201_CREATED)
def create_credit_note(
    body: CreditNoteWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:create"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    try:
        reason = body.reason.strip()
        if len(reason) < 10:
            raise HTTPException(status_code=422, detail="Credit note reason must be at least 10 characters")
        invoice = session.scalar(select(Invoice).where(Invoice.id == body.invoice_id).with_for_update())
        if invoice is None or invoice.status != "issued" or invoice.doc_type == "proforma":
            raise HTTPException(status_code=409, detail="Credit notes require an issued tax invoice or bill of supply")
        ids = [line.invoice_item_id for line in body.lines]
        if len(set(ids)) != len(ids):
            raise HTTPException(status_code=422, detail="Each invoice line may appear only once")
        source_items = session.scalars(
            select(InvoiceItem)
            .where(InvoiceItem.invoice_id == invoice.id, InvoiceItem.id.in_(ids))
            .order_by(InvoiceItem.id)
        ).all()
        source_by_id = {item.id: item for item in source_items}
        if len(source_by_id) != len(ids):
            raise HTTPException(status_code=422, detail="A credit-note line does not belong to the selected invoice")
        prior_quantities = dict(
            session.execute(
                select(CreditNoteItem.invoice_item_id, func.sum(CreditNoteItem.quantity))
                .join(CreditNote, CreditNote.id == CreditNoteItem.credit_note_id)
                .where(CreditNote.status == "issued", CreditNote.invoice_id == invoice.id)
                .group_by(CreditNoteItem.invoice_item_id)
            ).all()
        )
        settings = session.scalar(select(CompanySettings).where(CompanySettings.id == 1).with_for_update())
        if settings is None:
            raise HTTPException(status_code=409, detail="Company settings are not configured")
        seller_state = invoice.seller_state_code
        if seller_state is None:
            raise HTTPException(status_code=409, detail="Original invoice has no seller state snapshot; cannot safely calculate a credit note")
        allowed_rates = {
            Decimal(rate)
            for rate in session.scalars(select(GstRate.rate).where(GstRate.is_active.is_(True))).all()
        }
        inputs: list[LineInput] = []
        details: list[tuple[InvoiceItem, Decimal, Decimal]] = []
        for line in body.lines:
            source = source_by_id[line.invoice_item_id]
            remaining_quantity = Decimal(source.quantity) - Decimal(prior_quantities.get(source.id, 0))
            if line.quantity > remaining_quantity:
                raise HTTPException(
                    status_code=409,
                    detail=f"Credit quantity for invoice line {source.id} exceeds its remaining returnable quantity",
                )
            if source.serial_id is not None and (line.quantity != Decimal("1") or remaining_quantity != Decimal("1")):
                raise HTTPException(status_code=409, detail=f"Serialized invoice line {source.id} must be credited as a whole unit")
            if source.gst_rate not in allowed_rates:
                raise HTTPException(status_code=422, detail="Original invoice tax rate is not active in GST settings")
            discount = q2(Decimal(source.discount) * line.quantity / Decimal(source.quantity))
            inputs.append(
                LineInput(
                    source.description,
                    source.hsn_sac,
                    line.quantity,
                    source.unit_price,
                    source.gst_rate,
                    0,
                    discount,
                    source.cess_rate,
                    source.unit,
                )
            )
            details.append((source, line.quantity, discount))
        try:
            result = compute_invoice(
                inputs,
                seller_state,
                invoice.place_of_supply_code,
                tax_applicable=invoice.doc_type == "tax_invoice",
                allowed_rates=allowed_rates,
            )
        except GSTError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        note_number = _number(session, settings, "credit_note", "C", body.note_date)
        note = CreditNote(
            note_number=note_number,
            invoice_id=invoice.id,
            note_date=body.note_date,
            reason=reason,
            taxable_total=result.taxable_total,
            cgst_total=result.cgst_total,
            sgst_total=result.sgst_total,
            igst_total=result.igst_total,
            cess_total=result.cess_total,
            round_off=result.round_off,
            grand_total=result.grand_total,
            restock=body.restock,
            hsn_summary=[
                {
                    "hsn_sac": row.hsn_sac,
                    "taxable": _text(row.taxable),
                    "cgst": _text(row.cgst),
                    "sgst": _text(row.sgst),
                    "igst": _text(row.igst),
                    "cess": _text(row.cess),
                }
                for row in result.hsn_summary
            ],
            status="draft",
            created_by=user.id,
        )
        session.add(note)
        session.flush()

        stock_lines = [
            (source, quantity)
            for source, quantity, _discount in details
            if body.restock and source.product_id is not None
        ]
        products = {
            product.id: product
            for product in session.scalars(
                select(Product).where(Product.id.in_([item.product_id for item, _ in stock_lines])).order_by(Product.id)
            ).all()
        } if stock_lines else {}
        stock_lines = [
            (source, quantity)
            for source, quantity in stock_lines
            if source.product_id in products and not products[source.product_id].is_service
        ]
        inventory_keys = sorted({(source.product_id, source.warehouse_id) for source, _ in stock_lines})
        inventory_rows = session.scalars(
            select(Inventory)
            .where(tuple_(Inventory.product_id, Inventory.warehouse_id).in_(inventory_keys))
            .order_by(Inventory.product_id, Inventory.warehouse_id)
            .with_for_update()
        ).all() if inventory_keys else []
        inventory_by_key = {(row.product_id, row.warehouse_id): row for row in inventory_rows}

        cogs_reversal = ZERO
        credit_items: list[CreditNoteItem] = []
        for calculated, (source, quantity, discount) in zip(result.lines, details, strict=True):
            credit_items.append(
                CreditNoteItem(
                    credit_note_id=note.id,
                    invoice_item_id=source.id,
                    quantity=quantity,
                    taxable_value=calculated.taxable,
                    description=calculated.description,
                    hsn_sac=calculated.hsn_sac,
                    unit=calculated.unit,
                    unit_price=calculated.unit_price,
                    discount=calculated.discount,
                    gst_rate=calculated.gst_rate,
                    cess_rate=calculated.cess_rate,
                    cgst=calculated.cgst,
                    sgst=calculated.sgst,
                    igst=calculated.igst,
                    cess=calculated.cess,
                    serial_id=source.serial_id,
                    warehouse_id=source.warehouse_id,
                    unit_cost=source.unit_cost,
                )
            )
            if body.restock and source.product_id in products and not products[source.product_id].is_service:
                key = (source.product_id, source.warehouse_id)
                inventory = inventory_by_key.get(key)
                if inventory is None or source.warehouse_id is None:
                    raise HTTPException(status_code=409, detail=f"Inventory row missing for product {products[source.product_id].name}")
                inventory.quantity += quantity
                session.add(
                    StockMovement(
                        product_id=source.product_id,
                        warehouse_id=source.warehouse_id,
                        serial_id=source.serial_id,
                        movement_type="sales_return",
                        quantity_change=quantity,
                        unit_cost=source.unit_cost,
                        ref_type="credit_note",
                        ref_id=note.id,
                        notes=reason,
                        created_by=user.id,
                    )
                )
                cogs_reversal += (source.unit_cost or ZERO) * quantity
                if source.serial_id is not None:
                    serial = session.scalar(
                        select(ProductSerial).where(ProductSerial.id == source.serial_id).with_for_update()
                    )
                    if serial is None or serial.status != "sold":
                        raise HTTPException(status_code=409, detail="Serialized item is not currently sold and cannot be restocked")
                    serial.status = "available"
                    serial.updated_at = datetime.now(timezone.utc)
        session.add_all(credit_items)
        session.flush()
        note.status = "issued"
        session.flush()
        accounting_entries = post_credit_note(body.note_date, note.note_number, result, cogs_reversal)
        for entry in accounting_entries:
            _persist_entry(session, entry, user.id, "credit_note", note.id, invoice.customer_id)
        _audit(
            session,
            user.id,
            request,
            "issue_credit_note",
            "credit_note",
            note.id,
            None,
            {
                "note_number": note.note_number,
                "invoice_id": invoice.id,
                "grand_total": _text(note.grand_total),
                "restock": note.restock,
            },
        )
        session.commit()
        session.refresh(note)
        return _credit_note_output(session, note)
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Credit note conflicts with current data: {error.orig}") from None


@router.get("/credit-notes/{credit_note_id}/pdf")
def credit_note_pdf(
    credit_note_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("sales:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    note = session.get(CreditNote, credit_note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Credit note not found")
    output = _credit_note_output(session, note)
    invoice = session.get(Invoice, note.invoice_id)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("BEREV ERP — Credit Note", styles["Title"]),
        Spacer(1, 6 * mm),
        Paragraph(f"Credit note: {escape(note.note_number)}", styles["BodyText"]),
        Paragraph(f"Original invoice: {escape(invoice.invoice_number or '')}", styles["BodyText"]),
        Paragraph(f"Date: {note.note_date.isoformat()}", styles["BodyText"]),
        Paragraph(f"Reason: {escape(note.reason)}", styles["BodyText"]),
    ]
    rows = [["Description / HSN", "Qty", "Taxable", "GST", "CGST", "SGST", "IGST", "Cess"]]
    for line in output["lines"]:
        rows.append([
            f"{escape(str(line['description']))} / {escape(str(line['hsn_sac']))}",
            str(line["quantity"]),
            str(line["taxable_value"]),
            str(line["gst_rate"]),
            str(line["cgst"]),
            str(line["sgst"]),
            str(line["igst"]),
            str(line["cess"]),
        ])
    table = Table(rows, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174c3c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5d1")),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([Spacer(1, 5 * mm), table, Spacer(1, 5 * mm)])
    story.append(Paragraph(f"Grand total: INR {_text(note.grand_total)}", styles["Heading2"]))
    story.append(Paragraph(escape(str(output["amount_in_words"])), styles["BodyText"]))
    buffer = BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, rightMargin=12 * mm, leftMargin=12 * mm).build(story)
    _audit(session, user.id, request, "download_credit_note_pdf", "credit_note", note.id, None, {"note_number": note.note_number})
    session.commit()
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{note.note_number}.pdf"'},
    )


@router.get("/customers/{customer_id}/ledger")
def customer_ledger(
    customer_id: int,
    _: Annotated[User, Depends(require_permissions("customers:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    customer = session.scalar(
        select(Customer).where(Customer.id == customer_id, Customer.deleted_at.is_(None))
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    events: list[tuple[date, int, str, str, Decimal, str]] = []
    invoices = session.scalars(
        select(Invoice).where(Invoice.customer_id == customer_id, Invoice.status == "issued")
    ).all()
    for invoice in invoices:
        events.append((invoice.invoice_date, invoice.id, "invoice", invoice.invoice_number or "", invoice.grand_total, "debit"))
    credit_notes = session.scalars(
        select(CreditNote).join(Invoice).where(CreditNote.status == "issued", Invoice.customer_id == customer_id)
    ).all()
    for note in credit_notes:
        events.append((note.note_date, note.id, "credit_note", note.note_number, note.grand_total, "credit"))
    payments = session.scalars(
        select(Payment).where(
            Payment.customer_id == customer_id,
            Payment.status.in_(("recorded", "reversed")),
            Payment.direction.in_(("in", "out")),
        )
    ).all()
    for payment in payments:
        events.append((
            payment.payment_date,
            payment.id,
            "receipt" if payment.direction == "in" else "refund",
            payment.payment_number,
            payment.amount,
            "credit" if payment.direction == "in" else "debit",
        ))
    events.sort(key=lambda event: (event[0], event[1], event[2]))
    opening = sum(
        (amount if direction == "debit" else -amount for event_date, _, _, _, amount, direction in events
         if date_from is not None and event_date < date_from),
        ZERO,
    )
    visible = [
        event for event in events
        if (date_from is None or event[0] >= date_from) and (date_to is None or event[0] <= date_to)
    ]
    total = len(visible)
    start = (page - 1) * page_size
    selected = visible[start:start + page_size]
    running = opening
    rows = []
    for event_date, event_id, kind, reference, amount, direction in visible[:start]:
        running += amount if direction == "debit" else -amount
    for event_date, event_id, kind, reference, amount, direction in selected:
        running += amount if direction == "debit" else -amount
        rows.append({
            "date": event_date.isoformat(),
            "id": event_id,
            "type": kind,
            "reference": reference,
            "debit": _text(amount if direction == "debit" else ZERO),
            "credit": _text(amount if direction == "credit" else ZERO),
            "balance": _text(running),
        })
    closing = opening + sum(
        (amount if direction == "debit" else -amount for _, _, _, _, amount, direction in visible),
        ZERO,
    )
    return {
        "customer_id": customer.id,
        "customer_name": customer.name,
        "opening_balance": _text(opening),
        "items": rows,
        "total": total,
        "closing_balance": _text(closing),
    }
