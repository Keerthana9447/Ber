import { describe, expect, it } from "vitest";
import { formatCount, formatDate, formatINR } from "./format";

describe("format", () => {
  it("formats rupees in the Indian grouping", () => {
    expect(formatINR("118000")).toContain("1,18,000");
    expect(formatINR("0.5")).toContain("0.5");
  });
  it("shows a dash for missing values and passes through non-numbers", () => {
    expect(formatINR(null)).toBe("—");
    expect(formatINR("")).toBe("—");
    expect(formatINR("n/a")).toBe("n/a");
    expect(formatCount(undefined)).toBe("—");
  });
  it("formats date-only strings without timezone shifts", () => {
    expect(formatDate("2026-04-01")).toMatch(/01 Apr 2026|1 Apr 2026/);
    expect(formatDate(null)).toBe("—");
    expect(formatDate("garbage")).toBe("garbage");
  });
});
