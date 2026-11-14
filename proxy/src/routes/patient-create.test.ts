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
import { PatientCreateStore } from "../writes/patient-create-store.js";
import type { Authenticate } from "./auth.js";

const TEST_ORIGIN = "http://localhost:5173";
const LAB_ORIGIN = "https://lab.example";
const PATIENT_CREATE_URL = `${LAB_ORIGIN}/api/resource/Patient`;
const GENDER_URL_PREFIX = `${LAB_ORIGIN}/api/resource/Gender?`;
const LOGGED_USER_URL = `${LAB_ORIGIN}/api/method/frappe.auth.get_logged_user`;

const NATIVE_SID = "native-sid-value";
const NATIVE_CSRF = "native-csrf-value";
const PATIENT_ID = "PAT-9001";

const LIVE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "live",
  livePatientWrites: true,
  live: {
    origin: LAB_ORIGIN,
    timeoutMs: 1_000,
    syntheticPatients: new Map([["PAT-1", "SYNTH-PATIENT-1"]]),
    clinicianRoles: new Set(["Clinic Clinician"]),
    receptionRoles: new Set(["Clinic Reception"]),
  },
};

/** Live lab with the photo opt-in only: patient writes must stay refused. */
const NO_PATIENT_WRITE_CONFIG: AppConfig = {
  ...LIVE_CONFIG,
  livePatientWrites: false,
};

const FIXTURE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

const recAuthenticate: Authenticate = async () => ({
  user: {
    id: "reception@lab.example",
    displayName: "Front Desk",
    roles: ["REC"],
  },
  native: { sid: NATIVE_SID, csrf: NATIVE_CSRF },
});

const docAuthenticate: Authenticate = async () => ({
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

function defaultHandler(url: string, init: RequestInit): Response {
  if (url.startsWith(GENDER_URL_PREFIX)) {
    return jsonResponse({
      data: [{ name: "Female" }, { name: "Male" }, { name: "Other" }],
    });
  }
  if (url === LOGGED_USER_URL) {
    return jsonResponse({ message: "reception@lab.example" });
  }
  if (url === PATIENT_CREATE_URL && init.method === "POST") {
    return jsonResponse({ data: { name: PATIENT_ID } });
  }
  throw new Error(`unexpected upstream call ${url}`);
}

function buildLive(
  handler: Handler = defaultHandler,
  authenticate: Authenticate = recAuthenticate,
  config: AppConfig = LIVE_CONFIG,
  patientCreates?: PatientCreateStore,
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
    ...(patientCreates === undefined ? {} : { patientCreates }),
  });
  openApps.push(app);
  return { app, db, native, fetchImpl };
}

function buildFixture(): Harness {
  const db = new Database(":memory:");
  openDbs.push(db);
  const fetchImpl = vi.fn();
  const app = buildServer({
    config: FIXTURE_CONFIG,
    database: db,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  openApps.push(app);
  return { app, db, native: new NativeCredentialStore(), fetchImpl };
}

async function login(
  app: FastifyInstance,
  payload: Record<string, string> = {
    usr: "reception@lab.example",
    pwd: "secret",
  },
): Promise<{ sessionId: string; body: unknown }> {
  const pre = await app.inject({ method: "GET", url: "/api/auth/csrf" });
  expect(pre.statusCode).toBe(200);
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
    payload,
  });
  expect(loginResponse.statusCode).toBe(200);
  const session = loginResponse.cookies.find(
    (cookie) => cookie.name === SESSION_COOKIE,
  );
  expect(session).toBeDefined();
  return { sessionId: session?.value ?? "", body: loginResponse.json() };
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
    cookie: `${SESSION_COOKIE}=${sessionId}; ${CSRF_COOKIE}=${
      csrfCookie?.value ?? ""
    }`,
  };
}

function validCreateBody(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    request_key: "request-key-0123456789",
    first_name: "Somchai",
    last_name: "Jaidee",
    sex: "Male",
    ...overrides,
  };
}

async function postCreate(
  app: FastifyInstance,
  sessionId: string,
  body: unknown,
  options: {
    csrf?: { token: string; cookie: string };
    origin?: string;
    contentType?: string;
    withCsrf?: boolean;
  } = {},
) {
  const csrf = options.csrf ?? (await issuedWriteCsrf(app, sessionId));
  return app.inject({
    method: "POST",
    url: "/api/write/patient-create",
    headers: {
      origin: options.origin ?? TEST_ORIGIN,
      "content-type": options.contentType ?? "application/json",
      ...(options.withCsrf === false ? {} : { [CSRF_HEADER]: csrf.token }),
      cookie: csrf.cookie,
    },
    payload: body as string,
  });
}

function createCalls(fetchImpl: ReturnType<typeof vi.fn>): unknown[][] {
  return fetchImpl.mock.calls.filter(
    ([url, init]) =>
      String(url) === PATIENT_CREATE_URL &&
      (init as RequestInit | undefined)?.method === "POST",
  );
}

describe("fixture and opt-in boundary", () => {
  it("does not expose either route in fixture mode", async () => {
    const { app, fetchImpl } = buildFixture();

    const create = await app.inject({
      method: "POST",
      url: "/api/write/patient-create",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: validCreateBody(),
    });
    const genders = await app.inject({
      method: "GET",
      url: "/api/live/patient-genders",
    });

    expect(create.statusCode).toBe(404);
    expect(create.json()).toEqual({ code: "UNSUPPORTED" });
    expect(genders.statusCode).toBe(404);
    expect(genders.json()).toEqual({ code: "UNSUPPORTED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses create on a live lab without the patient write opt-in", async () => {
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      recAuthenticate,
      NO_PATIENT_WRITE_CONFIG,
    );
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "WRITE_DISABLED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses create for a live clinician role that is not reception", async () => {
    const { app, fetchImpl } = buildLive(defaultHandler, docAuthenticate);
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses create without a CSRF token", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody(), {
      withCsrf: false,
    });

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses create from an untrusted origin and non-JSON content type", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const crossOrigin = await postCreate(app, sessionId, validCreateBody(), {
      origin: "https://evil.example",
    });
    const wrongType = await postCreate(app, sessionId, validCreateBody(), {
      contentType: "text/plain",
    });

    expect(crossOrigin.statusCode).toBe(403);
    expect(crossOrigin.json()).toEqual({ code: "FORBIDDEN" });
    expect(wrongType.statusCode).toBe(415);
    expect(wrongType.json()).toEqual({ code: "UNSUPPORTED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses create without a session", async () => {
    const { app, fetchImpl } = buildLive();

    const response = await app.inject({
      method: "POST",
      url: "/api/write/patient-create",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: validCreateBody(),
    });

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("capability activation", () => {
  it("enables write.patient.create for an opted-in live reception login only", async () => {
    const live = buildLive();
    const liveLogin = await login(live.app);
    const liveCaps = (
      liveLogin.body as { capabilities: Record<string, string> }
    ).capabilities;
    expect(liveCaps["write.patient.create"]).toBe("enabled");
    // The photo capability keeps its own separate opt-in.
    expect(liveCaps["write.photo.upload"]).toBe("read_only");

    const noOptIn = buildLive(
      defaultHandler,
      recAuthenticate,
      NO_PATIENT_WRITE_CONFIG,
    );
    const noOptInLogin = await login(noOptIn.app);
    expect(
      (noOptInLogin.body as { capabilities: Record<string, string> })
        .capabilities["write.patient.create"],
    ).toBe("read_only");

    const clinician = buildLive(defaultHandler, docAuthenticate);
    const clinicianLogin = await login(clinician.app);
    expect(
      (clinicianLogin.body as { capabilities: Record<string, string> })
        .capabilities["write.patient.create"],
    ).toBe("read_only");

    const fixture = buildFixture();
    const fixtureLogin = await login(fixture.app, {
      usr: "rec",
      pwd: "fixture",
    });
    expect(
      (fixtureLogin.body as { capabilities: Record<string, string> })
        .capabilities["write.patient.create"],
    ).toBe("read_only");
  });

  it("reports the same capability from the session route", async () => {
    const { app } = buildLive();
    const { sessionId } = await login(app);

    const enabled = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });
    expect(
      (enabled.json() as { capabilities: Record<string, string> }).capabilities[
        "write.patient.create"
      ],
    ).toBe("enabled");

    const noOptIn = buildLive(
      defaultHandler,
      recAuthenticate,
      NO_PATIENT_WRITE_CONFIG,
    );
    const noOptInLogin = await login(noOptIn.app);
    const readOnly = await noOptIn.app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${noOptInLogin.sessionId}` },
    });
    expect(
      (readOnly.json() as { capabilities: Record<string, string> })
        .capabilities["write.patient.create"],
    ).toBe("read_only");
  });

  it("drops the capability once the native credential is gone", async () => {
    const { app, native } = buildLive();
    const { sessionId } = await login(app);
    native.clear(sessionId);

    const response = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(
      (response.json() as { capabilities: Record<string, string> })
        .capabilities["write.patient.create"],
    ).toBe("read_only");
  });
});

describe("native sex reference", () => {
  it("returns only native gender names for a live reception session", async () => {
    const { app } = buildLive();
    const { sessionId } = await login(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/live/patient-genders",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      items: ["Female", "Male", "Other"],
    });
  });

  it("requires a reception session", async () => {
    const { app } = buildLive(defaultHandler, docAuthenticate);
    const { sessionId } = await login(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/live/patient-genders",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });

    const anonymous = await app.inject({
      method: "GET",
      url: "/api/live/patient-genders",
    });
    expect(anonymous.statusCode).toBe(401);
  });

  it("reports an unavailable native reference as UPSTREAM_UNAVAILABLE", async () => {
    const { app } = buildLive((url) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ exc: "boom" }, 500);
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/live/patient-genders",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  });

  it("rejects a malformed native reference", async () => {
    const { app } = buildLive((url) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: 5 }] });
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/live/patient-genders",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  });
});

describe("patient create", () => {
  it("creates one native patient and returns a created status", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "created",
      patient_id: PATIENT_ID,
    });

    const calls = createCalls(fetchImpl);
    expect(calls).toHaveLength(1);
    const init = calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      first_name: "Somchai",
      last_name: "Jaidee",
      sex: "Male",
      invite_user: 0,
    });
    const headers = init.headers as Record<string, string>;
    expect(headers.cookie).toBe(`sid=${NATIVE_SID}`);
    expect(headers["x-frappe-csrf-token"]).toBe(NATIVE_CSRF);
  });

  it("omits an absent last name", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postCreate(
      app,
      sessionId,
      validCreateBody({ last_name: undefined }),
    );

    expect(response.statusCode).toBe(200);
    const init = createCalls(fetchImpl)[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      first_name: "Somchai",
      sex: "Male",
      invite_user: 0,
    });
  });

  it("sets invite_user to 0 server-side and refuses a client-supplied value", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());

    expect(response.statusCode).toBe(200);
    const init = createCalls(fetchImpl)[0]?.[1] as RequestInit;
    const forwarded = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(forwarded.invite_user).toBe(0);
    expect(Object.keys(forwarded).sort()).toEqual([
      "first_name",
      "invite_user",
      "last_name",
      "sex",
    ]);

    // The client cannot choose it: the strict schema rejects the extra key and
    // nothing reaches the native site.
    const before = createCalls(fetchImpl).length;
    const clientSupplied = await postCreate(
      app,
      sessionId,
      validCreateBody({
        invite_user: 1,
        request_key: "request-key-9876543210",
      }),
    );

    expect(clientSupplied.statusCode).toBe(400);
    expect(clientSupplied.json()).toEqual({ code: "VALIDATION" });
    expect(createCalls(fetchImpl)).toHaveLength(before);
  });

  it("replays a repeated request key without a second upstream create", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const first = await postCreate(app, sessionId, validCreateBody());
    const second = await postCreate(app, sessionId, validCreateBody());

    expect(first.statusCode).toBe(200);
    expect(second.statusCode).toBe(200);
    expect(second.json()).toEqual({
      status: "created",
      patient_id: PATIENT_ID,
    });
    expect(createCalls(fetchImpl)).toHaveLength(1);
  });

  it("rejects reuse of a request key with a different payload", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    await postCreate(app, sessionId, validCreateBody());
    const conflict = await postCreate(
      app,
      sessionId,
      validCreateBody({ first_name: "Somsri" }),
    );

    expect(conflict.statusCode).toBe(409);
    expect(conflict.json()).toEqual({ code: "CONFLICT_DUPLICATE" });
    expect(createCalls(fetchImpl)).toHaveLength(1);
  });

  it("keeps an unknown outcome unknown without retrying the create", async () => {
    let attempts = 0;
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        attempts += 1;
        throw new TypeError("socket hang up");
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const first = await postCreate(app, sessionId, validCreateBody());
    const second = await postCreate(app, sessionId, validCreateBody());

    expect(first.statusCode).toBe(200);
    expect(first.json()).toEqual({ status: "unknown" });
    expect(second.statusCode).toBe(200);
    expect(second.json()).toEqual({ status: "unknown" });
    expect(attempts).toBe(1);
    expect(createCalls(fetchImpl)).toHaveLength(1);
  });

  it("treats an unreadable success body as an unknown outcome", async () => {
    const { app } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        return new Response("not-json", { status: 200 });
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
  });

  it("maps a native validation failure and allows a corrected retry", async () => {
    let attempts = 0;
    const { app } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        attempts += 1;
        return jsonResponse({ exc_type: "ValidationError" }, 417);
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const rejected = await postCreate(app, sessionId, validCreateBody());
    const retried = await postCreate(app, sessionId, validCreateBody());

    expect(rejected.statusCode).toBe(400);
    expect(rejected.json()).toEqual({ code: "VALIDATION" });
    expect(retried.statusCode).toBe(400);
    expect(attempts).toBe(2);
  });

  it("rejects a sex value the native site does not offer", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postCreate(
      app,
      sessionId,
      validCreateBody({ sex: "Unlisted" }),
    );

    expect(response.statusCode).toBe(400);
    expect(response.json()).toEqual({ code: "VALIDATION" });
    expect(createCalls(fetchImpl)).toHaveLength(0);
  });

  it("rejects a strict body violation before any upstream call", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const extra = await postCreate(
      app,
      sessionId,
      validCreateBody({ patient_id: "client-chosen" }),
    );
    const missingKey = await postCreate(app, sessionId, {
      first_name: "Somchai",
      sex: "Male",
    });

    expect(extra.statusCode).toBe(400);
    expect(extra.json()).toEqual({ code: "VALIDATION" });
    expect(missingKey.statusCode).toBe(400);
    expect(missingKey.json()).toEqual({ code: "VALIDATION" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("invalidates the session on a native 401", async () => {
    const { app } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        return jsonResponse({ message: "Not permitted" }, 401);
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());
    const afterwards = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(afterwards.statusCode).toBe(401);
  });

  it("keeps the session on a live-session permission denial", async () => {
    const { app } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ message: "reception@lab.example" });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        return jsonResponse({ message: "Not permitted" }, 403);
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());
    const afterwards = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(afterwards.statusCode).toBe(200);
  });

  it("maps a native rate limit", async () => {
    const { app } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        return jsonResponse({ message: "Too many requests" }, 429);
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());

    expect(response.statusCode).toBe(429);
    expect(response.json()).toEqual({ code: "RATE_LIMITED" });
  });

  it("stores no client-typed field, only a hash and the native docname", async () => {
    const { app, db } = buildLive();
    const { sessionId } = await login(app);

    await postCreate(app, sessionId, validCreateBody());

    const rows = db.prepare("SELECT * FROM patient_creates").all();
    expect(rows).toHaveLength(1);
    const serialized = JSON.stringify(rows);
    // The payload the clinician typed is only present as a hash. The native
    // docname is stored as-is and may itself be a human name.
    expect(serialized).not.toMatch(/Somchai|Jaidee/);
    expect(serialized).toContain(PATIENT_ID);
    expect(rows[0]).toMatchObject({
      user_id: "reception@lab.example",
      request_key: "request-key-0123456789",
      state: "created",
    });
  });

  it("answers unknown and never re-creates when the outcome cannot be stored", async () => {
    let attempts = 0;
    const db = new Database(":memory:");
    openDbs.push(db);
    const patientCreates = new PatientCreateStore(db);
    vi.spyOn(patientCreates, "markCreated").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    const { app, fetchImpl } = buildLive(
      (url, init) => {
        if (url.startsWith(GENDER_URL_PREFIX)) {
          return jsonResponse({ data: [{ name: "Male" }] });
        }
        if (url === PATIENT_CREATE_URL && init.method === "POST") {
          attempts += 1;
          return jsonResponse({ data: { name: PATIENT_ID } });
        }
        throw new Error(`unexpected upstream call ${url}`);
      },
      recAuthenticate,
      LIVE_CONFIG,
      patientCreates,
    );
    const { sessionId } = await login(app);

    const first = await postCreate(app, sessionId, validCreateBody());
    const second = await postCreate(app, sessionId, validCreateBody());

    // The native document exists, so no failure may be reported; the row stays
    // pending and the repeated key fails closed as unknown.
    expect(first.statusCode).toBe(200);
    expect(first.json()).toEqual({ status: "unknown" });
    expect(second.statusCode).toBe(200);
    expect(second.json()).toEqual({ status: "unknown" });
    expect(attempts).toBe(1);
    expect(createCalls(fetchImpl)).toHaveLength(1);
    expect(
      patientCreates.get("reception@lab.example", "request-key-0123456789"),
    ).toMatchObject({ state: "pending" });
  });

  it("fails before any native create when the outcome row cannot be written", async () => {
    const db = new Database(":memory:");
    openDbs.push(db);
    const patientCreates = new PatientCreateStore(db);
    vi.spyOn(patientCreates, "begin").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      recAuthenticate,
      LIVE_CONFIG,
      patientCreates,
    );
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());

    expect(response.statusCode).toBe(500);
    expect(response.json()).toEqual({ code: "UNKNOWN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("treats a native redirect as an unknown outcome and never retries it", async () => {
    let attempts = 0;
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        attempts += 1;
        return new Response(null, {
          status: 302,
          headers: { location: "/login" },
        });
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const first = await postCreate(app, sessionId, validCreateBody());
    const second = await postCreate(app, sessionId, validCreateBody());

    expect(first.statusCode).toBe(200);
    expect(first.json()).toEqual({ status: "unknown" });
    expect(second.json()).toEqual({ status: "unknown" });
    expect(attempts).toBe(1);
    expect(createCalls(fetchImpl)).toHaveLength(1);
  });

  it("treats a native 5xx as unknown even when the body looks like an error", async () => {
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        return jsonResponse({ exc_type: "ValidationError" }, 503);
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const response = await postCreate(app, sessionId, validCreateBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(createCalls(fetchImpl)).toHaveLength(1);
  });

  it("maps a missing native patient API to unavailable and keeps the key reusable", async () => {
    let attempts = 0;
    const { app } = buildLive((url, init) => {
      if (url.startsWith(GENDER_URL_PREFIX)) {
        return jsonResponse({ data: [{ name: "Male" }] });
      }
      if (url === PATIENT_CREATE_URL && init.method === "POST") {
        attempts += 1;
        return jsonResponse({ message: "Not Found" }, 404);
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const first = await postCreate(app, sessionId, validCreateBody());
    const retried = await postCreate(app, sessionId, validCreateBody());

    expect(first.statusCode).toBe(502);
    expect(first.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    expect(retried.statusCode).toBe(502);
    expect(attempts).toBe(2);
  });
});
