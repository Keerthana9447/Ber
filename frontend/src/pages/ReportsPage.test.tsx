import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReportsPage } from "./ReportsPage";

describe("reports page", () => {
  it("offers export formats for real accounting and operations reports", () => {
    const markup = renderToStaticMarkup(<ReportsPage />);

    expect(markup).toContain("Trial balance");
    expect(markup).toContain("Profit and loss");
    expect(markup).toContain("Sales register");
    expect(markup).toContain("Purchase register");
    expect(markup).toContain("CSV");
    expect(markup).toContain("XLSX");
    expect(markup).toContain("PDF");
  });
});
