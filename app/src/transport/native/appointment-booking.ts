import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

const APPOINTMENT = "Patient Appointment";

// Pinned Desk Link field settings. Frappe verifies ignore_user_permissions
// against the reference DocType/field metadata; Company uses normal filtering.
const links = {
  patient: { doctype: "Patient", ignore_user_permissions: true, filters: { status: "Active" } },
  company: { doctype: "Company" },
  department: { doctype: "Medical Department", ignore_user_permissions: true },
  practitioner: { doctype: "Healthcare Practitioner" },
  appointment_type: {
    doctype: "Appointment Type",
    ignore_user_permissions: true,
    filters: { allow_booking_for: "Department" },
  },
};

export type BookingLinkField = Exclude<keyof typeof links, "practitioner">;
export type PractitionerBookingLinkField = BookingLinkField | "practitioner";
export type BookingLinkOption = { id: string; label: string };
export type DepartmentBooking = Record<"patient" | "company" | "department" | "appointment_type", string> & {
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

export async function searchBookingLinks(field: PractitionerBookingLinkField, query: string, signal: AbortSignal, context?: { department?: string; appointment_for?: "Practitioner" | "Department" }): Promise<BookingLinkOption[]> {
  const args = {
    ...links[field],
    ...(field === "practitioner" && context?.department ? { filters: { department: context.department } } : {}),
    ...(field === "appointment_type" && context?.appointment_for ? { filters: { allow_booking_for: context.appointment_for } } : {}),
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
  if (field === "practitioner") {
    const ids = [...options.values()].filter((option) => option.label === option.id).slice(0, 10).map((option) => option.id);
    if (ids.length) {
      const params = new URLSearchParams({
        fields: JSON.stringify(["name", "practitioner_name"]),
        filters: JSON.stringify([["name", "in", ids]]),
        limit_page_length: "10",
      });
      try {
        const lookup = await fetch(`/api/resource/${encodeURIComponent("Healthcare Practitioner")}?${params}`, {
          credentials: "same-origin", cache: "no-store", redirect: "error",
          headers: { accept: "application/json" }, signal,
        });
        const result: unknown = lookup.ok ? await lookup.json() : null;
        if (record(result) && Array.isArray(result.data)) {
          for (const row of result.data) {
            if (record(row) && text(row.name) && ids.includes(row.name) && text(row.practitioner_name)) {
              options.set(row.name, { id: row.name, label: row.practitioner_name });
            }
          }
        }
      } catch (error) {
        if (signal.aborted) throw error;
        // Link search remains usable when resource-list permission is absent.
      }
    }
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

type BookingContext = Pick<DepartmentBooking, "patient" | "company" | "department" | "appointment_type"> & { duration?: number };
export type PractitionerSlot = {
  appointment_time: string;
  end_time: string;
  duration: number;
  service_unit: string;
  appointment_based_on_check_in: 0 | 1;
  add_video_conferencing: 0 | 1;
};
export type PractitionerAvailability = { practitioner: string; date: string; appointment: BookingContext };
export type PractitionerBooking = BookingContext & { practitioner: string; appointment_date: string; slot: PractitionerSlot };

function seconds(value: unknown): number {
  if (typeof value !== "string" || !/^\d{1,2}:\d{2}(?::\d{2})?$/.test(value)) throw new AppointmentBookingError("uncertain");
  const [h = 0, m = 0, s = 0] = value.split(":").map(Number);
  return h * 3600 + m * 60 + s;
}

export async function listPractitionerSlots(input: PractitionerAvailability, signal?: AbortSignal): Promise<PractitionerSlot[]> {
  const token = nativeCsrfToken();
  if (!token) throw new AppointmentBookingError("denied");
  const response = await frappeRequest("healthcare.healthcare.doctype.patient_appointment.patient_appointment.get_availability_data", {
    method: "POST", headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
    body: JSON.stringify({ practitioner: input.practitioner, date: input.date, appointment: { doctype: APPOINTMENT, __islocal: 1, appointment_for: "Practitioner", ...input.appointment } }),
    signal, redirect: "error",
  });
  if (response.status === 401 || response.status === 403) throw new AppointmentBookingError("denied");
  const body: unknown = response.ok ? await response.json().catch(() => null) : null;
  if (!record(body) || !record(body.message) || !Array.isArray(body.message.slot_details)) throw new AppointmentBookingError("uncertain");
  const result: PractitionerSlot[] = [];
  for (const group of body.message.slot_details) {
    if (!record(group) || !Array.isArray(group.avail_slot) || !Array.isArray(group.appointments)) throw new AppointmentBookingError("uncertain");
    for (const slot of group.avail_slot) {
      if (!record(slot) || !text(slot.from_time) || !text(slot.to_time)) throw new AppointmentBookingError("uncertain");
      const start = seconds(slot.from_time), end = seconds(slot.to_time);
      const maximum = Number(slot.maximum_appointments) || 0;
      const capacity = Number(group.service_unit_capacity) || 0;
      const overlap = Number(group.allow_overlap) === 1;
      let count = 0, disabled = false, unavailable = false;
      const now = new Date();
      const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
      if (today === input.date && start < now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds() && !maximum) disabled = true;
      else for (const booked of group.appointments) {
        if (!record(booked)) throw new AppointmentBookingError("uncertain");
        const bookedStart = seconds(booked.appointment_time);
        const duration = Number(booked.duration) || 0;
        if (maximum && booked.appointment_date === input.date) count++;
        if (bookedStart >= start && bookedStart < end && duration === 0) disabled = true;
        if (start < bookedStart + duration * 60 && end > bookedStart) {
          if (!overlap) disabled = true;
          else {
            if (booked.type === "Unavailable") unavailable = true;
            count++;
          }
        }
        if (overlap && count >= capacity) disabled = true;
      }
      if (unavailable && overlap && capacity > 1) disabled = true;
      if (maximum) disabled = count >= maximum || unavailable;
      if (!disabled) result.push({
        appointment_time: slot.from_time, end_time: slot.to_time,
        duration: maximum ? Number(slot.duration) : (end - start) / 60,
        service_unit: typeof group.service_unit === "string" ? group.service_unit : "",
        appointment_based_on_check_in: maximum ? 1 : 0,
        add_video_conferencing: !maximum && Number(group.tele_conf) === 1 && !capacity ? 1 : 0,
      });
    }
  }
  return result;
}

export async function createDepartmentAppointment(input: DepartmentBooking): Promise<BookedAppointment> {
  return createAppointment(input, "Department");
}

export async function createPractitionerAppointment(input: PractitionerBooking): Promise<BookedAppointment> {
  const { slot, ...context } = input;
  const { end_time: _endTime, ...metadata } = slot;
  return createAppointment({ ...context, ...metadata, duration: context.duration || metadata.duration }, "Practitioner");
}

async function createAppointment(input: DepartmentBooking | (BookingContext & { practitioner: string; appointment_date: string; appointment_time: string }), appointmentFor: "Department" | "Practitioner"): Promise<BookedAppointment> {
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
      body: JSON.stringify({ ...input, appointment_for: appointmentFor }),
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
  if (!record(doc) || doc.doctype !== APPOINTMENT || doc.appointment_for !== appointmentFor ||
      doc.patient !== input.patient || !text(doc.name) || !status ||
      ("practitioner" in input && doc.practitioner !== input.practitioner) ||
      doc.name !== doc.name.trim() || /^(?:\.|\.\.)$|[\u0000-\u001f\u007f]/.test(doc.name)) {
    throw new AppointmentBookingError("uncertain");
  }
  return { id: doc.name, status };
}
