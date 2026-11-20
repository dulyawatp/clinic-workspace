export type InventoryKind = "items" | "batches" | "balances" | "movements";

export interface InventoryRows {
  items: { name: string; item_code: string; item_name: string; stock_uom: string; disabled: number; is_stock_item: number; has_batch_no: number };
  batches: { name: string; batch_id: string; item: string; expiry_date: string | null; disabled: number };
  balances: { name: string; item_code: string; warehouse: string; stock_uom: string; actual_qty: number };
  movements: { name: string; item_code: string; warehouse: string; posting_date: string; posting_time: string; actual_qty: number; qty_after_transaction: number; voucher_type: string; voucher_no: string; is_cancelled: number };
}

export class InventoryReadError extends Error {
  constructor(readonly kind: "permission" | "unavailable") {
    super(kind);
  }
}

function isAbort(cause: unknown): boolean {
  return typeof cause === "object" && cause !== null && "name" in cause && cause.name === "AbortError";
}

const resources = {
  items: { doctype: "Item", fields: ["name", "item_code", "item_name", "stock_uom", "disabled", "is_stock_item", "has_batch_no"], numbers: ["disabled", "is_stock_item", "has_batch_no"], filters: { is_stock_item: 1 } },
  batches: { doctype: "Batch", fields: ["name", "batch_id", "item", "expiry_date", "disabled"], numbers: ["disabled"], filters: {} },
  balances: { doctype: "Bin", fields: ["name", "item_code", "warehouse", "stock_uom", "actual_qty"], numbers: ["actual_qty"], filters: {} },
  movements: { doctype: "Stock Ledger Entry", fields: ["name", "item_code", "warehouse", "posting_date", "posting_time", "actual_qty", "qty_after_transaction", "voucher_type", "voucher_no", "is_cancelled"], numbers: ["actual_qty", "qty_after_transaction", "is_cancelled"], filters: { is_cancelled: 0 } },
};

export async function readNativeInventory<K extends InventoryKind>(
  kind: K, offset: number, signal?: AbortSignal,
): Promise<{ rows: InventoryRows[K][]; hasMore: boolean }> {
  const resource = resources[kind];
  if (!resource || !Number.isSafeInteger(offset) || offset < 0) throw new InventoryReadError("unavailable");
  const query = new URLSearchParams({
    fields: JSON.stringify(resource.fields),
    filters: JSON.stringify(resource.filters),
    limit_start: String(offset),
    limit_page_length: "26",
    order_by: "name asc",
  });
  let response: Response;
  try {
    response = await fetch(`/api/resource/${encodeURIComponent(resource.doctype)}?${query}`, {
      credentials: "same-origin", cache: "no-store", redirect: "error", signal,
    });
  } catch (cause) {
    if (isAbort(cause)) throw cause;
    throw new InventoryReadError("unavailable");
  }
  if (response.status === 401 || response.status === 403) throw new InventoryReadError("permission");
  if (!response.ok) throw new InventoryReadError("unavailable");
  const body: unknown = await response.json().catch((cause: unknown) => {
    if (isAbort(cause)) throw cause;
    return null;
  });
  if (!body || typeof body !== "object" || !("data" in body) || !Array.isArray(body.data) || body.data.length > 26) {
    throw new InventoryReadError("unavailable");
  }
  for (const row of body.data) {
    if (!row || typeof row !== "object" || Array.isArray(row)) throw new InventoryReadError("unavailable");
    for (const field of resource.fields) {
      const value: unknown = row[field];
      if (kind === "batches" && field === "expiry_date" && value === null) continue;
      if (resource.numbers.includes(field) ? typeof value !== "number" || !Number.isFinite(value) : typeof value !== "string") {
        throw new InventoryReadError("unavailable");
      }
    }
  }
  return { rows: body.data.slice(0, 25) as InventoryRows[K][], hasMore: body.data.length > 25 };
}
