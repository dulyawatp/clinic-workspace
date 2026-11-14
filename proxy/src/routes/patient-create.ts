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
import {
  hashCreatePayload,
  type PatientCreateStore,
} from "../writes/patient-create-store.js";
import { hasJsonContentType, headerValue } from "./auth.js";
import { probeNativeSession } from "./patient-authority.js";

const CREATE_PATH = "/api/resource/Patient";
const GENDER_PATH = "/api/resource/Gender";

const MAX_REQUEST_BYTES = 4 * 1024;
const MAX_UPSTREAM_RESPONSE_BYTES = 128 * 1024;
const MAX_GENDERS = 100;

const REQUEST_KEY = /^[A-Za-z0-9_-]{16,128}$/;
const RECEPTION_ROLE = "REC";

/**
 * Names are never used in a URL; they only travel inside the JSON body. Control
 * characters are refused by scanning code points so no pattern has to embed
 * them.
 */
function hasControlCharacter(value: string): boolean {
  for (const character of value) {
    const code = character.codePointAt(0);
    if (code !== undefined && (code <= 0x1f || code === 0x7f)) {
      return true;
    }
  }
  return false;
}

const personName = z
  .string()
  .trim()
  .min(1)
  .max(140)
  .refine((value) => !hasControlCharacter(value));

const createBodySchema = z
  .object({
    request_key: z.string().regex(REQUEST_KEY),
    first_name: personName,
    last_name: personName.optional(),
    sex: personName,
  })
  .strict();

interface CheckedRequest {
  sessionId: string;
  userId: string;
  native: NativeCredentials;
}

export interface PatientCreateDeps {
  config: AppConfig;
  sessions: SessionStore;
  csrf: CsrfStore;
  native: NativeCredentialStore;
  patientCreates: PatientCreateStore;
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

type GendersResult =
  | { state: "ok"; items: string[] }
  | { state: "expired" }
  | { state: "unavailable" };

/**
 * Reads the native `Gender` reference list as the clinician. Names are the
 * authoritative values a native `Patient.sex` link accepts; nothing is
 * hard-coded in the proxy.
 */
async function readNativeGenders(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
): Promise<GendersResult> {
  const query = new URLSearchParams({
    fields: '["name"]',
    limit_page_length: String(MAX_GENDERS),
  });

  let response: Response;
  try {
    response = await fetchImpl(
      `${upstream.origin}${GENDER_PATH}?${query.toString()}`,
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
  if (response.status === 403) {
    const session = await probeNativeSession(upstream, fetchImpl, native);
    return session === "expired"
      ? { state: "expired" }
      : { state: "unavailable" };
  }
  if (response.status !== 200) return { state: "unavailable" };

  let body: unknown;
  try {
    body = await readUpstreamJson(response);
  } catch {
    return { state: "unavailable" };
  }

  const data =
    typeof body === "object" && body !== null
      ? (body as Record<string, unknown>).data
      : undefined;
  if (!Array.isArray(data) || data.length > MAX_GENDERS) {
    return { state: "unavailable" };
  }

  const items: string[] = [];
  for (const entry of data) {
    const name =
      typeof entry === "object" && entry !== null
        ? (entry as Record<string, unknown>).name
        : undefined;
    if (
      typeof name !== "string" ||
      name.trim() === "" ||
      name.length > 140 ||
      hasControlCharacter(name)
    ) {
      return { state: "unavailable" };
    }
    if (!items.includes(name)) items.push(name);
  }
  if (items.length === 0) return { state: "unavailable" };

  return { state: "ok", items };
}

type CreateResult =
  | { state: "created"; patientId: string }
  | { state: "unknown" }
  | { state: "expired" }
  | { state: "failed"; status: number; code: string };

/**
 * Creates exactly one native `Patient` as the clinician. Only a 2xx whose
 * `data.name` is a safe native docname counts as created; transport failures,
 * 5xx and unreadable bodies are *unknown* and must never be retried with the
 * same request key.
 */
async function postNativePatient(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  native: NativeCredentials,
  payload: Record<string, string | number>,
): Promise<CreateResult> {
  let response: Response;
  try {
    response = await fetchImpl(`${upstream.origin}${CREATE_PATH}`, {
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
    return { state: "created", patientId: name };
  }

  switch (response.status) {
    case 401:
      return { state: "expired" };
    case 403: {
      const session = await probeNativeSession(upstream, fetchImpl, native);
      if (session === "expired") return { state: "expired" };
      return { state: "failed", status: 403, code: "FORBIDDEN" };
    }
    case 400:
    case 409:
    case 417:
    case 422:
      return { state: "failed", status: 400, code: "VALIDATION" };
    case 404:
    case 405:
      // This site has no usable Patient create API: nothing was created, but a
      // retry cannot help until the lab is fixed.
      return { state: "failed", status: 502, code: "UPSTREAM_UNAVAILABLE" };
    case 413:
      return { state: "failed", status: 413, code: "FILE_TOO_LARGE" };
    case 429:
      return { state: "failed", status: 429, code: "RATE_LIMITED" };
    default:
      // Redirects (a login bounce), 5xx, 408 and any unexpected status can hide
      // a committed document. Treat them as uncertain, never as "no create".
      return { state: "unknown" };
  }
}

/**
 * Live reception registration slice: one native `Patient` per request key.
 * Requires an explicit `CLINIC_LIVE_PATIENT_WRITES` opt-in, the clinician's own
 * native session (never a service account), the trusted browser origin, a
 * session CSRF token and the `REC` role. Nothing is written to the fixture data
 * set and no local patient record is invented. Locally only a payload hash and
 * the native `Patient` docname are kept (see `PatientCreateStore`); the docname
 * may itself be a human name, so created patients are not exposed through any
 * proxy directory route.
 */
export function registerPatientCreateRoutes(
  app: FastifyInstance,
  deps: PatientCreateDeps,
): void {
  const { config, sessions, csrf, native, patientCreates, fetchImpl } = deps;
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
   * cross-origin, CSRF-less or non-reception request never has its payload read
   * and never consumes a CSRF token.
   */
  async function createGuard(
    req: FastifyRequest,
    reply: FastifyReply,
  ): Promise<void> {
    reply.header("cache-control", "no-store");

    if (upstream === undefined) {
      sendCode(reply, 404, "UNSUPPORTED");
      return;
    }
    if (config.livePatientWrites !== true) {
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
    if (!session.roles.includes(RECEPTION_ROLE)) {
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
    "/api/write/patient-create",
    { bodyLimit: MAX_REQUEST_BYTES, onRequest: createGuard },
    async (req, reply) => {
      const checked = checkedRequests.get(req);
      if (checked === undefined || upstream === undefined) {
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }

      const parsed = createBodySchema.safeParse(req.body);
      if (!parsed.success) {
        return sendCode(reply, 400, "VALIDATION");
      }
      const { request_key, first_name, last_name, sex } = parsed.data;
      const payloadHash = hashCreatePayload({
        firstName: first_name,
        sex,
        ...(last_name === undefined ? {} : { lastName: last_name }),
      });

      const begun = patientCreates.begin(
        checked.userId,
        request_key,
        payloadHash,
      );
      if (begun.kind === "conflict") {
        return sendCode(reply, 409, "CONFLICT_DUPLICATE");
      }
      if (begun.kind === "replay") {
        // A settled or uncertain key never triggers a second native create.
        return begun.state === "created" && begun.patientId !== undefined
          ? reply
              .code(200)
              .send({ status: "created", patient_id: begun.patientId })
          : reply.code(200).send({ status: "unknown" });
      }

      /**
       * Releases the write-ahead row after a definite non-create. A storage
       * failure leaves it `pending`, which replays as `unknown`; it never
       * changes the response because nothing was created.
       */
      const releaseKey = (): void => {
        try {
          patientCreates.release(checked.userId, request_key);
        } catch {
          // Intentionally ignored: see above.
        }
      };

      const genders = await readNativeGenders(
        upstream,
        fetchImpl,
        checked.native,
      );
      if (genders.state === "expired") {
        releaseKey();
        invalidateSession(reply, checked.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (genders.state !== "ok") {
        releaseKey();
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }
      // The native site owns the accepted values; the client only proposes one.
      if (!genders.items.includes(sex)) {
        releaseKey();
        return sendCode(reply, 400, "VALIDATION");
      }

      const result = await postNativePatient(
        upstream,
        fetchImpl,
        checked.native,
        {
          first_name,
          ...(last_name === undefined ? {} : { last_name }),
          sex,
          // Server-owned by contract: registration never invites the patient to
          // a portal account, and the client cannot choose otherwise.
          invite_user: 0,
        },
      );

      if (result.state === "created") {
        // The document exists natively. If the outcome cannot be recorded the
        // row stays `pending`, so a repeated request key replays as `unknown`
        // and never creates a second patient. Never answer with a failure that
        // would imply nothing was created.
        try {
          patientCreates.markCreated(
            checked.userId,
            request_key,
            result.patientId,
          );
        } catch {
          return reply.code(200).send({ status: "unknown" });
        }
        return reply
          .code(200)
          .send({ status: "created", patient_id: result.patientId });
      }
      if (result.state === "unknown") {
        try {
          patientCreates.markUnknown(checked.userId, request_key);
        } catch {
          // Still `pending`, which replays as `unknown`: same duplicate-safe
          // outcome, so the client answer does not change.
        }
        return reply.code(200).send({ status: "unknown" });
      }

      // Definite non-create: the key stays reusable. A storage failure here
      // leaves the row `pending`; the mapped refusal is still truthful because
      // nothing was created, and a later replay fails closed as `unknown`.
      releaseKey();
      if (result.state === "expired") {
        invalidateSession(reply, checked.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      return sendCode(reply, result.status, result.code);
    },
  );

  /**
   * Native sex reference for the registration form. A read of data the
   * clinician may already see natively, so it stays available without the write
   * opt-in, but only for a live reception session.
   */
  app.get("/api/live/patient-genders", async (req, reply) => {
    reply.header("cache-control", "no-store");

    if (upstream === undefined) {
      return sendCode(reply, 404, "UNSUPPORTED");
    }

    const sessionId = req.cookies[SESSION_COOKIE];
    const session =
      sessionId === undefined ? undefined : sessions.get(sessionId);
    if (sessionId === undefined || session === undefined) {
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }
    if (!session.roles.includes(RECEPTION_ROLE)) {
      return sendCode(reply, 403, "FORBIDDEN");
    }

    const credentials = native.get(sessionId);
    if (credentials === undefined) {
      invalidateSession(reply, sessionId);
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }

    const genders = await readNativeGenders(upstream, fetchImpl, credentials);
    if (genders.state === "expired") {
      invalidateSession(reply, sessionId);
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }
    if (genders.state !== "ok") {
      return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
    }
    return reply.code(200).send({ items: genders.items });
  });
}
