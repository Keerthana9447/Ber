import { expect, test } from "@playwright/test";

test("dealer signs in and reaches the scoped dealer portal", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/auth/login")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: "test-access-token",
          refresh_token: "test-refresh-token",
          token_type: "bearer",
          expires_at: "2099-01-01T00:00:00Z",
        }),
      });
    } else if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          email: "dealer@example.test",
          full_name: "Test Dealer",
          role: "Dealer / Distributor",
          permissions: ["auth:self:read", "products:read", "sales:read", "payments:read"],
        }),
      });
    } else if (path.endsWith("/dealer/portal/profile")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ dealer_code: "DEALER-TEST", name: "Test Dealer", status: "active" }),
      });
    } else if (path.endsWith("/notifications")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    } else {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      });
    }
  });

  await page.goto("/login");
  await page.getByLabel("Email").fill("dealer@example.test");
  await page.getByLabel("Password").fill("test-only-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/products$/);
  await expect(page.getByRole("heading", { name: "Dealer portal" })).toBeVisible();
  await expect(page.getByText("No products available")).toBeVisible();
  await expect(page.getByText("DEALER-TEST")).toBeVisible();
});
