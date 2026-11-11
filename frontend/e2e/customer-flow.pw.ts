import { expect, test } from "@playwright/test";

test.describe("Customer management flow", () => {
  test.beforeEach(async ({ page }) => {
    let customersList = [
      {
        id: 1,
        code: "CUST-001",
        name: "Apex EV Solutions",
        customer_type: "dealer",
        phone: "+91 98765 43210",
        email: "contact@apexev.test",
        gstin: "37AABCU9603R1ZM",
        pan: "AABCU9603R",
        billing_address: "Industrial Park, Kurnool",
        shipping_address: "Industrial Park, Kurnool",
        state_code: "37",
        city: "Kurnool",
        opening_balance: "0.00",
        credit_limit: "500000.00",
        payment_terms_days: 30,
        branch_id: 1,
        is_active: true,
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
              "customers:read",
              "customers:create",
              "customers:update",
              "customers:delete",
              "settings:read",
            ],
          }),
        });
      }

      if (path.endsWith("/notifications")) {
        return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
      }

      if (path.endsWith("/states")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            { code: "37", name: "Andhra Pradesh" },
            { code: "29", name: "Karnataka" },
          ]),
        });
      }

      if (path.endsWith("/customers") && method === "GET") {
        const q = url.searchParams.get("q") ?? "";
        const filtered = q
          ? customersList.filter((c) => c.name.toLowerCase().includes(q.toLowerCase()))
          : customersList;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: filtered, total: filtered.length }),
        });
      }

      if (path.endsWith("/customers") && method === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        const newCust = {
          id: customersList.length + 1,
          ...body,
          opening_balance: String(body.opening_balance ?? "0.00"),
          credit_limit: String(body.credit_limit ?? "0.00"),
          payment_terms_days: Number(body.payment_terms_days ?? 0),
          branch_id: null,
          is_active: true,
        };
        customersList.push(newCust);
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(newCust),
        });
      }

      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      });
    });
  });

  test("admin navigates to customers, views list, and adds a new customer", async ({ page }) => {
    // 1. Sign in
    await page.goto("/login");
    await page.getByLabel("Email").fill("admin@berev.test");
    await page.getByLabel("Password").fill("super-secure-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    // 2. Navigate to /customers
    await page.goto("/customers");
    await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();

    // 3. Verify existing customer row
    await expect(page.getByText("Apex EV Solutions")).toBeVisible();
    await expect(page.getByText("CUST-001")).toBeVisible();

    // 4. Open Add customer modal
    await page.getByRole("button", { name: "Add customer" }).click();
    await expect(page.getByRole("heading", { name: "Add customer" })).toBeVisible();

    // 5. Fill customer form within modal dialog
    const modal = page.getByRole("dialog");
    await modal.getByLabel("Customer code").fill("CUST-002");
    await modal.getByLabel("Name").fill("Bharat Mobility");
    await modal.locator("select[name='customer_type']").selectOption("business");
    await modal.getByLabel("Phone").fill("+91 99887 76655");
    await modal.locator("select[name='state_code']").selectOption("37");
    await modal.getByLabel("GSTIN").fill("37AABCU9603R1ZM");
    await modal.getByLabel("PAN").fill("AABCU9603R");
    await modal.getByLabel("City").fill("Vijayawada");
    await modal.getByLabel("Billing address").fill("MG Road, Vijayawada");
    await modal.getByLabel("Credit limit").fill("250000");
    await modal.getByLabel("Payment terms (days)").fill("15");

    // 6. Submit
    await modal.getByRole("button", { name: "Create customer" }).click();

    // 7. Verify modal closes and new customer appears in list
    await expect(page.getByText("Bharat Mobility")).toBeVisible();
  });
});
