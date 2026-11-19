import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  PurchaseDraftInFlightError,
  PurchaseDraftOutcomeUnknownError,
  PurchaseDraftRejectedError,
  draftPurchaseOrder,
  findPurchaseDrafts,
  viewPurchaseOrder,
} from "./purchase-draft";

const session = vi.hoisted(() => ({ token: "csrf" as string | null }));
vi.mock("./session", () => ({ nativeCsrfToken: () => session.token }));

const OPERATION = "purchase-draft-key-0001";
const STALE_LOCK = "clinic.purchase-write.pending.purchase_order_draft";

const input = {
  operation_key: OPERATION,
  supplier: "SUP-1",
  items: [{ item_code: "ITEM-1", qty: 5, rate: 2.5 }],
};

function created(
  purchase_order: Record<string, unknown> = {
    purchase_order_id: "PO-DRAFT-0001",
    status: "Draft",
    docstatus: 0,
  },
) {
  return new Response(
    JSON.stringify({ message: { status: "created", purchase_order } }),
    { status: 200 },
  );
}

describe("native purchase draft reads", () => {
  const order = { purchase_order_id: "PO /& 1", status: "Draft", docstatus: 0 };

  beforeEach(() => vi.restoreAllMocks());

  it("encodes the supplier and reads a valid draft list with one GET", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ message: { items: [order] } }),
    );
    await expect(findPurchaseDrafts("SUP /& 1")).resolves.toEqual([order]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/method/clinic_ui.api.purchase_write.drafts?supplier=SUP+%2F%26+1",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: "same-origin",
      cache: "no-store",
    });
    expect(fetchMock.mock.calls[0]?.[1]?.method ?? "GET").toBe("GET");
  });

  it("encodes the order ID and reads a valid view with one GET", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ message: { purchase_order: order } }),
    );
    await expect(viewPurchaseOrder(order.purchase_order_id)).resolves.toEqual(order);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/method/clinic_ui.api.purchase_write.view?purchase_order_id=PO+%2F%26+1",
    );
    expect(fetchMock.mock.calls[0]?.[1]?.method ?? "GET").toBe("GET");
  });

  it.each([401, 403])("rejects status %i for both reads without retry", async (status) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () => new Response("denied", { status }),
    );
    await expect(findPurchaseDrafts("SUP-1")).rejects.toThrow();
    await expect(viewPurchaseOrder("PO-1")).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["missing envelope", {}],
    ["invalid message", { message: [] }],
    ["missing items", { message: {} }],
    ["invalid item", { message: { items: [{ purchase_order_id: "PO-1", status: "Draft", docstatus: "0" }] } }],
  ])("rejects draft list with %s", async (_label, body) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(body));
    await expect(findPurchaseDrafts("SUP-1")).rejects.toThrow();
  });

  it.each([
    ["missing envelope", {}],
    ["invalid message", { message: [] }],
    ["missing order", { message: {} }],
    ["invalid order", { message: { purchase_order: { purchase_order_id: "", status: "Draft", docstatus: 0 } } }],
  ])("rejects order view with %s", async (_label, body) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(body));
    await expect(viewPurchaseOrder("PO-1")).rejects.toThrow();
  });

  it.each(["drafts", "view"] as const)("rejects malformed JSON for %s", async (kind) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{", { status: 200 }));
    await expect(kind === "drafts" ? findPurchaseDrafts("SUP-1") : viewPurchaseOrder("PO-1")).rejects.toThrow();
  });
});

describe("native purchase draft", () => {
  beforeEach(() => {
    localStorage.clear();
    session.token = "csrf";
    vi.restoreAllMocks();
  });

  it("posts one draft request and never acknowledges", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(created());

    await expect(draftPurchaseOrder(input)).resolves.toEqual({
      status: "created",
      purchase_order_id: "PO-DRAFT-0001",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/method/clinic_ui.api.purchase_write.save_po_draft",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(
      input,
    );
    expect(
      (fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>)[
        "X-Frappe-CSRF-Token"
      ],
    ).toBe("csrf");
    expect(localStorage.length).toBe(0);
  });

  it("ignores a retired draft lock and never writes a new one", async () => {
    localStorage.setItem(STALE_LOCK, "1");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(created());

    await expect(draftPurchaseOrder(input)).resolves.toMatchObject({
      status: "created",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(STALE_LOCK)).toBe("1");
  });

  it.each([
    ["auth", 401, "UNAUTHENTICATED"],
    ["forbidden", 403, "FORBIDDEN"],
    ["validation", 422, "VALIDATION"],
    ["validation", 417, "VALIDATION"],
    ["conflict", 409, "OPERATION_CONFLICT"],
  ] as const)(
    "maps a %s refusal to a known failure with the native status and code",
    async (kind, status, code) => {
      const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ message: { code } }), { status }),
      );
      const failure = await draftPurchaseOrder(input).catch((error) => error);
      expect(failure).toBeInstanceOf(PurchaseDraftRejectedError);
      expect(failure).toMatchObject({ kind, status, code });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(localStorage.length).toBe(0);
    },
  );

  it("keeps an unclassified 409 as an unknown outcome", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ message: { code: "WRITE_LOCKED" } }), {
        status: 409,
      }),
    );
    await expect(draftPurchaseOrder(input)).rejects.toBeInstanceOf(
      PurchaseDraftOutcomeUnknownError,
    );
  });

  it.each([
    ["5xx", new Response("upstream", { status: 503 })],
    ["unreadable body", new Response("not json", { status: 200 })],
    [
      "missing purchase order",
      new Response(JSON.stringify({ message: { status: "created" } }), {
        status: 200,
      }),
    ],
    [
      "legacy acknowledgement shape",
      new Response(
        JSON.stringify({
          message: {
            status: "drafted",
            purchase_order: "PO-1",
            ackToken: "a".repeat(64),
          },
        }),
        { status: 200 },
      ),
    ],
  ])("treats a %s response as unknown without a retry", async (_case, body) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(body);
    await expect(draftPurchaseOrder(input)).rejects.toBeInstanceOf(
      PurchaseDraftOutcomeUnknownError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["missing id", { status: "Draft", docstatus: 0 }],
    ["blank id", { purchase_order_id: " ", status: "Draft", docstatus: 0 }],
    ["numeric id", { purchase_order_id: 7, status: "Draft", docstatus: 0 }],
    [
      "submitted draft",
      { purchase_order_id: "PO-1", status: "Draft", docstatus: 1 },
    ],
    ["missing docstatus", { purchase_order_id: "PO-1", status: "Draft" }],
    ["missing status", { purchase_order_id: "PO-1", docstatus: 0 }],
    ["blank status", { purchase_order_id: "PO-1", status: " ", docstatus: 0 }],
  ])("rejects a draft with a %s as unknown", async (_case, purchase_order) => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(created(purchase_order));
    await expect(draftPurchaseOrder(input)).rejects.toBeInstanceOf(
      PurchaseDraftOutcomeUnknownError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a lost connection as an unknown outcome", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("dropped"));
    await expect(draftPurchaseOrder(input)).rejects.toBeInstanceOf(
      PurchaseDraftOutcomeUnknownError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses to draft without a session token", async () => {
    session.token = null;
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const failure = await draftPurchaseOrder(input).catch((error) => error);
    expect(failure).toBeInstanceOf(PurchaseDraftRejectedError);
    expect(failure).toMatchObject({ kind: "auth" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a second concurrent draft from the same page", async () => {
    let release: (response: Response) => void = () => {};
    const gate = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockReturnValueOnce(gate)
      .mockResolvedValueOnce(created());

    const first = draftPurchaseOrder(input);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await expect(draftPurchaseOrder(input)).rejects.toBeInstanceOf(
      PurchaseDraftInFlightError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);

    release(created());
    await expect(first).resolves.toMatchObject({ status: "created" });
    await expect(draftPurchaseOrder(input)).resolves.toMatchObject({
      status: "created",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
