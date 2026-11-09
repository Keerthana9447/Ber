from __future__ import annotations

import csv
import re
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import (
    AuditLog,
    Branch,
    CreditNote,
    Customer,
    DebitNote,
    Invoice,
    InvoiceItem,
    Inventory,
    JournalEntry,
    JournalEntryLine,
    LedgerAccount,
    Payment,
    PaymentAllocation,
    Product,
    Purchase,
    PurchaseItem,
    Supplier,
    User,
    Warehouse,
)
from berev_core.reporting import (
    ageing_report,
    gst_summary,
    inventory_valuation,
    profit_and_loss_from_rows,
    running_ledger,
    trial_balance_from_rows,
)

router = APIRouter(tags=["accounting", "reports"])
ZERO = Decimal("0.00")


def _json_values(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_values(item) for item in value]
    return value


def _document_branch_filters(model, user: User) -> list:
    if user.role.name != "Branch Manager":
        return []
    return [model.branch_id == user.branch_id] if user.branch_id is not None else [model.id == -1]


def _validate_report_dates(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="from must be on or before to")
    if date_from is not None and date_to is not None and (date_to - date_from).days > 365:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 366 days")


def _apply_date_filters(column, date_from: date | None, date_to: date | None) -> list:
    _validate_report_dates(date_from, date_to)
    filters = []
    if date_from is not None:
        filters.append(column >= date_from)
    if date_to is not None:
        filters.append(column <= date_to)
    return filters


def _trial_balance_data(
    session: Session, user: User, date_from: date | None, date_to: date | None
) -> dict[str, object]:
    filters = _date_filters(date_from, date_to) + _branch_filters(user)
    rows = session.execute(
        select(
            LedgerAccount.code,
            LedgerAccount.name,
            LedgerAccount.type,
            func.coalesce(func.sum(JournalEntryLine.debit), 0),
            func.coalesce(func.sum(JournalEntryLine.credit), 0),
        )
        .join(JournalEntryLine, JournalEntryLine.account_code == LedgerAccount.code)
        .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
        .where(LedgerAccount.is_active.is_(True), *filters)
        .group_by(LedgerAccount.code, LedgerAccount.name, LedgerAccount.type)
    ).all()
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        **_json_values(trial_balance_from_rows(rows)),
    }


def _profit_and_loss_data(
    session: Session, user: User, date_from: date | None, date_to: date | None
) -> dict[str, object]:
    filters = _date_filters(date_from, date_to) + _branch_filters(user)
    rows = session.execute(
        select(
            LedgerAccount.type,
            func.coalesce(func.sum(JournalEntryLine.debit), 0),
            func.coalesce(func.sum(JournalEntryLine.credit), 0),
        )
        .join(JournalEntryLine, JournalEntryLine.account_code == LedgerAccount.code)
        .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
        .where(
            LedgerAccount.is_active.is_(True),
            LedgerAccount.type.in_(("income", "cogs", "expense")),
            *filters,
        )
        .group_by(LedgerAccount.type)
    ).all()
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "label": "before income tax",
        **_json_values(profit_and_loss_from_rows(rows)),
    }


def _sales_data(
    session: Session,
    user: User,
    from_date: date | None,
    to_date: date | None,
    group_by: str,
) -> dict[str, object]:
    if from_date is None or to_date is None:
        raise HTTPException(status_code=422, detail="from_date and to_date are required for sales")
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to_date must be on or after from_date")
    groups = {"day", "month", "product", "customer", "salesperson", "gst", "branch"}
    if group_by not in groups:
        raise HTTPException(status_code=422, detail="Unsupported sales group_by")
    filters = [
        Invoice.status == "issued",
        Invoice.doc_type.in_(("tax_invoice", "bill_of_supply")),
        Invoice.invoice_date >= from_date,
        Invoice.invoice_date <= to_date,
        *_document_branch_filters(Invoice, user),
    ]
    date_key = func.date_trunc("day" if group_by == "day" else "month", Invoice.invoice_date)
    if group_by in {"day", "month"}:
        rows = session.execute(
            select(date_key.label("bucket"), func.count(Invoice.id), func.sum(Invoice.grand_total))
            .where(*filters)
            .group_by(date_key)
            .order_by(date_key)
        ).all()
        items = [
            {"key": bucket.date().isoformat(), "count": count, "grand_total": format(total or 0, "f")}
            for bucket, count, total in rows
        ]
    else:
        joins = []
        key_expression = None
        line_totals = False
        if group_by == "customer":
            joins.append((Customer, Customer.id == Invoice.customer_id))
            key_expression = Customer.name
        elif group_by == "salesperson":
            joins.append((User, User.id == Invoice.salesperson_id))
            key_expression = User.full_name
        elif group_by == "branch":
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
        query = select(
            key_expression.label("key"),
            func.count(func.distinct(Invoice.id)),
            total_expression,
        ).where(*filters)
        for model, condition in joins:
            query = query.outerjoin(model, condition) if group_by == "product" and model is Product else query.join(model, condition)
        rows = session.execute(query.group_by(key_expression).order_by(key_expression)).all()
        items = [
            {"key": str(key), "count": count, "grand_total": format(total or 0, "f")}
            for key, count, total in rows
        ]
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "group_by": group_by,
        "items": items,
    }


def _purchase_register_data(
    session: Session, user: User, date_from: date | None, date_to: date | None
) -> dict[str, object]:
    filters = [
        Purchase.doc_type == "purchase_invoice",
        Purchase.status == "posted",
        *_apply_date_filters(Purchase.doc_date, date_from, date_to),
        *_document_branch_filters(Purchase, user),
    ]
    rows = session.execute(
        select(Purchase, Supplier.name)
        .join(Supplier, Supplier.id == Purchase.supplier_id)
        .where(*filters)
        .order_by(Purchase.doc_date, Purchase.id)
    ).all()
    return {
        "items": [
            {
                "number": item.number,
                "supplier_name": name,
                "doc_date": item.doc_date.isoformat(),
                "taxable_total": format(item.taxable_total, "f"),
                "cgst": format(item.cgst_total, "f"),
                "sgst": format(item.sgst_total, "f"),
                "igst": format(item.igst_total, "f"),
                "cess": format(item.cess_total, "f"),
                "grand_total": format(item.grand_total, "f"),
            }
            for item, name in rows
        ],
        "total": len(rows),
    }


def _input_gst_data(
    session: Session, user: User, date_from: date | None, date_to: date | None
) -> dict[str, object]:
    filters = [
        Purchase.doc_type == "purchase_invoice",
        Purchase.status == "posted",
        *_apply_date_filters(Purchase.doc_date, date_from, date_to),
        *_document_branch_filters(Purchase, user),
    ]
    purchases = session.scalars(select(Purchase).where(*filters)).all()
    totals = gst_summary(
        {
            "side": "purchases",
            "taxable": item.taxable_total,
            "cgst": item.cgst_total,
            "sgst": item.sgst_total,
            "igst": item.igst_total,
            "cess": item.cess_total,
        }
        for item in purchases
    )["purchases"]
    by_rate = session.execute(
        select(
            PurchaseItem.gst_rate,
            func.coalesce(func.sum(PurchaseItem.taxable_value), 0),
            func.coalesce(func.sum(PurchaseItem.cgst), 0),
            func.coalesce(func.sum(PurchaseItem.sgst), 0),
            func.coalesce(func.sum(PurchaseItem.igst), 0),
            func.coalesce(func.sum(PurchaseItem.cess), 0),
        )
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .where(*filters)
        .group_by(PurchaseItem.gst_rate)
        .order_by(PurchaseItem.gst_rate)
    ).all()
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "taxable_total": format(totals["taxable"], "f"),
        "cgst": format(totals["cgst"], "f"),
        "sgst": format(totals["sgst"], "f"),
        "igst": format(totals["igst"], "f"),
        "cess": format(totals["cess"], "f"),
        "by_rate": [
            {
                "gst_rate": format(row[0], "f"),
                "taxable_total": format(row[1], "f"),
                "cgst": format(row[2], "f"),
                "sgst": format(row[3], "f"),
                "igst": format(row[4], "f"),
                "cess": format(row[5], "f"),
            }
            for row in by_rate
        ],
    }


def _stock_valuation_data(session: Session, user: User) -> dict[str, object]:
    filters = [Inventory.quantity > 0]
    if user.role.name == "Branch Manager":
        filters.append(Warehouse.branch_id == user.branch_id if user.branch_id is not None else Warehouse.id == -1)
    rows = session.execute(
        select(
            Inventory.product_id,
            Product.sku,
            Product.name,
            Inventory.warehouse_id,
            Warehouse.name,
            Warehouse.branch_id,
            Inventory.quantity,
            Inventory.average_unit_cost,
        )
        .join(Product, Product.id == Inventory.product_id)
        .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
        .where(*filters)
        .order_by(Product.name, Warehouse.name)
    ).all()
    return _json_values(inventory_valuation(
        {
            "product_id": product_id,
            "sku": sku,
            "product_name": product_name,
            "warehouse_id": warehouse_id,
            "warehouse_name": warehouse_name,
            "branch_id": branch_id,
            "quantity": quantity,
            "average_unit_cost": average_unit_cost,
        }
        for product_id, sku, product_name, warehouse_id, warehouse_name, branch_id, quantity, average_unit_cost in rows
    ))


def _customer_ledger_data(
    session: Session,
    user: User,
    customer_id: int,
    date_from: date | None,
    date_to: date | None,
) -> dict[str, object]:
    filters = [Customer.id == customer_id, Customer.deleted_at.is_(None), *_document_branch_filters(Customer, user)]
    customer = session.scalar(select(Customer).where(*filters))
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    events: list[tuple[date, int, str, str, object, str]] = []
    invoice_filters = [
        Invoice.customer_id == customer_id,
        Invoice.status == "issued",
        *_document_branch_filters(Invoice, user),
    ]
    for invoice in session.scalars(select(Invoice).where(*invoice_filters)).all():
        events.append((invoice.invoice_date, invoice.id, "invoice", invoice.invoice_number or "", invoice.grand_total, "debit"))
    credit_note_filters = [
        Invoice.customer_id == customer_id,
        CreditNote.status == "issued",
        *_document_branch_filters(Invoice, user),
    ]
    for note in session.scalars(
        select(CreditNote).join(Invoice, Invoice.id == CreditNote.invoice_id).where(*credit_note_filters)
    ).all():
        events.append((note.note_date, note.id, "credit_note", note.note_number, note.grand_total, "credit"))
    payment_filters = [
        Payment.customer_id == customer_id,
        Payment.status.in_(("recorded", "reversed")),
        *_document_branch_filters(Payment, user),
    ]
    for payment in session.scalars(select(Payment).where(*payment_filters)).all():
        events.append((
            payment.payment_date,
            payment.id,
            "receipt" if payment.direction == "in" else "refund",
            payment.payment_number,
            payment.amount,
            "credit" if payment.direction == "in" else "debit",
        ))
    ledger = running_ledger(customer.opening_balance, events, date_from, date_to)
    return _json_values({
        "customer_id": customer.id,
        **ledger,
    })


def _supplier_ledger_data(
    session: Session,
    user: User,
    supplier_id: int,
    date_from: date | None,
    date_to: date | None,
) -> dict[str, object]:
    supplier = session.scalar(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.deleted_at.is_(None))
    )
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    filters = [
        JournalEntryLine.supplier_id == supplier_id,
        *_branch_filters(user),
    ]
    if date_to is not None:
        filters.append(JournalEntry.entry_date <= date_to)
    rows = session.execute(
        select(
            JournalEntry.entry_date,
            JournalEntry.id,
            JournalEntry.reference,
            JournalEntry.narration,
            JournalEntryLine.debit,
            JournalEntryLine.credit,
        )
        .join(JournalEntryLine, JournalEntryLine.entry_id == JournalEntry.id)
        .where(*filters)
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalEntryLine.id)
    ).all()
    events = [
        (entry_date, entry_id, narration or "", reference, debit or credit, "debit" if debit else "credit")
        for entry_date, entry_id, reference, narration, debit, credit in rows
    ]
    ledger = running_ledger(supplier.opening_balance, events, date_from, date_to, debit_positive=False)
    return _json_values({"supplier_id": supplier.id, **ledger})


def _ageing_data(session: Session, user: User, party_type: str, as_of: date) -> dict[str, object]:
    if party_type == "customer":
        invoice_filters = [
            Invoice.status == "issued",
            Invoice.invoice_date <= as_of,
            *_document_branch_filters(Invoice, user),
        ]
        invoices = session.scalars(select(Invoice).where(*invoice_filters)).all()
        items = []
        for invoice in invoices:
            paid = session.scalar(
                select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                .join(Payment, Payment.id == PaymentAllocation.payment_id)
                .where(
                    PaymentAllocation.invoice_id == invoice.id,
                    Payment.direction == "in",
                    Payment.status == "recorded",
                    *_document_branch_filters(Payment, user),
                )
            ) or ZERO
            credits = session.scalar(
                select(func.coalesce(func.sum(CreditNote.grand_total), 0))
                .where(CreditNote.invoice_id == invoice.id, CreditNote.status == "issued")
            ) or ZERO
            items.append({
                "document_id": invoice.id,
                "number": invoice.invoice_number,
                "party_id": invoice.customer_id,
                "party_name": invoice.customer_name,
                "document_date": invoice.invoice_date,
                "due_date": invoice.due_date or invoice.invoice_date,
                "outstanding": Decimal(invoice.grand_total) - Decimal(paid) - Decimal(credits),
            })
    elif party_type == "supplier":
        purchase_filters = [
            Purchase.doc_type == "purchase_invoice",
            Purchase.status == "posted",
            Purchase.doc_date <= as_of,
            *_document_branch_filters(Purchase, user),
        ]
        purchases = session.execute(
            select(Purchase, Supplier.name)
            .join(Supplier, Supplier.id == Purchase.supplier_id)
            .where(*purchase_filters)
        ).all()
        items = []
        for purchase, supplier_name in purchases:
            paid = session.scalar(
                select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                .join(Payment, Payment.id == PaymentAllocation.payment_id)
                .where(
                    PaymentAllocation.purchase_id == purchase.id,
                    Payment.direction == "out",
                    Payment.status == "recorded",
                    *_document_branch_filters(Payment, user),
                )
            ) or ZERO
            debit_notes = session.scalar(
                select(func.coalesce(func.sum(DebitNote.grand_total), 0))
                .where(DebitNote.purchase_id == purchase.id, DebitNote.status == "issued")
            ) or ZERO
            items.append({
                "document_id": purchase.id,
                "number": purchase.number,
                "party_id": purchase.supplier_id,
                "party_name": supplier_name,
                "document_date": purchase.doc_date,
                "due_date": purchase.due_date or purchase.doc_date,
                "outstanding": Decimal(purchase.grand_total) - Decimal(paid) - Decimal(debit_notes),
            })
    else:
        raise HTTPException(status_code=422, detail="party_type must be customer or supplier")
    return _json_values(ageing_report(items, as_of))


def _gst_summary_data(
    session: Session, user: User, date_from: date | None, date_to: date | None
) -> dict[str, object]:
    rows = []
    invoice_filters = [
        Invoice.status == "issued",
        Invoice.doc_type.in_(("tax_invoice", "bill_of_supply")),
        *_apply_date_filters(Invoice.invoice_date, date_from, date_to),
        *_document_branch_filters(Invoice, user),
    ]
    for invoice in session.scalars(select(Invoice).where(*invoice_filters)).all():
        rows.append({
            "side": "sales",
            "taxable": invoice.taxable_total,
            "cgst": invoice.cgst_total,
            "sgst": invoice.sgst_total,
            "igst": invoice.igst_total,
            "cess": invoice.cess_total,
        })
    credit_filters = [
        CreditNote.status == "issued",
        *_apply_date_filters(CreditNote.note_date, date_from, date_to),
        *_document_branch_filters(Invoice, user),
    ]
    for note in session.scalars(
        select(CreditNote).join(Invoice, Invoice.id == CreditNote.invoice_id).where(*credit_filters)
    ).all():
        rows.append({
            "side": "sales",
            "taxable": -Decimal(note.taxable_total),
            "cgst": -Decimal(note.cgst_total),
            "sgst": -Decimal(note.sgst_total),
            "igst": -Decimal(note.igst_total),
            "cess": -Decimal(note.cess_total),
        })
    purchase_filters = [
        Purchase.doc_type == "purchase_invoice",
        Purchase.status == "posted",
        *_apply_date_filters(Purchase.doc_date, date_from, date_to),
        *_document_branch_filters(Purchase, user),
    ]
    for purchase in session.scalars(select(Purchase).where(*purchase_filters)).all():
        rows.append({
            "side": "purchases",
            "taxable": purchase.taxable_total,
            "cgst": purchase.cgst_total,
            "sgst": purchase.sgst_total,
            "igst": purchase.igst_total,
            "cess": purchase.cess_total,
        })
    debit_filters = [
        DebitNote.status == "issued",
        *_apply_date_filters(DebitNote.note_date, date_from, date_to),
        *_document_branch_filters(Purchase, user),
    ]
    for note in session.scalars(
        select(DebitNote).join(Purchase, Purchase.id == DebitNote.purchase_id).where(*debit_filters)
    ).all():
        rows.append({
            "side": "purchases",
            "taxable": -Decimal(note.taxable_total),
            "cgst": -Decimal(note.cgst_total),
            "sgst": -Decimal(note.sgst_total),
            "igst": -Decimal(note.igst_total),
            "cess": -Decimal(note.cess_total),
        })
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        **_json_values(gst_summary(rows)),
    }


def _report_payload(
    session: Session,
    user: User,
    report_key: str,
    date_from: date | None,
    date_to: date | None,
    from_date: date | None,
    to_date: date | None,
    group_by: str,
    customer_id: int | None,
    supplier_id: int | None,
    party_type: str,
    as_of: date | None,
) -> dict[str, object]:
    date_from = date_from or from_date
    date_to = date_to or to_date
    if report_key == "trial-balance":
        return _trial_balance_data(session, user, date_from, date_to)
    if report_key == "profit-and-loss":
        return _profit_and_loss_data(session, user, date_from, date_to)
    if report_key in {"cash-book", "bank-book"}:
        return _cash_book(session, user, "1000" if report_key == "cash-book" else "1010", date_from, date_to)
    if report_key == "sales":
        return _sales_data(session, user, from_date or date_from, to_date or date_to, group_by)
    if report_key in {"purchase-register", "purchases-register"}:
        return _purchase_register_data(session, user, date_from, date_to)
    if report_key == "input-gst":
        return _input_gst_data(session, user, date_from, date_to)
    if report_key == "stock-valuation":
        return _stock_valuation_data(session, user)
    if report_key == "sales-register":
        return _sales_register_data(session, user, date_from, date_to)
    if report_key == "customer-ledger":
        if customer_id is None:
            raise HTTPException(status_code=422, detail="customer_id is required")
        return _customer_ledger_data(session, user, customer_id, date_from, date_to)
    if report_key == "supplier-ledger":
        if supplier_id is None:
            raise HTTPException(status_code=422, detail="supplier_id is required")
        return _supplier_ledger_data(session, user, supplier_id, date_from, date_to)
    if report_key == "ageing":
        return _ageing_data(session, user, party_type, as_of or date.today())
    if report_key == "gst-summary":
        return _gst_summary_data(session, user, date_from, date_to)
    raise HTTPException(status_code=404, detail="Unknown report key")


def _sales_register_data(
    session: Session, user: User, date_from: date | None, date_to: date | None
) -> dict[str, object]:
    filters = [
        Invoice.status == "issued",
        Invoice.doc_type.in_(("tax_invoice", "bill_of_supply")),
        *_apply_date_filters(Invoice.invoice_date, date_from, date_to),
        *_document_branch_filters(Invoice, user),
    ]
    rows = session.execute(
        select(Invoice).where(*filters).order_by(Invoice.invoice_date, Invoice.id)
    ).scalars().all()
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "items": [
            {
                "invoice_number": invoice.invoice_number,
                "customer_name": invoice.customer_name,
                "invoice_date": invoice.invoice_date.isoformat(),
                "taxable_total": format(invoice.taxable_total, "f"),
                "cgst": format(invoice.cgst_total, "f"),
                "sgst": format(invoice.sgst_total, "f"),
                "igst": format(invoice.igst_total, "f"),
                "cess": format(invoice.cess_total, "f"),
                "grand_total": format(invoice.grand_total, "f"),
            }
            for invoice in rows
        ],
        "total": len(rows),
    }


def _safe_export_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()):
        return value
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _export_tables(payload: dict[str, object]) -> tuple[list[tuple[str, object]], list[dict[str, object]]]:
    details = payload.get("items")
    if not isinstance(details, list):
        details = payload.get("by_rate")
    detail_rows = [item for item in details if isinstance(item, dict)] if isinstance(details, list) else []
    summary = []
    for key, value in payload.items():
        if key in {"items", "by_rate"}:
            continue
        if isinstance(value, dict):
            summary.extend((f"{key}.{nested_key}", nested_value) for nested_key, nested_value in value.items())
        elif not isinstance(value, list):
            summary.append((key, value))
    return summary, detail_rows


def _export_csv(payload: dict[str, object]) -> bytes:
    summary, details = _export_tables(payload)
    output = StringIO(newline="")
    writer = csv.writer(output)
    for key, value in summary:
        writer.writerow([_safe_export_text(key), _safe_export_text(value)])
    if summary and details:
        writer.writerow([])
    if details:
        headers = list(dict.fromkeys(key for item in details for key in item))
        writer.writerow([_safe_export_text(key) for key in headers])
        for item in details:
            writer.writerow([_safe_export_text(item.get(key, "")) for key in headers])
    return output.getvalue().encode("utf-8-sig")


def _export_xlsx(payload: dict[str, object]) -> bytes:
    from openpyxl import Workbook

    summary, details = _export_tables(payload)
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Summary"
    summary_sheet.append(["Metric", "Value"])
    for key, value in summary:
        summary_sheet.append([_safe_export_text(key), _safe_export_text(value)])
    detail_sheet = workbook.create_sheet("Details")
    if details:
        headers = list(dict.fromkeys(key for item in details for key in item))
        detail_sheet.append([_safe_export_text(key) for key in headers])
        for item in details:
            detail_sheet.append([_safe_export_text(item.get(key, "")) for key in headers])
    else:
        detail_sheet.append(["No detail rows"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _export_pdf(report_key: str, payload: dict[str, object]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle
    from xml.sax.saxutils import escape

    summary, details = _export_tables(payload)
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = getSampleStyleSheet()
    story = [Paragraph(escape(report_key.replace("-", " ").title()), styles["Title"])]
    if summary:
        summary_table = LongTable(
            [["Metric", "Value"]] + [[escape(str(key)), escape(str(value))] for key, value in summary],
            colWidths=[60 * mm, 190 * mm],
            repeatRows=1,
        )
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174c3c")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5d1")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.extend([summary_table, Spacer(1, 5 * mm)])
    if details:
        headers = list(dict.fromkeys(key for item in details for key in item))
        rows = [[Paragraph(escape(str(key)), styles["BodyText"]) for key in headers]]
        for item in details:
            rows.append([Paragraph(escape(str(item.get(key, ""))), styles["BodyText"]) for key in headers])
        table = LongTable(rows, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#174c3c")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5d1")),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("No detail rows", styles["BodyText"]))
    document.build(story)
    return output.getvalue()


def _export_report_response(
    report_key: str,
    file_format: str,
    payload: dict[str, object],
) -> Response:
    if file_format == "csv":
        body = _export_csv(payload)
        media_type = "text/csv; charset=utf-8"
    elif file_format == "xlsx":
        body = _export_xlsx(payload)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        body = _export_pdf(report_key, payload)
        media_type = "application/pdf"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{report_key}.{file_format}"'},
    )


@router.get("/reports/sales-register")
def sales_register(
    user: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    return _sales_register_data(session, user, date_from, date_to)


@router.get("/reports/customer-ledger")
def customer_ledger_report(
    user: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
    customer_id: int = Query(gt=0),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    return _customer_ledger_data(session, user, customer_id, date_from, date_to)


@router.get("/reports/supplier-ledger")
def supplier_ledger_report(
    user: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
    supplier_id: int = Query(gt=0),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    return _supplier_ledger_data(session, user, supplier_id, date_from, date_to)


@router.get("/reports/ageing")
def ageing(
    user: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
    party_type: str = Query(default="customer"),
    as_of: date = Query(default_factory=date.today),
) -> dict[str, object]:
    return _ageing_data(session, user, party_type, as_of)


@router.get("/reports/gst-summary")
def gst_summary_report(
    user: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    return _gst_summary_data(session, user, date_from, date_to)


@router.get("/reports/export/{report_key}.{file_format}")
def export_report(
    request: Request,
    user: Annotated[User, Depends(require_permissions("reports:read", "reports:export"))],
    session: Annotated[Session, Depends(get_db)],
    report_key: str = Path(pattern="^[a-z0-9-]+$"),
    file_format: str = Path(pattern="^(csv|xlsx|pdf)$"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    group_by: str = Query(default="day"),
    customer_id: int | None = Query(default=None, gt=0),
    supplier_id: int | None = Query(default=None, gt=0),
    party_type: str = Query(default="customer"),
    as_of: date | None = Query(default=None),
) -> Response:
    payload = _report_payload(
        session,
        user,
        report_key,
        date_from,
        date_to,
        from_date,
        to_date,
        group_by,
        customer_id,
        supplier_id,
        party_type,
        as_of,
    )
    response = _export_report_response(report_key, file_format, payload)
    session.add(
        AuditLog(
            user_id=user.id,
            action="export_report",
            entity_type="report",
            entity_id=None,
            new_values={
                "report_key": report_key,
                "format": file_format,
                "from": (date_from or from_date).isoformat() if (date_from or from_date) else None,
                "to": (date_to or to_date).isoformat() if (date_to or to_date) else None,
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    session.commit()
    return response


def _branch_filters(user: User) -> list:
    if user.role.name != "Branch Manager":
        return []
    return [JournalEntry.branch_id == user.branch_id] if user.branch_id is not None else [JournalEntry.id == -1]


def _date_filters(date_from: date | None, date_to: date | None) -> list:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="from must be on or before to")
    if date_from is not None and date_to is not None and (date_to - date_from).days > 365:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 366 days")
    filters = []
    if date_from is not None:
        filters.append(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        filters.append(JournalEntry.entry_date <= date_to)
    return filters


@router.get("/accounting/journal-entries")
def list_journal_entries(
    user: Annotated[User, Depends(require_permissions("accounting:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    filters = _date_filters(date_from, date_to) + _branch_filters(user)
    totals = (
        select(
            JournalEntryLine.entry_id.label("entry_id"),
            func.sum(JournalEntryLine.debit).label("debit"),
            func.sum(JournalEntryLine.credit).label("credit"),
        )
        .group_by(JournalEntryLine.entry_id)
        .subquery()
    )
    total = session.scalar(select(func.count(JournalEntry.id)).where(*filters)) or 0
    rows = session.execute(
        select(JournalEntry, totals.c.debit, totals.c.credit)
        .join(totals, totals.c.entry_id == JournalEntry.id)
        .where(*filters)
        .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": entry.id,
                "reference": entry.reference,
                "entry_date": entry.entry_date.isoformat(),
                "narration": entry.narration,
                "total_debit": format(debit, "f"),
                "total_credit": format(credit, "f"),
                "source_type": entry.source_type,
                "source_id": entry.source_id,
            }
            for entry, debit, credit in rows
        ],
        "total": total,
    }


@router.get("/accounting/trial-balance")
def trial_balance(
    user: Annotated[User, Depends(require_permissions("accounting:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    return _trial_balance_data(session, user, date_from, date_to)


@router.get("/accounting/profit-and-loss")
def profit_and_loss_report(
    user: Annotated[User, Depends(require_permissions("accounting:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    return _profit_and_loss_data(session, user, date_from, date_to)


def _cash_book(
    session: Session,
    user: User,
    account_code: str,
    date_from: date | None,
    date_to: date | None,
) -> dict[str, object]:
    date_filters = _date_filters(date_from, date_to)
    branch_filters = _branch_filters(user)
    account_filter = JournalEntryLine.account_code == account_code
    opening = ZERO
    if date_from is not None:
        opening = session.scalar(
            select(func.coalesce(func.sum(JournalEntryLine.debit - JournalEntryLine.credit), 0))
            .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
            .where(
                account_filter,
                JournalEntry.entry_date < date_from,
                *branch_filters,
            )
        ) or ZERO
    filters = [account_filter, *date_filters, *branch_filters]
    rows = session.execute(
        select(
            JournalEntry.id,
            JournalEntry.entry_date,
            JournalEntry.reference,
            JournalEntry.narration,
            JournalEntryLine.debit,
            JournalEntryLine.credit,
        )
        .join(JournalEntryLine, JournalEntryLine.entry_id == JournalEntry.id)
        .where(*filters)
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalEntryLine.id)
    ).all()
    events = [
        (entry_date, entry_id, narration or "", reference, debit or credit,
         "debit" if debit else "credit")
        for entry_id, entry_date, reference, narration, debit, credit in rows
    ]
    ledger = running_ledger(opening, events)
    return {
        "account_code": account_code,
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "opening_balance": format(ledger["opening_balance"], "f"),
        "items": [
            {
                "journal_entry_id": item["id"],
                "date": item["date"],
                "reference": item["reference"],
                "narration": item["type"],
                "debit": format(item["debit"], "f"),
                "credit": format(item["credit"], "f"),
                "balance": format(item["balance"], "f"),
            }
            for item in ledger["items"]
        ],
        "closing_balance": format(ledger["closing_balance"], "f"),
    }


@router.get("/accounting/cash-book")
def cash_book(
    user: Annotated[User, Depends(require_permissions("accounting:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    return _cash_book(session, user, "1000", date_from, date_to)


@router.get("/accounting/bank-book")
def bank_book(
    user: Annotated[User, Depends(require_permissions("accounting:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    return _cash_book(session, user, "1010", date_from, date_to)
