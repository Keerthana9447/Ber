import { describe, expect, it } from "vitest";
import { resolveRange } from "./dateRanges";

const now = new Date(2026, 10, 6); // 6 Nov 2026

describe("resolveRange", () => {
  it("today and yesterday", () => {
    expect(resolveRange("today", now)).toEqual({ from: "2026-11-06", to: "2026-11-06" });
    expect(resolveRange("yesterday", now)).toEqual({ from: "2026-11-05", to: "2026-11-05" });
  });
  it("last 7 days includes today", () => {
    expect(resolveRange("last7", now)).toEqual({ from: "2026-10-31", to: "2026-11-06" });
  });
  it("this and previous month", () => {
    expect(resolveRange("this_month", now)).toEqual({ from: "2026-11-01", to: "2026-11-06" });
    expect(resolveRange("prev_month", now)).toEqual({ from: "2026-10-01", to: "2026-10-31" });
  });
  it("previous month across a year boundary and leap February", () => {
    expect(resolveRange("prev_month", new Date(2027, 0, 15))).toEqual({ from: "2026-12-01", to: "2026-12-31" });
    expect(resolveRange("prev_month", new Date(2028, 2, 10))).toEqual({ from: "2028-02-01", to: "2028-02-29" });
  });
  it("financial year starts in April", () => {
    expect(resolveRange("this_fy", now)).toEqual({ from: "2026-04-01", to: "2026-11-06" });
    expect(resolveRange("this_fy", new Date(2027, 1, 10))).toEqual({ from: "2026-04-01", to: "2027-02-10" });
    expect(resolveRange("this_fy", new Date(2026, 3, 1))).toEqual({ from: "2026-04-01", to: "2026-04-01" });
  });
  it("custom passes through", () => {
    expect(resolveRange("custom", now, 4, { from: "2026-01-01", to: "2026-01-31" })).toEqual({ from: "2026-01-01", to: "2026-01-31" });
  });
});
