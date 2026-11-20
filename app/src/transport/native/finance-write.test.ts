import { beforeEach, describe, expect, it, vi } from "vitest";
import { financeWriteLocked, receivePayment } from "./finance-write";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));

const TOKEN = "a".repeat(64);
const OPERATION = "finance-write-key-0001";

function json(message: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify({ message }), { status });
}

function acknowledged() {
  return json({ acknowledged: true });
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

const PAYMENT = {
  operation_key: OPERATION,
  invoice: "SINV-1",
  mode_of_payment: "Cash" as const,
  amount: 20,
  allocations: [{ invoice: "SINV-1", amount: 20 }],
};

describe("native payment writes", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("receives a payment with a matching allocation and acknowledges once", async () => {
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
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "clinic_ui.api.finance_write.receive_payment",
    );
    const body = JSON.parse(
      String((fetchMock.mock.calls[0]?.[1] as RequestInit).body),
    );
    expect(body).toEqual(PAYMENT);
    expect(JSON.stringify(body)).not.toMatch(/card|cvv|pan/i);
    expect(financeWriteLocked("payment_receive")).toBe(false);
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

  it.each([
    { invoice: "SINV-other", outstanding: 0 },
    { invoice: "SINV-1", outstanding: 0.01 },
  ])("keeps the lock and never acknowledges an unexpected full settlement (%j)", async (result) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json({
      status: "received",
      payment: "PE-1",
      ackToken: TOKEN,
      ...result,
    })).mockResolvedValueOnce(acknowledged());

    await expect(receivePayment(PAYMENT, { expectFullSettlement: true })).rejects.toMatchObject({ code: "uncertain" });
    expect(financeWriteLocked("payment_receive")).toBe(true);
    await expect(receivePayment(PAYMENT, { expectFullSettlement: true })).rejects.toMatchObject({ code: "locked" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("maps a validation refusal and clears the pending lock", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({ code: "VALIDATION", problem: "mode_of_payment" }, 417),
    );

    await expect(receivePayment(PAYMENT)).rejects.toMatchObject({
      code: "validation",
    });
    expect(financeWriteLocked("payment_receive")).toBe(false);
  });

  it("never retries a payment after an uncertain dispatch", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("network"));

    await expect(receivePayment(PAYMENT)).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(financeWriteLocked("payment_receive")).toBe(true);
  });
});
