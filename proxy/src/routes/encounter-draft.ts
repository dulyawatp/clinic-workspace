import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { z } from "zod";
import {
  type AppConfig,
  isSafeNativePatientName,
  isSafePatientReference,
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
import {
  type EncounterDraftStore,
  hashEncounterDraftPayload,
  patientIntentFingerprint,
} from "../writes/encounter-draft-store.js";
import { hasJsonContentType, headerValue } from "./auth.js";
import {
  probeNativeSession,
  readAuthorizedPatient,
} from "./patient-authority.js";

/** Native `Patient Encounter` collection; never a client-chosen doctype. */
const ENCOUNTER_PATH = "/api/resource/Patient%20Encounter";
const PRACTITIONER_PATH_PREFIX = "/api/resource/Healthcare%20Practitioner/";
const APPOINTMENT_TYPE_PATH_PREFIX = "/api/resource/Appointment%20Type/";

const MAX_REQUEST_BYTES = 2 * 1024;
const MAX_UPSTREAM_RESPONSE_BYTES = 128 * 1024;

const REQUEST_KEY = /^[A-Za-z0-9_-]{16,128}$/;
const CLINICIAN_ROLE = "DOC";

const draftBodySchema = z
  .object({
    request_key: z.string().regex(REQUEST_KEY),
    patient_reference: z.string().refine(isSafePatientReference),
  })
  .strict();

interface CheckedRequest {
  sessionId: string;
  userId: string;
  native: NativeCredentials;
}

export interface EncounterDraftDeps {
  config: AppConfig;
  sessions: SessionStore;
  csrf: CsrfStore;
  native: NativeCredentialStore;
  encounterDrafts: EncounterDraftStore;
  fetchImpl: typeof fetch;
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

type PractitionerRead =
  | { state: "verified" }
  | { state: "denied" }
  | { state: "expired" }
  | { state: "unavailable" };

/**
 * Reads the single server-owned `Practitioner` docname with the caller's own
 * native session. Only an exact docname that this session may read and that is
 * natively active counts as verified.
 *
 * This proves the mapped record's authorization and mapping; it does not prove
 * ownership. The installed Marley Practitioner row leaves `user_id` blank, so a
 * `user_id` comparison is deliberately not performed and ownership is never
 * claimed. There is no alternate-practitioner fallback.
 */
async function readVerifiedPractitioner(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  practitionerId: string,
): Promise<PractitionerRead> {
  let response: Response;
  try {
    response = await fetchImpl(
      `${upstream.origin}${PRACTITIONER_PATH_PREFIX}${encodeURIComponent(practitionerId)}`,
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
        ? (body as Record<string, unknown>).data
        : undefined;
    if (typeof data !== "object" || data === null) {
      return { state: "unavailable" };
    }
    const row = data as Record<string, unknown>;
    // A wrong or inactive mapping is a refusal, not a transient failure.
    if (row.name !== practitionerId || !isNativePractitionerActive(row)) {
      return { state: "denied" };
    }
    return { state: "verified" };
  } catch {
    return { state: "unavailable" };
  }
}

/**
 * Native Healthcare `Practitioner` activity. The installed doctype exposes a
 * `status` select; a boolean `active` flag is accepted as well. Anything else is
 * treated as inactive so an unknown shape cannot authorize a draft.
 */
function isNativePractitionerActive(row: Record<string, unknown>): boolean {
  if (typeof row.status === "string") {
    return row.status.trim().toLowerCase() === "active";
  }
  return row.active === 1 || row.active === true;
}

type AppointmentTypeRead =
  | { state: "verified" }
  | { state: "denied" }
  | { state: "expired" }
  | { state: "unavailable" };

/**
 * Reads the configured native `Appointment Type` with the caller's own session.
 * The value is server-owned; this only proves that this clinician may read that
 * exact record before it is forwarded.
 */
async function readVerifiedAppointmentType(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  appointmentType: string,
): Promise<AppointmentTypeRead> {
  let response: Response;
  try {
    response = await fetchImpl(
      `${upstream.origin}${APPOINTMENT_TYPE_PATH_PREFIX}${encodeURIComponent(appointmentType)}`,
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
    const session = await probeNativeSession(upstream, fetchImpl, native);
    return session === "expired" ? { state: "expired" } : { state: "denied" };
  }
  if (response.status !== 200) return { state: "unavailable" };

  try {
    const body = await readUpstreamJson(response);
    const data =
      typeof body === "object" && body !== null
        ? (body as Record<string, unknown>).data
        : undefined;
    if (typeof data !== "object" || data === null) {
      return { state: "unavailable" };
    }
    return (data as Record<string, unknown>).name === appointmentType
      ? { state: "verified" }
      : { state: "denied" };
  } catch {
    return { state: "unavailable" };
  }
}

type CreateResult =
  /** Only a 2xx body naming a safe native docname proves the draft exists. */
  | { state: "created"; encounterId: string }
  /** Fail closed. `expired` still clears the dead native session. */
  | { state: "unknown"; expired?: true };

/**
 * Creates exactly one draft native `Patient Encounter` as the clinician. The
 * body is fully server-owned: no appointment link (native `on_update` would
 * close it), no orders and no submit. Only a 2xx whose `data.name` is a safe
 * native docname counts as created.
 *
 * Nothing else counts as a non-create. Once the request is dispatched, a 400,
 * 401, 403, 404, 405, 409, 413, 417, 422 or 429 can equally come from an
 * intermediary, and a buffered create can still commit, so every non-2xx answer
 * is `unknown` and keeps the durable intent. A 401 (or a 403 whose read-only
 * probe proves the session dead) additionally clears the dead session.
 */
async function postNativeEncounter(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  payload: Record<string, string | number>,
): Promise<CreateResult> {
  let response: Response;
  try {
    response = await fetchImpl(`${upstream.origin}${ENCOUNTER_PATH}`, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
        cookie: `sid=${native.sid}`,
        "x-frappe-csrf-token": native.csrf,
      },
      body: JSON.stringify(payload),
      redirect: "manual",
      signal: AbortSignal.timeout(upstream.timeoutMs),
    });
  } catch {
    return { state: "unknown" };
  }

  if (response.status === 200 || response.status === 201) {
    let body: unknown;
    try {
      body = await readUpstreamJson(response);
    } catch {
      return { state: "unknown" };
    }
    const data =
      typeof body === "object" && body !== null
        ? (body as Record<string, unknown>).data
        : undefined;
    const name =
      typeof data === "object" && data !== null
        ? (data as Record<string, unknown>).name
        : undefined;
    if (typeof name !== "string" || !isSafeNativePatientName(name)) {
      return { state: "unknown" };
    }
    return { state: "created", encounterId: name };
  }

  if (response.status === 401) return { state: "unknown", expired: true };
  if (response.status === 403) {
    // A denial can also mean the native session is gone. The probe's only
    // effect is dropping a dead session; it never decides the write outcome.
    const session = await probeNativeSession(upstream, fetchImpl, native);
    return session === "expired"
      ? { state: "unknown", expired: true }
      : { state: "unknown" };
  }

  // Redirects, 5xx, 408, released 4xx refusals and any unexpected status can
  // hide a committed document: uncertain, never "no create".
  return { state: "unknown" };
}

/**
 * Live synthetic encounter draft slice: one native `Patient Encounter` per
 * request key, plus one durable unresolved-intent lock per user and patient so a
 * second key cannot start a duplicate while an earlier outcome is pending or
 * unknown. Requires an explicit `CLINIC_LIVE_ENCOUNTER_WRITES` opt-in, the
 * clinician's own native session (never a service account), the trusted browser
 * origin, a session CSRF token and the `DOC` role. The patient must be an
 * allow-listed synthetic reference that the calling session may still read. The
 * practitioner comes from an operator-controlled user→practitioner mapping,
 * verified by an exact native read that the mapped record is active; this is not
 * a claim of `user_id` ownership. The appointment type comes from configuration
 * only.
 *
 * Locally only a payload fingerprint and the native encounter docname are kept
 * (see `EncounterDraftStore`). The encounter is always a draft: the proxy never
 * submits it and never sends orders.
 */
export function registerEncounterDraftRoutes(
  app: FastifyInstance,
  deps: EncounterDraftDeps,
): void {
  const { config, sessions, csrf, native, encounterDrafts, fetchImpl } = deps;
  const upstream = config.upstreamMode === "live" ? config.live : undefined;

  const checkedRequests = new WeakMap<FastifyRequest, CheckedRequest>();

  function invalidateSession(reply: FastifyReply, sessionId: string): void {
    native.clear(sessionId);
    sessions.destroy(sessionId);
    csrf.revokeAll(sessionId);
    reply.clearCookie(SESSION_COOKIE, { path: "/" });
    reply.clearCookie(CSRF_COOKIE, { path: "/" });
  }

  /**
   * Runs before body parsing: a browse-only live lab, an unauthenticated,
   * cross-origin, CSRF-less or non-clinician request never has its payload read
   * and never consumes a CSRF token.
   */
  async function draftGuard(
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

    checkedRequests.set(req, {
      sessionId,
      userId: session.userId,
      native: credentials,
    });
  }

  app.post(
    "/api/write/encounter-draft",
    { bodyLimit: MAX_REQUEST_BYTES, onRequest: draftGuard },
    async (req, reply) => {
      const checked = checkedRequests.get(req);
      if (checked === undefined || upstream === undefined) {
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }

      const parsed = draftBodySchema.safeParse(req.body);
      if (!parsed.success) {
        return sendCode(reply, 400, "VALIDATION");
      }
      const { request_key, patient_reference } = parsed.data;

      // The client only names a workspace reference; the native patient always
      // comes from the server-owned allow-list.
      const nativePatientId = upstream.syntheticPatients.get(patient_reference);
      if (nativePatientId === undefined) {
        return sendCode(reply, 400, "VALIDATION");
      }

      const appointmentType = upstream.encounterAppointmentType;
      const practitionerId = upstream.encounterPractitioners?.get(
        checked.userId,
      );
      if (appointmentType === undefined || practitionerId === undefined) {
        // No verified mapping for this clinician: refuse with no native side
        // effect rather than inventing a native name.
        return sendCode(reply, 403, "FORBIDDEN");
      }

      const payloadHash = hashEncounterDraftPayload({
        patient: nativePatientId,
        practitioner: practitionerId,
        appointmentType,
      });
      const patientFingerprint = patientIntentFingerprint(nativePatientId);

      const begun = encounterDrafts.begin(
        checked.userId,
        request_key,
        payloadHash,
        patientFingerprint,
      );
      if (begun.kind === "conflict") {
        return sendCode(reply, 409, "CONFLICT_DUPLICATE");
      }
      if (begun.kind === "locked") {
        // Another request key already owns an unresolved intent for this
        // patient. The earlier key is the only one that may resolve it, so this
        // client must not retry; the proxy never reconciles or clears it.
        return sendCode(
          reply,
          409,
          begun.state === "unknown" ? "INTENT_UNCONFIRMED" : "INTENT_PENDING",
        );
      }
      if (begun.kind === "replay") {
        // A settled or uncertain key never triggers a second native create.
        return begun.state === "created" && begun.encounterId !== undefined
          ? reply
              .code(200)
              .send({ status: "created", encounter_id: begun.encounterId })
          : reply.code(200).send({ status: "unknown" });
      }

      /**
       * Releases the write-ahead row after a *pre-dispatch* refusal, where no
       * native create was sent. A storage failure leaves it `pending`, which
       * replays as `unknown`; it never changes the response because nothing was
       * created. Post-dispatch answers never release: see `postNativeEncounter`.
       */
      const releaseKey = (): void => {
        try {
          encounterDrafts.release(checked.userId, request_key);
        } catch {
          // Intentionally ignored: see above.
        }
      };

      // Reauthorize against the live site as the caller before any write: an
      // allow-list entry alone is not access.
      const authority = await readAuthorizedPatient(
        upstream,
        fetchImpl,
        checked.native,
        nativePatientId,
      );
      if (authority.state === "expired") {
        releaseKey();
        invalidateSession(reply, checked.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (authority.state === "denied") {
        releaseKey();
        return sendCode(reply, 403, "FORBIDDEN");
      }
      if (authority.state !== "authorized") {
        releaseKey();
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      const practitioner = await readVerifiedPractitioner(
        upstream,
        fetchImpl,
        checked.native,
        practitionerId,
      );
      if (practitioner.state === "expired") {
        releaseKey();
        invalidateSession(reply, checked.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (practitioner.state === "denied") {
        releaseKey();
        return sendCode(reply, 403, "FORBIDDEN");
      }
      if (practitioner.state !== "verified") {
        releaseKey();
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      // The configured appointment type must also be readable by this session
      // before it is forwarded.
      const typeCheck = await readVerifiedAppointmentType(
        upstream,
        fetchImpl,
        checked.native,
        appointmentType,
      );
      if (typeCheck.state === "expired") {
        releaseKey();
        invalidateSession(reply, checked.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (typeCheck.state === "denied") {
        releaseKey();
        return sendCode(reply, 403, "FORBIDDEN");
      }
      if (typeCheck.state !== "verified") {
        releaseKey();
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      const result = await postNativeEncounter(
        upstream,
        fetchImpl,
        checked.native,
        {
          patient: nativePatientId,
          practitioner: practitionerId,
          appointment_type: appointmentType,
          // Server-owned by contract: a draft that never submits orders and is
          // never linked to a native appointment.
          docstatus: 0,
          submit_orders_on_save: 0,
        },
      );

      if (result.state === "created") {
        // The document exists natively. If the outcome cannot be recorded the
        // row stays `pending`, so a repeated request key replays as `unknown`
        // and never creates a second encounter.
        try {
          encounterDrafts.markCreated(
            checked.userId,
            request_key,
            result.encounterId,
          );
        } catch {
          return reply.code(200).send({ status: "unknown" });
        }
        return reply
          .code(200)
          .send({ status: "created", encounter_id: result.encounterId });
      }
      // The only remaining outcome is `unknown`: the request was dispatched but
      // no response proved what it did.
      try {
        encounterDrafts.markUnknown(checked.userId, request_key);
      } catch {
        // Still `pending`, which replays as `unknown`: same duplicate-safe
        // outcome, so the client answer does not change.
      }
      // The write may have committed. Report the uncertain outcome, but still
      // drop a native session the response or probe proved dead.
      if (result.expired === true) {
        invalidateSession(reply, checked.sessionId);
      }
      return reply.code(200).send({ status: "unknown" });
    },
  );
}
