import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export type StockWriteKind =
  | "stock_receive"
  | "stock_transfer"
  | "stock_adjust";

export type StockWriteStatus = "received" | "transferred" | "adjusted";

export type StockWriteFailure =
  | "locked"
  | "uncertain"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden"
  | "unavailable";

/** One pending-write lock per kind: a receive never blocks a transfer. */
const LOCKS: Record<StockWriteKind, string> = {
  stock_receive: "clinic.stock-write.pending.stock_receive",
  stock_transfer: "clinic.stock-write.pending.stock_transfer",
  stock_adjust: "clinic.stock-write.pending.stock_adjust",
};

const ACK_TOKEN = /^[0-9a-f]{64}$/;

export class NativeStockWriteError extends Error {
  readonly code: StockWriteFailure;

  constructor(code: StockWriteFailure) {
    super(code);
    this.name = "NativeStockWriteError";
    this.code = code;
  }
}

export interface ReceiveStockInput {
  operation_key: string;
  item_code: string;
  qty: number;
  warehouse: string;
  supplier: string;
  batch_id?: string;
  batch_expiry?: string;
  purchase_order?: string;
}

export interface TransferStockInput {
  operation_key: string;
  item_code: string;
  qty: number;
  source_warehouse: string;
  target_warehouse: string;
  batch_id?: string;
}

export interface AdjustStockInput {
  operation_key: string;
  item_code: string;
  qty: number;
  warehouse: string;
  reason: string;
  batch_id?: string;
}

export interface StockMovement {
  kind: StockWriteKind;
  item_code: string;
  warehouse: string;
  qty: number;
  target_warehouse?: string;
  batch?: string | null;
  reason?: string;
}

export interface StockWriteResult {
  status: StockWriteStatus;
  movement: StockMovement;
}

const ENDPOINTS: Record<StockWriteKind, string> = {
  stock_receive: "clinic_ui.api.stock_write.receive",
  stock_transfer: "clinic_ui.api.stock_write.transfer",
  stock_adjust: "clinic_ui.api.stock_write.adjust",
};

const STATUSES: Record<StockWriteKind, StockWriteStatus> = {
  stock_receive: "received",
  stock_transfer: "transferred",
  stock_adjust: "adjusted",
};

const listeners = new Set<() => void>();

function notifyLock(): void {
  for (const listener of listeners) listener();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  return (
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

/** The browser lock is the only persisted write state: no data is stored. */
export function stockWriteLocked(kind: StockWriteKind): boolean {
  try {
    return localStorage.getItem(LOCKS[kind]) !== null;
  } catch {
    return true;
  }
}

export function subscribeStockWriteLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function persistLock(kind: StockWriteKind): void {
  try {
    localStorage.setItem(LOCKS[kind], "1");
    if (localStorage.getItem(LOCKS[kind]) !== "1")
      throw new NativeStockWriteError("locked");
  } catch {
    throw new NativeStockWriteError("locked");
  }
}

function clearLock(kind: StockWriteKind): void {
  try {
    localStorage.removeItem(LOCKS[kind]);
    if (stockWriteLocked(kind)) throw new NativeStockWriteError("uncertain");
  } catch {
    throw new NativeStockWriteError("uncertain");
  }
  notifyLock();
}

function errorCode(body: unknown): string | null {
  if (!isRecord(body) || !isRecord(body.message)) return null;
  return typeof body.message.code === "string" ? body.message.code : null;
}

function failure(status: number, body: unknown): NativeStockWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "OPERATION_CONFLICT")
      return new NativeStockWriteError("conflict");
    return new NativeStockWriteError("locked");
  }
  if (status === 401) return new NativeStockWriteError("auth");
  if (status === 403) return new NativeStockWriteError("forbidden");
  if (status === 417 || status === 422)
    return new NativeStockWriteError("validation");
  return new NativeStockWriteError("uncertain");
}

function movementKeys(kind: StockWriteKind): readonly string[] {
  if (kind === "stock_receive")
    return ["kind", "item_code", "warehouse", "qty", "batch"];
  if (kind === "stock_transfer")
    return [
      "kind",
      "item_code",
      "warehouse",
      "target_warehouse",
      "qty",
      "batch",
    ];
  return ["kind", "item_code", "warehouse", "qty", "reason"];
}

function parseMovement(kind: StockWriteKind, value: unknown): StockMovement {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, movementKeys(kind)) ||
    value.kind !== kind ||
    !isText(value.item_code) ||
    !isText(value.warehouse) ||
    typeof value.qty !== "number"
  )
    throw new NativeStockWriteError("uncertain");
  if (kind === "stock_transfer") {
    if (!isText(value.target_warehouse))
      throw new NativeStockWriteError("uncertain");
    if (value.batch !== null && !isText(value.batch))
      throw new NativeStockWriteError("uncertain");
    return {
      kind,
      item_code: value.item_code,
      warehouse: value.warehouse,
      target_warehouse: value.target_warehouse,
      qty: value.qty,
      batch: (value.batch as string | null) ?? null,
    };
  }
  if (kind === "stock_adjust") {
    if (!isText(value.reason)) throw new NativeStockWriteError("uncertain");
    return {
      kind,
      item_code: value.item_code,
      warehouse: value.warehouse,
      qty: value.qty,
      reason: value.reason,
    };
  }
  if (value.batch !== null && !isText(value.batch))
    throw new NativeStockWriteError("uncertain");
  return {
    kind,
    item_code: value.item_code,
    warehouse: value.warehouse,
    qty: value.qty,
    batch: (value.batch as string | null) ?? null,
  };
}

function parseWriteResult(
  kind: StockWriteKind,
  body: unknown,
): { result: StockWriteResult; ackToken: string } {
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["status", "movement", "ackToken"])
  )
    throw new NativeStockWriteError("uncertain");
  const message = body.message;
  if (
    message.status !== STATUSES[kind] ||
    typeof message.ackToken !== "string" ||
    !ACK_TOKEN.test(message.ackToken)
  )
    throw new NativeStockWriteError("uncertain");
  return {
    result: {
      status: STATUSES[kind],
      movement: parseMovement(kind, message.movement),
    },
    ackToken: message.ackToken,
  };
}

async function acknowledge(
  token: string,
  kind: StockWriteKind,
  operationKey: string,
  ackToken: string,
): Promise<void> {
  const response = await frappeRequest("clinic_ui.api.stock_write.ack", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      [FRAPPE_CSRF_HEADER]: token,
    },
    body: JSON.stringify({
      operation_key: operationKey,
      kind,
      ack_token: ackToken,
    }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (
    response.status !== 200 ||
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["acknowledged"]) ||
    body.message.acknowledged !== true
  )
    throw new NativeStockWriteError("uncertain");
}

/**
 * One guarded POST with one explicit acknowledgement and no retry anywhere.
 * The lock is persisted before dispatch and only cleared after a confirmed ack;
 * any uncertain outcome keeps it, so a reload shows the stop state again.
 */
async function submit(
  kind: StockWriteKind,
  input: Record<string, unknown>,
): Promise<StockWriteResult> {
  const operationKey = input.operation_key;
  if (!isText(operationKey))
    throw new NativeStockWriteError("validation");

  const token = nativeCsrfToken();
  if (!token || stockWriteLocked(kind))
    throw new NativeStockWriteError("locked");

  persistLock(kind);

  let response: Response;
  try {
    response = await frappeRequest(ENDPOINTS[kind], {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify(input),
    });
  } catch {
    notifyLock();
    throw new NativeStockWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    notifyLock();
    throw failure(response.status, body);
  }

  let parsed: { result: StockWriteResult; ackToken: string };
  try {
    parsed = parseWriteResult(kind, body);
  } catch {
    notifyLock();
    throw new NativeStockWriteError("uncertain");
  }

  try {
    await acknowledge(token, kind, operationKey, parsed.ackToken);
  } catch {
    notifyLock();
    throw new NativeStockWriteError("uncertain");
  }

  clearLock(kind);
  return parsed.result;
}

export async function receiveNativeStock(
  input: ReceiveStockInput,
): Promise<StockWriteResult> {
  return submit("stock_receive", {
    operation_key: input.operation_key,
    item_code: input.item_code,
    qty: input.qty,
    warehouse: input.warehouse,
    supplier: input.supplier,
    ...(input.batch_id?.trim() ? { batch_id: input.batch_id.trim() } : {}),
    ...(input.batch_expiry?.trim()
      ? { batch_expiry: input.batch_expiry.trim() }
      : {}),
    ...(input.purchase_order?.trim()
      ? { purchase_order: input.purchase_order.trim() }
      : {}),
  });
}

export async function transferNativeStock(
  input: TransferStockInput,
): Promise<StockWriteResult> {
  return submit("stock_transfer", {
    operation_key: input.operation_key,
    item_code: input.item_code,
    qty: input.qty,
    source_warehouse: input.source_warehouse,
    target_warehouse: input.target_warehouse,
    ...(input.batch_id?.trim() ? { batch_id: input.batch_id.trim() } : {}),
  });
}

export async function adjustNativeStock(
  input: AdjustStockInput,
): Promise<StockWriteResult> {
  return submit("stock_adjust", {
    operation_key: input.operation_key,
    item_code: input.item_code,
    qty: input.qty,
    warehouse: input.warehouse,
    reason: input.reason,
    ...(input.batch_id?.trim() ? { batch_id: input.batch_id.trim() } : {}),
  });
}
