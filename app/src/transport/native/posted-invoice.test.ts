import { afterEach, describe, expect, it, vi } from "vitest";
import { getNativePostedInvoiceDetail } from "./posted-invoice";

const doc = () => ({
  name: "SINV-001", docstatus: 1, status: "Unpaid", customer: "Customer", company: "Clinic",
  posting_date: "2026-01-01", due_date: "2026-01-31", currency: "THB",
  total: 120, discount_amount: 10, net_total: 110, total_taxes_and_charges: 7,
  grand_total: 117, rounded_total: 118, outstanding_amount: 117,
  items: [{ item_code: "A", item_name: "Consultation", qty: 2, uom: "Unit", rate: 60, amount: 120, net_amount: 110 }],
  taxes: [{ description: "Tax", rate: 7, tax_amount: 8, tax_amount_after_discount_amount: 7 }],
});

afterEach(() => vi.unstubAllGlobals());

function respond(data: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(data), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("getNativePostedInvoiceDetail", () => {
  it("reads and maps only native posted invoice fields", async () => {
    const fetchMock = respond({ data: doc() });
    const signal = new AbortController().signal;
    await expect(getNativePostedInvoiceDetail("SINV-001", signal)).resolves.toEqual({
      id: "SINV-001", status: "Unpaid", customer: "Customer", company: "Clinic",
      postingDate: "2026-01-01", dueDate: "2026-01-31", currency: "THB",
      subtotal: 120, discount: 10, netTotal: 110, taxTotal: 7, grandTotal: 117,
      roundedTotal: 118, outstanding: 117,
      items: [{ code: "A", name: "Consultation", quantity: 2, uom: "Unit", rate: 60, amount: 120, netAmount: 110 }],
      taxes: [{ description: "Tax", rate: 7, amount: 7 }],
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/resource/Sales%20Invoice/SINV-001", {
      credentials: "same-origin", cache: "no-store", redirect: "error", signal,
    });
  });

  it("omits absent optional values and uses actual fallback tax amount", async () => {
    const value = doc();
    respond({ data: { ...value, due_date: null, rounded_total: null, items: [{ ...value.items[0], item_code: null }],
      taxes: [{ description: "Tax", tax_amount: 8 }] } });
    const result = await getNativePostedInvoiceDetail("SINV-001");
    expect(result.dueDate).toBeUndefined();
    expect(result.roundedTotal).toBeUndefined();
    expect(result.items[0]?.code).toBeUndefined();
    expect(result.taxes).toEqual([{ description: "Tax", amount: 8 }]);
  });

  it("falls back to numeric tax_amount when discounted tax is not numeric", async () => {
    respond({ data: { ...doc(), taxes: [{ description: "Tax", tax_amount: 8, tax_amount_after_discount_amount: "masked" }] } });
    expect((await getNativePostedInvoiceDetail("SINV-001")).taxes).toEqual([{ description: "Tax", amount: 8 }]);
  });

  it.each([
    ["missing header", { customer: undefined }],
    ["masked header", { customer: "XXXXXXXX" }],
    ["missing total", { total: undefined }],
    ["string amount", { total: "120" }],
    ["non-finite amount", { total: Infinity }],
    ["mismatched id", { name: "SINV-002" }],
    ["draft", { docstatus: 0 }],
    ["missing status", { status: "" }],
    ["missing items", { items: undefined }],
    ["empty items", { items: [] }],
    ["malformed item", { items: [{ item_name: "XXXXXXXX", qty: 1 }] }],
    ["missing taxes", { taxes: undefined }],
    ["malformed tax", { taxes: [{ description: "Tax", tax_amount: "8" }] }],
    ["oversized collection", { taxes: Array.from({ length: 1001 }, () => ({})) }],
    ["oversized text", { customer: "a".repeat(1025) }],
  ])("rejects %s", async (_label, changes) => {
    respond({ data: { ...doc(), ...changes } });
    await expect(getNativePostedInvoiceDetail("SINV-001")).rejects.toThrow();
  });

  it.each(["", " SINV-001", "../other", "SINV%2Fother", "XXXXXXXX", "a".repeat(257)])("rejects unsafe id %s before fetch", async (id) => {
    const fetchMock = respond({ data: doc() });
    await expect(getNativePostedInvoiceDetail(id)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([401, 403])("rejects denied HTTP %i", async (status) => {
    respond({}, status);
    await expect(getNativePostedInvoiceDetail("SINV-001")).rejects.toMatchObject({ reason: "denied" });
  });

  it("rejects malformed envelopes", async () => {
    respond({ message: doc() });
    await expect(getNativePostedInvoiceDetail("SINV-001")).rejects.toThrow();
  });
});
