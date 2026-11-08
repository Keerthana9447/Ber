from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permissions
from app.db.session import get_db
from app.models import Customer, Dealer, DealerPrice, Product, User

router = APIRouter(prefix="/dealer/portal", tags=["dealer portal"])


def _dealer(user: User, session: Session) -> Dealer:
    if user.role.name != "Dealer / Distributor":
        raise HTTPException(status_code=403, detail="Dealer portal access is restricted to dealer accounts")
    if user.dealer_id is None:
        raise HTTPException(status_code=403, detail="This user is not linked to a dealer")
    dealer = session.scalar(
        select(Dealer).where(Dealer.id == user.dealer_id, Dealer.status == "active")
    )
    if dealer is None:
        raise HTTPException(status_code=403, detail="Dealer account is inactive or unavailable")
    return dealer


@router.get("/profile")
def dealer_profile(
    user: Annotated[User, Depends(require_permissions("auth:self:read"))],
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    dealer = _dealer(user, session)
    customer = session.get(Customer, dealer.customer_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=403, detail="Dealer profile is unavailable")
    return {
        "dealer_code": dealer.dealer_code,
        "name": customer.name,
        "territory": dealer.territory,
        "status": dealer.status,
    }


@router.get("/products")
def dealer_products(
    user: Annotated[User, Depends(require_permissions("products:read"))],
    session: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
) -> dict[str, object]:
    dealer = _dealer(user, session)
    filters = [Product.is_active.is_(True), Product.deleted_at.is_(None)]
    if q.strip():
        term = f"%{q.strip()}%"
        filters.append((Product.name.ilike(term)) | (Product.sku.ilike(term)))
    total = session.scalar(select(func.count(Product.id)).where(*filters)) or 0
    rows = session.execute(
        select(Product, DealerPrice.price)
        .outerjoin(
            DealerPrice,
            (DealerPrice.product_id == Product.id) & (DealerPrice.dealer_id == dealer.id),
        )
        .where(*filters)
        .order_by(Product.name, Product.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "product_id": product.id,
                "sku": product.sku,
                "name": product.name,
                "hsn_sac": product.hsn_sac,
                "gst_rate": format(product.gst_rate, "f"),
                "unit": product.unit,
                "price": format(dealer_price if dealer_price is not None else product.selling_price, "f"),
                "has_dealer_price": dealer_price is not None,
            }
            for product, dealer_price in rows
        ],
        "total": total,
    }
