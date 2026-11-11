import { expect, test } from "@playwright/test";

test.describe("Payments and receipts workflow", () => {
  test.beforeEach(async ({ page }) => {
    let paymentsList = [
      {
        id: 1,
        payment_number: "RCP-26-27-0001",
        customer_id: 1,
        customer_name: "Apex EV Solutions",
        direction: "in",
        method: "Bank Transfer",
        amount: "50000.00",
        payment_date: "2026-11-10",
        status: "recorded",
        is_advance: false,
        allocations: [{ invoice_id: 101, amount: "50000.00" }],
      },
    ];

    await page.route("**/api/v1/**", async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      const method = route.request().method();

      if (path.endsWith("/auth/login")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            access_token: "admin-access-token",
            refresh_token: "admin-refresh-token",
            token_type: "bearer",
            expires_at: "2099-01-01T00:00:00Z",
          }),
        });
      }

      if (path.endsWith("/auth/me")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: 1,
            email: "admin@berev.test",
            full_name: "Admin User",
            role: "Super Admin",
            permissions: [
              "auth:self:read",
              "payments:read",
              "payments:manage",
              "customers:read",
              "sales:read",
              "settings:read",
            ],
          }),
        });
      }

      if (path.endsWith("/notifications")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
      }

      if (path.endsWith("/customers")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: [{ id: 1, name: "Apex EV Solutions", is_active: true }],
            total: 1,
          }),
        });
      }

      if (path.endsWith("/suppliers")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: [], total: 0 }),
        });
      }

      if (path.endsWith("/invoices")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: [
              {
                id: 101,
                invoice_number: "BEREV/26-27/0001T",
                customer_id: 1,
                customer_name: "Apex EV Solutions",
                grand_total: "50000.00",
                status: "issued",
              },
            ],
            total: 1,
          }),
        });
      }

      if (path.endsWith("/payment-methods")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            { id: 1, name: "Bank Transfer", ledger_kind: "bank" },
            { id: 2, name: "Cash", ledger_kind: "cash" },
          ]),
        });
      }

      if (path.endsWith("/payments") && method === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: paymentsList, total: paymentsList.length }),
        });
      }

      if (path.endsWith("/payments") && method === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        const newPayment = {
          id: paymentsList.length + 1,
          payment_number: "RCP-26-27-0002",
          customer_id: body.customer_id,
          customer_name: "Apex EV Solutions",
          direction: "in",
          method: "Bank Transfer",
          amount: String(body.amount),
          payment_date: body.payment_date,
          status: "recorded",
          is_advance: !body.allocations || body.allocations.length === 0,
          allocations: body.allocations ?? [],
        };
        paymentsList.push(newPayment);
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(newPayment),
        });
      }

      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      });
    });
  });

  test("admin navigates to payments, reviews history, and records a customer receipt", async ({ page }) => {
    // 1. Sign in
    await page.goto("/login");
    await page.getByLabel("Email").fill("admin@berev.test");
    await page.getByLabel("Password").fill("super-secure-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    // 2. Navigate to /payments
    await page.goto("/payments");
    await expect(page.getByRole("heading", { name: "Payments" })).toBeVisible();

    // 3. Verify existing payment in history
    await expect(page.getByText("RCP-26-27-0001")).toBeVisible();
    await expect(page.getByRole("cell", { name: "Apex EV Solutions" })).toBeVisible();

    // 4. Fill in Record receipt form
    await page.locator("select").filter({ hasText: "Select customer" }).selectOption("1");
    await page.locator("select").filter({ hasText: "Select method" }).selectOption("1");
    await page.getByLabel("Amount").first().fill("25000");

    // 5. Submit receipt
    const submitButton = page.getByRole("button", { name: "Record receipt" });
    await expect(submitButton).toBeEnabled();
    await submitButton.click();

    // 6. Verify second receipt appears in history
    await expect(page.getByRole("cell", { name: "RCP-26-27-0002" })).toBeVisible();
  });
});
