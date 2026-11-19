import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  InvoiceWriteInFlightError,
  InvoiceWriteOutcomeUnknownError,
  InvoiceWriteRejectedError,
  previewInvoice,
  submitInvoice,
} from "./invoice-write";

const session = vi.hoisted(() => ({ token: "csrf" as string | null }));
vi.mock("./session", () => ({ nativeCsrfToken: () => session.token }));

const TOKEN = "a".repeat(64);
const OPERATION = "invoice-write-key-0001";

function json(message: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify({ message }), { status });
}

function previewMessage(available = false) {
  return {
    status: "preview",
    customer: "CUST-1",
    currency: "USD",
    company: "SIT Synthetic Company USD",
    selling_price_list: "Standard Selling",
    due_date: "2026-11-30",
    lines: [{ item_code: "ITEM-1", qty: 2, rate: 10, amount: 20 }],
    net_total: 20,
    discount: 0,
    discount_available: available,
    total_taxes: 0,
    total: 20,
  };
}

function preview(available = false) {
  return json(previewMessage(available));
}

function submitted(total = 20) {
  return json({ status: "submitted", invoice: "SINV-1", total });
}

const INVOICE = {
  operation_key: OPERATION,
  customer: "CUST-1",
  selling_price_list: "Standard Selling",
  due_date: "2026-11-30",
  items: [{ item_code: "ITEM-1", qty: 2 }],
};

describe("native invoice writes", () => {
  beforeEach(() => {
    localStorage.clear();
    session.token = "csrf";
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
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.finance_write.save_invoice_draft",
    );
    expect(localStorage.length).toBe(0);
  });

  it("refuses a preview that is not the reviewed shape", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({ ...previewMessage(), total: "20" }),
    );

    await expect(previewInvoice(INVOICE)).rejects.toBeInstanceOf(
      InvoiceWriteOutcomeUnknownError,
    );
  });

  it("submits one invoice without acknowledgement", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(submitted());

    await expect(submitInvoice(INVOICE)).resolves.toEqual({
      status: "submitted",
      invoice: "SINV-1",
      total: 20,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.finance_write.submit_invoice",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      INVOICE,
    );
    expect(localStorage.length).toBe(0);
  });

  it("ignores the retired browser fence key", async () => {
    localStorage.setItem("clinic.finance-write.pending.invoice_submit", "1");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(submitted());

    await expect(submitInvoice(INVOICE)).resolves.toMatchObject({
      invoice: "SINV-1",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends at most the fields the contract accepts", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(submitted());

    await submitInvoice({ ...INVOICE, discount: 0 });

    const body = JSON.parse(
      String((fetchMock.mock.calls[0]?.[1] as RequestInit).body),
    );
    expect(body).toEqual(INVOICE);
    expect(body).not.toHaveProperty("discount");
    expect(body).not.toHaveProperty("rate");
    expect(body).not.toHaveProperty("grand_total");
  });

  it("rejects the legacy ackToken envelope as an unknown outcome", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        json({ status: "submitted", invoice: "SINV-1", total: 20, ackToken: TOKEN }),
      );

    await expect(submitInvoice(INVOICE)).rejects.toBeInstanceOf(
      InvoiceWriteOutcomeUnknownError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.length).toBe(0);
  });

  it("rejects any unexpected success field as an unknown outcome", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({ status: "submitted", invoice: "SINV-1", total: 20, receipt: "R" }),
    );

    await expect(submitInvoice(INVOICE)).rejects.toBeInstanceOf(
      InvoiceWriteOutcomeUnknownError,
    );
  });

  it("blocks a second dispatch while the first is in flight", async () => {
    let resolveFetch!: (response: Response) => void;
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockReturnValue(
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
      );

    const first = submitInvoice(INVOICE);
    await expect(submitInvoice(INVOICE)).rejects.toBeInstanceOf(
      InvoiceWriteInFlightError,
    );
    resolveFetch(submitted());
    await expect(first).resolves.toMatchObject({ invoice: "SINV-1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["auth", 401, "UNAUTHENTICATED"],
    ["forbidden", 403, "FORBIDDEN"],
    ["validation", 422, "VALIDATION"],
    ["conflict", 409, "OPERATION_CONFLICT"],
  ] as const)(
    "maps a %s refusal without retrying",
    async (kind, status, code) => {
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(json({ code }, status));

      const failure = await submitInvoice(INVOICE).catch((error) => error);

      expect(failure).toBeInstanceOf(InvoiceWriteRejectedError);
      expect(failure).toMatchObject({ kind, status, code });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(localStorage.length).toBe(0);
    },
  );

  it("keeps an unclassified 409 as an unknown outcome", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({ code: "WRITE_LOCKED" }, 409),
    );

    await expect(submitInvoice(INVOICE)).rejects.toBeInstanceOf(
      InvoiceWriteOutcomeUnknownError,
    );
  });

  it("keeps a lost connection as an unknown outcome and does not retry", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("network"));

    await expect(submitInvoice(INVOICE)).rejects.toBeInstanceOf(
      InvoiceWriteOutcomeUnknownError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a malformed success as an unknown outcome", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("not json", { status: 200 }),
    );

    await expect(submitInvoice(INVOICE)).rejects.toBeInstanceOf(
      InvoiceWriteOutcomeUnknownError,
    );
  });

  it("refuses to dispatch without a session token", async () => {
    session.token = null;
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(submitInvoice(INVOICE)).rejects.toMatchObject({
      kind: "auth",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
