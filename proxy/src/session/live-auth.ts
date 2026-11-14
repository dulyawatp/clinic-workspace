import type { LiveUpstream } from "../config.js";
import type { Authenticate, AuthenticatedUser } from "../routes/auth.js";
import { isNativeCredentials, type NativeCredentials } from "./native-store.js";
import type { SessionUser } from "./store.js";

const LOGIN_PATH = "/api/method/login";
const LOGGED_USER_PATH = "/api/method/frappe.auth.get_logged_user";
const ROLES_PATH =
  "/api/method/frappe.core.doctype.user.user.get_current_user_roles";
// The desk page is the native renderer that calls `frappe.sessions.get_csrf_token`
// and embeds the session token as `frappe.csrf_token`. `get_csrf_token` itself is
// not whitelisted, so there is no API-only token endpoint to call. Frappe's
// website rules redirect `/app` to `/desk`, so only the final 200 desk page counts.
const DESK_PATH = "/desk";

const MAX_RESPONSE_BYTES = 64 * 1024;
const MAX_DESK_BYTES = 2 * 1024 * 1024;
const CSRF_ASSIGNMENT = /frappe\.csrf_token\s*=\s*"([^"]{1,256})"/;
const CSRF_TOKEN = /^[A-Za-z0-9+/=_-]{16,256}$/;

/** Fixed-message upstream failure; never carries a URL, header or body text. */
export class NativeUpstreamUnavailable extends Error {
  constructor() {
    super("NATIVE_UPSTREAM_UNAVAILABLE");
  }
}

interface NativeJson {
  message?: unknown;
  full_name?: unknown;
}

async function readJson(response: Response): Promise<unknown> {
  let text: string;
  try {
    text = await response.text();
  } catch {
    throw new NativeUpstreamUnavailable();
  }
  if (Buffer.byteLength(text, "utf8") > MAX_RESPONSE_BYTES) {
    throw new NativeUpstreamUnavailable();
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new NativeUpstreamUnavailable();
  }
}

function sidFromCookies(response: Response): string | null {
  const header = response.headers as Headers & {
    getSetCookie?: () => string[];
  };
  const cookies =
    typeof header.getSetCookie === "function" ? header.getSetCookie() : [];
  for (const raw of cookies) {
    const pair = raw.split(";")[0] ?? "";
    const separator = pair.indexOf("=");
    if (separator <= 0) continue;
    if (pair.slice(0, separator).trim() !== "sid") continue;
    const value = pair.slice(separator + 1).trim();
    if (
      value === "" ||
      value === "Guest" ||
      !/^[\x21-\x7e]{1,1024}$/.test(value) ||
      /[;,]/.test(value)
    ) {
      return null;
    }
    return value;
  }
  return null;
}

async function postJson(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  path: string,
  body: Record<string, string>,
  sid?: string,
): Promise<Response> {
  const headers: Record<string, string> = {
    accept: "application/json",
    "content-type": "application/json",
  };
  if (sid !== undefined) {
    headers.cookie = `sid=${sid}`;
  }

  try {
    return await fetchImpl(`${upstream.origin}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      redirect: "manual",
      signal: AbortSignal.timeout(upstream.timeoutMs),
    });
  } catch {
    throw new NativeUpstreamUnavailable();
  }
}

async function readDeskToken(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  sid: string,
): Promise<string> {
  let response: Response;
  try {
    response = await fetchImpl(`${upstream.origin}${DESK_PATH}`, {
      method: "GET",
      headers: {
        accept: "text/html",
        cookie: `sid=${sid}`,
      },
      redirect: "manual",
      signal: AbortSignal.timeout(upstream.timeoutMs),
    });
  } catch {
    throw new NativeUpstreamUnavailable();
  }

  if (response.status !== 200) {
    throw new NativeUpstreamUnavailable();
  }

  let html: string;
  try {
    html = await response.text();
  } catch {
    throw new NativeUpstreamUnavailable();
  }
  if (Buffer.byteLength(html, "utf8") > MAX_DESK_BYTES) {
    throw new NativeUpstreamUnavailable();
  }

  const match = CSRF_ASSIGNMENT.exec(html);
  const token = match?.[1];
  if (token === undefined || !CSRF_TOKEN.test(token)) {
    throw new NativeUpstreamUnavailable();
  }
  return token;
}

/**
 * Live lab authentication: establishes the clinician's own native Frappe
 * session and stores its `sid`/CSRF token server-side. No shared service
 * account, API token or `Authorization` header is used, and no response value
 * is ever returned to the browser. Only configured native clinician roles grant
 * the closed `DOC` proxy role; the native endpoint remains the authorization
 * authority.
 */
export function createLiveAuthenticate(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch = fetch,
): Authenticate {
  return async ({ usr, pwd }): Promise<AuthenticatedUser | null> => {
    const login = await postJson(upstream, fetchImpl, LOGIN_PATH, {
      usr,
      pwd,
    });
    if (login.status === 401 || login.status === 403) {
      return null;
    }
    if (login.status !== 200) {
      throw new NativeUpstreamUnavailable();
    }

    const sid = sidFromCookies(login);
    if (sid === null) {
      throw new NativeUpstreamUnavailable();
    }

    const loginBody = (await readJson(login)) as NativeJson;
    if (typeof loginBody !== "object" || loginBody === null) {
      throw new NativeUpstreamUnavailable();
    }

    // Canonical user identity from the session itself, before any CSRF token
    // exists, so the native CSRF guard cannot require a header we do not have.
    const identity = await postJson(
      upstream,
      fetchImpl,
      LOGGED_USER_PATH,
      {},
      sid,
    );
    if (!identity.ok) {
      throw new NativeUpstreamUnavailable();
    }
    const identityBody = (await readJson(identity)) as NativeJson;
    if (
      typeof identityBody !== "object" ||
      identityBody === null ||
      typeof identityBody.message !== "string" ||
      identityBody.message.trim() === "" ||
      identityBody.message === "Guest" ||
      identityBody.message === "Administrator"
    ) {
      throw new NativeUpstreamUnavailable();
    }
    const userId = identityBody.message;

    // Native roles come from Frappe itself, never from the browser. No
    // configured clinician role means no live session.
    const rolesResponse = await postJson(
      upstream,
      fetchImpl,
      ROLES_PATH,
      {},
      sid,
    );
    if (!rolesResponse.ok) {
      throw new NativeUpstreamUnavailable();
    }
    const rolesBody = (await readJson(rolesResponse)) as NativeJson;
    if (
      typeof rolesBody !== "object" ||
      rolesBody === null ||
      !Array.isArray(rolesBody.message) ||
      rolesBody.message.some((role) => typeof role !== "string")
    ) {
      throw new NativeUpstreamUnavailable();
    }
    const nativeRoles = rolesBody.message as string[];
    if (nativeRoles.includes("Administrator")) {
      return null;
    }
    const isReception = nativeRoles.some((role) =>
      upstream.receptionRoles?.has(role),
    );
    const isPharmacy = nativeRoles.some((role) =>
      upstream.pharmacyRoles?.has(role),
    );
    const isManager = nativeRoles.some((role) =>
      upstream.managerRoles?.has(role),
    );
    const isNurse = nativeRoles.some((role) => upstream.nurseRoles?.has(role));
    const isCashier = nativeRoles.some((role) =>
      upstream.cashierRoles?.has(role),
    );
    const isAdmin = nativeRoles.some((role) => upstream.adminRoles?.has(role));
    if (
      [isReception, isPharmacy, isManager, isNurse, isCashier, isAdmin].filter(
        Boolean,
      ).length > 1
    )
      return null;
    if (
      !isReception &&
      !isPharmacy &&
      !isManager &&
      !isNurse &&
      !isCashier &&
      !isAdmin &&
      !nativeRoles.some((role) => upstream.clinicianRoles.has(role))
    ) {
      return null;
    }

    const csrf = await readDeskToken(upstream, fetchImpl, sid);

    const native: NativeCredentials = {
      sid,
      csrf,
    };
    if (!isNativeCredentials(native)) {
      throw new NativeUpstreamUnavailable();
    }

    const fullName =
      typeof loginBody.full_name === "string" &&
      loginBody.full_name.trim() !== ""
        ? loginBody.full_name
        : userId;
    const user: SessionUser = {
      id: userId,
      displayName: fullName,
      roles: [
        isReception
          ? "REC"
          : isPharmacy
            ? "PHA"
            : isManager
              ? "MGR"
              : isNurse
                ? "NUR"
                : isCashier
                  ? "CAS"
                  : isAdmin
                    ? "ADM"
                    : "DOC",
      ],
    };

    return { user, native };
  };
}
