from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import (
    AuditLog,
    Expense,
    ExpenseCategory,
    JournalEntry,
    JournalEntryLine,
    LedgerAccount,
    PaymentMethod,
    User,
)
from berev_core.accounting import JournalEntry as CoreJournalEntry, post_expense

router = APIRouter(tags=["expenses"])
ZERO = Decimal("0.00")
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
ALLOWED_RECEIPT_TYPES = {
    "application/pdf": (".pdf", lambda data: data.startswith(b"%PDF-")),
    "image/jpeg": (".jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": (".png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (".webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
}


class ExpenseWrite(BaseModel):
    expense_date: date
    category_id: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=2000)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    cgst_amount: Decimal = Field(default=ZERO, ge=0, max_digits=14, decimal_places=2)
    sgst_amount: Decimal = Field(default=ZERO, ge=0, max_digits=14, decimal_places=2)
    igst_amount: Decimal = Field(default=ZERO, ge=0, max_digits=14, decimal_places=2)
    cess_amount: Decimal = Field(default=ZERO, ge=0, max_digits=14, decimal_places=2)
    vendor: str | None = Field(default=None, max_length=255)
    method_id: int = Field(gt=0)
    reference_no: str | None = Field(default=None, max_length=255)

    @field_validator("description", "vendor", "reference_no")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def taxes_have_consistent_total(self) -> ExpenseWrite:
        if sum((self.cgst_amount, self.sgst_amount, self.igst_amount, self.cess_amount), ZERO) < ZERO:
            raise ValueError("GST amounts must be non-negative")
        return self


class ExpenseCategoryWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    account_code: str = Field(min_length=1, max_length=32)


class ExpenseRejection(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("A rejection reason is required")
        return normalized


def _money(value: Decimal) -> str:
    return format(value, "f")


def _audit(
    session: Session,
    user: User,
    request: Request,
    action: str,
    entity: str,
    entity_id: int,
    new_values: dict[str, object],
) -> None:
    session.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity_type=entity,
            entity_id=entity_id,
            new_values=new_values,
            ip_address=request.client.host if request.client else None,
        )
    )


def _expense_output(session: Session, expense: Expense) -> dict[str, object]:
    category = session.get(ExpenseCategory, expense.category_id)
    method = session.get(PaymentMethod, expense.method_id) if expense.method_id else None
    return {
        "id": expense.id,
        "expense_number": expense.expense_number,
        "expense_date": expense.expense_date.isoformat(),
        "category_id": expense.category_id,
        "category_name": category.name if category else None,
        "description": expense.description,
        "amount": _money(expense.amount),
        "gst_amount": _money(expense.gst_amount),
        "cgst_amount": _money(expense.cgst_amount),
        "sgst_amount": _money(expense.sgst_amount),
        "igst_amount": _money(expense.igst_amount),
        "cess_amount": _money(expense.cess_amount),
        "total_amount": _money(expense.amount + expense.gst_amount),
        "vendor": expense.vendor,
        "method_id": expense.method_id,
        "method": method.name if method else None,
        "reference_no": expense.reference_no,
        "status": expense.status,
        "has_receipt": expense.attachment_path is not None,
        "created_by": expense.created_by,
        "approved_by": expense.approved_by,
    }


def _persist_journal(
    session: Session,
    entry: CoreJournalEntry,
    expense: Expense,
    user_id: int,
) -> JournalEntry:
    row = JournalEntry(
        entry_date=entry.entry_date,
        reference=entry.reference,
        narration=entry.narration,
        source_type="expense",
        source_id=expense.id,
        posted_by=user_id,
    )
    session.add(row)
    session.flush()
    session.add_all(
        JournalEntryLine(
            entry_id=row.id,
            account_code=line.account,
            debit=line.debit,
            credit=line.credit,
        )
        for line in entry.lines
    )
    return row


@router.get("/expense-categories")
def list_expense_categories(
    _: Annotated[User, Depends(require_permissions("expenses:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> list[dict[str, object]]:
    return [
        {"id": item.id, "name": item.name, "account_code": item.account_code}
        for item in session.scalars(
            select(ExpenseCategory)
            .where(ExpenseCategory.is_active.is_(True))
            .order_by(ExpenseCategory.name)
        ).all()
    ]


@router.post("/expense-categories", status_code=status.HTTP_201_CREATED)
def create_expense_category(
    body: ExpenseCategoryWrite,
    _: Annotated[User, Depends(require_permissions("expenses:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    name = body.name.strip()
    account_code = body.account_code.strip()
    account = session.get(LedgerAccount, account_code)
    if account is None or account.type != "expense" or not account.is_active:
        raise HTTPException(status_code=422, detail="account_code must identify an active expense account")
    category = ExpenseCategory(name=name, account_code=account_code, is_active=True)
    session.add(category)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Expense category already exists") from None
    return {"id": category.id, "name": category.name, "account_code": category.account_code}


@router.get("/expenses")
def list_expenses(
    _: Annotated[User, Depends(require_permissions("expenses:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    status_filter: str | None = Query(default=None, alias="status"),
) -> dict[str, object]:
    filters = []
    if q.strip():
        term = f"%{q.strip()}%"
        filters.append(
            or_(
                Expense.expense_number.ilike(term),
                Expense.vendor.ilike(term),
                Expense.description.ilike(term),
            )
        )
    if status_filter is not None:
        if status_filter not in {"pending", "approved", "rejected", "posted", "cancelled"}:
            raise HTTPException(status_code=422, detail="Unknown expense status")
        filters.append(Expense.status == status_filter)
    total = session.scalar(select(func.count(Expense.id)).where(*filters)) or 0
    rows = session.scalars(
        select(Expense).where(*filters)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [_expense_output(session, item) for item in rows], "total": total}


@router.get("/expenses/{expense_id}")
def get_expense(
    expense_id: int,
    _: Annotated[User, Depends(require_permissions("expenses:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    expense = session.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    return _expense_output(session, expense)


@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def create_expense(
    body: ExpenseWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("expenses:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    category = session.get(ExpenseCategory, body.category_id)
    method = session.get(PaymentMethod, body.method_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=422, detail="An active expense category is required")
    if method is None or not method.is_active:
        raise HTTPException(status_code=422, detail="An active payment method is required")
    gst_amount = body.cgst_amount + body.sgst_amount + body.igst_amount + body.cess_amount
    expense = Expense(
        expense_number=f"EXP-TMP-{uuid4().hex}",
        expense_date=body.expense_date,
        category_id=category.id,
        description=body.description,
        amount=body.amount,
        gst_amount=gst_amount,
        cgst_amount=body.cgst_amount,
        sgst_amount=body.sgst_amount,
        igst_amount=body.igst_amount,
        cess_amount=body.cess_amount,
        vendor=body.vendor,
        method_id=method.id,
        reference_no=body.reference_no,
        status="pending",
        created_by=user.id,
    )
    session.add(expense)
    try:
        session.flush()
        expense.expense_number = f"EXP-{expense.id:08d}"
        _audit(
            session, user, request, "create_expense", "expense", expense.id,
            {"expense_number": expense.expense_number, "amount": _money(expense.amount), "gst_amount": _money(gst_amount)},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Expense conflicts with current records") from None
    return _expense_output(session, expense)


@router.post("/expenses/{expense_id}/approve")
def approve_expense(
    expense_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("expenses:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    expense = session.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.status != "pending":
        raise HTTPException(status_code=409, detail="Only pending expenses can be approved")
    expense.status = "approved"
    expense.approved_by = user.id
    _audit(session, user, request, "approve_expense", "expense", expense.id, {"status": "approved"})
    session.commit()
    return _expense_output(session, expense)


@router.post("/expenses/{expense_id}/reject")
def reject_expense(
    expense_id: int,
    body: ExpenseRejection,
    request: Request,
    user: Annotated[User, Depends(require_permissions("expenses:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    reason = body.reason
    expense = session.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.status != "pending":
        raise HTTPException(status_code=409, detail="Only pending expenses can be rejected")
    expense.status = "rejected"
    _audit(session, user, request, "reject_expense", "expense", expense.id, {"status": "rejected", "reason": reason})
    session.commit()
    return _expense_output(session, expense)


@router.post("/expenses/{expense_id}/post")
def post_expense_document(
    expense_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("accounting:post"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    expense = session.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.status != "approved":
        raise HTTPException(status_code=409, detail="Only approved expenses can be posted")
    category = session.get(ExpenseCategory, expense.category_id)
    method = session.get(PaymentMethod, expense.method_id) if expense.method_id else None
    if category is None or not category.is_active or not category.account_code:
        raise HTTPException(status_code=409, detail="Expense category has no configured ledger account")
    if method is None or not method.is_active:
        raise HTTPException(status_code=409, detail="Expense payment method is not active")
    entry = post_expense(
        expense.expense_date,
        expense.expense_number,
        category.account_code,
        expense.amount,
        method.ledger_kind,
        taxes={
            "cgst": expense.cgst_amount,
            "sgst": expense.sgst_amount,
            "igst": expense.igst_amount,
            "cess": expense.cess_amount,
        },
    )
    journal = _persist_journal(session, entry, expense, user.id)
    expense.status = "posted"
    _audit(
        session, user, request, "post_expense", "expense", expense.id,
        {"status": "posted", "journal_entry_id": journal.id, "total": _money(expense.amount + expense.gst_amount)},
    )
    session.commit()
    return {"id": expense.id, "status": expense.status, "journal_entry_id": journal.id}


@router.put("/expenses/{expense_id}/receipt")
async def upload_expense_receipt(
    expense_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("expenses:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    expense = session.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.status not in {"pending", "approved"}:
        raise HTTPException(status_code=409, detail="Receipts can only be attached to pending or approved expenses")
    if expense.attachment_path is not None:
        raise HTTPException(status_code=409, detail="An expense receipt is already attached")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    file_type = ALLOWED_RECEIPT_TYPES.get(content_type)
    if file_type is None:
        raise HTTPException(status_code=415, detail="Receipt must be a PDF, JPEG, PNG, or WebP file")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_RECEIPT_BYTES:
            raise HTTPException(status_code=413, detail="Receipt file exceeds the 5 MB limit")
        chunks.append(chunk)
    data = b"".join(chunks)
    extension, signature_check = file_type
    if not data or not signature_check(data):
        raise HTTPException(status_code=415, detail="Receipt content does not match the declared file type")
    upload_dir: Path = request.app.state.settings.expense_upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{uuid4().hex}{extension}"
    file_path = upload_dir / file_name
    try:
        with file_path.open("xb") as receipt_file:
            receipt_file.write(data)
        expense.attachment_path = file_name
        _audit(
            session, user, request, "upload_expense_receipt", "expense", expense.id,
            {"content_type": content_type, "size_bytes": size},
        )
        session.commit()
    except OSError as error:
        session.rollback()
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not store expense receipt") from error
    except SQLAlchemyError as error:
        session.rollback()
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not attach the receipt to this expense") from error
    return {"id": expense.id, "has_receipt": True, "content_type": content_type, "size_bytes": size}


@router.get("/expenses/{expense_id}/receipt")
def download_expense_receipt(
    expense_id: int,
    request: Request,
    _: Annotated[User, Depends(require_permissions("expenses:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> FileResponse:
    expense = session.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if expense.attachment_path is None:
        raise HTTPException(status_code=404, detail="Expense has no receipt")
    upload_dir: Path = request.app.state.settings.expense_upload_dir
    file_path = upload_dir / expense.attachment_path
    if not file_path.is_file() or file_path.parent.resolve() != upload_dir.resolve():
        raise HTTPException(status_code=404, detail="Expense receipt is unavailable")
    return FileResponse(file_path, filename=f"{expense.expense_number}{file_path.suffix}")
