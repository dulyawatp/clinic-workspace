import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import { frappeRequest } from "./frappe";
import { classifyNativeForbidden } from "./session";

const DIRECTORY_METHOD = "clinic_ui.api.directory.synthetic_patients";

function unavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Reads the native synthetic-patient directory.
 *
 * Only the Frappe envelope is unwrapped; the returned `message` is validated by
 * the feature's native directory validator, which permits only the public row
 * fields. 401 is an expired session with no retry and 404 is unsupported. A 403
 * is reconciled through the authenticated bootstrap so a guest/expired session
 * becomes `AUTH_EXPIRED` while a genuine denial stays `FORBIDDEN`.
 */
export async function fetchNativeDirectoryBody(
  signal: AbortSignal,
): Promise<unknown> {
  const response = await frappeRequest(DIRECTORY_METHOD, {
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
