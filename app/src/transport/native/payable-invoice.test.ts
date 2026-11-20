import { afterEach, describe, expect, it, vi } from "vitest";
import { listNativePayableInvoices, PAYABLE_INVOICE_PAGE_SIZE, PayableInvoiceReadError } from "./payable-invoice";

const invoice = (changes: Record<string, unknown> = {}) => ({
  name: "SINV-001", customer: "CUST-1", customer_name: "Billing party",
  posting_date: "2026-01-01", currency: "THB", grand_total: 120,
  outstanding_amount: 50, docstatus: 1, patient: "PAT-1", patient_name: "Patient One",
  ...changes,
});

function respond(data: unknown, status = 200) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(data), { status }));
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => vi.unstubAllGlobals());

describe("listNativePayableInvoices", () => {
  it("requests no patient fields and does not leak unexpected patient data", async () => {
    const mock = respond({ data: [
      invoice(),
      invoice({ name: "SINV-002", patient: "PAT-2", patient_name: "Patient Two" }),
      invoice({ name: "SINV-003", currency: "USD", patient: null, patient_name: "Unlinked" }),
      invoice({ name: "SINV-004", patient: "", patient_name: "" }),
    ] });
    const signal = new AbortController().signal;
    const result = await listNativePayableInvoices({ search: "clinic", offset: 25 }, signal);
    expect(result.rows).toEqual([
      { id: "SINV-001", customer: "CUST-1", customerName: "Billing party", postingDate: "2026-01-01", currency: "THB", total: 120, outstanding: 50 },
      { id: "SINV-002", customer: "CUST-1", customerName: "Billing party", postingDate: "2026-01-01", currency: "THB", total: 120, outstanding: 50 },
      { id: "SINV-003", customer: "CUST-1", customerName: "Billing party", postingDate: "2026-01-01", currency: "USD", total: 120, outstanding: 50 },
      { id: "SINV-004", customer: "CUST-1", customerName: "Billing party", postingDate: "2026-01-01", currency: "THB", total: 120, outstanding: 50 },
    ]);
    expect(result.hasMore).toBe(false);
    const [url, options] = mock.mock.calls[0]!;
    const parsed = new URL(url, "http://localhost");
    expect(parsed.pathname).toBe("/api/resource/Sales%20Invoice");
    expect(JSON.parse(parsed.searchParams.get("fields")!)).toEqual(["name", "customer", "customer_name", "posting_date", "currency", "grand_total", "outstanding_amount", "docstatus"]);
    expect(JSON.parse(parsed.searchParams.get("filters")!)).toEqual([["docstatus", "=", 1], ["outstanding_amount", ">", 0]]);
    expect(JSON.parse(parsed.searchParams.get("or_filters")!)).toEqual([["name", "like", "%clinic%"], ["customer", "like", "%clinic%"]]);
    expect(parsed.searchParams.get("order_by")).toBe("posting_date desc, name desc");
    expect(parsed.searchParams.get("limit_start")).toBe("25");
    expect(parsed.searchParams.get("limit_page_length")).toBe("26");
    expect(options).toEqual({ credentials: "same-origin", cache: "no-store", redirect: "error", signal });
  });

  it("filters currency and returns bounded page with hasMore", async () => {
    const mock = respond({ data: Array.from({ length: 26 }, (_, i) => invoice({ name: `SINV-${i}` })) });
    const result = await listNativePayableInvoices({ currency: "THB" });
    expect(PAYABLE_INVOICE_PAGE_SIZE).toBe(25);
    expect(result.rows).toHaveLength(PAYABLE_INVOICE_PAGE_SIZE);
    expect(result.hasMore).toBe(true);
    const params = new URL(mock.mock.calls[0]![0], "http://localhost").searchParams;
    expect(JSON.parse(params.get("filters")!)).toContainEqual(["currency", "=", "THB"]);
    expect(params.has("or_filters")).toBe(false);
  });

  it.each([
    { docstatus: 0 }, { outstanding_amount: 0 }, { outstanding_amount: "50" },
    { grand_total: null }, { name: "" }, { customer: " " }, { posting_date: "" }, { currency: "" },
  ])("fails closed for invalid candidate %o", async (changes) => {
    respond({ data: [invoice(changes)] });
    await expect(listNativePayableInvoices({})).rejects.toMatchObject({ kind: "unavailable" });
  });

  it("rejects mismatched currency and malformed envelopes", async () => {
    respond({ data: [invoice({ currency: "USD" })] });
    await expect(listNativePayableInvoices({ currency: "THB" })).rejects.toMatchObject({ kind: "unavailable" });
    respond({ message: [] });
    await expect(listNativePayableInvoices({})).rejects.toMatchObject({ kind: "unavailable" });
  });

  it.each(["thb", "EURO", "12$", "฿TH", "US "])("rejects malformed invoice currency %s", async (currency) => {
    respond({ data: [invoice({ currency })] });
    await expect(listNativePayableInvoices({})).rejects.toMatchObject({ kind: "unavailable" });
  });

  it.each(["thb", "EURO", "12$", "฿TH", "US "])("rejects malformed currency filter %s before fetch", async (currency) => {
    const mock = respond({ data: [] });
    await expect(listNativePayableInvoices({ currency })).rejects.toMatchObject({ kind: "unavailable" });
    expect(mock).not.toHaveBeenCalled();
  });

  it.each([401, 403])("reports denied status %i without retry", async (status) => {
    const mock = respond({}, status);
    const error = await listNativePayableInvoices({}).catch((err: unknown) => err);
    expect(error).toBeInstanceOf(PayableInvoiceReadError);
    expect(error).toMatchObject({ kind: "permission" });
    expect(mock).toHaveBeenCalledTimes(1);
  });

  it("rejects invalid offsets without fetching", async () => {
    const mock = respond({ data: [] });
    await expect(listNativePayableInvoices({ offset: -1 })).rejects.toThrow();
    expect(mock).not.toHaveBeenCalled();
  });

  it("preserves abort errors", async () => {
    const controller = new AbortController();
    const error = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => {
      controller.abort();
      return Promise.reject(error);
    }));
    await expect(listNativePayableInvoices({}, controller.signal)).rejects.toBe(error);
  });

  it("preserves abort errors while reading response body", async () => {
    const error = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.reject(error) }));
    await expect(listNativePayableInvoices({})).rejects.toBe(error);
  });
});
