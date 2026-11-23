import { beforeEach, describe, expect, it, vi } from "vitest";
import { insertNativeInvoice, NativeInvoiceHttpError, previewNativeInvoice, submitNativeInvoice } from "./native-invoice";

const session = vi.hoisted(() => ({ token: "csrf" as string | null }));
vi.mock("./session", () => ({ nativeCsrfToken: () => session.token }));

const draft = { doctype: "Sales Invoice" as const, docstatus: 0 as const, customer: "CUST-1", items: [{ item_code: "ITEM-1", qty: 1 }] };
const saved = { ...draft, name: "SINV-1", grand_total: 12, net_total: 10, total_taxes_and_charges: 2 };

describe("native invoice transport", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    session.token = "csrf";
  });

  it.each([
    ["preview", previewNativeInvoice, "clinic_ui.api.native_invoice.preview", draft, draft],
    ["insert", insertNativeInvoice, "frappe.client.insert", draft, saved],
    ["submit", submitNativeInvoice, "frappe.client.submit", saved, { ...saved, docstatus: 1 }],
  ] as const)("%s posts the native doc once and returns actual response", async (_label, call, endpoint, input, result) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ message: result }));
    await expect(call(input)).resolves.toEqual(result);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(`/api/method/${endpoint}`, expect.objectContaining({
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: expect.objectContaining({ "content-type": "application/json", "X-Frappe-CSRF-Token": "csrf" }),
      body: JSON.stringify({ doc: input }),
    }));
  });

  it.each([
    {},
    { message: null },
    { message: { ...saved, doctype: "Purchase Invoice" } },
    { message: { ...saved, name: "" } },
    { message: { ...saved, docstatus: 1 } },
    { message: { ...saved, grand_total: "12" } },
    { message: { ...saved, items: "bad" } },
  ])("rejects malformed or unsafe insert response %#", async (body) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(body));
    await expect(insertNativeInvoice(draft)).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects a submitted response for a different invoice", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ message: { ...saved, name: "SINV-2", docstatus: 1 } }));
    await expect(submitNativeInvoice(saved)).rejects.toThrow();
  });

  it("rejects malformed preview and posted responses", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ message: { ...draft, docstatus: 1 } }))
      .mockResolvedValueOnce(new Response("{", { status: 200 }));
    await expect(previewNativeInvoice(draft)).rejects.toThrow();
    await expect(submitNativeInvoice(saved)).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("passes native draft fields and taxes through unchanged for submission", async () => {
    const doc = { ...saved, taxes: [{ account_head: "Tax", rate: 5 }], custom_field: "native" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ message: { ...doc, docstatus: 1 } }));
    await expect(submitNativeInvoice(doc)).resolves.toMatchObject({ taxes: doc.taxes });
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({ doc });
  });

  it("preserves native HTTP error status and response body without retry", async () => {
    const body = { exception: "frappe.exceptions.PermissionError", _server_messages: '["Denied"]' };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(body, { status: 403 }));
    const error = await insertNativeInvoice(draft).catch((cause) => cause);
    expect(error).toBeInstanceOf(NativeInvoiceHttpError);
    expect(error).toMatchObject({ status: 403, body });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses to send without session CSRF token", async () => {
    session.token = null;
    const fetchMock = vi.spyOn(globalThis, "fetch");
    await expect(previewNativeInvoice(draft)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
