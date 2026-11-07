import asyncio
import os
import unittest
import uuid
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.auth.security import create_token_pair, hash_password
from app.config import PROJECT_ROOT, Settings
from app.main import create_app
from app.models import Role, User
from app.seed import seed_reference_data


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


if __name__ == "__main__":
    unittest.main()
