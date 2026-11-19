import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export interface DraftPurchaseOrderLine {
  item_code: string;
  qty: number;
  rate: number;
}

export interface DraftPurchaseOrderInput {
  operation_key: string;
  supplier: string;
  items: DraftPurchaseOrderLine[];
}

export interface PurchaseDraftCreated {
  status: "created";
  purchase_order_id: string;
}

/** Deterministic refusals: the server rejected the draft and changed nothing. */
export type PurchaseDraftRefusal =
  | "auth"
  | "forbidden"
  | "conflict"
  | "validation";

export class PurchaseDraftRejectedError extends Error {
  constructor(
    readonly kind: PurchaseDraftRefusal,
    readonly status: number | null,
    readonly code?: string,
  ) {
    super(kind);
    this.name = "PurchaseDraftRejectedError";
  }
}

/** A lost or unreadable response: the draft may or may not have been created. */
export class PurchaseDraftOutcomeUnknownError extends Error {
  constructor() {
    super("unknown outcome");
    this.name = "PurchaseDraftOutcomeUnknownError";
  }
}

/** A second draft from the same page while the first is still in flight. */
export class PurchaseDraftInFlightError extends Error {
  constructor() {
    super("draft already in flight");
    this.name = "PurchaseDraftInFlightError";
  }
}

const ENDPOINT = "clinic_ui.api.purchase_write.save_po_draft";

/** Page-local only: reset on reload, never persisted and never shared. */
let inFlight = false;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

export interface PurchaseDraftRecord {
  purchase_order_id: string;
  status: string | null;
  docstatus: number;
}

function parseRecord(value: unknown): PurchaseDraftRecord {
  if (
    !isRecord(value) ||
    !isText(value.purchase_order_id) ||
    (value.status !== null && !isText(value.status)) ||
    !Number.isInteger(value.docstatus)
  ) throw new Error("Invalid purchase order response");
  return value as unknown as PurchaseDraftRecord;
}

async function readPurchase(method: string, params: URLSearchParams): Promise<Record<string, unknown>> {
  const response = await frappeRequest(`${method}?${params.toString()}`);
  if (!response.ok) throw new Error("Purchase order lookup failed");
  const body: unknown = await response.json();
  if (!isRecord(body) || !isRecord(body.message)) throw new Error("Invalid purchase order response");
  return body.message;
}

export async function findPurchaseDrafts(supplier: string): Promise<PurchaseDraftRecord[]> {
  const message = await readPurchase("clinic_ui.api.purchase_write.drafts", new URLSearchParams({ supplier }));
  if (!Array.isArray(message.items)) throw new Error("Invalid purchase order response");
  return message.items.map(parseRecord);
}

export async function viewPurchaseOrder(purchase_order_id: string): Promise<PurchaseDraftRecord> {
  const message = await readPurchase("clinic_ui.api.purchase_write.view", new URLSearchParams({ purchase_order_id }));
  return parseRecord(message.purchase_order);
}


function responseCode(body: unknown): string | undefined {
  if (!isRecord(body)) return undefined;
  if (isRecord(body.message) && typeof body.message.code === "string")
    return body.message.code;
  return typeof body.code === "string" ? body.code : undefined;
}

/** Maps a native refusal to its deterministic reason; anything else is unknown. */
function rejection(response: Response, body: unknown): Error {
  const code = responseCode(body);
  if (response.status === 401)
    return new PurchaseDraftRejectedError("auth", 401, code);
  if (response.status === 403)
    return new PurchaseDraftRejectedError("forbidden", 403, code);
  if (response.status === 417 || response.status === 422)
    return new PurchaseDraftRejectedError("validation", response.status, code);
  if (response.status === 409 && code === "OPERATION_CONFLICT")
    return new PurchaseDraftRejectedError("conflict", 409, code);
  return new PurchaseDraftOutcomeUnknownError();
}

/**
 * Only the native identifier is read from the created draft. The backend owns
 * the status text, company, currency and lines, so nothing is compared against
 * what the form submitted; `docstatus: 0` proves the draft was not submitted.
 */
function parseCreated(body: unknown): PurchaseDraftCreated {
  const message =
    isRecord(body) && isRecord(body.message) ? body.message : null;
  if (
    message === null ||
    message.status !== "created" ||
    !isRecord(message.purchase_order)
  )
    throw new PurchaseDraftOutcomeUnknownError();
  const order = message.purchase_order;
  if (
    !isText(order.purchase_order_id) ||
    order.docstatus !== 0 ||
    !("status" in order) ||
    (order.status !== null && !isText(order.status))
  )
    throw new PurchaseDraftOutcomeUnknownError();
  return {
    status: "created",
    purchase_order_id: order.purchase_order_id.trim(),
  };
}

/**
 * One draft purchase order through the native Frappe method.
 *
 * Creating a draft is additive: there is no acknowledgement request, no
 * browser fence and no retry. The caller keeps the in-flight state, renders a
 * deterministic refusal as a known failure, and reconciles an unknown outcome
 * by inspecting purchase-order records before creating another draft.
 */
export async function draftPurchaseOrder(
  input: DraftPurchaseOrderInput,
): Promise<PurchaseDraftCreated> {
  if (inFlight) throw new PurchaseDraftInFlightError();
  const token = nativeCsrfToken();
  if (!token) throw new PurchaseDraftRejectedError("auth", null);

  inFlight = true;
  try {
    let response: Response;
    try {
      response = await frappeRequest(ENDPOINT, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          [FRAPPE_CSRF_HEADER]: token,
        },
        body: JSON.stringify({
          operation_key: input.operation_key,
          supplier: input.supplier,
          items: input.items.map((line) => ({
            item_code: line.item_code,
            qty: line.qty,
            rate: line.rate,
          })),
        }),
      });
    } catch {
      // The request may have reached the server: the outcome is unknown.
      throw new PurchaseDraftOutcomeUnknownError();
    }

    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) throw rejection(response, body);
    return parseCreated(body);
  } finally {
    inFlight = false;
  }
}
