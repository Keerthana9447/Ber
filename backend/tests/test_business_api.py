import asyncio
import os
import unittest
import uuid
from datetime import date
from io import BytesIO
from decimal import Decimal
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.auth.security import create_token_pair, hash_password
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.models import (
    AuditLog,
    Customer,
    Invoice,
    Inventory,
    JournalEntry,
    Permission,
    ProductSerial,
    Role,
    StockMovement,
    User,
)
from app.seed import seed_reference_data
from berev_core.gst import LineInput, compute_invoice, gstin_checksum_char
from berev_core.words import amount_in_words
from pypdf import PdfReader


@unittest.skipUnless(
    os.environ.get("BEREV_TEST_DATABASE_URL"),
    "Set BEREV_TEST_DATABASE_URL to run PostgreSQL API integration tests",
)
class BusinessApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings(
            _env_file=PROJECT_ROOT / ".env",
            database_url=os.environ["BEREV_TEST_DATABASE_URL"],
        )
        cls.app = create_app(cls.settings)
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
