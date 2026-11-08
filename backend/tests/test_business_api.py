import asyncio
import unittest
import uuid
from datetime import date
from io import BytesIO
from decimal import Decimal
import hashlib
import hmac
import json
import shutil
import tempfile
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError

from app.auth.security import create_token_pair, hash_password
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.models import (
    AuditLog,
    CompanySettings,
    Customer,
    Dealer,
    Expense,
    ExpenseCategory,
    GstRate,
    GoodsReceiptItem,
    Invoice,
    InvoiceItem,
    Inventory,
    HsnSacCode,
    InventoryOpeningValuation,
    JournalEntry,
    JournalEntryLine,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    Permission,
    Product,
    ProductCategory,
    ProductSerial,
    Purchase,
    PurchaseItem,
    Quotation,
    Role,
    SalesOrder,
    StockMovement,
    Supplier,
    Unit,
    User,
    Warehouse,
)
from app.seed import seed_reference_data
from berev_core.gst import LineInput, compute_invoice, gstin_checksum_char
from berev_core.words import amount_in_words
from pypdf import PdfReader
from pydantic import SecretStr
from tests.database_test_config import (
    configured_test_database_url,
    database_tests_required,
    require_test_database_url,
)


@unittest.skipUnless(
    configured_test_database_url() is not None or database_tests_required(),
    "Set TEST_DATABASE_URL or BEREV_TEST_DATABASE_URL to run PostgreSQL API integration tests",
)
class BusinessApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings(
            _env_file=PROJECT_ROOT / ".env",
            database_url=require_test_database_url(),
        )
        cls.app = create_app(cls.settings)
        try:
            with cls.app.state.engine.connect():
                pass
        except OperationalError as error:
            cls.app.state.engine.dispose()
            raise RuntimeError(
                "PostgreSQL integration tests require the isolated berev_test database to be available."
            ) from None
        cls.receipt_test_dir = tempfile.mkdtemp(prefix="berev-expense-test-")
        cls.app.state.settings.expense_upload_dir = cls.receipt_test_dir
        cls.factory = cls.app.state.session_factory
        cls.user_email = f"business-api-{uuid.uuid4().hex}@example.test"
        with cls.factory.begin() as session:
            seed_reference_data(session)
            role = session.scalar(select(Role).where(Role.name == "Super Admin"))
            if role is None:
                raise RuntimeError("The reference seed did not create Super Admin.")
            user = User(
                email=cls.user_email,
                full_name="Business API Integration Test",
                password_hash=hash_password(uuid.uuid4().hex),
                role_id=role.id,
                must_change_password=False,
            )
            session.add(user)
            session.flush()
            cls.user_id = user.id
        cls.access_token = create_token_pair(cls.settings, cls.user_id).access_token

    @classmethod
    def tearDownClass(cls):
        with cls.factory.begin() as session:
            session.execute(
                update(User).where(User.id == cls.user_id).values(is_active=False)
            )
        cls.app.state.engine.dispose()
        shutil.rmtree(cls.receipt_test_dir, ignore_errors=True)

    def test_refresh_rotation_and_logout_revoke_tokens(self):
        suffix = uuid.uuid4().hex
        email = f"token-test-{suffix}@example.test"
        password = f"test-password-{suffix}"
        with self.factory.begin() as session:
            role = session.scalar(select(Role).where(Role.name == "Super Admin"))
            if role is None:
                self.fail("Super Admin role is required")
            user = User(
                email=email,
                full_name="Refresh token test",
                password_hash=hash_password(password),
                role_id=role.id,
                must_change_password=False,
            )
            session.add(user)
            session.flush()
            user_id = user.id

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
                self.assertEqual(login.status_code, 200, login.text)
                first_refresh = login.json()["refresh_token"]
                rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
                self.assertEqual(rotated.status_code, 200, rotated.text)
                second_refresh = rotated.json()["refresh_token"]
                self.assertNotEqual(first_refresh, second_refresh)
                replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
                self.assertEqual(replay.status_code, 401, replay.text)
                logout = await client.post(
                    "/api/v1/auth/logout",
                    headers={"Authorization": f"Bearer {rotated.json()['access_token']}"},
                    json={"refresh_token": second_refresh},
                )
                self.assertEqual(logout.status_code, 204, logout.text)
                revoked = await client.post("/api/v1/auth/refresh", json={"refresh_token": second_refresh})
                self.assertEqual(revoked.status_code, 401, revoked.text)

        try:
            asyncio.run(exercise_api())
        finally:
            with self.factory.begin() as session:
                session.execute(update(User).where(User.id == user_id).values(is_active=False))

    def test_repeated_failed_logins_lock_account(self):
        suffix = uuid.uuid4().hex
        email = f"login-lockout-{suffix}@example.test"
        password = f"test-password-{suffix}"
        with self.factory.begin() as session:
            role = session.scalar(select(Role).where(Role.name == "Super Admin"))
            if role is None:
                self.fail("Super Admin role is required")
            user = User(
                email=email,
                full_name="Login lockout test",
                password_hash=hash_password(password),
                role_id=role.id,
                must_change_password=False,
            )
            session.add(user)
            session.flush()
            user_id = user.id

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                for _ in range(self.settings.login_max_attempts):
                    failed = await client.post(
                        "/api/v1/auth/login",
                        json={"email": email, "password": "incorrect-password"},
                    )
                    self.assertEqual(failed.status_code, 401, failed.text)
                locked = await client.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": password},
                )
                self.assertEqual(locked.status_code, 401, locked.text)

        try:
            asyncio.run(exercise_api())
            with self.factory() as session:
                locked_until = session.scalar(select(User.locked_until).where(User.id == user_id))
                self.assertIsNotNone(locked_until)
        finally:
            with self.factory.begin() as session:
                session.execute(update(User).where(User.id == user_id).values(is_active=False))

    def test_expense_receipt_validation_approval_and_journal_posting(self):
        with self.factory.begin() as session:
            category = session.scalar(
                select(ExpenseCategory).where(ExpenseCategory.account_code == "5100").limit(1)
            )
            method = session.scalar(
                select(PaymentMethod).where(PaymentMethod.is_active.is_(True)).order_by(PaymentMethod.id).limit(1)
            )
            if category is None or method is None:
                self.fail("Reference expense category and payment method are required")
            category_id, method_id = category.id, method.id
        headers = {"Authorization": f"Bearer {self.access_token}"}

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/api/v1/expenses",
                    headers=headers,
                    json={
                        "expense_date": date.today().isoformat(),
                        "category_id": category_id,
                        "method_id": method_id,
                        "amount": "100.00",
                        "cgst_amount": "9.00",
                        "sgst_amount": "9.00",
                        "vendor": "Test vendor",
                    },
                )
                self.assertEqual(response.status_code, 201, response.text)
                expense_id = response.json()["id"]
                invalid_receipt = await client.put(
                    f"/api/v1/expenses/{expense_id}/receipt",
                    headers={**headers, "Content-Type": "image/png"},
                    content=b"not an image",
                )
                self.assertEqual(invalid_receipt.status_code, 415, invalid_receipt.text)
                oversized_receipt = await client.put(
                    f"/api/v1/expenses/{expense_id}/receipt",
                    headers={**headers, "Content-Type": "application/pdf"},
                    content=b"%PDF-1.4" + b"x" * (5 * 1024 * 1024),
                )
                self.assertEqual(oversized_receipt.status_code, 413, oversized_receipt.text)
                receipt = await client.put(
                    f"/api/v1/expenses/{expense_id}/receipt",
                    headers={**headers, "Content-Type": "application/pdf"},
                    content=b"%PDF-1.4 test receipt",
                )
                self.assertEqual(receipt.status_code, 200, receipt.text)
                approved = await client.post(f"/api/v1/expenses/{expense_id}/approve", headers=headers)
                self.assertEqual(approved.status_code, 200, approved.text)
                posted = await client.post(f"/api/v1/expenses/{expense_id}/post", headers=headers)
                self.assertEqual(posted.status_code, 200, posted.text)
                self.assertEqual(posted.json()["status"], "posted")
                downloaded = await client.get(f"/api/v1/expenses/{expense_id}/receipt", headers=headers)
                self.assertEqual(downloaded.status_code, 200, downloaded.text)
                self.assertEqual(downloaded.content, b"%PDF-1.4 test receipt")
                trial_balance = await client.get(
                    "/api/v1/accounting/trial-balance",
                    params={"from": date.today().isoformat(), "to": date.today().isoformat()},
                    headers=headers,
                )
                self.assertEqual(trial_balance.status_code, 200, trial_balance.text)
                self.assertTrue(trial_balance.json()["is_balanced"])
                self.assertEqual(
                    trial_balance.json()["total_debit"],
                    trial_balance.json()["total_credit"],
                )
                profit_and_loss = await client.get(
                    "/api/v1/accounting/profit-and-loss",
                    params={"from": date.today().isoformat(), "to": date.today().isoformat()},
                    headers=headers,
                )
                self.assertEqual(profit_and_loss.status_code, 200, profit_and_loss.text)
                self.assertEqual(profit_and_loss.json()["label"], "before income tax")
                return expense_id, posted.json()["journal_entry_id"]

        expense_id, journal_id = asyncio.run(exercise_api())
        with self.factory() as session:
            expense = session.get(Expense, expense_id)
            self.assertIsNotNone(expense)
            self.assertEqual(expense.status, "posted")
            self.assertEqual(expense.gst_amount, Decimal("18.00"))
            journal_lines = session.scalars(
                select(JournalEntryLine).where(JournalEntryLine.entry_id == journal_id)
            ).all()
            self.assertEqual(sum((line.debit for line in journal_lines), Decimal("0")), Decimal("118.00"))
            self.assertEqual(sum((line.credit for line in journal_lines), Decimal("0")), Decimal("118.00"))

    def test_dealer_cannot_list_or_guess_another_dealers_records(self):
        suffix = uuid.uuid4().hex[:8].upper()
        with self.factory.begin() as session:
            role = session.scalar(select(Role).where(Role.name == "Dealer / Distributor"))
            if role is None:
                self.fail("Dealer role is required")
            customer_a = Customer(
                code=f"DL-A-{suffix}",
                name=f"Dealer A {suffix}",
                customer_type="dealer",
                state_code="27",
            )
            customer_b = Customer(
                code=f"DL-B-{suffix}",
                name=f"Dealer B {suffix}",
                customer_type="dealer",
                state_code="27",
            )
            session.add_all([customer_a, customer_b])
            session.flush()
            dealer_a = Dealer(dealer_code=f"DL-A-{suffix}", customer_id=customer_a.id, status="active")
            dealer_b = Dealer(dealer_code=f"DL-B-{suffix}", customer_id=customer_b.id, status="active")
            session.add_all([dealer_a, dealer_b])
            session.flush()
            user_a = User(
                email=f"dealer-a-{suffix.lower()}@example.test",
                full_name="Dealer A test user",
                password_hash=hash_password(uuid.uuid4().hex),
                role_id=role.id,
                dealer_id=dealer_a.id,
                must_change_password=False,
            )
            session.add(user_a)
            session.flush()
            order_a = SalesOrder(
                number=f"DL-SO-A-{suffix}",
                customer_id=customer_a.id,
                order_date=date.today(),
                place_of_supply_code="27",
                status="draft",
            )
            order_b = SalesOrder(
                number=f"DL-SO-B-{suffix}",
                customer_id=customer_b.id,
                order_date=date.today(),
                place_of_supply_code="27",
                status="draft",
            )
            session.add_all([order_a, order_b])
            invoice_a = Invoice(
                doc_type="tax_invoice",
                customer_id=customer_a.id,
                customer_name=customer_a.name,
                invoice_date=date.today(),
                place_of_supply_code="27",
                is_inter_state=False,
                status="draft",
            )
            invoice_b = Invoice(
                doc_type="tax_invoice",
                customer_id=customer_b.id,
                customer_name=customer_b.name,
                invoice_date=date.today(),
                place_of_supply_code="27",
                is_inter_state=False,
                status="draft",
            )
            session.add_all([invoice_a, invoice_b])
            session.flush()
            payment_method = session.scalar(
                select(PaymentMethod).where(PaymentMethod.is_active.is_(True)).order_by(PaymentMethod.id).limit(1)
            )
            if payment_method is None:
                self.fail("Reference payment method is required")
            payment_a = Payment(
                payment_number=f"DL-PAY-A-{suffix}",
                direction="in",
                customer_id=customer_a.id,
                method_id=payment_method.id,
                amount=Decimal("1.00"),
                payment_date=date.today(),
                status="recorded",
            )
            payment_b = Payment(
                payment_number=f"DL-PAY-B-{suffix}",
                direction="in",
                customer_id=customer_b.id,
                method_id=payment_method.id,
                amount=Decimal("1.00"),
                payment_date=date.today(),
                status="recorded",
            )
            session.add_all([payment_a, payment_b])
            session.flush()
            user_a_id = user_a.id
            order_a_id, order_b_id = order_a.id, order_b.id
            invoice_a_id, invoice_b_id = invoice_a.id, invoice_b.id
            payment_a_id, payment_b_id = payment_a.id, payment_b.id
        token = create_token_pair(self.settings, user_a_id).access_token
        headers = {"Authorization": f"Bearer {token}"}

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                orders = await client.get("/api/v1/orders", headers=headers)
                self.assertEqual(orders.status_code, 200, orders.text)
                listed_order_ids = {row["id"] for row in orders.json()["items"]}
                self.assertIn(order_a_id, listed_order_ids)
                self.assertNotIn(order_b_id, listed_order_ids)
                guessed_order = await client.get(f"/api/v1/orders/{order_b_id}", headers=headers)
                self.assertEqual(guessed_order.status_code, 404, guessed_order.text)

                invoices = await client.get("/api/v1/invoices", headers=headers)
                self.assertEqual(invoices.status_code, 200, invoices.text)
                listed_invoice_ids = {row["id"] for row in invoices.json()["items"]}
                self.assertIn(invoice_a_id, listed_invoice_ids)
                self.assertNotIn(invoice_b_id, listed_invoice_ids)
                guessed_invoice = await client.get(f"/api/v1/invoices/{invoice_b_id}", headers=headers)
                self.assertEqual(guessed_invoice.status_code, 404, guessed_invoice.text)

                payments = await client.get("/api/v1/payments", headers=headers)
                self.assertEqual(payments.status_code, 200, payments.text)
                listed_payment_ids = {row["id"] for row in payments.json()["items"]}
                self.assertIn(payment_a_id, listed_payment_ids)
                self.assertNotIn(payment_b_id, listed_payment_ids)

        asyncio.run(exercise_api())

    def test_purchase_grn_concurrency_partial_receipts_serials_and_payables(self):
        suffix = uuid.uuid4().hex[:8].upper()
        with self.factory.begin() as session:
            company = session.scalar(select(CompanySettings).order_by(CompanySettings.id).limit(1))
            if company is None:
                company = CompanySettings(
                    legal_name="BEREV API Test",
                    state_code="27",
                    invoice_prefix="TEST",
                    fy_start_month=4,
                )
                session.add(company)
                session.flush()
            elif company.state_code is None:
                company.state_code = "27"
            state_code = company.state_code
            rate = session.scalar(select(GstRate).where(GstRate.is_active.is_(True)).order_by(GstRate.rate).limit(1))
            if rate is None:
                rate = GstRate(rate=Decimal("5"), description="Purchase API test rate")
                session.add(rate)
                session.flush()
            unit = session.get(Unit, "PCS")
            if unit is None:
                unit = Unit(code="PCS", name="Pieces")
                session.add(unit)
            supplier = Supplier(code=f"PH5-{suffix}", name=f"Purchase supplier {suffix}", state_code=state_code)
            category = ProductCategory(name=f"Purchase test {suffix}")
            warehouse = Warehouse(code=f"PH5-{suffix}", name=f"Purchase warehouse {suffix}")
            hsn = HsnSacCode(
                code=f"{int(suffix, 16) % 100000000:08d}",
                kind="HSN",
                description="Purchase integration test",
                default_gst_rate=rate.rate,
                default_cess_rate=Decimal("0"),
            )
            session.add_all([supplier, category, warehouse, hsn])
            session.flush()
            product = Product(
                sku=f"PH5-{suffix}",
                name=f"Purchase item {suffix}",
                category_id=category.id,
                hsn_sac=hsn.code,
                unit=unit.code,
                purchase_price=Decimal("100"),
                selling_price=Decimal("150"),
                gst_rate=rate.rate,
                cess_rate=Decimal("0"),
            )
            serial_category = ProductCategory(name=f"Serialized purchase test {suffix}", is_serialized=True)
            session.add(serial_category)
            session.flush()
            serial_product = Product(
                sku=f"PH5-S-{suffix}",
                name=f"Serialized purchase item {suffix}",
                category_id=serial_category.id,
                hsn_sac=hsn.code,
                unit=unit.code,
                purchase_price=Decimal("100"),
                selling_price=Decimal("150"),
                gst_rate=rate.rate,
                cess_rate=Decimal("0"),
                is_serialized=True,
            )
            session.add_all([product, serial_product])
            session.flush()
            supplier_id, product_id = supplier.id, product.id
            serial_product_id = serial_product.id
            warehouse_id = warehouse.id
            gst_rate = format(rate.rate, "f")
            payment_method = session.scalar(
                select(PaymentMethod).where(PaymentMethod.is_active.is_(True)).order_by(PaymentMethod.id).limit(1)
            )
            if payment_method is None:
                self.fail("Reference payment method is required")
            method_id = payment_method.id

        headers = {"Authorization": f"Bearer {self.access_token}"}

        async def exercise_workflow():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                async def create_order(item_id: int, quantity: str) -> tuple[int, int]:
                    response = await client.post(
                        "/api/v1/purchases/requests",
                        headers=headers,
                        json={
                            "supplier_id": supplier_id,
                            "doc_date": date.today().isoformat(),
                            "lines": [{
                                "product_id": item_id,
                                "quantity": quantity,
                                "unit_price": "100",
                                "gst_rate": gst_rate,
                            }],
                        },
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    request_id = response.json()["id"]
                    response = await client.post(f"/api/v1/purchases/{request_id}/approve", headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    response = await client.post(
                        f"/api/v1/purchases/{request_id}/convert-to-order",
                        headers=headers,
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    with self.factory() as session:
                        order_line = session.scalar(
                            select(PurchaseItem).where(PurchaseItem.purchase_id == response.json()["id"])
                        )
                        self.assertIsNotNone(order_line)
                        return response.json()["id"], order_line.id

                order_id, order_line_id = await create_order(product_id, "3")
                receipt_body = {
                    "warehouse_id": warehouse_id,
                    "received_date": date.today().isoformat(),
                    "lines": [{"purchase_item_id": order_line_id, "quantity": "2"}],
                }
                competing = await asyncio.gather(
                    client.post(f"/api/v1/purchases/{order_id}/goods-receipts", headers=headers, json=receipt_body),
                    client.post(f"/api/v1/purchases/{order_id}/goods-receipts", headers=headers, json=receipt_body),
                )
                self.assertEqual(sorted(response.status_code for response in competing), [201, 409])
                partial = await client.post(
                    f"/api/v1/purchases/{order_id}/goods-receipts",
                    headers=headers,
                    json={
                        "warehouse_id": warehouse_id,
                        "received_date": date.today().isoformat(),
                        "lines": [{"purchase_item_id": order_line_id, "quantity": "1"}],
                    },
                )
                self.assertEqual(partial.status_code, 201, partial.text)

                invoice_payload = {
                    "supplier_invoice_no": f"SUP-INV-{suffix}-1",
                    "doc_date": date.today().isoformat(),
                    "lines": [{
                        "purchase_item_id": order_line_id,
                        "quantity": "2",
                        "unit_price": "100",
                        "gst_rate": gst_rate,
                    }],
                }
                first_invoice = await client.post(
                    f"/api/v1/purchases/{order_id}/invoices", headers=headers, json=invoice_payload
                )
                self.assertEqual(first_invoice.status_code, 201, first_invoice.text)
                invoice_payload["supplier_invoice_no"] = f"SUP-INV-{suffix}-2"
                invoice_payload["lines"][0]["quantity"] = "1"
                second_invoice = await client.post(
                    f"/api/v1/purchases/{order_id}/invoices", headers=headers, json=invoice_payload
                )
                self.assertEqual(second_invoice.status_code, 201, second_invoice.text)
                total_due = Decimal(first_invoice.json()["grand_total"]) + Decimal(second_invoice.json()["grand_total"])
                payment = await client.post(
                    "/api/v1/supplier-payments",
                    headers=headers,
                    json={
                        "supplier_id": supplier_id,
                        "method_id": method_id,
                        "amount": format(total_due, "f"),
                        "payment_date": date.today().isoformat(),
                        "allocations": [
                            {"purchase_id": first_invoice.json()["id"], "amount": first_invoice.json()["grand_total"]},
                            {"purchase_id": second_invoice.json()["id"], "amount": second_invoice.json()["grand_total"]},
                        ],
                    },
                )
                self.assertEqual(payment.status_code, 201, payment.text)
                ledger = await client.get(f"/api/v1/suppliers/{supplier_id}/ledger", headers=headers)
                self.assertEqual(ledger.status_code, 200, ledger.text)
                self.assertEqual(ledger.json()["closing_balance"], "0.00")

                serial_order_id, serial_order_line_id = await create_order(serial_product_id, "1")
                serial_receipt = {
                    "warehouse_id": warehouse_id,
                    "received_date": date.today().isoformat(),
                    "lines": [{
                        "purchase_item_id": serial_order_line_id,
                        "quantity": "1",
                        "serials": [{"chassis_no": f"CH-{suffix}"}],
                    }],
                }
                first_serial_receipt = await client.post(
                    f"/api/v1/purchases/{serial_order_id}/goods-receipts",
                    headers=headers,
                    json=serial_receipt,
                )
                self.assertEqual(first_serial_receipt.status_code, 201, first_serial_receipt.text)
                duplicate_order_id, duplicate_order_line_id = await create_order(serial_product_id, "1")
                serial_receipt["lines"][0]["purchase_item_id"] = duplicate_order_line_id
                duplicate = await client.post(
                    f"/api/v1/purchases/{duplicate_order_id}/goods-receipts",
                    headers=headers,
                    json=serial_receipt,
                )
                self.assertEqual(duplicate.status_code, 409, duplicate.text)

        asyncio.run(exercise_workflow())
        with self.factory() as session:
            inventory = session.get(Inventory, (product_id, warehouse_id))
            self.assertIsNotNone(inventory)
            self.assertEqual(inventory.quantity, Decimal("3"))
            self.assertEqual(inventory.average_unit_cost, Decimal("100.0000"))
            movement_quantity = session.scalar(
                select(func.sum(StockMovement.quantity_change)).where(
                    StockMovement.product_id == product_id,
                    StockMovement.warehouse_id == warehouse_id,
                )
            )
            self.assertEqual(movement_quantity, Decimal("3"))

    def test_opening_valuation_posts_one_batch_entry_and_reverses(self):
        suffix = uuid.uuid4().hex[:8].upper()
        category = ProductCategory(name=f"Opening valuation {suffix}")
        warehouse = Warehouse(code=f"OV-{suffix}", name=f"Opening valuation {suffix}")
        hsn_code = f"{int(suffix, 16) % 100000000:08d}"
        with self.factory.begin() as session:
            session.add_all([
                category,
                warehouse,
                HsnSacCode(
                    code=hsn_code,
                    kind="HSN",
                    description="Opening valuation test product",
                    default_gst_rate=Decimal("18"),
                    default_cess_rate=Decimal("0"),
                ),
            ])
            session.flush()
            product = Product(
                sku=f"OV-{suffix}",
                name=f"Opening valuation product {suffix}",
                category_id=category.id,
                hsn_sac=hsn_code,
                unit="PCS",
                purchase_price=Decimal("1"),
                selling_price=Decimal("2"),
                gst_rate=Decimal("18"),
                cess_rate=Decimal("0"),
            )
            session.add(product)
            session.flush()
            product_id = product.id
            warehouse_id = warehouse.id
            session.add(Inventory(product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal("2")))
            session.add(
                StockMovement(
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                    movement_type="opening",
                    quantity_change=Decimal("2"),
                    ref_type="test_opening_stock",
                    created_by=self.user_id,
                )
            )

        headers = {"Authorization": f"Bearer {self.access_token}"}

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/api/v1/inventory/opening-valuations",
                    json={"lines": [{"product_id": product_id, "warehouse_id": warehouse_id, "unit_cost": "25.50"}]},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(response.json()["total_value"], "51.00")
                journal_id = response.json()["journal_entry_id"]

                with self.factory() as session:
                    inventory = session.get(Inventory, (product_id, warehouse_id))
                    self.assertEqual(inventory.average_unit_cost, Decimal("25.5000"))
                    valuation_rows = list(session.scalars(
                        select(InventoryOpeningValuation).where(InventoryOpeningValuation.journal_entry_id == journal_id)
                    ))
                    journal_lines = list(session.scalars(
                        select(JournalEntryLine).where(JournalEntryLine.entry_id == journal_id)
                    ))
                    self.assertEqual(len(valuation_rows), 1)
                    self.assertEqual(
                        {(line.account_code, line.debit, line.credit) for line in journal_lines},
                        {
                            ("1200", Decimal("51.00"), Decimal("0.00")),
                            ("3000", Decimal("0.00"), Decimal("51.00")),
                        },
                    )

                reversal = await client.post(
                    f"/api/v1/inventory/opening-valuations/{journal_id}/reverse",
                    headers=headers,
                )
                self.assertEqual(reversal.status_code, 200, reversal.text)
                with self.factory() as session:
                    inventory = session.get(Inventory, (product_id, warehouse_id))
                    self.assertIsNone(inventory.average_unit_cost)

        asyncio.run(exercise_api())

    def test_phase3a_accounting_balances_across_issued_invoices(self):
        suffix = uuid.uuid4().hex[:8].upper()
        headers = {"Authorization": f"Bearer {self.access_token}"}
        pan = f"AB{chr(65 + int(suffix[0], 16))}DE{int(suffix[:4], 16) % 10000:04d}F"
        first_fourteen = f"06{pan}1Z"
        gstin = first_fourteen + gstin_checksum_char(first_fourteen)

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                settings_response = await client.put(
                    "/api/v1/settings",
                    json={
                        "legal_name": "Accounting Integration Company",
                        "address": "Integration test address",
                        "gstin": gstin,
                        "pan": pan,
                        "state_code": "06",
                        "invoice_prefix": f"A{suffix[:2]}",
                        "fy_start_month": 4,
                    },
                    headers=headers,
                )
                self.assertEqual(settings_response.status_code, 200, settings_response.text)

                customer_response = await client.post(
                    "/api/v1/customers",
                    json={
                        "code": f"ACCT-{suffix}",
                        "name": "Accounting integration customer",
                        "customer_type": "business",
                        "state_code": "29",
                        "billing_address": "Test billing address",
                    },
                    headers=headers,
                )
                self.assertEqual(customer_response.status_code, 201, customer_response.text)
                customer_id = customer_response.json()["id"]

                category = await client.post(
                    "/api/v1/product-categories",
                    json={"name": f"Accounting category {suffix}"},
                    headers=headers,
                )
                self.assertEqual(category.status_code, 201, category.text)
                product_options = await client.get("/api/v1/product-options", headers=headers)
                self.assertEqual(product_options.status_code, 200, product_options.text)
                rate = next(
                    (item["rate"] for item in product_options.json()["gst_rates"] if item["rate"] == "18.00"),
                    None,
                )
                if rate is None:
                    created_rate = await client.post(
                        "/api/v1/gst-rates",
                        json={"rate": "18.00", "description": "Accounting integration rate"},
                        headers=headers,
                    )
                    self.assertEqual(created_rate.status_code, 201, created_rate.text)
                    rate = created_rate.json()["rate"]
                warehouse = await client.post(
                    "/api/v1/warehouses",
                    json={"code": f"AC-{suffix}", "name": f"Accounting warehouse {suffix}"},
                    headers=headers,
                )
                self.assertEqual(warehouse.status_code, 201, warehouse.text)
                hsn = await client.post(
                    "/api/v1/hsn-sac-codes",
                    json={
                        "code": f"{int(suffix, 16) % 100000000:08d}",
                        "description": "Accounting integration item",
                        "kind": "HSN",
                        "default_gst_rate": rate,
                        "default_cess_rate": "0",
                    },
                    headers=headers,
                )
                self.assertEqual(hsn.status_code, 201, hsn.text)
                product = await client.post(
                    "/api/v1/products",
                    json={
                        "sku": f"AC-{suffix}",
                        "name": "Accounting integration item",
                        "category_id": category.json()["id"],
                        "hsn_sac": hsn.json()["code"],
                        "unit": "PCS",
                        "purchase_price": "40.00",
                        "selling_price": "100.00",
                        "gst_rate": rate,
                        "cess_rate": "0",
                    },
                    headers=headers,
                )
                self.assertEqual(product.status_code, 201, product.text)
                stock = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={
                        "product_id": product.json()["id"],
                        "warehouse_id": warehouse.json()["id"],
                        "quantity_delta": "2",
                    },
                    headers=headers,
                )
                self.assertEqual(stock.status_code, 201, stock.text)
                valuation = await client.post(
                    "/api/v1/inventory/opening-valuations",
                    json={
                        "lines": [{
                            "product_id": product.json()["id"],
                            "warehouse_id": warehouse.json()["id"],
                            "unit_cost": "40.0000",
                        }]
                    },
                    headers=headers,
                )
                self.assertEqual(valuation.status_code, 201, valuation.text)

                invoice_ids = []
                for place_of_supply in ("06", "29"):
                    draft = await client.post(
                        "/api/v1/invoices",
                        json={
                            "customer_id": customer_id,
                            "place_of_supply_code": place_of_supply,
                            "invoice_date": date.today().isoformat(),
                            "lines": [{
                                "product_id": product.json()["id"],
                                "warehouse_id": warehouse.json()["id"],
                                "description": "Accounting integration item",
                                "hsn_sac": hsn.json()["code"],
                                "unit": "PCS",
                                "quantity": "1",
                                "unit_price": "100.00",
                                "discount_pct": "0",
                                "discount_amount": "0",
                                "gst_rate": rate,
                                "cess_rate": "0",
                            }],
                        },
                        headers=headers,
                    )
                    self.assertEqual(draft.status_code, 201, draft.text)
                    invoice_id = draft.json()["id"]
                    approved = await client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=headers)
                    self.assertEqual(approved.status_code, 200, approved.text)
                    issued = await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=headers)
                    self.assertEqual(issued.status_code, 200, issued.text)
                    invoice_ids.append(invoice_id)

                with self.factory() as session:
                    balances = {
                        account: (Decimal(debit), Decimal(credit))
                        for account, debit, credit in session.execute(
                            text(
                                "SELECT line.account_code, sum(line.debit), sum(line.credit) "
                                "FROM journal_entry_lines line "
                                "JOIN journal_entries entry ON entry.id = line.entry_id "
                                "WHERE entry.source_type = 'invoice' "
                                "AND entry.source_id = ANY(:invoice_ids) "
                                "AND entry.reverses_entry_id IS NULL "
                                "GROUP BY line.account_code"
                            ),
                            {"invoice_ids": invoice_ids},
                        ).all()
                    }
                    self.assertEqual(balances["1100"], (Decimal("236.00"), Decimal("0")))
                    self.assertEqual(balances["4000"], (Decimal("0"), Decimal("200.00")))
                    self.assertEqual(balances["2100"], (Decimal("0"), Decimal("9.00")))
                    self.assertEqual(balances["2101"], (Decimal("0"), Decimal("9.00")))
                    self.assertEqual(balances["2102"], (Decimal("0"), Decimal("18.00")))
                    self.assertEqual(balances["5000"], (Decimal("80.00"), Decimal("0")))
                    self.assertEqual(balances["1200"], (Decimal("0"), Decimal("80.00")))
                    self.assertEqual(
                        sum((debit for debit, _credit in balances.values()), Decimal("0")),
                        sum((credit for _debit, credit in balances.values()), Decimal("0")),
                    )
                    self.assertEqual(
                        session.get(Inventory, (product.json()["id"], warehouse.json()["id"])).quantity,
                        Decimal("0"),
                    )

                for invoice_id in invoice_ids:
                    cancelled = await client.post(
                        f"/api/v1/invoices/{invoice_id}/cancel",
                        json={"reason": "Accounting integration test cleanup"},
                        headers=headers,
                    )
                    self.assertEqual(cancelled.status_code, 200, cancelled.text)

        asyncio.run(exercise_api())

    def test_phase3b_payments_credit_notes_webhook_and_ledger(self):
        suffix = uuid.uuid4().hex[:8].upper()
        headers = {"Authorization": f"Bearer {self.access_token}"}
        pan = f"AB{chr(65 + int(suffix[0], 16))}DE{int(suffix[:4], 16) % 10000:04d}F"
        gstin_prefix = f"06{pan}1Z"
        gstin = gstin_prefix + gstin_checksum_char(gstin_prefix)

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                configured = await client.put(
                    "/api/v1/settings",
                    json={
                        "legal_name": "Payment Integration Company",
                        "address": "Payment integration address",
                        "gstin": gstin,
                        "pan": pan,
                        "state_code": "06",
                        "invoice_prefix": f"P{suffix[:2]}",
                        "fy_start_month": 4,
                    },
                    headers=headers,
                )
                self.assertEqual(configured.status_code, 200, configured.text)
                customer = await client.post(
                    "/api/v1/customers",
                    json={
                        "code": f"PAY-{suffix}",
                        "name": "Payment integration customer",
                        "customer_type": "business",
                        "state_code": "29",
                        "credit_limit": "1000.00",
                    },
                    headers=headers,
                )
                self.assertEqual(customer.status_code, 201, customer.text)
                customer_id = customer.json()["id"]
                methods = await client.get("/api/v1/payment-methods", headers=headers)
                self.assertEqual(methods.status_code, 200, methods.text)
                method_id = next(item["id"] for item in methods.json() if item["ledger_kind"] == "bank")

                category = await client.post(
                    "/api/v1/product-categories",
                    json={"name": f"Payment category {suffix}"},
                    headers=headers,
                )
                self.assertEqual(category.status_code, 201, category.text)
                hsn_code = f"{int(suffix, 16) % 100000000:08d}"
                hsn = await client.post(
                    "/api/v1/hsn-sac-codes",
                    json={
                        "code": hsn_code,
                        "description": "Payment test goods",
                        "kind": "HSN",
                        "default_gst_rate": "18.00",
                        "default_cess_rate": "0",
                    },
                    headers=headers,
                )
                self.assertEqual(hsn.status_code, 201, hsn.text)
                options = await client.get("/api/v1/product-options", headers=headers)
                self.assertEqual(options.status_code, 200, options.text)
                rate = next((item["rate"] for item in options.json()["gst_rates"] if item["rate"] == "18.00"), None)
                if rate is None:
                    created_rate = await client.post(
                        "/api/v1/gst-rates",
                        json={"rate": "18.00", "description": "Payment integration rate"},
                        headers=headers,
                    )
                    self.assertEqual(created_rate.status_code, 201, created_rate.text)
                    rate = created_rate.json()["rate"]
                warehouse = await client.post(
                    "/api/v1/warehouses",
                    json={"code": f"PY-{suffix}", "name": f"Payment warehouse {suffix}"},
                    headers=headers,
                )
                self.assertEqual(warehouse.status_code, 201, warehouse.text)
                product = await client.post(
                    "/api/v1/products",
                    json={
                        "sku": f"PY-{suffix}",
                        "name": "Payment test goods",
                        "category_id": category.json()["id"],
                        "hsn_sac": hsn_code,
                        "unit": "PCS",
                        "purchase_price": "40.00",
                        "selling_price": "100.00",
                        "gst_rate": rate,
                        "cess_rate": "0",
                    },
                    headers=headers,
                )
                self.assertEqual(product.status_code, 201, product.text)
                opening = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={
                        "product_id": product.json()["id"],
                        "warehouse_id": warehouse.json()["id"],
                        "quantity_delta": "3",
                    },
                    headers=headers,
                )
                self.assertEqual(opening.status_code, 201, opening.text)
                valuation = await client.post(
                    "/api/v1/inventory/opening-valuations",
                    json={
                        "lines": [{
                            "product_id": product.json()["id"],
                            "warehouse_id": warehouse.json()["id"],
                            "unit_cost": "40.0000",
                        }]
                    },
                    headers=headers,
                )
                self.assertEqual(valuation.status_code, 201, valuation.text)

                async def issue_one():
                    draft = await client.post(
                        "/api/v1/invoices",
                        json={
                            "customer_id": customer_id,
                            "place_of_supply_code": "29",
                            "invoice_date": date.today().isoformat(),
                            "due_date": date.today().isoformat(),
                            "lines": [{
                                "product_id": product.json()["id"],
                                "warehouse_id": warehouse.json()["id"],
                                "description": "Payment test goods",
                                "hsn_sac": hsn_code,
                                "unit": "PCS",
                                "quantity": "1",
                                "unit_price": "100.00",
                                "discount_pct": "0",
                                "discount_amount": "0",
                                "gst_rate": rate,
                                "cess_rate": "0",
                            }],
                        },
                        headers=headers,
                    )
                    self.assertEqual(draft.status_code, 201, draft.text)
                    invoice_id = draft.json()["id"]
                    approved = await client.post(f"/api/v1/invoices/{invoice_id}/approve", headers=headers)
                    self.assertEqual(approved.status_code, 200, approved.text)
                    issued = await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=headers)
                    self.assertEqual(issued.status_code, 200, issued.text)
                    return invoice_id, issued.json()

                invoice_id, invoice = await issue_one()
                self.assertEqual(invoice["grand_total"], "118.00")
                self.assertEqual(invoice["payment_status"], "unpaid")
                unauthenticated = await client.post("/api/v1/payments", json={})
                self.assertEqual(unauthenticated.status_code, 401)
                read_only_role = Role(name=f"Payment Read Only {suffix}")
                with self.factory.begin() as session:
                    read_only_role.permissions = [session.scalar(select(Permission).where(Permission.code == "payments:read"))]
                    session.add(read_only_role)
                    session.flush()
                    read_only_user = User(
                        email=f"payments-read-{suffix}@example.test",
                        full_name="Payments read only",
                        password_hash=hash_password(uuid.uuid4().hex),
                        role_id=read_only_role.id,
                        must_change_password=False,
                    )
                    session.add(read_only_user)
                    session.flush()
                    read_only_id = read_only_user.id
                read_only_headers = {
                    "Authorization": f"Bearer {create_token_pair(self.settings, read_only_id).access_token}"
                }
                forbidden = await client.post(
                    "/api/v1/payments",
                    json={
                        "customer_id": customer_id,
                        "method_id": method_id,
                        "amount": "1.00",
                        "payment_date": date.today().isoformat(),
                    },
                    headers=read_only_headers,
                )
                self.assertEqual(forbidden.status_code, 403)

                note_body = {
                    "invoice_id": invoice_id,
                    "note_date": date.today().isoformat(),
                    "reason": "Partial quantity returned",
                    "restock": True,
                    "lines": [{"invoice_item_id": invoice["lines"][0]["id"], "quantity": "0.5"}],
                }
                note_response = await client.post("/api/v1/credit-notes", json=note_body, headers=headers)
                self.assertEqual(note_response.status_code, 201, note_response.text)
                note = note_response.json()
                blocked_cancel = await client.post(
                    f"/api/v1/invoices/{invoice_id}/cancel",
                    json={"reason": "Credit note has already been issued"},
                    headers=headers,
                )
                self.assertEqual(blocked_cancel.status_code, 409)
                expected_note = compute_invoice(
                    [LineInput("Payment test goods", hsn_code, "0.5", "100.00", rate, "0", "0", "0", "PCS")],
                    "06",
                    "29",
                    allowed_rates={Decimal(rate)},
                )
                self.assertEqual(Decimal(note["grand_total"]), expected_note.grand_total)
                self.assertEqual(Decimal(note["taxable_total"]), expected_note.taxable_total)
                excessive_note = await client.post(
                    "/api/v1/credit-notes",
                    json={**note_body, "lines": [{"invoice_item_id": invoice["lines"][0]["id"], "quantity": "0.6"}]},
                    headers=headers,
                )
                self.assertEqual(excessive_note.status_code, 409)
                note_pdf = await client.get(f"/api/v1/credit-notes/{note['id']}/pdf", headers=headers)
                self.assertEqual(note_pdf.status_code, 200, note_pdf.text)
                note_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(note_pdf.content)).pages)
                self.assertIn(note["note_number"], note_text)
                self.assertIn(note["amount_in_words"], note_text)
                with self.factory() as session:
                    self.assertEqual(
                        session.get(Inventory, (product.json()["id"], warehouse.json()["id"])).quantity,
                        Decimal("2.5"),
                    )

                first_payment = await client.post(
                    "/api/v1/payments",
                    json={
                        "customer_id": customer_id,
                        "method_id": method_id,
                        "amount": "20.00",
                        "payment_date": date.today().isoformat(),
                        "allocations": [{"invoice_id": invoice_id, "amount": "20.00"}],
                    },
                    headers=headers,
                )
                self.assertEqual(first_payment.status_code, 201, first_payment.text)
                balance = await client.get(f"/api/v1/invoices/{invoice_id}", headers=headers)
                self.assertEqual(balance.json()["payment_status"], "partially_paid")
                self.assertEqual(balance.json()["outstanding"], "39.00")
                second_payment = await client.post(
                    "/api/v1/payments",
                    json={
                        "customer_id": customer_id,
                        "method_id": method_id,
                        "amount": "39.00",
                        "payment_date": date.today().isoformat(),
                    },
                    headers=headers,
                )
                self.assertEqual(second_payment.status_code, 201, second_payment.text)
                balance = await client.get(f"/api/v1/invoices/{invoice_id}", headers=headers)
                self.assertEqual(balance.json()["payment_status"], "paid")
                self.assertEqual(balance.json()["outstanding"], "0.00")
                receipt_pdf = await client.get(f"/api/v1/payments/{first_payment.json()['id']}/pdf", headers=headers)
                self.assertEqual(receipt_pdf.status_code, 200, receipt_pdf.text)
                receipt_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(receipt_pdf.content)).pages)
                self.assertIn(first_payment.json()["payment_number"], receipt_text)
                reversed_payment = await client.post(
                    f"/api/v1/payments/{second_payment.json()['id']}/reverse",
                    headers=headers,
                )
                self.assertEqual(reversed_payment.status_code, 200, reversed_payment.text)
                restored_balance = await client.get(f"/api/v1/invoices/{invoice_id}", headers=headers)
                self.assertEqual(restored_balance.json()["outstanding"], "39.00")
                paged_ledger = await client.get(
                    f"/api/v1/customers/{customer_id}/ledger",
                    params={"page": 1, "page_size": 1},
                    headers=headers,
                )
                self.assertEqual(paged_ledger.status_code, 200, paged_ledger.text)
                self.assertEqual(paged_ledger.json()["closing_balance"], "39.00")
                self.assertEqual(paged_ledger.json()["total"], 5)
                duplicate_reverse = await client.post(
                    f"/api/v1/payments/{second_payment.json()['id']}/reverse",
                    headers=headers,
                )
                self.assertEqual(duplicate_reverse.status_code, 409)

                final_credit_note = await client.post(
                    "/api/v1/credit-notes",
                    json={
                        **note_body,
                        "reason": "Remaining invoice quantity returned",
                        "restock": False,
                        "lines": [{"invoice_item_id": invoice["lines"][0]["id"], "quantity": "0.5"}],
                    },
                    headers=headers,
                )
                self.assertEqual(final_credit_note.status_code, 201, final_credit_note.text)
                refund = await client.post(
                    "/api/v1/payments/refunds",
                    json={
                        "customer_id": customer_id,
                        "method_id": method_id,
                        "amount": "20.00",
                        "payment_date": date.today().isoformat(),
                        "source": "invoice_credit",
                        "invoice_id": invoice_id,
                    },
                    headers=headers,
                )
                self.assertEqual(refund.status_code, 201, refund.text)
                self.assertEqual(refund.json()["direction"], "out")
                refunded_balance = await client.get(f"/api/v1/invoices/{invoice_id}", headers=headers)
                self.assertEqual(refunded_balance.json()["outstanding"], "0.00")
                self.assertEqual(refunded_balance.json()["customer_credit"], "0.00")
                excessive_refund = await client.post(
                    "/api/v1/payments/refunds",
                    json={
                        "customer_id": customer_id,
                        "method_id": method_id,
                        "amount": "0.01",
                        "payment_date": date.today().isoformat(),
                        "source": "invoice_credit",
                        "invoice_id": invoice_id,
                    },
                    headers=headers,
                )
                self.assertEqual(excessive_refund.status_code, 409)
                refund_reversal = await client.post(
                    f"/api/v1/payments/{refund.json()['id']}/reverse",
                    headers=headers,
                )
                self.assertEqual(refund_reversal.status_code, 200, refund_reversal.text)
                refund_reversal_balance = await client.get(f"/api/v1/invoices/{invoice_id}", headers=headers)
                self.assertEqual(refund_reversal_balance.json()["customer_credit"], "20.00")

                advance_receipt = await client.post(
                    "/api/v1/payments",
                    json={
                        "customer_id": customer_id,
                        "method_id": method_id,
                        "amount": "10.00",
                        "payment_date": date.today().isoformat(),
                    },
                    headers=headers,
                )
                self.assertEqual(advance_receipt.status_code, 201, advance_receipt.text)
                self.assertTrue(advance_receipt.json()["is_advance"])
                advance_refund = await client.post(
                    "/api/v1/payments/refunds",
                    json={
                        "customer_id": customer_id,
                        "method_id": method_id,
                        "amount": "10.00",
                        "payment_date": date.today().isoformat(),
                        "source": "advance",
                    },
                    headers=headers,
                )
                self.assertEqual(advance_refund.status_code, 201, advance_refund.text)
                excess_advance_refund = await client.post(
                    "/api/v1/payments/refunds",
                    json={
                        "customer_id": customer_id,
                        "method_id": method_id,
                        "amount": "0.01",
                        "payment_date": date.today().isoformat(),
                        "source": "advance",
                    },
                    headers=headers,
                )
                self.assertEqual(excess_advance_refund.status_code, 409)

                competing_invoice_id, competing_invoice = await issue_one()
                allocation_body = {
                    "customer_id": customer_id,
                    "method_id": method_id,
                    "amount": competing_invoice["grand_total"],
                    "payment_date": date.today().isoformat(),
                    "allocations": [{
                        "invoice_id": competing_invoice_id,
                        "amount": competing_invoice["grand_total"],
                    }],
                }
                concurrent_payments = await asyncio.gather(*[
                    client.post("/api/v1/payments", json=allocation_body, headers=headers)
                    for _ in range(2)
                ])
                self.assertEqual(sorted(response.status_code for response in concurrent_payments), [201, 409])
                with self.factory() as session:
                    allocated = session.scalar(
                        select(func.sum(PaymentAllocation.amount))
                        .join(Payment)
                        .where(
                            PaymentAllocation.invoice_id == competing_invoice_id,
                            Payment.status == "recorded",
                        )
                    )
                    self.assertEqual(Decimal(allocated), Decimal(competing_invoice["grand_total"]))

                    payment_number = f"DBTEST-{suffix}"
                    bad_payment = Payment(
                        payment_number=payment_number,
                        direction="in",
                        customer_id=customer_id,
                        method_id=method_id,
                        amount=Decimal("1.00"),
                        payment_date=date.today(),
                        created_by=self.user_id,
                    )
                    session.add(bad_payment)
                    session.flush()
                    session.add(
                        PaymentAllocation(
                            payment_id=bad_payment.id,
                            invoice_id=competing_invoice_id,
                            amount=Decimal("2.00"),
                        )
                    )
                    session.flush()
                    with self.assertRaises(DBAPIError):
                        session.execute(text("SET CONSTRAINTS alloc_total_ok IMMEDIATE"))
                    session.rollback()

                ledger = await client.get(f"/api/v1/customers/{customer_id}/ledger", headers=headers)
                self.assertEqual(ledger.status_code, 200, ledger.text)
                self.assertGreaterEqual(ledger.json()["total"], 5)

                webhook_secret = "fake-test-signer-secret"
                self.app.state.settings = self.settings.model_copy(
                    update={"gateway_webhook_secret": SecretStr(webhook_secret)}
                )
                webhook_body = json.dumps({
                    "customer_id": customer_id,
                    "method_id": method_id,
                    "amount": "1.00",
                    "payment_date": date.today().isoformat(),
                    "gateway_txn_id": f"GW-{suffix}",
                }).encode()
                fake_signature = hmac.new(webhook_secret.encode(), webhook_body, hashlib.sha256).hexdigest()
                invalid_webhook = await client.post(
                    "/api/v1/payments/gateway/webhook",
                    content=webhook_body,
                    headers={"X-Gateway-Signature": "invalid"},
                )
                self.assertEqual(invalid_webhook.status_code, 401)
                accepted_webhook = await client.post(
                    "/api/v1/payments/gateway/webhook",
                    content=webhook_body,
                    headers={"X-Gateway-Signature": fake_signature, "Content-Type": "application/json"},
                )
                self.assertEqual(accepted_webhook.status_code, 201, accepted_webhook.text)
                self.assertTrue(accepted_webhook.json()["gateway_signature_verified"])
                duplicate_webhook = await client.post(
                    "/api/v1/payments/gateway/webhook",
                    content=webhook_body,
                    headers={"X-Gateway-Signature": fake_signature, "Content-Type": "application/json"},
                )
                self.assertEqual(duplicate_webhook.status_code, 409)

                self.app.state.settings = self.settings

        asyncio.run(exercise_api())

    def test_phase4_quotation_order_partial_challan_invoice_and_sales_reports(self):
        suffix = uuid.uuid4().hex[:8].upper()
        headers = {"Authorization": f"Bearer {self.access_token}"}
        pan = f"AB{chr(65 + int(suffix[0], 16))}DE{int(suffix[:4], 16) % 10000:04d}F"
        gstin_prefix = f"06{pan}1Z"
        gstin = gstin_prefix + gstin_checksum_char(gstin_prefix)

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                configured = await client.put(
                    "/api/v1/settings",
                    json={
                        "legal_name": "Phase 4 Integration Company",
                        "address": "Phase 4 test address",
                        "gstin": gstin,
                        "pan": pan,
                        "state_code": "06",
                        "invoice_prefix": f"F{suffix[:2]}",
                        "fy_start_month": 4,
                    },
                    headers=headers,
                )
                self.assertEqual(configured.status_code, 200, configured.text)
                customer = await client.post(
                    "/api/v1/customers",
                    json={
                        "code": f"PH4-{suffix}",
                        "name": "Phase 4 integration customer",
                        "customer_type": "business",
                        "state_code": "06",
                        "billing_address": "Phase 4 billing address",
                    },
                    headers=headers,
                )
                self.assertEqual(customer.status_code, 201, customer.text)
                options = await client.get("/api/v1/product-options", headers=headers)
                self.assertEqual(options.status_code, 200, options.text)
                rate = next((row["rate"] for row in options.json()["gst_rates"] if row["rate"] == "18.00"), None)
                if rate is None:
                    created_rate = await client.post(
                        "/api/v1/gst-rates",
                        json={"rate": "18.00", "description": "Phase 4 integration"},
                        headers=headers,
                    )
                    self.assertEqual(created_rate.status_code, 201, created_rate.text)
                    rate = created_rate.json()["rate"]
                category = await client.post(
                    "/api/v1/product-categories",
                    json={"name": f"Phase 4 category {suffix}"},
                    headers=headers,
                )
                self.assertEqual(category.status_code, 201, category.text)
                code = f"{int(suffix, 16) % 100000000:08d}"
                hsn = await client.post(
                    "/api/v1/hsn-sac-codes",
                    json={
                        "code": code,
                        "description": "Phase 4 integration good",
                        "kind": "HSN",
                        "default_gst_rate": rate,
                        "default_cess_rate": "0",
                    },
                    headers=headers,
                )
                self.assertEqual(hsn.status_code, 201, hsn.text)
                product = await client.post(
                    "/api/v1/products",
                    json={
                        "sku": f"PH4-{suffix}",
                        "name": "Phase 4 integration good",
                        "category_id": category.json()["id"],
                        "hsn_sac": code,
                        "unit": "PCS",
                        "purchase_price": "40.00",
                        "selling_price": "100.00",
                        "gst_rate": rate,
                        "cess_rate": "0",
                    },
                    headers=headers,
                )
                self.assertEqual(product.status_code, 201, product.text)
                warehouse = await client.post(
                    "/api/v1/warehouses",
                    json={"code": f"PH4-{suffix}", "name": f"Phase 4 warehouse {suffix}"},
                    headers=headers,
                )
                self.assertEqual(warehouse.status_code, 201, warehouse.text)
                adjustment = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={
                        "product_id": product.json()["id"],
                        "warehouse_id": warehouse.json()["id"],
                        "quantity_delta": "2",
                    },
                    headers=headers,
                )
                self.assertEqual(adjustment.status_code, 201, adjustment.text)

                quote_response = await client.post(
                    "/api/v1/quotations",
                    json={
                        "customer_id": customer.json()["id"],
                        "quote_date": date.today().isoformat(),
                        "place_of_supply_code": "06",
                        "lines": [{
                            "product_id": product.json()["id"],
                            "quantity": "2",
                            "unit_price": "100",
                            "gst_rate": rate,
                        }],
                    },
                    headers=headers,
                )
                self.assertEqual(quote_response.status_code, 201, quote_response.text)
                quote_id = quote_response.json()["id"]
                premature = await client.post(
                    f"/api/v1/quotations/{quote_id}/convert-to-order",
                    headers=headers,
                )
                self.assertEqual(premature.status_code, 409, premature.text)
                sent = await client.post(f"/api/v1/quotations/{quote_id}/send", headers=headers)
                self.assertEqual(sent.status_code, 200, sent.text)
                accepted = await client.post(f"/api/v1/quotations/{quote_id}/accept", headers=headers)
                self.assertEqual(accepted.status_code, 200, accepted.text)
                order_response = await client.post(
                    f"/api/v1/quotations/{quote_id}/convert-to-order",
                    headers=headers,
                )
                self.assertEqual(order_response.status_code, 201, order_response.text)
                order = order_response.json()
                order_id = order["id"]
                self.assertEqual(order["lines"][0]["quantity"], "2.000")

                unconfirmed_challan = await client.post(
                    f"/api/v1/orders/{order_id}/delivery-challans",
                    json={"lines": [{"sales_order_item_id": order["lines"][0]["id"], "quantity": "1"}]},
                    headers=headers,
                )
                self.assertEqual(unconfirmed_challan.status_code, 409, unconfirmed_challan.text)
                confirmed = await client.post(f"/api/v1/orders/{order_id}/confirm", headers=headers)
                self.assertEqual(confirmed.status_code, 200, confirmed.text)

                first_challan = await client.post(
                    f"/api/v1/orders/{order_id}/delivery-challans",
                    json={"lines": [{"sales_order_item_id": order["lines"][0]["id"], "quantity": "1"}]},
                    headers=headers,
                )
                self.assertEqual(first_challan.status_code, 201, first_challan.text)
                first_id = first_challan.json()["id"]
                dispatched = await client.post(
                    f"/api/v1/delivery-challans/{first_id}/dispatch",
                    headers=headers,
                )
                self.assertEqual(dispatched.status_code, 200, dispatched.text)
                with self.factory() as session:
                    self.assertEqual(
                        session.get(Inventory, (product.json()["id"], warehouse.json()["id"])).quantity,
                        Decimal("2.000"),
                    )
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.ref_type == "delivery_challan")),
                        0,
                    )
                delivered = await client.post(
                    f"/api/v1/delivery-challans/{first_id}/deliver",
                    headers=headers,
                )
                self.assertEqual(delivered.status_code, 200, delivered.text)
                first_invoice = await client.post(
                    f"/api/v1/orders/{order_id}/convert-to-invoice",
                    headers=headers,
                )
                self.assertEqual(first_invoice.status_code, 201, first_invoice.text)
                self.assertEqual(first_invoice.json()["status"], "draft")

                second_challan = await client.post(
                    f"/api/v1/orders/{order_id}/delivery-challans",
                    json={"lines": [{"sales_order_item_id": order["lines"][0]["id"], "quantity": "1"}]},
                    headers=headers,
                )
                self.assertEqual(second_challan.status_code, 201, second_challan.text)
                second_id = second_challan.json()["id"]
                await client.post(f"/api/v1/delivery-challans/{second_id}/dispatch", headers=headers)
                await client.post(f"/api/v1/delivery-challans/{second_id}/deliver", headers=headers)
                second_invoice = await client.post(
                    f"/api/v1/orders/{order_id}/convert-to-invoice",
                    headers=headers,
                )
                self.assertEqual(second_invoice.status_code, 201, second_invoice.text)
                with self.factory() as session:
                    invoice_rows = session.scalars(
                        select(InvoiceItem)
                        .where(InvoiceItem.invoice_id.in_([first_invoice.json()["id"], second_invoice.json()["id"]]))
                    ).all()
                    self.assertEqual(sum((line.quantity for line in invoice_rows), Decimal("0")), Decimal("2.000"))
                    self.assertTrue(all(line.sales_order_item_id == order["lines"][0]["id"] for line in invoice_rows))
                    self.assertEqual(
                        session.get(Inventory, (product.json()["id"], warehouse.json()["id"])).quantity,
                        Decimal("2.000"),
                    )
                    self.assertEqual(
                        session.scalar(
                            select(func.count()).select_from(JournalEntry).where(
                                JournalEntry.source_type == "invoice",
                                JournalEntry.source_id.in_([first_invoice.json()["id"], second_invoice.json()["id"]]),
                            )
                        ),
                        0,
                    )

                quote_pdf = await client.get(f"/api/v1/quotations/{quote_id}/pdf", headers=headers)
                self.assertEqual(quote_pdf.status_code, 200, quote_pdf.text)
                self.assertIn(quote_response.json()["number"], PdfReader(BytesIO(quote_pdf.content)).pages[0].extract_text())
                for group in ("day", "month", "product", "customer", "salesperson", "gst", "branch"):
                    report_response = await client.get(
                        "/api/v1/reports/sales",
                        params={"from_date": date.today().isoformat(), "to_date": date.today().isoformat(), "group_by": group},
                        headers=headers,
                    )
                    self.assertEqual(report_response.status_code, 200, report_response.text)
                    self.assertIsInstance(report_response.json()["items"], list)
                self.assertEqual(
                    (await client.get("/api/v1/quotations", headers=headers)).status_code,
                    200,
                )

        asyncio.run(exercise_api())

    def test_phase4_sales_endpoints_require_authentication_and_sales_permissions(self):
        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                unauthenticated = await client.get("/api/v1/quotations")
                self.assertEqual(unauthenticated.status_code, 401, unauthenticated.text)
                with self.factory.begin() as session:
                    role = session.scalar(select(Role).where(Role.name == "Dealer / Distributor"))
                    self.assertIsNotNone(role)
                    user = User(
                        email=f"phase4-read-only-{uuid.uuid4().hex}@example.test",
                        full_name="Phase 4 read only test",
                        password_hash=hash_password(uuid.uuid4().hex),
                        role_id=role.id,
                        must_change_password=False,
                    )
                    session.add(user)
                    session.flush()
                    token = create_token_pair(self.settings, user.id).access_token
                forbidden = await client.post(
                    "/api/v1/quotations",
                    json={
                        "customer_id": 1,
                        "quote_date": date.today().isoformat(),
                        "place_of_supply_code": "06",
                        "lines": [{"product_id": 1, "quantity": "1", "unit_price": "1", "gst_rate": "18"}],
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(forbidden.status_code, 403, forbidden.text)

        asyncio.run(exercise_api())

    def test_dashboard_and_customer_crud(self):
        customer_code = f"TEST-{uuid.uuid4().hex[:16]}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        payload = {
            "code": customer_code,
            "name": "  Integration Customer  ",
            "customer_type": "business",
            "email": " CUSTOMER@EXAMPLE.TEST ",
            "credit_limit": "1250.50",
            "payment_terms_days": 30,
        }

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                unauthorized = await client.get("/api/v1/customers")
                self.assertEqual(unauthorized.status_code, 401)

                dashboard = await client.get(
                    "/api/v1/dashboard",
                    params={"from": "2026-11-01", "to": "2026-11-06"},
                    headers=headers,
                )
                self.assertEqual(dashboard.status_code, 200, dashboard.text)
                dashboard_data = dashboard.json()
                self.assertEqual(
                    set(dashboard_data["kpis"]),
                    {
                        "sales_today",
                        "sales_month",
                        "purchases_total",
                        "revenue_total",
                        "expenses_total",
                        "gross_profit",
                        "receivables_outstanding",
                        "payables_outstanding",
                        "customers_total",
                        "suppliers_total",
                        "products_total",
                        "low_stock_products",
                        "ev_vehicles_in_stock",
                    },
                )
                self.assertEqual(len(dashboard_data["sales_trend"]), 6)
                self.assertTrue(all(isinstance(value, str) for value in dashboard_data["kpis"].values() if isinstance(value, str)))
                notifications = await client.get("/api/v1/notifications", headers=headers)
                self.assertEqual(notifications.status_code, 200, notifications.text)
                self.assertEqual(notifications.json(), {"items": []})
                invalid_range = await client.get(
                    "/api/v1/dashboard",
                    params={"from": "2026-11-07", "to": "2026-11-06"},
                    headers=headers,
                )
                self.assertEqual(invalid_range.status_code, 422)

                created = await client.post("/api/v1/customers", json=payload, headers=headers)
                self.assertEqual(created.status_code, 201, created.text)
                customer = created.json()
                customer_id = customer["id"]
                self.assertEqual(customer["name"], "Integration Customer")
                self.assertEqual(customer["email"], "customer@example.test")
                self.assertEqual(customer["credit_limit"], "1250.50")

                listing = await client.get(
                    "/api/v1/customers",
                    params={"q": customer_code, "page": 1, "page_size": 5},
                    headers=headers,
                )
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["total"], 1)
                self.assertEqual(listing.json()["items"][0]["id"], customer_id)

                fetched = await client.get(f"/api/v1/customers/{customer_id}", headers=headers)
                self.assertEqual(fetched.status_code, 200)
                self.assertEqual(fetched.json()["code"], customer_code)

                update_payload = {
                    **payload,
                    "name": "Updated Integration Customer",
                    "credit_limit": "2000.00",
                }
                updated = await client.put(
                    f"/api/v1/customers/{customer_id}",
                    json=update_payload,
                    headers=headers,
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["name"], "Updated Integration Customer")
                self.assertEqual(updated.json()["credit_limit"], "2000.00")

                duplicate = await client.post("/api/v1/customers", json=payload, headers=headers)
                self.assertEqual(duplicate.status_code, 409)

                deleted = await client.delete(f"/api/v1/customers/{customer_id}", headers=headers)
                self.assertEqual(deleted.status_code, 204)
                missing = await client.get(f"/api/v1/customers/{customer_id}", headers=headers)
                self.assertEqual(missing.status_code, 404)
                listing_after_delete = await client.get(
                    "/api/v1/customers",
                    params={"q": customer_code},
                    headers=headers,
                )
                self.assertEqual(listing_after_delete.json()["total"], 0)

        asyncio.run(exercise_api())

    def test_product_and_concurrent_inventory_workflow(self):
        suffix = uuid.uuid4().hex[:12]
        sku = f"TEST-{suffix}"
        category_name = f"Integration category {suffix}"
        warehouse_code = f"TEST-{suffix}"
        hsn_code = f"{uuid.uuid4().int % 100_000_000:08d}"
        headers = {"Authorization": f"Bearer {self.access_token}"}

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                options = await client.get("/api/v1/product-options", headers=headers)
                self.assertEqual(options.status_code, 200, options.text)
                rates = options.json()["gst_rates"]
                if rates:
                    gst_rate = rates[0]["rate"]
                else:
                    created_rate = await client.post(
                        "/api/v1/gst-rates",
                        json={"rate": "18.00", "description": "Integration test rate"},
                        headers=headers,
                    )
                    self.assertEqual(created_rate.status_code, 201, created_rate.text)
                    gst_rate = created_rate.json()["rate"]

                category = await client.post(
                    "/api/v1/product-categories",
                    json={"name": category_name},
                    headers=headers,
                )
                self.assertEqual(category.status_code, 201, category.text)
                category_id = category.json()["id"]

                hsn = await client.post(
                    "/api/v1/hsn-sac-codes",
                    json={
                        "code": hsn_code,
                        "kind": "HSN",
                        "description": "Integration test product code",
                        "default_gst_rate": gst_rate,
                    },
                    headers=headers,
                )
                self.assertEqual(hsn.status_code, 201, hsn.text)

                warehouse = await client.post(
                    "/api/v1/warehouses",
                    json={"code": warehouse_code, "name": f"Integration warehouse {suffix}"},
                    headers=headers,
                )
                self.assertEqual(warehouse.status_code, 201, warehouse.text)
                warehouse_id = warehouse.json()["id"]

                payload = {
                    "sku": sku,
                    "name": "Integration product",
                    "category_id": category_id,
                    "hsn_sac": hsn_code,
                    "unit": "PCS",
                    "purchase_price": "100.00",
                    "selling_price": "150.00",
                    "gst_rate": gst_rate,
                    "cess_rate": "0",
                    "min_stock": "2",
                }
                created = await client.post("/api/v1/products", json=payload, headers=headers)
                self.assertEqual(created.status_code, 201, created.text)
                product = created.json()
                product_id = product["id"]
                self.assertEqual(product["selling_price"], "150.00")

                listing = await client.get(
                    "/api/v1/products",
                    params={"q": sku, "page": 1, "page_size": 5},
                    headers=headers,
                )
                self.assertEqual(listing.status_code, 200, listing.text)
                self.assertEqual(listing.json()["total"], 1)

                fetched = await client.get(f"/api/v1/products/{product_id}", headers=headers)
                self.assertEqual(fetched.status_code, 200, fetched.text)

                updated_payload = {**payload, "name": "Updated integration product", "selling_price": "175.00"}
                updated = await client.put(
                    f"/api/v1/products/{product_id}",
                    json=updated_payload,
                    headers=headers,
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["name"], "Updated integration product")

                opening = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={
                        "product_id": product_id,
                        "warehouse_id": warehouse_id,
                        "quantity_delta": "10.250",
                        "unit_cost": "100.0000",
                        "notes": "Initial test stock",
                    },
                    headers=headers,
                )
                self.assertEqual(opening.status_code, 201, opening.text)
                self.assertEqual(opening.json()["quantity"], "10.250")

                concurrent_results = await asyncio.gather(
                    client.post(
                        "/api/v1/inventory/adjustments",
                        json={"product_id": product_id, "warehouse_id": warehouse_id, "quantity_delta": "-7"},
                        headers=headers,
                    ),
                    client.post(
                        "/api/v1/inventory/adjustments",
                        json={"product_id": product_id, "warehouse_id": warehouse_id, "quantity_delta": "-6"},
                        headers=headers,
                    ),
                )
                self.assertEqual(sorted(result.status_code for result in concurrent_results), [201, 409])

                inventory = await client.get(
                    "/api/v1/inventory",
                    params={"q": sku, "warehouse_id": warehouse_id},
                    headers=headers,
                )
                self.assertEqual(inventory.status_code, 200, inventory.text)
                stock = Decimal(inventory.json()["items"][0]["quantity"])
                self.assertIn(stock, {Decimal("3.250"), Decimal("4.250")})

                negative = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={"product_id": product_id, "warehouse_id": warehouse_id, "quantity_delta": "-10"},
                    headers=headers,
                )
                self.assertEqual(negative.status_code, 409)

                deleted = await client.delete(f"/api/v1/products/{product_id}", headers=headers)
                self.assertEqual(deleted.status_code, 204, deleted.text)
                missing = await client.get(f"/api/v1/products/{product_id}", headers=headers)
                self.assertEqual(missing.status_code, 404)

        asyncio.run(exercise_api())

    def test_settings_customers_invoice_drafts_and_postgres_guards(self):
        suffix = uuid.uuid4().hex[:12]
        headers = {"Authorization": f"Bearer {self.access_token}"}
        pan = f"AB{chr(65 + int(suffix[0], 16))}DE{int(suffix[:4], 16) % 10000:04d}F"

        def gstin_for(state_code: str) -> str:
            first_fourteen = f"{state_code}{pan}1Z"
            return first_fourteen + gstin_checksum_char(first_fourteen)

        settings_payload = {
            "legal_name": "Integration Test Company",
            "trade_name": None,
            "address": "Integration test address",
            "phone": None,
            "email": None,
            "gstin": gstin_for("06"),
            "pan": pan,
            "state_code": "06",
            "bank_name": None,
            "bank_account_no": f"TEST-SECRET-{suffix}",
            "bank_ifsc": "TESTIFSC0001",
            "upi_id": f"test-{suffix}@upi",
            "logo_path": None,
            "invoice_prefix": "TST",
            "fy_start_month": 4,
            "terms_and_conditions": None,
            "authorized_signatory": None,
        }

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                initial = await client.get("/api/v1/settings", headers=headers)
                self.assertEqual(initial.status_code, 200, initial.text)

                invalid_company = {**settings_payload, "gstin": gstin_for("29")}
                rejected_company = await client.put("/api/v1/settings", json=invalid_company, headers=headers)
                self.assertEqual(rejected_company.status_code, 422)
                invalid_prefix = {**settings_payload, "invoice_prefix": "LONGP"}
                rejected_prefix = await client.put("/api/v1/settings", json=invalid_prefix, headers=headers)
                self.assertEqual(rejected_prefix.status_code, 422)
                invalid_pan = {**settings_payload, "pan": "INVALID"}
                rejected_pan = await client.put("/api/v1/settings", json=invalid_pan, headers=headers)
                self.assertEqual(rejected_pan.status_code, 422)
                invalid_fy = {**settings_payload, "fy_start_month": 13}
                rejected_fy = await client.put("/api/v1/settings", json=invalid_fy, headers=headers)
                self.assertEqual(rejected_fy.status_code, 422)

                saved_settings = await client.put("/api/v1/settings", json=settings_payload, headers=headers)
                self.assertEqual(saved_settings.status_code, 200, saved_settings.text)
                self.assertEqual(saved_settings.json()["gstin"], gstin_for("06"))
                with self.factory() as session:
                    settings_audit = session.scalar(
                        select(AuditLog)
                        .where(AuditLog.entity_type == "company_settings", AuditLog.entity_id == 1)
                        .order_by(AuditLog.id.desc())
                    )
                    self.assertIsNotNone(settings_audit)
                    self.assertNotIn("bank_account_no", settings_audit.new_values)
                    self.assertNotIn("bank_ifsc", settings_audit.new_values)
                    self.assertNotIn("upi_id", settings_audit.new_values)
                    self.assertNotIn(f"TEST-SECRET-{suffix}", str(settings_audit.new_values))
                    if settings_audit.old_values is not None:
                        self.assertNotIn("bank_account_no", settings_audit.old_values)
                        self.assertNotIn("bank_ifsc", settings_audit.old_values)
                        self.assertNotIn("upi_id", settings_audit.old_values)
                readiness = await client.get("/api/v1/settings/readiness", headers=headers)
                self.assertEqual(readiness.status_code, 200, readiness.text)
                self.assertEqual(readiness.json(), {"ready": True, "missing_fields": []})

                unauthenticated = await client.put("/api/v1/settings", json=settings_payload)
                self.assertEqual(unauthenticated.status_code, 401)

                states = await client.get("/api/v1/states", headers=headers)
                self.assertEqual(states.status_code, 200)
                self.assertIn({"code": "06", "name": "Haryana"}, states.json())

                customer_one_payload = {
                    "code": f"GST-{suffix}-A",
                    "name": "Interstate customer",
                    "customer_type": "business",
                    "gstin": gstin_for("29"),
                    "pan": pan,
                    "state_code": "29",
                    "billing_address": "Billing snapshot",
                    "shipping_address": "Shipping snapshot",
                }
                customer_one = await client.post("/api/v1/customers", json=customer_one_payload, headers=headers)
                self.assertEqual(customer_one.status_code, 201, customer_one.text)
                customer_id = customer_one.json()["id"]
                duplicate = await client.post(
                    "/api/v1/customers",
                    json={**customer_one_payload, "code": f"GST-{suffix}-DUP"},
                    headers=headers,
                )
                self.assertEqual(duplicate.status_code, 409)
                invalid_gstin = await client.post(
                    "/api/v1/customers",
                    json={**customer_one_payload, "code": f"GST-{suffix}-BAD", "gstin": "29ABCDE1234F1Z0"},
                    headers=headers,
                )
                self.assertEqual(invalid_gstin.status_code, 422)
                invalid_phone = await client.post(
                    "/api/v1/customers",
                    json={**customer_one_payload, "code": f"GST-{suffix}-PHONE", "phone": "not-a-phone"},
                    headers=headers,
                )
                self.assertEqual(invalid_phone.status_code, 422)
                filtered_customers = await client.get(
                    "/api/v1/customers",
                    params={
                        "customer_type": "business",
                        "state_code": "29",
                        "is_active": True,
                        "page_size": 100,
                    },
                    headers=headers,
                )
                self.assertEqual(filtered_customers.status_code, 200)
                self.assertIn(customer_id, {item["id"] for item in filtered_customers.json()["items"]})

                rate_rows = await client.get("/api/v1/product-options", headers=headers)
                self.assertEqual(rate_rows.status_code, 200, rate_rows.text)
                rates = rate_rows.json()["gst_rates"]
                rate = next((item["rate"] for item in rates if item["rate"] == "18.00"), None)
                if rate is None:
                    created_rate = await client.post(
                        "/api/v1/gst-rates",
                        json={"rate": "18.00", "description": "Integration-only rate"},
                        headers=headers,
                    )
                    self.assertEqual(created_rate.status_code, 201, created_rate.text)
                    rate = created_rate.json()["rate"]

                category = await client.post(
                    "/api/v1/product-categories",
                    json={"name": f"Invoice test category {suffix}"},
                    headers=headers,
                )
                self.assertEqual(category.status_code, 201, category.text)
                hsn_code = f"{uuid.uuid4().int % 100_000_000:08d}"
                hsn = await client.post(
                    "/api/v1/hsn-sac-codes",
                    json={
                        "code": hsn_code,
                        "kind": "HSN",
                        "description": "Invoice parity test item",
                        "default_gst_rate": rate,
                        "default_cess_rate": "1.00",
                    },
                    headers=headers,
                )
                self.assertEqual(hsn.status_code, 201, hsn.text)
                warehouse = await client.post(
                    "/api/v1/warehouses",
                    json={"code": f"INV-{suffix}", "name": f"Invoice warehouse {suffix}"},
                    headers=headers,
                )
                self.assertEqual(warehouse.status_code, 201, warehouse.text)
                product = await client.post(
                    "/api/v1/products",
                    json={
                        "sku": f"INV-{suffix}",
                        "name": "Invoice test item",
                        "category_id": category.json()["id"],
                        "hsn_sac": hsn_code,
                        "unit": "PCS",
                        "purchase_price": "40.00",
                        "selling_price": "100.00",
                        "gst_rate": rate,
                        "cess_rate": "1.00",
                    },
                    headers=headers,
                )
                self.assertEqual(product.status_code, 201, product.text)
                stock = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={
                        "product_id": product.json()["id"],
                        "warehouse_id": warehouse.json()["id"],
                        "quantity_delta": "10",
                    },
                    headers=headers,
                )
                self.assertEqual(stock.status_code, 201, stock.text)
                valuation = await client.post(
                    "/api/v1/inventory/opening-valuations",
                    json={
                        "lines": [{
                            "product_id": product.json()["id"],
                            "warehouse_id": warehouse.json()["id"],
                            "unit_cost": "40.0000",
                        }]
                    },
                    headers=headers,
                )
                self.assertEqual(valuation.status_code, 201, valuation.text)

                serialized_product = await client.post(
                    "/api/v1/products",
                    json={
                        "sku": f"SER-{suffix}",
                        "name": "Serialized invoice item",
                        "category_id": category.json()["id"],
                        "hsn_sac": hsn_code,
                        "unit": "PCS",
                        "purchase_price": "40.00",
                        "selling_price": "100.00",
                        "gst_rate": rate,
                        "cess_rate": "1.00",
                        "is_serialized": True,
                    },
                    headers=headers,
                )
                self.assertEqual(serialized_product.status_code, 201, serialized_product.text)
                with self.factory.begin() as session:
                    available_serial = ProductSerial(
                        product_id=serialized_product.json()["id"],
                        chassis_no=f"CHASSIS-{suffix}",
                        warehouse_id=warehouse.json()["id"],
                        status="available",
                    )
                    unavailable_serial = ProductSerial(
                        product_id=serialized_product.json()["id"],
                        chassis_no=f"SOLD-{suffix}",
                        status="sold",
                    )
                    session.add_all([available_serial, unavailable_serial])
                    session.flush()
                    available_serial_id = available_serial.id
                    unavailable_serial_id = unavailable_serial.id
                    session.add(
                        Inventory(
                            product_id=serialized_product.json()["id"],
                            warehouse_id=warehouse.json()["id"],
                            quantity=Decimal("1"),
                        )
                    )
                    session.add(
                        StockMovement(
                            product_id=serialized_product.json()["id"],
                            warehouse_id=warehouse.json()["id"],
                            serial_id=available_serial_id,
                            movement_type="opening",
                            quantity_change=Decimal("1"),
                            ref_type="integration_fixture",
                        )
                    )
                serial_valuation = await client.post(
                    "/api/v1/inventory/opening-valuations",
                    json={
                        "lines": [{
                            "product_id": serialized_product.json()["id"],
                            "warehouse_id": warehouse.json()["id"],
                            "unit_cost": "40.0000",
                        }]
                    },
                    headers=headers,
                )
                self.assertEqual(serial_valuation.status_code, 201, serial_valuation.text)

                line = {
                    "product_id": product.json()["id"],
                    "warehouse_id": warehouse.json()["id"],
                    "description": "Invoice test item",
                    "hsn_sac": hsn_code,
                    "unit": "PCS",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "discount_pct": "5",
                    "discount_amount": "0",
                    "gst_rate": rate,
                    "cess_rate": "1",
                }
                intra = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "06",
                        "doc_type": "tax_invoice",
                        "lines": [line],
                    },
                    headers=headers,
                )
                self.assertEqual(intra.status_code, 200, intra.text)
                expected_intra = compute_invoice(
                    [LineInput("Invoice test item", hsn_code, "1", "100.00", rate, "5", "0", "1", "PCS")],
                    "06",
                    "06",
                    allowed_rates={Decimal(rate)},
                )
                self.assertFalse(intra.json()["is_inter_state"])
                self.assertEqual(Decimal(intra.json()["cgst_total"]), expected_intra.cgst_total)
                self.assertEqual(Decimal(intra.json()["sgst_total"]), expected_intra.sgst_total)
                self.assertEqual(Decimal(intra.json()["grand_total"]), expected_intra.grand_total)
                self.assertEqual(Decimal(intra.json()["round_off"]), expected_intra.round_off)
                self.assertNotEqual(expected_intra.round_off, Decimal("0"))
                self.assertEqual(intra.json()["amount_in_words"], amount_in_words(expected_intra.grand_total))
                intra_hsn = intra.json()["hsn_summary"][0]
                expected_hsn = expected_intra.hsn_summary[0]
                self.assertEqual(intra_hsn["hsn_sac"], expected_hsn.hsn_sac)
                self.assertEqual(Decimal(intra_hsn["taxable"]), expected_hsn.taxable)
                self.assertEqual(Decimal(intra_hsn["cgst"]), expected_hsn.cgst)
                self.assertEqual(Decimal(intra_hsn["sgst"]), expected_hsn.sgst)

                interstate = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "doc_type": "tax_invoice",
                        "lines": [line],
                    },
                    headers=headers,
                )
                self.assertEqual(interstate.status_code, 200, interstate.text)
                expected_interstate = compute_invoice(
                    [LineInput("Invoice test item", hsn_code, "1", "100.00", rate, "5", "0", "1", "PCS")],
                    "06",
                    "29",
                    allowed_rates={Decimal(rate)},
                )
                self.assertTrue(interstate.json()["is_inter_state"])
                self.assertEqual(Decimal(interstate.json()["igst_total"]), expected_interstate.igst_total)
                self.assertEqual(Decimal(interstate.json()["cgst_total"]), Decimal("0"))
                self.assertEqual(Decimal(interstate.json()["sgst_total"]), Decimal("0"))

                unconfigured_rate = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "lines": [{**line, "gst_rate": "7.13"}],
                    },
                    headers=headers,
                )
                self.assertEqual(unconfigured_rate.status_code, 422)

                bill_of_supply = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "doc_type": "bill_of_supply",
                        "lines": [line],
                    },
                    headers=headers,
                )
                self.assertEqual(bill_of_supply.status_code, 200, bill_of_supply.text)
                self.assertFalse(bill_of_supply.json()["tax_applicable"])
                self.assertEqual(Decimal(bill_of_supply.json()["cgst_total"]), Decimal("0"))
                self.assertEqual(Decimal(bill_of_supply.json()["igst_total"]), Decimal("0"))
                expected_bos = compute_invoice(
                    [LineInput("Invoice test item", hsn_code, "1", "100.00", rate, "5", "0", "1", "PCS")],
                    "06",
                    "29",
                    tax_applicable=False,
                    allowed_rates={Decimal(rate)},
                )
                self.assertEqual(Decimal(bill_of_supply.json()["grand_total"]), expected_bos.grand_total)

                serialized_line = {
                    **line,
                    "product_id": serialized_product.json()["id"],
                    "serial_id": available_serial_id,
                }
                missing_serial = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "lines": [{**serialized_line, "serial_id": None}],
                    },
                    headers=headers,
                )
                self.assertEqual(missing_serial.status_code, 422)
                unavailable_serial_result = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "lines": [{**serialized_line, "serial_id": unavailable_serial_id}],
                    },
                    headers=headers,
                )
                self.assertEqual(unavailable_serial_result.status_code, 422)
                repeated_serial = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "lines": [serialized_line, serialized_line],
                    },
                    headers=headers,
                )
                self.assertEqual(repeated_serial.status_code, 422)
                valid_serial = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "lines": [serialized_line],
                    },
                    headers=headers,
                )
                self.assertEqual(valid_serial.status_code, 200, valid_serial.text)
                with self.factory() as session:
                    self.assertEqual(session.get(ProductSerial, available_serial_id).status, "available")
                serial_draft = await client.post(
                    "/api/v1/invoices",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "doc_type": "tax_invoice",
                        "lines": [serialized_line],
                    },
                    headers=headers,
                )
                self.assertEqual(serial_draft.status_code, 201, serial_draft.text)
                with self.factory() as session:
                    self.assertEqual(session.get(ProductSerial, available_serial_id).status, "available")

                missing_rate = await client.post("/api/v1/invoices/preview", json={
                    "customer_id": customer_id,
                    "place_of_supply_code": "29",
                    "lines": [{**line, "gst_rate": "7.13"}],
                })
                self.assertEqual(missing_rate.status_code, 401)

                with self.factory.begin() as session:
                    role = session.scalar(select(Role).where(Role.name == "Dealer / Distributor"))
                    self.assertIsNotNone(role)
                    dealer_user = User(
                        email=f"dealer-{suffix}@example.test",
                        full_name="Invoice API Dealer Test",
                        password_hash=hash_password(uuid.uuid4().hex),
                        role_id=role.id,
                        must_change_password=False,
                    )
                    session.add(dealer_user)
                    session.flush()
                    dealer_user_id = dealer_user.id
                dealer_headers = {
                    "Authorization": f"Bearer {create_token_pair(self.settings, dealer_user_id).access_token}"
                }
                forbidden = await client.post(
                    "/api/v1/invoices",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "lines": [line],
                    },
                    headers=dealer_headers,
                )
                self.assertEqual(forbidden.status_code, 403)

                with self.factory.begin() as session:
                    update_permission = session.scalar(
                        select(Permission).where(Permission.code == "sales:update")
                    )
                    self.assertIsNotNone(update_permission)
                    update_role = Role(
                        name=f"Invoice Update Test {suffix}",
                        permissions=[update_permission],
                    )
                    session.add(update_role)
                    session.flush()
                    update_user = User(
                        email=f"invoice-update-{suffix}@example.test",
                        full_name="Invoice Update Test",
                        password_hash=hash_password(uuid.uuid4().hex),
                        role_id=update_role.id,
                        must_change_password=False,
                    )
                    session.add(update_user)
                    session.flush()
                    update_user_id = update_user.id
                update_headers = {
                    "Authorization": f"Bearer {create_token_pair(self.settings, update_user_id).access_token}"
                }
                update_readiness = await client.get("/api/v1/settings/readiness", headers=update_headers)
                self.assertEqual(update_readiness.status_code, 200, update_readiness.text)
                update_preview = await client.post(
                    "/api/v1/invoices/preview",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "lines": [line],
                    },
                    headers=update_headers,
                )
                self.assertEqual(update_preview.status_code, 200, update_preview.text)

                created_draft = await client.post(
                    "/api/v1/invoices",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "doc_type": "tax_invoice",
                        "invoice_date": "2026-11-07",
                        "due_date": "2026-12-07",
                        "payment_terms": "Net 30",
                        "notes": "Draft note",
                        "lines": [line],
                    },
                    headers=headers,
                )
                self.assertEqual(created_draft.status_code, 201, created_draft.text)
                draft = created_draft.json()
                draft_id = draft["id"]
                self.assertIsNone(draft["invoice_number"])
                self.assertEqual(draft["status"], "draft")
                self.assertEqual(draft["due_date"], "2026-12-07")
                self.assertEqual(draft["payment_terms"], "Net 30")
                self.assertEqual(draft["notes"], "Draft note")
                self.assertEqual(draft["seller_gstin"], gstin_for("06"))
                self.assertEqual(draft["customer_gstin"], gstin_for("29"))
                self.assertEqual(draft["billing_address"], "Billing snapshot")

                updated_draft = await client.put(
                    f"/api/v1/invoices/{draft_id}",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "06",
                        "doc_type": "proforma",
                        "invoice_date": "2026-11-08",
                        "lines": [line],
                    },
                    headers=headers,
                )
                self.assertEqual(updated_draft.status_code, 200, updated_draft.text)
                self.assertEqual(updated_draft.json()["doc_type"], "proforma")
                self.assertIsNone(updated_draft.json()["invoice_number"])
                for action in ("approve", "issue", "cancel"):
                    response = await client.post(
                        f"/api/v1/invoices/{draft_id}/{action}",
                        json={"reason": "Not applicable for proforma"},
                        headers=headers,
                    )
                    self.assertEqual(response.status_code, 409, response.text)
                converted = await client.post(
                    f"/api/v1/invoices/{draft_id}/convert-to-tax-invoice",
                    headers=headers,
                )
                self.assertEqual(converted.status_code, 200, converted.text)
                self.assertEqual(converted.json()["doc_type"], "tax_invoice")
                self.assertEqual(converted.json()["status"], "draft")
                self.assertIsNone(converted.json()["invoice_number"])
                with self.factory() as session:
                    self.assertEqual(session.get(ProductSerial, available_serial_id).status, "available")
                    self.assertEqual(
                        session.scalar(
                            text(
                                "SELECT count(*) FROM stock_movements "
                                "WHERE ref_type = 'invoice' AND ref_id = :id"
                            ),
                            {"id": converted.json()["id"]},
                        ),
                        0,
                    )
                    draft_actions = session.scalars(
                        select(AuditLog.action).where(
                            AuditLog.entity_type == "invoice",
                            AuditLog.entity_id == draft_id,
                        )
                    ).all()
                    self.assertIn("create_draft", draft_actions)
                    self.assertIn("update_draft", draft_actions)
                    self.assertIn(
                        "convert_proforma",
                        session.scalars(
                            select(AuditLog.action).where(
                                AuditLog.entity_type == "invoice",
                                AuditLog.entity_id == converted.json()["id"],
                            )
                        ).all(),
                    )

                disposable_draft = await client.post(
                    "/api/v1/invoices",
                    json={"customer_id": customer_id, "place_of_supply_code": "29", "lines": [line]},
                    headers=headers,
                )
                self.assertEqual(disposable_draft.status_code, 201, disposable_draft.text)
                deleted_draft = await client.delete(
                    f"/api/v1/invoices/{disposable_draft.json()['id']}",
                    headers=headers,
                )
                self.assertEqual(deleted_draft.status_code, 204, deleted_draft.text)
                with self.factory() as session:
                    self.assertIn(
                        "delete_draft",
                        session.scalars(
                            select(AuditLog.action).where(
                                AuditLog.entity_type == "invoice",
                                AuditLog.entity_id == disposable_draft.json()["id"],
                            )
                        ).all(),
                    )

                issue_draft = await client.post(
                    "/api/v1/invoices",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "invoice_date": date.today().isoformat(),
                        "lines": [line],
                    },
                    headers=headers,
                )
                self.assertEqual(issue_draft.status_code, 201, issue_draft.text)
                issue_id = issue_draft.json()["id"]
                unauthenticated_issue = await client.post(f"/api/v1/invoices/{issue_id}/issue")
                self.assertEqual(unauthenticated_issue.status_code, 401)
                update_only_issue = await client.post(f"/api/v1/invoices/{issue_id}/issue", headers=update_headers)
                self.assertEqual(update_only_issue.status_code, 403)
                approved = await client.post(f"/api/v1/invoices/{issue_id}/approve", headers=headers)
                self.assertEqual(approved.status_code, 200, approved.text)
                unauthenticated_approve = await client.post(f"/api/v1/invoices/{issue_id}/approve")
                self.assertEqual(unauthenticated_approve.status_code, 401)
                update_only_approve = await client.post(f"/api/v1/invoices/{issue_id}/approve", headers=update_headers)
                self.assertEqual(update_only_approve.status_code, 403)
                issued = await client.post(
                    f"/api/v1/invoices/{issue_id}/issue",
                    headers={**headers, "Idempotency-Key": f"issue-{suffix}"},
                )
                self.assertEqual(issued.status_code, 200, issued.text)
                invoice_number = issued.json()["invoice_number"]
                self.assertIsNotNone(invoice_number)
                self.assertLessEqual(len(invoice_number), 16)
                repeated_issue = await client.post(f"/api/v1/invoices/{issue_id}/issue", headers=headers)
                self.assertEqual(repeated_issue.status_code, 409)
                self.assertIn(invoice_number, repeated_issue.text)
                unauthenticated_cancel = await client.post(
                    f"/api/v1/invoices/{issue_id}/cancel",
                    json={"reason": "Missing authentication"},
                )
                self.assertEqual(unauthenticated_cancel.status_code, 401)
                update_only_cancel = await client.post(
                    f"/api/v1/invoices/{issue_id}/cancel",
                    json={"reason": "Missing cancel permission"},
                    headers=update_headers,
                )
                self.assertEqual(update_only_cancel.status_code, 403)
                with self.factory() as session:
                    inventory = session.get(Inventory, (product.json()["id"], warehouse.json()["id"]))
                    self.assertEqual(inventory.quantity, Decimal("9"))
                    self.assertEqual(
                        session.scalar(
                            select(JournalEntry.id).where(
                                JournalEntry.source_type == "invoice",
                                JournalEntry.source_id == issue_id,
                                JournalEntry.reverses_entry_id.is_(None),
                            ).limit(1)
                        ) is not None,
                        True,
                    )
                    posted_accounts = {
                        account: (Decimal(debit), Decimal(credit))
                        for account, debit, credit in session.execute(text(
                            "SELECT jel.account_code, sum(jel.debit), sum(jel.credit) "
                            "FROM journal_entry_lines jel JOIN journal_entries je ON je.id = jel.entry_id "
                            "WHERE je.source_type = 'invoice' AND je.source_id = :id "
                            "GROUP BY jel.account_code"
                        ), {"id": issue_id}).all()
                    }
                    self.assertEqual(posted_accounts["1100"], (Decimal("113.00"), Decimal("0")))
                    self.assertEqual(posted_accounts["4000"], (Decimal("0"), Decimal("95.00")))
                    self.assertEqual(posted_accounts["2102"], (Decimal("0"), Decimal("17.10")))
                    self.assertEqual(posted_accounts["2103"], (Decimal("0"), Decimal("0.95")))
                    self.assertEqual(posted_accounts["4900"], (Decimal("0.05"), Decimal("0")))
                    self.assertEqual(posted_accounts["5000"], (Decimal("40.00"), Decimal("0")))
                    self.assertEqual(posted_accounts["1200"], (Decimal("0"), Decimal("40.00")))
                    self.assertEqual(
                        sum((debit for debit, _credit in posted_accounts.values()), Decimal("0")),
                        sum((credit for _debit, credit in posted_accounts.values()), Decimal("0")),
                    )
                    self.assertIn(
                        "issue",
                        session.scalars(
                            select(AuditLog.action).where(
                                AuditLog.entity_type == "invoice",
                                AuditLog.entity_id == issue_id,
                            )
                        ).all(),
                    )
                pdf = await client.get(f"/api/v1/invoices/{issue_id}/pdf", headers=headers)
                self.assertEqual(pdf.status_code, 200, pdf.text)
                self.assertEqual(pdf.headers["content-type"], "application/pdf")
                pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf.content)).pages)
                self.assertIn(invoice_number, pdf_text)
                self.assertIn(gstin_for("06"), pdf_text)
                self.assertIn(amount_in_words(Decimal(issued.json()["grand_total"])), pdf_text)
                with self.factory() as session:
                    self.assertIn(
                        "download_pdf",
                        session.scalars(
                            select(AuditLog.action).where(
                                AuditLog.entity_type == "invoice",
                                AuditLog.entity_id == issue_id,
                            )
                        ).all(),
                    )

                many_line_body = {
                    "customer_id": customer_id,
                    "place_of_supply_code": "29",
                    "invoice_date": date.today().isoformat(),
                    "lines": [
                        {
                            "description": f"PDF line {index}",
                            "hsn_sac": hsn_code,
                            "unit": "PCS",
                            "quantity": "1",
                            "unit_price": "1.00",
                            "discount_pct": "0",
                            "discount_amount": "0",
                            "gst_rate": rate,
                            "cess_rate": "0",
                        }
                        for index in range(80)
                    ],
                }
                empty_bank_settings = {
                    **settings_payload,
                    "bank_name": None,
                    "bank_account_no": None,
                    "bank_ifsc": None,
                    "upi_id": None,
                }
                saved_empty_bank_settings = await client.put(
                    "/api/v1/settings",
                    json=empty_bank_settings,
                    headers=headers,
                )
                self.assertEqual(saved_empty_bank_settings.status_code, 200, saved_empty_bank_settings.text)
                many_line_draft = await client.post("/api/v1/invoices", json=many_line_body, headers=headers)
                self.assertEqual(many_line_draft.status_code, 201, many_line_draft.text)
                many_line_id = many_line_draft.json()["id"]
                many_line_approval = await client.post(f"/api/v1/invoices/{many_line_id}/approve", headers=headers)
                self.assertEqual(many_line_approval.status_code, 200, many_line_approval.text)
                many_line_issue = await client.post(f"/api/v1/invoices/{many_line_id}/issue", headers=headers)
                self.assertEqual(many_line_issue.status_code, 200, many_line_issue.text)
                many_line_pdf = await client.get(f"/api/v1/invoices/{many_line_id}/pdf", headers=headers)
                self.assertEqual(many_line_pdf.status_code, 200, many_line_pdf.text)
                many_line_reader = PdfReader(BytesIO(many_line_pdf.content))
                self.assertGreater(len(many_line_reader.pages), 1)
                many_line_text = "\n".join(page.extract_text() or "" for page in many_line_reader.pages)
                self.assertIn("Description / HSN", many_line_text)
                self.assertNotIn(f"TEST-SECRET-{suffix}", many_line_text)
                self.assertNotIn("TESTIFSC0001", many_line_text)
                self.assertNotIn("Bank:", many_line_text)
                many_line_cancel = await client.post(
                    f"/api/v1/invoices/{many_line_id}/cancel",
                    json={"reason": "Multi page PDF test cleanup"},
                    headers=headers,
                )
                self.assertEqual(many_line_cancel.status_code, 200, many_line_cancel.text)

                with self.factory.begin() as session:
                    invoice_triggers = set(session.scalars(text(
                        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgrelid IN ('invoices'::regclass, 'invoice_items'::regclass)"
                    )).all())
                self.assertTrue({"invoices_guard", "invoice_items_guard"}.issubset(invoice_triggers))

                with self.factory() as session:
                    with self.assertRaises(DBAPIError):
                        with session.begin_nested():
                            session.execute(
                                text("UPDATE invoices SET notes = 'direct edit' WHERE id = :id"),
                                {"id": issue_id},
                            )
                    with self.assertRaises(DBAPIError):
                        with session.begin_nested():
                            session.execute(
                                text("UPDATE invoice_items SET description = description || ' changed' WHERE invoice_id = :id"),
                                {"id": issue_id},
                            )
                    with self.assertRaises(DBAPIError):
                        with session.begin_nested():
                            session.execute(
                                text("DELETE FROM journal_entry_lines WHERE entry_id IN "
                                     "(SELECT id FROM journal_entries WHERE source_type = 'invoice' AND source_id = :id)"),
                                {"id": issue_id},
                            )
                    with self.assertRaises(DBAPIError):
                        with session.begin_nested():
                            session.execute(
                                text("UPDATE invoices SET status = 'issued', invoice_number = :number WHERE id = :id"),
                                {"number": f"SQL-{suffix}", "id": converted.json()["id"]},
                            )
                            session.execute(
                                text("UPDATE invoices SET notes = 'direct edit' WHERE id = :id"),
                                {"id": converted.json()["id"]},
                            )
                    session.rollback()

                cancelled = await client.post(
                    f"/api/v1/invoices/{issue_id}/cancel",
                    json={"reason": "Customer requested cancellation"},
                    headers=headers,
                )
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(cancelled.json()["status"], "cancelled")
                with self.factory() as session:
                    self.assertIn(
                        "cancel",
                        session.scalars(
                            select(AuditLog.action).where(
                                AuditLog.entity_type == "invoice",
                                AuditLog.entity_id == issue_id,
                            )
                        ).all(),
                    )
                    cancelled_account_balances = session.execute(
                        text(
                            "SELECT line.account_code, sum(line.debit), sum(line.credit) "
                            "FROM journal_entry_lines line "
                            "JOIN journal_entries entry ON entry.id = line.entry_id "
                            "WHERE entry.source_type = 'invoice' AND entry.source_id = :invoice_id "
                            "GROUP BY line.account_code"
                        ),
                        {"invoice_id": issue_id},
                    ).all()
                    self.assertTrue(cancelled_account_balances)
                    for _account, debit, credit in cancelled_account_balances:
                        self.assertEqual(Decimal(debit), Decimal(credit))
                    self.assertGreater(
                        session.scalar(
                            select(func.count(JournalEntry.id)).where(
                                JournalEntry.source_type == "invoice",
                                JournalEntry.source_id == issue_id,
                                JournalEntry.reverses_entry_id.is_not(None),
                            )
                        ),
                        0,
                    )
                cancel_twice = await client.post(
                    f"/api/v1/invoices/{issue_id}/cancel",
                    json={"reason": "Cancel it one more time"},
                    headers=headers,
                )
                self.assertEqual(cancel_twice.status_code, 409)
                cancelled_pdf = await client.get(f"/api/v1/invoices/{issue_id}/pdf", headers=headers)
                self.assertEqual(cancelled_pdf.status_code, 200, cancelled_pdf.text)
                cancelled_pdf_text = "\n".join(
                    page.extract_text() or "" for page in PdfReader(BytesIO(cancelled_pdf.content)).pages
                )
                self.assertIn("CANCELLED", cancelled_pdf_text)
                with self.factory() as session:
                    inventory = session.get(Inventory, (product.json()["id"], warehouse.json()["id"]))
                    self.assertEqual(inventory.quantity, Decimal("10"))
                    self.assertEqual(
                        sum(
                            (row.quantity_change for row in session.scalars(
                                select(StockMovement).where(
                                    StockMovement.ref_type == "invoice",
                                    StockMovement.ref_id == issue_id,
                                )
                            ).all()),
                            Decimal("0"),
                        ),
                        Decimal("0"),
                    )

                intra_draft = await client.post(
                    "/api/v1/invoices",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "06",
                        "invoice_date": date.today().isoformat(),
                        "lines": [line],
                    },
                    headers=headers,
                )
                self.assertEqual(intra_draft.status_code, 201, intra_draft.text)
                intra_id = intra_draft.json()["id"]
                intra_approved = await client.post(f"/api/v1/invoices/{intra_id}/approve", headers=headers)
                self.assertEqual(intra_approved.status_code, 200, intra_approved.text)
                intra_issued = await client.post(f"/api/v1/invoices/{intra_id}/issue", headers=headers)
                self.assertEqual(intra_issued.status_code, 200, intra_issued.text)
                with self.factory() as session:
                    intra_accounts = {
                        account: (Decimal(debit), Decimal(credit))
                        for account, debit, credit in session.execute(text(
                            "SELECT jel.account_code, sum(jel.debit), sum(jel.credit) "
                            "FROM journal_entry_lines jel JOIN journal_entries je ON je.id = jel.entry_id "
                            "WHERE je.source_type = 'invoice' AND je.source_id = :id "
                            "GROUP BY jel.account_code"
                        ), {"id": intra_id}).all()
                    }
                    self.assertEqual(intra_accounts["2100"], (Decimal("0"), Decimal("8.55")))
                    self.assertEqual(intra_accounts["2101"], (Decimal("0"), Decimal("8.55")))
                    self.assertNotIn("2102", intra_accounts)
                intra_cancel = await client.post(
                    f"/api/v1/invoices/{intra_id}/cancel",
                    json={"reason": "Intra-state posting test cleanup"},
                    headers=headers,
                )
                self.assertEqual(intra_cancel.status_code, 200, intra_cancel.text)

                concurrent_draft = await client.post(
                    "/api/v1/invoices",
                    json={"customer_id": customer_id, "place_of_supply_code": "29", "lines": [line]},
                    headers=headers,
                )
                self.assertEqual(concurrent_draft.status_code, 201, concurrent_draft.text)
                concurrent_id = concurrent_draft.json()["id"]
                concurrent_approval = await client.post(f"/api/v1/invoices/{concurrent_id}/approve", headers=headers)
                self.assertEqual(concurrent_approval.status_code, 200, concurrent_approval.text)
                same_draft_responses = await asyncio.gather(*[
                    client.post(f"/api/v1/invoices/{concurrent_id}/issue", headers=headers)
                    for _ in range(5)
                ])
                self.assertEqual(sum(response.status_code == 200 for response in same_draft_responses), 1)
                self.assertEqual(sum(response.status_code == 409 for response in same_draft_responses), 4)
                concurrent_number = next(response.json()["invoice_number"] for response in same_draft_responses if response.status_code == 200)
                concurrent_cancel = await client.post(
                    f"/api/v1/invoices/{concurrent_id}/cancel",
                    json={"reason": "Parallel request test cleanup"},
                    headers=headers,
                )
                self.assertEqual(concurrent_cancel.status_code, 200, concurrent_cancel.text)

                adjustment = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={"product_id": product.json()["id"], "warehouse_id": warehouse.json()["id"], "quantity_delta": "-9"},
                    headers=headers,
                )
                self.assertEqual(adjustment.status_code, 201, adjustment.text)
                competing_drafts = []
                for _ in range(2):
                    competing = await client.post(
                        "/api/v1/invoices",
                        json={"customer_id": customer_id, "place_of_supply_code": "29", "lines": [line]},
                        headers=headers,
                    )
                    self.assertEqual(competing.status_code, 201, competing.text)
                    competing_approved = await client.post(
                        f"/api/v1/invoices/{competing.json()['id']}/approve",
                        headers=headers,
                    )
                    self.assertEqual(competing_approved.status_code, 200, competing_approved.text)
                    competing_drafts.append(competing.json()["id"])
                competing_results = await asyncio.gather(*[
                    client.post(f"/api/v1/invoices/{candidate}/issue", headers=headers)
                    for candidate in competing_drafts
                ])
                self.assertEqual(sum(response.status_code == 200 for response in competing_results), 1)
                shortage = next(response for response in competing_results if response.status_code == 409)
                self.assertIn("shortages", shortage.json()["detail"])
                winner_id = competing_drafts[next(index for index, response in enumerate(competing_results) if response.status_code == 200)]
                with self.factory() as session:
                    self.assertEqual(
                        session.get(Inventory, (product.json()["id"], warehouse.json()["id"])).quantity,
                        Decimal("0"),
                    )
                winner_cancel = await client.post(
                    f"/api/v1/invoices/{winner_id}/cancel",
                    json={"reason": "Last unit test cleanup"},
                    headers=headers,
                )
                self.assertEqual(winner_cancel.status_code, 200, winner_cancel.text)
                restored = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={"product_id": product.json()["id"], "warehouse_id": warehouse.json()["id"], "quantity_delta": "9"},
                    headers=headers,
                )
                self.assertEqual(restored.status_code, 201, restored.text)

                failed_draft = await client.post(
                    "/api/v1/invoices",
                    json={"customer_id": customer_id, "place_of_supply_code": "29", "lines": [line]},
                    headers=headers,
                )
                self.assertEqual(failed_draft.status_code, 201, failed_draft.text)
                failed_id = failed_draft.json()["id"]
                failed_approval = await client.post(f"/api/v1/invoices/{failed_id}/approve", headers=headers)
                self.assertEqual(failed_approval.status_code, 200, failed_approval.text)
                with self.factory() as session:
                    before_stock = session.get(Inventory, (product.json()["id"], warehouse.json()["id"])).quantity
                    before_sequence = session.scalar(text(
                        "SELECT coalesce(max(last_number), 0) FROM number_sequences WHERE series LIKE 'tax_invoice:TST:%'"
                    ))
                    before_movements = session.scalar(text(
                        "SELECT count(*) FROM stock_movements WHERE ref_type = 'invoice' AND ref_id = :id"
                    ), {"id": failed_id})
                    before_journals = session.scalar(text(
                        "SELECT count(*) FROM journal_entries WHERE source_type = 'invoice' AND source_id = :id"
                    ), {"id": failed_id})
                with patch("app.api.v1.invoices.post_sales_invoice", side_effect=RuntimeError("injected posting failure")):
                    async with AsyncClient(
                        transport=ASGITransport(app=self.app, raise_app_exceptions=False),
                        base_url="http://testserver",
                    ) as failing_client:
                        failed_issue = await failing_client.post(f"/api/v1/invoices/{failed_id}/issue", headers=headers)
                self.assertEqual(failed_issue.status_code, 500)
                with self.factory() as session:
                    self.assertEqual(session.get(Inventory, (product.json()["id"], warehouse.json()["id"])).quantity, before_stock)
                    failed_invoice = session.get(Invoice, failed_id)
                    self.assertEqual(failed_invoice.status, "approved")
                    self.assertIsNone(failed_invoice.invoice_number)
                    self.assertEqual(session.scalar(text(
                        "SELECT coalesce(max(last_number), 0) FROM number_sequences WHERE series LIKE 'tax_invoice:TST:%'"
                    )), before_sequence)
                    self.assertEqual(session.scalar(text(
                        "SELECT count(*) FROM stock_movements WHERE ref_type = 'invoice' AND ref_id = :id"
                    ), {"id": failed_id}), before_movements)
                    self.assertEqual(session.scalar(text(
                        "SELECT count(*) FROM journal_entries WHERE source_type = 'invoice' AND source_id = :id"
                    ), {"id": failed_id}), before_journals)

                failed_serial_draft = await client.post(
                    "/api/v1/invoices",
                    json={
                        "customer_id": customer_id,
                        "place_of_supply_code": "29",
                        "lines": [serialized_line],
                    },
                    headers=headers,
                )
                self.assertEqual(failed_serial_draft.status_code, 201, failed_serial_draft.text)
                failed_serial_id = failed_serial_draft.json()["id"]
                failed_serial_approval = await client.post(
                    f"/api/v1/invoices/{failed_serial_id}/approve",
                    headers=headers,
                )
                self.assertEqual(failed_serial_approval.status_code, 200, failed_serial_approval.text)
                with self.factory() as session:
                    serial_sequence_before = session.scalar(text(
                        "SELECT coalesce(max(last_number), 0) FROM number_sequences WHERE series LIKE 'tax_invoice:TST:%'"
                    ))
                    serial_movements_before = session.scalar(text(
                        "SELECT count(*) FROM stock_movements WHERE ref_type = 'invoice' AND ref_id = :id"
                    ), {"id": failed_serial_id})
                with patch("app.api.v1.invoices.post_sales_invoice", side_effect=RuntimeError("injected serial posting failure")):
                    async with AsyncClient(
                        transport=ASGITransport(app=self.app, raise_app_exceptions=False),
                        base_url="http://testserver",
                    ) as failing_client:
                        failed_serial_issue = await failing_client.post(
                            f"/api/v1/invoices/{failed_serial_id}/issue",
                            headers=headers,
                        )
                self.assertEqual(failed_serial_issue.status_code, 500)
                with self.factory() as session:
                    failed_serial_invoice = session.get(Invoice, failed_serial_id)
                    self.assertEqual(failed_serial_invoice.status, "approved")
                    self.assertIsNone(failed_serial_invoice.invoice_number)
                    self.assertEqual(session.get(ProductSerial, available_serial_id).status, "available")
                    self.assertEqual(
                        session.get(Inventory, (serialized_product.json()["id"], warehouse.json()["id"])).quantity,
                        Decimal("1"),
                    )
                    self.assertEqual(session.scalar(text(
                        "SELECT coalesce(max(last_number), 0) FROM number_sequences WHERE series LIKE 'tax_invoice:TST:%'"
                    )), serial_sequence_before)
                    self.assertEqual(session.scalar(text(
                        "SELECT count(*) FROM stock_movements WHERE ref_type = 'invoice' AND ref_id = :id"
                    ), {"id": failed_serial_id}), serial_movements_before)
                    self.assertEqual(session.scalar(text(
                        "SELECT count(*) FROM journal_entries WHERE source_type = 'invoice' AND source_id = :id"
                    ), {"id": failed_serial_id}), 0)

                parallel_drafts = []
                for _ in range(20):
                    parallel_draft = await client.post(
                        "/api/v1/invoices",
                        json={"customer_id": customer_id, "place_of_supply_code": "29", "lines": [line]},
                        headers=headers,
                    )
                    self.assertEqual(parallel_draft.status_code, 201, parallel_draft.text)
                    parallel_id = parallel_draft.json()["id"]
                    parallel_approval = await client.post(f"/api/v1/invoices/{parallel_id}/approve", headers=headers)
                    self.assertEqual(parallel_approval.status_code, 200, parallel_approval.text)
                    parallel_drafts.append(parallel_id)
                extra_stock = await client.post(
                    "/api/v1/inventory/adjustments",
                    json={"product_id": product.json()["id"], "warehouse_id": warehouse.json()["id"], "quantity_delta": "20"},
                    headers=headers,
                )
                self.assertEqual(extra_stock.status_code, 201, extra_stock.text)
                parallel_issues = await asyncio.gather(*[
                    client.post(f"/api/v1/invoices/{candidate}/issue", headers=headers)
                    for candidate in parallel_drafts
                ])
                self.assertTrue(all(response.status_code == 200 for response in parallel_issues), [response.text for response in parallel_issues if response.status_code != 200])
                parallel_numbers = sorted(
                    int(response.json()["invoice_number"].rsplit("/", 1)[1])
                    for response in parallel_issues
                )
                self.assertEqual(len(set(parallel_numbers)), 20)
                self.assertEqual(parallel_numbers, list(range(parallel_numbers[0], parallel_numbers[0] + 20)))
                for invoice_id in parallel_drafts:
                    parallel_cancel = await client.post(
                        f"/api/v1/invoices/{invoice_id}/cancel",
                        json={"reason": "Concurrency test cleanup"},
                        headers=headers,
                    )
                    self.assertEqual(parallel_cancel.status_code, 200, parallel_cancel.text)

                serial_issue_id = serial_draft.json()["id"]
                serial_approved = await client.post(f"/api/v1/invoices/{serial_issue_id}/approve", headers=headers)
                self.assertEqual(serial_approved.status_code, 200, serial_approved.text)
                serial_issued = await client.post(f"/api/v1/invoices/{serial_issue_id}/issue", headers=headers)
                self.assertEqual(serial_issued.status_code, 200, serial_issued.text)
                self.assertNotEqual(serial_issued.json()["invoice_number"], invoice_number)
                with self.factory() as session:
                    self.assertEqual(session.get(ProductSerial, available_serial_id).status, "sold")
                    self.assertEqual(
                        session.get(Inventory, (serialized_product.json()["id"], warehouse.json()["id"])).quantity,
                        Decimal("0"),
                    )
                serial_cancelled = await client.post(
                    f"/api/v1/invoices/{serial_issue_id}/cancel",
                    json={"reason": "Serialized product return"},
                    headers=headers,
                )
                self.assertEqual(serial_cancelled.status_code, 200, serial_cancelled.text)
                with self.factory() as session:
                    self.assertEqual(session.get(ProductSerial, available_serial_id).status, "available")
                    self.assertEqual(
                        session.get(Inventory, (serialized_product.json()["id"], warehouse.json()["id"])).quantity,
                        Decimal("1"),
                    )

                deleted_customer = await client.delete(f"/api/v1/customers/{customer_id}", headers=headers)
                self.assertEqual(deleted_customer.status_code, 204)
                historical_invoice = await client.get(f"/api/v1/invoices/{draft_id}", headers=headers)
                self.assertEqual(historical_invoice.status_code, 200, historical_invoice.text)
                self.assertEqual(historical_invoice.json()["customer_id"], customer_id)

                draft_for_delete = await client.post(
                    "/api/v1/invoices",
                    json={
                        "customer_id": (await client.post(
                            "/api/v1/customers",
                            json={
                                **customer_one_payload,
                                "code": f"GST-{suffix}-B",
                                "name": "Draft delete customer",
                            },
                            headers=headers,
                        )).json()["id"],
                        "place_of_supply_code": "29",
                        "doc_type": "bill_of_supply",
                        "lines": [line],
                    },
                    headers=headers,
                )
                self.assertEqual(draft_for_delete.status_code, 201, draft_for_delete.text)
                deleted_draft = await client.delete(f"/api/v1/invoices/{draft_for_delete.json()['id']}", headers=headers)
                self.assertEqual(deleted_draft.status_code, 204)

        asyncio.run(exercise_api())


if __name__ == "__main__":
    unittest.main()
