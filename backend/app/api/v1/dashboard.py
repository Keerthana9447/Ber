from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import (
    CreditNote,
    Customer,
    DebitNote,
    Expense,
    Invoice,
    InvoiceItem,
    Inventory,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    Product,
    ProductCategory,
    Purchase,
    Supplier,
    User,
    Warehouse,
)

router = APIRouter(tags=["dashboard"])
BUSINESS_TIMEZONE = ZoneInfo("Asia/Kolkata")
ZERO = Decimal("0")


def _branch_filter(model: Any, user: User):
    if user.role.name != "Branch Manager":
        return None
    if user.branch_id is None:
        return model.id == -1 if hasattr(model, "id") else model.branch_id == -1
    return model.branch_id == user.branch_id


def _where_branch(filters: list[Any], model: Any, user: User) -> list[Any]:
    branch_filter = _branch_filter(model, user)
    if branch_filter is not None:
        filters.append(branch_filter)
    return filters


def _sum(session: Session, statement: Any) -> Decimal:
    value = session.scalar(statement)
    return Decimal(value or 0)


def _money(value: Decimal | int | float) -> str:
    return f"{Decimal(value):.2f}"


def _day_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _periods(start: date, end: date) -> list[date]:
    current = start.replace(day=1)
    last = end.replace(day=1)
    periods = []
    while current <= last:
        periods.append(current)
        current = current.replace(day=monthrange(current.year, current.month)[1]) + timedelta(days=1)
    return periods


def _invoice_filters(start: date, end: date, user: User) -> list[Any]:
    filters = [
        Invoice.status == "issued",
        Invoice.doc_type != "proforma",
        Invoice.invoice_date >= start,
        Invoice.invoice_date <= end,
    ]
    return _where_branch(filters, Invoice, user)


def _purchase_filters(start: date, end: date, user: User) -> list[Any]:
    filters = [
        Purchase.doc_type == "purchase_invoice",
        Purchase.status == "posted",
        Purchase.doc_date >= start,
        Purchase.doc_date <= end,
    ]
    return _where_branch(filters, Purchase, user)


def _expense_filters(start: date, end: date, user: User) -> list[Any]:
    filters = [
        Expense.status == "posted",
        Expense.expense_date >= start,
        Expense.expense_date <= end,
    ]
    return _where_branch(filters, Expense, user)


def _recent_transactions(session: Session, start: date, end: date, user: User) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    invoice_query = (
        select(Invoice.invoice_number, Invoice.invoice_date, Invoice.grand_total, Invoice.status, Customer.name)
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(*_invoice_filters(start, end, user))
        .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
        .limit(10)
    )
    for number, entry_date, amount, entry_status, party in session.execute(invoice_query):
        entries.append({
            "reference": number or f"Invoice #{entry_date.isoformat()}",
            "date": entry_date.isoformat(),
            "description": f"Sales invoice · {party} · {entry_status}",
            "amount": _money(amount),
        })

    purchase_query = (
        select(Purchase.number, Purchase.doc_date, Purchase.grand_total, Supplier.name)
        .outerjoin(Supplier, Supplier.id == Purchase.supplier_id)
        .where(*_purchase_filters(start, end, user))
        .order_by(Purchase.doc_date.desc(), Purchase.id.desc())
        .limit(10)
    )
    for number, entry_date, amount, party in session.execute(purchase_query):
        entries.append({
            "reference": number,
            "date": entry_date.isoformat(),
            "description": f"Purchase · {party or 'Supplier'}",
            "amount": _money(amount),
        })

    payment_filters = [
        Payment.status == "recorded",
        Payment.payment_date >= start,
        Payment.payment_date <= end,
    ]
    payment_filters = _where_branch(payment_filters, Payment, user)
    payment_query = (
        select(Payment.payment_number, Payment.payment_date, Payment.amount, Payment.direction)
        .where(*payment_filters)
        .order_by(Payment.payment_date.desc(), Payment.id.desc())
        .limit(10)
    )
    for number, entry_date, amount, direction in session.execute(payment_query):
        entries.append({
            "reference": number,
            "date": entry_date.isoformat(),
            "description": "Receipt" if direction == "in" else "Supplier payment",
            "amount": _money(amount),
        })

    expense_query = (
        select(Expense.expense_number, Expense.expense_date, Expense.amount, Expense.gst_amount, Expense.vendor)
        .where(*_expense_filters(start, end, user))
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .limit(10)
    )
    for number, entry_date, amount, gst, vendor in session.execute(expense_query):
        entries.append({
            "reference": number,
            "date": entry_date.isoformat(),
            "description": f"Expense · {vendor or 'Business expense'}",
            "amount": _money(amount + gst),
        })

    entries.sort(key=lambda item: (item["date"], item["reference"]), reverse=True)
    return entries[:10]


@router.get("/dashboard")
def get_dashboard(
    user: Annotated[User, Depends(require_permissions("dashboard:read"))],
    session: Annotated[Session, Depends(get_db)],
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
) -> dict[str, Any]:
    if to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="'to' must be on or after 'from'",
        )
    if (to_date - from_date).days > 365:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Dashboard date range cannot exceed 366 days",
        )

    today = datetime.now(BUSINESS_TIMEZONE).date()
    month_start = today.replace(day=1)
    invoice_period = _invoice_filters(from_date, to_date, user)
    purchase_period = _purchase_filters(from_date, to_date, user)
    expense_period = _expense_filters(from_date, to_date, user)

    revenue = _sum(
        session,
        select(func.coalesce(func.sum(Invoice.grand_total), 0)).where(*invoice_period),
    )
    purchases = _sum(
        session,
        select(func.coalesce(func.sum(Purchase.grand_total), 0)).where(*purchase_period),
    )
    expenses = _sum(
        session,
        select(func.coalesce(func.sum(Expense.amount + Expense.gst_amount), 0)).where(*expense_period),
    )
    cost_of_goods = _sum(
        session,
        select(
            func.coalesce(
                func.sum(InvoiceItem.quantity * func.coalesce(InvoiceItem.unit_cost, 0)),
                0,
            )
        )
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .where(*invoice_period),
    )

    month_sales_filters = [
        Invoice.status == "issued",
        Invoice.doc_type != "proforma",
        Invoice.invoice_date >= month_start,
        Invoice.invoice_date <= today,
    ]
    month_sales_filters = _where_branch(month_sales_filters, Invoice, user)
    today_sales_filters = [
        Invoice.status == "issued",
        Invoice.doc_type != "proforma",
        Invoice.invoice_date == today,
    ]
    today_sales_filters = _where_branch(today_sales_filters, Invoice, user)

    allocated_invoices = (
        select(
            PaymentAllocation.invoice_id.label("invoice_id"),
            func.sum(PaymentAllocation.amount).label("allocated"),
        )
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id.is_not(None), Payment.status == "recorded")
        .group_by(PaymentAllocation.invoice_id)
        .subquery()
    )
    credited_invoices = (
        select(CreditNote.invoice_id.label("invoice_id"), func.sum(CreditNote.grand_total).label("credited"))
        .where(CreditNote.status == "issued")
        .group_by(CreditNote.invoice_id)
        .subquery()
    )
    receivables = _sum(
        session,
        select(
            func.coalesce(
                func.sum(
                    func.greatest(
                        Invoice.grand_total
                        - func.coalesce(allocated_invoices.c.allocated, 0)
                        - func.coalesce(credited_invoices.c.credited, 0),
                        0,
                    )
                ),
                0,
            )
        )
        .outerjoin(allocated_invoices, allocated_invoices.c.invoice_id == Invoice.id)
        .outerjoin(credited_invoices, credited_invoices.c.invoice_id == Invoice.id)
        .where(
            Invoice.status == "issued",
            Invoice.doc_type != "proforma",
            *_where_branch([], Invoice, user),
        ),
    )

    allocated_purchases = (
        select(
            PaymentAllocation.purchase_id.label("purchase_id"),
            func.sum(PaymentAllocation.amount).label("allocated"),
        )
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.purchase_id.is_not(None), Payment.status == "recorded")
        .group_by(PaymentAllocation.purchase_id)
        .subquery()
    )
    debited_purchases = (
        select(DebitNote.purchase_id.label("purchase_id"), func.sum(DebitNote.grand_total).label("debited"))
        .where(DebitNote.status == "issued")
        .group_by(DebitNote.purchase_id)
        .subquery()
    )
    payables = _sum(
        session,
        select(
            func.coalesce(
                func.sum(
                    func.greatest(
                        Purchase.grand_total
                        - func.coalesce(allocated_purchases.c.allocated, 0)
                        - func.coalesce(debited_purchases.c.debited, 0),
                        0,
                    )
                ),
                0,
            )
        )
        .outerjoin(allocated_purchases, allocated_purchases.c.purchase_id == Purchase.id)
        .outerjoin(debited_purchases, debited_purchases.c.purchase_id == Purchase.id)
        .where(
            Purchase.doc_type == "purchase_invoice",
            Purchase.status == "posted",
            *_where_branch([], Purchase, user),
        ),
    )

    customer_filters: list[Any] = [Customer.is_active.is_(True), Customer.deleted_at.is_(None)]
    if user.role.name == "Branch Manager":
        customer_filters.append(Customer.branch_id == user.branch_id if user.branch_id else Customer.id == -1)
    customers_count = session.scalar(select(func.count(Customer.id)).where(*customer_filters)) or 0
    suppliers_count = session.scalar(
        select(func.count(Supplier.id)).where(Supplier.is_active.is_(True), Supplier.deleted_at.is_(None))
    ) or 0
    products_count = session.scalar(
        select(func.count(Product.id)).where(Product.is_active.is_(True), Product.deleted_at.is_(None))
    ) or 0

    stock_totals = (
        select(Inventory.product_id, func.sum(Inventory.quantity).label("quantity"))
        .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
        .where(*_where_branch([Warehouse.is_active.is_(True)], Warehouse, user))
        .group_by(Inventory.product_id)
        .subquery()
    )
    low_stock = session.scalar(
        select(func.count(Product.id))
        .outerjoin(stock_totals, stock_totals.c.product_id == Product.id)
        .where(
            Product.is_active.is_(True),
            Product.deleted_at.is_(None),
            Product.is_service.is_(False),
            Product.min_stock > 0,
            func.coalesce(stock_totals.c.quantity, 0) < Product.min_stock,
        )
    ) or 0
    vehicle_stock = _sum(
        session,
        select(func.coalesce(func.sum(Inventory.quantity), 0))
        .join(Product, Product.id == Inventory.product_id)
        .join(ProductCategory, ProductCategory.id == Product.category_id)
        .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
        .where(
            Product.is_active.is_(True),
            Product.deleted_at.is_(None),
            or_(
                ProductCategory.name.ilike("%vehicle%"),
                ProductCategory.name.op("~*")(r"(^|[^[:alnum:]])ev([^[:alnum:]]|$)"),
            ),
            *_where_branch([Warehouse.is_active.is_(True)], Warehouse, user),
        ),
    )

    sales_by_day = dict(
        session.execute(
            select(Invoice.invoice_date, func.sum(Invoice.grand_total))
            .where(*invoice_period)
            .group_by(Invoice.invoice_date)
        ).all()
    )
    purchases_by_day = dict(
        session.execute(
            select(Purchase.doc_date, func.sum(Purchase.grand_total))
            .where(*purchase_period)
            .group_by(Purchase.doc_date)
        ).all()
    )

    revenue_by_month = dict(
        session.execute(
            select(
                func.date_trunc("month", Invoice.invoice_date).label("period"),
                func.sum(Invoice.grand_total),
            )
            .where(*invoice_period)
            .group_by("period")
        ).all()
    )
    expenses_by_month = dict(
        session.execute(
            select(
                func.date_trunc("month", Expense.expense_date).label("period"),
                func.sum(Expense.amount + Expense.gst_amount),
            )
            .where(*expense_period)
            .group_by("period")
        ).all()
    )
    revenue_expense_trend = []
    for period in _periods(from_date, to_date):
        period_key = datetime(period.year, period.month, 1)
        revenue_expense_trend.append({
            "period": period.strftime("%b %Y"),
            "revenue": _money(revenue_by_month.get(period_key, ZERO)),
            "expenses": _money(expenses_by_month.get(period_key, ZERO)),
        })

    top_products_query = (
        select(
            InvoiceItem.description,
            func.sum(InvoiceItem.quantity),
            func.sum(InvoiceItem.taxable_value),
        )
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .where(*invoice_period)
        .group_by(InvoiceItem.description)
        .order_by(func.sum(InvoiceItem.taxable_value).desc())
        .limit(5)
    )
    top_products = [
        {"name": name, "quantity": str(quantity), "amount": _money(amount)}
        for name, quantity, amount in session.execute(top_products_query)
    ]
    payment_filters = [
        Payment.status == "recorded",
        Payment.payment_date >= from_date,
        Payment.payment_date <= to_date,
    ]
    payment_filters = _where_branch(payment_filters, Payment, user)
    payments_by_method = session.execute(
        select(PaymentMethod.name, func.sum(Payment.amount))
        .join(Payment, Payment.method_id == PaymentMethod.id)
        .where(*payment_filters)
        .group_by(PaymentMethod.name)
        .order_by(func.sum(Payment.amount).desc())
    ).all()

    recent_invoice_query = (
        select(Invoice.invoice_number, Customer.name, Invoice.invoice_date, Invoice.grand_total, Invoice.status)
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(*invoice_period)
        .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
        .limit(10)
    )
    recent_invoices = [
        {
            "invoice_number": number or "",
            "customer_name": customer_name,
            "invoice_date": invoice_date.isoformat(),
            "grand_total": _money(total),
            "status": invoice_status,
        }
        for number, customer_name, invoice_date, total, invoice_status in session.execute(recent_invoice_query)
    ]

    return {
        "kpis": {
            "sales_today": _money(_sum(session, select(func.coalesce(func.sum(Invoice.grand_total), 0)).where(*today_sales_filters))),
            "sales_month": _money(_sum(session, select(func.coalesce(func.sum(Invoice.grand_total), 0)).where(*month_sales_filters))),
            "purchases_total": _money(purchases),
            "revenue_total": _money(revenue),
            "expenses_total": _money(expenses),
            "gross_profit": _money(revenue - cost_of_goods),
            "receivables_outstanding": _money(receivables),
            "payables_outstanding": _money(payables),
            "customers_total": customers_count,
            "suppliers_total": suppliers_count,
            "products_total": products_count,
            "low_stock_products": low_stock,
            "ev_vehicles_in_stock": _money(vehicle_stock),
        },
        "sales_trend": [
            {"date": day.isoformat(), "amount": _money(sales_by_day.get(day, ZERO))}
            for day in _day_range(from_date, to_date)
        ],
        "purchase_trend": [
            {"date": day.isoformat(), "amount": _money(purchases_by_day.get(day, ZERO))}
            for day in _day_range(from_date, to_date)
        ],
        "revenue_vs_expenses": revenue_expense_trend,
        "top_products": top_products,
        "payment_methods": [
            {"method": method, "amount": _money(amount)}
            for method, amount in payments_by_method
        ],
        "recent_invoices": recent_invoices,
        "recent_transactions": _recent_transactions(session, from_date, to_date, user),
    }
