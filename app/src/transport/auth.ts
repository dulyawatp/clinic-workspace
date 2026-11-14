import type { AppError } from "../domain/errors";
import { isSessionDTO, type SessionDTO } from "../domain/session";
import { mapHttpError } from "./error-map";
import { nativeTransportEnabled } from "./native/flag";
import { assertLegacyRoute } from "./native/legacy";
import { nativeLogin, nativeLogout } from "./native/session";

export const CSRF_HEADER = "x-clinic-csrf";

function unavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

async function authRequest(path: string, init: RequestInit): Promise<Response> {
  // The proxy auth routes do not exist in the native build.
  assertLegacyRoute();
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw unavailable();
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw mapHttpError(response.status, body);
  }
  return response;
}

/** Token stays local to each handshake; never cached or persisted. */
export async function fetchCsrf(): Promise<{ token: string }> {
  const response = await authRequest("/api/auth/csrf", {
    method: "GET",
    headers: { accept: "application/json" },
  });
  const body: unknown = await response.json().catch(() => null);
  if (
    typeof body !== "object" ||
    body === null ||
    !("token" in body) ||
    typeof body.token !== "string" ||
    body.token.trim() === ""
  )
    throw unavailable();
  return { token: body.token };
}

export async function postLogin(usr: string, pwd: string): Promise<SessionDTO> {
  if (nativeTransportEnabled()) return nativeLogin(usr, pwd);
  const { token } = await fetchCsrf();
  const response = await authRequest("/api/auth/login", {
    method: "POST",
    headers: {
      [CSRF_HEADER]: token,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({ usr, pwd }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!isSessionDTO(body)) throw unavailable();
  return body;
}

/** Caller refreshes SessionProvider after success; no local identity changes. */
export async function postLogout(): Promise<void> {
  if (nativeTransportEnabled()) return nativeLogout();
  const { token } = await fetchCsrf();
  await authRequest("/api/auth/logout", {
    method: "POST",
    headers: { [CSRF_HEADER]: token, "content-type": "application/json" },
  });
}
