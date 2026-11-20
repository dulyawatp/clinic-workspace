import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export type PractitionerOption = { id: string; label: string };
type CheckInAppointment = {
  id: string;
  status: string;
  basedOnCheckIn: boolean;
  practitioner: string | null;
};

export class NativeCheckInError extends Error {
  constructor(public reason: "denied" | "unavailable" | "uncertain") {
    super(reason);
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string =>
  typeof value === "string" && value.trim() !== "";

async function readJson(response: Response): Promise<unknown> {
  if (response.status === 401 || response.status === 403)
    throw new NativeCheckInError("denied");
  if (!response.ok) throw new NativeCheckInError("unavailable");
  return response.json();
}

export async function readCheckInAppointment(id: string, signal?: AbortSignal): Promise<CheckInAppointment> {
  const response = await fetch(`/api/resource/Patient%20Appointment/${encodeURIComponent(id)}`, {
    signal,
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    headers: { accept: "application/json" },
  });
  const body = await readJson(response);
  const doc = record(body) ? body.data : null;
  if (!record(doc) || doc.name !== id || !text(doc.status) ||
      !(doc.practitioner === null || doc.practitioner === "" || text(doc.practitioner)))
    throw new NativeCheckInError("unavailable");
  const flag = doc.appointment_based_on_check_in;
  if (flag !== 0 && flag !== 1 && typeof flag !== "boolean")
    throw new NativeCheckInError("unavailable");
  return {
    id,
    status: doc.status,
    basedOnCheckIn: flag === 1 || flag === true,
    practitioner: text(doc.practitioner) ? doc.practitioner : null,
  };
}

export async function searchCheckInPractitioners(query: string, signal?: AbortSignal): Promise<PractitionerOption[]> {
  const params = new URLSearchParams({
    doctype: "Healthcare Practitioner",
    txt: query,
    reference_doctype: "Patient Appointment",
    link_fieldname: "practitioner",
    page_length: "10",
  });
  const response = await frappeRequest(`frappe.desk.search.search_link?${params}`, { signal, redirect: "error" });
  const body = await readJson(response);
  if (!record(body) || !Array.isArray(body.message))
    throw new NativeCheckInError("unavailable");
  const options = new Map<string, PractitionerOption>();
  for (const row of body.message) {
    if (!record(row) || !text(row.value)) throw new NativeCheckInError("unavailable");
    options.set(row.value, { id: row.value, label: text(row.label) ? row.label : row.value });
  }
  return [...options.values()];
}

export async function checkInAppointment(appointmentId: string, practitioner: string): Promise<CheckInAppointment> {
  const token = nativeCsrfToken();
  if (!token) throw new NativeCheckInError("denied");

  let response: Response;
  try {
    response = await frappeRequest(
      "healthcare.healthcare.doctype.patient_appointment.patient_appointment.check_in_appointment",
      {
        method: "POST",
        redirect: "error",
        headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
        body: JSON.stringify({ appointment_id: appointmentId, practitioner }),
      },
    );
  } catch {
    throw new NativeCheckInError("uncertain");
  }
  if (response.status === 401 || response.status === 403)
    throw new NativeCheckInError("denied");
  try {
    if (response.ok) {
      const saved = await readCheckInAppointment(appointmentId);
      if (saved.status === "Checked In" && saved.practitioner === practitioner) return saved;
    }
  } catch {
    // The POST may have saved even when we cannot read back the appointment.
  }
  throw new NativeCheckInError("uncertain");
}
