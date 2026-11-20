import { type CapabilityMap, isCapability } from "../../domain/capability";
import type { AppError } from "../../domain/errors";
import { isSessionDTO, type SessionDTO } from "../../domain/session";
import { mapHttpError } from "../error-map";
import { FRAPPE_CSRF_HEADER, frappeRequest } from "./frappe";

const BOOTSTRAP_METHOD = "clinic_ui.api.session.bootstrap";
const LOGIN_METHOD = "login";
const LOGOUT_METHOD = "logout";

/**
 * Session CSRF token, memory only.
 *
 * It is never written to storage, cookies, URLs or logs. It is committed only
 * for the newest accepted bootstrap response, and dropped as soon as the server
 * reports no session or rejects it, so a stale token can never be reused.
 */
let csrfToken: string | null = null;

/**
 * Monotonic bootstrap generation. A delayed response from an earlier
 * generation may still be returned to its caller (which owns its own
 * request-generation check), but it never commits the module token again.
 */
let bootstrapGeneration = 0;

/** Identity of the last accepted session; part of the token commit decision. */
let acceptedIdentity: string | null = null;

/** Exposed for callers that must not reuse a token the server rejected. */
export function nativeCsrfToken(): string | null {
  return csrfToken;
}

/**
 * Invalidates the memory token and its identity.
 *
 * The bootstrap generation is advanced too, so a bootstrap that is already in
 * flight can never reinstall credentials the app has just rejected or ended
 * (logout, CSRF rejection, a rejected or malformed newest bootstrap).
 */
export function clearNativeCsrfToken(): void {
  csrfToken = null;
  acceptedIdentity = null;
  bootstrapGeneration += 1;
}

function unavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSafeToken(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim() !== "" &&
    value.length <= 256 &&
    ![...value].some((char) => {
      const code = char.charCodeAt(0);
      return code < 32 || code === 127;
    })
  );
}

function identityOf(session: SessionDTO): string {
  return [
    session.sessionEpoch,
    session.mode ?? "fixture",
    session.user.id,
    session.user.roles.join("|"),
  ].join(":");
}

/**
 * Only the reviewed native write capabilities can be enabled by bootstrap:
 * patient create/update, appointment book/reschedule/checkin, and the
 * deployment-gated writes from the full write-contract wave (appointment
 * no-show, encounter amend, invoice void/amend, stock consume). Every other
 * known capability stays read-only, so cancellation can never be enabled by a
 * malformed or substituted session payload. Unknown keys are dropped and
 * malformed values never enable a write.
 */
const NATIVE_ENABLEABLE = new Set<string>([
  "write.patient.create",
  "write.patient.update",
  "write.appointment.book",
  "write.appointment.reschedule",
  "write.appointment.cancel",
  "write.appointment.checkin",
  "write.appointment.no_show",
  "write.encounter.draft",
  "write.encounter.complete",
  "write.encounter.amend",
  "write.medication.order",
  "write.lab.order",
  "write.procedure.complete",
  "write.procedure.create",
  "write.invoice.void_amend",
  "write.stock.consume",
  "write.stock.receive",
  "write.stock.transfer",
  "write.stock.adjust",
  "write.purchase_order.draft",
  "write.purchase_order.submit",
  "write.invoice.submit",
  "write.payment.receive",
  "write.admin.user",
  "write.admin.master",
]);

function nativeCapabilities(value: unknown): CapabilityMap {
  const capabilities: CapabilityMap = {};
  if (!isRecord(value)) return capabilities;
  for (const name of Object.keys(value)) {
    if (isCapability(name))
      capabilities[name] =
        NATIVE_ENABLEABLE.has(name) && value[name] === "enabled"
          ? "enabled"
          : "read_only";
  }
  return capabilities;
}

/**
 * Narrows a bootstrap body to a session and the token it authorises. Nothing is
 * committed here: validation must succeed before any module state changes.
 */
function parseBootstrap(body: unknown): {
  session: SessionDTO;
  csrfToken: string;
} {
  if (!isRecord(body) || !isRecord(body.message)) throw unavailable();
  const message = body.message;
  const { csrfToken, paymentSettlement: marker } = message;

  // Existing validator: roles/epoch/capability shape are unchanged here.
  if (!isSessionDTO(message)) throw unavailable();
  if (message.mode !== "live") throw unavailable();
  if (!isSafeToken(csrfToken)) throw unavailable();

  return {
    session: {
      mode: "live",
      sessionEpoch: message.sessionEpoch,
      user: message.user,
      capabilities: nativeCapabilities(message.capabilities),
      ...(marker === "full_balance_v1" ? { paymentSettlement: marker } : {}),
    },
    csrfToken,
  };
}

/**
 * Commits a bootstrap result only when it belongs to the newest generation.
 * A superseded response (an older request resolving late) therefore cannot
 * replace the token or identity of the session the app currently accepts.
 */
function commitBootstrap(
  call: number,
  result: { session: SessionDTO; csrfToken: string } | null,
): void {
  if (call !== bootstrapGeneration) return;
  if (result === null) {
    clearNativeCsrfToken();
    return;
  }
  csrfToken = result.csrfToken;
  acceptedIdentity = identityOf(result.session);
}

/** Reads and validates the bootstrap response without touching module state. */
async function loadBootstrap(): Promise<{
  session: SessionDTO;
  csrfToken: string;
} | null> {
  const response = await frappeRequest(BOOTSTRAP_METHOD, { method: "GET" });
  // 401 is the only "no session" signal; 403 stays an error.
  if (response.status === 401) return null;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw mapHttpError(response.status, body);
  return parseBootstrap(body);
}

/**
 * GET the Frappe session. `null` means the server reports no session (401);
 * every other failure is an error state, never an anonymous session.
 */
export async function fetchNativeSession(): Promise<SessionDTO | null> {
  return fetchValidatedNativeSession(false);
}

async function fetchValidatedNativeSession(requireClinicIdentity: boolean): Promise<SessionDTO | null> {
  const call = ++bootstrapGeneration;
  let result: { session: SessionDTO; csrfToken: string } | null;
  try {
    result = await loadBootstrap();
    if (
      requireClinicIdentity &&
      result !== null &&
      (!result.session.user.id.trim() ||
        !result.session.user.displayName.trim() ||
        result.session.user.roles.length !== 1)
    ) throw unavailable();
  } catch (cause) {
    // A rejected or malformed newest bootstrap leaves no verified session, so
    // the earlier accepted credential must not be kept. A superseded call that
    // fails late may not clear the newer accepted identity.
    if (call === bootstrapGeneration) clearNativeCsrfToken();
    throw cause;
  }
  commitBootstrap(call, result);
  return result?.session ?? null;
}

/**
 * Frappe login. Guest login carries no session CSRF token; the session itself
 * (and its token) is read back from bootstrap, so the returned shape matches
 * the proxy transport exactly.
 */
export async function nativeLogin(
  usr: string,
  pwd: string,
): Promise<SessionDTO> {
  const response = await frappeRequest(LOGIN_METHOD, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ usr, pwd }),
  });
  const body: unknown = await response.json().catch(() => null);
  // A rejected credential pair stays the existing client outcome (FORBIDDEN),
  // not "session expired".
  if (response.status === 401) throw { code: "FORBIDDEN", retryable: false };
  if (!response.ok) throw mapHttpError(response.status, body);

  if (!isRecord(body) || (body.message !== "Logged In" && body.message !== "No App"))
    throw unavailable();

  const session = await fetchValidatedNativeSession(body.message === "No App");
  if (session === null) throw unavailable();
  return session;
}

/**
 * Classification-only bootstrap read.
 *
 * This never commits `csrfToken`, `acceptedIdentity` or `bootstrapGeneration`,
 * so it cannot supersede an in-flight identity refresh, logout token lookup or
 * provider state. The caller-supplied signal is the original read signal, so a
 * cancelled query also cancels this probe. Only a boolean-like state leaves
 * this function; no session, token or record data is returned.
 */
async function probeNativeSession(
  signal: AbortSignal,
): Promise<"authenticated" | "anonymous"> {
  const response = await frappeRequest(BOOTSTRAP_METHOD, {
    method: "GET",
    signal,
  });
  if (response.status === 401) return "anonymous";
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw mapHttpError(response.status, body);
  // Validate exactly like a real bootstrap, then discard the result. A
  // malformed or non-live body is an upstream failure, never a silent denial.
  parseBootstrap(body);
  return "authenticated";
}

/**
 * Reconciles a native read 403 that may have been produced by Frappe's
 * whitelist before the endpoint body ran.
 *
 * A guest/expired session receives 403 from a non-guest whitelisted method, so
 * a raw 403 cannot be treated as a permanent denial. The classification-only
 * bootstrap probe dispatches guests explicitly (401) and returns an
 * authenticated state only after a valid session check. A returned
 * authenticated state means the 403 was a real authorization denial. A probe
 * failure is reported as an upstream error so an uncertain session is never
 * misclassified as forbidden. No proxy request is made and no credential or
 * generation state is touched.
 */
export async function classifyNativeForbidden(
  signal: AbortSignal,
): Promise<AppError> {
  const state = await probeNativeSession(signal);
  if (state === "anonymous")
    return { code: "AUTH_EXPIRED", retryable: false };
  return { code: "FORBIDDEN", retryable: false };
}

/** Frappe reports a rejected session CSRF token in the error envelope. */
function isCsrfRejection(body: unknown): boolean {
  return isRecord(body) && body.exc_type === "CSRFTokenError";
}

type TokenLookup =
  | { kind: "token"; token: string; generation: number }
  | { kind: "none" }
  | { kind: "superseded" };

/**
 * Token for the logout POST.
 *
 * A memory token is only usable while it is bound to the accepted session
 * identity. When memory is empty (fresh page) the session is read exactly once,
 * and a superseded read is reported instead of posting with a token that may
 * belong to another session.
 */
async function sessionToken(): Promise<TokenLookup> {
  if (csrfToken !== null && acceptedIdentity !== null)
    return {
      kind: "token",
      token: csrfToken,
      generation: bootstrapGeneration,
    };

  const call = ++bootstrapGeneration;
  const result = await loadBootstrap();
  if (call !== bootstrapGeneration) return { kind: "superseded" };
  commitBootstrap(call, result);
  if (result === null) return { kind: "none" };
  return {
    kind: "token",
    token: result.csrfToken,
    generation: bootstrapGeneration,
  };
}

/**
 * Native logout.
 *
 * Frappe validates the session CSRF token on this POST, so exactly one attempt
 * is made with the accepted session token. There is no retry: a rejected token
 * is cleared (never reused) and an uncertain network outcome leaves the token
 * intact for the caller to decide.
 */
export async function nativeLogout(): Promise<void> {
  const current = await sessionToken();
  // No session upstream: nothing to end, and no request to make.
  if (current.kind === "none") return;
  if (current.kind === "superseded") throw unavailable();

  // The lookup itself awaits, so credentials can be invalidated or replaced
  // while it resolves. Re-check the bound generation and the exact token
  // immediately before the request: a superseded token is never spent and no
  // POST is sent. This is not a success: the caller must see that the logout
  // was not performed, so it can reconcile against the live session.
  if (current.generation !== bootstrapGeneration || csrfToken !== current.token)
    throw unavailable();

  const response = await frappeRequest(LOGOUT_METHOD, {
    method: "POST",
    headers: {
      [FRAPPE_CSRF_HEADER]: current.token,
      "content-type": "application/json",
    },
  });
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 401 || response.status === 403) {
    // A rejected token must never be retried; the next attempt re-reads it.
    clearNativeCsrfToken();
    throw mapHttpError(response.status, body);
  }
  if (isCsrfRejection(body)) {
    // Frappe answers 400 with a CSRFTokenError envelope for a stale token.
    clearNativeCsrfToken();
    throw { code: "FORBIDDEN", retryable: false } satisfies AppError;
  }
  if (!response.ok) throw mapHttpError(response.status, body);
  clearNativeCsrfToken();
}
