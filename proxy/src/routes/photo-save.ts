import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { z } from "zod";
import type { AppConfig, LiveUpstream } from "../config.js";
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
import { hasJsonContentType, headerValue } from "./auth.js";
import {
  probeNativeSession,
  readAuthorizedPatient,
} from "./patient-authority.js";

const SAVE_PATH = "/api/method/clinic_photos.api.photo_save.save";
const STATUS_PATH = "/api/method/clinic_photos.api.photo_save.status";

/** Native request bound from the documented publication contract. */
const MAX_REQUEST_BYTES = 12 * 1024 * 1024;
const MAX_STATUS_BYTES = 4 * 1024;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_UPSTREAM_RESPONSE_BYTES = 128 * 1024;

const REQUEST_KEY = /^[A-Za-z0-9_-]{16,128}$/;
const WORKSPACE_PATIENT = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$/;
const OPERATION_ID = /^[0-9a-f]{32}$/;
const CLINICIAN_ROLE = "DOC";

const SAVE_STATUSES = [
  "pending",
  "unknown",
  "conflict",
  "rejected",
  "saved",
] as const;

const saveBodySchema = z
  .object({
    kind: z.literal("synthetic_original_create"),
    request_key: z.string().regex(REQUEST_KEY),
    synthetic_patient: z
      .string()
      .regex(WORKSPACE_PATIENT)
      .refine((value) => !value.includes("..")),
    image_base64: z.string().min(1),
  })
  .strict();

const statusBodySchema = z
  .object({
    operation_id: z.string().regex(OPERATION_ID),
  })
  .strict();

const outcomeSchema = z.object({
  status: z.enum(SAVE_STATUSES),
  operation_id: z.string().regex(OPERATION_ID),
  version: z.number().int().positive().optional(),
  file_name: z.string().min(1).max(1024).optional(),
});

type SaveStatus = (typeof SAVE_STATUSES)[number];

interface Outcome {
  status: SaveStatus;
  operation_id: string;
  version?: number;
  file_name?: string;
}

interface UpstreamResult {
  statusCode: number;
  body: Record<string, unknown>;
}

interface CheckedRequest {
  sessionId: string;
  native: NativeCredentials;
}

export interface PhotoWriteDeps {
  config: AppConfig;
  sessions: SessionStore;
  csrf: CsrfStore;
  native: NativeCredentialStore;
  fetchImpl: typeof fetch;
}

function sendCode(reply: FastifyReply, status: number, code: string) {
  return reply.code(status).send({ code });
}

function upstreamUnavailable(): UpstreamResult {
  return { statusCode: 502, body: { code: "UPSTREAM_UNAVAILABLE" } };
}

function isPng(bytes: Buffer): boolean {
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  return (
    bytes.length >= signature.length &&
    signature.every((value, index) => bytes[index] === value)
  );
}

function isJpeg(bytes: Buffer): boolean {
  return (
    bytes.length >= 3 &&
    bytes[0] === 0xff &&
    bytes[1] === 0xd8 &&
    bytes[2] === 0xff
  );
}

/** Bounded canonical decode; bytes are validated and then discarded. */
function decodeImage(value: string): Buffer | null {
  if (value.length === 0 || value.length % 4 !== 0) return null;
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(value)) return null;

  const bytes = Buffer.from(value, "base64");
  if (bytes.length === 0 || bytes.length > MAX_IMAGE_BYTES) return null;
  if (bytes.toString("base64") !== value) return null;
  if (!isPng(bytes) && !isJpeg(bytes)) return null;
  return bytes;
}

async function readUpstreamJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > MAX_UPSTREAM_RESPONSE_BYTES) {
    throw new Error("UPSTREAM_RESPONSE_TOO_LARGE");
  }
  return JSON.parse(text);
}

function outcomeFromBody(body: unknown): Outcome | null {
  if (typeof body !== "object" || body === null) return null;
  const parsed = outcomeSchema.safeParse(
    (body as Record<string, unknown>).message,
  );
  if (!parsed.success) return null;
  const { status, operation_id, version, file_name } = parsed.data;
  return {
    status,
    operation_id,
    ...(version === undefined ? {} : { version }),
    ...(file_name === undefined ? {} : { file_name }),
  };
}

async function postNative(
  upstream: LiveUpstream,
  fetchImpl: typeof fetch,
  path: string,
  payload: Record<string, unknown>,
  native: NativeCredentials,
): Promise<UpstreamResult> {
  let response: Response;
  try {
    response = await fetchImpl(`${upstream.origin}${path}`, {
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
    return upstreamUnavailable();
  }

  if (response.status === 200) {
    let body: unknown;
    try {
      body = await readUpstreamJson(response);
    } catch {
      return upstreamUnavailable();
    }
    const outcome = outcomeFromBody(body);
    if (outcome === null) {
      return upstreamUnavailable();
    }
    return {
      statusCode: 200,
      body: outcome as unknown as Record<string, unknown>,
    };
  }

  switch (response.status) {
    case 401:
      return { statusCode: 401, body: { code: "AUTH_EXPIRED" } };
    case 403:
      return { statusCode: 403, body: { code: "FORBIDDEN" } };
    case 409:
      return { statusCode: 409, body: { code: "CONFLICT_STALE" } };
    case 413:
      return { statusCode: 413, body: { code: "FILE_TOO_LARGE" } };
    case 400:
    case 417:
    case 422:
      return { statusCode: 400, body: { code: "VALIDATION" } };
    case 429:
      return { statusCode: 429, body: { code: "RATE_LIMITED" } };
    default:
      return upstreamUnavailable();
  }
}

export function registerPhotoWriteRoutes(
  app: FastifyInstance,
  deps: PhotoWriteDeps,
): void {
  const { config, sessions, csrf, native, fetchImpl } = deps;
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
   * Runs before body parsing: unauthenticated, cross-origin, CSRF-less and
   * role-less requests never cause a 12 MiB payload to be read or parsed.
   * `writesEnabled` is false for a browse-only live lab (no
   * `CLINIC_LIVE_PHOTO_WRITES` opt-in), so the write route refuses explicitly
   * before the body parser runs and before any CSRF token is consumed.
   */
  const createGuard =
    (writesEnabled: boolean) =>
    async (req: FastifyRequest, reply: FastifyReply): Promise<void> => {
      reply.header("cache-control", "no-store");

      if (upstream === undefined) {
        sendCode(reply, 404, "UNSUPPORTED");
        return;
      }
      if (!writesEnabled) {
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

      checkedRequests.set(req, { sessionId, native: credentials });
    };

  const writeGuard = createGuard(config.livePhotoWrites === true);
  // Status is a read of an operation the clinician already started, so it stays
  // available on a browse-only live lab. It never creates or mutates a photo.
  const statusGuard = createGuard(true);

  app.post(
    "/api/write/photo-save",
    { bodyLimit: MAX_REQUEST_BYTES, onRequest: writeGuard },
    async (req, reply) => {
      const checked = checkedRequests.get(req);
      if (checked === undefined || upstream === undefined) {
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }

      const parsed = saveBodySchema.safeParse(req.body);
      if (!parsed.success || decodeImage(parsed.data.image_base64) === null) {
        return sendCode(reply, 400, "VALIDATION");
      }

      // Client value is only an allow-list key. The forwarded native name is
      // always the server-owned mapping, never fixture route text.
      const nativePatient = upstream.syntheticPatients.get(
        parsed.data.synthetic_patient,
      );
      if (nativePatient === undefined) {
        return sendCode(reply, 400, "VALIDATION");
      }

      const authority = await readAuthorizedPatient(
        upstream,
        fetchImpl,
        checked.native,
        nativePatient,
      );
      if (authority.state === "expired") {
        invalidateSession(reply, checked.sessionId);
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (authority.state === "denied") {
        return sendCode(reply, 403, "FORBIDDEN");
      }
      if (authority.state !== "authorized") {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }

      const result = await postNative(
        upstream,
        fetchImpl,
        SAVE_PATH,
        {
          // Native `save(payload)` takes one payload argument; Frappe binds
          // body keys to kwargs, so the request document stays nested.
          payload: {
            kind: parsed.data.kind,
            request_key: parsed.data.request_key,
            synthetic_patient: nativePatient,
            image_base64: parsed.data.image_base64,
          },
        },
        checked.native,
      );
      if (result.statusCode === 401) {
        invalidateSession(reply, checked.sessionId);
      }
      if (result.statusCode === 403) {
        const session = await probeNativeSession(
          upstream,
          fetchImpl,
          checked.native,
        );
        if (session === "expired") {
          invalidateSession(reply, checked.sessionId);
          return sendCode(reply, 401, "AUTH_EXPIRED");
        }
      }
      return reply.code(result.statusCode).send(result.body);
    },
  );

  app.post(
    "/api/write/photo-status",
    { bodyLimit: MAX_STATUS_BYTES, onRequest: statusGuard },
    async (req, reply) => {
      const checked = checkedRequests.get(req);
      if (checked === undefined || upstream === undefined) {
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }

      const parsed = statusBodySchema.safeParse(req.body);
      if (!parsed.success) {
        return sendCode(reply, 400, "VALIDATION");
      }

      const result = await postNative(
        upstream,
        fetchImpl,
        STATUS_PATH,
        { operation_id: parsed.data.operation_id },
        checked.native,
      );
      if (result.statusCode === 401) {
        invalidateSession(reply, checked.sessionId);
      }
      if (result.statusCode === 403) {
        const session = await probeNativeSession(
          upstream,
          fetchImpl,
          checked.native,
        );
        if (session === "expired") {
          invalidateSession(reply, checked.sessionId);
          return sendCode(reply, 401, "AUTH_EXPIRED");
        }
      }
      return reply.code(result.statusCode).send(result.body);
    },
  );
}
