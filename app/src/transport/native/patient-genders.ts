import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import { frappeRequest } from "./frappe";
import { classifyNativeForbidden } from "./session";

const GENDERS_METHOD = "clinic_ui.api.patients.genders";

function unavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The exact sex values the authenticated user may write on this site.
 *
 * Frappe wraps the payload as `{message: {items: string[]}}`; every item must
 * be a non-empty string and the response is otherwise refused. Values are
 * returned verbatim (never trimmed or renamed) because the server rejects a
 * value it did not offer. An empty list is a valid response: callers must still
 * refuse to submit, so this function does not invent options.
 */
export function parseNativePatientGenders(body: unknown): string[] {
  if (!isRecord(body) || !isRecord(body.message)) throw unavailable();
  const { items } = body.message;
  if (body.message.items === undefined || !Array.isArray(items))
    throw unavailable();
  if (!items.every((item) => typeof item === "string" && item.trim() !== ""))
    throw unavailable();
  return items as string[];
}

/**
 * Reads the permission-aware sex list from the native method.
 *
 * A 401 is an expired session with no retry, a 403 is reconciled through the
 * authenticated bootstrap so a guest/expired session becomes `AUTH_EXPIRED`
 * while a genuine denial stays `FORBIDDEN`, and a 404 is unsupported. Any
 * other failure is a read error: the caller must not submit without the list.
 */
export async function fetchNativePatientGenders(
  signal: AbortSignal,
): Promise<string[]> {
  const response = await frappeRequest(GENDERS_METHOD, {
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
  return parseNativePatientGenders(body);
}
