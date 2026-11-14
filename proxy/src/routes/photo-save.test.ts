import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppConfig } from "../config.js";
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  SESSION_COOKIE,
} from "../security/constants.js";
import { buildServer } from "../server.js";
import { NativeCredentialStore } from "../session/native-store.js";
import type { Authenticate } from "./auth.js";

const TEST_ORIGIN = "http://localhost:5173";
const LAB_ORIGIN = "https://lab.example";
const PATIENT_URL = `${LAB_ORIGIN}/api/resource/Patient/SYNTH-PATIENT-1`;
const SAVE_URL = `${LAB_ORIGIN}/api/method/clinic_photos.api.photo_save.save`;
const STATUS_URL = `${LAB_ORIGIN}/api/method/clinic_photos.api.photo_save.status`;
const METADATA_URL = `${LAB_ORIGIN}/api/resource/Patient/SYNTH-PATIENT-2`;

const NATIVE_SID = "native-sid-value";
const NATIVE_CSRF = "native-csrf-value";
const OPERATION_ID = "a".repeat(32);

const IMAGE_BASE64 = Buffer.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
]).toString("base64");

const LIVE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "live",
  livePhotoWrites: true,
  live: {
    origin: LAB_ORIGIN,
    timeoutMs: 1_000,
    syntheticPatients: new Map([
      ["PAT-1", "SYNTH-PATIENT-1"],
      ["PAT-2", "SYNTH-PATIENT-2"],
    ]),
    clinicianRoles: new Set(["Clinic Clinician"]),
  },
};

const FIXTURE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

/** Live lab without the `CLINIC_LIVE_PHOTO_WRITES` opt-in: browse-only. */
const BROWSE_ONLY_LIVE_CONFIG: AppConfig = {
  ...LIVE_CONFIG,
  livePhotoWrites: false,
};

const liveAuthenticate: Authenticate = async () => ({
  user: { id: "clinician@lab.example", displayName: "Dr Lab", roles: ["DOC"] },
  native: { sid: NATIVE_SID, csrf: NATIVE_CSRF },
});

type Handler = (url: string, init: RequestInit) => Response;

interface Harness {
  app: FastifyInstance;
  db: Database.Database;
  native: NativeCredentialStore;
  fetchImpl: ReturnType<typeof vi.fn>;
}

const openApps: FastifyInstance[] = [];
const openDbs: Database.Database[] = [];

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(async () => {
  for (const app of openApps.splice(0)) {
    await app.close();
  }
  for (const db of openDbs.splice(0)) {
    db.close();
  }
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function defaultHandler(url: string): Response {
  if (url === `${LAB_ORIGIN}/api/method/frappe.auth.get_logged_user`) {
    return jsonResponse({ message: "clinician@lab.example" });
  }
  if (url === PATIENT_URL) {
    return jsonResponse({ data: { name: "SYNTH-PATIENT-1" } });
  }
  if (url === METADATA_URL) {
    return jsonResponse({
      data: { name: "SYNTH-PATIENT-2", patient_name: "Synthetic Two" },
    });
  }
  if (url === SAVE_URL) {
    return jsonResponse({
      message: { status: "pending", operation_id: OPERATION_ID },
    });
  }
  if (url === STATUS_URL) {
    return jsonResponse({
      message: {
        status: "saved",
        operation_id: OPERATION_ID,
        version: 1,
        file_name: "photo.png",
      },
    });
  }
  throw new Error(`unexpected upstream call ${url}`);
}

function buildLive(
  handler: Handler = defaultHandler,
  authenticate: Authenticate = liveAuthenticate,
  config: AppConfig = LIVE_CONFIG,
): Harness {
  const db = new Database(":memory:");
  openDbs.push(db);
  const native = new NativeCredentialStore();
  const fetchImpl = vi.fn(
    async (input: string | URL | Request, init?: RequestInit) =>
      handler(String(input), init ?? {}),
  );
  const app = buildServer({
    config,
    database: db,
    authenticate,
    native,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  openApps.push(app);
  return { app, db, native, fetchImpl };
}

function cookiePairs(cookies: Array<{ name: string; value: string }>): string {
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

async function login(
  app: FastifyInstance,
  payload: Record<string, string> = {
    usr: "clinician@lab.example",
    pwd: "secret",
  },
): Promise<string> {
  const pre = await app.inject({ method: "GET", url: "/api/auth/csrf" });
  expect(pre.statusCode).toBe(200);
  const preCsrf = pre.cookies.find((cookie) => cookie.name === CSRF_COOKIE);
  expect(preCsrf).toBeDefined();
  const loginResponse = await app.inject({
    method: "POST",
    url: "/api/auth/login",
    headers: {
      origin: TEST_ORIGIN,
      "content-type": "application/json",
      [CSRF_HEADER]: (pre.json() as { token: string }).token,
      cookie: `${CSRF_COOKIE}=${preCsrf?.value ?? ""}`,
    },
    payload,
  });
  expect(loginResponse.statusCode).toBe(200);
  const session = loginResponse.cookies.find(
    (cookie) => cookie.name === SESSION_COOKIE,
  );
  expect(session).toBeDefined();
  return session?.value ?? "";
}

async function issuedWriteCsrf(
  app: FastifyInstance,
  sessionId: string,
): Promise<{ token: string; cookie: string }> {
  const response = await app.inject({
    method: "GET",
    url: "/api/auth/csrf",
    headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
  });
  expect(response.statusCode).toBe(200);
  const csrfCookie = response.cookies.find(
    (cookie) => cookie.name === CSRF_COOKIE,
  );
  return {
    token: (response.json() as { token: string }).token,
    cookie: `${SESSION_COOKIE}=${sessionId}; ${cookiePairs(
      csrfCookie === undefined ? [] : [csrfCookie],
    )}`,
  };
}

function validSaveBody(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    kind: "synthetic_original_create",
    request_key: "request-key-0123456789",
    synthetic_patient: "PAT-1",
    image_base64: IMAGE_BASE64,
    ...overrides,
  };
}

async function postSave(
  app: FastifyInstance,
  sessionId: string,
  body: unknown,
  options: {
    csrf?: { token: string; cookie: string };
    origin?: string;
    contentType?: string;
  } = {},
) {
  const csrf = options.csrf ?? (await issuedWriteCsrf(app, sessionId));
  return app.inject({
    method: "POST",
    url: "/api/write/photo-save",
    headers: {
      origin: options.origin ?? TEST_ORIGIN,
      "content-type": options.contentType ?? "application/json",
      ...(csrf.token === "" ? {} : { [CSRF_HEADER]: csrf.token }),
      cookie: csrf.cookie,
    },
    payload: body as string,
  });
}

async function postStatus(
  app: FastifyInstance,
  sessionId: string,
  body: unknown,
  csrf?: { token: string; cookie: string },
) {
  const issued = csrf ?? (await issuedWriteCsrf(app, sessionId));
  return app.inject({
    method: "POST",
    url: "/api/write/photo-status",
    headers: {
      origin: TEST_ORIGIN,
      "content-type": "application/json",
      [CSRF_HEADER]: issued.token,
      cookie: issued.cookie,
    },
    payload: body as string,
  });
}

describe("live opt-in boundary", () => {
  it("does not expose write or metadata routes in fixture mode", async () => {
    const db = new Database(":memory:");
    openDbs.push(db);
    const fetchImpl = vi.fn();
    const app = buildServer({
      config: FIXTURE_CONFIG,
      database: db,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    openApps.push(app);

    const save = await app.inject({
      method: "POST",
      url: "/api/write/photo-save",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: validSaveBody(),
    });
    const metadata = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-patients",
    });

    expect(save.statusCode).toBe(404);
    expect(save.json()).toEqual({ code: "UNSUPPORTED" });
    expect(metadata.statusCode).toBe(404);
    expect(metadata.json()).toEqual({ code: "UNSUPPORTED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("keeps fixture sessions without the live photo upload capability", async () => {
    const db = new Database(":memory:");
    openDbs.push(db);
    const app = buildServer({ config: FIXTURE_CONFIG, database: db });
    openApps.push(app);

    const pre = await app.inject({ method: "GET", url: "/api/auth/csrf" });
    const preCsrf = pre.cookies.find((cookie) => cookie.name === CSRF_COOKIE);
    const loginResponse = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: {
        origin: TEST_ORIGIN,
        "content-type": "application/json",
        [CSRF_HEADER]: (pre.json() as { token: string }).token,
        cookie: `${CSRF_COOKIE}=${preCsrf?.value ?? ""}`,
      },
      payload: { usr: "rec", pwd: "fixture" },
    });

    const body = loginResponse.json() as {
      capabilities: Record<string, string>;
    };
    expect(body.capabilities["write.photo.upload"]).toBe("read_only");
  });

  it("reports the upstream mode on a fixture session", async () => {
    const db = new Database(":memory:");
    openDbs.push(db);
    const app = buildServer({ config: FIXTURE_CONFIG, database: db });
    openApps.push(app);

    const pre = await app.inject({ method: "GET", url: "/api/auth/csrf" });
    const preCsrf = pre.cookies.find((cookie) => cookie.name === CSRF_COOKIE);
    const loginResponse = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: {
        origin: TEST_ORIGIN,
        "content-type": "application/json",
        [CSRF_HEADER]: (pre.json() as { token: string }).token,
        cookie: `${CSRF_COOKIE}=${preCsrf?.value ?? ""}`,
      },
      payload: { usr: "rec", pwd: "fixture" },
    });

    expect(loginResponse.json()).toMatchObject({ mode: "fixture" });
  });

  it("keeps a live lab browse-only without the photo write opt-in", async () => {
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      liveAuthenticate,
      BROWSE_ONLY_LIVE_CONFIG,
    );
    const sessionId = await login(app);

    const session = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });
    expect(session.statusCode).toBe(200);
    expect(session.json()).toMatchObject({ mode: "live" });
    expect(
      (session.json() as { capabilities: Record<string, string> }).capabilities[
        "write.photo.upload"
      ],
    ).toBe("read_only");

    const response = await postSave(app, sessionId, validSaveBody());
    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "WRITE_DISABLED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a browse-only live write before parsing the body", async () => {
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      liveAuthenticate,
      BROWSE_ONLY_LIVE_CONFIG,
    );

    // No session and malformed JSON: the refusal cannot depend on either.
    const response = await app.inject({
      method: "POST",
      url: "/api/write/photo-save",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: "{not-json",
    });

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "WRITE_DISABLED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("keeps the status read available on a browse-only live lab", async () => {
    const { app } = buildLive(
      defaultHandler,
      liveAuthenticate,
      BROWSE_ONLY_LIVE_CONFIG,
    );
    const sessionId = await login(app);

    const response = await postStatus(app, sessionId, {
      operation_id: OPERATION_ID,
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "saved",
      operation_id: OPERATION_ID,
      version: 1,
      file_name: "photo.png",
    });
  });
});

describe("native session handling", () => {
  it("keeps the clinician's native session in process memory only, never SQLite", async () => {
    const { app, db, native } = buildLive();
    const sessionId = await login(app);

    expect(native.get(sessionId)).toEqual({
      sid: NATIVE_SID,
      csrf: NATIVE_CSRF,
    });
    expect(
      db
        .prepare(
          "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%native%'",
        )
        .all(),
    ).toEqual([]);

    const session = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });
    expect(session.statusCode).toBe(200);
    expect(session.body).not.toContain(NATIVE_SID);
    expect(session.body).not.toContain(NATIVE_CSRF);

    const capabilities = (
      session.json() as { capabilities: Record<string, string> }
    ).capabilities;
    expect(capabilities["write.photo.upload"]).toBe("enabled");
  });

  it("enables the upload capability only for a validated live clinician role", async () => {
    const receptionist: Authenticate = async () => ({
      user: { id: "reception@lab.example", displayName: "Rec", roles: ["REC"] },
      native: { sid: NATIVE_SID, csrf: NATIVE_CSRF },
    });
    const { app, fetchImpl } = buildLive(defaultHandler, receptionist);
    const sessionId = await login(app);

    const session = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });
    expect(
      (session.json() as { capabilities: Record<string, string> }).capabilities[
        "write.photo.upload"
      ],
    ).toBe("read_only");

    const response = await postSave(app, sessionId, validSaveBody());
    expect(response.statusCode).toBe(403);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a nurse photo-save before contacting the lab", async () => {
    const nurse: Authenticate = async () => ({
      user: { id: "nurse@lab.example", displayName: "Nurse", roles: ["NUR"] },
      native: { sid: NATIVE_SID, csrf: NATIVE_CSRF },
    });
    const { app, fetchImpl } = buildLive(defaultHandler, nurse);
    const sessionId = await login(app);

    const response = await postSave(app, sessionId, validSaveBody());
    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();

    const session = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });
    expect(
      (session.json() as { capabilities: Record<string, string> }).capabilities[
        "write.photo.upload"
      ],
    ).toBe("read_only");
  });

  it("invalidates the proxy session when the native credential is gone", async () => {
    const { app, native, fetchImpl } = buildLive();
    const sessionId = await login(app);
    native.clear(sessionId);

    const response = await postSave(app, sessionId, validSaveBody());

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(fetchImpl).not.toHaveBeenCalled();
    const session = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });
    expect(session.statusCode).toBe(401);
  });

  it("clears the native credential on logout", async () => {
    const { app, native } = buildLive();
    const sessionId = await login(app);
    const csrf = await issuedWriteCsrf(app, sessionId);

    const logout = await app.inject({
      method: "POST",
      url: "/api/auth/logout",
      headers: {
        origin: TEST_ORIGIN,
        "content-type": "application/json",
        [CSRF_HEADER]: csrf.token,
        cookie: csrf.cookie,
      },
    });
    expect(logout.statusCode).toBe(204);
    expect(native.get(sessionId)).toBeUndefined();
  });
});

describe("save forwarding", () => {
  it("maps the workspace patient to the server-owned native patient and forwards with the native session", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    const response = await postSave(app, sessionId, validSaveBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "pending",
      operation_id: OPERATION_ID,
    });

    const patientCall = fetchImpl.mock.calls.find(([url]) =>
      String(url).startsWith(PATIENT_URL),
    );
    expect(patientCall).toBeDefined();
    expect(
      (patientCall?.[1] as RequestInit | undefined)?.headers,
    ).toMatchObject({
      cookie: `sid=${NATIVE_SID}`,
    });

    const saveCall = fetchImpl.mock.calls.find(
      ([url]) => String(url) === SAVE_URL,
    );
    const init = saveCall?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      cookie: `sid=${NATIVE_SID}`,
      "x-frappe-csrf-token": NATIVE_CSRF,
    });
    const forwarded = JSON.parse(String(init.body));
    expect(forwarded).toEqual({
      payload: {
        kind: "synthetic_original_create",
        request_key: "request-key-0123456789",
        synthetic_patient: "SYNTH-PATIENT-1",
        image_base64: IMAGE_BASE64,
      },
    });
    // Native `save(payload)` takes one payload argument: no flat top-level request.
    expect(forwarded).not.toHaveProperty("kind");
    expect(forwarded).not.toHaveProperty("request_key");
    expect(forwarded).not.toHaveProperty("image_base64");
    expect(init.redirect).toBe("manual");

    // The workspace reference and native name never get confused.
    expect(forwarded.payload.synthetic_patient).not.toBe("PAT-1");
  });

  it("rejects an unmapped or traversal-shaped patient reference before any upstream call", async () => {
    const cases = ["PAT-UNKNOWN", "..", "../SYNTH-PATIENT-1"];
    for (const patient of cases) {
      const { app, fetchImpl } = buildLive();
      const sessionId = await login(app);
      const response = await postSave(
        app,
        sessionId,
        validSaveBody({ synthetic_patient: patient }),
      );
      expect(response.statusCode).toBe(400);
      expect(response.json()).toEqual({ code: "VALIDATION" });
      expect(fetchImpl).not.toHaveBeenCalled();
    }
  });

  it("rejects a native patient the clinician cannot read", async () => {
    const { app, fetchImpl } = buildLive((url) =>
      url === PATIENT_URL
        ? jsonResponse({ exc_type: "PermissionError" }, 403)
        : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await postSave(app, sessionId, validSaveBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl.mock.calls.some(([url]) => String(url) === SAVE_URL)).toBe(
      false,
    );
  });

  it("fails closed when the live patient read is not a matching row", async () => {
    const { app, fetchImpl } = buildLive((url) =>
      url === PATIENT_URL
        ? jsonResponse({ data: { name: "OTHER-PATIENT" } })
        : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await postSave(app, sessionId, validSaveBody());

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    expect(fetchImpl.mock.calls.some(([url]) => String(url) === SAVE_URL)).toBe(
      false,
    );
  });

  it("maps upstream unavailability without retrying the write", async () => {
    const { app, fetchImpl } = buildLive((url) => {
      if (url === SAVE_URL) throw new Error("socket hang up");
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await postSave(app, sessionId, validSaveBody());

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    expect(
      fetchImpl.mock.calls.filter(([url]) => String(url) === SAVE_URL),
    ).toHaveLength(1);
  });

  (
    [
      [401, 401, "AUTH_EXPIRED"],
      [403, 403, "FORBIDDEN"],
      [409, 409, "CONFLICT_STALE"],
      [413, 413, "FILE_TOO_LARGE"],
      [417, 400, "VALIDATION"],
      [429, 429, "RATE_LIMITED"],
      [500, 502, "UPSTREAM_UNAVAILABLE"],
    ] as const
  ).forEach(([nativeStatus, expectedStatus, code]) => {
    it(`maps native ${nativeStatus} to a safe ${code}`, async () => {
      const { app, native, fetchImpl } = buildLive((url) => {
        if (url === SAVE_URL) return jsonResponse({ exc: "x" }, nativeStatus);
        return defaultHandler(url);
      });
      const sessionId = await login(app);

      const response = await postSave(app, sessionId, validSaveBody());

      expect(response.statusCode).toBe(expectedStatus);
      expect(response.json()).toEqual({ code });
      if (nativeStatus === 401) {
        expect(native.get(sessionId)).toBeUndefined();
        const session = await app.inject({
          method: "GET",
          url: "/api/session",
          headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
        });
        expect(session.statusCode).toBe(401);
      }
      if (nativeStatus === 403) {
        expect(native.get(sessionId)).toBeDefined();
      }
      expect(
        fetchImpl.mock.calls.filter(([url]) => String(url) === SAVE_URL),
      ).toHaveLength(1);
    });
  });

  it("never turns a malformed native body into saved", async () => {
    const { app } = buildLive((url) =>
      url === SAVE_URL
        ? jsonResponse({ message: { status: "saved" } })
        : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await postSave(app, sessionId, validSaveBody());

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  });
});

describe("status forwarding", () => {
  it("forwards the operation id and returns only the native outcome", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    const response = await postStatus(app, sessionId, {
      operation_id: OPERATION_ID,
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "saved",
      operation_id: OPERATION_ID,
      version: 1,
      file_name: "photo.png",
    });

    const call = fetchImpl.mock.calls.find(
      ([url]) => String(url) === STATUS_URL,
    );
    const init = call?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      operation_id: OPERATION_ID,
    });
    expect(init.headers).toMatchObject({
      cookie: `sid=${NATIVE_SID}`,
      "x-frappe-csrf-token": NATIVE_CSRF,
    });
  });

  it.each(["pending", "unknown", "conflict", "rejected"] as const)(
    "passes through native %s without claiming saved",
    async (status) => {
      const { app } = buildLive((url) =>
        url === STATUS_URL
          ? jsonResponse({
              message: { status, operation_id: OPERATION_ID },
            })
          : defaultHandler(url),
      );
      const sessionId = await login(app);

      const response = await postStatus(app, sessionId, {
        operation_id: OPERATION_ID,
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        status,
        operation_id: OPERATION_ID,
      });
    },
  );

  it("rejects malformed operation ids and extra fields", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    expect(
      (await postStatus(app, sessionId, { operation_id: "not-an-id" }))
        .statusCode,
    ).toBe(400);
    expect(
      (
        await postStatus(app, sessionId, {
          operation_id: OPERATION_ID,
          patient: "PAT-1",
        })
      ).statusCode,
    ).toBe(400);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("browser CSRF, origin and body ordering", () => {
  it("rejects a cross-origin save before any upstream call", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    const response = await postSave(app, sessionId, validSaveBody(), {
      origin: "https://evil.example",
    });

    expect(response.statusCode).toBe(403);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("rejects a missing or reused browser CSRF token", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);
    const csrf = await issuedWriteCsrf(app, sessionId);

    const missing = await postSave(app, sessionId, validSaveBody(), {
      csrf: { token: "", cookie: csrf.cookie },
    });
    expect(missing.statusCode).toBe(403);

    const first = await postSave(app, sessionId, validSaveBody(), { csrf });
    expect(first.statusCode).toBe(200);
    const callsAfterFirst = fetchImpl.mock.calls.length;

    const reused = await postSave(app, sessionId, validSaveBody(), { csrf });
    expect(reused.statusCode).toBe(403);
    expect(fetchImpl.mock.calls.length).toBe(callsAfterFirst);
  });

  it("rejects a non-JSON content type", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    const response = await postSave(app, sessionId, validSaveBody(), {
      contentType: "text/plain",
    });

    expect(response.statusCode).toBe(415);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("checks auth and CSRF before parsing an oversized body", async () => {
    const { app, fetchImpl } = buildLive();

    // No session at all: the oversized body must not be parsed into a 413.
    const anonymous = await app.inject({
      method: "POST",
      url: "/api/write/photo-save",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: JSON.stringify(
        validSaveBody({ image_base64: "A".repeat(13 * 1024 * 1024) }),
      ),
    });
    expect(anonymous.statusCode).toBe(401);
    expect(anonymous.json()).toEqual({ code: "AUTH_EXPIRED" });

    const sessionId = await login(app);
    const crossOrigin = await app.inject({
      method: "POST",
      url: "/api/write/photo-save",
      headers: {
        origin: "https://evil.example",
        "content-type": "application/json",
        cookie: `${SESSION_COOKIE}=${sessionId}`,
      },
      payload: JSON.stringify(
        validSaveBody({ image_base64: "A".repeat(13 * 1024 * 1024) }),
      ),
    });
    expect(crossOrigin.statusCode).toBe(403);
    expect(crossOrigin.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("request bounds and validation", () => {
  it("rejects unknown fields and invalid shapes before forwarding", async () => {
    const cases: unknown[] = [
      validSaveBody({ owner: "someone-else" }),
      validSaveBody({ kind: "annotation_append" }),
      validSaveBody({ request_key: "short" }),
      validSaveBody({ request_key: "bad key with spaces!" }),
      validSaveBody({ synthetic_patient: "PAT 1" }),
      validSaveBody({ image_base64: "not base64" }),
      validSaveBody({
        image_base64: Buffer.from([0x47, 0x49, 0x46]).toString("base64"),
      }),
      validSaveBody({
        image_base64: Buffer.alloc(8 * 1024 * 1024 + 1, 0x89).toString(
          "base64",
        ),
      }),
    ];

    for (const body of cases) {
      const { app, fetchImpl } = buildLive();
      const sessionId = await login(app);
      const response = await postSave(app, sessionId, body);
      expect(response.statusCode).toBe(400);
      expect(response.json()).toEqual({ code: "VALIDATION" });
      expect(fetchImpl).not.toHaveBeenCalled();
    }
  });

  it("rejects a body above the 12 MiB route bound after auth", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);
    const csrf = await issuedWriteCsrf(app, sessionId);

    const response = await app.inject({
      method: "POST",
      url: "/api/write/photo-save",
      headers: {
        origin: TEST_ORIGIN,
        "content-type": "application/json",
        [CSRF_HEADER]: csrf.token,
        cookie: csrf.cookie,
      },
      payload: JSON.stringify(
        validSaveBody({ image_base64: "A".repeat(13 * 1024 * 1024) }),
      ),
    });

    expect(response.statusCode).toBe(413);
    expect(response.json()).toEqual({ code: "FILE_TOO_LARGE" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("live reads never mix fixture records", () => {
  it("fails closed for every fixture read and private file surface", async () => {
    const { app } = buildLive();
    const sessionId = await login(app);

    for (const url of [
      "/api/read/patients",
      "/api/read/clinical-photos",
      "/api/read/clinical-visits",
      "/api/files/fixture-photo-1",
    ]) {
      const response = await app.inject({
        method: "GET",
        url,
        headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
      });
      expect(response.statusCode).toBe(404);
      expect(response.json()).toEqual({ code: "UNSUPPORTED" });
    }
  });

  it("exposes only verified configured synthetic patient metadata", async () => {
    const { app } = buildLive();
    const sessionId = await login(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-patients",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      items: [
        {
          workspace_patient_id: "PAT-1",
          native_patient_id: "SYNTH-PATIENT-1",
          display_name: "SYNTH-PATIENT-1",
        },
        {
          workspace_patient_id: "PAT-2",
          native_patient_id: "SYNTH-PATIENT-2",
          display_name: "Synthetic Two",
        },
      ],
    });
  });

  it("omits a configured patient the clinician cannot read and never invents one", async () => {
    const { app } = buildLive((url) => {
      if (url === PATIENT_URL) return jsonResponse({}, 403);
      if (url === METADATA_URL) {
        return jsonResponse({ data: { name: "SYNTH-PATIENT-2" } });
      }
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-patients",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      items: [
        {
          workspace_patient_id: "PAT-2",
          native_patient_id: "SYNTH-PATIENT-2",
          display_name: "SYNTH-PATIENT-2",
        },
      ],
    });
  });

  it("rejects malformed patient metadata instead of returning partial results", async () => {
    const { app } = buildLive((url) =>
      url === PATIENT_URL
        ? jsonResponse({ data: { name: "WRONG" } })
        : defaultHandler(url),
    );
    const sessionId = await login(app);
    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-patients",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });
    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  });

  it("invalidates the session on an expired native read", async () => {
    const { app, native } = buildLive((url) =>
      url === PATIENT_URL
        ? jsonResponse({}, 401)
        : url === METADATA_URL
          ? jsonResponse({ data: { name: "SYNTH-PATIENT-2" } })
          : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-patients",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(native.get(sessionId)).toBeUndefined();
  });
});

describe("no secret or payload logging", () => {
  it("keeps native credentials and image bytes out of responses and logs", async () => {
    const stderr = vi
      .spyOn(process.stderr, "write")
      .mockImplementation(() => true);
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    try {
      const { app } = buildLive((url) => {
        if (url === SAVE_URL) return jsonResponse({ exc: "denied" }, 403);
        return defaultHandler(url);
      });
      const sessionId = await login(app);

      const response = await postSave(app, sessionId, validSaveBody());

      const logs = [
        response.body,
        ...stderr.mock.calls.map((call) => String(call[0] ?? "")),
        ...consoleError.mock.calls.map((call) => call.join(" ")),
      ].join("\n");
      expect(logs).not.toContain(NATIVE_SID);
      expect(logs).not.toContain(NATIVE_CSRF);
      expect(logs).not.toContain(IMAGE_BASE64);
      expect(logs).not.toContain("request-key-0123456789");
    } finally {
      stderr.mockRestore();
      consoleError.mockRestore();
    }
  });
});
