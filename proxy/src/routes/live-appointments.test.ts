import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RoleCode } from "../capabilities.js";
import type { AppConfig, LiveUpstream } from "../config.js";
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
const URL_PATH = "/api/live/synthetic-appointments";
const APPT_1 = `${LAB_ORIGIN}/api/resource/Patient%20Appointment/APPT-1`;
const APPT_2 = `${LAB_ORIGIN}/api/resource/Patient%20Appointment/APPT-2`;
const APPT_3 = `${LAB_ORIGIN}/api/resource/Patient%20Appointment/APPT-3`;
const LOGGED_USER_URL = `${LAB_ORIGIN}/api/method/frappe.auth.get_logged_user`;

const LIVE_UPSTREAM: LiveUpstream = {
  origin: LAB_ORIGIN,
  timeoutMs: 1_000,
  syntheticPatients: new Map([
    ["PAT-1", "SYNTH-PATIENT-1"],
    ["PAT-2", "SYNTH-PATIENT-2"],
  ]),
  syntheticAppointments: new Set(["APPT-1", "APPT-2"]),
  clinicianRoles: new Set(["Clinic Clinician"]),
};

const LIVE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "live",
  live: LIVE_UPSTREAM,
};

const FIXTURE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

function authFor(id: string, roles: RoleCode[], sid: string): Authenticate {
  return async () => ({
    user: { id, displayName: id, roles },
    native: { sid, csrf: "native-csrf" },
  });
}

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
  for (const app of openApps.splice(0)) await app.close();
  for (const db of openDbs.splice(0)) db.close();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function appointmentBody(
  name: string,
  overrides: Record<string, unknown> = {},
): unknown {
  return {
    data: {
      name,
      patient: "SYNTH-PATIENT-1",
      appointment_date: "2026-11-20",
      appointment_time: "09:30:00",
      status: "Open",
      patient_name: "Synthetic One",
      ...overrides,
    },
  };
}

type Handler = (url: string, init: RequestInit) => Response;

function defaultHandler(url: string): Response {
  if (url === LOGGED_USER_URL) {
    return jsonResponse({ message: "user@lab.example" });
  }
  if (url === APPT_1) return jsonResponse(appointmentBody("APPT-1"));
  if (url === APPT_2) {
    return jsonResponse(
      appointmentBody("APPT-2", {
        patient: "SYNTH-PATIENT-2",
        status: "Checked In",
      }),
    );
  }
  throw new Error(`unexpected upstream call ${url}`);
}

function buildLive(
  handler: Handler = defaultHandler,
  authenticate: Authenticate = authFor("rec@lab.example", ["REC"], "rec-sid"),
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

function buildFixture(): Harness {
  const db = new Database(":memory:");
  openDbs.push(db);
  const native = new NativeCredentialStore();
  const fetchImpl = vi.fn();
  const app = buildServer({
    config: FIXTURE_CONFIG,
    database: db,
    native,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  openApps.push(app);
  return { app, db, native, fetchImpl };
}

async function login(
  app: FastifyInstance,
  usr = "rec@lab.example",
): Promise<string> {
  const pre = await app.inject({ method: "GET", url: "/api/auth/csrf" });
  const preCookie = pre.cookies.find((cookie) => cookie.name === CSRF_COOKIE);
  const response = await app.inject({
    method: "POST",
    url: "/api/auth/login",
    headers: {
      origin: TEST_ORIGIN,
      "content-type": "application/json",
      [CSRF_HEADER]: (pre.json() as { token: string }).token,
      cookie: `${CSRF_COOKIE}=${preCookie?.value ?? ""}`,
    },
    payload: { usr, pwd: "secret" },
  });
  expect(response.statusCode).toBe(200);
  return (
    response.cookies.find((cookie) => cookie.name === SESSION_COOKIE)?.value ??
    ""
  );
}

function list(app: FastifyInstance, sessionId?: string) {
  return app.inject({
    method: "GET",
    url: URL_PATH,
    headers:
      sessionId === undefined
        ? {}
        : { cookie: `${SESSION_COOKIE}=${sessionId}` },
  });
}

function cookieHeader(init: RequestInit): string {
  const headers = (init.headers ?? {}) as Record<string, string>;
  return headers.cookie ?? "";
}

describe("live synthetic appointments", () => {
  it("returns the minimal appointment DTO for a reception user", async () => {
    const { app } = buildLive();
    const response = await list(app, await login(app, "rec@lab.example"));
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      items: [
        {
          workspace_patient_id: "PAT-1",
          appointment_id: "APPT-1",
          appointment_date: "2026-11-20",
          appointment_time: "09:30:00",
          status: "Open",
        },
        {
          workspace_patient_id: "PAT-2",
          appointment_id: "APPT-2",
          appointment_date: "2026-11-20",
          appointment_time: "09:30:00",
          status: "Checked In",
        },
      ],
    });
  });

  it("allows a clinician to read the same list", async () => {
    const { app } = buildLive(
      defaultHandler,
      authFor("doc@lab.example", ["DOC"], "doc-sid"),
    );
    const response = await list(app, await login(app, "doc@lab.example"));
    expect(response.statusCode).toBe(200);
    expect(response.json().items).toHaveLength(2);
  });

  it("never echoes a patient name or native patient identifier", async () => {
    const { app } = buildLive();
    const response = await list(app, await login(app, "rec@lab.example"));
    expect(response.statusCode).toBe(200);
    const text = response.body;
    expect(text).not.toContain("Synthetic One");
    expect(text).not.toContain("SYNTH-PATIENT-1");
    expect(text).not.toContain("patient_name");
    expect(text).not.toContain("patientId");
  });

  it("refuses a guest without any upstream request", async () => {
    const { app, fetchImpl } = buildLive();
    const response = await list(app);
    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it.each(["NUR", "PHA", "CAS", "MGR", "ADM"] as const)(
    "denies the appointment list to %s without an upstream request",
    async (role) => {
      const { app, fetchImpl } = buildLive(
        defaultHandler,
        authFor("other@lab.example", [role], "other-sid"),
      );
      const response = await list(app, await login(app, "other@lab.example"));
      expect(response.statusCode).toBe(403);
      expect(response.json()).toEqual({ code: "FORBIDDEN" });
      expect(fetchImpl).not.toHaveBeenCalled();
    },
  );

  it("omits an appointment whose patient is outside the synthetic allow-list", async () => {
    const config: AppConfig = {
      ...LIVE_CONFIG,
      live: {
        ...LIVE_UPSTREAM,
        syntheticAppointments: new Set(["APPT-1", "APPT-3"]),
      },
    };
    const { app } = buildLive(
      (url) => {
        if (url === APPT_1) return jsonResponse(appointmentBody("APPT-1"));
        if (url === APPT_3) {
          return jsonResponse(
            appointmentBody("APPT-3", {
              patient: "REAL-PATIENT-9",
              patient_name: "Real Person",
            }),
          );
        }
        throw new Error(`unexpected upstream call ${url}`);
      },
      authFor("rec@lab.example", ["REC"], "rec-sid"),
      config,
    );
    const response = await list(app, await login(app, "rec@lab.example"));
    expect(response.statusCode).toBe(200);
    expect(response.json().items).toEqual([
      {
        workspace_patient_id: "PAT-1",
        appointment_id: "APPT-1",
        appointment_date: "2026-11-20",
        appointment_time: "09:30:00",
        status: "Open",
      },
    ]);
    expect(response.body).not.toContain("REAL-PATIENT-9");
    expect(response.body).not.toContain("Real Person");
  });

  it("returns no items when the appointment allow-list is empty", async () => {
    const config: AppConfig = {
      ...LIVE_CONFIG,
      live: { ...LIVE_UPSTREAM, syntheticAppointments: new Set() },
    };
    const { app, fetchImpl } = buildLive(
      defaultHandler,
      authFor("rec@lab.example", ["REC"], "rec-sid"),
      config,
    );
    const response = await list(app, await login(app, "rec@lab.example"));
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ items: [] });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("fails closed in fixture mode with no fixture fallback", async () => {
    const { app, fetchImpl } = buildFixture();
    const response = await app.inject({
      method: "GET",
      url: URL_PATH,
      headers: { cookie: `${SESSION_COOKIE}=fixture-session` },
    });
    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({ code: "UNSUPPORTED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("reports an expired native session and clears the proxy session", async () => {
    const { app, native } = buildLive(() => jsonResponse({}, 401));
    const sessionId = await login(app, "rec@lab.example");
    expect(native.get(sessionId)).toBeDefined();
    const response = await list(app, sessionId);
    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(native.get(sessionId)).toBeUndefined();
  });

  it("keeps a native 403 as a denial when the native session is alive", async () => {
    const { app } = buildLive((url) => {
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ message: "rec@lab.example" });
      }
      return jsonResponse({}, 403);
    });
    const response = await list(app, await login(app, "rec@lab.example"));
    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
  });

  it.each([
    { appointment_date: "2026-02-30" },
    { appointment_date: "20-11-2026" },
    { appointment_time: "25:00:00" },
    { appointment_time: "9:30" },
    { status: "" },
    { status: "Open\nSecond" },
    { patient: "" },
    { name: "APPT-9" },
  ])(
    "fails the whole response on a contract violation %#",
    async (override) => {
      const { app } = buildLive(() =>
        jsonResponse(appointmentBody("APPT-1", override)),
      );
      const response = await list(app, await login(app, "rec@lab.example"));
      expect(response.statusCode).toBe(502);
      expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    },
  );

  it("does not return a partial list when a later read fails", async () => {
    const { app } = buildLive((url) => {
      if (url === APPT_1) return jsonResponse(appointmentBody("APPT-1"));
      if (url === APPT_2) return jsonResponse({}, 500);
      throw new Error(`unexpected upstream call ${url}`);
    });
    const response = await list(app, await login(app, "rec@lab.example"));
    expect(response.statusCode).toBe(502);
    expect(response.body).not.toContain("APPT-1");
  });

  it("maps a network failure to an upstream error", async () => {
    const { app } = buildLive(() => {
      throw new Error("socket hang up");
    });
    const response = await list(app, await login(app, "rec@lab.example"));
    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  });

  it("uses each user's own native session and never another user's", async () => {
    const authenticate: Authenticate = async (credentials) => {
      const isDoctor = credentials.usr === "doc@lab.example";
      return {
        user: {
          id: credentials.usr,
          displayName: credentials.usr,
          roles: [isDoctor ? "DOC" : "REC"],
        },
        native: { sid: isDoctor ? "doc-sid" : "rec-sid", csrf: "native-csrf" },
      };
    };
    const { app, fetchImpl } = buildLive(defaultHandler, authenticate);

    const recSession = await login(app, "rec@lab.example");
    const recCalls = fetchImpl.mock.calls.length;
    await list(app, recSession);
    const recSids = new Set(
      fetchImpl.mock.calls
        .slice(recCalls)
        .map((call) => cookieHeader(call[1] as RequestInit)),
    );
    expect(recSids).toEqual(new Set(["sid=rec-sid"]));

    const docSession = await login(app, "doc@lab.example");
    const docCalls = fetchImpl.mock.calls.length;
    await list(app, docSession);
    const docSids = new Set(
      fetchImpl.mock.calls
        .slice(docCalls)
        .map((call) => cookieHeader(call[1] as RequestInit)),
    );
    expect(docSids).toEqual(new Set(["sid=doc-sid"]));
    expect(docSids.has("sid=rec-sid")).toBe(false);
  });

  it("percent-encodes the server-owned appointment name in the path", async () => {
    const config: AppConfig = {
      ...LIVE_CONFIG,
      live: {
        ...LIVE_UPSTREAM,
        syntheticAppointments: new Set(["APPT 1"]),
      },
    };
    const { app, fetchImpl } = buildLive(
      (url) => {
        expect(url).toBe(
          `${LAB_ORIGIN}/api/resource/Patient%20Appointment/APPT%201`,
        );
        return jsonResponse(appointmentBody("APPT 1"));
      },
      authFor("rec@lab.example", ["REC"], "rec-sid"),
      config,
    );
    const response = await list(app, await login(app, "rec@lab.example"));
    expect(response.statusCode).toBe(200);
    expect(response.json().items[0].appointment_id).toBe("APPT 1");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
