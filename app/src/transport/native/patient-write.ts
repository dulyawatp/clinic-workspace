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

/** Deterministic refusals: the server rejected the write and changed nothing. */
export type PatientWriteFailureKind = "denied" | "validation" | "stale";

export class PatientWriteRejectedError extends Error {
  constructor(
    readonly kind: PatientWriteFailureKind,
    readonly status: number,
    readonly code?: string,
  ) {
    super(kind);
    this.name = "PatientWriteRejectedError";
  }
}

/** A lost or unreadable response: the write may or may not have applied. */
export class PatientWriteOutcomeUnknownError extends Error {
  constructor() {
    super("unknown outcome");
    this.name = "PatientWriteOutcomeUnknownError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function responseCode(body: unknown): string | undefined {
  if (!isRecord(body)) return undefined;
  if (isRecord(body.message) && typeof body.message.code === "string")
    return body.message.code;
  return typeof body.code === "string" ? body.code : undefined;
}

/**
 * Maps a native refusal to its deterministic reason while preserving the
 * response status and backend code. Anything else (network loss, unknown
 * status, unreadable body) is an unknown outcome, never a silent success.
 */
function rejection(response: Response, body: unknown): Error {
  const code = responseCode(body);
  if (response.status === 403)
    return new PatientWriteRejectedError("denied", 403, code);
  if (response.status === 422)
    return new PatientWriteRejectedError("validation", 422, code);
  if (response.status === 409 && code === "STALE_VERSION")
    return new PatientWriteRejectedError("stale", 409, code);
  return new PatientWriteOutcomeUnknownError();
}

/**
 * One patient write through the native Frappe method.
 *
 * The success body is the allowlisted `{status, patient}` echo: the backend
 * canonicalises names, sex and dates, so only the identity and version fields
 * are validated here, never equality with what the form submitted. A returned
 * alias may differ from the stored one, so it is never compared either. `status`
 * is required because every patient detail carries it, though it may be null.
 * There is no acknowledgement request and no browser-side fence: the caller
 * keeps the in-flight state, renders a deterministic refusal as a known
 * failure, and reconciles an unknown outcome by refreshing records before any
 * repeat. Every path throws at most once, never retried.
 */
export async function saveNativePatient(
  input: PatientWriteInput,
): Promise<void> {
  const token = nativeCsrfToken();
  if (!token) throw new PatientWriteOutcomeUnknownError();
  const method = input.workspace_patient_id ? "update" : "create";
  let response: Response;
  try {
    response = await frappeRequest(`clinic_ui.api.patients.${method}`, {
      method: "POST",
      headers: { "content-type": "application/json", [FRAPPE_CSRF_HEADER]: token },
      body: JSON.stringify(input),
    });
  } catch {
    // The request may have reached the server: the outcome is unknown.
    throw new PatientWriteOutcomeUnknownError();
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw rejection(response, body);
  const message =
    isRecord(body) && "message" in body ? body.message : null;
  if (
    !isRecord(message) ||
    message.status !== (method === "create" ? "created" : "updated") ||
    !isRecord(message.patient)
  )
    throw new PatientWriteOutcomeUnknownError();
  const patient = message.patient;
  if (
    !isText(patient.workspace_patient_id) ||
    !isText(patient.display_name) ||
    !isText(patient.modified) ||
    !("status" in patient) ||
    (patient.status !== null && !isText(patient.status))
  )
    throw new PatientWriteOutcomeUnknownError();
}
