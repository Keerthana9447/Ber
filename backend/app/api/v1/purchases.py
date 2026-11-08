from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import (
    AuditLog,
    CompanySettings,
    DebitNote,
    DebitNoteItem,
    GoodsReceipt,
    GoodsReceiptItem,
    GstRate,
    Inventory,
    JournalEntry,
    JournalEntryLine,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    Product,
    ProductSerial,
    Purchase,
    PurchaseItem,
    StockMovement,
    State,
    Supplier,
    User,
    Warehouse,
)
from berev_core.accounting import (
    INPUT_TAX,
    JournalEntry as CoreJournalEntry,
    Line as CoreLine,
    post_purchase_invoice,
    post_supplier_payment,
)
from berev_core.costing import weighted_average_cost
from berev_core.gst import GSTError, InvoiceResult, LineInput, compute_invoice, is_valid_gstin
from berev_core.numbering import next_sequence

router = APIRouter(tags=["purchases", "suppliers"])
ZERO = Decimal("0.00")


class SupplierWrite(BaseModel):
    code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    gstin: str | None = Field(default=None, max_length=32)
    pan: str | None = Field(default=None, max_length=16)
    address: str | None = Field(default=None, max_length=2000)
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
    city: str | None = Field(default=None, max_length=255)
    payment_terms_days: int = Field(default=0, ge=0, le=365)
    is_active: bool = True

    @field_validator("code", "name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("gstin", "pan", "state_code")
    @classmethod
    def normalize_identifiers(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @field_validator("phone", "email", "address", "city")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized.casefold() if normalized and "@" in normalized else normalized or None

    @model_validator(mode="after")
    def validate_gstin(self) -> SupplierWrite:
        if self.gstin and (
            self.state_code is None or not is_valid_gstin(self.gstin, self.state_code)
        ):
            raise ValueError("GSTIN is invalid or its state prefix does not match state_code")
        return self


class PurchaseLineWrite(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    discount: Decimal = Field(default=ZERO, ge=0, max_digits=14, decimal_places=2)
    gst_rate: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    cess_rate: Decimal = Field(default=ZERO, ge=0, le=100, max_digits=5, decimal_places=2)


class PurchaseRequestWrite(BaseModel):
    supplier_id: int | None = Field(default=None, gt=0)
    doc_date: date
    due_date: date | None = None
    place_of_supply_code: str | None = Field(default=None, min_length=2, max_length=2)
    lines: list[PurchaseLineWrite] = Field(min_length=1, max_length=500)


class ReceiptSerialWrite(BaseModel):
    chassis_no: str | None = Field(default=None, max_length=255)
    vin: str | None = Field(default=None, max_length=255)
    motor_no: str | None = Field(default=None, max_length=255)
    battery_serial: str | None = Field(default=None, max_length=255)
    battery_chemistry: str | None = Field(default=None, max_length=128)
    battery_capacity_kwh: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    variant: str | None = Field(default=None, max_length=255)
    mfg_date: date | None = None
    warranty_start: date | None = None
    warranty_months: int | None = Field(default=None, ge=0, le=600)


class ReceiptLineWrite(BaseModel):
    purchase_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    serials: list[ReceiptSerialWrite] = Field(default_factory=list, max_length=500)


class GoodsReceiptWrite(BaseModel):
    warehouse_id: int = Field(gt=0)
    received_date: date
    lines: list[ReceiptLineWrite] = Field(min_length=1, max_length=500)


class PurchaseInvoiceLineWrite(BaseModel):
    purchase_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    discount: Decimal = Field(default=ZERO, ge=0, max_digits=14, decimal_places=2)
    gst_rate: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    cess_rate: Decimal = Field(default=ZERO, ge=0, le=100, max_digits=5, decimal_places=2)


class PurchaseInvoiceWrite(BaseModel):
    supplier_invoice_no: str = Field(min_length=1, max_length=255)
    doc_date: date
    due_date: date | None = None
    lines: list[PurchaseInvoiceLineWrite] = Field(min_length=1, max_length=500)


class SupplierPaymentAllocationWrite(BaseModel):
    purchase_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class SupplierPaymentWrite(BaseModel):
    supplier_id: int = Field(gt=0)
    method_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_date: date
    reference_no: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)
    allocations: list[SupplierPaymentAllocationWrite] | None = Field(default=None, max_length=100)


class PurchaseReturnLineWrite(BaseModel):
    purchase_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    warehouse_id: int = Field(gt=0)
    serial_ids: list[int] = Field(default_factory=list, max_length=500)


class PurchaseReturnWrite(BaseModel):
    note_date: date
    reason: str = Field(min_length=10, max_length=2000)
    lines: list[PurchaseReturnLineWrite] = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("Purchase return reason must be at least 10 characters")
        return normalized


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


def _line_result(
    session: Session,
    supplier: Supplier,
    place_of_supply_code: str,
    lines: list[PurchaseLineWrite],
) -> tuple[InvoiceResult, list[Product]]:
    settings = session.scalar(select(CompanySettings).order_by(CompanySettings.id).limit(1))
    if settings is None or settings.state_code is None:
        raise HTTPException(status_code=409, detail="Configure the company state before creating purchases")
    if supplier.state_code is None:
        raise HTTPException(status_code=422, detail="Supplier state_code is required for GST calculation")
    configured_rates = set(session.scalars(select(GstRate.rate).where(GstRate.is_active.is_(True))).all())
    inputs: list[LineInput] = []
    products: list[Product] = []
    for line in lines:
        product = session.get(Product, line.product_id)
        if product is None or product.deleted_at is not None or not product.is_active or product.is_service:
            raise HTTPException(status_code=422, detail=f"Product {line.product_id} is not an active stock product")
        if line.gst_rate not in configured_rates:
            raise HTTPException(status_code=422, detail=f"GST rate {line.gst_rate} is not configured")
        if line.gst_rate != product.gst_rate or line.cess_rate != product.cess_rate:
            raise HTTPException(status_code=422, detail=f"Tax rates do not match product {product.sku}")
        inputs.append(
            LineInput(
                description=product.name,
                hsn_sac=product.hsn_sac,
                quantity=line.quantity,
                unit_price=line.unit_price,
                gst_rate=line.gst_rate,
                discount_amount=line.discount,
                cess_rate=line.cess_rate,
                unit=product.unit,
            )
        )
        products.append(product)
    try:
        result = compute_invoice(
            inputs,
            supplier.state_code,
            place_of_supply_code or settings.state_code,
            allowed_rates=configured_rates,
        )
    except GSTError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return result, products


def _store_items(
    session: Session,
    purchase_id: int,
    result: InvoiceResult,
    products: list[Product],
    source_ids: list[int | None] | None = None,
    quantities: list[Decimal] | None = None,
) -> None:
    for index, (line, product) in enumerate(zip(result.lines, products, strict=True)):
        quantity = quantities[index] if quantities else line.quantity
        session.add(
            PurchaseItem(
                purchase_id=purchase_id,
                source_purchase_item_id=source_ids[index] if source_ids else None,
                product_id=product.id,
                description=line.description,
                hsn_sac=line.hsn_sac,
                quantity=quantity,
                unit_price=line.unit_price,
                discount=line.discount,
                taxable_value=line.taxable,
                gst_rate=line.gst_rate,
                cgst=line.cgst,
                sgst=line.sgst,
                igst=line.igst,
                cess=line.cess,
                cess_rate=line.cess_rate,
            )
        )


def _journal(
    session: Session,
    core_entry: CoreJournalEntry,
    user_id: int,
    source_type: str,
    source_id: int,
    supplier_id: int | None = None,
) -> JournalEntry:
    entry = JournalEntry(
        entry_date=core_entry.entry_date,
        reference=core_entry.reference,
        narration=core_entry.narration,
        source_type=source_type,
        source_id=source_id,
        posted_by=user_id,
    )
    session.add(entry)
    session.flush()
    session.add_all(
        JournalEntryLine(
            entry_id=entry.id,
            account_code=line.account,
            debit=line.debit,
            credit=line.credit,
            supplier_id=supplier_id if line.account == "2000" else None,
        )
        for line in core_entry.lines
    )
    return entry


def _supplier_dict(supplier: Supplier) -> dict[str, object]:
    return {
        "id": supplier.id,
        "code": supplier.code,
        "name": supplier.name,
        "gstin": supplier.gstin,
        "phone": supplier.phone,
        "email": supplier.email,
        "state_code": supplier.state_code.strip() if supplier.state_code else None,
        "city": supplier.city,
        "is_active": supplier.is_active,
    }


@router.get("/suppliers")
def list_suppliers(
    _: Annotated[User, Depends(require_permissions("suppliers:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
) -> dict[str, object]:
    filters = [Supplier.deleted_at.is_(None)]
    if q.strip():
        term = f"%{q.strip()}%"
        filters.append(or_(Supplier.code.ilike(term), Supplier.name.ilike(term), Supplier.gstin.ilike(term)))
    total = session.scalar(select(func.count(Supplier.id)).where(*filters)) or 0
    rows = session.scalars(
        select(Supplier).where(*filters).order_by(Supplier.name, Supplier.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [_supplier_dict(row) for row in rows], "total": total}


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
def create_supplier(
    body: SupplierWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("suppliers:create"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    values = body.model_dump()
    for key in ("code", "name"):
        values[key] = values[key].strip()
    for key in ("gstin", "pan", "state_code"):
        if values[key] is not None:
            values[key] = values[key].strip().upper()
    if values["email"]:
        values["email"] = values["email"].strip().casefold()
    if values["state_code"] and session.get(State, values["state_code"]) is None:
        raise HTTPException(status_code=422, detail="Unknown supplier state_code")
    supplier = Supplier(**values)
    session.add(supplier)
    try:
        session.flush()
        _audit(session, user, request, "create", "supplier", supplier.id, {"code": supplier.code, "name": supplier.name})
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Supplier conflicts with an existing code, GSTIN, or state") from None
    return _supplier_dict(supplier)


@router.put("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: int,
    body: SupplierWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("suppliers:update"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    supplier = session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id, Supplier.deleted_at.is_(None))
        .with_for_update()
    )
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    values = body.model_dump()
    values["code"] = values["code"].strip()
    values["name"] = values["name"].strip()
    for key in ("gstin", "pan", "state_code"):
        if values[key] is not None:
            values[key] = values[key].strip().upper()
    if values["email"]:
        values["email"] = values["email"].strip().casefold()
    if values["state_code"] and session.get(State, values["state_code"]) is None:
        raise HTTPException(status_code=422, detail="Unknown supplier state_code")
    old_values = {
        "code": supplier.code,
        "name": supplier.name,
        "gstin": supplier.gstin,
        "phone": supplier.phone,
        "city": supplier.city,
        "state_code": supplier.state_code.strip() if supplier.state_code else None,
        "is_active": supplier.is_active,
    }
    for key, value in values.items():
        setattr(supplier, key, value)
    try:
        _audit(
            session, user, request, "update", "supplier", supplier.id,
            {"old": old_values, "new": _supplier_dict(supplier)},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Supplier conflicts with an existing code, GSTIN, or state") from None
    return _supplier_dict(supplier)


@router.get("/purchases")
def list_purchases(
    _: Annotated[User, Depends(require_permissions("purchases:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    doc_type: str | None = None,
) -> dict[str, object]:
    filters = []
    if q.strip():
        term = f"%{q.strip()}%"
        filters.append(Purchase.number.ilike(term))
    if doc_type is not None:
        if doc_type not in {"purchase_request", "purchase_order", "purchase_invoice"}:
            raise HTTPException(status_code=422, detail="Unknown purchase document type")
        filters.append(Purchase.doc_type == doc_type)
    total = session.scalar(select(func.count(Purchase.id)).where(*filters)) or 0
    rows = session.execute(
        select(Purchase, Supplier.name)
        .outerjoin(Supplier, Supplier.id == Purchase.supplier_id)
        .where(*filters)
        .order_by(Purchase.doc_date.desc(), Purchase.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": purchase.id,
                "number": purchase.number,
                "doc_type": purchase.doc_type,
                "supplier_id": purchase.supplier_id,
                "supplier_name": supplier_name,
                "doc_date": purchase.doc_date.isoformat(),
                "status": purchase.status,
                "grand_total": format(purchase.grand_total, "f"),
                "parent_id": purchase.parent_id,
            }
            for purchase, supplier_name in rows
        ],
        "total": total,
    }


@router.get("/purchases/{purchase_id}")
def get_purchase(
    purchase_id: int,
    _: Annotated[User, Depends(require_permissions("purchases:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    purchase = session.get(Purchase, purchase_id)
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase document not found")
    supplier = session.get(Supplier, purchase.supplier_id) if purchase.supplier_id else None
    items = session.scalars(
        select(PurchaseItem).where(PurchaseItem.purchase_id == purchase.id).order_by(PurchaseItem.id)
    ).all()
    return {
        "id": purchase.id,
        "number": purchase.number,
        "doc_type": purchase.doc_type,
        "supplier_id": purchase.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "supplier_invoice_no": purchase.supplier_invoice_no,
        "doc_date": purchase.doc_date.isoformat(),
        "due_date": purchase.due_date.isoformat() if purchase.due_date else None,
        "status": purchase.status,
        "taxable_total": format(purchase.taxable_total, "f"),
        "cgst_total": format(purchase.cgst_total, "f"),
        "sgst_total": format(purchase.sgst_total, "f"),
        "igst_total": format(purchase.igst_total, "f"),
        "cess_total": format(purchase.cess_total, "f"),
        "grand_total": format(purchase.grand_total, "f"),
        "parent_id": purchase.parent_id,
        "items": [
            {
                "id": item.id,
                "source_purchase_item_id": item.source_purchase_item_id,
                "product_id": item.product_id,
                "description": item.description,
                "hsn_sac": item.hsn_sac,
                "quantity": format(item.quantity, "f"),
                "unit_price": format(item.unit_price, "f"),
                "discount": format(item.discount, "f"),
                "taxable_value": format(item.taxable_value, "f"),
                "gst_rate": format(item.gst_rate, "f"),
                "cess_rate": format(item.cess_rate, "f"),
                "cgst": format(item.cgst, "f"),
                "sgst": format(item.sgst, "f"),
                "igst": format(item.igst, "f"),
                "cess": format(item.cess, "f"),
            }
            for item in items
        ],
    }


@router.post("/purchases/requests", status_code=status.HTTP_201_CREATED)
def create_purchase_request(
    body: PurchaseRequestWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("purchases:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    supplier = session.get(Supplier, body.supplier_id) if body.supplier_id else None
    if supplier is None or supplier.deleted_at is not None or not supplier.is_active:
        raise HTTPException(status_code=422, detail="An active supplier is required to calculate purchase tax")
    settings = session.scalar(select(CompanySettings).order_by(CompanySettings.id).limit(1))
    place = body.place_of_supply_code or (settings.state_code if settings else None)
    if place is None:
        raise HTTPException(status_code=409, detail="Configure the company state before creating purchases")
    result, products = _line_result(session, supplier, place, body.lines)
    if session.get(State, place) is None:
        raise HTTPException(status_code=422, detail="Unknown place_of_supply_code")
    purchase = Purchase(
        doc_type="purchase_request",
        number=f"PR-TMP-{uuid4().hex}",
        supplier_id=supplier.id,
        doc_date=body.doc_date,
        due_date=body.due_date,
        place_of_supply_code=place,
        status="draft",
        taxable_total=result.taxable_total,
        cgst_total=result.cgst_total,
        sgst_total=result.sgst_total,
        igst_total=result.igst_total,
        cess_total=result.cess_total,
        round_off=result.round_off,
        grand_total=result.grand_total,
        created_by=user.id,
    )
    session.add(purchase)
    try:
        session.flush()
        purchase.number = f"PR-{purchase.id:08d}"
        _store_items(session, purchase.id, result, products)
        _audit(session, user, request, "create", "purchase_request", purchase.id, {"number": purchase.number})
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Purchase request conflicts with current records") from None
    return {"id": purchase.id, "number": purchase.number, "status": purchase.status, "grand_total": format(purchase.grand_total, "f")}


@router.post("/purchases/{purchase_id}/approve")
def approve_purchase_request(
    purchase_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("purchases:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    purchase = session.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update())
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase request not found")
    if purchase.doc_type != "purchase_request" or purchase.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft purchase request can be approved")
    purchase.status = "approved"
    _audit(session, user, request, "approve", "purchase_request", purchase.id, {"status": "approved"})
    session.commit()
    return {"id": purchase.id, "status": purchase.status}


@router.post("/purchases/{purchase_id}/convert-to-order", status_code=status.HTTP_201_CREATED)
def convert_request_to_order(
    purchase_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("purchases:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    source = session.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update())
    if source is None:
        raise HTTPException(status_code=404, detail="Purchase request not found")
    if source.doc_type != "purchase_request" or source.status != "approved" or source.supplier_id is None:
        raise HTTPException(status_code=409, detail="An approved supplier-linked request is required")
    existing_order = session.scalar(
        select(Purchase.id).where(Purchase.doc_type == "purchase_order", Purchase.parent_id == source.id)
    )
    if existing_order is not None:
        raise HTTPException(status_code=409, detail="Purchase request has already been converted to an order")
    source_items = session.scalars(select(PurchaseItem).where(PurchaseItem.purchase_id == source.id).order_by(PurchaseItem.id)).all()
    order = Purchase(
        doc_type="purchase_order",
        number=f"PO-TMP-{uuid4().hex}",
        supplier_id=source.supplier_id,
        doc_date=source.doc_date,
        due_date=source.due_date,
        place_of_supply_code=source.place_of_supply_code,
        status="ordered",
        taxable_total=source.taxable_total,
        cgst_total=source.cgst_total,
        sgst_total=source.sgst_total,
        igst_total=source.igst_total,
        cess_total=source.cess_total,
        round_off=source.round_off,
        grand_total=source.grand_total,
        parent_id=source.id,
        created_by=user.id,
    )
    session.add(order)
    session.flush()
    order.number = f"PO-{order.id:08d}"
    for line in source_items:
        session.add(
            PurchaseItem(
                purchase_id=order.id,
                product_id=line.product_id,
                description=line.description,
                hsn_sac=line.hsn_sac,
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount=line.discount,
                taxable_value=line.taxable_value,
                gst_rate=line.gst_rate,
                cgst=line.cgst,
                sgst=line.sgst,
                igst=line.igst,
                cess=line.cess,
                cess_rate=line.cess_rate,
            )
        )
    _audit(session, user, request, "convert_to_order", "purchase_order", order.id, {"source_request_id": source.id})
    source.status = "ordered"
    session.commit()
    return {"id": order.id, "number": order.number, "parent_id": source.id, "status": order.status}


@router.post("/purchases/{purchase_id}/goods-receipts", status_code=status.HTTP_201_CREATED)
def post_goods_receipt(
    purchase_id: int,
    body: GoodsReceiptWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("purchases:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    order = session.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update())
    warehouse = session.get(Warehouse, body.warehouse_id)
    if order is None or order.doc_type != "purchase_order" or order.status not in {"ordered", "received"}:
        raise HTTPException(status_code=404, detail="Open purchase order not found")
    if warehouse is None or not warehouse.is_active:
        raise HTTPException(status_code=422, detail="An active warehouse is required")
    if len({line.purchase_item_id for line in body.lines}) != len(body.lines):
        raise HTTPException(status_code=422, detail="Each purchase order line may occur only once per receipt")
    item_ids = [line.purchase_item_id for line in body.lines]
    source_items = session.scalars(
        select(PurchaseItem).where(PurchaseItem.purchase_id == order.id, PurchaseItem.id.in_(item_ids)).with_for_update()
    ).all()
    item_map = {item.id: item for item in source_items}
    if len(item_map) != len(item_ids):
        raise HTTPException(status_code=422, detail="Goods receipt references a line outside this purchase order")
    received_before = dict(
        session.execute(
            select(GoodsReceiptItem.purchase_item_id, func.coalesce(func.sum(GoodsReceiptItem.quantity), 0))
            .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptItem.goods_receipt_id)
            .where(GoodsReceipt.purchase_id == order.id, GoodsReceipt.status == "posted")
            .group_by(GoodsReceiptItem.purchase_item_id)
        ).all()
    )
    for line in body.lines:
        source = item_map[line.purchase_item_id]
        already_received = Decimal(received_before.get(source.id, 0))
        if already_received + line.quantity > source.quantity:
            raise HTTPException(status_code=409, detail=f"Receipt exceeds the remaining quantity for purchase line {source.id}")
        product = session.get(Product, source.product_id)
        if product is None:
            raise HTTPException(status_code=409, detail="Purchase order product no longer exists")
        if product.is_serialized:
            if line.quantity != line.quantity.to_integral_value() or len(line.serials) != int(line.quantity):
                raise HTTPException(status_code=422, detail=f"{product.name} requires one unique serial record per received unit")
            if any(not (serial.chassis_no or serial.vin or serial.motor_no or serial.battery_serial) for serial in line.serials):
                raise HTTPException(status_code=422, detail=f"{product.name} serial records require a chassis, VIN, motor, or battery identifier")
        elif line.serials:
            raise HTTPException(status_code=422, detail=f"{product.name} is not serialized")

    receipt = GoodsReceipt(
        grn_number=f"GRN-TMP-{uuid4().hex}",
        purchase_id=order.id,
        warehouse_id=warehouse.id,
        received_date=body.received_date,
        status="posted",
        created_by=user.id,
    )
    session.add(receipt)
    try:
        session.flush()
        receipt.grn_number = f"GRN-{receipt.id:08d}"
        for line in body.lines:
            source = item_map[line.purchase_item_id]
            product = session.get(Product, source.product_id)
            assert product is not None
            unit_cost = (source.taxable_value / source.quantity).quantize(Decimal("0.0001"))
            session.execute(
                insert(Inventory)
                .values(product_id=product.id, warehouse_id=warehouse.id, quantity=Decimal("0"), average_unit_cost=None)
                .on_conflict_do_nothing(index_elements=[Inventory.product_id, Inventory.warehouse_id])
            )
            inventory = session.scalar(
                select(Inventory)
                .where(Inventory.product_id == product.id, Inventory.warehouse_id == warehouse.id)
                .with_for_update()
            )
            assert inventory is not None
            if inventory.quantity > 0 and inventory.average_unit_cost is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Opening valuation is required before receiving {product.name} into {warehouse.name}",
                )
            if product.is_serialized:
                serial_rows: list[ProductSerial] = []
                for serial_data in line.serials:
                    serial = ProductSerial(
                        product_id=product.id,
                        **serial_data.model_dump(),
                        supplier_id=order.supplier_id,
                        purchase_item_id=source.id,
                        warehouse_id=warehouse.id,
                        status="available",
                    )
                    session.add(serial)
                    session.flush()
                    serial_rows.append(serial)
                    session.add(
                        GoodsReceiptItem(
                            goods_receipt_id=receipt.id,
                            purchase_item_id=source.id,
                            quantity=Decimal("1"),
                            serial_id=serial.id,
                            unit_cost=unit_cost,
                        )
                    )
                    session.add(
                        StockMovement(
                            product_id=product.id,
                            warehouse_id=warehouse.id,
                            serial_id=serial.id,
                            movement_type="purchase_in",
                            quantity_change=Decimal("1"),
                            unit_cost=unit_cost,
                            ref_type="goods_receipt",
                            ref_id=receipt.id,
                            created_by=user.id,
                        )
                    )
            else:
                session.add(
                    GoodsReceiptItem(
                        goods_receipt_id=receipt.id,
                        purchase_item_id=source.id,
                        quantity=line.quantity,
                        unit_cost=unit_cost,
                    )
                )
                session.add(
                    StockMovement(
                        product_id=product.id,
                        warehouse_id=warehouse.id,
                        movement_type="purchase_in",
                        quantity_change=line.quantity,
                        unit_cost=unit_cost,
                        ref_type="goods_receipt",
                        ref_id=receipt.id,
                        created_by=user.id,
                    )
                )
            inventory.average_unit_cost = weighted_average_cost(
                Decimal(inventory.quantity),
                inventory.average_unit_cost,
                line.quantity,
                unit_cost,
            )
            inventory.quantity = Decimal(inventory.quantity) + line.quantity
        receipt_value = sum(
            (
                (Decimal(item.quantity) * Decimal(item.unit_cost)).quantize(Decimal("0.01"))
                for item in session.scalars(
                    select(GoodsReceiptItem).where(GoodsReceiptItem.goods_receipt_id == receipt.id)
                ).all()
            ),
            ZERO,
        )
        if receipt_value > ZERO:
            _journal(
                session,
                CoreJournalEntry(
                    body.received_date,
                    receipt.grn_number,
                    f"Goods receipt {receipt.grn_number}",
                    [
                        CoreLine("1200", receipt_value, ZERO),
                        CoreLine("2104", ZERO, receipt_value),
                    ],
                ),
                user.id,
                "goods_receipt",
                receipt.id,
                order.supplier_id,
            )
        received_after = dict(
            session.execute(
                select(GoodsReceiptItem.purchase_item_id, func.coalesce(func.sum(GoodsReceiptItem.quantity), 0))
                .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptItem.goods_receipt_id)
                .where(GoodsReceipt.purchase_id == order.id, GoodsReceipt.status == "posted")
                .group_by(GoodsReceiptItem.purchase_item_id)
            ).all()
        )
        all_items = session.scalars(select(PurchaseItem).where(PurchaseItem.purchase_id == order.id)).all()
        if all(Decimal(received_after.get(item.id, 0)) >= item.quantity for item in all_items):
            order.status = "received"
        _audit(
            session, user, request, "post_goods_receipt", "goods_receipt", receipt.id,
            {"grn_number": receipt.grn_number, "purchase_id": order.id, "warehouse_id": warehouse.id},
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="A serial identifier is already registered or the receipt conflicts with current stock") from None
    return {"id": receipt.id, "grn_number": receipt.grn_number, "status": receipt.status}


@router.post("/purchases/{purchase_id}/invoices", status_code=status.HTTP_201_CREATED)
def post_purchase_invoice_document(
    purchase_id: int,
    body: PurchaseInvoiceWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("purchases:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    order = session.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update())
    if order is None or order.doc_type != "purchase_order" or order.status not in {"ordered", "received"}:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    supplier = session.get(Supplier, order.supplier_id) if order.supplier_id else None
    if supplier is None:
        raise HTTPException(status_code=409, detail="Purchase order has no supplier")
    order_items = session.scalars(select(PurchaseItem).where(PurchaseItem.purchase_id == order.id).order_by(PurchaseItem.id)).all()
    received = dict(
        session.execute(
            select(GoodsReceiptItem.purchase_item_id, func.coalesce(func.sum(GoodsReceiptItem.quantity), 0))
            .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptItem.goods_receipt_id)
            .where(GoodsReceipt.purchase_id == order.id, GoodsReceipt.status == "posted")
            .group_by(GoodsReceiptItem.purchase_item_id)
        ).all()
    )
    previously_invoiced = dict(
        session.execute(
            select(PurchaseItem.source_purchase_item_id, func.coalesce(func.sum(PurchaseItem.quantity), 0))
            .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
            .where(PurchaseItem.source_purchase_item_id.is_not(None), Purchase.parent_id == order.id, Purchase.doc_type == "purchase_invoice", Purchase.status == "posted")
            .group_by(PurchaseItem.source_purchase_item_id)
        ).all()
    )
    order_item_map = {item.id: item for item in order_items}
    if len({line.purchase_item_id for line in body.lines}) != len(body.lines):
        raise HTTPException(status_code=422, detail="Each purchase order line may occur only once per invoice")
    selected: list[tuple[PurchaseItem, PurchaseInvoiceLineWrite]] = []
    for line in body.lines:
        item = order_item_map.get(line.purchase_item_id)
        if item is None:
            raise HTTPException(status_code=422, detail="Purchase invoice references a line outside this purchase order")
        remaining_received = Decimal(received.get(item.id, 0)) - Decimal(previously_invoiced.get(item.id, 0))
        if line.quantity > remaining_received:
            raise HTTPException(status_code=409, detail=f"Invoice quantity exceeds received uninvoiced goods for line {item.id}")
        if line.gst_rate != item.gst_rate or line.cess_rate != item.cess_rate:
            raise HTTPException(status_code=422, detail=f"GST rates do not match purchase order line {item.id}")
        selected.append((item, line))
    purchase_lines = [
        PurchaseLineWrite(
            product_id=item.product_id,
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount=line.discount,
            gst_rate=line.gst_rate,
            cess_rate=line.cess_rate,
        )
        for item, line in selected
    ]
    result, products = _line_result(
        session,
        supplier,
        order.place_of_supply_code or supplier.state_code or "",
        purchase_lines,
    )
    invoice_quantities = [line.quantity for _, line in selected]
    source_ids = [item.id for item, _ in selected]
    received_value = ZERO
    for source, invoice_line in selected:
        receipt_cost = session.scalar(
            select(func.coalesce(func.sum(GoodsReceiptItem.quantity * GoodsReceiptItem.unit_cost), 0))
            .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptItem.goods_receipt_id)
            .where(
                GoodsReceipt.purchase_id == order.id,
                GoodsReceipt.status == "posted",
                GoodsReceiptItem.purchase_item_id == source.id,
            )
        ) or ZERO
        receipt_quantity = Decimal(received.get(source.id, 0))
        if receipt_quantity <= 0:
            raise HTTPException(status_code=409, detail=f"No posted receipt exists for purchase order line {source.id}")
        received_value += (
            Decimal(receipt_cost) / receipt_quantity * invoice_line.quantity
        ).quantize(Decimal("0.01"))
    invoice = Purchase(
        doc_type="purchase_invoice",
        number=f"PI-TMP-{uuid4().hex}",
        supplier_id=supplier.id,
        supplier_invoice_no=body.supplier_invoice_no.strip(),
        doc_date=body.doc_date,
        due_date=body.due_date,
        branch_id=order.branch_id,
        place_of_supply_code=order.place_of_supply_code,
        status="posted",
        taxable_total=result.taxable_total,
        cgst_total=result.cgst_total,
        sgst_total=result.sgst_total,
        igst_total=result.igst_total,
        cess_total=result.cess_total,
        round_off=result.round_off,
        grand_total=result.grand_total,
        parent_id=order.id,
        created_by=user.id,
    )
    session.add(invoice)
    try:
        session.flush()
        invoice.number = f"PI-{invoice.id:08d}"
        _store_items(
            session, invoice.id, result, products,
            source_ids=source_ids,
            quantities=invoice_quantities,
        )
        core_entry = post_purchase_invoice(body.doc_date, invoice.number, result, received_value)
        _journal(session, core_entry, user.id, "purchase_invoice", invoice.id, supplier.id)
        _audit(
            session, user, request, "post_purchase_invoice", "purchase_invoice", invoice.id,
            {"number": invoice.number, "supplier_id": supplier.id, "grand_total": format(invoice.grand_total, "f")},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Supplier invoice number is duplicated or purchase invoice conflicts with current records") from None
    return {"id": invoice.id, "number": invoice.number, "status": invoice.status, "grand_total": format(invoice.grand_total, "f")}


@router.post("/purchases/{purchase_id}/debit-notes", status_code=status.HTTP_201_CREATED)
def post_purchase_return(
    purchase_id: int,
    body: PurchaseReturnWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("purchases:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    invoice = session.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update())
    if invoice is None or invoice.doc_type != "purchase_invoice" or invoice.status != "posted":
        raise HTTPException(status_code=404, detail="Posted purchase invoice not found")
    supplier = session.get(Supplier, invoice.supplier_id) if invoice.supplier_id else None
    if supplier is None:
        raise HTTPException(status_code=409, detail="Purchase invoice has no supplier")
    if len({serial_id for line in body.lines for serial_id in line.serial_ids}) != sum(
        len(line.serial_ids) for line in body.lines
    ):
        raise HTTPException(status_code=422, detail="A serial may be returned only once")
    invoice_items = {
        item.id: item
        for item in session.scalars(
            select(PurchaseItem).where(PurchaseItem.purchase_id == invoice.id).with_for_update()
        ).all()
    }
    requested_by_item: dict[int, Decimal] = {}
    for line in body.lines:
        if line.purchase_item_id not in invoice_items:
            raise HTTPException(status_code=422, detail="Return references a line outside this purchase invoice")
        requested_by_item[line.purchase_item_id] = (
            requested_by_item.get(line.purchase_item_id, Decimal("0")) + line.quantity
        )
    already_returned = dict(
        session.execute(
            select(DebitNoteItem.purchase_item_id, func.coalesce(func.sum(DebitNoteItem.quantity), 0))
            .join(DebitNote, DebitNote.id == DebitNoteItem.debit_note_id)
            .where(DebitNote.purchase_id == invoice.id, DebitNote.status == "issued")
            .group_by(DebitNoteItem.purchase_item_id)
        ).all()
    )
    for item_id, quantity in requested_by_item.items():
        if Decimal(already_returned.get(item_id, 0)) + quantity > invoice_items[item_id].quantity:
            raise HTTPException(status_code=409, detail=f"Return exceeds billed quantity for purchase line {item_id}")

    input_lines: list[LineInput] = []
    products: list[Product] = []
    serial_rows: list[list[ProductSerial]] = []
    inventories: list[Inventory] = []
    for line in body.lines:
        item = invoice_items[line.purchase_item_id]
        product = session.get(Product, item.product_id)
        inventory = session.scalar(
            select(Inventory)
            .where(Inventory.product_id == item.product_id, Inventory.warehouse_id == line.warehouse_id)
            .with_for_update()
        )
        warehouse = session.get(Warehouse, line.warehouse_id)
        if product is None or inventory is None or warehouse is None or not warehouse.is_active:
            raise HTTPException(status_code=409, detail="Return requires the product to be on hand in an active warehouse")
        if inventory.quantity < line.quantity:
            raise HTTPException(status_code=409, detail=f"Insufficient stock to return {product.name}")
        if inventory.average_unit_cost is None:
            raise HTTPException(status_code=409, detail=f"{product.name} has no known weighted-average cost")
        serials: list[ProductSerial] = []
        if product.is_serialized:
            if line.quantity != Decimal("1") or len(line.serial_ids) != 1:
                raise HTTPException(status_code=422, detail=f"{product.name} returns must be entered one available serial at a time")
            serials = session.scalars(
                select(ProductSerial)
                .where(
                    ProductSerial.id.in_(line.serial_ids),
                    ProductSerial.product_id == product.id,
                    ProductSerial.warehouse_id == line.warehouse_id,
                    ProductSerial.status == "available",
                )
                .with_for_update()
            ).all()
            if len(serials) != len(line.serial_ids):
                raise HTTPException(status_code=409, detail=f"One or more serials for {product.name} are not available in this warehouse")
        elif line.serial_ids:
            raise HTTPException(status_code=422, detail=f"{product.name} is not serialized")
        input_lines.append(
            LineInput(
                description=item.description or product.name,
                hsn_sac=item.hsn_sac,
                quantity=line.quantity,
                unit_price=item.unit_price,
                gst_rate=item.gst_rate,
                discount_amount=(item.discount * line.quantity / item.quantity).quantize(Decimal("0.01")),
                cess_rate=item.cess_rate,
            )
        )
        products.append(product)
        serial_rows.append(serials)
        inventories.append(inventory)
    settings = session.scalar(select(CompanySettings).order_by(CompanySettings.id).limit(1))
    if settings is None or settings.state_code is None or supplier.state_code is None:
        raise HTTPException(status_code=409, detail="Company and supplier states are required for purchase return tax")
    configured_rates = set(session.scalars(select(GstRate.rate).where(GstRate.is_active.is_(True))).all())
    try:
        result = compute_invoice(
            input_lines,
            supplier.state_code,
            invoice.place_of_supply_code or settings.state_code,
            allowed_rates=configured_rates,
        )
    except GSTError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if result.grand_total <= ZERO:
        raise HTTPException(status_code=409, detail="A zero-value purchase return cannot be posted")
    note = DebitNote(
        note_number=f"DN-TMP-{uuid4().hex}",
        purchase_id=invoice.id,
        note_date=body.note_date,
        reason=body.reason,
        taxable_total=result.taxable_total,
        cgst_total=result.cgst_total,
        sgst_total=result.sgst_total,
        igst_total=result.igst_total,
        cess_total=result.cess_total,
        round_off=result.round_off,
        grand_total=result.grand_total,
        status="issued",
        created_by=user.id,
    )
    session.add(note)
    try:
        session.flush()
        note.note_number = f"DN-{note.id:08d}"
        cost_value = ZERO
        for index, (line, item, product, inventory, serials) in enumerate(
            zip(body.lines, [invoice_items[row.purchase_item_id] for row in body.lines], products, inventories, serial_rows, strict=True)
        ):
            result_line = result.lines[index]
            unit_cost = Decimal(inventory.average_unit_cost)
            line_cost = (line.quantity * unit_cost).quantize(Decimal("0.01"))
            cost_value += line_cost
            if serials:
                for serial in serials:
                    serial.status = "returned"
                    session.add(
                        DebitNoteItem(
                            debit_note_id=note.id,
                            purchase_item_id=item.id,
                            quantity=Decimal("1"),
                            taxable_value=result_line.taxable,
                            cgst=result_line.cgst,
                            sgst=result_line.sgst,
                            igst=result_line.igst,
                            cess=result_line.cess,
                            serial_id=serial.id,
                            warehouse_id=line.warehouse_id,
                            unit_cost=unit_cost,
                        )
                    )
                    session.add(
                        StockMovement(
                            product_id=product.id,
                            warehouse_id=line.warehouse_id,
                            serial_id=serial.id,
                            movement_type="purchase_return",
                            quantity_change=Decimal("-1"),
                            unit_cost=unit_cost,
                            ref_type="debit_note",
                            ref_id=note.id,
                            created_by=user.id,
                        )
                    )
            else:
                session.add(
                    DebitNoteItem(
                        debit_note_id=note.id,
                        purchase_item_id=item.id,
                        quantity=line.quantity,
                        taxable_value=result_line.taxable,
                        cgst=result_line.cgst,
                        sgst=result_line.sgst,
                        igst=result_line.igst,
                        cess=result_line.cess,
                        warehouse_id=line.warehouse_id,
                        unit_cost=unit_cost,
                    )
                )
                session.add(
                    StockMovement(
                        product_id=product.id,
                        warehouse_id=line.warehouse_id,
                        movement_type="purchase_return",
                        quantity_change=-line.quantity,
                        unit_cost=unit_cost,
                        ref_type="debit_note",
                        ref_id=note.id,
                        created_by=user.id,
                    )
                )
            inventory.quantity = Decimal(inventory.quantity) - line.quantity

        variance = result.taxable_total - cost_value
        journal_lines = [
            CoreLine("2000", result.grand_total, ZERO),
            *([CoreLine("1200", ZERO, cost_value)] if cost_value > ZERO else []),
            *[
                CoreLine(INPUT_TAX[key], ZERO, amount)
                for key, amount in (
                    ("cgst", result.cgst_total),
                    ("sgst", result.sgst_total),
                    ("igst", result.igst_total),
                    ("cess", result.cess_total),
                )
                if amount > ZERO
            ],
        ]
        if variance > ZERO:
            journal_lines.append(CoreLine("5191", ZERO, variance))
        elif variance < ZERO:
            journal_lines.append(CoreLine("5191", -variance, ZERO))
        if result.round_off > ZERO:
            journal_lines.append(CoreLine("4900", ZERO, result.round_off))
        elif result.round_off < ZERO:
            journal_lines.append(CoreLine("4900", -result.round_off, ZERO))
        core_entry = CoreJournalEntry(
            body.note_date,
            note.note_number,
            f"Purchase return {note.note_number}",
            journal_lines,
        )
        _journal(session, core_entry, user.id, "debit_note", note.id, supplier.id)
        _audit(
            session, user, request, "post_purchase_return", "debit_note", note.id,
            {"note_number": note.note_number, "purchase_id": invoice.id, "grand_total": format(note.grand_total, "f")},
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Purchase return conflicts with current stock, serial, or accounting data") from None
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {"id": note.id, "note_number": note.note_number, "status": note.status, "grand_total": format(note.grand_total, "f")}


def _purchase_outstanding(session: Session, purchase: Purchase) -> Decimal:
    paid = session.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.purchase_id == purchase.id,
            Payment.direction == "out",
            Payment.status == "recorded",
        )
    ) or ZERO
    credits = session.scalar(
        select(func.coalesce(func.sum(DebitNote.grand_total), 0)).where(
            DebitNote.purchase_id == purchase.id,
            DebitNote.status == "issued",
        )
    ) or ZERO
    return max(Decimal(purchase.grand_total) - Decimal(paid) - Decimal(credits), ZERO)


@router.post("/supplier-payments", status_code=status.HTTP_201_CREATED)
def create_supplier_payment(
    body: SupplierPaymentWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("payments:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    supplier = session.scalar(select(Supplier).where(Supplier.id == body.supplier_id, Supplier.deleted_at.is_(None)).with_for_update())
    method = session.get(PaymentMethod, body.method_id)
    if supplier is None or not supplier.is_active:
        raise HTTPException(status_code=404, detail="Active supplier not found")
    if method is None or not method.is_active:
        raise HTTPException(status_code=422, detail="Active payment method not found")
    allocations: list[tuple[Purchase, Decimal]] = []
    if body.allocations is not None:
        if len({item.purchase_id for item in body.allocations}) != len(body.allocations):
            raise HTTPException(status_code=422, detail="A purchase invoice may be allocated only once")
        remaining = body.amount
        for allocation in body.allocations:
            purchase = session.scalar(
                select(Purchase).where(Purchase.id == allocation.purchase_id).with_for_update()
            )
            if purchase is None or purchase.doc_type != "purchase_invoice" or purchase.status != "posted" or purchase.supplier_id != supplier.id:
                raise HTTPException(status_code=422, detail="Payment allocation must reference this supplier's posted purchase invoice")
            if allocation.amount > _purchase_outstanding(session, purchase):
                raise HTTPException(status_code=409, detail=f"Allocation exceeds outstanding balance for purchase invoice {purchase.number}")
            allocations.append((purchase, allocation.amount))
            remaining -= allocation.amount
        if remaining != 0:
            raise HTTPException(status_code=422, detail="Supplier payments must be fully allocated to posted purchase invoices")
    else:
        open_invoices = session.scalars(
            select(Purchase).where(
                Purchase.doc_type == "purchase_invoice",
                Purchase.status == "posted",
                Purchase.supplier_id == supplier.id,
            ).order_by(Purchase.doc_date, Purchase.id).with_for_update()
        ).all()
        remaining = body.amount
        for purchase in open_invoices:
            amount = min(remaining, _purchase_outstanding(session, purchase))
            if amount > 0:
                allocations.append((purchase, amount))
                remaining -= amount
            if remaining == 0:
                break
        if remaining != 0:
            raise HTTPException(status_code=409, detail="Payment exceeds this supplier's outstanding posted invoices")
    raw_connection = session.connection().connection.driver_connection
    with raw_connection.cursor() as cursor:
        sequence = next_sequence(cursor, "supplier-payment", "format")
    payment = Payment(
        payment_number=f"SPAY-{sequence:08d}",
        direction="out",
        supplier_id=supplier.id,
        method_id=method.id,
        amount=body.amount,
        payment_date=body.payment_date,
        reference_no=body.reference_no,
        notes=body.notes,
        is_advance=False,
        created_by=user.id,
    )
    session.add(payment)
    try:
        session.flush()
        for purchase, amount in allocations:
            session.add(PaymentAllocation(payment_id=payment.id, purchase_id=purchase.id, amount=amount))
        _journal(
            session,
            post_supplier_payment(body.payment_date, payment.payment_number, body.amount, method.ledger_kind),
            user.id,
            "supplier_payment",
            payment.id,
            supplier.id,
        )
        _audit(
            session, user, request, "record_supplier_payment", "payment", payment.id,
            {"payment_number": payment.payment_number, "supplier_id": supplier.id, "amount": format(body.amount, "f")},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Supplier payment conflicts with current records") from None
    return {
        "id": payment.id,
        "payment_number": payment.payment_number,
        "supplier_id": supplier.id,
        "direction": payment.direction,
        "amount": format(payment.amount, "f"),
        "payment_date": payment.payment_date.isoformat(),
        "allocations": [{"purchase_id": purchase.id, "amount": format(amount, "f")} for purchase, amount in allocations],
    }


@router.get("/suppliers/{supplier_id}/ledger")
def supplier_ledger(
    supplier_id: int,
    _: Annotated[User, Depends(require_permissions("suppliers:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None or supplier.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    rows = session.execute(
        select(JournalEntry, JournalEntryLine)
        .join(JournalEntryLine, JournalEntryLine.entry_id == JournalEntry.id)
        .where(JournalEntryLine.supplier_id == supplier.id)
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalEntryLine.id)
    ).all()
    balance = Decimal(supplier.opening_balance)
    items: list[dict[str, object]] = []
    for entry, line in rows:
        balance += Decimal(line.credit) - Decimal(line.debit)
        items.append({
            "date": entry.entry_date.isoformat(),
            "reference": entry.reference,
            "narration": entry.narration,
            "debit": format(line.debit, "f"),
            "credit": format(line.credit, "f"),
            "balance": format(balance, "f"),
        })
    return {"supplier_id": supplier.id, "opening_balance": format(supplier.opening_balance, "f"), "items": items, "closing_balance": format(balance, "f")}


@router.get("/suppliers/{supplier_id}/outstanding-invoices")
def supplier_outstanding_invoices(
    supplier_id: int,
    _: Annotated[User, Depends(require_permissions("suppliers:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None or supplier.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    invoices = session.scalars(
        select(Purchase)
        .where(
            Purchase.supplier_id == supplier.id,
            Purchase.doc_type == "purchase_invoice",
            Purchase.status == "posted",
        )
        .order_by(Purchase.doc_date, Purchase.id)
    ).all()
    items = [
        {
            "id": invoice.id,
            "number": invoice.number,
            "doc_date": invoice.doc_date.isoformat(),
            "grand_total": format(invoice.grand_total, "f"),
            "outstanding": format(outstanding, "f"),
        }
        for invoice in invoices
        if (outstanding := _purchase_outstanding(session, invoice)) > ZERO
    ]
    return {"items": items, "total": len(items)}


@router.get("/reports/purchase-register")
def purchase_register(
    _: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="from must be on or before to")
    filters = [Purchase.doc_type == "purchase_invoice", Purchase.status == "posted"]
    if date_from:
        filters.append(Purchase.doc_date >= date_from)
    if date_to:
        filters.append(Purchase.doc_date <= date_to)
    rows = session.execute(
        select(Purchase, Supplier.name).join(Supplier, Supplier.id == Purchase.supplier_id)
        .where(*filters).order_by(Purchase.doc_date, Purchase.id)
    ).all()
    return {
        "items": [
            {"number": item.number, "supplier_name": name, "doc_date": item.doc_date.isoformat(),
             "taxable_total": format(item.taxable_total, "f"), "cgst": format(item.cgst_total, "f"),
             "sgst": format(item.sgst_total, "f"), "igst": format(item.igst_total, "f"),
             "cess": format(item.cess_total, "f"), "grand_total": format(item.grand_total, "f")}
            for item, name in rows
        ],
        "total": len(rows),
    }


@router.get("/reports/input-gst")
def input_gst_report(
    _: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> dict[str, object]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="from must be on or before to")
    filters = [Purchase.doc_type == "purchase_invoice", Purchase.status == "posted"]
    if date_from:
        filters.append(Purchase.doc_date >= date_from)
    if date_to:
        filters.append(Purchase.doc_date <= date_to)
    totals = session.execute(
        select(
            func.coalesce(func.sum(Purchase.taxable_total), 0),
            func.coalesce(func.sum(Purchase.cgst_total), 0),
            func.coalesce(func.sum(Purchase.sgst_total), 0),
            func.coalesce(func.sum(Purchase.igst_total), 0),
            func.coalesce(func.sum(Purchase.cess_total), 0),
        ).where(*filters)
    ).one()
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
        "taxable_total": format(totals[0], "f"),
        "cgst": format(totals[1], "f"),
        "sgst": format(totals[2], "f"),
        "igst": format(totals[3], "f"),
        "cess": format(totals[4], "f"),
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


@router.get("/reports/stock-valuation")
def stock_valuation_report(
    _: Annotated[User, Depends(require_permissions("reports:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    rows = session.execute(
        select(Inventory, Product.sku, Product.name, Warehouse.name)
        .join(Product, Product.id == Inventory.product_id)
        .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
        .where(Inventory.quantity > 0)
        .order_by(Product.name, Warehouse.name)
    ).all()
    return {
        "items": [
            {
                "product_id": inventory.product_id,
                "sku": sku,
                "product_name": product_name,
                "warehouse_id": inventory.warehouse_id,
                "warehouse_name": warehouse_name,
                "quantity": format(inventory.quantity, "f"),
                "average_unit_cost": format(inventory.average_unit_cost, "f") if inventory.average_unit_cost is not None else None,
                "value": format((inventory.quantity * inventory.average_unit_cost).quantize(Decimal("0.01")), "f")
                if inventory.average_unit_cost is not None else None,
            }
            for inventory, sku, product_name, warehouse_name in rows
        ],
        "unvalued_items": sum(1 for inventory, _, _, _ in rows if inventory.average_unit_cost is None),
    }
