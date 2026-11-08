from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import Inventory, Product, User, Warehouse
from app.services.inventory_valuation import record_opening_valuation, reverse_opening_valuation

router = APIRouter(tags=["inventory valuation"])


class OpeningValuationLine(BaseModel):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    unit_cost: Decimal = Field(gt=0, max_digits=14, decimal_places=4)


class OpeningValuationBatch(BaseModel):
    lines: list[OpeningValuationLine] = Field(min_length=1, max_length=500)


def _require_admin(user: User) -> None:
    if user.role.name not in {"Admin", "Super Admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role is required")


@router.get("/inventory/opening-valuations")
def list_unvalued_inventory(
    user: Annotated[User, Depends(require_permissions("inventory:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    _require_admin(user)
    rows = session.execute(
        select(Inventory, Product.name, Product.sku, Warehouse.name)
        .join(Product, Product.id == Inventory.product_id)
        .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
        .where(Inventory.quantity > 0, Inventory.average_unit_cost.is_(None))
        .order_by(Product.name, Warehouse.name)
    ).all()
    return {
        "items": [
            {
                "product_id": inventory.product_id,
                "product_name": product_name,
                "sku": sku,
                "warehouse_id": inventory.warehouse_id,
                "warehouse_name": warehouse_name,
                "quantity": format(inventory.quantity, "f"),
            }
            for inventory, product_name, sku, warehouse_name in rows
        ]
    }


@router.post("/inventory/opening-valuations", status_code=status.HTTP_201_CREATED)
def create_opening_valuation(
    body: OpeningValuationBatch,
    request: Request,
    user: Annotated[User, Depends(require_permissions("inventory:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    _require_admin(user)
    try:
        result = record_opening_valuation(
            session,
            user,
            [(line.product_id, line.warehouse_id, line.unit_cost) for line in body.lines],
            request.client.host if request.client else None,
        )
        session.commit()
        return result
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Opening valuation conflicts with current inventory or accounting data") from None


@router.post("/inventory/opening-valuations/{journal_entry_id}/reverse")
def reverse_valuation(
    journal_entry_id: int,
    request: Request,
    user: Annotated[User, Depends(require_permissions("inventory:manage"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    _require_admin(user)
    try:
        result = reverse_opening_valuation(
            session,
            user,
            journal_entry_id,
            request.client.host if request.client else None,
        )
        session.commit()
        return result
    except LookupError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from None
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Opening valuation reversal conflicts with current accounting data") from None
