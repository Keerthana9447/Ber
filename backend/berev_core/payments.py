"""Payment allocation and invoice payment status."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from .gst import ZERO, q2, to_decimal


class PaymentError(ValueError):
    pass


@dataclass(frozen=True)
class OpenInvoice:
    invoice_id: int
    invoice_date: date
    balance: Decimal


def allocate_fifo(amount, open_invoices: list) -> tuple:
    """Oldest invoice first. Returns ([(invoice_id, amount)], unallocated_advance)."""
    remaining = q2(to_decimal(amount, 'amount'))
    if remaining <= 0:
        raise PaymentError("payment amount must be > 0")
    out = []
    for inv in sorted(open_invoices, key=lambda i: (i.invoice_date, i.invoice_id)):
        if remaining == 0:
            break
        if inv.balance <= 0:
            continue
        part = min(remaining, inv.balance)
        out.append((inv.invoice_id, part))
        remaining -= part
    return out, remaining


def validate_allocations(amount, allocations: dict, balances: dict) -> Decimal:
    """Explicit allocation check. Returns the unallocated remainder (advance)."""
    amount = q2(to_decimal(amount, 'amount'))
    total = ZERO
    for inv_id, a in allocations.items():
        a = q2(to_decimal(a, 'allocation'))
        if a <= 0:
            raise PaymentError("allocation must be > 0")
        if inv_id not in balances:
            raise PaymentError(f"unknown invoice {inv_id}")
        if a > balances[inv_id]:
            raise PaymentError(f"allocation to {inv_id} exceeds its outstanding balance")
        total += a
    if total > amount:
        raise PaymentError("allocations exceed the payment amount")
    return amount - total


def invoice_status(total, paid, due_date: Optional[date], today: date, refunded=False) -> str:
    total, paid = q2(to_decimal(total, 'total')), q2(to_decimal(paid, 'paid'))
    if refunded:
        return "refunded"
    outstanding = total - paid
    if outstanding < 0:
        raise PaymentError("paid amount exceeds invoice total")
    if outstanding == 0:
        return "paid"
    if due_date is not None and due_date < today:
        return "overdue"
    return "partially_paid" if paid > 0 else "unpaid"
