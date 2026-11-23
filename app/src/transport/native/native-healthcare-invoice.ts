import { frappeRequest, FRAPPE_CSRF_HEADER } from "./frappe";
import { NativeInvoiceHttpError } from "./native-invoice";
import type { NativeInvoiceResult } from "./native-invoice";
import { nativeCsrfToken } from "./session";

export interface HealthcareServiceRow extends Record<string, unknown> {
  service?: string | null;
  reference_type: string;
  reference_name: string;
  qty?: number | string | null;
}

export interface HealthcareInvoiceContext {
  appointment: string;
  patient: string;
  company: string;
  customer: string;
  rows: HealthcareServiceRow[];
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function id(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value === value.trim();
}

async function get(method: string, args: Record<string, string>, signal?: AbortSignal): Promise<unknown> {
  const response = await frappeRequest(`${method}?${new URLSearchParams(args)}`, { signal });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new NativeInvoiceHttpError(response.status, body);
  if (!record(body) || body.exception || body.exc || !("message" in body))
    throw new Error("Invalid native healthcare invoice response");
  return body.message;
}

async function getDocument(doctype: string, name: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
  if (!id(name)) throw new Error("Invalid native document name");
  const doc = await get("frappe.client.get", { doctype, name }, signal);
  if (!record(doc) || doc.doctype !== doctype || doc.name !== name)
    throw new Error("Invalid native document response");
  return doc;
}

/** Services are patient-wide, not restricted to the appointment. No row is selected by default. */
export async function getHealthcareInvoiceContext(appointment: string, signal?: AbortSignal): Promise<HealthcareInvoiceContext> {
  const visit = await getDocument("Patient Appointment", appointment, signal);
  if (!id(visit.patient) || visit.patient.startsWith("cpr1_") || !id(visit.company))
    throw new Error("Appointment lacks native patient or company");
  const patient = await getDocument("Patient", visit.patient, signal);
  if (!id(patient.customer)) throw new Error("Patient lacks linked native customer");
  const args = { patient: visit.patient, customer: patient.customer, company: visit.company };
  const rows = await get("healthcare.healthcare.utils.get_healthcare_services_to_invoice", args, signal);
  if (!Array.isArray(rows) || !rows.every((row) =>
    record(row) && (typeof row.service === "string" || row.service == null) &&
    id(row.reference_type) && id(row.reference_name) &&
    (row.qty == null || ((typeof row.qty === "number" || typeof row.qty === "string") &&
      String(row.qty).trim() !== "" && Number.isFinite(Number(row.qty))))))
    throw new Error("Invalid native healthcare services response");
  return { appointment, ...args, rows: rows as HealthcareServiceRow[] };
}

/** Run native Desk service preparation on an unsaved service-only invoice. */
export async function prepareHealthcareInvoice(
  context: HealthcareInvoiceContext, selectedRows: HealthcareServiceRow[], signal?: AbortSignal,
): Promise<NativeInvoiceResult> {
  if (!selectedRows.length) throw new Error("Select at least one healthcare service");
  if (new Set(selectedRows).size !== selectedRows.length ||
      selectedRows.some((row) => !context.rows.includes(row) || !id(row.service)))
    throw new Error("Selected services must come from the native context");
  const token = nativeCsrfToken();
  if (!token) throw new Error("Missing native session CSRF token");
  const fields = [
    "rate", "income_account", "qty", "description", "discount_percentage",
    "coverage_rate", "coverage_qty", "insurance_payor", "patient_insurance_policy",
    "coverage_percentage", "insurance_coverage",
  ] as const;
  const checkedValues = selectedRows.map((row) => {
    const values: Record<string, string | false> = {
      dn: row.reference_name, dt: row.reference_type, item: row.service!,
    };
    for (const field of fields) {
      const value = row[field];
      if (value != null && typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean")
        throw new Error("Invalid native healthcare service field");
      values[field] = value == null ? false : String(value);
    }
    return values;
  });
  const response = await frappeRequest("run_doc_method", {
    method: "POST", signal,
    headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
    body: JSON.stringify({
      docs: {
        doctype: "Sales Invoice", docstatus: 0, patient: context.patient, customer: context.customer,
        company: context.company, __islocal: 1, update_stock: 0, items: [],
      },
      method: "set_healthcare_services", args: { checked_values: checkedValues },
    }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new NativeInvoiceHttpError(response.status, body);
  if (!record(body) || body.exception || body.exc || !Array.isArray(body.docs) || body.docs.length !== 1)
    throw new Error("Invalid native healthcare invoice preparation response");
  const doc: unknown = body.docs[0];
  if (!record(doc) || doc.doctype !== "Sales Invoice" || doc.docstatus !== 0 ||
      doc.patient !== context.patient || doc.customer !== context.customer || doc.company !== context.company ||
      !Array.isArray(doc.items) || doc.items.length !== selectedRows.length ||
      !doc.items.every((item, index) => {
        const source = selectedRows[index];
        return source && record(item) && item.item_code === source.service &&
          item.reference_dt === source.reference_type && item.reference_dn === source.reference_name;
      }))
    throw new Error("Invalid native healthcare invoice preparation response");
  return doc as NativeInvoiceResult;
}

/** Recover the exact persisted draft or submitted invoice. */
export async function readHealthcareInvoice(name: string, signal?: AbortSignal): Promise<NativeInvoiceResult> {
  const doc = await getDocument("Sales Invoice", name, signal);
  if ((doc.docstatus !== 0 && doc.docstatus !== 1) || !Array.isArray(doc.items) || !doc.items.every(record))
    throw new Error("Invalid native invoice");
  return doc as NativeInvoiceResult;
}
