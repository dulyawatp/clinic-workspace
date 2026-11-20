import {
  type NativeAppointmentDetail,
  parseNativeAppointmentDetail,
} from "./appointment";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

export type AppointmentWriteKind = "reschedule" | "checkin";
/** Operation kinds of the designed cancel/no-show transitions. */
export type AppointmentTransitionKind =
  | "appointment_cancel"
  | "appointment_no_show";
export type AppointmentOperationKind =
  | AppointmentWriteKind
  | AppointmentTransitionKind;
export type AppointmentWriteStatus =
  | "rescheduled"
  | "checked_in"
  | "cancelled"
  | "no_show";

/**
 * A deterministic refusal (`stale`, `validation`, `auth`, `forbidden`) or an
 * unresolved outcome (`uncertain`). `conflict` is retained for a historical
 * operation-conflict code; the converted lane reports a lost or unreadable
 * response as uncertain.
 */
export type AppointmentWriteFailure =
  | "uncertain"
  | "stale"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden";

export class NativeAppointmentWriteError extends Error {
  readonly code: AppointmentWriteFailure;

  constructor(code: AppointmentWriteFailure) {
    super(code);
    this.name = "NativeAppointmentWriteError";
    this.code = code;
  }
}

export interface RescheduleAppointmentInput {
  operation_key: string;
  workspace_appointment_id: string;
  base_modified: string;
  appointment_date: string;
  appointment_time: string;
}

export interface CheckinAppointmentInput {
  operation_key: string;
  workspace_appointment_id: string;
  base_modified: string;
}

/** Cancel and manual no-show share one request shape (reason required). */
export interface CancelAppointmentInput {
  operation_key: string;
  patient: string;
  appointment_id: string;
  reason: string;
}

export type NoShowAppointmentInput = CancelAppointmentInput;

export interface AppointmentWriteResult {
  status: AppointmentWriteStatus;
  appointment: NativeAppointmentDetail;
}

const STATUS_BY_OPERATION: Readonly<
  Record<AppointmentOperationKind, AppointmentWriteStatus>
> = {
  reschedule: "rescheduled",
  checkin: "checked_in",
  appointment_cancel: "cancelled",
  appointment_no_show: "no_show",
};

/**
 * Transitions that must return their own terminal status. The client never
 * writes a status: this only refuses a success envelope whose re-read status
 * contradicts the operation it claims to have completed.
 */
const EXPECTED_DETAIL_STATUS: Readonly<Record<string, string>> = {
  appointment_cancel: "Cancelled",
  appointment_no_show: "No Show",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
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

/**
 * Cancel/no-show inputs: the reason is copied verbatim into the appointment's
 * notes by the server, so it must be a single bounded line. Nothing is
 * dispatched when it is not.
 */
function validTransitionInput(input: CancelAppointmentInput): void {
  if (
    !isText(input.operation_key) ||
    !isText(input.patient) ||
    !isText(input.appointment_id) ||
    typeof input.reason !== "string" ||
    input.reason.trim() !== input.reason ||
    input.reason.length === 0 ||
    input.reason.length > 200 ||
    [...input.reason].some((char) => {
      const code = char.charCodeAt(0);
      return code < 32 || code === 127;
    })
  )
    throw new NativeAppointmentWriteError("validation");
}

function validTargetInput(
  input: RescheduleAppointmentInput | CheckinAppointmentInput,
): void {
  if (
    !isText(input.operation_key) ||
    !isText(input.workspace_appointment_id) ||
    !isText(input.base_modified)
  )
    throw new NativeAppointmentWriteError("validation");
}

function errorCode(body: unknown): string | null {
  if (!isRecord(body)) return null;
  if (isRecord(body.message) && typeof body.message.code === "string")
    return body.message.code;
  return typeof body.code === "string" ? body.code : null;
}

function failure(status: number, body: unknown): NativeAppointmentWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "STALE_VERSION")
      return new NativeAppointmentWriteError("stale");
    if (code === "OPERATION_CONFLICT")
      return new NativeAppointmentWriteError("conflict");
    return new NativeAppointmentWriteError("uncertain");
  }
  if (status === 401) return new NativeAppointmentWriteError("auth");
  if (status === 403) return new NativeAppointmentWriteError("forbidden");
  if (status === 422) return new NativeAppointmentWriteError("validation");
  return new NativeAppointmentWriteError("uncertain");
}

/**
 * Validates the exact `{status, appointment}` result. An unexpected envelope
 * field (including the retired `ackToken`) fails the write as uncertain, the
 * operation status must match, and the echoed detail must be a readable
 * appointment.
 */
function parseWriteResult(
  operation: AppointmentOperationKind,
  expectedId: string | undefined,
  body: unknown,
): AppointmentWriteResult {
  if (!isRecord(body) || !isRecord(body.message))
    throw new NativeAppointmentWriteError("uncertain");

  const message = body.message;
  if (
    !hasExactKeys(message, ["status", "appointment"]) ||
    message.status !== STATUS_BY_OPERATION[operation]
  )
    throw new NativeAppointmentWriteError("uncertain");

  let appointment: NativeAppointmentDetail;
  try {
    appointment = parseNativeAppointmentDetail(
      message.appointment,
      expectedId,
    );
  } catch {
    throw new NativeAppointmentWriteError("uncertain");
  }

  const expectedStatus = EXPECTED_DETAIL_STATUS[operation];
  if (expectedStatus !== undefined && appointment.status !== expectedStatus)
    throw new NativeAppointmentWriteError("uncertain");

  return { status: STATUS_BY_OPERATION[operation], appointment };
}

/**
 * One appointment write through the native Frappe method.
 *
 * The success body is the canonical `{status, appointment}` echo: the backend
 * owns canonicalisation, so the returned detail is validated by shape, never
 * compared field-by-field with what the form submitted. There is no
 * acknowledgement request and no browser-side fence: the caller keeps the
 * in-flight form guard and renders a deterministic refusal as a known failure.
 * An unknown outcome is not reconciled for the reader: the records may be
 * refreshed best-effort, and the reader must check current records before any
 * repeat. Every path throws at most once, never retried.
 */
async function writeAppointment(
  operation: AppointmentOperationKind,
  method: string,
  expectedId: string | undefined,
  payload: Record<string, string>,
): Promise<AppointmentWriteResult> {
  const token = nativeCsrfToken();
  if (!token) throw new NativeAppointmentWriteError("uncertain");

  let response: Response;
  try {
    response = await frappeRequest(method, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        [FRAPPE_CSRF_HEADER]: token,
      },
      body: JSON.stringify(payload),
    });
  } catch {
    // The request may have reached the server: the outcome is unknown.
    throw new NativeAppointmentWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw failure(response.status, body);

  return parseWriteResult(operation, expectedId, body);
}

export async function rescheduleNativeAppointment(
  input: RescheduleAppointmentInput,
): Promise<AppointmentWriteResult> {
  validTargetInput(input);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(input.appointment_date) ||
    !/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(input.appointment_time)
  )
    throw new NativeAppointmentWriteError("validation");
  return writeAppointment(
    "reschedule",
    "clinic_ui.api.appointments.reschedule",
    input.workspace_appointment_id,
    {
      operation_key: input.operation_key,
      workspace_appointment_id: input.workspace_appointment_id,
      base_modified: input.base_modified,
      appointment_date: input.appointment_date,
      appointment_time: input.appointment_time,
    },
  );
}

export async function checkinNativeAppointment(
  input: CheckinAppointmentInput,
): Promise<AppointmentWriteResult> {
  validTargetInput(input);
  return writeAppointment(
    "checkin",
    "clinic_ui.api.appointments.checkin",
    input.workspace_appointment_id,
    {
      operation_key: input.operation_key,
      workspace_appointment_id: input.workspace_appointment_id,
      base_modified: input.base_modified,
    },
  );
}

/**
 * Cancel one appointment through the designed transition. The server decides
 * eligibility and writes the terminal status; the browser only sends the
 * target, the workspace patient and the operator's reason.
 */
export async function cancelNativeAppointment(
  input: CancelAppointmentInput,
): Promise<AppointmentWriteResult> {
  validTransitionInput(input);
  return writeAppointment(
    "appointment_cancel",
    "clinic_ui.api.appointments_cancel_write.cancel",
    input.appointment_id,
    {
      operation_key: input.operation_key,
      patient: input.patient,
      appointment_id: input.appointment_id,
      reason: input.reason,
    },
  );
}

/** Record a manual no-show through the designed transition. */
export async function noShowNativeAppointment(
  input: NoShowAppointmentInput,
): Promise<AppointmentWriteResult> {
  validTransitionInput(input);
  return writeAppointment(
    "appointment_no_show",
    "clinic_ui.api.appointments_cancel_write.no_show",
    input.appointment_id,
    {
      operation_key: input.operation_key,
      patient: input.patient,
      appointment_id: input.appointment_id,
      reason: input.reason,
    },
  );
}
