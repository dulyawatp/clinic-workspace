import type { LiveUpstream } from "../config.js";
import type { NativeCredentials } from "../session/native-store.js";

const PATIENT_PATH_PREFIX = "/api/resource/Patient/";
const LOGGED_USER_PATH = "/api/method/frappe.auth.get_logged_user";
const MAX_RESPONSE_BYTES = 128 * 1024;

export type NativeSessionState = "alive" | "expired" | "unknown";

/**
 * Read-only proof that the clinician's native session still resolves to a real
 * user. Used when a 403 must be distinguished from an expired session; a GET is
 * used because Frappe CSRF checks apply only to unsafe methods.
 */
export async function probeNativeSession(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
): Promise<NativeSessionState> {
  let response: Response;
  try {
    response = await fetchImpl(`${upstream.origin}${LOGGED_USER_PATH}`, {
      method: "GET",
      headers: {
        accept: "application/json",
        cookie: `sid=${native.sid}`,
      },
      redirect: "manual",
      signal: AbortSignal.timeout(upstream.timeoutMs),
    });
  } catch {
    return "unknown";
  }

  if (response.status === 401 || response.status === 403) {
    return "expired";
  }
  if (response.status !== 200) {
    return "unknown";
  }

  try {
    const body = await readJson(response);
    const user =
      typeof body === "object" && body !== null
        ? (body as Record<string, unknown>).message
        : undefined;
    if (user === "Guest" || user === "Administrator") {
      return "expired";
    }
    return typeof user === "string" && user.trim() !== "" ? "alive" : "expired";
  } catch {
    return "unknown";
  }
}

export type PatientAuthority =
  | { state: "authorized"; nativePatientId: string; displayName: string }
  | { state: "denied" }
  | { state: "expired" }
  | { state: "unavailable" };

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > MAX_RESPONSE_BYTES) {
    throw new Error("UPSTREAM_RESPONSE_TOO_LARGE");
  }
  return JSON.parse(text);
}

/**
 * Reads exactly one native Patient as the authenticated clinician. The native
 * name must be the server-owned mapping value; the client never supplies it as
 * a URL segment. Only a 200 row whose `name` matches that value is authorized.
 */
export async function readAuthorizedPatient(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  nativePatientId: string,
): Promise<PatientAuthority> {
  let response: Response;
  try {
    response = await fetchImpl(
      `${upstream.origin}${PATIENT_PATH_PREFIX}${encodeURIComponent(nativePatientId)}`,
      {
        method: "GET",
        headers: {
          accept: "application/json",
          cookie: `sid=${native.sid}`,
        },
        redirect: "manual",
        signal: AbortSignal.timeout(upstream.timeoutMs),
      },
    );
  } catch {
    return { state: "unavailable" };
  }

  if (response.status === 401) return { state: "expired" };
  if (response.status === 403 || response.status === 404) {
    // A denied Patient read can also be an expired native session. Distinguish
    // with a read-only session probe; when in doubt keep the denial.
    const session = await probeNativeSession(upstream, fetchImpl, native);
    return session === "expired" ? { state: "expired" } : { state: "denied" };
  }
  if (response.status !== 200) return { state: "unavailable" };

  try {
    const body = await readJson(response);
    const data =
      typeof body === "object" && body !== null
        ? (body as Record<string, unknown>).data
        : undefined;
    if (
      typeof data !== "object" ||
      data === null ||
      (data as Record<string, unknown>).name !== nativePatientId
    ) {
      return { state: "unavailable" };
    }

    const rawName = (data as Record<string, unknown>).patient_name;
    const displayName =
      typeof rawName === "string" && rawName.trim() !== ""
        ? rawName
        : nativePatientId;
    return {
      state: "authorized",
      nativePatientId,
      displayName,
    };
  } catch {
    return { state: "unavailable" };
  }
}
