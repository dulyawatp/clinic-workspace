import { captureClinicRequestContext, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export type NativeEncounterOption = { id: string; label: string };
export type NativeEncounterDraftReceipt = { id: string };
export class NativeEncounterDraftError extends Error {
  constructor(public reason: "denied" | "unavailable" | "conflict" | "unconfirmed" | "unsupported") {
    super(reason);
  }
}

type RecordValue = Record<string, unknown>;
const record = (value: unknown): value is RecordValue => !!value && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const fail = (reason: NativeEncounterDraftError["reason"]): never => { throw new NativeEncounterDraftError(reason); };
const options: RequestInit = { credentials: "same-origin", cache: "no-store", redirect: "error" };

async function read(url: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, { ...options, signal });
  } catch {
    return fail("unavailable");
  }
  if (response.status === 401 || response.status === 403) return fail("denied");
  if (!response.ok) return fail("unavailable");
  try { return await response.json(); } catch { return fail("unavailable"); }
}

async function list(doctype: string, fields: string[], filters: unknown[], signal?: AbortSignal): Promise<RecordValue[]> {
  const params = new URLSearchParams({ fields: JSON.stringify(fields), filters: JSON.stringify(filters), limit_page_length: "20" });
  const body = await read(`/api/resource/${encodeURIComponent(doctype)}?${params}`, signal);
  if (!record(body) || !Array.isArray(body.data) || !body.data.every(record)) return fail("unavailable");
  return body.data;
}

export async function searchNativeEncounterPatients(query: string, signal?: AbortSignal): Promise<NativeEncounterOption[]> {
  const value = query.trim();
  if (!value) return [];
  if (value.length > 100 || /[%_\\\x00-\x1f\x7f]/.test(value)) return fail("unsupported");
  const rows = await list("Patient", ["name", "patient_name"], [["patient_name", "like", `%${value}%`]], signal);
  return rows.map((row) => {
    if (!text(row.name) || !text(row.patient_name)) return fail("unavailable");
    return { id: row.name, label: row.patient_name };
  });
}

export async function readNativeEncounterPractitioner(userId: string, signal?: AbortSignal): Promise<NativeEncounterOption> {
  if (!text(userId)) return fail("unsupported");
  const rows = await list("Healthcare Practitioner", ["name", "practitioner_name", "user_id", "status"], [["user_id", "=", userId], ["status", "=", "Active"]], signal);
  if (rows.length !== 1) return fail("unavailable");
  const row = rows[0];
  if (!row || !text(row.name) || !text(row.practitioner_name) || row.user_id !== userId || row.status !== "Active") return fail("unavailable");
  return { id: row.name, label: row.practitioner_name };
}

export async function listNativeEncounterAppointmentTypes(signal?: AbortSignal): Promise<NativeEncounterOption[]> {
  const rows = await list("Appointment Type", ["name"], [], signal);
  return rows.map((row) => {
    if (!text(row.name)) return fail("unavailable");
    return { id: row.name, label: row.name };
  });
}

export async function createNativeEncounterDraft(input: { userId: string; patientId: string; practitionerId: string; appointmentTypeId: string }): Promise<NativeEncounterDraftReceipt> {
  if (!Object.values(input).every(text)) return fail("unsupported");
  const token = nativeCsrfToken();
  if (!token) return fail("denied");
  const current = captureClinicRequestContext();
  let response: Response;
  try {
    response = await frappeRequest("clinic_ui.api.encounters.create_draft", {
      ...options, method: "POST", headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": token },
      body: JSON.stringify({ patient: input.patientId, practitioner: input.practitionerId, appointment_type: input.appointmentTypeId }),
    });
  } catch { return fail("unconfirmed"); }
  if (response.status === 401 || response.status === 403) return fail("denied");
  if (response.status === 409) return fail("conflict");
  if (!response.ok) return fail("unconfirmed");
  try {
      const body: unknown = await response.json();
      if (!record(body) || !record(body.message) || !text(body.message.id) || body.message.docstatus !== 0) return fail("unconfirmed");
      const id = body.message.id;
      const result = await read(`/api/resource/Patient%20Encounter/${encodeURIComponent(id)}`);
       if (!current() || !record(result) || !record(result.data)) return fail("unconfirmed");
      const doc = result.data;
      if (doc.name !== id || doc.patient !== input.patientId || doc.practitioner !== input.practitionerId ||
          doc.appointment_type !== input.appointmentTypeId || doc.docstatus !== 0 ||
          (doc.appointment != null && doc.appointment !== "") || doc.submit_orders_on_save !== 0 ||
          ![doc.drug_prescription, doc.procedure_prescription, doc.lab_test_prescription, doc.therapies]
            .every((rows) => Array.isArray(rows) && rows.length === 0)) return fail("unconfirmed");
      return { id };
    } catch { return fail("unconfirmed"); }
}
