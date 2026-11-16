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
    !isSafeId(value.workspace_patient_id) ||
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

function parseItem(value: unknown): NativeAppointmentItem {
  if (!isRecord(value) || !hasExactKeys(value, ITEM_KEYS))
    throw unavailable();
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

/** The read detail adds only the two server-computed action flags. */
export interface NativeAppointmentRead extends NativeAppointmentDetail {
  can_reschedule: boolean;
  can_checkin: boolean;
}

/**
 * Strict parser for the versioned write-result appointment. Any extra field
 * (such as a native identifier) or a malformed version fails the whole write.
 */
export function parseNativeAppointmentDetail(
  value: unknown,
  expectedId?: string,
): NativeAppointmentDetail {
  if (!isRecord(value) || !hasExactKeys(value, DETAIL_KEYS))
    throw unavailable();
  return readDetailFields(value, expectedId);
}

/** Detail fields shared by the six-field write result and the eight-field read. */
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
 * Strict parser for `appointments.view`. Both flags must be booleans; there is
 * no client-side inference from status or date.
 */
export function parseNativeAppointmentRead(
  value: unknown,
  expectedId?: string,
): NativeAppointmentRead {
  if (!isRecord(value) || !hasExactKeys(value, READ_KEYS))
    throw unavailable();
  const detail = readDetailFields(value, expectedId);
  if (
    typeof value.can_reschedule !== "boolean" ||
    typeof value.can_checkin !== "boolean"
  )
    throw unavailable();
  return {
    ...detail,
    can_reschedule: value.can_reschedule,
    can_checkin: value.can_checkin,
  };
}

/**
 * Reads the native synthetic appointment allowlist.
 *
 * Only the Frappe envelope is unwrapped; the returned `message` is validated by
 * the feature's five-field appointment parser. 401 is an expired session with
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
 * The only input is the server-owned workspace alias. The detail contract is
 * native-only: without the native transport the caller must show unsupported,
 * never a proxy or fixture substitute.
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
