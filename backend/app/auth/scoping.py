from __future__ import annotations

from sqlalchemy import false, select, true
from sqlalchemy.sql.elements import ColumnElement

from app.models import Dealer, User


def dealer_customer_scope(user: User, customer_id_column: ColumnElement) -> ColumnElement[bool]:
    if user.role.name != "Dealer / Distributor":
        return true()
    if user.dealer_id is None:
        return false()
    dealer_customer_id = (
        select(Dealer.customer_id)
        .where(Dealer.id == user.dealer_id, Dealer.status == "active")
        .scalar_subquery()
    )
    return customer_id_column == dealer_customer_id
