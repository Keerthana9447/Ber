import asyncio
import unittest
import uuid
from datetime import date
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.auth.security import create_token_pair, hash_password
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.models import (
    CompanySettings,
    Customer,
    Dealer,
    DealerPrice,
    GstRate,
    HsnSacCode,
    Invoice,
    Payment,
    PaymentMethod,
    Permission,
    Product,
    ProductCategory,
    Role,
    SalesOrder,
    Unit,
    User,
)
from app.seed import seed_reference_data
from tests.database_test_config import (
    configured_test_database_url,
    database_tests_required,
    require_test_database_url,
)


@unittest.skipUnless(
    configured_test_database_url() is not None or database_tests_required(),
    "Set TEST_DATABASE_URL or BEREV_TEST_DATABASE_URL to run PostgreSQL API integration tests",
)
class DealerPortalWorkflowTests(unittest.TestCase):
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
                "Dealer portal integration tests require the isolated berev_test database."
            ) from error
        cls.factory = cls.app.state.session_factory
        with cls.factory.begin() as session:
            seed_reference_data(session)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.engine.dispose()

    def test_server_pricing_status_and_dealer_isolation(self):
        suffix = uuid.uuid4().hex[:8].upper()
        extra_permissions = {
            "sales:create",
            "sales:update",
            "sales:approve",
            "sales:issue",
            "sales:cancel",
            "payments:manage",
            "customers:read",
        }
        with self.factory.begin() as session:
            role = session.scalar(select(Role).where(Role.name == "Dealer / Distributor"))
            self.assertIsNotNone(role)
            original_permission_codes = {permission.code for permission in role.permissions}
            permissions = {
                permission.code: permission
                for permission in session.scalars(
                    select(Permission).where(Permission.code.in_(extra_permissions))
                ).all()
            }
            self.assertEqual(set(permissions), extra_permissions)
            role.permissions.extend(
                permissions[code] for code in extra_permissions if code not in original_permission_codes
            )

            settings = session.get(CompanySettings, 1)
            if settings is None:
                settings = CompanySettings(
                    legal_name="BEREV Dealer API Test",
                    state_code="27",
                    invoice_prefix="DLT",
                    fy_start_month=4,
                )
                session.add(settings)
            else:
                settings.state_code = "27"

            rate = session.scalar(
                select(GstRate).where(GstRate.is_active.is_(True)).order_by(GstRate.rate).limit(1)
            )
            hsn = session.scalar(
                select(HsnSacCode)
                .where(HsnSacCode.kind == "HSN", HsnSacCode.is_active.is_(True))
                .order_by(HsnSacCode.code)
                .limit(1)
            )
            unit = session.get(Unit, "PCS")
            payment_method = session.scalar(
                select(PaymentMethod)
                .where(PaymentMethod.is_active.is_(True))
                .order_by(PaymentMethod.id)
                .limit(1)
            )
            self.assertIsNotNone(rate)
            self.assertIsNotNone(hsn)
            self.assertIsNotNone(unit)
            self.assertIsNotNone(payment_method)

            customer_a = Customer(
                code=f"PORTAL-A-{suffix}",
                name=f"Dealer A {suffix}",
                customer_type="dealer",
                state_code="27",
            )
            customer_b = Customer(
                code=f"PORTAL-B-{suffix}",
                name=f"Dealer B {suffix}",
                customer_type="dealer",
                state_code="27",
            )
            category = ProductCategory(name=f"Dealer API {suffix}")
            session.add_all([customer_a, customer_b, category])
            session.flush()
            dealer_a = Dealer(dealer_code=f"PORTAL-A-{suffix}", customer_id=customer_a.id, status="active")
            dealer_b = Dealer(dealer_code=f"PORTAL-B-{suffix}", customer_id=customer_b.id, status="active")
            session.add_all([dealer_a, dealer_b])
            session.flush()
            product = Product(
                sku=f"PORTAL-{suffix}",
                name=f"Dealer portal product {suffix}",
                category_id=category.id,
                hsn_sac=hsn.code,
                unit=unit.code,
                purchase_price=Decimal("70.00"),
                selling_price=Decimal("150.00"),
                gst_rate=rate.rate,
                cess_rate=Decimal("0"),
            )
            session.add(product)
            session.flush()
            unpriced_product = Product(
                sku=f"PORTAL-NP-{suffix}",
                name=f"Unpriced dealer portal product {suffix}",
                category_id=category.id,
                hsn_sac=hsn.code,
                unit=unit.code,
                purchase_price=Decimal("70.00"),
                selling_price=Decimal("150.00"),
                gst_rate=rate.rate,
                cess_rate=Decimal("0"),
            )
            session.add(unpriced_product)
            session.flush()
            session.add_all([
                DealerPrice(dealer_id=dealer_a.id, product_id=product.id, price=Decimal("123.45")),
                DealerPrice(dealer_id=dealer_b.id, product_id=product.id, price=Decimal("678.90")),
            ])
            user = User(
                email=f"dealer-a-{suffix.lower()}@example.test",
                full_name="Dealer A portal test user",
                password_hash=hash_password(uuid.uuid4().hex),
                role_id=role.id,
                dealer_id=dealer_a.id,
                must_change_password=False,
            )
            order_a = SalesOrder(
                number=f"PORTAL-SO-A-{suffix}",
                customer_id=customer_a.id,
                order_date=date.today(),
                place_of_supply_code="27",
                status="draft",
            )
            order_b = SalesOrder(
                number=f"PORTAL-SO-B-{suffix}",
                customer_id=customer_b.id,
                order_date=date.today(),
                place_of_supply_code="27",
                status="draft",
            )
            invoice_a = Invoice(
                doc_type="tax_invoice",
                invoice_number=f"PORTAL-INV-A-{suffix}",
                customer_id=customer_a.id,
                customer_name=customer_a.name,
                invoice_date=date.today(),
                place_of_supply_code="27",
                is_inter_state=False,
                status="issued",
                taxable_total=Decimal("100.00"),
                grand_total=Decimal("100.00"),
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
            session.add_all([user, order_a, order_b, invoice_a, invoice_b])
            session.flush()
            payment_a = Payment(
                payment_number=f"PORTAL-PAY-A-{suffix}",
                direction="in",
                customer_id=customer_a.id,
                method_id=payment_method.id,
                amount=Decimal("25.00"),
                payment_date=date.today(),
                status="recorded",
            )
            payment_b = Payment(
                payment_number=f"PORTAL-PAY-B-{suffix}",
                direction="in",
                customer_id=customer_b.id,
                method_id=payment_method.id,
                amount=Decimal("30.00"),
                payment_date=date.today(),
                status="recorded",
            )
            session.add_all([payment_a, payment_b])
            session.flush()
            user_id = user.id
            customer_a_id, customer_b_id = customer_a.id, customer_b.id
            product_id, unpriced_product_id = product.id, unpriced_product.id
            payment_method_id = payment_method.id
            order_a_id, order_b_id = order_a.id, order_b.id
            invoice_b_id = invoice_b.id
            payment_b_id = payment_b.id

        headers = {
            "Authorization": f"Bearer {create_token_pair(self.settings, user_id).access_token}"
        }

        async def exercise_api():
            async with AsyncClient(
                transport=ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                catalog = await client.get("/api/v1/dealer/portal/products", headers=headers)
                self.assertEqual(catalog.status_code, 200, catalog.text)
                self.assertEqual(
                    {item["product_id"] for item in catalog.json()["items"]},
                    {product_id},
                )
                unpriced_order = await client.post(
                    "/api/v1/dealer/portal/orders",
                    headers=headers,
                    json={"lines": [{"product_id": unpriced_product_id, "quantity": "1"}]},
                )
                self.assertEqual(unpriced_order.status_code, 409, unpriced_order.text)
                spoofed_price = await client.post(
                    "/api/v1/dealer/portal/orders",
                    headers=headers,
                    json={"lines": [{"product_id": product_id, "quantity": "2", "unit_price": "0.01"}]},
                )
                self.assertEqual(spoofed_price.status_code, 422, spoofed_price.text)

                created = await client.post(
                    "/api/v1/dealer/portal/orders",
                    headers=headers,
                    json={"lines": [{"product_id": product_id, "quantity": "2"}]},
                )
                self.assertEqual(created.status_code, 201, created.text)
                order = created.json()
                self.assertEqual(order["customer_name"], f"Dealer A {suffix}")
                self.assertEqual(order["status"], "draft")
                self.assertEqual(order["lines"][0]["unit_price"], "123.4500")
                self.assertNotEqual(order["lines"][0]["unit_price"], "0.0100")

                portal_orders = await client.get("/api/v1/dealer/portal/orders", headers=headers)
                self.assertEqual(portal_orders.status_code, 200, portal_orders.text)
                portal_ids = {row["id"] for row in portal_orders.json()["items"]}
                self.assertIn(order_a_id, portal_ids)
                self.assertIn(order["id"], portal_ids)
                self.assertNotIn(order_b_id, portal_ids)
                self.assertEqual(
                    (await client.get(f"/api/v1/dealer/portal/orders/{order_b_id}", headers=headers)).status_code,
                    404,
                )

                generic_order_list = await client.get("/api/v1/orders", headers=headers)
                self.assertEqual(generic_order_list.status_code, 200, generic_order_list.text)
                generic_order_ids = {row["id"] for row in generic_order_list.json()["items"]}
                self.assertNotIn(order_b_id, generic_order_ids)
                for path in (
                    f"/api/v1/orders/{order_b_id}",
                    f"/api/v1/orders/{order_b_id}/confirm",
                    f"/api/v1/orders/{order_b_id}/cancel",
                ):
                    response = await (
                        client.get(path, headers=headers)
                        if path.endswith(f"/{order_b_id}")
                        else client.post(path, headers=headers)
                    )
                    self.assertEqual(response.status_code, 404, f"{path}: {response.text}")

                invoice_list = await client.get("/api/v1/invoices", headers=headers)
                self.assertEqual(invoice_list.status_code, 200, invoice_list.text)
                self.assertNotIn(invoice_b_id, {row["id"] for row in invoice_list.json()["items"]})
                for path, method in (
                    (f"/api/v1/invoices/{invoice_b_id}", "get"),
                    (f"/api/v1/invoices/{invoice_b_id}", "delete"),
                    (f"/api/v1/invoices/{invoice_b_id}", "put"),
                    (f"/api/v1/invoices/{invoice_b_id}/approve", "post"),
                    (f"/api/v1/invoices/{invoice_b_id}/issue", "post"),
                    (f"/api/v1/invoices/{invoice_b_id}/convert-to-tax-invoice", "post"),
                    (f"/api/v1/invoices/{invoice_b_id}/payments", "get"),
                    (f"/api/v1/invoices/{invoice_b_id}/pdf", "get"),
                ):
                    kwargs = {"headers": headers}
                    if method == "put":
                        kwargs["json"] = {
                            "customer_id": customer_a_id,
                            "place_of_supply_code": "27",
                            "lines": [{
                                "product_id": product_id,
                                "quantity": "1",
                                "unit_price": "1.00",
                            }],
                        }
                    response = await getattr(client, method)(path, **kwargs)
                    self.assertEqual(response.status_code, 404, f"{path}: {response.text}")
                b_invoice_cancel = await client.post(
                    f"/api/v1/invoices/{invoice_b_id}/cancel",
                    headers=headers,
                    json={"reason": "Not authorized for this dealer invoice"},
                )
                self.assertEqual(b_invoice_cancel.status_code, 404, b_invoice_cancel.text)

                b_invoice_create = await client.post(
                    "/api/v1/invoices",
                    headers=headers,
                    json={
                        "customer_id": customer_b_id,
                        "place_of_supply_code": "27",
                        "lines": [{
                            "product_id": product_id,
                            "quantity": "1",
                            "unit_price": "99.00",
                        }],
                    },
                )
                self.assertEqual(b_invoice_create.status_code, 404, b_invoice_create.text)

                payment_list = await client.get("/api/v1/payments", headers=headers)
                self.assertEqual(payment_list.status_code, 200, payment_list.text)
                self.assertNotIn(payment_b_id, {row["id"] for row in payment_list.json()["items"]})
                self.assertEqual(
                    (await client.get(f"/api/v1/payments/{payment_b_id}/pdf", headers=headers)).status_code,
                    404,
                )
                self.assertEqual(
                    (await client.post(f"/api/v1/payments/{payment_b_id}/reverse", headers=headers)).status_code,
                    404,
                )
                b_payment_create = await client.post(
                    "/api/v1/payments",
                    headers=headers,
                    json={
                        "customer_id": customer_b_id,
                        "method_id": payment_method_id,
                        "amount": "10.00",
                        "payment_date": date.today().isoformat(),
                    },
                )
                self.assertEqual(b_payment_create.status_code, 404, b_payment_create.text)
                b_refund_create = await client.post(
                    "/api/v1/payments/refunds",
                    headers=headers,
                    json={
                        "customer_id": customer_b_id,
                        "method_id": payment_method_id,
                        "amount": "10.00",
                        "payment_date": date.today().isoformat(),
                        "source": "advance",
                    },
                )
                self.assertEqual(b_refund_create.status_code, 404, b_refund_create.text)
                b_credit_note_create = await client.post(
                    "/api/v1/credit-notes",
                    headers=headers,
                    json={
                        "invoice_id": invoice_b_id,
                        "note_date": date.today().isoformat(),
                        "reason": "Attempt to modify another dealer's invoice",
                        "lines": [{"invoice_item_id": 1, "quantity": "1"}],
                    },
                )
                self.assertEqual(b_credit_note_create.status_code, 404, b_credit_note_create.text)

                statement = await client.get(
                    "/api/v1/dealer/portal/statement",
                    params={"customer_id": customer_b_id},
                    headers=headers,
                )
                self.assertEqual(statement.status_code, 200, statement.text)
                self.assertEqual(statement.json()["customer_id"], customer_a_id)
                self.assertEqual(statement.json()["customer_name"], f"Dealer A {suffix}")
                references = {item["reference"] for item in statement.json()["items"]}
                self.assertIn(f"PORTAL-INV-A-{suffix}", references)
                self.assertIn(f"PORTAL-PAY-A-{suffix}", references)
                self.assertNotIn(f"PORTAL-PAY-B-{suffix}", references)
                self.assertEqual(
                    (
                        await client.get(
                            f"/api/v1/customers/{customer_b_id}/ledger",
                            headers=headers,
                        )
                    ).status_code,
                    404,
                )

        try:
            asyncio.run(exercise_api())
        finally:
            with self.factory.begin() as session:
                role = session.scalar(select(Role).where(Role.name == "Dealer / Distributor"))
                role.permissions[:] = [
                    permission for permission in role.permissions
                    if permission.code not in extra_permissions or permission.code in original_permission_codes
                ]
                user = session.get(User, user_id)
                if user is not None:
                    user.is_active = False
