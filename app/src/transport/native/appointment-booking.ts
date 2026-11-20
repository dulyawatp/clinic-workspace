import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

const APPOINTMENT = "Patient Appointment";

// Pinned Desk Link field settings. Frappe verifies ignore_user_permissions
// against the reference DocType/field metadata; Company uses normal filtering.
const links = {
  patient: { doctype: "Patient", ignore_user_permissions: true, filters: { status: "Active" } },
  company: { doctype: "Company" },
  department: { doctype: "Medical Department", ignore_user_permissions: true },
  appointment_type: {
    doctype: "Appointment Type",
    ignore_user_permissions: true,
    filters: { allow_booking_for: "Department" },
  },
};

export type BookingLinkField = keyof typeof links;
export type BookingLinkOption = { id: string; label: string };
export type DepartmentBooking = Record<BookingLinkField, string> & {
  appointment_date: string;
  appointment_time: string;
};

const bookingStatuses = ["Scheduled", "Open", "Confirmed", "No Show"] as const;
export type BookedAppointment = { id: string; status: (typeof bookingStatuses)[number] };

export class AppointmentBookingError extends Error {
  constructor(public reason: "denied" | "validation" | "uncertain", public detail?: string) {
    super(reason);
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string =>
  typeof value === "string" && value.trim() !== "";

export async function searchBookingLinks(field: BookingLinkField, query: string, signal: AbortSignal): Promise<BookingLinkOption[]> {
  const args = {
    ...links[field],
    txt: query,
    reference_doctype: APPOINTMENT,
    link_fieldname: field,
    page_length: 10,
  };
  let response: Response;
  if (field === "patient") {
    // Keep potentially identifying search text out of URLs.
    const token = nativeCsrfToken();
    if (!token) throw new AppointmentBookingError("denied");
    response = await frappeRequest("frappe.desk.search.search_link", {
      method: "POST",
      headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
      body: JSON.stringify(args),
      signal,
      redirect: "error",
    });
  } else {
    const params = new URLSearchParams(Object.entries(args).map(([key, value]) =>
      [key, typeof value === "object" ? JSON.stringify(value) : String(value)],
    ));
    response = await frappeRequest(`frappe.desk.search.search_link?${params}`, { signal, redirect: "error" });
  }
  if (response.status === 401 || response.status === 403) throw new AppointmentBookingError("denied");
  const body: unknown = response.ok ? await response.json() : null;
  if (!record(body) || !Array.isArray(body.message)) throw new AppointmentBookingError("uncertain");
  const options = new Map<string, BookingLinkOption>();
  for (const row of body.message) {
    if (!record(row) || !text(row.value)) throw new AppointmentBookingError("uncertain");
    options.set(row.value, { id: row.value, label: text(row.label) ? row.label : row.value });
  }
  return [...options.values()];
}

const validationTypes = new Set([
  "ValidationError", "MandatoryError", "LinkValidationError", "CancelledLinkError",
  "OverlapError", "MaximumCapacityError",
]);

function safeValidationMessage(encoded: unknown): string | undefined {
  if (typeof encoded !== "string") return;
  try {
    const log: unknown = JSON.parse(encoded);
    if (!Array.isArray(log)) return;
    for (const entry of log) {
      if (typeof entry !== "string") continue;
      const item: unknown = JSON.parse(entry);
      if (!record(item) || !item.raise_exception || !text(item.message)) continue;
      // Detached template contents are inert. Never render or follow the HTML.
      // Remove the linked existing appointment's reference as well as its URL.
      const template = document.createElement("template");
      template.innerHTML = item.message;
      template.content.querySelectorAll("a, script, style, iframe").forEach((node) => node.remove());
      template.content.querySelectorAll("br, p, div, li").forEach((node) => node.after(" "));
      const message = template.content.textContent?.replace(/\s+/g, " ").trim();
      if (message && message.length <= 500 && !/traceback|(?:exception|error):|file ["']/i.test(message)) return message;
    }
  } catch {
    // Malformed message logs are not safe to display.
  }
}

export async function createDepartmentAppointment(input: DepartmentBooking): Promise<BookedAppointment> {
  const token = nativeCsrfToken();
  if (!token) throw new AppointmentBookingError("denied");

  let response: Response;
  try {
    // v1 create_doc uses ordinary Document.insert, like Desk's save of a
    // new document. Native link validation fetches duration from the type.
    response = await fetch(`/api/resource/${encodeURIComponent(APPOINTMENT)}`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { accept: "application/json", "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
      body: JSON.stringify({ ...input, appointment_for: "Department" }),
    });
  } catch {
    throw new AppointmentBookingError("uncertain");
  }
  if (response.status === 401 || response.status === 403) throw new AppointmentBookingError("denied");
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (record(body)) {
      const detail = safeValidationMessage(body._server_messages);
      // Marley's Department/day conflict throws DuplicateEntryError with a
      // raised message. A bare duplicate database error is not that refusal.
      if ((body.exc_type === "DuplicateEntryError" && detail) ||
          (response.status === 417 && typeof body.exc_type === "string" && validationTypes.has(body.exc_type))) {
        throw new AppointmentBookingError("validation", detail);
      }
    }
    throw new AppointmentBookingError("uncertain");
  }

  const doc = record(body) ? body.data : null;
  const status = record(doc) ? bookingStatuses.find((value) => value === doc.status) : undefined;
  if (!record(doc) || doc.doctype !== APPOINTMENT || doc.appointment_for !== "Department" ||
      doc.patient !== input.patient || !text(doc.name) || !status ||
      doc.name !== doc.name.trim() || /^(?:\.|\.\.)$|[\u0000-\u001f\u007f]/.test(doc.name)) {
    throw new AppointmentBookingError("uncertain");
  }
  return { id: doc.name, status };
}
