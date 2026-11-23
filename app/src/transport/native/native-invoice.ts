import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

/** Native draft fields remain intact; the preview endpoint binds its own inputs. */
export interface NativeInvoiceDoc extends Record<string, unknown> {
  doctype: "Sales Invoice";
  docstatus?: 0 | 1;
  name?: string;
  customer?: string;
  company?: string;
  items?: Record<string, unknown>[];
}

export interface NativeInvoiceResult extends NativeInvoiceDoc {
  docstatus: 0 | 1;
  items: Record<string, unknown>[];
}

/** Native HTTP failure, including Frappe's unmodified response body. */
export class NativeInvoiceHttpError extends Error {
  constructor(readonly status: number, readonly body: unknown) {
    super(`Native invoice request failed (${status})`);
    this.name = "NativeInvoiceHttpError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseInvoice(body: unknown, status: 0 | 1, requireName: boolean): NativeInvoiceResult {
  if (!isRecord(body) || body.exception || body.exc || !isRecord(body.message))
    throw new Error("Invalid native invoice response");
  const doc = body.message;
  if (
    doc.doctype !== "Sales Invoice" ||
    doc.docstatus !== status ||
    (requireName && (typeof doc.name !== "string" || !doc.name.trim())) ||
    !Array.isArray(doc.items) ||
    !doc.items.every(isRecord) ||
    ["net_total", "total", "grand_total", "rounded_total", "total_taxes_and_charges", "discount_amount", "additional_discount_percentage"]
      .some((key) => doc[key] != null && (typeof doc[key] !== "number" || !Number.isFinite(doc[key])))
  ) throw new Error("Invalid native invoice response");
  return doc as NativeInvoiceResult;
}

async function postInvoice(method: string, doc: NativeInvoiceDoc, status: 0 | 1, requireName: boolean): Promise<NativeInvoiceResult> {
  const token = nativeCsrfToken();
  if (!token) throw new Error("Missing native session CSRF token");
  const response = await frappeRequest(method, {
    method: "POST",
    headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
    body: JSON.stringify({ doc }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new NativeInvoiceHttpError(response.status, body);
  return parseInvoice(body, status, requireName);
}

/** Read-only server calculation; response is not a replacement insert payload. */
export function previewNativeInvoice(doc: NativeInvoiceDoc): Promise<NativeInvoiceResult> {
  return postInvoice("clinic_ui.api.native_invoice.preview", doc, 0, false);
}

/** Explicitly insert a native draft; never submits it. */
export function insertNativeInvoice(doc: NativeInvoiceDoc): Promise<NativeInvoiceResult> {
  return postInvoice("frappe.client.insert", doc, 0, true);
}

/** Explicitly submit the actual persisted native draft document. */
export async function submitNativeInvoice(doc: NativeInvoiceDoc): Promise<NativeInvoiceResult> {
  if (doc.docstatus !== 0 || typeof doc.name !== "string" || !doc.name.trim())
    throw new Error("Expected a persisted native invoice draft");
  const result = await postInvoice("frappe.client.submit", doc, 1, true);
  if (result.name !== doc.name) throw new Error("Invalid native invoice response");
  return result;
}
