import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { purchaseActions, PurchaseDetailPanel, type Purchase } from "./PurchasingPage";

const invoice: Purchase = {
  id: 12,
  number: "PI-00000012",
  doc_type: "purchase_invoice",
  supplier_id: 3,
  supplier_name: "Example Supplier",
  supplier_invoice_no: "SUP-1004",
  doc_date: "2026-11-07",
  due_date: "2026-12-07",
  status: "posted",
  grand_total: "1180.00",
  taxable_total: "1000.00",
  parent_id: 8,
  items: [{
    id: 44,
    source_purchase_item_id: 21,
    product_id: 2,
    description: "EV battery",
    hsn_sac: "85076000",
    quantity: "2.000",
    unit_price: "500.00",
    discount: "0.00",
    taxable_value: "1000.00",
    gst_rate: "18.00",
    cess_rate: "0.00",
    cgst: "90.00",
    sgst: "90.00",
    igst: "0.00",
    cess: "0.00",
  }],
  debit_notes: [{
    id: 15,
    note_number: "DN-00000015",
    note_date: "2026-11-08",
    reason: "Damaged goods returned",
    status: "issued",
    grand_total: "590.00",
  }],
};

describe("purchasing workflow", () => {
  it("offers actions appropriate to document type and status, while keeping read-only access safe", () => {
    expect(purchaseActions({ ...invoice, doc_type: "purchase_request", status: "draft" }, true)).toContain("approve");
    expect(purchaseActions({ ...invoice, doc_type: "purchase_request", status: "approved" }, true)).toContain("convert-to-order");
    expect(purchaseActions({ ...invoice, doc_type: "purchase_order", status: "ordered" }, true)).toEqual(
      expect.arrayContaining(["receive", "invoice", "pdf"]),
    );
    expect(purchaseActions(invoice, true)).toContain("return");
    expect(purchaseActions(invoice, true)).toContain("cancel");
    expect(purchaseActions(invoice, false)).toEqual(["pdf"]);
    expect(purchaseActions({ ...invoice, doc_type: "purchase_order", status: "ordered" }, true)).not.toContain("cancel");
  });

  it("shows supplier invoice details, purchase-return actions and debit-note downloads", () => {
    const markup = renderToStaticMarkup(
      <PurchaseDetailPanel
        purchase={invoice}
        canManage
        onAction={vi.fn()}
        onNoteAction={vi.fn()}
      />,
    );

    expect(markup).toContain("PI-00000012");
    expect(markup).toContain("EV battery");
    expect(markup).toContain("Create purchase return");
    expect(markup).toContain("Purchase returns / debit notes");
    expect(markup).toContain("DN-00000015");
    expect(markup).toContain("Download PDF");
    expect(markup).toContain("Cancel debit note");
  });

  it("does not offer management actions for a cancelled purchase", () => {
    expect(purchaseActions({ ...invoice, status: "cancelled" }, true)).toEqual(["pdf"]);
  });
});
