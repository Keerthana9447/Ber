from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from berev_core.accounting import DEFAULT_ACCOUNTS
from berev_core.gst import STATE_CODES
from app.models import AccountModel, Permission, PaymentMethod, Role, State, Unit

logger = logging.getLogger("berev.seed")

UNITS = (
    ("PCS", "Pieces"),
    ("NOS", "Numbers"),
    ("SET", "Set"),
    ("UNIT", "Unit"),
    ("KG", "Kilogram"),
    ("LTR", "Litre"),
    ("MTR", "Metre"),
    ("HOUR", "Hour"),
    ("JOB", "Job"),
)

PAYMENT_METHODS = (
    ("cash", "Cash", "cash"),
    ("upi", "UPI", "bank"),
    ("bank_transfer", "Bank transfer", "bank"),
    ("neft", "NEFT", "bank"),
    ("rtgs", "RTGS", "bank"),
    ("imps", "IMPS", "bank"),
    ("cheque", "Cheque", "bank"),
    ("card", "Card", "bank"),
    ("other", "Other", "bank"),
)

PERMISSION_DESCRIPTIONS = {
    "auth:self:read": "Read the current user's own profile",
    "auth:self:logout": "Revoke the current user's refresh token",
    "users:read": "View user accounts",
    "users:create": "Create user accounts",
    "users:update": "Update and deactivate user accounts",
    "roles:read": "View roles and permissions",
    "roles:update": "Manage roles and permission assignments",
    "customers:read": "View customer records",
    "customers:create": "Create customer records",
    "customers:update": "Update customer records",
    "suppliers:read": "View supplier records",
    "suppliers:create": "Create supplier records",
    "suppliers:update": "Update supplier records",
    "dealers:read": "View dealer records",
    "dealers:manage": "Manage dealer records",
    "products:read": "View product records",
    "products:manage": "Manage product records",
    "inventory:read": "View inventory records",
    "inventory:manage": "Post inventory movements",
    "sales:read": "View sales records",
    "sales:create": "Create sales records",
    "sales:approve": "Approve and issue sales documents",
    "purchases:read": "View purchase records",
    "purchases:manage": "Manage purchase workflows",
    "payments:read": "View payment records",
    "payments:manage": "Record and allocate payments",
    "expenses:read": "View expense records",
    "expenses:manage": "Manage expense workflows",
    "accounting:read": "View accounting records",
    "accounting:post": "Post accounting entries",
    "reports:read": "View reports",
    "reports:export": "Export reports",
    "settings:read": "View company settings",
    "settings:manage": "Manage company settings",
}

ALL_PERMISSIONS = tuple(PERMISSION_DESCRIPTIONS)
ROLE_PERMISSIONS = {
    "Super Admin": ALL_PERMISSIONS,
    "Admin": ALL_PERMISSIONS,
    "Accountant": (
        "auth:self:read", "auth:self:logout", "customers:read", "suppliers:read",
        "sales:read", "purchases:read", "payments:read", "payments:manage",
        "expenses:read", "expenses:manage", "accounting:read", "accounting:post",
        "reports:read", "reports:export",
    ),
    "Sales Manager": (
        "auth:self:read", "auth:self:logout", "customers:read", "customers:create",
        "customers:update", "dealers:read", "products:read", "inventory:read",
        "sales:read", "sales:create", "sales:approve", "payments:read",
        "reports:read", "reports:export",
    ),
    "Sales Executive": (
        "auth:self:read", "auth:self:logout", "customers:read", "customers:create",
        "products:read", "inventory:read", "sales:read", "sales:create",
        "payments:read",
    ),
    "Inventory Manager": (
        "auth:self:read", "auth:self:logout", "products:read", "products:manage",
        "inventory:read", "inventory:manage", "purchases:read",
    ),
    "Purchase Manager": (
        "auth:self:read", "auth:self:logout", "suppliers:read", "suppliers:create",
        "suppliers:update", "products:read", "inventory:read", "purchases:read",
        "purchases:manage", "payments:read",
    ),
    "Branch Manager": (
        "auth:self:read", "auth:self:logout", "customers:read", "customers:create",
        "customers:update", "suppliers:read", "dealers:read", "products:read",
        "inventory:read", "sales:read", "sales:create", "sales:approve",
        "purchases:read", "payments:read", "expenses:read", "expenses:manage",
        "reports:read",
    ),
    "Dealer / Distributor": (
        "auth:self:read", "auth:self:logout", "products:read", "sales:read",
        "sales:create", "payments:read",
    ),
}


def _get_or_add(session: Session, model: type, key: str, values: dict):
    instance = session.get(model, key)
    if instance is None:
        instance = model(**values)
        session.add(instance)
    return instance


def seed_reference_data(session: Session) -> None:
    for code, name in STATE_CODES.items():
        _get_or_add(session, State, code, {"code": code, "name": name})

    for code, name in UNITS:
        _get_or_add(session, Unit, code, {"code": code, "name": name})

    for code, name, ledger_kind in PAYMENT_METHODS:
        method = session.scalar(select(PaymentMethod).where(PaymentMethod.code == code))
        if method is None:
            session.add(PaymentMethod(code=code, name=name, ledger_kind=ledger_kind))

    for account in DEFAULT_ACCOUNTS:
        _get_or_add(
            session,
            AccountModel,
            account.code,
            {"code": account.code, "name": account.name, "type": account.type},
        )

    permission_rows: dict[str, Permission] = {}
    for code, description in PERMISSION_DESCRIPTIONS.items():
        permission = session.scalar(select(Permission).where(Permission.code == code))
        if permission is None:
            permission = Permission(code=code, description=description)
            session.add(permission)
        permission_rows[code] = permission
    session.flush()

    for name, codes in ROLE_PERMISSIONS.items():
        role = session.scalar(select(Role).where(Role.name == name))
        if role is None:
            role = Role(name=name)
            session.add(role)
        assigned = {permission.code for permission in role.permissions}
        role.permissions.extend(
            permission_rows[code]
            for code in codes
            if code not in assigned
        )
    session.flush()
    logger.info(
        "reference_data_seeded",
        extra={
            "states": len(STATE_CODES),
            "units": len(UNITS),
            "payment_methods": len(PAYMENT_METHODS),
            "accounts": len(DEFAULT_ACCOUNTS),
            "roles": len(ROLE_PERMISSIONS),
            "permissions": len(PERMISSION_DESCRIPTIONS),
        },
    )


def main() -> None:
    from app.config import get_settings
    from app.db.session import create_database_engine, create_session_factory

    engine = create_database_engine(get_settings())
    try:
        factory = create_session_factory(engine)
        with factory.begin() as session:
            seed_reference_data(session)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
