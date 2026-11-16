import {
  type NativeAppointmentDetail,
  parseNativeAppointmentDetail,
} from "./appointment";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";
import { nativeCsrfToken } from "./session";

/** Distinct from the patient lock: appointment and patient writes never share it. */
const LOCK = "clinic.appointment-write.pending";

const ACK_TOKEN = /^[0-9a-f]{64}$/;

export type AppointmentWriteKind = "book" | "reschedule" | "checkin";
/** Fence kinds of the designed cancel/no-show transitions. */
export type AppointmentTransitionKind =
  | "appointment_cancel"
  | "appointment_no_show";
export type AppointmentAckKind = AppointmentWriteKind | AppointmentTransitionKind;
export type AppointmentWriteStatus =
  | "booked"
  | "rescheduled"
  | "checked_in"
  | "cancelled"
  | "no_show";

export type AppointmentWriteFailure =
  | "locked"
  | "uncertain"
  | "stale"
  | "conflict"
  | "validation"
  | "auth"
  | "forbidden"
  | "unavailable";

export class NativeAppointmentWriteError extends Error {
  readonly code: AppointmentWriteFailure;

  constructor(code: AppointmentWriteFailure) {
    super(code);
    this.name = "NativeAppointmentWriteError";
    this.code = code;
  }
}

export interface BookAppointmentInput {
  operation_key: string;
  workspace_patient_id: string;
  appointment_date: string;
  appointment_time: string;
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

const STATUS_BY_KIND: Readonly<Record<AppointmentAckKind, AppointmentWriteStatus>> =
  {
    book: "booked",
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

const listeners = new Set<() => void>();

function notifyLock(): void {
  for (const listener of listeners) listener();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
 * The browser lock is the only persisted write state. It stores no operation
 * key, token, patient or appointment data, so a reload cannot resume a write.
 */
export function appointmentWriteLocked(): boolean {
  try {
    return localStorage.getItem(LOCK) !== null;
  } catch {
    return true;
  }
}

/** Same-tab notification; cross-tab changes also emit a `storage` event. */
export function subscribeAppointmentWriteLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function persistLock(): void {
  try {
    localStorage.setItem(LOCK, "1");
    if (localStorage.getItem(LOCK) !== "1")
      throw new NativeAppointmentWriteError("locked");
  } catch {
    throw new NativeAppointmentWriteError("locked");
  }
}

function clearLock(): void {
  try {
    localStorage.removeItem(LOCK);
    if (appointmentWriteLocked())
      throw new NativeAppointmentWriteError("uncertain");
  } catch {
    throw new NativeAppointmentWriteError("uncertain");
  }
  notifyLock();
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function validBookInput(input: BookAppointmentInput): void {
  if (
    !isText(input.operation_key) ||
    !isText(input.workspace_patient_id) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(input.appointment_date) ||
    !/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(input.appointment_time)
  )
    throw new NativeAppointmentWriteError("validation");
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
  if (!isRecord(body) || !isRecord(body.message)) return null;
  return typeof body.message.code === "string" ? body.message.code : null;
}

function failure(
  status: number,
  body: unknown,
): NativeAppointmentWriteError {
  if (status === 409) {
    const code = errorCode(body);
    if (code === "STALE_VERSION")
      return new NativeAppointmentWriteError("stale");
    if (code === "OPERATION_CONFLICT")
      return new NativeAppointmentWriteError("conflict");
    return new NativeAppointmentWriteError("locked");
  }
  if (status === 401) return new NativeAppointmentWriteError("auth");
  if (status === 403) return new NativeAppointmentWriteError("forbidden");
  if (status === 422) return new NativeAppointmentWriteError("validation");
  return new NativeAppointmentWriteError("uncertain");
}

function parseWriteResult(
  ackKind: AppointmentAckKind,
  expectedAlias: string | undefined,
  body: unknown,
): AppointmentWriteResult {
  if (
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["status", "ackToken", "appointment"])
  )
    throw new NativeAppointmentWriteError("uncertain");

  const message = body.message;
  if (
    message.status !== STATUS_BY_KIND[ackKind] ||
    typeof message.ackToken !== "string" ||
    !ACK_TOKEN.test(message.ackToken)
  )
    throw new NativeAppointmentWriteError("uncertain");

  let appointment: NativeAppointmentDetail;
  try {
    appointment = parseNativeAppointmentDetail(
      message.appointment,
      expectedAlias,
    );
  } catch {
    throw new NativeAppointmentWriteError("uncertain");
  }

  const expectedStatus = EXPECTED_DETAIL_STATUS[ackKind];
  if (expectedStatus !== undefined && appointment.status !== expectedStatus)
    throw new NativeAppointmentWriteError("uncertain");

  return {
    status: STATUS_BY_KIND[ackKind],
    appointment,
  };
}

async function acknowledge(
  token: string,
  kind: AppointmentAckKind,
  operationKey: string,
  ackToken: string,
): Promise<void> {
  const response = await frappeRequest("clinic_ui.api.appointments.ack", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      [FRAPPE_CSRF_HEADER]: token,
    },
    body: JSON.stringify({
      operation_key: operationKey,
      kind,
      ack_token: ackToken,
    }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (
    response.status !== 200 ||
    !isRecord(body) ||
    !isRecord(body.message) ||
    !hasExactKeys(body.message, ["acknowledged"]) ||
    body.message.acknowledged !== true
  )
    throw new NativeAppointmentWriteError("uncertain");
}

async function writeAppointment(
  ackKind: AppointmentAckKind,
  method: string,
  operationKey: string,
  expectedAlias: string | undefined,
  payload: Record<string, string>,
): Promise<AppointmentWriteResult> {
  const token = nativeCsrfToken();
  if (!token || appointmentWriteLocked())
    throw new NativeAppointmentWriteError("locked");

  // Lock before dispatch. A reload, tab crash or lost response can never turn
  // an ambiguous write into a fresh attempt.
  persistLock();

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
    notifyLock();
    throw new NativeAppointmentWriteError("uncertain");
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    notifyLock();
    throw failure(response.status, body);
  }

  let result: AppointmentWriteResult;
  try {
    result = parseWriteResult(ackKind, expectedAlias, body);
  } catch {
    notifyLock();
    throw new NativeAppointmentWriteError("uncertain");
  }

  const ackToken =
    isRecord(body) && isRecord(body.message) ? body.message.ackToken : null;
  if (typeof ackToken !== "string")
    throw new NativeAppointmentWriteError("uncertain");

  // One explicit acknowledgement, never retried. The token stays in memory and
  // is never written to storage. A lost or failed acknowledgement keeps the
  // browser locked for manual reconciliation.
  try {
    await acknowledge(token, ackKind, operationKey, ackToken);
  } catch {
    notifyLock();
    throw new NativeAppointmentWriteError("uncertain");
  }

  clearLock();
  return result;
}

export async function bookNativeAppointment(
  input: BookAppointmentInput,
): Promise<AppointmentWriteResult> {
  validBookInput(input);
  return writeAppointment("book", "clinic_ui.api.appointments.book", input.operation_key, undefined, {
    operation_key: input.operation_key,
    workspace_patient_id: input.workspace_patient_id,
    appointment_date: input.appointment_date,
    appointment_time: input.appointment_time,
  });
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
    input.operation_key,
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
    input.operation_key,
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
 * target alias, the workspace patient and the operator's reason.
 */
export async function cancelNativeAppointment(
  input: CancelAppointmentInput,
): Promise<AppointmentWriteResult> {
  validTransitionInput(input);
  return writeAppointment(
    "appointment_cancel",
    "clinic_ui.api.appointments_cancel_write.cancel",
    input.operation_key,
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
    input.operation_key,
    input.appointment_id,
    {
      operation_key: input.operation_key,
      patient: input.patient,
      appointment_id: input.appointment_id,
      reason: input.reason,
    },
  );
}
