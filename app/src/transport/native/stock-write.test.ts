import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  adjustNativeStock,
  receiveNativeStock,
  StockAdjustOutcomeUnknownError,
  stockWriteLocked,
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
    operation_key: OPERATION,
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

  it("parses a batch-less transfer as success and acknowledges once", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        success("transferred", {
          kind: "stock_transfer",
          item_code: "ITEM-1",
          warehouse: "WH-1",
          target_warehouse: "WH-2",
          qty: 2,
          batch: null,
        }),
      )
      .mockResolvedValueOnce(acknowledged());

    await expect(transferNativeStock(transferInput())).resolves.toEqual({
      status: "transferred",
      movement: {
        kind: "stock_transfer",
        item_code: "ITEM-1",
        warehouse: "WH-1",
        target_warehouse: "WH-2",
        qty: 2,
        batch: null,
      },
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(stockWriteLocked("stock_transfer")).toBe(false);
  });

  it("still parses a batched transfer and a batch-less receipt", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        success("transferred", {
          kind: "stock_transfer",
          item_code: "ITEM-1",
          warehouse: "WH-1",
          target_warehouse: "WH-2",
          qty: 2,
          batch: "BATCH-1",
        }),
      )
      .mockResolvedValueOnce(acknowledged())
      .mockResolvedValueOnce(
        success("received", {
          kind: "stock_receive",
          item_code: "ITEM-2",
          warehouse: "WH-1",
          qty: 5,
          batch: null,
        }),
      )
      .mockResolvedValueOnce(acknowledged());

    await expect(transferNativeStock(transferInput())).resolves.toMatchObject({
      movement: { batch: "BATCH-1" },
    });
    await expect(
      receiveNativeStock({
        operation_key: OPERATION,
        item_code: "ITEM-2",
        qty: 5,
        warehouse: "WH-1",
        supplier: "SUP-1",
      }),
    ).resolves.toMatchObject({ movement: { batch: null } });
  });

  it("rejects a transfer whose batch field is neither text nor null", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      success("transferred", {
        kind: "stock_transfer",
        item_code: "ITEM-1",
        warehouse: "WH-1",
        target_warehouse: "WH-2",
        qty: 2,
        batch: 7,
      }),
    );

    await expect(transferNativeStock(transferInput())).rejects.toMatchObject({
      code: "uncertain",
    });
    expect(stockWriteLocked("stock_transfer")).toBe(true);
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
