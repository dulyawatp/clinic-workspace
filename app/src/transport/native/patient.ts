import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import { frappeRequest } from "./frappe";
import { classifyNativeForbidden } from "./session";

const VIEW_METHOD = "clinic_ui.api.patients.view";

function unavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Reads one allowlisted patient detail through the native Frappe method.
 *
 * The only input is the server-owned workspace alias; a native docname is never
 * accepted by the server and a blank alias is refused before any request. The
 * Frappe envelope is unwrapped and the `message` is validated by the feature
 * parser, exactly like the directory transport. 401 is an expired session with
 * no retry and 404 is unsupported. A 403 is reconciled through the
 * authenticated bootstrap so a guest/expired session becomes `AUTH_EXPIRED`
 * while a genuine denial stays `FORBIDDEN`.
 */
export async function fetchNativePatientDetailBody(
  workspacePatientId: string,
  signal: AbortSignal,
): Promise<unknown> {
  if (
    typeof workspacePatientId !== "string" ||
    workspacePatientId.trim() === ""
  )
    throw { code: "VALIDATION", retryable: false } satisfies AppError;

  const query = new URLSearchParams({
    workspace_patient_id: workspacePatientId,
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
