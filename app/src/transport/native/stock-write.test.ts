import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  adjustNativeStock,
  receiveNativeStock,
  StockAdjustOutcomeUnknownError,
  transferNativeStock,
} from "./stock-write";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));

const TOKEN = "a".repeat(64);
const OPERATION = "stock-write-key-0001";

function success(status: string, movement: Record<string, unknown>) {
  return new Response(
    JSON.stringify({ message: { status, movement, ackToken: TOKEN } }),
    { status: 200 },
  );
}

function acknowledged() {
  return new Response(JSON.stringify({ message: { acknowledged: true } }), {
    status: 200,
  });
}

function transferInput() {
  return {
    company: "Clinic",
    item_code: "ITEM-1",
    qty: 2,
    source_warehouse: "WH-1",
    target_warehouse: "WH-2",
  };
}

describe("native stock writes", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("posts one native transfer without lock or acknowledgement", async () => {
    localStorage.setItem("clinic.stock-write.pending.stock_transfer", "1");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ message: { status: "transferred", stock_entry: { stock_entry_id: "STE-1", docstatus: 1 } } }), { status: 200 }),
    );
    await expect(transferNativeStock(transferInput())).resolves.toEqual({
      status: "transferred", stock_entry: { stock_entry_id: "STE-1", docstatus: 1 },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(transferInput());
  });

  it("does not retry or lock after an unknown transfer outcome", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("offline"));
    await expect(transferNativeStock(transferInput())).rejects.toBeInstanceOf(StockAdjustOutcomeUnknownError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.length).toBe(0);
  });

  it("preserves batch-less receipt acknowledgement", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(success("received", {
      kind: "stock_receive", item_code: "ITEM-2", warehouse: "WH-1", qty: 5, batch: null,
    })).mockResolvedValueOnce(acknowledged());
    await expect(receiveNativeStock({ operation_key: OPERATION, item_code: "ITEM-2", qty: 5, warehouse: "WH-1", supplier: "SUP-1" })).resolves.toMatchObject({ movement: { batch: null } });
  });

  it("posts one reconciliation with no lock and no acknowledgement", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            message: {
              status: "adjusted",
              reconciliation: { reconciliation_id: "SR-1", docstatus: 1 },
            },
          }),
          { status: 200 },
        ),
      );

    await expect(
      adjustNativeStock({
        operation_key: OPERATION,
        purpose: "Opening Stock",
        company: "Clinic",
        item_code: "ITEM-1",
        qty: 3,
        warehouse: "WH-1",
        valuation_rate: 5,
        expense_account: "Opening - C",
      }),
    ).resolves.toEqual({
      status: "adjusted",
      reconciliation: { reconciliation_id: "SR-1", docstatus: 1 },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      operation_key: OPERATION,
      purpose: "Opening Stock",
      company: "Clinic",
      item_code: "ITEM-1",
      qty: 3,
      warehouse: "WH-1",
      valuation_rate: 5,
      expense_account: "Opening - C",
    });
    expect(localStorage.length).toBe(0);
  });

  it("treats a 409 lock as unknown for a reconciliation and never retries", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ message: { code: "WRITE_LOCKED" } }), {
        status: 409,
      }),
    );

    await expect(
      adjustNativeStock({
        operation_key: OPERATION,
        purpose: "Stock Reconciliation",
        company: "Clinic",
        item_code: "ITEM-1",
        qty: 3,
        warehouse: "WH-1",
        valuation_rate: 5,
        expense_account: "",
      }),
    ).rejects.toBeInstanceOf(StockAdjustOutcomeUnknownError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.length).toBe(0);
  });

  it.each([
    null,
    { message: { status: "adjusted" } },
    { message: { status: "adjusted", reconciliation: { reconciliation_id: "", docstatus: 1 } } },
    { message: { status: "adjusted", reconciliation: { reconciliation_id: "SR-1", docstatus: 0 } } },
    { message: { status: "draft", reconciliation: { reconciliation_id: "SR-1", docstatus: 1 } } },
  ])("treats malformed successful reconciliation response as unknown", async (body) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(body), { status: 200 }),
    );
    await expect(adjustNativeStock({
      operation_key: OPERATION,
      purpose: "Stock Reconciliation",
      company: "Clinic",
      item_code: "ITEM-1",
      qty: 3,
      warehouse: "WH-1",
    })).rejects.toBeInstanceOf(StockAdjustOutcomeUnknownError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.length).toBe(0);
  });

  it("keeps a 417 reconciliation refusal deterministic", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ message: { code: "VALIDATION" } }), {
        status: 417,
      }),
    );

    await expect(
      adjustNativeStock({
        operation_key: OPERATION,
        purpose: "Stock Reconciliation",
        company: "Clinic",
        item_code: "ITEM-1",
        qty: 3,
        warehouse: "WH-1",
        valuation_rate: 5,
        expense_account: "",
      }),
    ).rejects.toMatchObject({ kind: "validation" });
  });
});
