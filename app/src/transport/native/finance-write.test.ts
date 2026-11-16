import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  financeWriteLocked,
  previewInvoice,
  receivePayment,
  submitInvoice,
} from "./finance-write";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));

const TOKEN = "a".repeat(64);
const OPERATION = "finance-write-key-0001";

function json(message: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify({ message }), { status });
}

function acknowledged() {
  return json({ acknowledged: true });
}

function preview() {
  return json({
    status: "preview",
    customer: "CUST-1",
    currency: "USD",
    company: "SIT Synthetic Company USD",
    selling_price_list: "Standard Selling",
    due_date: "2026-11-30",
    lines: [{ item_code: "ITEM-1", qty: 2, rate: 10, amount: 20 }],
    net_total: 20,
    discount: 0,
    discount_available: false,
    total_taxes: 0,
    total: 20,
  });
}

function submitted() {
  return json({
    status: "submitted",
    invoice: "SINV-1",
    total: 20,
    ackToken: TOKEN,
  });
}

function received(outstanding = 0) {
  return json({
    status: "received",
    payment: "PE-1",
    invoice: "SINV-1",
    outstanding,
    ackToken: TOKEN,
  });
}

const INVOICE = {
  operation_key: OPERATION,
  customer: "CUST-1",
  selling_price_list: "Standard Selling",
  due_date: "2026-11-30",
  items: [{ item_code: "ITEM-1", qty: 2 }],
};

const PAYMENT = {
  operation_key: OPERATION,
  invoice: "SINV-1",
  mode_of_payment: "Cash" as const,
  amount: 20,
  allocations: [{ invoice: "SINV-1", amount: 20 }],
};

describe("native finance writes", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("previews the server document without dispatching a write", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(preview());

    const result = await previewInvoice(INVOICE);

    expect(result).toMatchObject({
      status: "preview",
      currency: "USD",
      total: 20,
      total_taxes: 0,
      discount_available: false,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "clinic_ui.api.finance_write.save_invoice_draft",
    );
    expect(financeWriteLocked("invoice_submit")).toBe(false);
  });

  it("refuses a preview that is not the reviewed shape", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({
        status: "preview",
        customer: "CUST-1",
        currency: "USD",
        company: "C",
        selling_price_list: "Standard Selling",
        due_date: "2026-11-30",
        lines: [{ item_code: "ITEM-1", qty: 2, rate: 10, amount: 20 }],
        net_total: 20,
        discount: 0,
        discount_available: false,
        total_taxes: 0,
        total: "20",
      }),
    );

    await expect(previewInvoice(INVOICE)).rejects.toMatchObject({
      code: "uncertain",
    });
  });

  it("submits one invoice and acknowledges once", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(acknowledged());

    await expect(submitInvoice(INVOICE)).resolves.toEqual({
      status: "submitted",
      invoice: "SINV-1",
      total: 20,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const body = JSON.parse(
      String((fetchMock.mock.calls[0]?.[1] as RequestInit).body),
    );
    expect(body).toEqual(INVOICE);
    expect(financeWriteLocked("invoice_submit")).toBe(false);
  });

  it("sends at most the fields the contract accepts", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(acknowledged());

    await submitInvoice({ ...INVOICE, discount: 0 });

    const body = JSON.parse(
      String((fetchMock.mock.calls[0]?.[1] as RequestInit).body),
    );
    expect(body).not.toHaveProperty("discount");
    expect(body).not.toHaveProperty("rate");
    expect(body).not.toHaveProperty("grand_total");
  });

  it("receives a payment with a matching allocation and hides no field", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(received())
      .mockResolvedValueOnce(acknowledged());

    const result = await receivePayment(PAYMENT);

    expect(result).toEqual({
      status: "received",
      payment: "PE-1",
      invoice: "SINV-1",
      outstanding: 0,
    });
    const body = JSON.parse(
      String((fetchMock.mock.calls[0]?.[1] as RequestInit).body),
    );
    expect(body).toEqual(PAYMENT);
    expect(JSON.stringify(body)).not.toMatch(/card|cvv|pan/i);
  });

  it("refuses a receipt whose payload is not the reviewed shape", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({
        status: "received",
        payment: "PE-1",
        invoice: "SINV-1",
        outstanding: "0",
        ackToken: TOKEN,
      }),
    );

    await expect(receivePayment(PAYMENT)).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(financeWriteLocked("payment_receive")).toBe(true);
  });

  it("maps a validation refusal and keeps every kind locked separately", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({ code: "VALIDATION", problem: "mode_of_payment" }, 417),
    );

    await expect(receivePayment(PAYMENT)).rejects.toMatchObject({
      code: "validation",
    });
    expect(financeWriteLocked("payment_receive")).toBe(false);
    expect(financeWriteLocked("invoice_submit")).toBe(false);
  });

  it("never retries after an uncertain dispatch", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("network"));

    await expect(submitInvoice(INVOICE)).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(financeWriteLocked("invoice_submit")).toBe(true);
  });
});
