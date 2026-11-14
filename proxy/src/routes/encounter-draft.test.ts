import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppConfig, LiveUpstream } from "../config.js";
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  SESSION_COOKIE,
} from "../security/constants.js";
import { buildServer } from "../server.js";
import { NativeCredentialStore } from "../session/native-store.js";
import { EncounterDraftStore } from "../writes/encounter-draft-store.js";
import type { Authenticate } from "./auth.js";

const TEST_ORIGIN = "http://localhost:5173";
const LAB_ORIGIN = "https://lab.example";
const ENCOUNTER_URL = `${LAB_ORIGIN}/api/resource/Patient%20Encounter`;
const PATIENT_READ_PREFIX = `${LAB_ORIGIN}/api/resource/Patient/`;
const PRACTITIONER_READ_PREFIX = `${LAB_ORIGIN}/api/resource/Healthcare%20Practitioner/`;
const APPOINTMENT_TYPE_READ_PREFIX = `${LAB_ORIGIN}/api/resource/Appointment%20Type/`;
const LOGGED_USER_URL = `${LAB_ORIGIN}/api/method/frappe.auth.get_logged_user`;

const NATIVE_SID = "native-sid-value";
const NATIVE_CSRF = "native-csrf-value";
const ENCOUNTER_ID = "ENC-9001";
const NATIVE_PATIENT = "SYNTH-PATIENT-1";
const PRACTITIONER = "PRACT-0001";
const APPOINTMENT_TYPE = "Synthetic Follow Up";
const REQUEST_KEY = "encounter-request-key-0001";

const LIVE_UPSTREAM: LiveUpstream = {
  origin: LAB_ORIGIN,
  timeoutMs: 1_000,
  syntheticPatients: new Map([
    ["PAT-1", NATIVE_PATIENT],
    ["PAT-2", "SYNTH-PATIENT-2"],
  ]),
  clinicianRoles: new Set(["Clinic Clinician"]),
  receptionRoles: new Set(["Clinic Reception"]),
  encounterAppointmentType: APPOINTMENT_TYPE,
  encounterPractitioners: new Map([["clinician@lab.example", PRACTITIONER]]),
};

const LIVE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "live",
  liveEncounterWrites: true,
  live: LIVE_UPSTREAM,
};

/** Live lab without the encounter write opt-in: the route must stay refused. */
const NO_ENCOUNTER_WRITE_CONFIG: AppConfig = {
  ...LIVE_CONFIG,
  liveEncounterWrites: false,
};

/** Opted in, but no server-owned appointment type or practitioner mapping. */
const UNMAPPED_CONFIG: AppConfig = {
  ...LIVE_CONFIG,
  live: {
    ...LIVE_UPSTREAM,
    encounterAppointmentType: undefined,
    encounterPractitioners: undefined,
  },
};

const FIXTURE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

const docAuthenticate: Authenticate = async () => ({
  user: { id: "clinician@lab.example", displayName: "Dr Lab", roles: ["DOC"] },
  native: { sid: NATIVE_SID, csrf: NATIVE_CSRF },
});

const otherDocAuthenticate: Authenticate = async () => ({
  user: { id: "other@lab.example", displayName: "Dr Other", roles: ["DOC"] },
  native: { sid: NATIVE_SID, csrf: NATIVE_CSRF },
});

const recAuthenticate: Authenticate = async () => ({
  user: {
    id: "reception@lab.example",
    displayName: "Front Desk",
    roles: ["REC"],
  },
  native: { sid: NATIVE_SID, csrf: NATIVE_CSRF },
});

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

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
  if (url.startsWith(PATIENT_READ_PREFIX)) {
    const nativeId = decodeURIComponent(url.slice(PATIENT_READ_PREFIX.length));
    return jsonResponse({
      data: { name: nativeId, patient_name: "Synthetic Patient" },
    });
  }
  if (url.startsWith(PRACTITIONER_READ_PREFIX)) {
    // The installed Marley rows leave `user_id` blank; the mapping is
    // operator-controlled and verified by read + activity, not by ownership.
    return jsonResponse({
      data: {
        name: decodeURIComponent(url.slice(PRACTITIONER_READ_PREFIX.length)),
        user_id: "",
        status: "Active",
      },
    });
  }
  if (url.startsWith(APPOINTMENT_TYPE_READ_PREFIX)) {
    return jsonResponse({
      data: {
        name: decodeURIComponent(
          url.slice(APPOINTMENT_TYPE_READ_PREFIX.length),
        ),
      },
    });
  }
  if (url === LOGGED_USER_URL) {
    return jsonResponse({ message: "clinician@lab.example" });
  }
  if (url === ENCOUNTER_URL && init.method === "POST") {
    return jsonResponse({ data: { name: ENCOUNTER_ID } });
  }
  throw new Error(`unexpected upstream call ${url}`);
}

function buildLive(
  handler: Handler = defaultHandler,
  authenticate: Authenticate = docAuthenticate,
  config: AppConfig = LIVE_CONFIG,
  encounterDrafts?: EncounterDraftStore,
  database?: Database.Database,
): Harness {
  const db = database ?? new Database(":memory:");
  if (database === undefined) {
    openDbs.push(db);
  }
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
    ...(encounterDrafts === undefined ? {} : { encounterDrafts }),
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
    usr: "clinician@lab.example",
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

function validDraftBody(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return { request_key: REQUEST_KEY, patient_reference: "PAT-1", ...overrides };
}

async function postDraft(
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
    url: "/api/write/encounter-draft",
    headers: {
      origin: options.origin ?? TEST_ORIGIN,
      "content-type": options.contentType ?? "application/json",
      ...(options.withCsrf === false ? {} : { [CSRF_HEADER]: csrf.token }),
      cookie: csrf.cookie,
    },
    payload: body as string,
  });
}

/** Durable outcome row for the fixture clinician's request key. */
function encounterState(
  db: Database.Database,
  requestKey: string = REQUEST_KEY,
): unknown {
  return db
    .prepare(
      "SELECT state FROM encounter_drafts WHERE user_id = ? AND request_key = ?",
    )
    .get("clinician@lab.example", requestKey);
}

function encounterCalls(fetchImpl: ReturnType<typeof vi.fn>): unknown[][] {
  return fetchImpl.mock.calls.filter(
    ([url, init]) =>
      String(url) === ENCOUNTER_URL &&
      (init as RequestInit | undefined)?.method === "POST",
  );
}

describe("fixture and opt-in boundary", () => {
  it("does not expose the route in fixture mode", async () => {
    const { app, fetchImpl } = buildFixture();

    const response = await app.inject({
      method: "POST",
      url: "/api/write/encounter-draft",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: validDraftBody(),
    });

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({ code: "UNSUPPORTED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses the draft on a live lab without the encounter write opt-in", async () => {
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      docAuthenticate,
      NO_ENCOUNTER_WRITE_CONFIG,
    );
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "WRITE_DISABLED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a reception session even with the opt-in", async () => {
    const { app, fetchImpl } = buildLive(defaultHandler, recAuthenticate);
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a missing CSRF token, cross origin, non-JSON and anonymous requests", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const noCsrf = await postDraft(app, sessionId, validDraftBody(), {
      withCsrf: false,
    });
    const crossOrigin = await postDraft(app, sessionId, validDraftBody(), {
      origin: "https://evil.example",
    });
    const wrongType = await postDraft(app, sessionId, validDraftBody(), {
      contentType: "text/plain",
    });
    const anonymous = await app.inject({
      method: "POST",
      url: "/api/write/encounter-draft",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: validDraftBody(),
    });

    expect(noCsrf.statusCode).toBe(403);
    expect(noCsrf.json()).toEqual({ code: "FORBIDDEN" });
    expect(crossOrigin.statusCode).toBe(403);
    expect(crossOrigin.json()).toEqual({ code: "FORBIDDEN" });
    expect(wrongType.statusCode).toBe(415);
    expect(wrongType.json()).toEqual({ code: "UNSUPPORTED" });
    expect(anonymous.statusCode).toBe(401);
    expect(anonymous.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("rejects a strict body violation before any upstream call", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const extra = await postDraft(
      app,
      sessionId,
      validDraftBody({ practitioner: PRACTITIONER }),
    );
    const missingKey = await postDraft(app, sessionId, {
      patient_reference: "PAT-1",
    });

    expect(extra.statusCode).toBe(400);
    expect(extra.json()).toEqual({ code: "VALIDATION" });
    expect(missingKey.statusCode).toBe(400);
    expect(missingKey.json()).toEqual({ code: "VALIDATION" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("capability activation", () => {
  it("enables write.encounter.draft for an opted-in live DOC login only", async () => {
    const liveLogin = await login(buildLive().app);
    expect(
      (liveLogin.body as { capabilities: Record<string, string> }).capabilities[
        "write.encounter.draft"
      ],
    ).toBe("enabled");

    const noOptIn = buildLive(
      defaultHandler,
      docAuthenticate,
      NO_ENCOUNTER_WRITE_CONFIG,
    );
    const noOptInLogin = await login(noOptIn.app);
    expect(
      (noOptInLogin.body as { capabilities: Record<string, string> })
        .capabilities["write.encounter.draft"],
    ).toBe("read_only");

    const reception = buildLive(defaultHandler, recAuthenticate);
    const receptionLogin = await login(reception.app);
    expect(
      (receptionLogin.body as { capabilities: Record<string, string> })
        .capabilities["write.encounter.draft"],
    ).toBe("read_only");

    const fixture = buildFixture();
    const fixtureLogin = await login(fixture.app, {
      usr: "rec",
      pwd: "fixture",
    });
    expect(
      (fixtureLogin.body as { capabilities: Record<string, string> })
        .capabilities["write.encounter.draft"],
    ).toBe("read_only");
  });

  it("reports the same capability from the session route", async () => {
    const { app } = buildLive();
    const { sessionId } = await login(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(
      (response.json() as { capabilities: Record<string, string> })
        .capabilities["write.encounter.draft"],
    ).toBe("enabled");
  });
});

describe("encounter draft create", () => {
  it("creates one draft encounter with only server-owned values", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "created",
      encounter_id: ENCOUNTER_ID,
    });

    const calls = encounterCalls(fetchImpl);
    expect(calls).toHaveLength(1);
    const init = calls[0]?.[1] as RequestInit;
    // Never an appointment link, never orders, never a submit.
    expect(JSON.parse(String(init.body))).toEqual({
      patient: NATIVE_PATIENT,
      practitioner: PRACTITIONER,
      appointment_type: APPOINTMENT_TYPE,
      docstatus: 0,
      submit_orders_on_save: 0,
    });
    const headers = init.headers as Record<string, string>;
    expect(headers.cookie).toBe(`sid=${NATIVE_SID}`);
    expect(headers["x-frappe-csrf-token"]).toBe(NATIVE_CSRF);

    // The configured appointment type must first be readable by this session.
    const typeReads = fetchImpl.mock.calls.filter(([url]) =>
      String(url).startsWith(APPOINTMENT_TYPE_READ_PREFIX),
    );
    expect(typeReads).toHaveLength(1);
    expect(String(typeReads[0]?.[0])).toBe(
      `${APPOINTMENT_TYPE_READ_PREFIX}${encodeURIComponent(APPOINTMENT_TYPE)}`,
    );
    const typeInit = typeReads[0]?.[1] as RequestInit | undefined;
    expect(typeInit?.headers as Record<string, string>).toMatchObject({
      cookie: `sid=${NATIVE_SID}`,
    });
  });

  it("uses the caller's own native session, not another clinician's", async () => {
    const { app, native, fetchImpl } = buildLive();
    const { sessionId } = await login(app);
    native.set(
      sessionId,
      { sid: "second-clinician-sid", csrf: "second-clinician-csrf" },
      Date.now() + 60_000,
    );

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(200);
    const init = encounterCalls(fetchImpl)[0]?.[1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.cookie).toBe("sid=second-clinician-sid");
    expect(headers["x-frappe-csrf-token"]).toBe("second-clinician-csrf");
  });

  it("rejects a patient reference outside the server-owned allow-list", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postDraft(
      app,
      sessionId,
      validDraftBody({ patient_reference: "PAT-UNKNOWN" }),
    );

    expect(response.statusCode).toBe(400);
    expect(response.json()).toEqual({ code: "VALIDATION" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("reauthorizes the patient with the calling DOC native session", async () => {
    const { app, fetchImpl } = buildLive((url) => {
      if (url.startsWith(PATIENT_READ_PREFIX)) {
        return jsonResponse({ message: "Not permitted" }, 403);
      }
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ message: "clinician@lab.example" });
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(encounterCalls(fetchImpl)).toHaveLength(0);
  });

  it("verifies a mapped practitioner whose native user_id is blank", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "created",
      encounter_id: ENCOUNTER_ID,
    });
    // Exactly one practitioner read: the mapped one, with the caller's session.
    const reads = fetchImpl.mock.calls.filter(([url]) =>
      String(url).startsWith(PRACTITIONER_READ_PREFIX),
    );
    expect(reads).toHaveLength(1);
    expect(String(reads[0]?.[0])).toBe(
      `${PRACTITIONER_READ_PREFIX}${PRACTITIONER}`,
    );
    const readInit = reads[0]?.[1] as RequestInit | undefined;
    expect(readInit?.headers as Record<string, string>).toMatchObject({
      cookie: `sid=${NATIVE_SID}`,
    });
  });

  it("reads the installed Healthcare Practitioner resource exactly", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(200);
    const urls = fetchImpl.mock.calls.map(([url]) => String(url));
    expect(
      urls.filter((url) =>
        url.startsWith(`${LAB_ORIGIN}/api/resource/Healthcare%20Practitioner/`),
      ),
    ).toEqual([
      `${LAB_ORIGIN}/api/resource/Healthcare%20Practitioner/${PRACTITIONER}`,
    ]);
    // The bare Practitioner doctype is not installed on Marley.
    expect(
      urls.filter((url) =>
        url.startsWith(`${LAB_ORIGIN}/api/resource/Practitioner/`),
      ),
    ).toEqual([]);
  });

  it("maps a practitioner read 5xx to a fail-closed upstream error", async () => {
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(PRACTITIONER_READ_PREFIX)) {
        return jsonResponse({ exc: "boom" }, 500);
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    expect(encounterCalls(fetchImpl)).toHaveLength(0);
  });

  it("fails closed when the mapped practitioner is not active", async () => {
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(PRACTITIONER_READ_PREFIX)) {
        return jsonResponse({
          data: { name: PRACTITIONER, user_id: "", status: "Inactive" },
        });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(encounterCalls(fetchImpl)).toHaveLength(0);
  });

  it("fails closed when the native read returns a different practitioner", async () => {
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(PRACTITIONER_READ_PREFIX)) {
        return jsonResponse({
          data: { name: "PRACT-OTHER", user_id: "", status: "Active" },
        });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(encounterCalls(fetchImpl)).toHaveLength(0);
    // No alternate practitioner is ever tried.
    const reads = fetchImpl.mock.calls.filter(([url]) =>
      String(url).startsWith(PRACTITIONER_READ_PREFIX),
    );
    expect(reads).toHaveLength(1);
  });

  it("fails closed when practitioner activity is unknown", async () => {
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(PRACTITIONER_READ_PREFIX)) {
        return jsonResponse({ data: { name: PRACTITIONER, user_id: "" } });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(encounterCalls(fetchImpl)).toHaveLength(0);
  });

  it("fails closed when the appointment type is not readable", async () => {
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(APPOINTMENT_TYPE_READ_PREFIX)) {
        return jsonResponse({ message: "Not permitted" }, 403);
      }
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ message: "clinician@lab.example" });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());
    const afterwards = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(afterwards.statusCode).toBe(200);
    expect(encounterCalls(fetchImpl)).toHaveLength(0);
  });

  it("fails closed when the appointment type read returns another record", async () => {
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(APPOINTMENT_TYPE_READ_PREFIX)) {
        return jsonResponse({ data: { name: "Other Type" } });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(encounterCalls(fetchImpl)).toHaveLength(0);
  });

  it("invalidates the session when the appointment type read expires", async () => {
    const { app } = buildLive((url, init) => {
      if (url.startsWith(APPOINTMENT_TYPE_READ_PREFIX)) {
        return jsonResponse({ message: "Not permitted" }, 401);
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());
    const afterwards = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(afterwards.statusCode).toBe(401);
  });

  it("fails closed when the mapped practitioner read is denied", async () => {
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(PRACTITIONER_READ_PREFIX)) {
        return jsonResponse({ message: "Not permitted" }, 403);
      }
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ message: "clinician@lab.example" });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());
    const afterwards = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(afterwards.statusCode).toBe(200);
    expect(encounterCalls(fetchImpl)).toHaveLength(0);
  });

  it("fails closed without a practitioner mapping for the calling clinician", async () => {
    const { app, fetchImpl } = buildLive(defaultHandler, otherDocAuthenticate);
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("fails closed without a configured appointment type", async () => {
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      docAuthenticate,
      UNMAPPED_CONFIG,
    );
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("replays a repeated request key without a second upstream create", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    const first = await postDraft(app, sessionId, validDraftBody());
    const second = await postDraft(app, sessionId, validDraftBody());

    expect(first.json()).toEqual({
      status: "created",
      encounter_id: ENCOUNTER_ID,
    });
    expect(second.json()).toEqual({
      status: "created",
      encounter_id: ENCOUNTER_ID,
    });
    expect(encounterCalls(fetchImpl)).toHaveLength(1);
  });

  it("rejects reuse of a request key for a different patient", async () => {
    const { app, fetchImpl } = buildLive();
    const { sessionId } = await login(app);

    await postDraft(app, sessionId, validDraftBody());
    const conflict = await postDraft(
      app,
      sessionId,
      validDraftBody({ patient_reference: "PAT-2" }),
    );

    expect(conflict.statusCode).toBe(409);
    expect(conflict.json()).toEqual({ code: "CONFLICT_DUPLICATE" });
    expect(encounterCalls(fetchImpl)).toHaveLength(1);
  });

  it("keeps an unknown outcome unknown without retrying the create", async () => {
    let attempts = 0;
    const { app } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        attempts += 1;
        throw new TypeError("socket hang up");
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const first = await postDraft(app, sessionId, validDraftBody());
    const second = await postDraft(app, sessionId, validDraftBody());

    expect(first.json()).toEqual({ status: "unknown" });
    expect(second.json()).toEqual({ status: "unknown" });
    expect(attempts).toBe(1);
  });

  it("treats an unreadable success body as unknown", async () => {
    const { app } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        return new Response("not-json", { status: 200 });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
  });

  // Every status this route used to release after dispatch. None of them is
  // proof that nothing committed: an intermediary can answer any of them while
  // the buffered native create still lands. The uncertain intent is retained.
  const AMBIGUOUS_POST_STATUSES = [400, 403, 404, 405, 409, 413, 417, 422, 429];

  it.each(AMBIGUOUS_POST_STATUSES)(
    "keeps the intent lock when the native create answers %i",
    async (status) => {
      let attempts = 0;
      const { app, db } = buildLive((url, init) => {
        if (url === ENCOUNTER_URL && init.method === "POST") {
          attempts += 1;
          return jsonResponse({ exc_type: "ValidationError" }, status);
        }
        return defaultHandler(url, init);
      });
      const { sessionId } = await login(app);

      const first = await postDraft(app, sessionId, validDraftBody());
      expect(first.statusCode).toBe(200);
      expect(first.json()).toEqual({ status: "unknown" });
      expect(encounterState(db)).toEqual({ state: "unknown" });

      // The same key replays the uncertain outcome; a fresh key is refused by
      // the retained intent. Neither sends a second native create.
      const replay = await postDraft(app, sessionId, validDraftBody());
      const freshKey = await postDraft(
        app,
        sessionId,
        validDraftBody({ request_key: "encounter-request-key-0002" }),
      );

      expect(replay.statusCode).toBe(200);
      expect(replay.json()).toEqual({ status: "unknown" });
      expect(freshKey.statusCode).toBe(409);
      expect(freshKey.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
      expect(attempts).toBe(1);
    },
  );

  it("invalidates the session but keeps the intent when the native create answers 401", async () => {
    let attempts = 0;
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        attempts += 1;
        return jsonResponse({ message: "Not permitted" }, 401);
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());
    const afterwards = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    // The session is still invalidated, but the write outcome stays unknown
    // because the POST may already have reached the site.
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(afterwards.statusCode).toBe(401);
    expect(encounterState(db)).toEqual({ state: "unknown" });

    // The dead session cannot replay the key and never re-creates.
    const replay = await postDraft(app, sessionId, validDraftBody());
    expect(replay.statusCode).toBe(401);
    expect(replay.json()).toEqual({ code: "AUTH_EXPIRED" });

    // A fresh session for the same user is still blocked by the retained intent.
    const { sessionId: nextSession } = await login(app);
    const freshKey = await postDraft(
      app,
      nextSession,
      validDraftBody({ request_key: "encounter-request-key-0002" }),
    );
    expect(freshKey.statusCode).toBe(409);
    expect(freshKey.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
    expect(attempts).toBe(1);
  });

  it("keeps the session and the intent on a live-session permission denial", async () => {
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        return jsonResponse({ message: "Not permitted" }, 403);
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());
    const afterwards = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(afterwards.statusCode).toBe(200);
    expect(encounterState(db)).toEqual({ state: "unknown" });
  });

  it("invalidates the session when the 403 probe proves it dead, keeping the intent", async () => {
    let attempts = 0;
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        attempts += 1;
        return jsonResponse({ message: "Not permitted" }, 403);
      }
      if (url === LOGGED_USER_URL) {
        // The read-only probe says the native session is gone, so the 403 is
        // not a live-session permission denial.
        return jsonResponse({ message: "Not permitted" }, 401);
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());
    const afterwards = await app.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    // Still unknown, never a definite non-create: the probe only drops the dead
    // session, it does not decide the write outcome.
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(afterwards.statusCode).toBe(401);
    expect(encounterState(db)).toEqual({ state: "unknown" });

    // Re-login as the same clinician: the retained intent still blocks a new key
    // and no second native create is sent.
    const { sessionId: nextSession } = await login(app);
    const freshKey = await postDraft(
      app,
      nextSession,
      validDraftBody({ request_key: "encounter-request-key-0002" }),
    );

    expect(freshKey.statusCode).toBe(409);
    expect(freshKey.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
    expect(attempts).toBe(1);
  });

  it("fails before any native create when the outcome row cannot be written", async () => {
    const db = new Database(":memory:");
    openDbs.push(db);
    const encounterDrafts = new EncounterDraftStore(db);
    vi.spyOn(encounterDrafts, "begin").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      docAuthenticate,
      LIVE_CONFIG,
      encounterDrafts,
    );
    const { sessionId } = await login(app);

    const response = await postDraft(app, sessionId, validDraftBody());

    expect(response.statusCode).toBe(500);
    expect(response.json()).toEqual({ code: "UNKNOWN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("answers unknown and never re-creates when the outcome cannot be stored", async () => {
    let attempts = 0;
    const db = new Database(":memory:");
    openDbs.push(db);
    const encounterDrafts = new EncounterDraftStore(db);
    vi.spyOn(encounterDrafts, "markCreated").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    const { app, fetchImpl } = buildLive(
      (url, init) => {
        if (url === ENCOUNTER_URL && init.method === "POST") {
          attempts += 1;
          return jsonResponse({ data: { name: ENCOUNTER_ID } });
        }
        return defaultHandler(url, init);
      },
      docAuthenticate,
      LIVE_CONFIG,
      encounterDrafts,
    );
    const { sessionId } = await login(app);

    const first = await postDraft(app, sessionId, validDraftBody());
    const second = await postDraft(app, sessionId, validDraftBody());

    expect(first.statusCode).toBe(200);
    expect(first.json()).toEqual({ status: "unknown" });
    expect(second.json()).toEqual({ status: "unknown" });
    expect(attempts).toBe(1);
    expect(
      encounterDrafts.get("clinician@lab.example", REQUEST_KEY),
    ).toMatchObject({ state: "pending" });
    expect(encounterCalls(fetchImpl)).toHaveLength(1);
  });

  it("treats a native redirect and a 5xx as unknown without retrying", async () => {
    let redirects = 0;
    const redirect = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        redirects += 1;
        return new Response(null, {
          status: 302,
          headers: { location: "/login" },
        });
      }
      return defaultHandler(url, init);
    });
    const redirectSession = await login(redirect.app);
    const first = await postDraft(
      redirect.app,
      redirectSession.sessionId,
      validDraftBody(),
    );
    const second = await postDraft(
      redirect.app,
      redirectSession.sessionId,
      validDraftBody(),
    );
    expect(first.json()).toEqual({ status: "unknown" });
    expect(second.json()).toEqual({ status: "unknown" });
    expect(redirects).toBe(1);

    let failures = 0;
    const failing = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        failures += 1;
        return jsonResponse({ exc_type: "ValidationError" }, 503);
      }
      return defaultHandler(url, init);
    });
    const failingSession = await login(failing.app);
    const failure = await postDraft(
      failing.app,
      failingSession.sessionId,
      validDraftBody(),
    );
    expect(failure.json()).toEqual({ status: "unknown" });
    expect(failures).toBe(1);
  });

  it("migrates an encounter table without the intent column", () => {
    const db = new Database(":memory:");
    openDbs.push(db);
    db.exec(`
      CREATE TABLE encounter_drafts (
        user_id TEXT NOT NULL,
        request_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        state TEXT NOT NULL,
        encounter_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, request_key)
      );
    `);
    db.prepare(
      "INSERT INTO encounter_drafts (user_id, request_key, payload_hash, state, encounter_id, created_at, updated_at) VALUES (?, ?, ?, 'unknown', NULL, 1, 1)",
    ).run("clinician@lab.example", REQUEST_KEY, "sha256:legacy");

    const store = new EncounterDraftStore(db);

    // The pre-existing key keeps its unresolved outcome and is still replayable.
    expect(
      store.begin("clinician@lab.example", REQUEST_KEY, "sha256:legacy", "fp"),
    ).toEqual({
      kind: "replay",
      state: "unknown",
    });
    // A legacy row carries no fingerprint, so it cannot lock the patient.
    expect(
      store.begin(
        "clinician@lab.example",
        "encounter-request-key-0002",
        "sha256:next",
        "fp",
      ),
    ).toEqual({ kind: "new" });
  });

  it("stores only the server-resolved draft fingerprint and encounter name", async () => {
    const { app, db } = buildLive();
    const { sessionId } = await login(app);

    await postDraft(app, sessionId, validDraftBody());

    const rows = db.prepare("SELECT * FROM encounter_drafts").all();
    expect(rows).toHaveLength(1);
    const serialized = JSON.stringify(rows);
    expect(serialized).not.toContain(NATIVE_PATIENT);
    expect(serialized).not.toContain(PRACTITIONER);
    expect(serialized).toContain(ENCOUNTER_ID);
    expect(rows[0]).toMatchObject({
      user_id: "clinician@lab.example",
      request_key: REQUEST_KEY,
      state: "created",
    });
    // The patient intent lock stores an opaque fingerprint, never a name.
    const fingerprint = (rows[0] as { patient_fingerprint?: unknown })
      .patient_fingerprint;
    expect(typeof fingerprint).toBe("string");
    expect(fingerprint).toMatch(/^[a-f0-9]{64}$/);
  });

  it("blocks a second draft for the same patient while the first is unresolved", async () => {
    let attempts = 0;
    const { app, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        attempts += 1;
        throw new TypeError("socket hang up");
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const first = await postDraft(app, sessionId, validDraftBody());
    const second = await postDraft(
      app,
      sessionId,
      validDraftBody({ request_key: "encounter-request-key-0002" }),
    );
    // The original key is still replayable and is never blocked by the lock.
    const replay = await postDraft(app, sessionId, validDraftBody());

    expect(first.statusCode).toBe(200);
    expect(first.json()).toEqual({ status: "unknown" });
    expect(second.statusCode).toBe(409);
    expect(second.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
    expect(replay.statusCode).toBe(200);
    expect(replay.json()).toEqual({ status: "unknown" });
    expect(attempts).toBe(1);
    expect(encounterCalls(fetchImpl)).toHaveLength(1);
  });

  it("allows only one of two concurrent keys for the same patient", async () => {
    let attempts = 0;
    const { app, fetchImpl } = buildLive(async (url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        attempts += 1;
        // Hold the first create open so the second request really starts while
        // the first intent is still pending.
        await new Promise((resolve) => setTimeout(resolve, 25));
        return jsonResponse({ data: { name: ENCOUNTER_ID } });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const [one, two] = await Promise.all([
      postDraft(app, sessionId, validDraftBody()),
      postDraft(
        app,
        sessionId,
        validDraftBody({ request_key: "encounter-request-key-0002" }),
      ),
    ]);

    const statuses = [one.statusCode, two.statusCode].sort();
    expect(statuses).toEqual([200, 409]);
    const blocked = one.statusCode === 409 ? one : two;
    expect(blocked.json()).toEqual({ code: "INTENT_PENDING" });
    expect(attempts).toBe(1);
    expect(encounterCalls(fetchImpl)).toHaveLength(1);
  });

  it("keeps the intent lock across a process restart", async () => {
    const first = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        throw new TypeError("socket hang up");
      }
      return defaultHandler(url, init);
    });
    const firstSession = await login(first.app);
    const unresolved = await postDraft(
      first.app,
      firstSession.sessionId,
      validDraftBody(),
    );
    expect(unresolved.json()).toEqual({ status: "unknown" });

    // Simulated restart: a fresh store, server and session over the same
    // durable database must still see the unresolved intent.
    const restarted = buildLive(
      defaultHandler,
      docAuthenticate,
      LIVE_CONFIG,
      undefined,
      first.db,
    );
    const restartedSession = await login(restarted.app);
    const blocked = await postDraft(
      restarted.app,
      restartedSession.sessionId,
      validDraftBody({ request_key: "encounter-request-key-0002" }),
    );

    expect(blocked.statusCode).toBe(409);
    expect(blocked.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
    expect(encounterCalls(restarted.fetchImpl)).toHaveLength(0);
  });

  it("releases the intent lock after a pre-dispatch refusal", async () => {
    let attempts = 0;
    let refuse = true;
    const { app, db } = buildLive((url, init) => {
      if (url.startsWith(PATIENT_READ_PREFIX)) {
        // The native read cannot confirm access; no create is sent.
        return refuse
          ? jsonResponse({ exc: "unavailable" }, 500)
          : defaultHandler(url, init);
      }
      if (url === ENCOUNTER_URL && init.method === "POST") {
        attempts += 1;
        return jsonResponse({ data: { name: ENCOUNTER_ID } });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const refused = await postDraft(app, sessionId, validDraftBody());
    expect(refused.statusCode).toBe(502);
    expect(encounterState(db)).toBeUndefined();

    refuse = false;
    const nextIntent = await postDraft(
      app,
      sessionId,
      validDraftBody({ request_key: "encounter-request-key-0002" }),
    );

    expect(nextIntent.statusCode).toBe(200);
    expect(nextIntent.json()).toEqual({
      status: "created",
      encounter_id: ENCOUNTER_ID,
    });
    expect(attempts).toBe(1);
  });

  it("allows a later legitimate draft once the earlier one was created", async () => {
    let attempts = 0;
    const { app } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        attempts += 1;
        return jsonResponse({ data: { name: `${ENCOUNTER_ID}-${attempts}` } });
      }
      return defaultHandler(url, init);
    });
    const { sessionId } = await login(app);

    const first = await postDraft(app, sessionId, validDraftBody());
    const second = await postDraft(
      app,
      sessionId,
      validDraftBody({ request_key: "encounter-request-key-0002" }),
    );

    expect(first.json()).toEqual({
      status: "created",
      encounter_id: `${ENCOUNTER_ID}-1`,
    });
    expect(second.json()).toEqual({
      status: "created",
      encounter_id: `${ENCOUNTER_ID}-2`,
    });
    expect(attempts).toBe(2);
  });

  it("scopes the intent lock to one user", async () => {
    const twoClinicianConfig: AppConfig = {
      ...LIVE_CONFIG,
      live: {
        ...LIVE_UPSTREAM,
        encounterPractitioners: new Map([
          ["clinician@lab.example", PRACTITIONER],
          ["other@lab.example", "PRACT-0002"],
        ]),
      },
    };
    let attempts = 0;
    const handler: Handler = (url, init) => {
      if (url === ENCOUNTER_URL && init.method === "POST") {
        attempts += 1;
        if (attempts === 1) throw new TypeError("socket hang up");
        return jsonResponse({ data: { name: ENCOUNTER_ID } });
      }
      return defaultHandler(url, init);
    };
    const first = buildLive(handler, docAuthenticate, twoClinicianConfig);
    const firstSession = await login(first.app);
    const unresolved = await postDraft(
      first.app,
      firstSession.sessionId,
      validDraftBody(),
    );
    expect(unresolved.json()).toEqual({ status: "unknown" });

    const other = buildLive(
      handler,
      otherDocAuthenticate,
      twoClinicianConfig,
      undefined,
      first.db,
    );
    const otherSession = await login(other.app);
    const allowed = await postDraft(
      other.app,
      otherSession.sessionId,
      validDraftBody(),
    );

    expect(allowed.statusCode).toBe(200);
    expect(allowed.json()).toEqual({
      status: "created",
      encounter_id: ENCOUNTER_ID,
    });
  });
});
