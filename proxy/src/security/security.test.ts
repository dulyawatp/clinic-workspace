import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppConfig } from "../config.js";
import { buildServer } from "../server.js";
import {
  type AuditEvent,
  type AuditRecord,
  type AuditSink,
  createAuditSink,
} from "./audit.js";
import { CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE } from "./constants.js";

const TEST_ORIGIN = "http://localhost:5173";

const TEST_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

// Fake upstream: never contacts a live backend. Credentials are fixture-only.
async function fakeUpstream(credentials: { usr: string; pwd: string }) {
  return credentials.usr === "rec" && credentials.pwd === "fixture"
    ? {
        user: {
          id: "fixture-rec",
          displayName: "Fixture Receptionist",
          roles: ["REC" as const],
        },
      }
    : null;
}

const silentAudit: AuditSink = () => {};

interface IssuedCsrf {
  token: string;
  csrfCookie: string;
}

let db: Database.Database;
let app: FastifyInstance;
const extraApps: FastifyInstance[] = [];
const extraDbs: Database.Database[] = [];

beforeEach(() => {
  db = new Database(":memory:");
  app = buildServer({
    config: TEST_CONFIG,
    database: db,
    authenticate: fakeUpstream,
    auditSink: silentAudit,
  });
});

afterEach(async () => {
  await app.close();
  db.close();
  for (const extra of extraApps.splice(0)) {
    await extra.close();
  }
  for (const extra of extraDbs.splice(0)) {
    extra.close();
  }
});

function track(instance: FastifyInstance, database: Database.Database) {
  extraApps.push(instance);
  extraDbs.push(database);
  return instance;
}

function cookiePairs(cookies: Array<{ name: string; value: string }>): string {
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

async function getCsrf(
  instance: FastifyInstance,
  cookies?: Array<{ name: string; value: string }>,
): Promise<IssuedCsrf> {
  const res = await instance.inject({
    method: "GET",
    url: "/api/auth/csrf",
    headers: cookies === undefined ? {} : { cookie: cookiePairs(cookies) },
  });
  expect(res.statusCode).toBe(200);
  const csrfCookie = res.cookies.find((cookie) => cookie.name === CSRF_COOKIE);
  expect(csrfCookie).toBeDefined();
  return {
    token: (res.json() as { token: string }).token,
    csrfCookie: csrfCookie?.value ?? "",
  };
}

async function getCsrfForCookie(
  instance: FastifyInstance,
  csrfCookie: string,
): Promise<IssuedCsrf> {
  return getCsrf(instance, [{ name: CSRF_COOKIE, value: csrfCookie }]);
}

function loginHeaders(token: string, csrfCookie: string) {
  return {
    origin: TEST_ORIGIN,
    "content-type": "application/json",
    [CSRF_HEADER]: token,
    cookie: `${CSRF_COOKIE}=${csrfCookie}`,
  };
}

async function postLogin(instance: FastifyInstance, issued: IssuedCsrf) {
  return instance.inject({
    method: "POST",
    url: "/api/auth/login",
    headers: loginHeaders(issued.token, issued.csrfCookie),
    payload: { usr: "rec", pwd: "fixture" },
  });
}

async function postLoginWith(
  instance: FastifyInstance,
  token: string,
  csrfCookie: string,
) {
  return instance.inject({
    method: "POST",
    url: "/api/auth/login",
    headers: loginHeaders(token, csrfCookie),
    payload: { usr: "rec", pwd: "fixture" },
  });
}

describe("proxy origin and CSRF protection", () => {
  it("rejects a login from a different origin", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: {
        origin: "https://evil.example",
        "content-type": "application/json",
      },
      payload: { usr: "a", pwd: "b" },
    });
    expect(res.statusCode).toBe(403);
  });

  it("rejects a request with neither Origin nor Referer", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: { "content-type": "application/json" },
    });
    expect(res.statusCode).toBe(403);
  });

  it("accepts a same-origin Referer when Origin is absent", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: {
        referer: `${TEST_ORIGIN}/clinic/login`,
        "content-type": "application/json",
      },
    });
    expect(res.statusCode).not.toBe(403);
  });

  it("rejects login without the proxy CSRF header even from the right origin", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: { usr: "a", pwd: "b" },
    });
    expect(res.statusCode).toBe(403);
  });

  it("rejects a state change sent as text/plain", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/logout",
      headers: { origin: TEST_ORIGIN, "content-type": "text/plain" },
    });
    expect(res.statusCode).toBe(415);
  });

  it("rejects a login token issued for a different pre-session", async () => {
    const a = await getCsrf(app);
    const b = await getCsrf(app);
    const res = await postLoginWith(app, a.token, b.csrfCookie);
    expect(res.statusCode).toBe(403);
  });

  it("rotates the token on login and rejects replay of the login token", async () => {
    const first = await getCsrf(app);
    const login = await postLogin(app, first);
    expect(login.statusCode).toBe(200);
    const replay = await postLoginWith(app, first.token, first.csrfCookie);
    expect(replay.statusCode).toBe(403);
    const second = await getCsrf(app, login.cookies);
    expect(second.token).not.toBe(first.token);
  });

  it("returns a SessionDTO and sets an HttpOnly session cookie", async () => {
    const login = await postLogin(app, await getCsrf(app));
    expect(login.json()).toMatchObject({
      user: { id: expect.any(String), roles: expect.any(Array) },
      capabilities: expect.any(Object),
    });
    expect(
      login.cookies.find((cookie) => cookie.name === SESSION_COOKIE)?.httpOnly,
    ).toBe(true);
    expect(JSON.stringify(login.json())).not.toMatch(/csrf|token/i);
  });
});

describe("strict origin matching", () => {
  const malformedOrigins = [
    "null",
    "not-a-url",
    `${TEST_ORIGIN}/clinic`,
    `${TEST_ORIGIN}/`,
    "HTTP://LOCALHOST:5173",
    "https://evil.example#@localhost:5173",
  ];

  it.each(malformedOrigins)("rejects malformed Origin %s", async (origin) => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: { origin, "content-type": "application/json" },
      payload: { usr: "rec", pwd: "fixture" },
    });
    expect(res.statusCode).toBe(403);
  });

  const invalidConfiguredOrigins = [
    "not-an-origin",
    "null",
    "file:///etc/clinic",
    "data:text/html,clinic",
    "ftp://clinic.example",
    "https://user:secret@clinic.example",
  ];

  it.each(invalidConfiguredOrigins)(
    "refuses to start with configured origin %s",
    (appOrigin) => {
      const dir = mkdtempSync(join(tmpdir(), "clinic-proxy-origin-"));
      const databasePath = join(dir, "clinic.sqlite");
      try {
        expect(() =>
          buildServer({
            config: { ...TEST_CONFIG, appOrigin, databasePath },
            authenticate: fakeUpstream,
          }),
        ).toThrow(/INVALID_APP_ORIGIN/);
        expect(existsSync(databasePath)).toBe(false);
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
    },
  );

  it("rejects the literal Origin: null header", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: { origin: "null", "content-type": "application/json" },
      payload: { usr: "rec", pwd: "fixture" },
    });
    expect(res.statusCode).toBe(403);
  });

  it("logs in with a same-origin Referer and no Origin header", async () => {
    const issued = await getCsrf(app);
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: {
        referer: `${TEST_ORIGIN}/clinic/login`,
        "content-type": "application/json",
        [CSRF_HEADER]: issued.token,
        cookie: `${CSRF_COOKIE}=${issued.csrfCookie}`,
      },
      payload: { usr: "rec", pwd: "fixture" },
    });
    expect(res.statusCode).toBe(200);
    expect(
      res.cookies.find((cookie) => cookie.name === SESSION_COOKIE)?.httpOnly,
    ).toBe(true);
  });
});

describe("pre-session claim and concurrency", () => {
  it("rejects a login whose pre-session has expired", async () => {
    const issued = await getCsrf(app);
    db.prepare("UPDATE pre_sessions SET expires_at = ?").run(0);
    const res = await postLogin(app, issued);
    expect(res.statusCode).toBe(403);
  });

  it("rolls back the claimed pre-session when the token is invalid", async () => {
    const issued = await getCsrf(app);
    const bad = await postLoginWith(app, "not-the-token", issued.csrfCookie);
    expect(bad.statusCode).toBe(403);

    const good = await postLogin(app, issued);
    expect(good.statusCode).toBe(200);
  });

  it("rolls back a pre-session when a token from another owner is presented", async () => {
    const ownerA = await getCsrf(app);
    const ownerB = await getCsrf(app);

    const crossOwner = await postLoginWith(
      app,
      ownerA.token,
      ownerB.csrfCookie,
    );
    expect(crossOwner.statusCode).toBe(403);

    const legit = await postLoginWith(app, ownerB.token, ownerB.csrfCookie);
    expect(legit.statusCode).toBe(200);
  });

  it("allows only one of two concurrent logins sharing a pre-session", async () => {
    const first = await getCsrf(app);
    const second = await getCsrfForCookie(app, first.csrfCookie);
    expect(second.token).not.toBe(first.token);
    const results = await Promise.all([
      postLoginWith(app, first.token, first.csrfCookie),
      postLoginWith(app, second.token, first.csrfCookie),
    ]);
    expect(results.map((res) => res.statusCode).sort()).toEqual([200, 403]);
  });
});

describe("live upstream refusal", () => {
  function liveConfig(databasePath: string): AppConfig {
    return { ...TEST_CONFIG, upstreamMode: "live", databasePath };
  }

  it("refuses live mode before creating the database even with an injected authenticator", () => {
    const dir = mkdtempSync(join(tmpdir(), "clinic-proxy-live-"));
    const databasePath = join(dir, "clinic.sqlite");
    try {
      expect(() =>
        buildServer({
          config: liveConfig(databasePath),
          authenticate: async () => null,
        }),
      ).toThrow(/LIVE_LAB_NOT_ENABLED/);
      expect(existsSync(databasePath)).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("refuses live mode before creating the database with the fixture authenticator", () => {
    const dir = mkdtempSync(join(tmpdir(), "clinic-proxy-live-"));
    const databasePath = join(dir, "clinic.sqlite");
    try {
      expect(() => buildServer({ config: liveConfig(databasePath) })).toThrow(
        /LIVE_LAB_NOT_ENABLED/,
      );
      expect(existsSync(databasePath)).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("safe error boundary", () => {
  it("returns a machine code and never the exception text when authentication throws", async () => {
    const secret = "upstream-password-hunter2";
    const records: AuditRecord[] = [];
    const throwDb = new Database(":memory:");
    const throwing = track(
      buildServer({
        config: TEST_CONFIG,
        database: throwDb,
        authenticate: async () => {
          throw new Error(secret);
        },
        auditSink: createAuditSink((record) => records.push(record)),
      }),
      throwDb,
    );

    const res = await postLogin(throwing, await getCsrf(throwing));

    expect(res.statusCode).toBe(502);
    expect(res.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    expect(res.body).not.toContain(secret);
    expect(records.map((record) => record.event)).toEqual([
      "auth.login.upstream_error",
    ]);
    expect(JSON.stringify(records)).not.toContain(secret);
  });

  it("never exposes raw exception text from the global error handler", async () => {
    const secret = "db-password-hunter3";
    app.get("/api/test/boom", async () => {
      throw new Error(secret);
    });

    const res = await app.inject({ method: "GET", url: "/api/test/boom" });

    expect(res.statusCode).toBe(500);
    expect(res.json()).toEqual({ code: "UNKNOWN" });
    expect(res.body).not.toContain(secret);
  });
});

describe("audit sink", () => {
  it("emits allow-listed records without credentials, tokens or cookies", async () => {
    const records: AuditRecord[] = [];
    const auditDb = new Database(":memory:");
    const audited = track(
      buildServer({
        config: TEST_CONFIG,
        database: auditDb,
        authenticate: fakeUpstream,
        auditSink: createAuditSink((record) => records.push(record)),
      }),
      auditDb,
    );

    await audited.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: {
        origin: "https://evil.example",
        "content-type": "application/json",
      },
      payload: { usr: "rec", pwd: "fixture" },
    });

    const issued = await getCsrf(audited);
    await audited.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: {
        origin: TEST_ORIGIN,
        "content-type": "application/json",
        [CSRF_HEADER]: issued.token,
        cookie: `${CSRF_COOKIE}=${issued.csrfCookie}`,
      },
      payload: { usr: "rec", pwd: "wrong-secret" },
    });

    expect(records.map((record) => record.event)).toEqual([
      "auth.login.rejected_origin",
      "auth.login.rejected_credentials",
    ]);
    expect(records[0]).toEqual({
      event: "auth.login.rejected_origin",
      ipHash: expect.any(String),
      route: "/api/auth/login",
      status: 403,
      code: "FORBIDDEN",
    });
    const serialised = JSON.stringify(records);
    expect(serialised).not.toMatch(
      /fixture|wrong-secret|hunter|dc_csrf|dc_session|x-clinic-csrf/i,
    );
  });

  it("writes privacy-safe audit lines to stderr when no sink is injected", async () => {
    const stderr = vi
      .spyOn(process.stderr, "write")
      .mockImplementation(() => true);
    try {
      const defaultDb = new Database(":memory:");
      const defaultApp = track(
        buildServer({
          config: TEST_CONFIG,
          database: defaultDb,
          authenticate: fakeUpstream,
        }),
        defaultDb,
      );

      await defaultApp.inject({
        method: "POST",
        url: "/api/auth/login",
        headers: {
          origin: "https://evil.example",
          "content-type": "application/json",
        },
        payload: { usr: "rec", pwd: "fixture" },
      });

      expect(stderr).toHaveBeenCalledTimes(1);
      const line = String(stderr.mock.calls[0]?.[0] ?? "");
      expect(JSON.parse(line)).toEqual({
        event: "auth.login.rejected_origin",
        ipHash: expect.any(String),
        route: "/api/auth/login",
        status: 403,
        code: "FORBIDDEN",
      });
      expect(line).not.toMatch(
        /fixture|dc_csrf|dc_session|x-clinic-csrf|password|hunter/i,
      );

      const issued = await getCsrf(defaultApp);
      const login = await postLogin(defaultApp, issued);
      expect(login.statusCode).toBe(200);
      expect(stderr).toHaveBeenCalledTimes(1);
    } finally {
      stderr.mockRestore();
    }
  });

  it("drops events outside the allow-list and copies only approved fields", () => {
    const written: AuditRecord[] = [];
    const sink = createAuditSink((record) => written.push(record));

    sink({
      event: "auth.login.unknown" as unknown as AuditEvent,
      ipHash: "abc",
      route: "/api/auth/login",
      status: 403,
      code: "FORBIDDEN",
    });

    sink({
      event: "auth.login.rejected_csrf",
      ipHash: "abc",
      route: "/api/auth/login",
      status: 403,
      code: "FORBIDDEN",
      secret: "hunter4",
    } as unknown as AuditRecord);

    expect(written).toEqual([
      {
        event: "auth.login.rejected_csrf",
        ipHash: "abc",
        route: "/api/auth/login",
        status: 403,
        code: "FORBIDDEN",
      },
    ]);
    expect(JSON.stringify(written)).not.toContain("hunter4");
  });
});
