from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import JournalEntry, JournalEntryLine, LedgerAccount, User

router = APIRouter(tags=["accounting", "reports"])
ZERO = Decimal("0.00")


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
    filters = _date_filters(date_from, date_to) + _branch_filters(user)
    rows = session.execute(
        select(
            LedgerAccount.code,
            LedgerAccount.name,
            LedgerAccount.type,
            func.coalesce(func.sum(JournalEntryLine.debit), 0).label("debit"),
            func.coalesce(func.sum(JournalEntryLine.credit), 0).label("credit"),
        )
        .join(JournalEntryLine, JournalEntryLine.account_code == LedgerAccount.code)
        .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
        .where(LedgerAccount.is_active.is_(True), *filters)
        .group_by(LedgerAccount.code, LedgerAccount.name, LedgerAccount.type)
        .order_by(LedgerAccount.code)
    ).all()
    items = []
    total_debit = ZERO
    total_credit = ZERO
    for code, name, account_type, debit, credit in rows:
        net = Decimal(debit) - Decimal(credit)
        if net == 0:
            continue
        debit_balance = net if net > 0 else ZERO
        credit_balance = -net if net < 0 else ZERO
        total_debit += debit_balance
        total_credit += credit_balance
        items.append({
            "account_code": code,
            "account_name": name,
            "account_type": account_type,
            "debit": format(debit_balance, "f"),
            "credit": format(credit_balance, "f"),
        })
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "items": items,
        "total_debit": format(total_debit, "f"),
        "total_credit": format(total_credit, "f"),
        "is_balanced": total_debit == total_credit,
    }


@router.get("/accounting/profit-and-loss")
def profit_and_loss_report(
    user: Annotated[User, Depends(require_permissions("accounting:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
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
        .where(LedgerAccount.is_active.is_(True), *filters, LedgerAccount.type.in_(("income", "cogs", "expense")))
        .group_by(LedgerAccount.type)
    ).all()
    balances = {
        account_type: (Decimal(debit) - Decimal(credit))
        for account_type, debit, credit in rows
    }
    revenue = -balances.get("income", ZERO)
    cogs = balances.get("cogs", ZERO)
    expenses = balances.get("expense", ZERO)
    gross_profit = revenue - cogs
    net_before_income_tax = gross_profit - expenses
    return {
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "label": "before income tax",
        "revenue": format(revenue, "f"),
        "cogs": format(cogs, "f"),
        "gross_profit": format(gross_profit, "f"),
        "expenses": format(expenses, "f"),
        "net_profit_before_income_tax": format(net_before_income_tax, "f"),
    }


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
    balance = Decimal(opening)
    items: list[dict[str, object]] = []
    for entry_id, entry_date, reference, narration, debit, credit in rows:
        balance += Decimal(debit) - Decimal(credit)
        items.append({
            "journal_entry_id": entry_id,
            "date": entry_date.isoformat(),
            "reference": reference,
            "narration": narration,
            "debit": format(debit, "f"),
            "credit": format(credit, "f"),
            "balance": format(balance, "f"),
        })
    return {
        "account_code": account_code,
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "opening_balance": format(opening, "f"),
        "items": items,
        "closing_balance": format(balance, "f"),
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
