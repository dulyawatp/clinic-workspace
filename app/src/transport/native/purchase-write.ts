import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export type PurchaseWriteKind =
  | "purchase_order_draft"
  | "purchase_order_submit"
  | "purchase_receipt";

export type PurchaseWriteStatus = "drafted" | "submitted" | "received";

export type PurchaseWriteFailure =
  | "locked"
  | "uncertain"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden"
  | "unavailable";

/** One pending-write lock per kind: a draft never blocks a receipt. */
const LOCKS: Record<PurchaseWriteKind, string> = {
  purchase_order_draft: "clinic.purchase-write.pending.purchase_order_draft",
  purchase_order_submit: "clinic.purchase-write.pending.purchase_order_submit",
  purchase_receipt: "clinic.purchase-write.pending.purchase_receipt",
};

const ACK_TOKEN = /^[0-9a-f]{64}$/;

export class NativePurchaseWriteError extends Error {
  readonly code: PurchaseWriteFailure;

  constructor(code: PurchaseWriteFailure) {
    super(code);
    this.name = "NativePurchaseWriteError";
    this.code = code;
  }
}

export interface SubmitPurchaseOrderInput {
  operation_key: string;
  purchase_order: string;
}

export interface ReceivePurchaseOrderInput {
  operation_key: string;
  purchase_order: string;
  item_code: string;
  qty: number;
  warehouse: string;
  batch_id?: string;
  batch_expiry?: string;
}

export interface PurchaseReceiptMovement {
  kind: "purchase_receipt";
  purchase_order: string;
  item_code: string;
  warehouse: string;
  qty: number;
  batch: string | null;
}

export type PurchaseWriteResult =
  | { status: "drafted"; purchase_order: string }
  | { status: "submitted"; purchase_order: string }
  | { status: "received"; movement: PurchaseReceiptMovement };

const ENDPOINTS: Record<PurchaseWriteKind, string> = {
  purchase_order_draft: "clinic_ui.api.purchase_write.save_po_draft",
  purchase_order_submit: "clinic_ui.api.purchase_write.submit_po",
  purchase_receipt: "clinic_ui.api.purchase_write.receive",
};

const STATUSES: Record<PurchaseWriteKind, PurchaseWriteStatus> = {
  purchase_order_draft: "drafted",
  purchase_order_submit: "submitted",
  purchase_receipt: "received",
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
export function purchaseWriteLocked(kind: PurchaseWriteKind): boolean {
  try {
    return localStorage.getItem(LOCKS[kind]) !== null;
  } catch {
    return true;
  }
}

export function subscribePurchaseWriteLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function persistLock(kind: PurchaseWriteKind): void {
  try {
    localStorage.setItem(LOCKS[kind], "1");
    if (localStorage.getItem(LOCKS[kind]) !== "1")
      throw new NativePurchaseWriteError("locked");
  } catch {
    throw new NativePurchaseWriteError("locked");
  }
}

function clearLock(kind: PurchaseWriteKind): void {
  try {
    localStorage.removeItem(LOCKS[kind]);
    if (purchaseWriteLocked(kind)) throw new NativePurchaseWriteError("uncertain");
  } catch {
    throw new NativePurchaseWriteError("uncertain");
  }
  notifyLock();
}

function errorCode(body: unknown): string | null {
  if (!isRecord(body) || !isRecord(body.message)) return null;
  return typeof body.message.code === "string" ? body.message.code : null;
}

function failure(status: number, body: unknown): NativePurchaseWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "OPERATION_CONFLICT")
      return new NativePurchaseWriteError("conflict");
    return new NativePurchaseWriteError("locked");
  }
  if (status === 401) return new NativePurchaseWriteError("auth");
  if (status === 403) return new NativePurchaseWriteError("forbidden");
  if (status === 417 || status === 422)
    return new NativePurchaseWriteError("validation");
  return new NativePurchaseWriteError("uncertain");
}

function parseReceiptMovement(value: unknown): PurchaseReceiptMovement {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "kind",
      "purchase_order",
      "item_code",
      "warehouse",
      "qty",
      "batch",
    ]) ||
    value.kind !== "purchase_receipt" ||
    !isText(value.purchase_order) ||
    !isText(value.item_code) ||
    !isText(value.warehouse) ||
    typeof value.qty !== "number" ||
    (value.batch !== null && !isText(value.batch))
  )
    throw new NativePurchaseWriteError("uncertain");
  return {
    kind: "purchase_receipt",
    purchase_order: value.purchase_order,
    item_code: value.item_code,
    warehouse: value.warehouse,
    qty: value.qty,
    batch: (value.batch as string | null) ?? null,
  };
}

function parseWriteResult(
  kind: PurchaseWriteKind,
  body: unknown,
): { result: PurchaseWriteResult; ackToken: string } {
  if (!isRecord(body) || !isRecord(body.message))
    throw new NativePurchaseWriteError("uncertain");
  const message = body.message;
  if (typeof message.ackToken !== "string" || !ACK_TOKEN.test(message.ackToken))
    throw new NativePurchaseWriteError("uncertain");
  const status = STATUSES[kind];
  if (message.status !== status) throw new NativePurchaseWriteError("uncertain");

  if (kind === "purchase_receipt") {
    if (!hasExactKeys(message, ["status", "movement", "ackToken"]))
      throw new NativePurchaseWriteError("uncertain");
    return {
      result: {
        status: "received",
        movement: parseReceiptMovement(message.movement),
      },
      ackToken: message.ackToken,
    };
  }

  if (!hasExactKeys(message, ["status", "purchase_order", "ackToken"]))
    throw new NativePurchaseWriteError("uncertain");
  if (!isText(message.purchase_order))
    throw new NativePurchaseWriteError("uncertain");
  return {
    result: {
      status: kind === "purchase_order_draft" ? "drafted" : "submitted",
      purchase_order: message.purchase_order,
    },
    ackToken: message.ackToken,
  };
}

async function acknowledge(
  token: string,
  kind: PurchaseWriteKind,
  operationKey: string,
  ackToken: string,
): Promise<void> {
  const response = await frappeRequest("clinic_ui.api.purchase_write.ack", {
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
    throw new NativePurchaseWriteError("uncertain");
}

/**
 * One guarded POST with one explicit acknowledgement and no retry anywhere.
 * The lock is persisted before dispatch and only cleared after a confirmed ack;
 * any uncertain outcome keeps it, so a reload shows the stop state again.
 */
async function submit(
  kind: PurchaseWriteKind,
  input: Record<string, unknown>,
): Promise<PurchaseWriteResult> {
  const operationKey = input.operation_key;
  if (!isText(operationKey))
    throw new NativePurchaseWriteError("validation");

  const token = nativeCsrfToken();
  if (!token || purchaseWriteLocked(kind))
    throw new NativePurchaseWriteError("locked");

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
    throw new NativePurchaseWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    notifyLock();
    throw failure(response.status, body);
  }

  let parsed: { result: PurchaseWriteResult; ackToken: string };
  try {
    parsed = parseWriteResult(kind, body);
  } catch {
    notifyLock();
    throw new NativePurchaseWriteError("uncertain");
  }

  try {
    await acknowledge(token, kind, operationKey, parsed.ackToken);
  } catch {
    notifyLock();
    throw new NativePurchaseWriteError("uncertain");
  }

  clearLock(kind);
  return parsed.result;
}

export async function submitPurchaseOrder(
  input: SubmitPurchaseOrderInput,
): Promise<PurchaseWriteResult> {
  return submit("purchase_order_submit", {
    operation_key: input.operation_key,
    purchase_order: input.purchase_order,
  });
}

export async function receivePurchaseOrder(
  input: ReceivePurchaseOrderInput,
): Promise<PurchaseWriteResult> {
  return submit("purchase_receipt", {
    operation_key: input.operation_key,
    purchase_order: input.purchase_order,
    item_code: input.item_code,
    qty: input.qty,
    warehouse: input.warehouse,
    ...(input.batch_id?.trim() ? { batch_id: input.batch_id.trim() } : {}),
    ...(input.batch_expiry?.trim()
      ? { batch_expiry: input.batch_expiry.trim() }
      : {}),
  });
}
