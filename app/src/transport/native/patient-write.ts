import { frappeRequest, FRAPPE_CSRF_HEADER } from "./frappe";
import { nativeCsrfToken } from "./session";

export interface PatientWriteFields {
  first_name: string;
  last_name: string;
  sex: string;
  dob: string;
}

export type PatientWriteInput = PatientWriteFields & {
  operation_key: string;
  workspace_patient_id?: string;
  base_modified?: string;
};

/** No patient data or user identity is stored in the browser lock. */
const LOCK = "clinic.patient-write.pending";

export function patientWriteLocked(): boolean {
  try {
    return localStorage.getItem(LOCK) !== null;
  } catch {
    return true;
  }
}

export async function saveNativePatient(input: PatientWriteInput): Promise<void> {
  const token = nativeCsrfToken();
  if (!token || patientWriteLocked()) throw new Error("locked");
  // Lock before dispatch: reload, browser failure, and identity changes cannot
  // silently turn an ambiguous result into a fresh operation.
  try {
    localStorage.setItem(LOCK, "1");
    if (localStorage.getItem(LOCK) !== "1") throw new Error("storage");
  } catch {
    throw new Error("locked");
  }
  const method = input.workspace_patient_id ? "update" : "create";
  const response = await frappeRequest(`clinic_ui.api.patients.${method}`, {
    method: "POST",
    headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
    body: JSON.stringify(input),
  });
  const body: unknown = await response.json().catch(() => null);
  const message = body && typeof body === "object" && "message" in body ? body.message : null;
  if (!response.ok || !message || typeof message !== "object" ||
      !("status" in message) || message.status !== (method === "create" ? "created" : "updated") ||
      !("ackToken" in message) || typeof message.ackToken !== "string" ||
      !/^[0-9a-f]{64}$/.test(message.ackToken) ||
      !("patient" in message) || !message.patient || typeof message.patient !== "object")
    throw new Error("uncertain");
  const patient = message.patient as Record<string, unknown>;
  if (typeof patient.workspace_patient_id !== "string" || !patient.workspace_patient_id.trim() ||
      (input.workspace_patient_id && patient.workspace_patient_id !== input.workspace_patient_id) ||
      typeof patient.display_name !== "string" || !patient.display_name.trim() ||
      typeof patient.modified !== "string" || !patient.modified.trim() ||
      patient.first_name !== input.first_name || patient.last_name !== input.last_name ||
      patient.sex !== input.sex || patient.dob !== input.dob ||
      (patient.status !== null && (typeof patient.status !== "string" || !patient.status.trim())))
    throw new Error("uncertain");
  // Acknowledgement is a separate one-shot request. The token never enters
  // persistent storage; loss of either response leaves the browser locked.
  const ack = await frappeRequest("clinic_ui.api.patients.ack", {
    method: "POST",
    headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
    body: JSON.stringify({ operation_key: input.operation_key, kind: method, ack_token: message.ackToken }),
  });
  const ackBody: unknown = await ack.json().catch(() => null);
  if (ack.status !== 200 || !ackBody || typeof ackBody !== "object" ||
      !("message" in ackBody) || !ackBody.message || typeof ackBody.message !== "object" ||
      !("acknowledged" in ackBody.message) || ackBody.message.acknowledged !== true)
    throw new Error("uncertain");
  try {
    localStorage.removeItem(LOCK);
    if (patientWriteLocked()) throw new Error("storage");
  } catch {
    throw new Error("uncertain");
  }
}
