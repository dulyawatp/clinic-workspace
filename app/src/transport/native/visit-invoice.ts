import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

const METHOD = "clinic_ui.api.visit_invoice";

export type VisitInvoiceDiscount = { mode: "amount" | "percent"; value: string };
export type VisitInvoiceVatMode = "vat_7" | "no_vat";

export interface VisitInvoiceInput {
  appointment_id: string;
  vat_mode: VisitInvoiceVatMode;
  discount?: VisitInvoiceDiscount;
}

export interface VisitInvoicePreview {
  currency: string;
  gross: string;
  discount: string;
  net: string;
  vat: string;
  total: string;
  preview_token: string;
}

type InvoiceReference = { invoice_id: string };

export class VisitInvoiceError extends Error {
  constructor(readonly code: "validation" | "unavailable" | "uncertain") {
    super(code);
    this.name = "VisitInvoiceError";
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) && Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key));
}

function safeText(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max &&
    value.trim() === value &&
    [...value].every((char) => char.charCodeAt(0) >= 32 && char.charCodeAt(0) !== 127);
}

function money(value: unknown): value is string {
  return typeof value === "string" && /^[0-9]+\.[0-9]{2}$/.test(value);
}

function message(body: unknown): unknown {
  return record(body) ? body.message : undefined;
}

function invoiceReference(value: unknown): InvoiceReference | null {
  if (!exact(value, ["invoice_id"]) || !safeText(value.invoice_id, 140)) return null;
  return { invoice_id: value.invoice_id };
}

async function post(
  operation: "preview" | "submit",
  input: VisitInvoiceInput & { preview_token?: string },
): Promise<unknown> {
  const token = nativeCsrfToken();
  if (!token) throw new VisitInvoiceError("unavailable"); // No request was sent.
  const unknown = new VisitInvoiceError(operation === "submit" ? "uncertain" : "unavailable");
  let response: Response;
  try {
    response = await frappeRequest(`${METHOD}.${operation}`, {
      method: "POST",
      headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
      body: JSON.stringify(input),
    });
  } catch {
    throw unknown;
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.ok) return message(body);
  if (exact(body, ["code"])) {
    if ((response.status === 417 || response.status === 422) && body.code === "VALIDATION")
      throw new VisitInvoiceError("validation");
    if ((response.status === 401 && body.code === "AUTH_EXPIRED") ||
        (response.status === 403 && body.code === "FORBIDDEN"))
      throw new VisitInvoiceError("unavailable");
  }
  // Includes 409 WRITE_UNCERTAIN (root or wrapped) and all unrecognized replies.
  throw unknown;
}

export async function previewVisitInvoice(input: VisitInvoiceInput): Promise<VisitInvoicePreview> {
  const value = await post("preview", input);
  if (!exact(value, ["currency", "gross", "discount", "net", "vat", "total", "preview_token"]))
    throw new VisitInvoiceError("unavailable");
  const { currency, gross, discount, net, vat, total, preview_token } = value;
  if (!safeText(currency, 140) || !money(gross) || !money(discount) || !money(net) ||
      !money(vat) || !money(total) || !safeText(preview_token, 100))
    throw new VisitInvoiceError("unavailable");

  // Check the server's breakdown without recalculating the discount or VAT.
  const cents = (amount: string) => BigInt(amount.replace(".", ""));
  const cut = cents(discount);
  const subtotal = cents(net);
  const tax = cents(vat);
  if (subtotal <= 0n || cents(gross) - cut !== subtotal || subtotal + tax !== cents(total) ||
      (input.vat_mode === "no_vat" && tax !== 0n) ||
      (cut > 0n) !== (input.discount !== undefined))
    throw new VisitInvoiceError("unavailable");
  return { currency, gross, discount, net, vat, total, preview_token };
}

export async function submitVisitInvoice(
  input: VisitInvoiceInput & { preview_token: string },
): Promise<InvoiceReference> {
  const result = invoiceReference(await post("submit", input));
  if (result === null) throw new VisitInvoiceError("uncertain");
  return result;
}

export async function viewVisitInvoice(appointment_id: string): Promise<InvoiceReference> {
  const query = new URLSearchParams({ appointment_id });
  try {
    const response = await frappeRequest(`${METHOD}.view?${query}`, { method: "GET" });
    if (response.ok) {
      const result = invoiceReference(message(await response.json()));
      if (result !== null) return result;
    }
  } catch {
    // Every failed read has the same display; absence is not proof of no post.
  }
  throw new VisitInvoiceError("unavailable");
}
