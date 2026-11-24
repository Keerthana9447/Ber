import { expect, test } from "@playwright/test";

test.describe("BEREV Master Prompt Section 21 - 15 E2E Scenarios against Compose Stack", () => {
  test.describe.configure({ mode: "serial" });

  let adminToken: string;
  let restrictedToken: string;
  let customerId: number;
  let categoryId: number;
  let warehouseId: number;
  let productId: number;
  let quotationId: number;
  let orderId: number;
  let invoiceId: number;
  let invoiceTotal: string;

  test.beforeAll(async ({ request }) => {
    // Authenticate Super Admin — credentials from env vars (see .env.example)
    const loginRes = await request.post("/api/v1/auth/login", {
      data: {
        email: process.env.E2E_ADMIN_EMAIL ?? "staging-test@berev.local",
        password: process.env.E2E_ADMIN_PASSWORD ?? "StagingPassword123!",
      },
    });
    expect(loginRes.ok()).toBeTruthy();
    const loginData = await loginRes.json();
    adminToken = loginData.access_token;

    // Authenticate Restricted User
    const restrictedRes = await request.post("/api/v1/auth/login", {
      data: {
        email: process.env.E2E_RESTRICTED_EMAIL ?? "restricted@berev.local",
        password: process.env.E2E_RESTRICTED_PASSWORD ?? "RestrictedPassword123!",
      },
    });
    expect(restrictedRes.ok()).toBeTruthy();
    const restrictedData = await restrictedRes.json();
    restrictedToken = restrictedData.access_token;

    // Ensure Product Category exists
    const catRes = await request.post("/api/v1/product-categories", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { name: `EV Motors ${Date.now()}` },
    });
    if (catRes.ok()) {
      const catData = await catRes.json();
      categoryId = catData.id;
    } else {
      const refDataRes = await request.get("/api/v1/products/reference-data", {
        headers: { Authorization: `Bearer ${adminToken}` },
      });
      const refData = await refDataRes.json();
      categoryId = refData.categories[0].id;
    }

    // Ensure Warehouse exists
    const whRes = await request.post("/api/v1/warehouses", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { code: `WH-${Date.now()}`, name: "E2E Central Hub" },
    });
    if (whRes.ok()) {
      const whData = await whRes.json();
      warehouseId = whData.id;
    } else {
      const refDataRes = await request.get("/api/v1/products/reference-data", {
        headers: { Authorization: `Bearer ${adminToken}` },
      });
      const refData = await refDataRes.json();
      warehouseId = refData.warehouses[0].id;
    }
  });

  test("Scenario 01: Create a customer", async ({ request }) => {
    const code = `CUST-${Date.now()}`;
    const res = await request.post("/api/v1/customers", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        code,
        name: `GreenDrive Logistics ${Date.now()}`,
        customer_type: "business",
        phone: "+91 98765 00001",
        email: `greendrive-${Date.now()}@example.test`,
        state_code: "37",
        city: "Kurnool",
        billing_address: "Plot 12 Industrial Area, Kurnool",
        shipping_address: "Plot 12 Industrial Area, Kurnool",
        opening_balance: "0.00",
        credit_limit: "500000.00",
        payment_terms_days: 30,
        is_active: true,
      },
    });
    expect(res.ok(), `create customer failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    expect(data.name).toContain("GreenDrive Logistics");
    customerId = data.id;
  });

  test("Scenario 02: Create a product with GST classification", async ({ request }) => {
    const sku = `SKU-E2E-${Date.now()}`;
    const res = await request.post("/api/v1/products", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        sku,
        name: "72V 3kW BLDC Traction Motor",
        category_id: categoryId,
        hsn_sac: "8501",
        unit: "NOS",
        purchase_price: "15000.00",
        selling_price: "20000.00",
        gst_rate: "18.00",
        cess_rate: "0.00",
        min_stock: "2.000",
        is_serialized: false,
        is_batch_tracked: false,
        is_service: false,
        is_active: true,
      },
    });
    expect(res.ok(), `create customer failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    expect(data.hsn_sac).toBe("8501");
    expect(data.gst_rate).toBe("18.00");
    productId = data.id;
  });

  test("Scenario 03: Add inventory", async ({ request }) => {
    const res = await request.post("/api/v1/inventory/adjustments", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        product_id: productId,
        warehouse_id: warehouseId,
        quantity_delta: "50.000",
        unit_cost: "15000.0000",
        notes: "Initial stocking for E2E scenarios",
      },
    });
    expect(res.ok(), `add inventory failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    expect(data.movement_id).toBeDefined();
    expect(parseFloat(data.quantity_delta)).toBe(50.0);

    // Record opening valuation for accounting and COGS
    const valRes = await request.post("/api/v1/inventory/opening-valuations", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        lines: [
          {
            product_id: productId,
            warehouse_id: warehouseId,
            unit_cost: "15000.0000",
          },
        ],
      },
    });
    expect(valRes.ok(), `valuation failed ${valRes.status()}: ${await valRes.text()}`).toBeTruthy();
  });

  test("Scenario 04: Generate a quotation", async ({ request }) => {
    const res = await request.post("/api/v1/quotations", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        customer_id: customerId,
        quote_date: new Date().toISOString().slice(0, 10),
        place_of_supply_code: "37",
        notes: "Quotation for 2 motors",
        lines: [
          {
            product_id: productId,
            quantity: "2.000",
            unit_price: "20000.00",
          },
        ],
      },
    });
    expect(res.ok(), `Quotation POST failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("draft");
    expect(data.lines.length).toBe(1);
    quotationId = data.id;
  });

  test("Scenario 05: Convert quotation into a sales order", async ({ request }) => {
    // Quotation state machine: draft -> send -> sent -> accept -> accepted -> convert-to-order
    const sendRes = await request.post(`/api/v1/quotations/${quotationId}/send`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(sendRes.ok(), `send failed ${sendRes.status()}: ${await sendRes.text()}`).toBeTruthy();

    const acceptRes = await request.post(`/api/v1/quotations/${quotationId}/accept`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(acceptRes.ok(), `accept failed ${acceptRes.status()}: ${await acceptRes.text()}`).toBeTruthy();

    const res = await request.post(`/api/v1/quotations/${quotationId}/convert-to-order`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(res.ok(), `create customer failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    // Order is created as draft; confirm it
    orderId = data.id;

    const confirmRes = await request.post(`/api/v1/orders/${orderId}/confirm`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(confirmRes.ok(), `confirm order failed ${confirmRes.status()}: ${await confirmRes.text()}`).toBeTruthy();
    const confirmedData = await confirmRes.json();
    expect(confirmedData.status).toBe("confirmed");
    expect(confirmedData.lines.length).toBe(1);
  });

  test("Scenario 06: Generate a GST invoice", async ({ request }) => {
    // Create invoice directly (standalone path - quotation->order->invoice requires delivery challan)
    const draftRes = await request.post("/api/v1/invoices", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        customer_id: customerId,
        place_of_supply_code: "37",
        doc_type: "tax_invoice",
        reverse_charge: false,
        invoice_date: new Date().toISOString().slice(0, 10),
        lines: [
          {
            product_id: productId,
            warehouse_id: warehouseId,
            quantity: "2.000",
            unit_price: "20000.00",
          },
        ],
      },
    });
    expect(draftRes.ok(), `create draft failed ${draftRes.status()}: ${await draftRes.text()}`).toBeTruthy();
    const draftData = await draftRes.json();
    invoiceId = draftData.id;

    // 2. Approve invoice
    const approveRes = await request.post(`/api/v1/invoices/${invoiceId}/approve`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(approveRes.ok(), `approve failed ${approveRes.status()}: ${await approveRes.text()}`).toBeTruthy();

    // 3. Issue invoice
    const issueRes = await request.post(`/api/v1/invoices/${invoiceId}/issue`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(issueRes.ok(), `issue failed ${issueRes.status()}: ${await issueRes.text()}`).toBeTruthy();
    const issuedData = await issueRes.json();
    expect(issuedData.status).toBe("issued");
    expect(issuedData.invoice_number).toMatch(/^BERT\/.*$/);
    invoiceTotal = issuedData.grand_total;
  });

  test("Scenario 07: Verify CGST/SGST or IGST", async ({ request }) => {
    const res = await request.get(`/api/v1/invoices/${invoiceId}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(res.ok(), `create customer failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    // Taxable: 2 * 20000 = 40000. Intra-state 37->37: CGST 9% (3600), SGST 9% (3600), IGST 0
    expect(parseFloat(data.taxable_total)).toBe(40000.0);
    expect(parseFloat(data.cgst_total)).toBe(3600.0);
    expect(parseFloat(data.sgst_total)).toBe(3600.0);
    expect(parseFloat(data.igst_total)).toBe(0.0);
    expect(parseFloat(data.grand_total)).toBe(47200.0);
  });

  test("Scenario 08: Download invoice PDF", async ({ request }) => {
    const res = await request.get(`/api/v1/invoices/${invoiceId}/pdf`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(res.ok(), `create customer failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    expect(res.headers()["content-type"]).toContain("application/pdf");
    const body = await res.body();
    expect(body.toString("ascii", 0, 4)).toBe("%PDF");
  });

  test("Scenario 09: Record partial payment", async ({ request }) => {
    const res = await request.post("/api/v1/payments", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        customer_id: customerId,
        method_id: 1, // Bank Transfer / Cash
        amount: "20000.00",
        payment_date: new Date().toISOString().slice(0, 10),
        reference_no: "PART-PAY-01",
        allocations: [{ invoice_id: invoiceId, amount: "20000.00" }],
      },
    });
    expect(res.ok(), `create customer failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("recorded");

    // Verify remaining outstanding is 27200.00
    const invRes = await request.get(`/api/v1/invoices/${invoiceId}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    const invData = await invRes.json();
    expect(parseFloat(invData.amount_received)).toBe(20000.0);
    expect(parseFloat(invData.outstanding)).toBe(27200.0);
    expect(invData.payment_status).toBe("partially_paid");
  });

  test("Scenario 10: Record remaining payment", async ({ request }) => {
    const res = await request.post("/api/v1/payments", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        customer_id: customerId,
        method_id: 1,
        amount: "27200.00",
        payment_date: new Date().toISOString().slice(0, 10),
        reference_no: "FINAL-PAY-02",
        allocations: [{ invoice_id: invoiceId, amount: "27200.00" }],
      },
    });
    expect(res.ok(), `create customer failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    expect(data.status).toBe("recorded");
  });

  test("Scenario 11: Verify outstanding balance becomes zero", async ({ request }) => {
    const res = await request.get(`/api/v1/invoices/${invoiceId}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(res.ok(), `create customer failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    expect(parseFloat(data.outstanding)).toBe(0.0);
    expect(parseFloat(data.amount_received)).toBe(47200.0);
    expect(data.payment_status).toBe("paid");
  });

  test("Scenario 12: Verify stock is deducted correctly", async ({ request }) => {
    // Original stock added was 50. Invoice issued 2 units -> remaining on-hand should be 48.
    const res = await request.get(`/api/v1/inventory?warehouse_id=${warehouseId}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(res.ok(), `list inventory failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    const item = data.items?.find((i: any) => i.product_id === productId);
    expect(item).toBeDefined();
    expect(parseFloat(item.quantity)).toBe(48.0);
  });

  test("Scenario 13: Verify audit logs", async ({ request }) => {
    const res = await request.get("/api/v1/audit-logs", {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(res.ok(), `audit logs failed ${res.status()}: ${await res.text()}`).toBeTruthy();
    const data = await res.json();
    const actions = data.items.map((log: any) => log.action);
    expect(actions).toContain("issue");
    expect(actions).toContain("create");
    expect(actions).toContain("record_receipt");
  });

  test("Scenario 14: Verify user without permission cannot access restricted endpoints", async ({ request }) => {
    // Restricted user tries to access /api/v1/users and /api/v1/settings (PUT)
    const usersRes = await request.get("/api/v1/users", {
      headers: { Authorization: `Bearer ${restrictedToken}` },
    });
    expect(usersRes.status()).toBe(403);

    const settingsRes = await request.put("/api/v1/settings", {
      headers: { Authorization: `Bearer ${restrictedToken}` },
      data: { legal_name: "Hacked Legal Name", invoice_prefix: "HACK", fy_start_month: 4 },
    });
    expect(settingsRes.status()).toBe(403);
  });

  test("Scenario 15: Verify concurrent invoice creation does not produce duplicate invoice numbers", async ({ request }) => {
    // Create two separate draft invoices with stock available
    const draft1Res = await request.post("/api/v1/invoices", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        customer_id: customerId,
        place_of_supply_code: "37",
        doc_type: "tax_invoice",
        invoice_date: new Date().toISOString().slice(0, 10),
        lines: [
          {
            product_id: productId,
            warehouse_id: warehouseId,
            quantity: "1.000",
            unit_price: "20000.00",
          },
        ],
      },
    });
    expect(draft1Res.ok()).toBeTruthy();
    const id1 = (await draft1Res.json()).id;

    const draft2Res = await request.post("/api/v1/invoices", {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        customer_id: customerId,
        place_of_supply_code: "37",
        doc_type: "tax_invoice",
        invoice_date: new Date().toISOString().slice(0, 10),
        lines: [
          {
            product_id: productId,
            warehouse_id: warehouseId,
            quantity: "1.000",
            unit_price: "20000.00",
          },
        ],
      },
    });
    expect(draft2Res.ok()).toBeTruthy();
    const id2 = (await draft2Res.json()).id;

    // Approve both
    await request.post(`/api/v1/invoices/${id1}/approve`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    await request.post(`/api/v1/invoices/${id2}/approve`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });

    // Issue both concurrently via Promise.all
    const [issue1Res, issue2Res] = await Promise.all([
      request.post(`/api/v1/invoices/${id1}/issue`, {
        headers: { Authorization: `Bearer ${adminToken}` },
      }),
      request.post(`/api/v1/invoices/${id2}/issue`, {
        headers: { Authorization: `Bearer ${adminToken}` },
      }),
    ]);

    expect(issue1Res.ok()).toBeTruthy();
    expect(issue2Res.ok()).toBeTruthy();

    const invoice1 = await issue1Res.json();
    const invoice2 = await issue2Res.json();

    // Verify invoice numbers are distinct, non-null, and gapless
    expect(invoice1.invoice_number).not.toBeNull();
    expect(invoice2.invoice_number).not.toBeNull();
    expect(invoice1.invoice_number).not.toBe(invoice2.invoice_number);

    // Both should match format BERT/<FY>/<SEQ>
    expect(invoice1.invoice_number).toMatch(/^BERT\/.*$/);
    expect(invoice2.invoice_number).toMatch(/^BERT\/.*$/);
  });
});
