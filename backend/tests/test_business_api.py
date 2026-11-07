import asyncio
import os
import unittest
import uuid
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.auth.security import create_token_pair, hash_password
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.models import AuditLog, Customer, Permission, ProductSerial, Role, User
from app.seed import seed_reference_data
from berev_core.gst import LineInput, compute_invoice, gstin_checksum_char
from berev_core.words import amount_in_words


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
                    params={"customer_type": "business", "state_code": "29", "is_active": True},
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

                line = {
                    "product_id": product.json()["id"],
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

                with self.factory.begin() as session:
                    invoice_triggers = set(session.scalars(text(
                        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgrelid IN ('invoices'::regclass, 'invoice_items'::regclass)"
                    )).all())
                self.assertTrue({"invoices_guard", "invoice_items_guard"}.issubset(invoice_triggers))

                with self.factory.begin() as session:
                    session.execute(
                        text("UPDATE invoices SET status = 'issued', invoice_number = :number WHERE id = :id"),
                        {"number": f"TEST-{suffix}", "id": draft_id},
                    )
                with self.factory() as session:
                    with self.assertRaises(DBAPIError):
                        session.execute(
                            text("UPDATE invoices SET grand_total = grand_total + 1 WHERE id = :id"),
                            {"id": draft_id},
                        )
                    session.rollback()
                    with self.assertRaises(DBAPIError):
                        session.execute(
                            text("UPDATE invoice_items SET description = description || ' changed' WHERE invoice_id = :id"),
                            {"id": draft_id},
                        )
                    session.rollback()

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
