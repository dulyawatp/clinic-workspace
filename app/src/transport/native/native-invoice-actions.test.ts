import { describe, expect, it, vi } from "vitest";
import { createNativeInvoiceActions } from "./native-invoice-actions";
import { NativeInvoiceHttpError } from "./native-invoice";

const native = vi.hoisted(() => ({ preview: vi.fn(), insert: vi.fn(), submit: vi.fn() }));
vi.mock("./native-invoice", async (importOriginal) => ({
  ...await importOriginal<typeof import("./native-invoice")>(),
  previewNativeInvoice: native.preview,
  insertNativeInvoice: native.insert,
  submitNativeInvoice: native.submit,
}));

const draft = { doctype: "Sales Invoice" as const, currency: "THB", items: [{ item_code: "A", item_name: "Service", qty: 1 }] };
const calculated = { ...draft, docstatus: 0 as const, name: "INV-1", net_total: 100, total: 100, discount_amount: 0, total_taxes_and_charges: 7, grand_total: 107, rounded_total: 107, taxes: [{ description: "VAT", tax_amount: 7 }] };
const none = { discount: { kind: "none" as const } };

describe("native invoice actions", () => {
  it("prepares without network and reviews native numeric totals and discount inputs", async () => {
    native.preview.mockReset().mockResolvedValue(calculated);
    const actions = createNativeInvoiceActions(draft, vi.fn());
    expect(native.preview).not.toHaveBeenCalled();
    expect(actions.preparation.lines).toEqual([{ id: "A-0", description: "Service", quantity: 1 }]);
    expect(await actions.reviewInvoice({ discount: { kind: "percent", value: "10" } })).toMatchObject({
      subtotal: 100, taxTotal: 7, grandTotal: 107, taxes: [{ label: "VAT", amount: 7 }],
    });
    expect(native.preview).toHaveBeenCalledWith({ ...draft, discount_amount: 0, additional_discount_percentage: 10 });
  });

  it("inserts original draft with reviewed input then submits returned draft", async () => {
    native.preview.mockResolvedValue(calculated);
    native.insert.mockReset().mockResolvedValue(calculated);
    native.submit.mockReset().mockResolvedValue({ ...calculated, docstatus: 1 });
    const actions = createNativeInvoiceActions(draft, vi.fn());
    await actions.reviewInvoice(none);
    expect(await actions.submitInvoice(none)).toMatchObject({ status: "issued", invoice: { id: "INV-1", grandTotal: 107 } });
    expect(native.insert).toHaveBeenCalledWith({ ...draft, discount_amount: 0, additional_discount_percentage: 0 });
    expect(native.submit).toHaveBeenCalledOnce();
    expect(native.submit).toHaveBeenCalledWith(calculated);
  });

  it("returns actual saved draft without submitting when reviewed total changes", async () => {
    native.preview.mockResolvedValue(calculated);
    native.insert.mockResolvedValue({ ...calculated, grand_total: 120 });
    native.submit.mockReset();
    const actions = createNativeInvoiceActions(draft, vi.fn());
    await actions.reviewInvoice(none);
    expect(await actions.submitInvoice(none)).toMatchObject({ status: "draft", draft: { id: "INV-1", document: { name: "INV-1", grand_total: 120 }, review: { grandTotal: 120 } } });
    expect(native.submit).not.toHaveBeenCalled();
  });

  it("distinguishes definitive errors from unknown outcomes and preserves saved ID", async () => {
    const actions = createNativeInvoiceActions(draft, vi.fn());
    native.insert.mockRejectedValueOnce(new NativeInvoiceHttpError(403, { exception: "Denied" }));
    expect(await actions.submitInvoice(none)).toEqual({ status: "rejected" });
    native.insert.mockRejectedValueOnce(new TypeError("network"));
    expect(await actions.submitInvoice(none)).toEqual({ status: "unknown" });
    native.insert.mockResolvedValueOnce(calculated);
    native.submit.mockRejectedValueOnce(new TypeError("network"));
    expect(await actions.submitInvoice(none)).toEqual({ status: "unknown", draft: { id: "INV-1" } });
    native.insert.mockResolvedValueOnce(calculated);
    native.submit.mockRejectedValueOnce(new NativeInvoiceHttpError(417, { exception: "ValidationError" }));
    expect(await actions.submitInvoice(none)).toEqual({ status: "draft", draft: { id: "INV-1" } });
  });

  it("checks exact ID, never treats missing as absent, and only observes submitted as issued", async () => {
    const read = vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce(calculated).mockResolvedValueOnce({ ...calculated, docstatus: 1 });
    const actions = createNativeInvoiceActions(draft, read);
    expect(await actions.checkInvoiceStatus({})).toEqual({ status: "unknown" });
    expect(await actions.checkInvoiceStatus({ draftId: "INV-1" })).toEqual({ status: "unknown", draft: { id: "INV-1" } });
    expect(await actions.checkInvoiceStatus({ draftId: "INV-1" })).toEqual({ status: "unknown", draft: { id: "INV-1" } });
    expect(await actions.checkInvoiceStatus({ draftId: "INV-1" })).toMatchObject({ status: "issued", invoice: { id: "INV-1" } });
    expect(read).toHaveBeenCalledTimes(3);
    expect(read).toHaveBeenCalledWith("INV-1");
  });

  it("gives duplicate item rows distinct stable identities", () => {
    const item = draft.items[0]!;
    const actions = createNativeInvoiceActions({ ...draft, items: [item, item] }, vi.fn());
    expect(actions.preparation.lines.map((line) => line.id)).toEqual(["A-0", "A-1"]);
  });

  it("strips unsaved document metadata while retaining business and child fields", async () => {
    const prepared = {
      ...draft, name: "new-sales-invoice-1", owner: "old", creation: "old", modified: "old",
      modified_by: "old", docstatus: 0 as const, __islocal: 1, __unsaved: 1, flags: { ignore_permissions: true },
      items: [{ ...draft.items[0], doctype: "Sales Invoice Item", name: "new-item-1", parent: "new-sales-invoice-1", parentfield: "items", parenttype: "Sales Invoice", idx: 1, owner: "old", creation: "old", modified: "old", modified_by: "old", docstatus: 0, __islocal: 1, flags: { x: 1 }, insurance_payor: "INS-1" }],
      taxes: [{ doctype: "Sales Taxes and Charges", name: "new-tax-1", parent: "new-sales-invoice-1", account_head: "VAT", rate: 7 }],
      insurance_details: [{ doctype: "Insurance Detail", name: "new-ins-1", parent: "new-sales-invoice-1", policy: "POL-1" }],
    };
    const expected = {
      ...draft, items: [{ ...draft.items[0], doctype: "Sales Invoice Item", insurance_payor: "INS-1" }],
      taxes: [{ doctype: "Sales Taxes and Charges", account_head: "VAT", rate: 7 }],
      insurance_details: [{ doctype: "Insurance Detail", policy: "POL-1" }],
      discount_amount: 0, additional_discount_percentage: 0,
    };
    native.insert.mockReset().mockResolvedValue(calculated);
    native.submit.mockReset().mockResolvedValue({ ...calculated, docstatus: 1 });
    const actions = createNativeInvoiceActions(prepared, vi.fn());
    await actions.submitInvoice(none);
    expect(native.insert).toHaveBeenCalledWith(expected);
    expect(native.submit).toHaveBeenCalledWith(calculated);
  });
});
