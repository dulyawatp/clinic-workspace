import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import { frappeRequest } from "./frappe";

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
 * the same directory validator the proxy body uses, so row rules stay in one
 * place. Status mapping mirrors the proxy route: 401 is an expired session with
 * no retry, 404 is unsupported, and a 403 is denied.
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
  if (response.status === 404)
    throw { code: "UNSUPPORTED", retryable: false } satisfies AppError;
  if (!response.ok) throw mapHttpError(response.status, body);
  if (!isRecord(body) || !isRecord(body.message)) throw unavailable();
  return body.message;
}
