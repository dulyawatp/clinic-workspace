import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  purchaseWriteLocked,
  receivePurchaseOrder,
  submitPurchaseOrder,
} from "./purchase-write";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));

const TOKEN = "a".repeat(64);
const OPERATION = "purchase-write-key-0001";

function json(message: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify({ message }), { status });
}

function acknowledged() {
  return json({ acknowledged: true });
}

function receipt() {
  return json({
    status: "received",
    movement: {
      kind: "purchase_receipt",
      purchase_order: "PO-1",
      item_code: "ITEM-1",
      warehouse: "WH-1",
      qty: 2,
      batch: null,
    },
    ackToken: TOKEN,
  });
}

describe("native purchase writes", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("submits an existing draft without any submit flag in the body", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        json({ status: "submitted", purchase_order: "PO-1", ackToken: TOKEN }),
      )
      .mockResolvedValueOnce(acknowledged());

    await expect(
      submitPurchaseOrder({ operation_key: OPERATION, purchase_order: "PO-1" }),
    ).resolves.toEqual({ status: "submitted", purchase_order: "PO-1" });
    const body = JSON.parse(
      String((fetchMock.mock.calls[0]?.[1] as RequestInit).body),
    );
    expect(body).toEqual({
      operation_key: OPERATION,
      purchase_order: "PO-1",
    });
  });

  it("receives against the purchase order and hides the native reference", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(receipt())
      .mockResolvedValueOnce(acknowledged());

    const result = await receivePurchaseOrder({
      operation_key: OPERATION,
      purchase_order: "PO-1",
      item_code: "ITEM-1",
      qty: 2,
      warehouse: "WH-1",
    });

    expect(result).toMatchObject({
      status: "received",
      movement: { purchase_order: "PO-1", batch: null },
    });
    expect(JSON.stringify(result)).not.toContain("PR-");
  });

  it("refuses a receipt whose payload is not the reviewed shape", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({
        status: "received",
        movement: {
          kind: "purchase_receipt",
          purchase_order: "PO-1",
          item_code: "ITEM-1",
          warehouse: "WH-1",
          qty: "2",
          batch: null,
        },
        ackToken: TOKEN,
      }),
    );

    await expect(
      receivePurchaseOrder({
        operation_key: OPERATION,
        purchase_order: "PO-1",
        item_code: "ITEM-1",
        qty: 2,
        warehouse: "WH-1",
      }),
    ).rejects.toMatchObject({ code: "uncertain" });
    expect(purchaseWriteLocked("purchase_receipt")).toBe(true);
  });

  it("maps a validation refusal and keeps every kind locked separately", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      json({ code: "VALIDATION" }, 417),
    );

    await expect(
      submitPurchaseOrder({ operation_key: OPERATION, purchase_order: "PO-1" }),
    ).rejects.toMatchObject({ code: "validation" });
    expect(purchaseWriteLocked("purchase_order_draft")).toBe(false);
  });

  it("never retries after an uncertain dispatch", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("network"));

    await expect(
      submitPurchaseOrder({ operation_key: OPERATION, purchase_order: "PO-1" }),
    ).rejects.toMatchObject({ code: "uncertain" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(purchaseWriteLocked("purchase_order_submit")).toBe(true);
  });
});
