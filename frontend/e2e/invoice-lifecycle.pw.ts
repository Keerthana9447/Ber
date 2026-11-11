import { expect, test } from "@playwright/test";

test.describe("Invoice lifecycle and acceptance", () => {
  test.beforeEach(async ({ page }) => {
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
              "sales:read",
              "sales:create",
              "sales:update",
              "sales:approve",
              "sales:issue",
              "sales:cancel",
              "customers:read",
              "products:read",
              "inventory:read",
              "settings:read",
              "reports:read",
              "payments:read",
            ],
          }),
        });
      }

      if (path.endsWith("/settings/readiness")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ready: true, missing_fields: [] }),
        });
      }

      if (path.endsWith("/notifications")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        });
      }

      if (path.endsWith("/invoices/1/approve") && method === "POST") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: 1,
            invoice_number: null,
            customer_id: 10,
            customer_name: "Acme Motors",
            status: "approved",
            doc_type: "tax_invoice",
            invoice_date: "2026-11-10",
            due_date: "2026-12-10",
            grand_total: "11800.00",
            lines: [],
          }),
        });
      }

      if (path.endsWith("/invoices/1/pdf") && method === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/pdf",
          body: "%PDF-1.4 mock pdf content",
        });
      }

      if (path.endsWith("/invoices/1") && method === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: 1,
            invoice_number: "BEREV/26-27/0001T",
            customer_id: 10,
            customer_name: "Acme Motors",
            status: "issued",
            doc_type: "tax_invoice",
            reverse_charge: false,
            place_of_supply_code: "37",
            invoice_date: "2026-11-10",
            due_date: "2026-12-10",
            payment_terms: "Net 30",
            notes: "Test delivery note",
            taxable_total: "10000.00",
            cgst_total: "900.00",
            sgst_total: "900.00",
            igst_total: "0.00",
            cess_total: "0.00",
            round_off: "0.00",
            grand_total: "11800.00",
            amount_received: "0.00",
            credited_amount: "0.00",
            outstanding: "11800.00",
            customer_credit: "0.00",
            payment_status: "unpaid",
            lines: [
              {
                id: 1,
                product_id: 101,
                serial_id: null,
                warehouse_id: 1,
                is_service: false,
                description: "Lithium Traction Battery Pack",
                hsn_sac: "8507",
                unit: "NOS",
                quantity: "1.000",
                unit_price: "10000.00",
                discount: "0.00",
                taxable_value: "10000.00",
                gst_rate: "18.00",
                cess_rate: "0.00",
                cgst: "900.00",
                sgst: "900.00",
                igst: "0.00",
                cess: "0.00",
              },
            ],
          }),
        });
      }

      if (path.endsWith("/invoices") && method === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: [
              {
                id: 1,
                invoice_number: null,
                customer_id: 10,
                customer_name: "Acme Motors",
                doc_type: "tax_invoice",
                invoice_date: "2026-11-10",
                due_date: "2026-12-10",
                status: "draft",
                grand_total: "11800.00",
              },
            ],
            total: 1,
          }),
        });
      }

      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      });
    });
  });

  test("admin navigates invoices, reviews draft, approves, and views issued invoice", async ({ page }) => {
    // 1. Sign in
    await page.goto("/login");
    await page.getByLabel("Email").fill("admin@berev.test");
    await page.getByLabel("Password").fill("super-secure-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    // 2. Navigate to /invoices
    await page.goto("/invoices");
    await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();

    // 3. Verify readiness banner
    await expect(
      page.getByText("Company settings are complete for tax invoice drafts.")
    ).toBeVisible();

    // 4. Verify draft invoice row is listed
    await expect(page.getByText("Acme Motors")).toBeVisible();
    await expect(page.getByRole("button", { name: "Draft (not numbered)" })).toBeVisible();

    // 5. Test Approve action
    const approveButton = page.getByRole("button", { name: "Approve" });
    await expect(approveButton).toBeVisible();
    await approveButton.click();

    // Confirm dialog appears
    await expect(page.getByRole("heading", { name: "Approve invoice" })).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Approve" }).click();

    // 6. Test View invoice detail by clicking the invoice row link
    const viewLink = page.getByRole("button", { name: "Draft (not numbered)" });
    await expect(viewLink).toBeVisible();
    await viewLink.click();

    // Check detail view
    await expect(page.getByText("Lithium Traction Battery Pack")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download PDF" })).toBeVisible();
  });
});
