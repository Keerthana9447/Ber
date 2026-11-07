from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

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


def main() -> None:
    parser = argparse.ArgumentParser(prog="berev-admin")
    parser.add_argument("command", choices=("create-admin",))
    args = parser.parse_args()
    if args.command == "create-admin":
        create_first_admin()


if __name__ == "__main__":
    main()
