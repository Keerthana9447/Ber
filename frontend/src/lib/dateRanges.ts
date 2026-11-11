export type RangeKey = "today" | "yesterday" | "last7" | "this_month" | "prev_month" | "this_fy" | "custom";

export const RANGE_LABELS: Record<RangeKey, string> = {
  today: "Today",
  yesterday: "Yesterday",
  last7: "Last 7 days",
  this_month: "This month",
  prev_month: "Previous month",
  this_fy: "This financial year",
  custom: "Custom range",
};

const pad = (n: number) => String(n).padStart(2, "0");
export const toIso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** fyStartMonth is 1-12 (April = 4 in India); it should come from company settings when available. */
export function resolveRange(
  key: RangeKey,
  now: Date = new Date(),
  fyStartMonth = 4,
  custom?: { from: string; to: string },
): { from: string; to: string } {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  switch (key) {
    case "today":
      return { from: toIso(today), to: toIso(today) };
    case "yesterday": {
      const y = addDays(today, -1);
      return { from: toIso(y), to: toIso(y) };
    }
    case "last7":
      return { from: toIso(addDays(today, -6)), to: toIso(today) };
    case "this_month":
      return { from: toIso(new Date(today.getFullYear(), today.getMonth(), 1)), to: toIso(today) };
    case "prev_month":
      return {
        from: toIso(new Date(today.getFullYear(), today.getMonth() - 1, 1)),
        to: toIso(new Date(today.getFullYear(), today.getMonth(), 0)),
      };
    case "this_fy": {
      const startYear = today.getMonth() + 1 >= fyStartMonth ? today.getFullYear() : today.getFullYear() - 1;
      return { from: toIso(new Date(startYear, fyStartMonth - 1, 1)), to: toIso(today) };
    }
    case "custom":
      return custom ?? { from: toIso(today), to: toIso(today) };
  }
}
