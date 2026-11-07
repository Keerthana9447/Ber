import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { customerFormSchema } from "./CustomersPage";
import { invoiceDraftRequestBody, invoiceDraftSchema, PreviewTotals, type Preview } from "./InvoicesPage";

describe("customer form validation", () => {
  it("accepts valid details and rejects GSTINs for a different state", () => {
    const customer = {
      code: "CUST-001",
      name: "Example Customer",
      customer_type: "business",
      phone: "9876543210",
      email: "customer@example.test",
      gstin: "29ABCDE1234F1Z5",
      pan: "ABCDE1234F",
      state_code: "29",
      city: "",
      billing_address: "",
      shipping_address: "",
      opening_balance: "0",
      credit_limit: "0",
      payment_terms_days: "0",
      is_active: true,
    };

    expect(customerFormSchema.safeParse(customer).success).toBe(true);
    expect(customerFormSchema.safeParse({ ...customer, state_code: "06" }).success).toBe(false);
    expect(customerFormSchema.safeParse({ ...customer, phone: "abc" }).success).toBe(false);
  });
});

describe("invoice editor validation and preview display", () => {
  const draft = {
    customer_id: "1",
    place_of_supply_code: "29",
    doc_type: "tax_invoice",
    reverse_charge: false,
    invoice_date: "2026-11-07",
    due_date: "",
    payment_terms: "",
    notes: "",
    lines: [{
      product_id: "2",
      serial_id: "",
      description: "Product",
      hsn_sac: "1234",
      unit: "PCS",
      quantity: "1",
      unit_price: "100",
      discount_mode: "percent",
      discount_pct: "0",
      discount_amount: "0",
      gst_rate: "18",
      cess_rate: "0",
    }],
  } as const;

  it("accepts valid invoice lines and rejects quantity precision beyond three decimals", () => {
    expect(invoiceDraftSchema.safeParse(draft).success).toBe(true);
    const invalid = {
      ...draft,
      lines: [{ ...draft.lines[0], quantity: "1.0001" }],
    };
    expect(invoiceDraftSchema.safeParse(invalid).success).toBe(false);
  });

  it("preserves invoice header fields in the draft API request", () => {
    const values = {
      ...draft,
      lines: draft.lines.map((line) => ({ ...line })),
      due_date: "2026-12-07",
      payment_terms: "Net 30",
      notes: "Draft note",
    };

    expect(invoiceDraftRequestBody(values)).toMatchObject({
      invoice_date: "2026-11-07",
      due_date: "2026-12-07",
      payment_terms: "Net 30",
      notes: "Draft note",
    });
  });

  it("renders server-provided totals without deriving tax values", () => {
    const preview: Preview = {
      customer_id: 1,
      doc_type: "tax_invoice",
      reverse_charge: false,
      seller_state_code: "06",
      place_of_supply_code: "29",
      is_inter_state: true,
      tax_applicable: true,
      lines: [],
      taxable_total: "100.00",
      cgst_total: "0.00",
      sgst_total: "0.00",
      igst_total: "17.00",
      cess_total: "1.00",
      tax_total: "18.00",
      total_before_round: "118.00",
      round_off: "0.00",
      grand_total: "118.00",
      amount_in_words: "Server-computed amount in words",
      hsn_summary: [],
    };
    const markup = renderToStaticMarkup(<PreviewTotals preview={preview} loading={false} error={null} />);

    expect(markup).toContain("Grand total");
    expect(markup).toContain("Server-computed amount in words");
    expect(markup).toContain("118.00");
    expect(markup).toContain("17.00");
  });
});
