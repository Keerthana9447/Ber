from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import (
    AuditLog,
    Branch,
    GstRate,
    HsnSacCode,
    Inventory,
    Product,
    ProductCategory,
    StockMovement,
    Unit,
    User,
    Warehouse,
    ProductSerial,
)

router = APIRouter(tags=["products", "inventory"])


def _require_internal_product_access(
    user: Annotated[User, Depends(require_permissions("products:read"))],
) -> User:
    if user.role.name == "Dealer / Distributor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Use the dealer catalog for product access",
        )
    return user


def _trim(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("must not be blank")
    return normalized


class ProductWrite(BaseModel):
    sku: str = Field(min_length=1, max_length=128)
    code: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    category_id: int = Field(gt=0)
    brand: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    description: str | None = None
    hsn_sac: str = Field(min_length=4, max_length=8, pattern=r"^\d{4,8}$")
    unit: str = Field(min_length=1, max_length=32)
    purchase_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    selling_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    gst_rate: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    cess_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    min_stock: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=3)
    is_serialized: bool = False
    is_batch_tracked: bool = False
    is_service: bool = False
    is_active: bool = True

    @field_validator("sku", "name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _trim(value)

    @field_validator("code", "brand", "model", "description")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("hsn_sac", "unit")
    @classmethod
    def normalize_codes(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_tracking(self) -> ProductWrite:
        if self.is_service and (self.is_serialized or self.is_batch_tracked):
            raise ValueError("Services cannot be serialized or batch tracked")
        return self


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    code: str | None
    name: str
    category_id: int
    brand: str | None
    model: str | None
    description: str | None
    hsn_sac: str
    unit: str
    purchase_price: Decimal
    selling_price: Decimal
    gst_rate: Decimal
    cess_rate: Decimal
    min_stock: Decimal
    is_serialized: bool
    is_batch_tracked: bool
    is_service: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("purchase_price", "selling_price", "gst_rate", "cess_rate", "min_stock")
    def serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class ProductPage(BaseModel):
    items: list[ProductResponse]
    total: int


class CategoryWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = Field(default=None, gt=0)
    is_serialized: bool = False
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _trim(value)


class HsnWrite(BaseModel):
    code: str = Field(pattern=r"^\d{4,8}$")
    kind: Literal["HSN", "SAC"]
    description: str = Field(min_length=1, max_length=1000)
    default_gst_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    default_cess_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    is_active: bool = True

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return _trim(value)


class GstRateWrite(BaseModel):
    rate: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class WarehouseWrite(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    branch_id: int | None = Field(default=None, gt=0)
    is_active: bool = True

    @field_validator("code", "name")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _trim(value)


class StockAdjustment(BaseModel):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    quantity_delta: Decimal = Field(max_digits=14, decimal_places=3)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("quantity_delta")
    @classmethod
    def nonzero_delta(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value == 0:
            raise ValueError("quantity_delta must be a finite non-zero number")
        return value

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


def _snapshot(product: Product) -> dict[str, object]:
    return {
        "sku": product.sku,
        "code": product.code,
        "name": product.name,
        "category_id": product.category_id,
        "brand": product.brand,
        "model": product.model,
        "description": product.description,
        "hsn_sac": product.hsn_sac,
        "unit": product.unit,
        "purchase_price": str(product.purchase_price),
        "selling_price": str(product.selling_price),
        "gst_rate": str(product.gst_rate),
        "cess_rate": str(product.cess_rate),
        "min_stock": str(product.min_stock),
        "is_serialized": product.is_serialized,
        "is_batch_tracked": product.is_batch_tracked,
        "is_service": product.is_service,
        "is_active": product.is_active,
    }


def _audit(
    session: Session,
    user: User,
    request: Request,
    action: str,
    entity_type: str,
    entity_id: int,
    old_values: dict[str, object] | None,
    new_values: dict[str, object] | None,
) -> None:
    session.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=request.client.host if request.client else None,
        )
    )


def _commit_or_conflict(session: Session, detail: str) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from None


def _active_product(session: Session, product_id: int) -> Product | None:
    return session.scalar(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )


def _validate_product_references(session: Session, values: ProductWrite) -> None:
    category = session.get(ProductCategory, values.category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown or inactive category_id")
    hsn = session.get(HsnSacCode, values.hsn_sac)
    if hsn is None or not hsn.is_active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown or inactive hsn_sac")
    expected_kind = "SAC" if values.is_service else "HSN"
    if hsn.kind != expected_kind:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{expected_kind} code is required for this product type",
        )
    if hsn.default_gst_rate is not None and values.gst_rate != hsn.default_gst_rate:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="gst_rate must match the configured HSN/SAC default rate",
        )
    if values.cess_rate != hsn.default_cess_rate:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="cess_rate must match the configured HSN/SAC default rate",
        )
    if session.get(Unit, values.unit) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown unit")
    active_rate = session.scalar(
        select(GstRate.id).where(GstRate.rate == values.gst_rate, GstRate.is_active.is_(True))
    )
    if active_rate is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="GST rate is not configured as active",
        )


@router.get("/product-options")
def get_product_options(
    user: Annotated[User, Depends(require_permissions("products:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, list[dict[str, object]]]:
    categories = session.scalars(
        select(ProductCategory).where(ProductCategory.is_active.is_(True)).order_by(ProductCategory.name)
    ).all()
    hsn_codes = session.scalars(
        select(HsnSacCode).where(HsnSacCode.is_active.is_(True)).order_by(HsnSacCode.code)
    ).all()
    units = session.scalars(select(Unit).order_by(Unit.code)).all()
    gst_rates = session.scalars(
        select(GstRate).where(GstRate.is_active.is_(True)).order_by(GstRate.rate)
    ).all()
    warehouse_filters = [Warehouse.is_active.is_(True)]
    if user.role.name == "Branch Manager":
        warehouse_filters.append(
            Warehouse.branch_id == user.branch_id if user.branch_id is not None else Warehouse.id == -1
        )
    warehouses = session.scalars(
        select(Warehouse).where(*warehouse_filters).order_by(Warehouse.name)
    ).all()
    permissions = {permission.code for permission in user.role.permissions}
    if "inventory:read" not in permissions:
        warehouses = []
    return {
        "categories": [
            {"id": row.id, "name": row.name, "is_serialized": row.is_serialized}
            for row in categories
        ],
        "hsn_sac_codes": [
            {
                "code": row.code,
                "kind": row.kind,
                "description": row.description,
                "default_gst_rate": format(row.default_gst_rate, "f") if row.default_gst_rate is not None else None,
                "default_cess_rate": format(row.default_cess_rate, "f"),
            }
            for row in hsn_codes
        ],
        "units": [{"code": row.code, "name": row.name} for row in units],
        "gst_rates": [
            {"id": row.id, "rate": format(row.rate, "f"), "description": row.description}
            for row in gst_rates
        ],
        "warehouses": [
            {"id": row.id, "code": row.code, "name": row.name, "branch_id": row.branch_id}
            for row in warehouses
        ],
    }


@router.post("/product-categories", status_code=status.HTTP_201_CREATED)
def create_product_category(
    body: CategoryWrite,
    user: Annotated[User, Depends(require_permissions("products:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    del user
    if body.parent_id is not None:
        parent = session.get(ProductCategory, body.parent_id)
        if parent is None or not parent.is_active:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown or inactive parent_id")
    category = ProductCategory(**body.model_dump())
    session.add(category)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product category already exists") from None
    return {"id": category.id, "name": category.name, "is_serialized": category.is_serialized, "is_active": category.is_active}


@router.post("/hsn-sac-codes", status_code=status.HTTP_201_CREATED)
def create_hsn_sac_code(
    body: HsnWrite,
    user: Annotated[User, Depends(require_permissions("settings:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    del user
    if body.default_gst_rate is not None:
        configured_rate = session.scalar(
            select(GstRate.id).where(GstRate.rate == body.default_gst_rate, GstRate.is_active.is_(True))
        )
        if configured_rate is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="default_gst_rate must be an active configured GST rate",
            )
    code = HsnSacCode(**body.model_dump())
    session.add(code)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HSN/SAC code already exists") from None
    return {
        "code": code.code,
        "kind": code.kind,
        "description": code.description,
        "default_gst_rate": format(code.default_gst_rate, "f") if code.default_gst_rate is not None else None,
        "default_cess_rate": format(code.default_cess_rate, "f"),
        "is_active": code.is_active,
    }


@router.post("/gst-rates", status_code=status.HTTP_201_CREATED)
def create_gst_rate(
    body: GstRateWrite,
    user: Annotated[User, Depends(require_permissions("settings:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    del user
    rate = GstRate(**body.model_dump())
    session.add(rate)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="GST rate already exists") from None
    return {"id": rate.id, "rate": format(rate.rate, "f"), "description": rate.description, "is_active": rate.is_active}


@router.post("/warehouses", status_code=status.HTTP_201_CREATED)
def create_warehouse(
    body: WarehouseWrite,
    user: Annotated[User, Depends(require_permissions("inventory:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    del user
    if body.branch_id is not None and session.get(Branch, body.branch_id) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown branch_id")
    warehouse = Warehouse(**body.model_dump())
    session.add(warehouse)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Warehouse code already exists") from None
    return {
        "id": warehouse.id,
        "code": warehouse.code,
        "name": warehouse.name,
        "branch_id": warehouse.branch_id,
        "is_active": warehouse.is_active,
    }


@router.get("/products", response_model=ProductPage)
def list_products(
    user: Annotated[User, Depends(_require_internal_product_access)],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
) -> ProductPage:
    del user
    filters = [Product.deleted_at.is_(None)]
    search = q.strip()
    if search:
        pattern = f"%{search}%"
        filters.append(
            or_(
                Product.sku.ilike(pattern),
                Product.name.ilike(pattern),
                Product.brand.ilike(pattern),
                Product.hsn_sac.ilike(pattern),
            )
        )
    total = session.scalar(select(func.count(Product.id)).where(*filters)) or 0
    rows = session.scalars(
        select(Product)
        .where(*filters)
        .order_by(Product.name, Product.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ProductPage(items=[ProductResponse.model_validate(row) for row in rows], total=total)


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    body: ProductWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("products:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> ProductResponse:
    _validate_product_references(session, body)
    product = Product(**body.model_dump())
    session.add(product)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product SKU conflicts with an existing product") from None
    _audit(session, user, request, "create", "product", product.id, None, _snapshot(product))
    _commit_or_conflict(session, "Product conflicts with an existing SKU or related record")
    session.refresh(product)
    return ProductResponse.model_validate(product)


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: int,
    user: Annotated[User, Depends(_require_internal_product_access)],
    session: Annotated[Session, Depends(get_db)],
) -> ProductResponse:
    del user
    product = _active_product(session, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return ProductResponse.model_validate(product)


@router.get("/products/{product_id}/serials")
def list_available_serials(
    product_id: int,
    user: Annotated[User, Depends(_require_internal_product_access)],
    session: Annotated[Session, Depends(get_db)],
    q: str = Query(default="", max_length=200),
) -> list[dict[str, object]]:
    del user
    product = _active_product(session, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    if not product.is_serialized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Product is not serialized")
    filters = [ProductSerial.product_id == product_id, ProductSerial.status == "available"]
    search = q.strip()
    if search:
        pattern = f"%{search}%"
        filters.append(
            or_(
                ProductSerial.chassis_no.ilike(pattern),
                ProductSerial.vin.ilike(pattern),
                ProductSerial.motor_no.ilike(pattern),
                ProductSerial.battery_serial.ilike(pattern),
            )
        )
    serials = session.scalars(
        select(ProductSerial).where(*filters).order_by(ProductSerial.id).limit(100)
    ).all()
    return [
        {
            "id": serial.id,
            "chassis_no": serial.chassis_no,
            "vin": serial.vin,
            "motor_no": serial.motor_no,
            "battery_serial": serial.battery_serial,
            "warehouse_id": serial.warehouse_id,
            "status": serial.status,
        }
        for serial in serials
    ]


@router.put("/products/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    body: ProductWrite,
    request: Request,
    user: Annotated[User, Depends(require_permissions("products:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> ProductResponse:
    product = session.scalar(
        select(Product)
        .where(Product.id == product_id, Product.deleted_at.is_(None))
        .with_for_update()
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    _validate_product_references(session, body)
    old_values = _snapshot(product)
    for field, value in body.model_dump().items():
        setattr(product, field, value)
    product.updated_at = datetime.now(timezone.utc)
    _audit(session, user, request, "update", "product", product.id, old_values, _snapshot(product))
    _commit_or_conflict(session, "Product conflicts with an existing SKU or related record")
    session.refresh(product)
    return ProductResponse.model_validate(product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("products:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> None:
    product = session.scalar(
        select(Product)
        .where(Product.id == product_id, Product.deleted_at.is_(None))
        .with_for_update()
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    old_values = _snapshot(product)
    now = datetime.now(timezone.utc)
    product.deleted_at = now
    product.updated_at = now
    product.is_active = False
    _audit(session, user, request, "delete", "product", product.id, old_values, _snapshot(product))
    _commit_or_conflict(session, "Product is referenced by existing transactions and could not be deleted")


@router.get("/inventory")
def list_inventory(
    user: Annotated[User, Depends(require_permissions("inventory:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    warehouse_id: int | None = Query(default=None, gt=0),
) -> dict[str, object]:
    filters = [Product.deleted_at.is_(None), Product.is_service.is_(False), Warehouse.is_active.is_(True)]
    if user.role.name == "Branch Manager":
        if user.branch_id is None:
            filters.append(Inventory.warehouse_id == -1)
        else:
            filters.append(Warehouse.branch_id == user.branch_id)
    if warehouse_id is not None:
        filters.append(Inventory.warehouse_id == warehouse_id)
    search = q.strip()
    if search:
        pattern = f"%{search}%"
        filters.append(or_(Product.sku.ilike(pattern), Product.name.ilike(pattern)))
    base = (
        select(Inventory, Product, Warehouse)
        .join(Product, Product.id == Inventory.product_id)
        .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
        .where(*filters)
    )
    total = session.scalar(
        select(func.count()).select_from(
            select(Inventory.product_id, Inventory.warehouse_id)
            .join(Product, Product.id == Inventory.product_id)
            .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
            .where(*filters)
            .subquery()
        )
    ) or 0
    rows = session.execute(
        base.order_by(Product.name, Warehouse.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        {
            "product_id": inventory.product_id,
            "sku": product.sku,
            "product_name": product.name,
            "warehouse_id": warehouse.id,
            "warehouse_name": warehouse.name,
            "quantity": format(inventory.quantity, "f"),
            "min_stock": format(product.min_stock, "f"),
            "low_stock": inventory.quantity <= product.min_stock,
        }
        for inventory, product, warehouse in rows
    ]
    return {"items": items, "total": total}


@router.post("/inventory/adjustments", status_code=status.HTTP_201_CREATED)
def adjust_inventory(
    body: StockAdjustment,
    request: Request,
    user: Annotated[User, Depends(require_permissions("inventory:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    product = session.scalar(
        select(Product)
        .where(Product.id == body.product_id, Product.deleted_at.is_(None), Product.is_active.is_(True))
        .with_for_update()
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active product not found")
    if product.is_service:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Services cannot have stock")
    if product.is_serialized or product.is_batch_tracked:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Serialized and batch-tracked stock must be posted with serial or batch details",
        )
    warehouse = session.scalar(
        select(Warehouse)
        .where(Warehouse.id == body.warehouse_id, Warehouse.is_active.is_(True))
        .with_for_update()
    )
    if warehouse is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active warehouse not found")
    if user.role.name == "Branch Manager" and warehouse.branch_id != user.branch_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active warehouse not found")

    session.execute(
        insert(Inventory)
        .values(product_id=product.id, warehouse_id=warehouse.id, quantity=Decimal("0"))
        .on_conflict_do_nothing(index_elements=[Inventory.product_id, Inventory.warehouse_id])
    )
    inventory = session.scalar(
        select(Inventory)
        .where(Inventory.product_id == product.id, Inventory.warehouse_id == warehouse.id)
        .with_for_update()
    )
    if inventory is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Inventory row could not be locked")
    new_quantity = inventory.quantity + body.quantity_delta
    if new_quantity < 0:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Adjustment would make stock negative")

    inventory.quantity = new_quantity
    movement = StockMovement(
        product_id=product.id,
        warehouse_id=warehouse.id,
        movement_type="adjustment",
        quantity_change=body.quantity_delta,
        unit_cost=body.unit_cost,
        ref_type="manual_adjustment",
        notes=body.notes,
        created_by=user.id,
    )
    session.add(movement)
    session.flush()
    _audit(
        session,
        user,
        request,
        "adjust",
        "inventory",
        movement.id,
        {"quantity": format(new_quantity - body.quantity_delta, "f")},
        {"quantity": format(new_quantity, "f"), "quantity_delta": format(body.quantity_delta, "f")},
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Inventory adjustment conflicts with current stock") from None
    return {
        "movement_id": movement.id,
        "product_id": product.id,
        "warehouse_id": warehouse.id,
        "quantity": format(new_quantity, "f"),
        "quantity_delta": format(body.quantity_delta, "f"),
    }
