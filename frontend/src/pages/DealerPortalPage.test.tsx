import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { dealerOrderRequestBody, DealerOrderHistory } from "./DealerPortalPage";

describe("dealer order portal", () => {
  it("submits only product identifiers and quantities, never catalog prices", () => {
    expect(dealerOrderRequestBody([
      { product_id: 12, quantity: "2.500" },
    ])).toEqual({
      lines: [{ product_id: 12, quantity: "2.500" }],
    });
    expect(JSON.stringify(dealerOrderRequestBody([
      { product_id: 12, quantity: "2.500" },
    ]))).not.toContain("price");
  });

  it("renders dealer order history with current status and server total", () => {
    const markup = renderToStaticMarkup(
      <DealerOrderHistory
        orders={[{
          id: 8,
          number: "SO/26-27/00008",
          order_date: "2026-11-08",
          status: "confirmed",
          grand_total: "291.74",
          lines: [{
            id: 3,
            product_id: 12,
            description: "EV battery",
            quantity: "2.000",
            unit_price: "123.4500",
          }],
        }]}
      />,
    );
    expect(markup).toContain("SO/26-27/00008");
    expect(markup).toContain("confirmed");
    expect(markup).toContain("291.74");
  });
});
