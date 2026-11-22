import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import { frappeRequest } from "./frappe";
import { classifyNativeForbidden } from "./session";

const APPOINTMENTS_METHOD =
  "clinic_ui.api.appointments.synthetic_appointments";
const VIEW_METHOD = "clinic_ui.api.appointments.view";

function unavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The canonical DTO fields. A native docname is a *value* in
 * `workspace_patient_id` or `appointment_id`, never a raw alternate field such
 * as `patient` or `name`.
 */
const ITEM_KEYS = [
  "workspace_patient_id",
  "appointment_id",
  "appointment_date",
  "appointment_time",
  "status",
] as const;
const DETAIL_KEYS = [...ITEM_KEYS, "modified"] as const;
const READ_KEYS = [...DETAIL_KEYS, "can_reschedule", "can_checkin"] as const;

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME = /^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/;
const STATUS = /^[A-Za-z][A-Za-z0-9 _-]{0,63}$/;
const VERSION =
  /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$/;

/** Opaque workspace ids only: trimmed, bounded, no control characters. */
function isSafeId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim() === value &&
    value.length > 0 &&
    value.length <= 256 &&
    ![...value].some((char) => {
      const code = char.charCodeAt(0);
      return code < 32 || code === 127;
    })
  );
}

function isPatientReference(value: unknown): value is string {
  if (isSafeId(value)) return true;
  if (typeof value === "string" && value.startsWith("cpr1_")) {
    return value.length <= 808 && /^cpr1_[A-Za-z0-9_-]+$/.test(value);
  }
  return false;
}

function isSafePatientDisplayName(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 &&
    value.trim() === value && value !== "XXXXXXXX" &&
    [...value].length <= 140 && !/[<>\p{Cc}\p{Cf}\p{Cs}]/u.test(value);
}

function isCalendarDate(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = ISO_DATE.exec(value);
  if (match === null) return false;
  const [, yearText, monthText, dayText] = match;
  if (
    yearText === undefined ||
    monthText === undefined ||
    dayText === undefined
  )
    return false;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
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

function readItemFields(value: Record<string, unknown>): NativeAppointmentItem {
  if (
    !isPatientReference(value.workspace_patient_id) ||
    !isSafeId(value.appointment_id) ||
    !isCalendarDate(value.appointment_date) ||
    typeof value.appointment_time !== "string" ||
    !TIME.test(value.appointment_time) ||
    typeof value.status !== "string" ||
    !STATUS.test(value.status)
  )
    throw unavailable();
  return {
    workspace_patient_id: value.workspace_patient_id,
    appointment_id: value.appointment_id,
    appointment_date: value.appointment_date,
    appointment_time: value.appointment_time,
    status: value.status,
  };
}

/**
 * One appointment row from the list or a detail payload. Only the canonical DTO
 * fields are accepted; the identifier fields may carry an alias or a native
 * docname, but a raw alternate field is refused.
 */
export function readNativeAppointmentItem(value: unknown): NativeAppointmentItem {
  if (!isRecord(value) || !hasExactKeys(value, ITEM_KEYS)) throw unavailable();
  return readItemFields(value);
}

export interface NativeAppointmentItem {
  workspace_patient_id: string;
  appointment_id: string;
  appointment_date: string;
  appointment_time: string;
  status: string;
}

/** The six-field detail contract: the list fields plus the version token. */
export interface NativeAppointmentDetail extends NativeAppointmentItem {
  modified: string;
}

/** Authorized read detail; only non-fixtures can expose a native docname. */
export interface NativeAppointmentRead extends NativeAppointmentDetail {
  can_reschedule: boolean;
  can_checkin: boolean;
  is_fixture?: boolean;
  native_appointment_id?: string;
  native_checkin_enabled?: boolean;
  patient_display_name?: string;
}

/**
 * Parser for the write-result appointment. Only the canonical detail fields are
 * accepted; a raw alternate identifier field or a malformed version fails the
 * whole write.
 */
export function parseNativeAppointmentDetail(
  value: unknown,
  expectedId?: string,
): NativeAppointmentDetail {
  if (!isRecord(value) || !hasExactKeys(value, DETAIL_KEYS))
    throw unavailable();
  return readDetailFields(value, expectedId);
}

/** Detail fields shared by the write result and the versioned read. */
function readDetailFields(
  value: Record<string, unknown>,
  expectedId?: string,
): NativeAppointmentDetail {
  const item = readItemFields(value);
  if (
    typeof value.modified !== "string" ||
    value.modified.length > 64 ||
    !VERSION.test(value.modified) ||
    (expectedId !== undefined && item.appointment_id !== expectedId)
  )
    throw unavailable();
  return { ...item, modified: value.modified };
}

/**
 * Parser for `appointments.view`. Older endpoints can omit fixture identity.
 * Never infer it from an ID, status, date, or unrelated action flags.
 */
export function parseNativeAppointmentRead(
  value: unknown,
  expectedId?: string,
): NativeAppointmentRead {
  if (!isRecord(value)) throw unavailable();
  const hasFixture = Object.hasOwn(value, "is_fixture");
  const hasNativeId = Object.hasOwn(value, "native_appointment_id");
  const hasNativeCheckin = Object.hasOwn(value, "native_checkin_enabled");
  const hasPatientName = Object.hasOwn(value, "patient_display_name");
  if (!hasExactKeys(value, [
    ...READ_KEYS,
    ...(hasFixture ? ["is_fixture"] : []),
    ...(hasNativeId ? ["native_appointment_id"] : []),
    ...(hasNativeCheckin ? ["native_checkin_enabled"] : []),
    ...(hasPatientName ? ["patient_display_name"] : []),
  ]))
    throw unavailable();
  const detail = readDetailFields(value, expectedId);
  if (
    typeof value.can_reschedule !== "boolean" ||
    typeof value.can_checkin !== "boolean" ||
    (hasFixture && typeof value.is_fixture !== "boolean") ||
    (hasNativeCheckin && typeof value.native_checkin_enabled !== "boolean") ||
    (hasNativeId && (value.is_fixture !== false || !isSafeId(value.native_appointment_id)))
  )
    throw unavailable();
  return {
    ...detail,
    can_reschedule: value.can_reschedule,
    can_checkin: value.can_checkin,
    ...(typeof value.is_fixture === "boolean" ? { is_fixture: value.is_fixture } : {}),
    ...(typeof value.native_checkin_enabled === "boolean"
      ? { native_checkin_enabled: value.native_checkin_enabled }
      : {}),
    ...(typeof value.native_appointment_id === "string"
      ? { native_appointment_id: value.native_appointment_id }
      : {}),
    // Bad optional identity text must not break the existing detail or actions.
    ...(hasPatientName && value.is_fixture === false &&
      isSafePatientDisplayName(value.patient_display_name) &&
      value.patient_display_name !== detail.workspace_patient_id
      ? { patient_display_name: value.patient_display_name }
      : {}),
  };
}

/**
 * Reads the native synthetic appointment allowlist.
 *
 * Only the Frappe envelope is unwrapped; the returned `message` is validated by
 * the feature's appointment parser. 401 is an expired session with
 * no retry and 404 is unsupported. A 403 is reconciled through the
 * authenticated bootstrap so a guest/expired session becomes `AUTH_EXPIRED`
 * while a genuine denial stays `FORBIDDEN`. There is no proxy or fixture
 * fallback.
 */
export async function fetchNativeAppointmentsBody(
  signal: AbortSignal,
): Promise<unknown> {
  const response = await frappeRequest(APPOINTMENTS_METHOD, {
    method: "GET",
    signal,
  });
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 401)
    throw { code: "AUTH_EXPIRED", retryable: false } satisfies AppError;
  if (response.status === 403) throw await classifyNativeForbidden(signal);
  if (response.status === 404)
    throw { code: "UNSUPPORTED", retryable: false } satisfies AppError;
  if (!response.ok) throw mapHttpError(response.status, body);
  if (!isRecord(body) || !isRecord(body.message)) throw unavailable();
  return body.message;
}

/**
 * Reads one versioned native appointment detail through the native method.
 *
 * The only input is the server-owned workspace alias or native appointment
 * docname. The detail contract is native-only: without the native transport the
 * caller must show unsupported, never a proxy or fixture substitute.
 */
export async function fetchNativeAppointmentDetailBody(
  workspaceAppointmentId: string,
  signal: AbortSignal,
): Promise<unknown> {
  if (
    typeof workspaceAppointmentId !== "string" ||
    workspaceAppointmentId.trim() === ""
  )
    throw { code: "VALIDATION", retryable: false } satisfies AppError;

  const query = new URLSearchParams({
    workspace_appointment_id: workspaceAppointmentId,
  });
  const response = await frappeRequest(`${VIEW_METHOD}?${query.toString()}`, {
    method: "GET",
    signal,
  });
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 401)
    throw { code: "AUTH_EXPIRED", retryable: false } satisfies AppError;
  if (response.status === 403) throw await classifyNativeForbidden(signal);
  if (response.status === 404)
    throw { code: "UNSUPPORTED", retryable: false } satisfies AppError;
  if (!response.ok) throw mapHttpError(response.status, body);
  if (!isRecord(body) || !isRecord(body.message)) throw unavailable();
  return body.message;
}
