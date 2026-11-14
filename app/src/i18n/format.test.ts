import { describe, expect, it } from "vitest";
import { formatDate, formatDateTime, formatMoney } from "./format";

describe("formatters", () => {
  it("formats dates deterministically in Gregorian Bangkok time", () => {
    expect(formatDate("2026-11-03T00:00:00Z", "th")).toBe("03/11/2026");
    expect(formatDate("2026-11-03T00:00:00Z", "en")).toBe("03/11/2026");
    expect(formatDateTime("2026-11-03T07:05:00Z", "en")).toBe(
      "03/11/2026 14:05",
    );
  });

  it("formats THB exactly per locale", () => {
    expect(formatMoney(1200, "th")).toBe("฿1,200.00");
    expect(formatMoney(1200, "en")).toBe("THB 1,200.00");
  });
});
