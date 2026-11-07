from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select
from sqlalchemy import text

from app.auth.security import hash_password
from app.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.models import Role, User
from app.seed import seed_reference_data


def create_first_admin() -> None:
    email = input("Admin email: ").strip().casefold()
    full_name = input("Admin full name: ").strip()
    password = getpass.getpass("Admin password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm admin password: ")
    if not email or "@" not in email:
        raise SystemExit("A valid email address is required.")
    if not full_name:
        raise SystemExit("A full name is required.")
    if len(password) < 12:
        raise SystemExit("The admin password must contain at least 12 characters.")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")

    engine = create_database_engine(get_settings())
    try:
        factory = create_session_factory(engine)
        with factory.begin() as session:
            seed_reference_data(session)
            super_admin = session.scalar(
                select(Role).where(Role.name == "Super Admin").with_for_update()
            )
            if super_admin is None:
                raise RuntimeError("The Super Admin role was not seeded.")
            if session.scalar(select(User.id).where(User.role_id == super_admin.id).limit(1)):
                raise RuntimeError("A Super Admin already exists; refusing to create another first admin.")
            session.add(
                User(
                    email=email,
                    full_name=full_name,
                    password_hash=hash_password(password),
                    role_id=super_admin.id,
                    must_change_password=False,
                )
            )
    finally:
        engine.dispose()
    print("First administrator created.")


def verify_integrity() -> None:
    engine = create_database_engine(get_settings())
    checks = {
        "duplicate invoice numbers": """
            SELECT invoice_number FROM invoices WHERE invoice_number IS NOT NULL
            GROUP BY invoice_number HAVING count(*) > 1
        """,
        "numbering series gaps": """
            SELECT doc_type, regexp_replace(invoice_number, '/[0-9]+$', '') AS series,
                   fy_start_year, count(*) AS issued_count,
                   min(regexp_replace(invoice_number, '^.*/', '')::bigint) AS first_number,
                   max(regexp_replace(invoice_number, '^.*/', '')::bigint) AS last_number
            FROM invoices
            WHERE invoice_number IS NOT NULL AND invoice_number ~ '/[0-9]+$'
            GROUP BY doc_type, series, fy_start_year
            HAVING min(regexp_replace(invoice_number, '^.*/', '')::bigint) <> 1
                OR max(regexp_replace(invoice_number, '^.*/', '')::bigint) <> count(*)
        """,
        "inventory ledger parity": """
            WITH ledger AS (
              SELECT product_id, warehouse_id, sum(quantity_change) AS quantity
              FROM stock_movements GROUP BY product_id, warehouse_id
            ), balances AS (
              SELECT product_id, warehouse_id FROM inventory
              UNION
              SELECT product_id, warehouse_id FROM ledger
            )
            SELECT b.product_id, b.warehouse_id, i.quantity, l.quantity
            FROM balances b
            LEFT JOIN inventory i USING (product_id, warehouse_id)
            LEFT JOIN ledger l USING (product_id, warehouse_id)
            WHERE coalesce(i.quantity, 0) <> coalesce(l.quantity, 0)
        """,
        "unbalanced journal entries": """
            SELECT je.id
            FROM journal_entries je LEFT JOIN journal_entry_lines jel ON jel.entry_id = je.id
            GROUP BY je.id
            HAVING coalesce(sum(jel.debit), 0) <> coalesce(sum(jel.credit), 0)
        """,
        "trial balance parity": """
            SELECT sum(debit) AS debits, sum(credit) AS credits
            FROM journal_entry_lines
            HAVING coalesce(sum(debit), 0) <> coalesce(sum(credit), 0)
        """,
        "issued invoice totals": """
            SELECT i.id
            FROM invoices i LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
            WHERE i.status = 'issued'
            GROUP BY i.id
            HAVING i.grand_total <> i.taxable_total + i.cgst_total + i.sgst_total
                                   + i.igst_total + i.cess_total + i.round_off
                OR i.grand_total <> coalesce(sum(ii.taxable_value + ii.cgst + ii.sgst
                                   + ii.igst + ii.cess), 0) + i.round_off
        """,
        "issued invoice AR parity": """
            SELECT i.id
            FROM invoices i
            LEFT JOIN journal_entries je
              ON je.source_type = 'invoice' AND je.source_id = i.id AND je.reverses_entry_id IS NULL
            LEFT JOIN journal_entry_lines jel
              ON jel.entry_id = je.id AND jel.account_code = '1100'
            WHERE i.status = 'issued'
            GROUP BY i.id, i.grand_total
            HAVING coalesce(sum(jel.debit - jel.credit), 0) <> i.grand_total
        """,
        "issued invoices with journal entries": """
            SELECT i.id FROM invoices i
            WHERE i.status = 'issued'
              AND NOT EXISTS (
                SELECT 1 FROM journal_entries je
                WHERE je.source_type = 'invoice' AND je.source_id = i.id
                  AND je.reverses_entry_id IS NULL
              )
        """,
    }
    failures = 0
    try:
        with engine.connect() as connection:
            for label, query in checks.items():
                rows = connection.execute(text(query)).fetchmany(10)
                if rows:
                    failures += 1
                    print(f"FAIL {label}: {rows!r}")
                else:
                    print(f"PASS {label}")
    finally:
        engine.dispose()
    if failures:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="berev-admin")
    parser.add_argument("command", choices=("create-admin", "verify-integrity"))
    args = parser.parse_args()
    if args.command == "create-admin":
        create_first_admin()
    elif args.command == "verify-integrity":
        verify_integrity()


if __name__ == "__main__":
    main()
