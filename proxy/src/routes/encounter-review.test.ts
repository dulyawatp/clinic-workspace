import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
import {
  EncounterDraftStore,
  hashEncounterDraftPayload,
  patientIntentFingerprint,
} from "../writes/encounter-draft-store.js";
import {
  EncounterReviewStore,
  reviewOperationKey,
} from "../writes/encounter-review-store.js";
import type { Authenticate } from "./auth.js";

const TEST_ORIGIN = "http://localhost:5173";
const LAB_ORIGIN = "https://lab.example";
const ENCOUNTER_PATH_PREFIX = `${LAB_ORIGIN}/api/resource/Patient%20Encounter/`;
const PATIENT_READ_PREFIX = `${LAB_ORIGIN}/api/resource/Patient/`;
const LOGGED_USER_URL = `${LAB_ORIGIN}/api/method/frappe.auth.get_logged_user`;

const NATIVE_SID = "native-sid-value";
const NATIVE_CSRF = "native-csrf-value";
const ENCOUNTER_ID = "ENC-9001";
const NATIVE_PATIENT = "SYNTH-PATIENT-1";
const PRACTITIONER = "PRACT-0001";
const APPOINTMENT_TYPE = "Synthetic Follow Up";
const REQUEST_KEY = "encounter-request-key-0001";
const DOC_USER = "clinician@lab.example";
const MODIFIED = "2026-11-13 09:00:00.000000";
const NEW_MODIFIED = "2026-11-13 09:05:00.000000";
const REVIEW_TEXT = "Blood pressure stable.\nReviewed medication list.";
const ENCOUNTER_URL = `${ENCOUNTER_PATH_PREFIX}${ENCOUNTER_ID}`;

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
  encounterPractitioners: new Map([[DOC_USER, PRACTITIONER]]),
};

const LIVE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "live",
  liveEncounterWrites: true,
  live: LIVE_UPSTREAM,
};

const NO_WRITE_CONFIG: AppConfig = {
  ...LIVE_CONFIG,
  liveEncounterWrites: false,
};

const FIXTURE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

const docAuthenticate: Authenticate = async () => ({
  user: { id: DOC_USER, displayName: "Dr Lab", roles: ["DOC"] },
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

/** One database shared by two server instances, as two processes would be. */
function createSharedDb(): Database.Database {
  const db = new Database(":memory:");
  openDbs.push(db);
  return db;
}

/** Serves the exact requested encounter docname, for multi-encounter tests. */
function encounterByIdHandler(): Handler {
  return (url, init) => {
    if (url.startsWith(ENCOUNTER_PATH_PREFIX)) {
      const id = decodeURIComponent(url.slice(ENCOUNTER_PATH_PREFIX.length));
      return jsonResponse({ data: nativeEncounter({ name: id }) });
    }
    return defaultHandler(url, init);
  };
}

async function closeApp(app: FastifyInstance): Promise<void> {
  const index = openApps.indexOf(app);
  if (index >= 0) openApps.splice(index, 1);
  await app.close();
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function nativeEncounter(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    name: ENCOUNTER_ID,
    patient: NATIVE_PATIENT,
    docstatus: 0,
    appointment: null,
    drug_prescription: [],
    lab_test_prescription: [],
    procedure_prescription: [],
    therapies: [],
    submit_orders_on_save: 0,
    encounter_comment: "Existing review",
    modified: MODIFIED,
    ...overrides,
  };
}

function defaultHandler(url: string, init: RequestInit): Response {
  if (url.startsWith(PATIENT_READ_PREFIX)) {
    return jsonResponse({
      data: {
        name: decodeURIComponent(url.slice(PATIENT_READ_PREFIX.length)),
        patient_name: "Synthetic Patient",
      },
    });
  }
  if (url === LOGGED_USER_URL) {
    return jsonResponse({ message: DOC_USER });
  }
  if (url === ENCOUNTER_URL && (init.method ?? "GET") === "GET") {
    return jsonResponse({ data: nativeEncounter() });
  }
  if (url === ENCOUNTER_URL && init.method === "PUT") {
    return jsonResponse({
      data: nativeEncounter({
        encounter_comment: REVIEW_TEXT,
        modified: NEW_MODIFIED,
      }),
    });
  }
  throw new Error(`unexpected upstream call ${init.method ?? "GET"} ${url}`);
}

function buildLive(
  handler: Handler = defaultHandler,
  authenticate: Authenticate = docAuthenticate,
  config: AppConfig = LIVE_CONFIG,
  database?: Database.Database,
): Harness {
  const db = database ?? new Database(":memory:");
  if (database === undefined) {
    openDbs.push(db);
  }
  const fetchImpl = vi.fn(
    async (input: string | URL | Request, init?: RequestInit) =>
      handler(String(input), init ?? {}),
  );
  const app = buildServer({
    config,
    database: db,
    authenticate,
    native: new NativeCredentialStore(),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  openApps.push(app);
  return { app, db, fetchImpl };
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
  return { app, db, fetchImpl };
}

/** Seeds the caller's own created draft; the store is the only id source. */
function seedCreatedDraft(
  db: Database.Database,
  userId = DOC_USER,
  requestKey = REQUEST_KEY,
  encounterId = ENCOUNTER_ID,
): void {
  const store = new EncounterDraftStore(db);
  const begun = store.begin(
    userId,
    requestKey,
    hashEncounterDraftPayload({
      patient: NATIVE_PATIENT,
      practitioner: PRACTITIONER,
      appointmentType: APPOINTMENT_TYPE,
    }),
    patientIntentFingerprint(NATIVE_PATIENT),
  );
  expect(begun.kind).toBe("new");
  store.markCreated(userId, requestKey, encounterId);
}

async function login(
  app: FastifyInstance,
  payload: Record<string, string> = { usr: DOC_USER, pwd: "secret" },
): Promise<string> {
  const pre = await app.inject({ method: "GET", url: "/api/auth/csrf" });
  expect(pre.statusCode).toBe(200);
  const preCsrf = pre.cookies.find((cookie) => cookie.name === CSRF_COOKIE);
  const response = await app.inject({
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
  expect(response.statusCode).toBe(200);
  const session = response.cookies.find(
    (cookie) => cookie.name === SESSION_COOKIE,
  );
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
    cookie: `${SESSION_COOKIE}=${sessionId}; ${CSRF_COOKIE}=${
      csrfCookie?.value ?? ""
    }`,
  };
}

async function getReview(
  app: FastifyInstance,
  sessionId: string,
  query = `request_key=${REQUEST_KEY}`,
) {
  return app.inject({
    method: "GET",
    url: `/api/live/encounter-review?${query}`,
    headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
  });
}

function validWriteBody(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    request_key: REQUEST_KEY,
    review_details: REVIEW_TEXT,
    modified: MODIFIED,
    ...overrides,
  };
}

async function postReview(
  app: FastifyInstance,
  sessionId: string,
  body: unknown,
  options: {
    origin?: string;
    contentType?: string;
    withCsrf?: boolean;
  } = {},
) {
  const csrf = await issuedWriteCsrf(app, sessionId);
  return app.inject({
    method: "POST",
    url: "/api/write/encounter-review",
    headers: {
      origin: options.origin ?? TEST_ORIGIN,
      "content-type": options.contentType ?? "application/json",
      ...(options.withCsrf === false ? {} : { [CSRF_HEADER]: csrf.token }),
      cookie: csrf.cookie,
    },
    payload: body as string,
  });
}

function calls(
  fetchImpl: ReturnType<typeof vi.fn>,
  method: string,
): unknown[][] {
  return fetchImpl.mock.calls.filter(
    ([url, init]) =>
      String(url) === ENCOUNTER_URL &&
      ((init as RequestInit | undefined)?.method ?? "GET") === method,
  );
}

describe("feature boundary", () => {
  it("does not expose either route in fixture mode", async () => {
    const { app, fetchImpl } = buildFixture();

    const read = await app.inject({
      method: "GET",
      url: `/api/live/encounter-review?request_key=${REQUEST_KEY}`,
    });
    const write = await app.inject({
      method: "POST",
      url: "/api/write/encounter-review",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: validWriteBody(),
    });

    expect(read.statusCode).toBe(404);
    expect(read.json()).toEqual({ code: "UNSUPPORTED" });
    expect(write.statusCode).toBe(404);
    expect(write.json()).toEqual({ code: "UNSUPPORTED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses the read and write without the encounter opt-in", async () => {
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      docAuthenticate,
      NO_WRITE_CONFIG,
    );
    const sessionId = await login(app);

    const read = await getReview(app, sessionId);
    const write = await postReview(app, sessionId, validWriteBody());

    expect(read.statusCode).toBe(403);
    expect(read.json()).toEqual({ code: "WRITE_DISABLED" });
    expect(write.statusCode).toBe(403);
    expect(write.json()).toEqual({ code: "WRITE_DISABLED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a non-clinician session", async () => {
    const { app, fetchImpl } = buildLive(defaultHandler, recAuthenticate);
    const sessionId = await login(app, {
      usr: "reception@lab.example",
      pwd: "secret",
    });

    const read = await getReview(app, sessionId);
    const write = await postReview(app, sessionId, validWriteBody());

    expect(read.statusCode).toBe(403);
    expect(read.json()).toEqual({ code: "FORBIDDEN" });
    expect(write.statusCode).toBe(403);
    expect(write.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses anonymous, cross-origin, non-JSON and CSRF-less writes before any upstream call", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    const crossOrigin = await postReview(app, sessionId, validWriteBody(), {
      origin: "https://evil.example",
    });
    const wrongType = await postReview(app, sessionId, validWriteBody(), {
      contentType: "text/plain",
    });
    const noCsrf = await postReview(app, sessionId, validWriteBody(), {
      withCsrf: false,
    });
    const anonymous = await app.inject({
      method: "POST",
      url: "/api/write/encounter-review",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: validWriteBody(),
    });

    expect(crossOrigin.statusCode).toBe(403);
    expect(crossOrigin.json()).toEqual({ code: "FORBIDDEN" });
    expect(wrongType.statusCode).toBe(415);
    expect(wrongType.json()).toEqual({ code: "UNSUPPORTED" });
    expect(noCsrf.statusCode).toBe(403);
    expect(noCsrf.json()).toEqual({ code: "FORBIDDEN" });
    expect(anonymous.statusCode).toBe(401);
    expect(anonymous.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("request key resolution", () => {
  it("rejects a malformed or repeated request key before any upstream call", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const shortKey = await getReview(app, sessionId, "request_key=short");
    const extraParam = await getReview(
      app,
      sessionId,
      `request_key=${REQUEST_KEY}&request_key=other`,
    );
    const missing = await getReview(app, sessionId, "patient=SYNTH-PATIENT-1");

    expect(shortKey.statusCode).toBe(400);
    expect(shortKey.json()).toEqual({ code: "VALIDATION" });
    expect(extraParam.statusCode).toBe(400);
    expect(missing.statusCode).toBe(400);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("returns NOT_FOUND for a key this caller never created", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const unknownKey = await getReview(
      app,
      sessionId,
      "request_key=encounter-request-key-9999",
    );

    expect(unknownKey.statusCode).toBe(404);
    expect(unknownKey.json()).toEqual({ code: "NOT_FOUND" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("returns NOT_FOUND for another clinician's key and never reads it natively", async () => {
    const { app, db, fetchImpl } = buildLive(
      defaultHandler,
      otherDocAuthenticate,
    );
    seedCreatedDraft(db, DOC_USER);
    const sessionId = await login(app);

    const read = await getReview(app, sessionId);
    const write = await postReview(app, sessionId, validWriteBody());

    expect(read.statusCode).toBe(404);
    expect(read.json()).toEqual({ code: "NOT_FOUND" });
    expect(write.statusCode).toBe(404);
    expect(write.json()).toEqual({ code: "NOT_FOUND" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("returns NOT_FOUND while the draft outcome is still unresolved", async () => {
    const { app, db, fetchImpl } = buildLive();
    const store = new EncounterDraftStore(db);
    store.begin(
      DOC_USER,
      REQUEST_KEY,
      hashEncounterDraftPayload({
        patient: NATIVE_PATIENT,
        practitioner: PRACTITIONER,
        appointmentType: APPOINTMENT_TYPE,
      }),
      patientIntentFingerprint(NATIVE_PATIENT),
    );
    const sessionId = await login(app);

    const read = await getReview(app, sessionId);
    const write = await postReview(app, sessionId, validWriteBody());

    expect(read.statusCode).toBe(404);
    expect(write.statusCode).toBe(404);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("read", () => {
  it("returns the stored review details and version for a safe draft", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await getReview(app, sessionId);

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      review_details: "Existing review",
      modified: MODIFIED,
      write_state: "clear",
    });
    const [url, init] = calls(fetchImpl, "GET")[0] as [string, RequestInit];
    expect(url).toBe(ENCOUNTER_URL);
    expect(init.method ?? "GET").toBe("GET");
    expect(init.headers).toEqual({
      accept: "application/json",
      cookie: `sid=${NATIVE_SID}`,
    });
    expect(init.redirect).toBe("manual");
  });

  it("normalizes an absent native comment to an empty string", async () => {
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL) {
        return jsonResponse({
          data: nativeEncounter({ encounter_comment: null }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await getReview(app, sessionId);

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      review_details: "",
      modified: MODIFIED,
      write_state: "clear",
    });
  });

  it("accepts a draft whose nullable comment field is omitted entirely", async () => {
    // Observed on the installed site: a full resource GET omits
    // `encounter_comment` while it is null (confirmed separately by
    // `frappe.client.get_value`). Only this one nullable field may be absent.
    const row = nativeEncounter();
    delete row.encounter_comment;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        return jsonResponse({
          data: nativeEncounter({
            encounter_comment: REVIEW_TEXT,
            modified: NEW_MODIFIED,
          }),
        });
      }
      if (url === ENCOUNTER_URL) {
        return jsonResponse({ data: row });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const read = await getReview(app, sessionId);

    expect(read.statusCode).toBe(200);
    expect(read.json()).toEqual({
      review_details: "",
      modified: MODIFIED,
      write_state: "clear",
    });

    // The first write from an omitted comment is allowed and echoes the text.
    const write = await postReview(app, sessionId, validWriteBody());
    expect(write.statusCode).toBe(200);
    expect(write.json()).toEqual({ status: "saved", modified: NEW_MODIFIED });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("refuses rows that are not standalone drafts", async () => {
    const unsafeRows: Array<Record<string, unknown>> = [
      { docstatus: 1 },
      { docstatus: 2 },
      { appointment: "APPT-0001" },
      { drug_prescription: [{ parent: ENCOUNTER_ID }] },
      { lab_test_prescription: [{ parent: ENCOUNTER_ID }] },
      { procedure_prescription: [{ parent: ENCOUNTER_ID }] },
      { therapies: [{ parent: ENCOUNTER_ID }] },
      { submit_orders_on_save: 1 },
      { encounter_comment: "bad\u0000text" },
      { encounter_comment: "x".repeat(2001) },
    ];

    for (const overrides of unsafeRows) {
      const { app, db, fetchImpl } = buildLive((url, init) => {
        if (url === ENCOUNTER_URL) {
          return jsonResponse({ data: nativeEncounter(overrides) });
        }
        return defaultHandler(url, init);
      });
      seedCreatedDraft(db);
      const sessionId = await login(app);

      const response = await getReview(app, sessionId);

      expect(response.statusCode).toBe(409);
      expect(response.json()).toEqual({ code: "UNSAFE_ENCOUNTER" });
      expect(calls(fetchImpl, "PUT")).toHaveLength(0);
    }
  });

  it("fails closed when scope-critical appointment or order fields are absent", async () => {
    const unprovenRows: Array<Record<string, unknown>> = [];

    const without = (field: string): Record<string, unknown> => {
      const row = nativeEncounter();
      delete row[field];
      return row;
    };

    unprovenRows.push(
      // An absent flag or child table is NOT proof of an empty value.
      without("docstatus"),
      without("submit_orders_on_save"),
      without("drug_prescription"),
      without("lab_test_prescription"),
      without("procedure_prescription"),
      without("therapies"),
      { drug_prescription: null },
      { therapies: "none" },
    );

    for (const row of unprovenRows) {
      const { app, db, fetchImpl } = buildLive((url, init) => {
        if (url === ENCOUNTER_URL) {
          return jsonResponse({ data: row });
        }
        return defaultHandler(url, init);
      });
      seedCreatedDraft(db);
      const sessionId = await login(app);

      const response = await getReview(app, sessionId);

      expect(response.statusCode).toBe(502);
      expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
      expect(calls(fetchImpl, "PUT")).toHaveLength(0);
    }
  });

  it("refuses a draft whose native patient is outside the allow-list", async () => {
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL) {
        return jsonResponse({
          data: nativeEncounter({ patient: "NOT-ALLOW-LISTED" }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await getReview(app, sessionId);

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    const patientReads = fetchImpl.mock.calls.filter(([url]) =>
      String(url).startsWith(PATIENT_READ_PREFIX),
    );
    expect(patientReads).toHaveLength(0);
  });

  it("reauthorizes the patient with the caller's own session", async () => {
    const { app, db } = buildLive((url, init) => {
      if (url.startsWith(PATIENT_READ_PREFIX)) {
        return jsonResponse({ code: "FORBIDDEN" }, 403);
      }
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ message: DOC_USER });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await getReview(app, sessionId);

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
  });

  it("fails closed on an expired native session", async () => {
    const { app, db, fetchImpl } = buildLive((url) => {
      if (url === ENCOUNTER_URL) return jsonResponse({}, 401);
      throw new Error("unexpected upstream call");
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await getReview(app, sessionId);

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
    // The proxy session is invalidated; a replay is unauthenticated.
    const replay = await getReview(app, sessionId);
    expect(replay.statusCode).toBe(401);
  });

  it("fails closed on an unreadable or mismatched native row", async () => {
    const shapes: unknown[] = [
      null,
      {},
      { data: { ...nativeEncounter(), name: "OTHER-ENC" } },
      { data: { ...nativeEncounter(), modified: "yesterday" } },
      { data: { ...nativeEncounter(), patient: "../etc" } },
    ];

    for (const body of shapes) {
      const { app, db } = buildLive((url, init) => {
        if (url === ENCOUNTER_URL) return jsonResponse(body);
        return defaultHandler(url, init);
      });
      seedCreatedDraft(db);
      const sessionId = await login(app);

      const response = await getReview(app, sessionId);

      expect(response.statusCode).toBe(502);
      expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    }
  });

  it("marks the response no-store", async () => {
    const { app, db } = buildLive();
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await getReview(app, sessionId);

    expect(response.headers["cache-control"]).toBe("no-store");
  });
});

describe("write", () => {
  it("sends exactly the review fields to the caller's own draft", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "saved",
      modified: NEW_MODIFIED,
    });

    const [url, init] = calls(fetchImpl, "PUT")[0] as [string, RequestInit];
    expect(url).toBe(ENCOUNTER_URL);
    expect(init.method).toBe("PUT");
    expect(init.redirect).toBe("manual");
    expect(init.headers).toEqual({
      accept: "application/json",
      "content-type": "application/json",
      cookie: `sid=${NATIVE_SID}`,
      "x-frappe-csrf-token": NATIVE_CSRF,
    });
    expect(JSON.parse(String(init.body))).toEqual({
      encounter_comment: REVIEW_TEXT,
      modified: MODIFIED,
      submit_orders_on_save: 0,
    });
  });

  it("does not claim saved when the native response does not echo the exact text", async () => {
    let puts = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        return jsonResponse({
          data: nativeEncounter({
            encounter_comment: "Different text",
            modified: NEW_MODIFIED,
          }),
        });
      }
      if (url === ENCOUNTER_URL) {
        return jsonResponse({
          data: nativeEncounter({
            encounter_comment:
              puts === 0 ? "Existing review" : "Different text",
            modified: puts === 0 ? MODIFIED : NEW_MODIFIED,
          }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("does not claim saved when the native response keeps the base version", async () => {
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        // Echoes our text but no version change: the write is not proven.
        return jsonResponse({
          data: nativeEncounter({ encounter_comment: REVIEW_TEXT }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("treats a scope change during the write as unknown, never saved", async () => {
    let puts = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        return jsonResponse({
          data: nativeEncounter({
            encounter_comment: REVIEW_TEXT,
            modified: NEW_MODIFIED,
            appointment: "APPT-0001",
          }),
        });
      }
      if (url === ENCOUNTER_URL) {
        // The pre-read is a safe draft; the appointment appears only with the
        // successful write response.
        return jsonResponse({
          data: nativeEncounter(puts === 0 ? {} : { appointment: "APPT-0001" }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    const record = new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID);
    expect(record?.state).toBe("unknown");
  });

  it("treats a scope change found during verification as unknown, not stale", async () => {
    let puts = 0;
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        return jsonResponse({ exc_type: "TimestampMismatchError" }, 417);
      }
      if (url === ENCOUNTER_URL) {
        return puts === 0
          ? jsonResponse({ data: nativeEncounter() })
          : jsonResponse({
              data: nativeEncounter({
                docstatus: 1,
                encounter_comment: REVIEW_TEXT,
                modified: NEW_MODIFIED,
              }),
            });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
  });

  it("invalidates the native session when verification expires but still answers unknown", async () => {
    let puts = 0;
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        return jsonResponse({}, 500);
      }
      if (url === ENCOUNTER_URL) {
        return puts === 0
          ? jsonResponse({ data: nativeEncounter() })
          : jsonResponse({}, 401);
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    // The dead native session is gone; the truth about the write is not changed.
    const replay = await getReview(app, sessionId);
    expect(replay.statusCode).toBe(401);
    expect(replay.json()).toEqual({ code: "AUTH_EXPIRED" });
  });

  it("refuses stale write versions before any native mutation", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(
      app,
      sessionId,
      validWriteBody({ modified: "2026-11-13 08:00:00.000000" }),
    );

    expect(response.statusCode).toBe(409);
    expect(response.json()).toEqual({ code: "CONFLICT_STALE" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
  });

  it("refuses an unsafe row before any native mutation", async () => {
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL) {
        return jsonResponse({
          data: nativeEncounter({ appointment: "APPT-0001" }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(409);
    expect(response.json()).toEqual({ code: "UNSAFE_ENCOUNTER" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
  });

  it("validates the strict body and review text before any upstream call", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const bodies: unknown[] = [
      validWriteBody({ extra: "field" }),
      validWriteBody({ modified: "not-a-timestamp" }),
      validWriteBody({ modified: undefined }),
      validWriteBody({ review_details: 42 }),
      validWriteBody({ review_details: `bell\u0007` }),
      validWriteBody({ review_details: "\u202Ereversed" }),
      validWriteBody({ review_details: "\uD800lone" }),
      validWriteBody({ review_details: "x".repeat(2001) }),
      validWriteBody({ request_key: "short" }),
    ];

    for (const body of bodies) {
      const response = await postReview(app, sessionId, body);
      expect(response.statusCode).toBe(400);
      expect(response.json()).toEqual({ code: "VALIDATION" });
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  /** A second, separately seeded intent for the same encounter. */
  const SECOND_REQUEST_KEY = "encounter-request-key-0002";

  // Post-dispatch statuses prove nothing about the write: an intermediary can
  // answer any of them while the buffered PUT still commits. None may release
  // the durable intent, and none may allow a second native write.
  const AMBIGUOUS_STATUSES = [400, 403, 404, 405, 422, 429] as const;

  describe.each(AMBIGUOUS_STATUSES)("post-dispatch native %i", (status) => {
    it("keeps the intent unknown and never replays the native write", async () => {
      const { app, db, fetchImpl } = buildLive((url, init) => {
        if (url === ENCOUNTER_URL && init.method === "PUT") {
          return jsonResponse({ exc_type: "ValidationError" }, status);
        }
        return defaultHandler(url, init);
      });
      seedCreatedDraft(db);
      seedCreatedDraft(db, DOC_USER, SECOND_REQUEST_KEY, ENCOUNTER_ID);
      const sessionId = await login(app);

      const response = await postReview(app, sessionId, validWriteBody());

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ status: "unknown" });
      expect(calls(fetchImpl, "PUT")).toHaveLength(1);
      expect(
        new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
      ).toBe("unknown");

      // The same intent repeats its answer without a second native write.
      const replay = await postReview(app, sessionId, validWriteBody());
      expect(replay.statusCode).toBe(200);
      expect(replay.json()).toEqual({ status: "unknown" });

      // A fresh key carries the same base version, so it is the same intent:
      // the same answer and still no second native write.
      const fresh = await postReview(
        app,
        sessionId,
        validWriteBody({ request_key: SECOND_REQUEST_KEY }),
      );
      expect(fresh.statusCode).toBe(200);
      expect(fresh.json()).toEqual({ status: "unknown" });
      expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    });
  });

  it("keeps the intent unknown on a native 401 and still drops the dead session", async () => {
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        return jsonResponse({ exc_type: "AuthenticationError" }, 401);
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");

    // The dead native session is still invalidated.
    const read = await getReview(app, sessionId);
    expect(read.statusCode).toBe(401);
    expect(read.json()).toEqual({ code: "AUTH_EXPIRED" });

    // The retained intent still blocks a fresh session: the same intent replays
    // its unknown answer and never reaches the native document again.
    const nextSessionId = await login(app);
    const retry = await postReview(app, nextSessionId, validWriteBody());
    expect(retry.statusCode).toBe(200);
    expect(retry.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("keeps the intent unknown on a 403 whose probe proves the session dead", async () => {
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        return jsonResponse({ exc_type: "PermissionError" }, 403);
      }
      // The read-only probe after the refusal proves the session is gone.
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ exc_type: "AuthenticationError" }, 403);
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");

    // A refusal that proves the session dead still invalidates it.
    const read = await getReview(app, sessionId);
    expect(read.statusCode).toBe(401);
    expect(read.json()).toEqual({ code: "AUTH_EXPIRED" });

    // The retained intent still blocks a fresh session with no second write.
    const nextSessionId = await login(app);
    const retry = await postReview(app, nextSessionId, validWriteBody());
    expect(retry.statusCode).toBe(200);
    expect(retry.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("treats a 417 as unknown when the verification read proves no change", async () => {
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        return jsonResponse({ exc_type: "ValidationError" }, 417);
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    expect(calls(fetchImpl, "GET")).toHaveLength(2);
  });

  it("treats a 417 with a foreign version as unknown and keeps the lock", async () => {
    let puts = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        return jsonResponse({ exc_type: "TimestampMismatchError" }, 417);
      }
      if (url === ENCOUNTER_URL) {
        // The pre-check read still shows the client's base version; only the
        // post-failure read shows the foreign one.
        return puts === 0
          ? jsonResponse({ data: nativeEncounter() })
          : jsonResponse({
              data: nativeEncounter({
                encounter_comment: "Someone else wrote this",
                modified: "2026-11-13 09:30:00.000000",
              }),
            });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    // A foreign version is visible, but the write may still be in flight behind
    // an intermediary: the outcome stays unknown and the lock is kept.
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");

    // A later intent for the moved version cannot write either.
    const blocked = await postReview(
      app,
      sessionId,
      validWriteBody({ modified: "2026-11-13 09:30:00.000000" }),
    );
    expect(blocked.statusCode).toBe(409);
    expect(blocked.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("does not claim saved from a read when the response body carries no proof", async () => {
    let puts = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        return new Response("", { status: 200 });
      }
      if (url === ENCOUNTER_URL) {
        return puts === 0
          ? jsonResponse({ data: nativeEncounter() })
          : jsonResponse({
              data: nativeEncounter({
                encounter_comment: REVIEW_TEXT,
                modified: NEW_MODIFIED,
              }),
            });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");
  });

  it("keeps the lock when an intermediary 502 is followed by a foreign version", async () => {
    let puts = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        // Gateway failure: the origin may still commit the buffered request.
        return jsonResponse({ code: "BAD_GATEWAY" }, 502);
      }
      if (url === ENCOUNTER_URL) {
        return puts === 0
          ? jsonResponse({ data: nativeEncounter() })
          : jsonResponse({
              data: nativeEncounter({
                encounter_comment: "A concurrent clinician wrote this",
                modified: "2026-11-13 09:40:00.000000",
              }),
            });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    // No stale claim, no release: the durable intent still blocks.
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");

    const sameIntent = await postReview(app, sessionId, validWriteBody());
    // The base version is provably gone, so the read-side check refuses before
    // the lock is even consulted — and it must not release the intent.
    expect(sameIntent.statusCode).toBe(409);
    expect(sameIntent.json()).toEqual({ code: "CONFLICT_STALE" });
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");

    const movedIntent = await postReview(
      app,
      sessionId,
      validWriteBody({ modified: "2026-11-13 09:40:00.000000" }),
    );
    expect(movedIntent.statusCode).toBe(409);
    expect(movedIntent.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("treats a redirect as unknown even when a read shows our text", async () => {
    let puts = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        return new Response("", {
          status: 302,
          headers: { location: "/login" },
        });
      }
      if (url === ENCOUNTER_URL) {
        return puts === 0
          ? jsonResponse({ data: nativeEncounter() })
          : jsonResponse({
              data: nativeEncounter({
                encounter_comment: REVIEW_TEXT,
                modified: NEW_MODIFIED,
              }),
            });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("fails closed as unknown for transport errors and server errors, never replaying", async () => {
    const failures: Array<() => Response> = [
      () => {
        throw new Error("socket hang up");
      },
      () => new Response("", { status: 500 }),
      () => new Response("", { status: 302, headers: { location: "/login" } }),
      () => new Response("<html>", { status: 200 }),
    ];

    for (const fail of failures) {
      const { app, db, fetchImpl } = buildLive((url, init) => {
        if (url === ENCOUNTER_URL && init.method === "PUT") return fail();
        return defaultHandler(url, init);
      });
      seedCreatedDraft(db);
      const sessionId = await login(app);

      const response = await postReview(app, sessionId, validWriteBody());

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({ status: "unknown" });
      expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    }
  });

  it("fails closed when the verification read itself cannot prove the outcome", async () => {
    let puts = 0;
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        return jsonResponse({}, 409);
      }
      if (url === ENCOUNTER_URL) {
        return puts === 0
          ? jsonResponse({ data: nativeEncounter() })
          : jsonResponse({}, 401);
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "unknown" });
  });

  it("keeps the submitted text out of the error body and out of local storage", async () => {
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        return jsonResponse({ message: REVIEW_TEXT }, 500);
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await postReview(app, sessionId, validWriteBody());

    expect(response.body).not.toContain("Blood pressure");
    const dump = db.serialize().toString("utf8");
    expect(dump).not.toContain("Blood pressure");
    expect(dump).not.toContain(REVIEW_TEXT);
  });
});

/** Ambiguous outcome: the PUT may have committed, the verification read cannot tell. */
function unknownOutcomeHandler(): Handler {
  return (url, init) => {
    if (url === ENCOUNTER_URL && init.method === "PUT") {
      return jsonResponse({ exc_type: "ValidationError" }, 417);
    }
    return defaultHandler(url, init);
  };
}

describe("omitted nullable appointment", () => {
  const GET_VALUE_PREFIX = `${LAB_ORIGIN}/api/method/frappe.client.get_value`;

  function withoutAppointment(): Record<string, unknown> {
    const row = nativeEncounter();
    delete row.appointment;
    return row;
  }

  function probeHandler(
    probe: (url: string, init: RequestInit) => Response,
    encounter: Record<string, unknown> = withoutAppointment(),
    onPut?: () => Response,
  ): Handler {
    return (url, init) => {
      if (url.startsWith(GET_VALUE_PREFIX)) {
        return probe(url, init);
      }
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        return onPut === undefined
          ? jsonResponse({ data: encounter })
          : onPut();
      }
      if (url === ENCOUNTER_URL) {
        return jsonResponse({ data: encounter });
      }
      return defaultHandler(url, init);
    };
  }

  function probeMessages(
    fetchImpl: ReturnType<typeof vi.fn>,
  ): Array<[string, RequestInit]> {
    return fetchImpl.mock.calls.filter(([url]) =>
      String(url).startsWith(GET_VALUE_PREFIX),
    ) as Array<[string, RequestInit]>;
  }

  it("accepts an omitted appointment only when the probe confirms explicit null", async () => {
    const { app, db, fetchImpl } = buildLive(
      probeHandler(() =>
        jsonResponse({
          message: {
            name: ENCOUNTER_ID,
            appointment: null,
            modified: MODIFIED,
          },
        }),
      ),
    );
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const read = await getReview(app, sessionId);

    expect(read.statusCode).toBe(200);
    expect(read.json()).toEqual({
      review_details: "Existing review",
      modified: MODIFIED,
      write_state: "clear",
    });

    const [url, init] = probeMessages(fetchImpl)[0] as [string, RequestInit];
    const parsed = new URL(url);
    expect(parsed.pathname).toBe("/api/method/frappe.client.get_value");
    expect(parsed.searchParams.get("doctype")).toBe("Patient Encounter");
    expect(JSON.parse(parsed.searchParams.get("filters") ?? "")).toEqual({
      name: ENCOUNTER_ID,
    });
    expect(parsed.searchParams.get("fieldname")).toBe(
      "name,appointment,modified",
    );
    expect(init.method ?? "GET").toBe("GET");
    expect(init.headers).toEqual({
      accept: "application/json",
      cookie: `sid=${NATIVE_SID}`,
    });
  });

  it("saves a definite 2xx with an omitted nullable appointment after confirming scope", async () => {
    // Actual Marley shape: the successful PUT echoes the text and the new
    // version but omits the null appointment, so scope is confirmed at exactly
    // that version before the write is reported as saved.
    let saved = false;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(GET_VALUE_PREFIX)) {
        return jsonResponse({
          message: {
            name: ENCOUNTER_ID,
            appointment: null,
            modified: saved ? NEW_MODIFIED : MODIFIED,
          },
        });
      }
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        saved = true;
        const row = withoutAppointment();
        row.encounter_comment = REVIEW_TEXT;
        row.modified = NEW_MODIFIED;
        return jsonResponse({ data: row });
      }
      if (url === ENCOUNTER_URL) {
        const row = withoutAppointment();
        row.encounter_comment = saved ? REVIEW_TEXT : "Existing review";
        row.modified = saved ? NEW_MODIFIED : MODIFIED;
        return jsonResponse({ data: row });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const write = await postReview(app, sessionId, validWriteBody());

    expect(write.statusCode).toBe(200);
    expect(write.json()).toEqual({ status: "saved", modified: NEW_MODIFIED });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    // Pre-read + scope confirmation, both by the same caller.
    expect(probeMessages(fetchImpl)).toHaveLength(2);
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID),
    ).toBeUndefined();
  });

  it("stays unknown when the scope confirmation finds a linked appointment", async () => {
    let puts = 0;
    let probes = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url.startsWith(GET_VALUE_PREFIX)) {
        probes += 1;
        return jsonResponse({
          message: {
            name: ENCOUNTER_ID,
            appointment: probes === 1 ? null : "APPT-0001",
            modified: puts === 0 ? MODIFIED : NEW_MODIFIED,
          },
        });
      }
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        const row = withoutAppointment();
        row.encounter_comment = REVIEW_TEXT;
        row.modified = NEW_MODIFIED;
        return jsonResponse({ data: row });
      }
      if (url === ENCOUNTER_URL) {
        const row = withoutAppointment();
        row.encounter_comment = puts === 0 ? "Existing review" : REVIEW_TEXT;
        row.modified = puts === 0 ? MODIFIED : NEW_MODIFIED;
        return jsonResponse({ data: row });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const write = await postReview(app, sessionId, validWriteBody());

    expect(write.statusCode).toBe(200);
    expect(write.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");
  });

  it("stays unknown when the scope confirmation reads a different version", async () => {
    let puts = 0;
    let probes = 0;
    const { app, db } = buildLive((url, init) => {
      if (url.startsWith(GET_VALUE_PREFIX)) {
        probes += 1;
        return jsonResponse({
          message: {
            name: ENCOUNTER_ID,
            appointment: null,
            modified: probes === 1 ? MODIFIED : "2026-11-13 09:50:00.000000",
          },
        });
      }
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        const row = withoutAppointment();
        row.encounter_comment = REVIEW_TEXT;
        row.modified = NEW_MODIFIED;
        return jsonResponse({ data: row });
      }
      if (url === ENCOUNTER_URL) {
        const row = withoutAppointment();
        row.encounter_comment = puts === 0 ? "Existing review" : REVIEW_TEXT;
        row.modified = puts === 0 ? MODIFIED : NEW_MODIFIED;
        return jsonResponse({ data: row });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const write = await postReview(app, sessionId, validWriteBody());

    expect(write.statusCode).toBe(200);
    expect(write.json()).toEqual({ status: "unknown" });
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");
  });

  it("stays unknown when the scope confirmation shows a linked appointment in full", async () => {
    let puts = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        const row = withoutAppointment();
        row.encounter_comment = REVIEW_TEXT;
        row.modified = NEW_MODIFIED;
        return jsonResponse({ data: row });
      }
      if (url === ENCOUNTER_URL) {
        return jsonResponse({
          data: nativeEncounter(
            puts === 0
              ? {}
              : {
                  encounter_comment: REVIEW_TEXT,
                  modified: NEW_MODIFIED,
                  appointment: "APPT-0001",
                },
          ),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const write = await postReview(app, sessionId, validWriteBody());

    expect(write.statusCode).toBe(200);
    expect(write.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("writes when the omitted appointment is confirmed null for the same version", async () => {
    const { app, db, fetchImpl } = buildLive(
      probeHandler(
        () =>
          jsonResponse({
            message: {
              name: ENCOUNTER_ID,
              appointment: null,
              modified: MODIFIED,
            },
          }),
        withoutAppointment(),
        () =>
          jsonResponse({
            data: nativeEncounter({
              encounter_comment: REVIEW_TEXT,
              modified: NEW_MODIFIED,
            }),
          }),
      ),
    );
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const write = await postReview(app, sessionId, validWriteBody());

    expect(write.statusCode).toBe(200);
    expect(write.json()).toEqual({ status: "saved", modified: NEW_MODIFIED });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("refuses an omitted appointment that the probe reports as linked", async () => {
    const { app, db, fetchImpl } = buildLive(
      probeHandler(() =>
        jsonResponse({
          message: {
            name: ENCOUNTER_ID,
            appointment: "APPT-0001",
            modified: MODIFIED,
          },
        }),
      ),
    );
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const read = await getReview(app, sessionId);
    const write = await postReview(app, sessionId, validWriteBody());

    expect(read.statusCode).toBe(409);
    expect(read.json()).toEqual({ code: "UNSAFE_ENCOUNTER" });
    expect(write.statusCode).toBe(409);
    expect(write.json()).toEqual({ code: "UNSAFE_ENCOUNTER" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
  });

  it("refuses when the probe is denied for the caller", async () => {
    const { app, db, fetchImpl } = buildLive(
      probeHandler(() => jsonResponse({ code: "FORBIDDEN" }, 403)),
    );
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const write = await postReview(app, sessionId, validWriteBody());

    expect(write.statusCode).toBe(403);
    expect(write.json()).toEqual({ code: "FORBIDDEN" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
  });

  it("refuses when the probe reports a different version", async () => {
    const { app, db, fetchImpl } = buildLive(
      probeHandler(() =>
        jsonResponse({
          message: {
            name: ENCOUNTER_ID,
            appointment: null,
            modified: NEW_MODIFIED,
          },
        }),
      ),
    );
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const write = await postReview(app, sessionId, validWriteBody());

    expect(write.statusCode).toBe(502);
    expect(write.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
  });

  it("refuses malformed, absent and unavailable probe answers", async () => {
    const probes: Array<() => Response> = [
      () => jsonResponse({ message: null }),
      () => jsonResponse({ message: [] }),
      () =>
        jsonResponse({
          message: { name: "OTHER-ENC", appointment: null, modified: MODIFIED },
        }),
      () =>
        jsonResponse({ message: { name: ENCOUNTER_ID, modified: MODIFIED } }),
      () =>
        jsonResponse({
          message: { name: ENCOUNTER_ID, appointment: 0, modified: MODIFIED },
        }),
      () =>
        jsonResponse({ message: { name: ENCOUNTER_ID, appointment: null } }),
      () => jsonResponse({}, 404),
      () => jsonResponse({}, 500),
      () => new Response("not json", { status: 200 }),
    ];

    for (const probe of probes) {
      const { app, db, fetchImpl } = buildLive(probeHandler(probe));
      seedCreatedDraft(db);
      const sessionId = await login(app);

      const write = await postReview(app, sessionId, validWriteBody());

      expect(write.statusCode).toBe(502);
      expect(write.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
      expect(calls(fetchImpl, "PUT")).toHaveLength(0);
    }
  });

  it("fails closed when the probe session expired", async () => {
    const { app, db, fetchImpl } = buildLive(
      probeHandler(() => jsonResponse({}, 401)),
    );
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const write = await postReview(app, sessionId, validWriteBody());

    expect(write.statusCode).toBe(401);
    expect(write.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
    const replay = await getReview(app, sessionId);
    expect(replay.statusCode).toBe(401);
  });

  it("never probes when the full response carries the appointment explicitly", async () => {
    const { app, db, fetchImpl } = buildLive(defaultHandler);
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const nullAppointment = await getReview(app, sessionId);
    expect(nullAppointment.statusCode).toBe(200);
    expect(probeMessages(fetchImpl)).toHaveLength(0);

    const linked = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method !== "PUT") {
        return jsonResponse({
          data: nativeEncounter({ appointment: "APPT-0001" }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(linked.db);
    const linkedSession = await login(linked.app);

    const linkedRead = await getReview(linked.app, linkedSession);
    expect(linkedRead.statusCode).toBe(409);
    expect(probeMessages(linked.fetchImpl)).toHaveLength(0);
  });
});

describe("durable write-intent lock", () => {
  it("keeps the unknown intent for the user and encounter, not just the tab", async () => {
    const { app, db, fetchImpl } = buildLive(unknownOutcomeHandler());
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const first = await postReview(app, sessionId, validWriteBody());
    expect(first.json()).toEqual({ status: "unknown" });

    const replay = await postReview(app, sessionId, validWriteBody());
    expect(replay.statusCode).toBe(200);
    expect(replay.json()).toEqual({ status: "unknown" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("blocks a later intent once the base version moved", async () => {
    let version = MODIFIED;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        return jsonResponse({ exc_type: "ValidationError" }, 417);
      }
      if (url === ENCOUNTER_URL) {
        return jsonResponse({
          data: nativeEncounter({
            encounter_comment: "Existing review",
            modified: version,
          }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const first = await postReview(app, sessionId, validWriteBody());
    expect(first.json()).toEqual({ status: "unknown" });

    // Another actor moved the record: a new base version and a new intent key.
    version = NEW_MODIFIED;
    const blocked = await postReview(
      app,
      sessionId,
      validWriteBody({ modified: NEW_MODIFIED }),
    );

    expect(blocked.statusCode).toBe(409);
    expect(blocked.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });

  it("refuses while another instance owns a pending intent", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    new EncounterReviewStore(db).begin(DOC_USER, ENCOUNTER_ID, "other-intent");
    const sessionId = await login(app);

    const read = await getReview(app, sessionId);
    const write = await postReview(app, sessionId, validWriteBody());

    // Reading stays possible; writing is refused.
    expect(read.statusCode).toBe(200);
    expect(write.statusCode).toBe(409);
    expect(write.json()).toEqual({ code: "INTENT_PENDING" });
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
  });

  it("survives a restart on the same database", async () => {
    const first = buildLive(unknownOutcomeHandler());
    seedCreatedDraft(first.db);
    const sessionId = await login(first.app);
    const firstWrite = await postReview(first.app, sessionId, validWriteBody());
    expect(firstWrite.json()).toEqual({ status: "unknown" });
    await closeApp(first.app);

    const second = buildLive(
      (url, init) => {
        if (url === ENCOUNTER_URL && init.method === "PUT") {
          return jsonResponse({
            data: nativeEncounter({
              encounter_comment: REVIEW_TEXT,
              modified: NEW_MODIFIED,
            }),
          });
        }
        return defaultHandler(url, init);
      },
      docAuthenticate,
      LIVE_CONFIG,
      first.db,
    );
    const newSessionId = await login(second.app);
    const replay = await postReview(second.app, newSessionId, validWriteBody());

    expect(replay.statusCode).toBe(200);
    expect(replay.json()).toEqual({ status: "unknown" });
    expect(calls(second.fetchImpl, "PUT")).toHaveLength(0);
  });

  it("releases after a confirmed save so the next version can be written", async () => {
    let version = MODIFIED;
    let comment = "Existing review";
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        const body = JSON.parse(String(init.body)) as {
          encounter_comment: string;
        };
        comment = body.encounter_comment;
        version =
          version === MODIFIED ? NEW_MODIFIED : "2026-11-13 09:20:00.000000";
        return jsonResponse({
          data: nativeEncounter({
            encounter_comment: comment,
            modified: version,
          }),
        });
      }
      if (url === ENCOUNTER_URL) {
        return jsonResponse({
          data: nativeEncounter({
            encounter_comment: comment,
            modified: version,
          }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const first = await postReview(app, sessionId, validWriteBody());
    expect(first.json()).toEqual({ status: "saved", modified: NEW_MODIFIED });
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID),
    ).toBeUndefined();

    const second = await postReview(
      app,
      sessionId,
      validWriteBody({
        review_details: "Second review",
        modified: NEW_MODIFIED,
      }),
    );

    expect(second.statusCode).toBe(200);
    expect(second.json()).toEqual({
      status: "saved",
      modified: "2026-11-13 09:20:00.000000",
    });
    expect(calls(fetchImpl, "PUT")).toHaveLength(2);
  });

  it("keeps the intent after a native refusal so a corrected retry cannot write", async () => {
    const submitted: string[] = [];
    let puts = 0;
    const { app, db, fetchImpl } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method === "PUT") {
        puts += 1;
        const body = JSON.parse(String(init.body)) as {
          encounter_comment: string;
        };
        submitted.push(body.encounter_comment);
        return jsonResponse({ exc_type: "ValidationError" }, 400);
      }
      if (url === ENCOUNTER_URL) {
        // The base version moved while the refusal was in flight, so the next
        // attempt is a different intent for the same encounter.
        return jsonResponse({
          data: nativeEncounter({
            modified: puts === 0 ? MODIFIED : NEW_MODIFIED,
          }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    seedCreatedDraft(db, DOC_USER, "encounter-request-key-0002", ENCOUNTER_ID);
    const sessionId = await login(app);

    const first = await postReview(app, sessionId, validWriteBody());
    expect(first.statusCode).toBe(200);
    expect(first.json()).toEqual({ status: "unknown" });
    expect(
      new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID)?.state,
    ).toBe("unknown");

    // A refusal does not prove the write was not committed, so a "corrected"
    // retry — even under a fresh key and a new base version — never reaches the
    // native document.
    const corrected = await postReview(
      app,
      sessionId,
      validWriteBody({
        request_key: "encounter-request-key-0002",
        review_details: "Corrected review",
        modified: NEW_MODIFIED,
      }),
    );

    expect(corrected.statusCode).toBe(409);
    expect(corrected.json()).toEqual({ code: "INTENT_UNCONFIRMED" });
    expect(submitted).toEqual([REVIEW_TEXT]);
    expect(calls(fetchImpl, "PUT")).toHaveLength(1);
  });
});

describe("unresolved write state", () => {
  const OTHER_DOC = "other@lab.example";
  const ENCOUNTER_ID_2 = "ENC-9002";
  const REQUEST_KEY_2 = "encounter-request-key-0002";

  function intentRow(db: Database.Database) {
    return new EncounterReviewStore(db).get(DOC_USER, ENCOUNTER_ID);
  }

  it("reports clear when the durable intent table has no row", async () => {
    const { app, db } = buildLive();
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const response = await getReview(app, sessionId);

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({ write_state: "clear" });
    expect(intentRow(db)).toBeUndefined();
  });

  it("reports pending without changing or clearing it", async () => {
    const { app, db } = buildLive();
    seedCreatedDraft(db);
    new EncounterReviewStore(db).begin(
      DOC_USER,
      ENCOUNTER_ID,
      "pending-intent",
    );
    const before = intentRow(db);
    expect(before?.state).toBe("pending");
    const sessionId = await login(app);

    const first = await getReview(app, sessionId);
    const second = await getReview(app, sessionId);

    expect(first.statusCode).toBe(200);
    expect(first.json()).toMatchObject({ write_state: "pending" });
    expect(second.json()).toMatchObject({ write_state: "pending" });
    const after = intentRow(db);
    expect(after?.state).toBe("pending");
    expect(after?.operationKey).toBe(before?.operationKey);
    expect(after?.updatedAt).toBe(before?.updatedAt);
  });

  it("reports unknown without promoting, retrying or clearing it", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    const store = new EncounterReviewStore(db);
    store.begin(DOC_USER, ENCOUNTER_ID, "unknown-intent");
    store.markUnknown(DOC_USER, ENCOUNTER_ID);
    const before = intentRow(db);
    expect(before?.state).toBe("unknown");
    const sessionId = await login(app);

    const first = await getReview(app, sessionId);
    const second = await getReview(app, sessionId);

    expect(first.json()).toMatchObject({ write_state: "unknown" });
    expect(second.json()).toMatchObject({ write_state: "unknown" });
    const after = intentRow(db);
    expect(after?.state).toBe("unknown");
    expect(after?.updatedAt).toBe(before?.updatedAt);
    // A read never resolves the ambiguity by itself.
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
  });

  it("never derives the state from the current native document", async () => {
    // The native row already shows our text and a new version; without a durable
    // row the status is still clear, and with one it is still unknown.
    const { app, db } = buildLive((url, init) => {
      if (url === ENCOUNTER_URL && init.method !== "PUT") {
        return jsonResponse({
          data: nativeEncounter({
            encounter_comment: REVIEW_TEXT,
            modified: NEW_MODIFIED,
          }),
        });
      }
      return defaultHandler(url, init);
    });
    seedCreatedDraft(db);
    const sessionId = await login(app);

    const clear = await getReview(app, sessionId);
    expect(clear.json()).toMatchObject({ write_state: "clear" });

    const store = new EncounterReviewStore(db);
    store.begin(DOC_USER, ENCOUNTER_ID, "unknown-intent");
    store.markUnknown(DOC_USER, ENCOUNTER_ID);

    const unknown = await getReview(app, sessionId);
    expect(unknown.json()).toMatchObject({ write_state: "unknown" });
  });

  it("isolates the state per user for the same encounter", async () => {
    const shared = createSharedDb();
    const first = buildLive(
      defaultHandler,
      docAuthenticate,
      LIVE_CONFIG,
      shared,
    );
    seedCreatedDraft(first.db, DOC_USER, REQUEST_KEY, ENCOUNTER_ID);
    seedCreatedDraft(shared, OTHER_DOC, REQUEST_KEY_2, ENCOUNTER_ID);
    const store = new EncounterReviewStore(shared);
    store.begin(DOC_USER, ENCOUNTER_ID, "owner-intent");
    store.markUnknown(DOC_USER, ENCOUNTER_ID);

    const ownerSession = await login(first.app);
    const other = buildLive(
      defaultHandler,
      otherDocAuthenticate,
      LIVE_CONFIG,
      shared,
    );
    const otherSession = await login(other.app);

    const owner = await getReview(first.app, ownerSession);
    const stranger = await getReview(
      other.app,
      otherSession,
      `request_key=${REQUEST_KEY_2}`,
    );

    expect(owner.json()).toMatchObject({ write_state: "unknown" });
    expect(stranger.statusCode).toBe(200);
    expect(stranger.json()).toMatchObject({ write_state: "clear" });
  });

  it("persists pending and unknown across a reopened database", async () => {
    const dir = mkdtempSync(join(tmpdir(), "clinic-review-state-"));
    const databasePath = join(dir, "clinic.sqlite");
    let reopened: Database.Database | undefined;
    try {
      const file = new Database(databasePath);
      seedCreatedDraft(file, DOC_USER, REQUEST_KEY, ENCOUNTER_ID);
      seedCreatedDraft(file, DOC_USER, REQUEST_KEY_2, ENCOUNTER_ID_2);
      const store = new EncounterReviewStore(file);
      store.begin(
        DOC_USER,
        ENCOUNTER_ID,
        reviewOperationKey(ENCOUNTER_ID, MODIFIED),
      );
      store.begin(
        DOC_USER,
        ENCOUNTER_ID_2,
        reviewOperationKey(ENCOUNTER_ID_2, MODIFIED),
      );
      store.markUnknown(DOC_USER, ENCOUNTER_ID_2);
      file.close();

      reopened = new Database(databasePath);
      const app = buildServer({
        config: LIVE_CONFIG,
        database: reopened,
        authenticate: docAuthenticate,
        native: new NativeCredentialStore(),
        fetchImpl: encounterByIdHandler() as unknown as typeof fetch,
      });
      openApps.push(app);
      const sessionId = await login(app);

      const pending = await getReview(
        app,
        sessionId,
        `request_key=${REQUEST_KEY}`,
      );
      const unknown = await getReview(
        app,
        sessionId,
        `request_key=${REQUEST_KEY_2}`,
      );

      expect(pending.statusCode).toBe(200);
      expect(pending.json()).toMatchObject({ write_state: "pending" });
      expect(unknown.statusCode).toBe(200);
      expect(unknown.json()).toMatchObject({ write_state: "unknown" });
    } finally {
      reopened?.close();
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("does not let a non-clinician or a foreign key inspect the state", async () => {
    const { app, db, fetchImpl } = buildLive();
    seedCreatedDraft(db);
    new EncounterReviewStore(db).begin(
      DOC_USER,
      ENCOUNTER_ID,
      "pending-intent",
    );

    const reception = await buildLive(defaultHandler, recAuthenticate);
    const receptionSession = await login(reception.app, {
      usr: "reception@lab.example",
      pwd: "secret",
    });
    const refused = await getReview(reception.app, receptionSession);

    expect(refused.statusCode).toBe(403);
    expect(refused.json()).toEqual({ code: "FORBIDDEN" });
    expect(refused.body).not.toContain("write_state");

    const docSession = await login(app);
    const foreignKey = await getReview(
      app,
      docSession,
      `request_key=${REQUEST_KEY_2}`,
    );
    expect(foreignKey.statusCode).toBe(404);
    expect(foreignKey.json()).toEqual({ code: "NOT_FOUND" });
    expect(foreignKey.body).not.toContain("write_state");
    expect(calls(fetchImpl, "PUT")).toHaveLength(0);
  });
});
