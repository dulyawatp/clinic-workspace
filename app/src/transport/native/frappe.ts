import type { AppError } from "../../domain/errors";

/** Frappe's session CSRF header. The value is never persisted or logged. */
export const FRAPPE_CSRF_HEADER = "X-Frappe-CSRF-Token";

function upstreamUnavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

/** A cancelled caller signal is not an upstream failure. */
function isAbort(cause: unknown): boolean {
  return (
    typeof cause === "object" &&
    cause !== null &&
    "name" in cause &&
    (cause as { name?: unknown }).name === "AbortError"
  );
}

/**
 * Single same-origin call to a Frappe `/api/method/*` endpoint.
 *
 * Only transport failure is mapped here; status mapping stays with each caller
 * because a 401 means "no session" for bootstrap but "rejected credentials" for
 * login. Every request is same-origin, uncached and never retried.
 */
export async function frappeRequest(
  method: string,
  init: RequestInit = {},
): Promise<Response> {
  try {
    return await fetch(`/api/method/${method}`, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json", ...init.headers },
    });
  } catch (cause) {
    if (isAbort(cause)) throw cause;
    throw upstreamUnavailable();
  }
}
