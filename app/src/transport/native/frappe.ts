import type { AppError } from "../../domain/errors";
import type { RoleCode } from "../../domain/capability";

/** Frappe's session CSRF header. The value is never persisted or logged. */
export const FRAPPE_CSRF_HEADER = "X-Frappe-CSRF-Token";

let clinicContext: { workspace: RoleCode; sessionEpoch: string } | null | undefined;
let onClinicForbidden: (() => void) | undefined;

/** Null blocks clinic RPCs while the provider is verifying the workspace. */
export function setClinicRequestContext(context: typeof clinicContext): void {
  clinicContext = context;
}

export function setClinicForbiddenHandler(handler: typeof onClinicForbidden): void {
  onClinicForbidden = handler;
}

/** Bind deferred operations to the context that started them. */
export function captureClinicRequestContext(): () => boolean {
  const captured = clinicContext;
  return () => captured === clinicContext;
}

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
  const name = method.split("?", 1)[0];
  const scoped = name !== "clinic_ui.api.session.bootstrap" &&
    (method.startsWith("clinic_ui.api.") || method.startsWith("clinic_photos.api."));
  const context = scoped ? clinicContext : undefined;
  if (context === null) throw upstreamUnavailable();
  const headers = { accept: "application/json", ...init.headers };
  try {
    const response = await fetch(`/api/method/${method}`, {
      ...init,
      ...(scoped ? { mode: "same-origin", redirect: "error" } : {}),
      credentials: "same-origin",
      cache: "no-store",
      headers: context ? {
        ...headers,
        "X-Clinic-Workspace": context.workspace,
        "X-Clinic-Session-Epoch": context.sessionEpoch,
      } : headers,
    });
    if (!scoped) return response;
    const assertCurrent = () => {
      if (context !== clinicContext) throw upstreamUnavailable();
    };
    assertCurrent();
    if (context && response.status === 403) onClinicForbidden?.();
    // Headers can resolve before the body. Suppress both kinds of late results
    // without replaying or claiming an already-dispatched write did not happen.
    return new Proxy(response, {
      get(target, key) {
        const value: unknown = Reflect.get(target, key, target);
        if (typeof value !== "function") return value;
        if (key === "json" || key === "text" || key === "blob" || key === "arrayBuffer") {
          return async () => {
            assertCurrent();
            const body: unknown = await value.call(target);
            assertCurrent();
            return body;
          };
        }
        return value.bind(target);
      },
    });
  } catch (cause) {
    if (isAbort(cause)) throw cause;
    throw upstreamUnavailable();
  }
}
