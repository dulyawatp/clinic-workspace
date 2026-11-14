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
const PATIENTS_URL = "/api/live/synthetic-patients";
const PATIENT_1 = `${LAB_ORIGIN}/api/resource/Patient/SYNTH-PATIENT-1`;
const PATIENT_2 = `${LAB_ORIGIN}/api/resource/Patient/SYNTH-PATIENT-2`;
const LOGGED_USER_URL = `${LAB_ORIGIN}/api/method/frappe.auth.get_logged_user`;

const NATIVE_SID = "native-sid-value";

const LIVE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "live",
  live: {
    origin: LAB_ORIGIN,
    timeoutMs: 1_000,
    syntheticPatients: new Map([
      ["PAT-1", "SYNTH-PATIENT-1"],
      ["PAT-2", "SYNTH-PATIENT-2"],
    ]),
    clinicianRoles: new Set(["Clinic Clinician"]),
    syntheticItems: new Set(["DEMO-ITEM-1"]),
    syntheticInvoices: new Set(["TEST-INVOICE-1"]),
  },
};

const FIXTURE_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

const clinician: Authenticate = async () => ({
  user: { id: "clinician@lab.example", displayName: "Dr Lab", roles: ["DOC"] },
  native: { sid: NATIVE_SID, csrf: "native-csrf" },
});

const receptionist: Authenticate = async () => ({
  user: { id: "reception@lab.example", displayName: "Rec", roles: ["REC"] },
  native: { sid: NATIVE_SID, csrf: "native-csrf" },
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
  for (const app of openApps.splice(0)) await app.close();
  for (const db of openDbs.splice(0)) db.close();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function defaultHandler(url: string): Response {
  if (url === LOGGED_USER_URL) {
    return jsonResponse({ message: "clinician@lab.example" });
  }
  if (url === PATIENT_1) {
    return jsonResponse({
      data: { name: "SYNTH-PATIENT-1", patient_name: "Synthetic One" },
    });
  }
  if (url === PATIENT_2) {
    return jsonResponse({
      data: { name: "SYNTH-PATIENT-2", patient_name: "Synthetic Two" },
    });
  }
  throw new Error(`unexpected upstream call ${url}`);
}

function buildLive(
  handler: Handler = defaultHandler,
  authenticate: Authenticate = clinician,
): Harness {
  const db = new Database(":memory:");
  openDbs.push(db);
  const native = new NativeCredentialStore();
  const fetchImpl = vi.fn(
    async (input: string | URL | Request, init?: RequestInit) =>
      handler(String(input), init ?? {}),
  );
  const app = buildServer({
    config: LIVE_CONFIG,
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
  usr = "clinician@lab.example",
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
    url: PATIENTS_URL,
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

describe("live synthetic patient metadata boundaries", () => {
  it("allows cashier to read only the authorized synthetic invoice summary", async () => {
    const cashier: Authenticate = async () => ({
      user: {
        id: "cashier@lab.example",
        displayName: "Cashier",
        roles: ["CAS"],
      },
      native: { sid: "cashier-sid", csrf: "native-csrf" },
    });
    const { app, fetchImpl } = buildLive(
      () =>
        jsonResponse({
          data: {
            name: "TEST-INVOICE-1",
            docstatus: 1,
            company: "Demo Clinic",
            currency: "THB",
            remarks: "demo-seed-v1:invoice:0001",
            grand_total: 50,
            outstanding_amount: 10,
          },
        }),
      cashier,
    );
    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-finance",
      headers: {
        cookie: `${SESSION_COOKIE}=${await login(app, "cashier@lab.example")}`,
      },
    });
    expect(response.statusCode).toBe(200);
    expect(response.json().invoice_count).toBe(1);
    expect(cookieHeader(fetchImpl.mock.calls[0]?.[1] as RequestInit)).toBe(
      "sid=cashier-sid",
    );
  });
  it("allows nurse to read native-authorized synthetic patients", async () => {
    const nurse: Authenticate = async () => ({
      user: { id: "nur@lab.example", displayName: "Nurse", roles: ["NUR"] },
      native: { sid: NATIVE_SID, csrf: "native-csrf" },
    });
    const { app } = buildLive(defaultHandler, nurse);
    const response = await list(app, await login(app, "nur@lab.example"));
    expect(response.statusCode).toBe(200);
    expect(response.json().items).toHaveLength(2);
  });
  it.each([
    { name: "OTHER" },
    { docstatus: 0 },
    { company: "Other" },
    { currency: "USD" },
    { remarks: "not synthetic" },
  ])("rejects finance provenance mismatch %#", async (change) => {
    const manager: Authenticate = async () => ({
      user: { id: "mgr@lab.example", displayName: "Mgr", roles: ["MGR"] },
      native: { sid: "manager-sid", csrf: "native-csrf" },
    });
    const { app } = buildLive(
      () =>
        jsonResponse({
          data: {
            name: "TEST-INVOICE-1",
            docstatus: 1,
            company: "Demo Clinic",
            currency: "THB",
            remarks: "demo-seed-v1:invoice:0001",
            grand_total: 50,
            outstanding_amount: 10,
            ...change,
          },
        }),
      manager,
    );
    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-finance",
      headers: {
        cookie: `${SESSION_COOKIE}=${await login(app, "mgr@lab.example")}`,
      },
    });
    expect(response.statusCode).toBe(502);
  });
  it("rejects anonymous finance requests", async () => {
    const { app, fetchImpl } = buildLive();
    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-finance",
    });
    expect(response.statusCode).toBe(401);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
  it.each(["DOC", "REC", "PHA"] as const)(
    "denies finance to %s without an upstream request",
    async (role) => {
      const auth: Authenticate = async () => ({
        user: { id: "other@lab.example", displayName: "Other", roles: [role] },
        native: { sid: "other-sid", csrf: "native-csrf" },
      });
      const { app, fetchImpl } = buildLive(defaultHandler, auth);
      const response = await app.inject({
        method: "GET",
        url: "/api/live/synthetic-finance",
        headers: {
          cookie: `${SESSION_COOKIE}=${await login(app, "other@lab.example")}`,
        },
      });
      expect(response.statusCode).toBe(403);
      expect(fetchImpl).not.toHaveBeenCalled();
    },
  );
  it.each([-1, 1e308, 1.005])(
    "rejects invalid finance amount %s",
    async (amount) => {
      const manager: Authenticate = async () => ({
        user: { id: "mgr@lab.example", displayName: "Mgr", roles: ["MGR"] },
        native: { sid: "manager-sid", csrf: "native-csrf" },
      });
      const { app } = buildLive(
        () =>
          jsonResponse({
            data: {
              name: "TEST-INVOICE-1",
              docstatus: 1,
              company: "Demo Clinic",
              currency: "THB",
              remarks: "demo-seed-v1:invoice:0001",
              grand_total: amount,
              outstanding_amount: 0,
            },
          }),
        manager,
      );
      const response = await app.inject({
        method: "GET",
        url: "/api/live/synthetic-finance",
        headers: {
          cookie: `${SESSION_COOKIE}=${await login(app, "mgr@lab.example")}`,
        },
      });
      expect(response.statusCode).toBe(502);
    },
  );
  it("returns only verified synthetic finance totals to a manager", async () => {
    const manager: Authenticate = async () => ({
      user: { id: "mgr@lab.example", displayName: "Mgr", roles: ["MGR"] },
      native: { sid: "manager-sid", csrf: "native-csrf" },
    });
    const { app, fetchImpl } = buildLive((url) => {
      expect(url).toBe(
        `${LAB_ORIGIN}/api/resource/Sales%20Invoice/TEST-INVOICE-1`,
      );
      return jsonResponse({
        data: {
          name: "TEST-INVOICE-1",
          docstatus: 1,
          company: "Demo Clinic",
          currency: "THB",
          remarks: "demo-seed-v1:invoice:0001",
          grand_total: 50,
          outstanding_amount: 10,
        },
      });
    }, manager);
    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-finance",
      headers: {
        cookie: `${SESSION_COOKIE}=${await login(app, "mgr@lab.example")}`,
      },
    });
    expect(response.statusCode).toBe(200);
    expect(cookieHeader(fetchImpl.mock.calls[0]?.[1] as RequestInit)).toBe(
      "sid=manager-sid",
    );
    expect(response.json()).toEqual({
      currency: "THB",
      invoice_count: 1,
      invoiced: 50,
      outstanding: 10,
    });
  });
  it.each([401, 500])(
    "fails closed on stock upstream status %i",
    async (status) => {
      const pharmacy: Authenticate = async () => ({
        user: { id: "pha@lab.example", displayName: "Pha", roles: ["PHA"] },
        native: { sid: "pharmacy-sid", csrf: "native-csrf" },
      });
      const { app } = buildLive(() => jsonResponse({}, status), pharmacy);
      const response = await app.inject({
        method: "GET",
        url: "/api/live/synthetic-stock",
        headers: {
          cookie: `${SESSION_COOKIE}=${await login(app, "pha@lab.example")}`,
        },
      });
      expect(response.statusCode).toBe(status === 401 ? 401 : 502);
    },
  );
  it.each([403, 404])(
    "fails explicitly on stock upstream status %i with a live session",
    async (status) => {
      const pharmacy: Authenticate = async () => ({
        user: { id: "pha@lab.example", displayName: "Pha", roles: ["PHA"] },
        native: { sid: "pharmacy-sid", csrf: "native-csrf" },
      });
      // The session probe stays alive: this is a denial, not an expiry.
      const { app, native } = buildLive(
        (url) =>
          url === LOGGED_USER_URL
            ? jsonResponse({ message: "pha@lab.example" })
            : jsonResponse({ exc_type: "PermissionError" }, status),
        pharmacy,
      );
      const sessionId = await login(app, "pha@lab.example");

      const response = await app.inject({
        method: "GET",
        url: "/api/live/synthetic-stock",
        headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
      });

      expect(response.statusCode).toBe(403);
      expect(response.json()).toEqual({ code: "FORBIDDEN" });
      expect(native.get(sessionId)).toBeDefined();
    },
  );
  it("returns an explicit failure instead of partial inventory", async () => {
    const pharmacy: Authenticate = async () => ({
      user: { id: "pha@lab.example", displayName: "Pha", roles: ["PHA"] },
      native: { sid: "pharmacy-sid", csrf: "native-csrf" },
    });
    const db = new Database(":memory:");
    openDbs.push(db);
    const fetchImpl = vi.fn(
      async (input: string | URL | Request, _init?: RequestInit) => {
        const url = String(input);
        if (url === LOGGED_USER_URL)
          return jsonResponse({ message: "pha@lab.example" });
        if (url.endsWith("/api/resource/Item/DEMO-ITEM-1"))
          return jsonResponse({
            data: {
              name: "DEMO-ITEM-1",
              item_code: "DEMO-ITEM-1",
              item_name: "Synthetic item",
              stock_uom: "Unit",
              is_stock_item: 1,
            },
          });
        if (url.endsWith("/api/resource/Item/DEMO-ITEM-2"))
          return jsonResponse({ exc_type: "PermissionError" }, 403);
        throw new Error(`unexpected upstream path ${url}`);
      },
    );
    const app = buildServer({
      config: {
        appOrigin: TEST_ORIGIN,
        databasePath: ":memory:",
        activeWrites: new Set(),
        upstreamMode: "live",
        live: {
          origin: LAB_ORIGIN,
          timeoutMs: 1_000,
          syntheticPatients: new Map([["PAT-1", "SYNTH-PATIENT-1"]]),
          clinicianRoles: new Set(["Clinic Clinician"]),
          syntheticItems: new Set(["DEMO-ITEM-1", "DEMO-ITEM-2"]),
        },
      },
      database: db,
      authenticate: pharmacy,
      native: new NativeCredentialStore(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    openApps.push(app);
    const sessionId = await login(app, "pha@lab.example");

    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-stock",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(response.json()).not.toHaveProperty("items");
  });
  it("fails closed on a mismatched synthetic stock identity", async () => {
    const pharmacy: Authenticate = async () => ({
      user: { id: "pha@lab.example", displayName: "Pha", roles: ["PHA"] },
      native: { sid: "pharmacy-sid", csrf: "native-csrf" },
    });
    const { app } = buildLive(
      () => jsonResponse({ data: { name: "OTHER" } }),
      pharmacy,
    );
    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-stock",
      headers: {
        cookie: `${SESSION_COOKIE}=${await login(app, "pha@lab.example")}`,
      },
    });
    expect(response.statusCode).toBe(502);
  });
  it("reads only configured synthetic stock with the pharmacy session", async () => {
    const pharmacy: Authenticate = async () => ({
      user: { id: "pha@lab.example", displayName: "Pha", roles: ["PHA"] },
      native: { sid: "pharmacy-sid", csrf: "native-csrf" },
    });
    const { app, fetchImpl } = buildLive((url) => {
      if (url.endsWith("/api/resource/Item/DEMO-ITEM-1"))
        return jsonResponse({
          data: {
            name: "DEMO-ITEM-1",
            item_code: "DEMO-ITEM-1",
            item_name: "Synthetic item",
            stock_uom: "Unit",
            is_stock_item: 1,
            private_field: "excluded",
          },
        });
      throw new Error("unexpected upstream path");
    }, pharmacy);
    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-stock",
      headers: {
        cookie: `${SESSION_COOKIE}=${await login(app, "pha@lab.example")}`,
      },
    });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      items: [
        {
          item_code: "DEMO-ITEM-1",
          item_name: "Synthetic item",
          stock_uom: "Unit",
          is_stock_item: true,
        },
      ],
    });
    expect(cookieHeader(fetchImpl.mock.calls[0]?.[1] as RequestInit)).toBe(
      "sid=pharmacy-sid",
    );
  });
  it("refuses stock reads for a clinician before contacting upstream", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);
    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-stock",
      headers: { cookie: `${SESSION_COOKIE}=${sessionId}` },
    });
    expect(response.statusCode).toBe(403);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
  it("does not expose the endpoint in fixture mode", async () => {
    const { app, fetchImpl } = buildFixture();

    const response = await list(app);

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({ code: "UNSUPPORTED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a guest without contacting the lab", async () => {
    const { app, fetchImpl } = buildLive();

    const response = await list(app);

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses an unknown session cookie without contacting the lab", async () => {
    const { app, fetchImpl } = buildLive();

    const response = await list(app, "not-a-session");

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("allows reception only the native-authorized synthetic patients", async () => {
    const { app, fetchImpl } = buildLive(defaultHandler, receptionist);
    const sessionId = await login(app, "reception@lab.example");

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(200);
    expect(response.json().items).toHaveLength(2);
    expect(fetchImpl).toHaveBeenCalled();
  });

  it("refuses pharmacy before contacting the backend", async () => {
    const pharmacy: Authenticate = async () => ({
      user: {
        id: "pharmacy@lab.example",
        displayName: "Pharmacy",
        roles: ["PHA"],
      },
      native: { sid: NATIVE_SID, csrf: "native-csrf" },
    });
    const { app, fetchImpl } = buildLive(defaultHandler, pharmacy);
    const response = await list(app, await login(app, "pharmacy@lab.example"));
    expect(response.statusCode).toBe(403);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("returns only the authorized allow-listed row", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(200);
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(response.json()).toEqual({
      items: [
        {
          workspace_patient_id: "PAT-1",
          native_patient_id: "SYNTH-PATIENT-1",
          display_name: "Synthetic One",
        },
        {
          workspace_patient_id: "PAT-2",
          native_patient_id: "SYNTH-PATIENT-2",
          display_name: "Synthetic Two",
        },
      ],
    });

    // The forwarded native name comes from the server-owned allow-list and the
    // clinician's own native session cookie; nothing is client supplied.
    const patientCalls = fetchImpl.mock.calls.filter((call) =>
      String(call[0]).startsWith(`${LAB_ORIGIN}/api/resource/Patient/`),
    );
    expect(patientCalls.map((call) => String(call[0]))).toEqual([
      PATIENT_1,
      PATIENT_2,
    ]);
    for (const call of patientCalls) {
      expect(cookieHeader(call[1] as RequestInit)).toBe(`sid=${NATIVE_SID}`);
    }
  });

  it("omits a denied row and still returns the authorized one", async () => {
    const { app, native } = buildLive((url) => {
      if (url === PATIENT_1) return jsonResponse({}, 403);
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ message: "clinician@lab.example" });
      }
      if (url === PATIENT_2) {
        return jsonResponse({
          data: { name: "SYNTH-PATIENT-2", patient_name: "Synthetic Two" },
        });
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(200);
    const body = response.json() as { items: Array<Record<string, unknown>> };
    expect(body.items).toEqual([
      {
        workspace_patient_id: "PAT-2",
        native_patient_id: "SYNTH-PATIENT-2",
        display_name: "Synthetic Two",
      },
    ]);
    expect(response.body).not.toContain("PAT-1");
    // A plain denial is not an authorization failure for the session itself.
    expect(native.get(sessionId)).toBeDefined();
  });

  it("omits a denied row whose native session is still alive", async () => {
    const { app } = buildLive((url) => {
      if (url === PATIENT_1 || url === PATIENT_2) {
        return jsonResponse({}, 403);
      }
      if (url === LOGGED_USER_URL) {
        return jsonResponse({ message: "clinician@lab.example" });
      }
      throw new Error(`unexpected upstream call ${url}`);
    });
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ items: [] });
  });

  it("invalidates the session when the upstream read is expired", async () => {
    const { app, native } = buildLive((url) => {
      if (url === PATIENT_1) return jsonResponse({}, 401);
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(native.get(sessionId)).toBeUndefined();
    const cleared = response.cookies
      .filter((cookie) => cookie.name === SESSION_COOKIE)
      .map((cookie) => cookie.value);
    expect(cleared).toContain("");

    // The destroyed session cannot be reused for a second attempt.
    const retry = await list(app, sessionId);
    expect(retry.statusCode).toBe(401);
    expect(retry.json()).toEqual({ code: "AUTH_EXPIRED" });
  });

  it("invalidates the session when a denial hides an expired native session", async () => {
    const { app, native } = buildLive((url) => {
      if (url === PATIENT_1) return jsonResponse({}, 403);
      if (url === LOGGED_USER_URL) return jsonResponse({ message: "Guest" });
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(native.get(sessionId)).toBeUndefined();
  });

  it("fails closed on a malformed upstream row", async () => {
    const { app } = buildLive((url) => {
      if (url === PATIENT_1) return new Response("not json", { status: 200 });
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    expect(response.body).not.toContain("PAT-");
  });

  it("fails closed when the row identity does not match the allow-list", async () => {
    const { app } = buildLive((url) => {
      if (url === PATIENT_1) {
        return jsonResponse({ data: { name: "OTHER-PATIENT" } });
      }
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    expect(response.body).not.toContain("OTHER-PATIENT");
  });

  it("fails closed when the upstream row shape is unexpected", async () => {
    const { app } = buildLive((url) => {
      if (url === PATIENT_1) return jsonResponse({ data: ["SYNTH-PATIENT-1"] });
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  });

  it("reports an unreachable upstream without leaking rows", async () => {
    const { app } = buildLive((url) => {
      if (url === PATIENT_1) throw new Error("socket hang up");
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  });

  it("uses each session's own native cookie and cached authority", async () => {
    const authenticate: Authenticate = async ({ usr }) => ({
      user: {
        id: usr,
        displayName: usr,
        roles: ["DOC"],
      },
      native:
        usr === "doctor-a@lab.example"
          ? { sid: "sid-doctor-a", csrf: "csrf-a" }
          : { sid: "sid-doctor-b", csrf: "csrf-b" },
    });

    const { app, native } = buildLive((url, init) => {
      const cookie = cookieHeader(init);
      const isA = cookie === "sid=sid-doctor-a";
      const isB = cookie === "sid=sid-doctor-b";
      if (url === PATIENT_1) {
        // Only A may read patient 1; B gets a denial that keeps B's session.
        return isA
          ? jsonResponse({
              data: { name: "SYNTH-PATIENT-1", patient_name: "Synthetic One" },
            })
          : jsonResponse({}, isB ? 403 : 401);
      }
      if (url === PATIENT_2) {
        return isB
          ? jsonResponse({
              data: { name: "SYNTH-PATIENT-2", patient_name: "Synthetic Two" },
            })
          : jsonResponse({}, isA ? 403 : 401);
      }
      if (url === LOGGED_USER_URL) {
        return jsonResponse({
          message: isA ? "doctor-a@lab.example" : "doctor-b@lab.example",
        });
      }
      throw new Error(`unexpected upstream call ${url}`);
    }, authenticate);

    const sessionA = await login(app, "doctor-a@lab.example");
    const sessionB = await login(app, "doctor-b@lab.example");
    expect(sessionA).not.toBe(sessionB);

    const responseA = await list(app, sessionA);
    const responseB = await list(app, sessionB);

    expect(responseA.statusCode).toBe(200);
    expect(responseA.json()).toEqual({
      items: [
        {
          workspace_patient_id: "PAT-1",
          native_patient_id: "SYNTH-PATIENT-1",
          display_name: "Synthetic One",
        },
      ],
    });
    expect(responseB.statusCode).toBe(200);
    expect(responseB.json()).toEqual({
      items: [
        {
          workspace_patient_id: "PAT-2",
          native_patient_id: "SYNTH-PATIENT-2",
          display_name: "Synthetic Two",
        },
      ],
    });

    expect(native.get(sessionA)?.sid).toBe("sid-doctor-a");
    expect(native.get(sessionB)?.sid).toBe("sid-doctor-b");
    // Neither session saw the other's rows or lost its own credentials.
    expect(responseA.body).not.toContain("PAT-2");
    expect(responseB.body).not.toContain("PAT-1");
  });

  it("invalidates a live session whose native credential is gone", async () => {
    const { app, native } = buildLive();
    const sessionId = await login(app);
    native.clear(sessionId);

    const response = await list(app, sessionId);

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    const retry = await list(app, sessionId);
    expect(retry.statusCode).toBe(401);
  });
});
