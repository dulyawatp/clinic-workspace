import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { readNativeInventory } from "./inventory-read";

const samples = {
  items: { name: "MED-1", item_code: "MED-1", item_name: "Paracetamol", stock_uom: "Tablet", disabled: 0, is_stock_item: 1, has_batch_no: 1 },
  batches: { name: "LOT-1", batch_id: "LOT-1", item: "MED-1", expiry_date: null, disabled: 0 },
  balances: { name: "BIN-1", item_code: "MED-1", warehouse: "Pharmacy", stock_uom: "Tablet", actual_qty: 42 },
  movements: { name: "SLE-1", item_code: "MED-1", warehouse: "Pharmacy", posting_date: "2026-01-01", posting_time: "12:00:00", actual_qty: -3, qty_after_transaction: 42, voucher_type: "Stock Entry", voucher_no: "STE-1", is_cancelled: 0 },
};

it.each([
  ["items", "Item", samples.items, { is_stock_item: 1 }],
  ["batches", "Batch", samples.batches, {}],
  ["balances", "Bin", samples.balances, {}],
  ["movements", "Stock Ledger Entry", samples.movements, { is_cancelled: 0 }],
] as const)("reads %s with explicit fields and bounded paging", async (kind, doctype, row, filters) => {
  let requestUrl: URL | undefined;
  server.use(http.get(`http://localhost:5173/api/resource/${doctype}`, ({ request }) => {
    requestUrl = new URL(request.url);
    return HttpResponse.json({ data: [row] });
  }));
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const signal = new AbortController().signal;
  try {
    await expect(readNativeInventory(kind, 25, signal)).resolves.toEqual({ rows: [row], hasMore: false });
    expect(JSON.parse(requestUrl!.searchParams.get("fields")!)).toEqual(Object.keys(row));
    expect(JSON.parse(requestUrl!.searchParams.get("filters")!)).toEqual(filters);
    expect(requestUrl!.searchParams.get("limit_start")).toBe("25");
    expect(requestUrl!.searchParams.get("limit_page_length")).toBe("26");
    expect(requestUrl!.searchParams.get("order_by")).toBeTruthy();
    expect(fetchSpy.mock.calls[0]?.[1]).toMatchObject({ credentials: "same-origin", cache: "no-store", redirect: "error", signal });
  } finally {
    fetchSpy.mockRestore();
  }
});

it("returns multiple distinct batches, including null expiry", async () => {
  const rows = [samples.batches, { ...samples.batches, name: "LOT-2", batch_id: "LOT-2", item: "MED-2", expiry_date: "2027-01-01" }, { ...samples.batches, name: "LOT-3", batch_id: "LOT-3", item: "MED-1", expiry_date: "2028-02-02" }];
  server.use(http.get("http://localhost:5173/api/resource/Batch", () => HttpResponse.json({ data: rows })));
  await expect(readNativeInventory("batches", 0)).resolves.toEqual({ rows, hasMore: false });
});

it("handles empty and sentinel pages", async () => {
  let rows: typeof samples.items[] = [];
  server.use(http.get("http://localhost:5173/api/resource/Item", () => HttpResponse.json({ data: rows })));
  await expect(readNativeInventory("items", 0)).resolves.toEqual({ rows: [], hasMore: false });
  rows = Array.from({ length: 26 }, (_, i) => ({ ...samples.items, name: `MED-${i}`, item_code: `MED-${i}` }));
  await expect(readNativeInventory("items", 0)).resolves.toEqual({ rows: rows.slice(0, 25), hasMore: true });
});

it.each([401, 403])("maps HTTP %i to permission", async (status) => {
  server.use(http.get("http://localhost:5173/api/resource/Item", () => new HttpResponse(null, { status })));
  await expect(readNativeInventory("items", 0)).rejects.toMatchObject({ kind: "permission" });
});

it.each([{ data: null }, {}, { data: [{ ...samples.movements, actual_qty: "3" }] }, { data: [{ ...samples.movements, qty_after_transaction: null }] }])("rejects malformed data", async (body) => {
  server.use(http.get("http://localhost:5173/api/resource/Stock Ledger Entry", () => HttpResponse.json(body)));
  await expect(readNativeInventory("movements", 0)).rejects.toMatchObject({ kind: "unavailable" });
});
