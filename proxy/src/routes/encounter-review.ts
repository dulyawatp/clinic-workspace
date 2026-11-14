import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { z } from "zod";
import {
  type AppConfig,
  isSafeNativePatientName,
  type LiveUpstream,
} from "../config.js";
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  SESSION_COOKIE,
} from "../security/constants.js";
import type { CsrfStore } from "../security/csrf-store.js";
import { assertTrustedOrigin } from "../security/origin.js";
import type {
  NativeCredentialStore,
  NativeCredentials,
} from "../session/native-store.js";
import type { SessionStore } from "../session/store.js";
import type { EncounterDraftStore } from "../writes/encounter-draft-store.js";
import {
  type EncounterReviewStore,
  reviewOperationKey,
} from "../writes/encounter-review-store.js";
import { hasJsonContentType, headerValue } from "./auth.js";
import {
  probeNativeSession,
  readAuthorizedPatient,
} from "./patient-authority.js";

/** Exact native `Patient Encounter` record path; the docname is never a client value. */
const ENCOUNTER_PATH_PREFIX = "/api/resource/Patient%20Encounter/";
/**
 * Read-only native value probe, used only to confirm a *null* nullable field
 * that the full resource response omits. Never used to write or to discover new
 * scope: the caller's own session, the same docname and the same `modified` are
 * all required.
 */
const GET_VALUE_PATH = "/api/method/frappe.client.get_value";

const MAX_REQUEST_BYTES = 8 * 1024;
const MAX_UPSTREAM_RESPONSE_BYTES = 128 * 1024;
/** Native `encounter_comment` is a plain long-text field; stay well inside it. */
const MAX_REVIEW_CHARS = 2000;

const REQUEST_KEY = /^[A-Za-z0-9_-]{16,128}$/;
/** Native `modified` as Frappe returns it; compared byte-exact, never reparsed. */
const MODIFIED = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$/;
const CLINICIAN_ROLE = "DOC";

/** Child tables that make a `Patient Encounter` an order document, not a draft. */
const ORDER_TABLES = [
  "drug_prescription",
  "lab_test_prescription",
  "procedure_prescription",
  "therapies",
] as const;

const querySchema = z
  .object({ request_key: z.string().regex(REQUEST_KEY) })
  .strict();

const writeBodySchema = z
  .object({
    request_key: z.string().regex(REQUEST_KEY),
    review_details: z.string().refine(isSafeReviewText),
    modified: z.string().regex(MODIFIED),
  })
  .strict();

interface CheckedWrite {
  sessionId: string;
  userId: string;
  native: NativeCredentials;
}

export interface EncounterReviewDeps {
  config: AppConfig;
  sessions: SessionStore;
  csrf: CsrfStore;
  native: NativeCredentialStore;
  encounterDrafts: EncounterDraftStore;
  encounterReviews: EncounterReviewStore;
  fetchImpl: typeof fetch;
}

/**
 * C0/C1 controls (tab, newline and carriage return excepted), bidi overrides
 * and zero-width marks are refused: the value is rendered as text in a clinical
 * record. Expressed as code-point tests rather than a control-character regex.
 */
function isUnsafeCodePoint(code: number): boolean {
  if (code <= 0x08) return true;
  if (code === 0x0b || code === 0x0c) return true;
  if (code >= 0x0e && code <= 0x1f) return true;
  if (code >= 0x7f && code <= 0x9f) return true;
  if (code === 0x200e || code === 0x200f) return true;
  if (code >= 0x202a && code <= 0x202e) return true;
  return code >= 0x2066 && code <= 0x2069;
}

/** Plain text that can be rendered without control or direction spoofing. */
function isSafeReviewText(value: string): boolean {
  if (value.length > MAX_REVIEW_CHARS) {
    return false;
  }
  // A lone surrogate would be silently replaced on render; refuse it instead.
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (isUnsafeCodePoint(code)) return false;
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function sendCode(reply: FastifyReply, status: number, code: string) {
  return reply.code(status).send({ code });
}

async function readUpstreamJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > MAX_UPSTREAM_RESPONSE_BYTES) {
    throw new Error("UPSTREAM_RESPONSE_TOO_LARGE");
  }
  return JSON.parse(text);
}

interface ReviewRow {
  reviewDetails: string;
  modified: string;
  nativePatientId: string;
}

type EncounterRead =
  | { state: "ok"; row: ReviewRow }
  /** Present, but disqualifying: submitted, linked, ordered or unrenderable. */
  | { state: "unsafe" }
  /**
   * A scope-critical field is absent or of an unexpected type. The installed
   * response schema is not proven, so this record is never treated as an empty
   * draft.
   */
  | { state: "unverified" }
  /**
   * The full response omitted `appointment` (the installed site omits it while
   * null). Every other field passed; the caller must confirm an explicit null
   * with the same session and the same `modified` before this is trusted.
   */
  | { state: "appointment-unconfirmed"; row: ReviewRow }
  | { state: "denied" }
  | { state: "expired" }
  | { state: "unavailable" };

/** A zero-valued flag is only trusted when the field is actually present. */
function readFlag(value: unknown): "zero" | "nonzero" | "unreadable" {
  if (value === 0 || value === "0") return "zero";
  if (typeof value === "number" || typeof value === "string") return "nonzero";
  return "unreadable";
}

/**
 * Reads the draft's own fields. `docstatus`, the appointment link, every
 * prescription/therapy child table and `submit_orders_on_save` are required to
 * be *present and empty*: a missing field is an unproven schema, never an
 * assumed-empty one, so a partially returned document cannot authorize a write.
 */
function parseEncounter(encounterId: string, data: unknown): EncounterRead {
  if (typeof data !== "object" || data === null) {
    return { state: "unavailable" };
  }
  const row = data as Record<string, unknown>;
  if (row.name !== encounterId) {
    return { state: "unavailable" };
  }
  const nativePatientId =
    typeof row.patient === "string" ? row.patient.trim() : "";
  if (!isSafeNativePatientName(nativePatientId)) {
    return { state: "unavailable" };
  }
  const modified = row.modified;
  if (typeof modified !== "string" || !MODIFIED.test(modified)) {
    return { state: "unavailable" };
  }

  const docstatus = readFlag(row.docstatus);
  if (docstatus === "unreadable") return { state: "unverified" };
  if (docstatus !== "zero") return { state: "unsafe" };

  // `appointment` is nullable and the installed full response omits it while it
  // is null. Absence is NOT accepted here: it is handed back as unconfirmed so
  // the caller can confirm an explicit null under this session and version.
  const appointmentOmitted = row.appointment === undefined;
  if (!appointmentOmitted && !isEmptyLink(row.appointment)) {
    return { state: "unsafe" };
  }

  for (const table of ORDER_TABLES) {
    const value = row[table];
    if (!Array.isArray(value)) return { state: "unverified" };
    if (value.length > 0) return { state: "unsafe" };
  }

  const submitFlag = readFlag(row.submit_orders_on_save);
  if (submitFlag === "unreadable") return { state: "unverified" };
  if (submitFlag !== "zero") return { state: "unsafe" };

  const rawComment = row.encounter_comment;
  let reviewDetails: string;
  if (rawComment === undefined || rawComment === null) {
    // The same installed site omits this nullable field while it is null; a
    // missing value is therefore an empty comment, not an unproven schema. Every
    // scope-critical field above is still required and checked first.
    reviewDetails = "";
  } else if (typeof rawComment === "string") {
    reviewDetails = rawComment;
  } else {
    return { state: "unverified" };
  }
  if (!isSafeReviewText(reviewDetails)) return { state: "unsafe" };

  const parsedRow: ReviewRow = { reviewDetails, modified, nativePatientId };
  return appointmentOmitted
    ? { state: "appointment-unconfirmed", row: parsedRow }
    : { state: "ok", row: parsedRow };
}

function isEmptyLink(value: unknown): boolean {
  return value === null || value === "";
}

type AppointmentProbe =
  | { state: "empty" }
  | { state: "linked" }
  | { state: "denied" }
  | { state: "expired" }
  /** Missing method, malformed body, wrong name or a different version. */
  | { state: "unverified" };

/**
 * Confirms an omitted nullable `appointment` with the caller's own session.
 *
 * The response is only trusted when it is the *same* document (`name`), at the
 * *same* version (`modified` byte-equal to the full read) and carries an
 * explicit null/empty appointment. Anything else - linked, missing, malformed,
 * a different version, a refusal - is refused; no default is guessed.
 *
 * Safety does not rest on this method's own permission filter: the document was
 * already read in full under this session, and the probe can never widen scope
 * because only an explicit empty value is accepted.
 */
async function probeAppointment(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  encounterId: string,
  expectedModified: string,
): Promise<AppointmentProbe> {
  const params = new URLSearchParams({
    doctype: "Patient Encounter",
    filters: JSON.stringify({ name: encounterId }),
    // Comma-separated field list: the form the installed `get_value` splits into
    // a field list. A body missing any requested field is refused below, so a
    // semantics mismatch fails closed instead of accepting less proof.
    fieldname: "name,appointment,modified",
  });

  let response: Response;
  try {
    response = await fetchImpl(
      `${upstream.origin}${GET_VALUE_PATH}?${params}`,
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
    return { state: "unverified" };
  }

  if (response.status === 401) return { state: "expired" };
  if (response.status === 403) {
    const session = await probeNativeSession(upstream, fetchImpl, native);
    return session === "expired" ? { state: "expired" } : { state: "denied" };
  }
  if (response.status !== 200) return { state: "unverified" };

  let body: unknown;
  try {
    body = await readUpstreamJson(response);
  } catch {
    return { state: "unverified" };
  }
  const message =
    typeof body === "object" && body !== null
      ? (body as Record<string, unknown>).message
      : undefined;
  if (typeof message !== "object" || message === null) {
    return { state: "unverified" };
  }
  const row = message as Record<string, unknown>;
  if (row.name !== encounterId) return { state: "unverified" };
  if (row.modified !== expectedModified) return { state: "unverified" };

  const appointment = row.appointment;
  if (appointment === null || appointment === "") return { state: "empty" };
  if (typeof appointment === "string" && appointment.trim() !== "") {
    return { state: "linked" };
  }
  // Absent, wrong type or a placeholder value: nothing is assumed.
  return { state: "unverified" };
}

async function readEncounter(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  encounterId: string,
): Promise<EncounterRead> {
  let response: Response;
  try {
    response = await fetchImpl(
      `${upstream.origin}${ENCOUNTER_PATH_PREFIX}${encodeURIComponent(encounterId)}`,
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
    // A refusal can also mean the native session is gone. Probe read-only and
    // keep the denial when in doubt.
    const session = await probeNativeSession(upstream, fetchImpl, native);
    return session === "expired" ? { state: "expired" } : { state: "denied" };
  }
  if (response.status !== 200) return { state: "unavailable" };

  try {
    const body = await readUpstreamJson(response);
    const data =
      typeof body === "object" && body !== null
        ? ((body as Record<string, unknown>).data ??
          (body as Record<string, unknown>).message)
        : undefined;
    const parsed = parseEncounter(encounterId, data);
    if (parsed.state !== "appointment-unconfirmed") {
      return parsed;
    }
    // The nullable appointment was omitted: confirm an explicit null for this
    // exact document at this exact version before the row may be used.
    const probe = await probeAppointment(
      upstream,
      fetchImpl,
      native,
      encounterId,
      parsed.row.modified,
    );
    switch (probe.state) {
      case "empty":
        return { state: "ok", row: parsed.row };
      case "linked":
        return { state: "unsafe" };
      case "denied":
        return { state: "denied" };
      case "expired":
        return { state: "expired" };
      default:
        return { state: "unverified" };
    }
  } catch {
    return { state: "unavailable" };
  }
}

type WriteOutcome =
  | { state: "saved"; modified: string }
  /**
   * Fail closed. Every post-dispatch answer other than a proven 2xx lands here:
   * the intent is kept and the write is never replayed. `expired` still clears
   * the dead native session without releasing the intent.
   */
  | { state: "unknown"; expired?: true };

/**
 * One read-only probe after an ambiguous native answer. It never decides the
 * write outcome: an ambiguous write stays `unknown` and keeps its durable
 * intent whatever this returns. Its only effect is dropping a native session it
 * proves dead.
 *
 * The reason is deliberate: an interpreter between this proxy and the origin can
 * answer 5xx, redirect or time out while the buffered PUT still commits. A read
 * taken during that window says nothing reliable about the write.
 */
async function sessionDiedAfterWrite(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  encounterId: string,
): Promise<boolean> {
  const read = await readEncounter(upstream, fetchImpl, native, encounterId);
  return read.state === "expired";
}

function ambiguousOutcome(expired: boolean): WriteOutcome {
  return expired ? { state: "unknown", expired: true } : { state: "unknown" };
}

/**
 * Classifies the native 2xx body.
 * - `proven`: the body itself proves this write (exact echo, changed version,
 *   fully verified draft scope) — the only case that reports `saved` directly.
 * - `confirm-scope`: the body proves the write (exact echo, changed version) but
 *   omits the nullable appointment, so scope must be confirmed at that exact
 *   version before `saved` may be reported.
 * - `unproven`: anything else. Never inferred from a later read.
 */
type SavedProof =
  | { kind: "proven"; modified: string }
  | { kind: "confirm-scope"; modified: string }
  | { kind: "unproven" };

function savedVersion(
  body: unknown,
  encounterId: string,
  submitted: { details: string; modified: string },
): SavedProof {
  if (typeof body !== "object" || body === null) return { kind: "unproven" };
  const record = body as Record<string, unknown>;
  const data = record.data ?? record.message;
  if (typeof data !== "object" || data === null) return { kind: "unproven" };

  const parsed = parseEncounter(encounterId, data);
  if (parsed.state === "appointment-unconfirmed") {
    // A definite success that describes exactly this write, missing only the
    // nullable link. Anything less than exact proof is not confirmed later.
    return parsed.row.reviewDetails === submitted.details &&
      parsed.row.modified !== submitted.modified
      ? { kind: "confirm-scope", modified: parsed.row.modified }
      : { kind: "unproven" };
  }
  // Submitted, linked, ordered, unrenderable or malformed in a successful
  // response: never reported as saved.
  if (parsed.state !== "ok") return { kind: "unproven" };
  if (parsed.row.reviewDetails !== submitted.details)
    return { kind: "unproven" };
  if (parsed.row.modified === submitted.modified) return { kind: "unproven" };
  return { kind: "proven", modified: parsed.row.modified };
}

/**
 * Confirms the safe draft scope of a write the 2xx body already proved, at the
 * exact version that body reported. This is a scope check only: any failure,
 * any different version or any linked appointment keeps the outcome unknown.
 */
async function confirmScopeAtVersion(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  encounterId: string,
  version: string,
  details: string,
): Promise<WriteOutcome> {
  const read = await readEncounter(upstream, fetchImpl, native, encounterId);
  if (read.state === "expired") return { state: "unknown", expired: true };
  if (read.state !== "ok") return { state: "unknown" };
  if (read.row.modified !== version) return { state: "unknown" };
  if (read.row.reviewDetails !== details) return { state: "unknown" };
  return { state: "saved", modified: read.row.modified };
}

/**
 * Writes `encounter_comment` only. `modified` and `submit_orders_on_save: 0` are
 * the complete body: no orders, no appointment link, no submit.
 *
 * Installed Frappe was reviewed to lock parent and child rows for a REST v1
 * `update_doc`, run `check_if_latest` with the supplied `modified` under that
 * lock and answer 417 on a version mismatch. That guards ordinary parent
 * writes, but not child-level PUTs or `db_set(update_modified=False)`, and it
 * does not authenticate the HTTP answer: a 417 can also come from an
 * intermediary or from another validation failure. Only a *successful* response
 * that describes exactly this write can be reported as saved; everything
 * ambiguous stays unknown.
 */
async function putEncounterComment(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  encounterId: string,
  submitted: { details: string; modified: string },
): Promise<WriteOutcome> {
  let response: Response;
  try {
    response = await fetchImpl(
      `${upstream.origin}${ENCOUNTER_PATH_PREFIX}${encodeURIComponent(encounterId)}`,
      {
        method: "PUT",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
          cookie: `sid=${native.sid}`,
          "x-frappe-csrf-token": native.csrf,
        },
        body: JSON.stringify({
          encounter_comment: submitted.details,
          modified: submitted.modified,
          submit_orders_on_save: 0,
        }),
        redirect: "manual",
        signal: AbortSignal.timeout(upstream.timeoutMs),
      },
    );
  } catch {
    // Transport failure: the request may have committed behind an intermediary.
    // Never retried, never resolved from a read.
    return ambiguousOutcome(
      await sessionDiedAfterWrite(upstream, fetchImpl, native, encounterId),
    );
  }

  if (response.status === 200 || response.status === 201) {
    let body: unknown;
    try {
      body = await readUpstreamJson(response);
    } catch {
      return ambiguousOutcome(
        await sessionDiedAfterWrite(upstream, fetchImpl, native, encounterId),
      );
    }
    const saved = savedVersion(body, encounterId, submitted);
    if (saved.kind === "unproven") {
      // A response that does not describe this write stays unknown: a read
      // cannot prove a write that may still be in flight.
      return ambiguousOutcome(
        await sessionDiedAfterWrite(upstream, fetchImpl, native, encounterId),
      );
    }
    if (saved.kind === "confirm-scope") {
      // The body proved the write but omitted the nullable link; confirm the
      // safe draft scope at exactly that version before reporting saved.
      return confirmScopeAtVersion(
        upstream,
        fetchImpl,
        native,
        encounterId,
        saved.modified,
        submitted.details,
      );
    }
    return { state: "saved", modified: saved.modified };
  }

  switch (response.status) {
    case 401:
      // A native 401 still proves this session is gone, but a refusal after the
      // PUT was dispatched does not prove the write failed: the intent is kept
      // unknown and only the dead session is dropped.
      return { state: "unknown", expired: true };
    case 403: {
      // A refusal can also mean the native session is gone; keep the unknown
      // outcome and drop the session only when a read proves it dead.
      const session = await probeNativeSession(upstream, fetchImpl, native);
      return ambiguousOutcome(session === "expired");
    }
    default:
      // 400, 404, 405, 409, 413, 417, 422, 429, redirects, 5xx and anything
      // unexpected are indistinguishable from an intermediary answer sent while
      // the buffered PUT still commits. Response authenticity is not provable
      // here, so no native status releases the intent or permits a retry.
      return ambiguousOutcome(
        await sessionDiedAfterWrite(upstream, fetchImpl, native, encounterId),
      );
  }
}

/**
 * Proxy-only "Review Details" for the caller's own synthetic standalone
 * encounter drafts. The native field is the plain `encounter_comment`; no
 * Clinical Note is read or written and no native permission is altered.
 *
 * The docname always comes from `EncounterDraftStore` for the *session user*
 * and the presented creation key, never from the client. Each read and write
 * re-authorizes the encounter and its patient with the caller's own native
 * session against the server-owned patient allow-list. Writes additionally take
 * a durable `(user, encounter)` write-intent lock in SQLite before the native
 * PUT, so an unresolved outcome blocks retries from every tab, session and
 * process rather than only from the UI that saw it.
 */
export function registerEncounterReviewRoutes(
  app: FastifyInstance,
  deps: EncounterReviewDeps,
): void {
  const {
    config,
    sessions,
    csrf,
    native,
    encounterDrafts,
    encounterReviews,
    fetchImpl,
  } = deps;
  const upstream = config.upstreamMode === "live" ? config.live : undefined;

  const checkedWrites = new WeakMap<FastifyRequest, CheckedWrite>();

  function invalidateSession(reply: FastifyReply, sessionId: string): void {
    native.clear(sessionId);
    sessions.destroy(sessionId);
    csrf.revokeAll(sessionId);
    reply.clearCookie(SESSION_COOKIE, { path: "/" });
    reply.clearCookie(CSRF_COOKIE, { path: "/" });
  }

  /** Read-side admission: live lab, explicit opt-in, clinician, native session. */
  function readAccess(
    req: FastifyRequest,
    reply: FastifyReply,
  ): { sessionId: string; userId: string; native: NativeCredentials } | null {
    if (upstream === undefined) {
      sendCode(reply, 404, "UNSUPPORTED");
      return null;
    }
    if (config.liveEncounterWrites !== true) {
      sendCode(reply, 403, "WRITE_DISABLED");
      return null;
    }
    const sessionId = req.cookies[SESSION_COOKIE];
    const session =
      sessionId === undefined ? undefined : sessions.get(sessionId);
    if (sessionId === undefined || session === undefined) {
      sendCode(reply, 401, "AUTH_EXPIRED");
      return null;
    }
    if (!session.roles.includes(CLINICIAN_ROLE)) {
      sendCode(reply, 403, "FORBIDDEN");
      return null;
    }
    const credentials = native.get(sessionId);
    if (credentials === undefined) {
      invalidateSession(reply, sessionId);
      sendCode(reply, 401, "AUTH_EXPIRED");
      return null;
    }
    return { sessionId, userId: session.userId, native: credentials };
  }

  /**
   * Runs before body parsing: a browse-only lab, a cross-origin, CSRF-less or
   * non-clinician request never has its payload read and never consumes a CSRF
   * token. Check order matches the encounter draft write route.
   */
  async function writeGuard(
    req: FastifyRequest,
    reply: FastifyReply,
  ): Promise<void> {
    reply.header("cache-control", "no-store");

    if (upstream === undefined) {
      sendCode(reply, 404, "UNSUPPORTED");
      return;
    }
    if (config.liveEncounterWrites !== true) {
      sendCode(reply, 403, "WRITE_DISABLED");
      return;
    }
    if (!assertTrustedOrigin(req, config.appOrigin)) {
      sendCode(reply, 403, "FORBIDDEN");
      return;
    }
    if (!hasJsonContentType(req)) {
      sendCode(reply, 415, "UNSUPPORTED");
      return;
    }

    const sessionId = req.cookies[SESSION_COOKIE];
    const session =
      sessionId === undefined ? undefined : sessions.get(sessionId);
    if (sessionId === undefined || session === undefined) {
      sendCode(reply, 401, "AUTH_EXPIRED");
      return;
    }
    if (!session.roles.includes(CLINICIAN_ROLE)) {
      sendCode(reply, 403, "FORBIDDEN");
      return;
    }

    const token = headerValue(req.headers[CSRF_HEADER]);
    if (token === undefined || !csrf.consume(sessionId, token, "session")) {
      sendCode(reply, 403, "FORBIDDEN");
      return;
    }

    const credentials = native.get(sessionId);
    if (credentials === undefined) {
      invalidateSession(reply, sessionId);
      sendCode(reply, 401, "AUTH_EXPIRED");
      return;
    }

    checkedWrites.set(req, {
      sessionId,
      userId: session.userId,
      native: credentials,
    });
  }

  /** Resolves the caller's own created draft; any other key is not found. */
  function resolveDraft(
    userId: string,
    requestKey: string,
  ): { encounterId: string } | null {
    const record = encounterDrafts.get(userId, requestKey);
    if (record === undefined || record.state !== "created") return null;
    if (record.encounterId === undefined) return null;
    if (!isSafeNativePatientName(record.encounterId)) return null;
    return { encounterId: record.encounterId };
  }

  type Resolved =
    | { state: "ok"; row: ReviewRow; encounterId: string }
    | { state: "expired" }
    | { state: "refused"; status: number; code: string };

  /**
   * Re-authorizes the draft on every request: the native record must exist for
   * the caller, be a *provably* safe standalone draft, belong to an
   * allow-listed synthetic patient, and that patient must still be readable by
   * this session.
   */
  async function authorizeDraft(
    access: { sessionId: string; native: NativeCredentials },
    encounterId: string,
  ): Promise<Resolved> {
    if (upstream === undefined) {
      return { state: "refused", status: 404, code: "UNSUPPORTED" };
    }
    const read = await readEncounter(
      upstream,
      fetchImpl,
      access.native,
      encounterId,
    );
    if (read.state === "expired") {
      return { state: "expired" };
    }
    if (read.state === "denied") {
      return { state: "refused", status: 403, code: "FORBIDDEN" };
    }
    if (read.state === "unverified") {
      // The installed record schema could not be proven: refuse rather than
      // treat absent appointment/order fields as empty.
      return { state: "refused", status: 502, code: "UPSTREAM_UNAVAILABLE" };
    }
    if (read.state === "unavailable") {
      return { state: "refused", status: 502, code: "UPSTREAM_UNAVAILABLE" };
    }
    if (read.state === "unsafe") {
      // Out of scope: this route only ever edits a standalone draft.
      return { state: "refused", status: 409, code: "UNSAFE_ENCOUNTER" };
    }

    const allowListed = [...upstream.syntheticPatients.values()].includes(
      read.row.nativePatientId,
    );
    if (!allowListed) {
      return { state: "refused", status: 403, code: "FORBIDDEN" };
    }

    const authority = await readAuthorizedPatient(
      upstream,
      fetchImpl,
      access.native,
      read.row.nativePatientId,
    );
    if (authority.state === "expired") {
      return { state: "expired" };
    }
    if (authority.state === "denied") {
      return { state: "refused", status: 403, code: "FORBIDDEN" };
    }
    if (authority.state !== "authorized") {
      return { state: "refused", status: 502, code: "UPSTREAM_UNAVAILABLE" };
    }
    return { state: "ok", row: read.row, encounterId };
  }

  app.get("/api/live/encounter-review", async (req, reply) => {
    reply.header("cache-control", "no-store");

    const access = readAccess(req, reply);
    if (access === null) return;

    const parsed = querySchema.safeParse(req.query);
    if (!parsed.success) {
      return sendCode(reply, 400, "VALIDATION");
    }
    const draft = resolveDraft(access.userId, parsed.data.request_key);
    if (draft === null) {
      return sendCode(reply, 404, "NOT_FOUND");
    }

    const resolved = await authorizeDraft(access, draft.encounterId);
    if (resolved.state === "expired") {
      invalidateSession(reply, access.sessionId);
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }
    if (resolved.state === "refused") {
      return sendCode(reply, resolved.status, resolved.code);
    }

    // Read-only view of the durable intent, taken after every authorization and
    // synthetic check. It is never derived from the native document and this
    // handler never begins, resolves, promotes or clears an intent: a pending or
    // unknown write stays locked until something outside this route resolves it.
    const intent = encounterReviews.get(access.userId, draft.encounterId);
    return reply.code(200).send({
      review_details: resolved.row.reviewDetails,
      modified: resolved.row.modified,
      write_state: intent === undefined ? "clear" : intent.state,
    });
  });

  app.post(
    "/api/write/encounter-review",
    { bodyLimit: MAX_REQUEST_BYTES, onRequest: writeGuard },
    async (req, reply) => {
      const checked = checkedWrites.get(req);
      if (checked === undefined || upstream === undefined) {
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }

      const parsed = writeBodySchema.safeParse(req.body);
      if (!parsed.success) {
        return sendCode(reply, 400, "VALIDATION");
      }
      const { request_key, review_details, modified } = parsed.data;

      const draft = resolveDraft(checked.userId, request_key);
      if (draft === null) {
        return sendCode(reply, 404, "NOT_FOUND");
      }

      const resolved = await authorizeDraft(checked, draft.encounterId);
      if (resolved.state === "expired") {
        invalidateSession(reply, checked.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (resolved.state === "refused") {
        return sendCode(reply, resolved.status, resolved.code);
      }

      // Provable staleness from our own read: the base version is no longer
      // current, so nothing is sent and no update can be lost by us.
      if (resolved.row.modified !== modified) {
        return sendCode(reply, 409, "CONFLICT_STALE");
      }

      const operationKey = reviewOperationKey(draft.encounterId, modified);
      // Durable write-ahead claim. A crash after this point leaves `pending`,
      // which keeps blocking every later attempt instead of retrying blindly.
      const begun = encounterReviews.begin(
        checked.userId,
        draft.encounterId,
        operationKey,
      );
      if (begun.kind === "replay") {
        // The same intent already reached an uncertain outcome: same answer,
        // still no second write.
        return reply.code(200).send({ status: "unknown" });
      }
      if (begun.kind === "blocked") {
        return sendCode(
          reply,
          409,
          begun.state === "unknown" ? "INTENT_UNCONFIRMED" : "INTENT_PENDING",
        );
      }

      /** Clears only a resolved intent; a storage failure keeps it blocking. */
      const releaseIntent = (): void => {
        try {
          encounterReviews.release(checked.userId, draft.encounterId);
        } catch {
          // Fail closed: the row keeps blocking later writes.
        }
      };
      const keepUnknownIntent = (): void => {
        try {
          encounterReviews.markUnknown(checked.userId, draft.encounterId);
        } catch {
          // Still `pending`, which blocks too; the answer does not change.
        }
      };

      const outcome = await putEncounterComment(
        upstream,
        fetchImpl,
        checked.native,
        resolved.encounterId,
        { details: review_details, modified },
      );

      if (outcome.state === "saved") {
        releaseIntent();
        return reply
          .code(200)
          .send({ status: "saved", modified: outcome.modified });
      }
      if (outcome.state === "unknown") {
        keepUnknownIntent();
        // The write may have committed. Report the uncertain outcome, but still
        // drop a native session that the ambiguity probe proved dead.
        if (outcome.expired === true) {
          invalidateSession(reply, checked.sessionId);
        }
        return reply.code(200).send({ status: "unknown" });
      }
    },
  );
}
