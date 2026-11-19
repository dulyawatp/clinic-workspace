import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

/**
 * Invoice-only native frontend transport for the converted development lane.
 *
 * Expected backend contract (owned by the parallel backend lane; this client
 * verifies the responses but never discovers endpoints):
 *
 * - `POST clinic_ui.api.finance_write.save_invoice_draft` is the read-only
 *   preview. Body: `{operation_key, customer, selling_price_list, due_date,
 *   items: [{item_code, qty}], discount?}`. Response `message`:
 *   `{status: "preview", customer, currency, company, selling_price_list,
 *   due_date, lines: [{item_code, qty, rate, amount}], net_total, discount,
 *   discount_available, total_taxes, total}`.
 * - `POST clinic_ui.api.finance_write.submit_invoice` creates and submits one
 *   invoice. Same body; response `message`: `{status: "submitted", invoice,
 *   total}`. The converted lane returns no `ackToken` and there is no `ack`
 *   call: a legacy acknowledgement envelope is rejected as an unknown outcome.
 *
 * Native lookup is separate from fixture-backed invoice surfaces.
 *
 * There is no persistent lock, no acknowledgement and no automatic retry. The
 * caller keeps the in-flight guard, treats a deterministic refusal as a known
 * failure, and stops the mounted form when the outcome is unknown.
 */

export interface InvoiceLineInput {
  item_code: string;
  qty: number;
}

export interface SubmitInvoiceInput {
  operation_key: string;
  customer: string;
  selling_price_list: string;
  due_date: string;
  items: InvoiceLineInput[];
  /** An absolute discount amount; no rate is ever implied or invented. */
  discount?: number;
}

export interface InvoicePreviewLine {
  item_code: string;
  qty: number;
  rate: number;
  amount: number;
}

/** The server-resolved preview: every figure here is server truth. */
export interface InvoicePreview {
  status: "preview";
  customer: string;
  currency: string;
  company: string;
  selling_price_list: string;
  due_date: string;
  lines: InvoicePreviewLine[];
  net_total: number;
  discount: number;
  discount_available: boolean;
  total_taxes: number;
  total: number;
}

export interface InvoiceSubmitted {
  status: "submitted";
  invoice: string;
  total: number;
}

/** Deterministic refusals: the server rejected the write and changed nothing. */
export type InvoiceWriteFailure =
  | "auth"
  | "forbidden"
  | "validation"
  | "conflict";

export class InvoiceWriteRejectedError extends Error {
  constructor(
    readonly kind: InvoiceWriteFailure,
    readonly status: number | null,
    readonly code?: string,
  ) {
    super(kind);
    this.name = "InvoiceWriteRejectedError";
  }
}

/** A lost or unreadable response: the invoice may or may not have been posted. */
export class InvoiceWriteOutcomeUnknownError extends Error {
  constructor() {
    super("unknown outcome");
    this.name = "InvoiceWriteOutcomeUnknownError";
  }
}

/** A second submission from the same page while the first is still in flight. */
export class InvoiceWriteInFlightError extends Error {
  constructor() {
    super("invoice already in flight");
    this.name = "InvoiceWriteInFlightError";
  }
}

const PREVIEW_ENDPOINT = "clinic_ui.api.finance_write.save_invoice_draft";
const SUBMIT_ENDPOINT = "clinic_ui.api.finance_write.submit_invoice";

/** Page-local only: reset on reload, never persisted and never shared. */
let inFlight = false;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isAmount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export interface NativeInvoiceRecord {
  invoice_id: string;
  status: string;
  docstatus: number;
}

function parseInvoiceRecord(value: unknown): NativeInvoiceRecord {
  if (!isRecord(value) || !isText(value.invoice_id) || !isText(value.status) ||
      !Number.isInteger(value.docstatus) || ![0, 1, 2].includes(value.docstatus as number))
    throw new Error("Invalid invoice response");
  return { invoice_id: value.invoice_id, status: value.status, docstatus: value.docstatus as number };
}

async function readInvoice(method: string, parameter: string, value: string): Promise<Record<string, unknown>> {
  const query = new URLSearchParams({ [parameter]: value });
  const response = await frappeRequest(`clinic_ui.api.finance_write.${method}?${query}`);
  if (!response.ok) throw new Error("Invoice lookup failed");
  const body: unknown = await response.json();
  if (!isRecord(body) || !isRecord(body.message)) throw new Error("Invalid invoice response");
  return body.message;
}

export async function findInvoices(customer: string): Promise<NativeInvoiceRecord[]> {
  const message = await readInvoice("invoices", "customer", customer);
  if (!Array.isArray(message.items)) throw new Error("Invalid invoice response");
  return message.items.map(parseInvoiceRecord);
}

export async function viewInvoice(invoiceId: string): Promise<NativeInvoiceRecord> {
  const message = await readInvoice("view", "invoice_id", invoiceId);
  const invoice = parseInvoiceRecord(message.invoice);
  if (invoice.invoice_id !== invoiceId) throw new Error("Invalid invoice response");
  return invoice;
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

function responseCode(body: unknown): string | undefined {
  if (!isRecord(body)) return undefined;
  if (isRecord(body.message) && typeof body.message.code === "string")
    return body.message.code;
  return typeof body.code === "string" ? body.code : undefined;
}

function rejection(response: Response, body: unknown): Error {
  const code = responseCode(body);
  if (response.status === 401)
    return new InvoiceWriteRejectedError("auth", 401, code);
  if (response.status === 403)
    return new InvoiceWriteRejectedError("forbidden", 403, code);
  if (response.status === 417 || response.status === 422)
    return new InvoiceWriteRejectedError("validation", response.status, code);
  if (response.status === 409 && code === "OPERATION_CONFLICT")
    return new InvoiceWriteRejectedError("conflict", 409, code);
  return new InvoiceWriteOutcomeUnknownError();
}

function parsePreviewLine(value: unknown): InvoicePreviewLine {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["item_code", "qty", "rate", "amount"]) ||
    !isText(value.item_code) ||
    !isAmount(value.qty) ||
    !isAmount(value.rate) ||
    !isAmount(value.amount)
  )
    throw new InvoiceWriteOutcomeUnknownError();
  return {
    item_code: value.item_code,
    qty: value.qty,
    rate: value.rate,
    amount: value.amount,
  };
}

function parsePreview(body: unknown): InvoicePreview {
  if (!isRecord(body) || !isRecord(body.message))
    throw new InvoiceWriteOutcomeUnknownError();
  const message = body.message;
  if (
    !hasExactKeys(message, [
      "status",
      "customer",
      "currency",
      "company",
      "selling_price_list",
      "due_date",
      "lines",
      "net_total",
      "discount",
      "discount_available",
      "total_taxes",
      "total",
    ]) ||
    message.status !== "preview" ||
    !isText(message.customer) ||
    !isText(message.currency) ||
    !isText(message.company) ||
    !isText(message.selling_price_list) ||
    !isText(message.due_date) ||
    !Array.isArray(message.lines) ||
    !isAmount(message.net_total) ||
    !isAmount(message.discount) ||
    typeof message.discount_available !== "boolean" ||
    !isAmount(message.total_taxes) ||
    !isAmount(message.total)
  )
    throw new InvoiceWriteOutcomeUnknownError();
  return {
    status: "preview",
    customer: message.customer,
    currency: message.currency,
    company: message.company,
    selling_price_list: message.selling_price_list,
    due_date: message.due_date,
    lines: message.lines.map(parsePreviewLine),
    net_total: message.net_total,
    discount: message.discount,
    discount_available: message.discount_available,
    total_taxes: message.total_taxes,
    total: message.total,
  };
}

/**
 * Exactly `{status, invoice, total}`. The retired `ackToken` (and any other
 * unexpected field) is a legacy envelope: the outcome is unknown, never a
 * success, so the caller stops the form instead of acknowledging or retrying.
 */
function parseSubmitted(body: unknown): InvoiceSubmitted {
  if (!isRecord(body) || !isRecord(body.message))
    throw new InvoiceWriteOutcomeUnknownError();
  const message = body.message;
  if (Object.hasOwn(message, "ackToken"))
    throw new InvoiceWriteOutcomeUnknownError();
  if (
    !hasExactKeys(message, ["status", "invoice", "total"]) ||
    message.status !== "submitted" ||
    !isText(message.invoice) ||
    !isAmount(message.total)
  )
    throw new InvoiceWriteOutcomeUnknownError();
  return {
    status: "submitted",
    invoice: message.invoice,
    total: message.total,
  };
}

function requestBody(input: SubmitInvoiceInput): Record<string, unknown> {
  return {
    operation_key: input.operation_key,
    customer: input.customer,
    selling_price_list: input.selling_price_list,
    due_date: input.due_date,
    items: input.items.map((line) => ({
      item_code: line.item_code,
      qty: line.qty,
    })),
    ...(input.discount !== undefined && input.discount > 0
      ? { discount: input.discount }
      : {}),
  };
}

/**
 * Fetch the server-resolved invoice preview. This writes nothing, holds no
 * lock and is never retried: the client's own arithmetic is not shown as truth.
 */
export async function previewInvoice(
  input: SubmitInvoiceInput,
): Promise<InvoicePreview> {
  const token = nativeCsrfToken();
  if (!token) throw new InvoiceWriteRejectedError("auth", null);
  let response: Response;
  try {
    response = await frappeRequest(PREVIEW_ENDPOINT, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify(requestBody(input)),
    });
  } catch {
    throw new InvoiceWriteOutcomeUnknownError();
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw rejection(response, body);
  return parsePreview(body);
}

/**
 * One invoice submission through the native Frappe method.
 *
 * A deterministic refusal is a known failure; a lost or unreadable response is
 * an unknown outcome that may already have posted the invoice. Nothing is
 * acknowledged, persisted or retried here.
 */
export async function submitInvoice(
  input: SubmitInvoiceInput,
): Promise<InvoiceSubmitted> {
  if (inFlight) throw new InvoiceWriteInFlightError();
  const token = nativeCsrfToken();
  if (!token) throw new InvoiceWriteRejectedError("auth", null);

  inFlight = true;
  try {
    let response: Response;
    try {
      response = await frappeRequest(SUBMIT_ENDPOINT, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          [FRAPPE_CSRF_HEADER]: token,
        },
        body: JSON.stringify(requestBody(input)),
      });
    } catch {
      // The request may have reached the server: the outcome is unknown.
      throw new InvoiceWriteOutcomeUnknownError();
    }

    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) throw rejection(response, body);
    return parseSubmitted(body);
  } finally {
    inFlight = false;
  }
}
