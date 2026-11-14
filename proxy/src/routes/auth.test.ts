import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppConfig } from "../config.js";
import { type AuditRecord, createAuditSink } from "../security/audit.js";
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  CSRF_ISSUANCE_LIMIT_PER_CLIENT,
  CSRF_ISSUANCE_WINDOW_MS,
  MAX_PENDING_PRE_SESSIONS,
  MAX_UNUSED_CSRF_TOKENS,
  SESSION_COOKIE,
} from "../security/constants.js";
import { CsrfStore } from "../security/csrf-store.js";
import { buildServer } from "../server.js";
import { SessionStore } from "../session/store.js";
import { fixtureAuthenticate } from "./auth.js";

const TEST_ORIGIN = "http://localhost:5173";

const TEST_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

// The only account source is the shared fixture file; no upstream is contacted.
const fixtureAuth = fixtureAuthenticate();

const silentAudit = () => {};

interface IssuedCsrf {
  token: string;
  csrfCookie: string;
}

let db: Database.Database;
let csrf: CsrfStore;
let sessions: SessionStore;
let app: FastifyInstance;

beforeEach(() => {
  db = new Database(":memory:");
  csrf = new CsrfStore(db);
  sessions = new SessionStore(db);
  app = buildServer({
    config: TEST_CONFIG,
    database: db,
    authenticate: fixtureAuth,
    auditSink: silentAudit,
  });
});

afterEach(async () => {
  await app.close();
  db.close();
});

function cookiePairs(cookies: Array<{ name: string; value: string }>): string {
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

function cookieValue(
  response: { cookies: Array<{ name: string; value: string }> },
  name: string,
): string {
  return response.cookies.find((cookie) => cookie.name === name)?.value ?? "";
}

async function getCsrf(
  cookies?: Array<{ name: string; value: string }>,
): Promise<IssuedCsrf> {
  const res = await app.inject({
    method: "GET",
    url: "/api/auth/csrf",
    headers: cookies === undefined ? {} : { cookie: cookiePairs(cookies) },
  });
  expect(res.statusCode).toBe(200);
  return {
    token: (res.json() as { token: string }).token,
    csrfCookie: cookieValue(res, CSRF_COOKIE),
  };
}

async function postLogin(issued: IssuedCsrf, usr = "rec", pwd = "fixture") {
  return app.inject({
    method: "POST",
    url: "/api/auth/login",
    headers: {
      origin: TEST_ORIGIN,
      "content-type": "application/json",
      [CSRF_HEADER]: issued.token,
      cookie: `${CSRF_COOKIE}=${issued.csrfCookie}`,
    },
    payload: { usr, pwd },
  });
}

async function postLogout(
  token: string,
  cookies: Array<{ name: string; value: string }>,
) {
  return app.inject({
    method: "POST",
    url: "/api/auth/logout",
    headers: {
      origin: TEST_ORIGIN,
      "content-type": "application/json",
      [CSRF_HEADER]: token,
      cookie: cookiePairs(cookies),
    },
  });
}

describe("fixture accounts", () => {
  const FIXTURE_LOGINS = [
    ["rec", "REC", "fixture-rec", "Fixture Receptionist"],
    ["doc", "DOC", "fixture-doc", "Fixture Doctor"],
    ["nur", "NUR", "fixture-nur", "Fixture Nurse"],
    ["pha", "PHA", "fixture-pha", "Fixture Pharmacist"],
    ["cas", "CAS", "fixture-cas", "Fixture Cashier"],
    ["mgr", "MGR", "fixture-mgr", "Fixture Manager"],
    ["adm", "ADM", "fixture-adm", "Fixture Administrator"],
  ] as const;

  it.each(FIXTURE_LOGINS)(
    "authenticates the %s fixture account as %s",
    async (usr, role, id, displayName) => {
      const login = await postLogin(await getCsrf(), usr);

      expect(login.statusCode).toBe(200);
      expect(cookieValue(login, SESSION_COOKIE)).not.toBe("");
      const body = login.json() as {
        user: { id: string; displayName: string; roles: string[] };
      };
      expect(body.user.id).toBe(id);
      expect(body.user.displayName).toBe(displayName);
      expect(body.user.roles).toEqual([role]);
      expect(body.user.roles).toHaveLength(1);
    },
  );

  it.each(FIXTURE_LOGINS)(
    "rejects the %s fixture account with the wrong password",
    async (usr) => {
      const login = await postLogin(await getCsrf(), usr, "not-fixture");

      expect(login.statusCode).toBe(403);
      expect(login.json()).toEqual({ code: "FORBIDDEN" });
      expect(cookieValue(login, SESSION_COOKIE)).toBe("");
    },
  );

  it("rejects an unknown fixture account", async () => {
    const login = await postLogin(await getCsrf(), "unknown");

    expect(login.statusCode).toBe(403);
    expect(login.json()).toEqual({ code: "FORBIDDEN" });
    expect(cookieValue(login, SESSION_COOKIE)).toBe("");
  });
});

describe("JSON body hardening", () => {
  const poisonedBodies = [
    '{"__proto__":{"isAdmin":true},"usr":"rec","pwd":"fixture"}',
    '{"constructor":{"prototype":{"isAdmin":true}},"usr":"rec","pwd":"fixture"}',
    '{"usr":"rec","pwd":"fixture","nested":{"__proto__":{"polluted":true}}}',
    '{"usr":"rec","pwd":"fixture","nested":{"constructor":{"prototype":{"polluted":true}}}}',
  ];

  it.each(poisonedBodies)(
    "rejects prototype-poisoned JSON with a safe 400: %s",
    async (body) => {
      const res = await app.inject({
        method: "POST",
        url: "/api/auth/login",
        headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
        payload: body,
      });

      expect(res.statusCode).toBe(400);
      expect(res.json()).toEqual({ code: "VALIDATION" });
      expect(({} as Record<string, unknown>).isAdmin).toBeUndefined();
      expect(({} as Record<string, unknown>).polluted).toBeUndefined();
      expect(Object.prototype).not.toHaveProperty("isAdmin");
      expect(Object.prototype).not.toHaveProperty("polluted");
    },
  );

  it("maps malformed JSON to a safe 400 without parser details", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
      payload: '{"usr":',
    });

    expect(res.statusCode).toBe(400);
    expect(res.json()).toEqual({ code: "VALIDATION" });
    expect(res.body).not.toMatch(
      /unexpected|json|fst_err|position|stack|syntax/i,
    );
  });

  it("keeps the empty-body exception narrow to bodyless requests", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
    });

    expect(res.statusCode).toBe(400);
    expect(res.json()).toEqual({ code: "VALIDATION" });
  });
});

describe("logout", () => {
  it("destroys the session and revokes its CSRF tokens", async () => {
    const login = await postLogin(await getCsrf());
    const sid = cookieValue(login, SESSION_COOKIE);
    expect(sid).not.toBe("");
    const token = await getCsrf(login.cookies);

    const out = await postLogout(token.token, login.cookies);

    expect(out.statusCode).toBe(204);
    expect(sessions.get(sid)).toBeUndefined();
    expect(csrf.verify(sid, token.token, "session")).toBe(false);
  });

  it("is idempotent when the session is already gone", async () => {
    const out = await app.inject({
      method: "POST",
      url: "/api/auth/logout",
      headers: { origin: TEST_ORIGIN, "content-type": "application/json" },
    });

    expect(out.statusCode).toBe(204);
    expect(cookieValue(out, SESSION_COOKIE)).toBe("");
    expect(cookieValue(out, CSRF_COOKIE)).toBe("");
  });

  it("is idempotent when the session row is gone but a stale cookie remains", async () => {
    const login = await postLogin(await getCsrf());
    const token = await getCsrf(login.cookies);
    const first = await postLogout(token.token, login.cookies);
    expect(first.statusCode).toBe(204);

    const replay = await postLogout(token.token, login.cookies);

    expect(replay.statusCode).toBe(204);
    expect(cookieValue(replay, SESSION_COOKIE)).toBe("");
  });

  it("rejects logout without the proxy CSRF header and keeps the session", async () => {
    const login = await postLogin(await getCsrf());
    const sid = cookieValue(login, SESSION_COOKIE);

    const out = await app.inject({
      method: "POST",
      url: "/api/auth/logout",
      headers: {
        origin: TEST_ORIGIN,
        "content-type": "application/json",
        cookie: cookiePairs(login.cookies),
      },
    });

    expect(out.statusCode).toBe(403);
    expect(sessions.get(sid)).toBeDefined();
  });

  it("rejects logout from a different origin", async () => {
    const login = await postLogin(await getCsrf());
    const sid = cookieValue(login, SESSION_COOKIE);
    const token = await getCsrf(login.cookies);

    const out = await app.inject({
      method: "POST",
      url: "/api/auth/logout",
      headers: {
        origin: "https://evil.example",
        "content-type": "application/json",
        [CSRF_HEADER]: token.token,
        cookie: cookiePairs(login.cookies),
      },
    });

    expect(out.statusCode).toBe(403);
    expect(sessions.get(sid)).toBeDefined();
  });
});

describe("auth cache headers", () => {
  it("sets Cache-Control: no-store on anonymous CSRF", async () => {
    const res = await app.inject({ method: "GET", url: "/api/auth/csrf" });
    expect(res.statusCode).toBe(200);
    expect(res.headers["cache-control"]).toBe("no-store");
  });

  it("sets no-store on authenticated CSRF, login and logout", async () => {
    const login = await postLogin(await getCsrf());
    expect(login.headers["cache-control"]).toBe("no-store");

    const authenticated = await app.inject({
      method: "GET",
      url: "/api/auth/csrf",
      headers: { cookie: cookiePairs(login.cookies) },
    });
    expect(authenticated.statusCode).toBe(200);
    expect(authenticated.headers["cache-control"]).toBe("no-store");

    const logout = await app.inject({
      method: "POST",
      url: "/api/auth/logout",
      headers: {
        origin: TEST_ORIGIN,
        "content-type": "application/json",
        [CSRF_HEADER]: (authenticated.json() as { token: string }).token,
        cookie: cookiePairs(login.cookies),
      },
    });
    expect(logout.statusCode).toBe(204);
    expect(logout.headers["cache-control"]).toBe("no-store");
  });

  it("sets no-store on rejected auth responses", async () => {
    const res = await app.inject({
      method: "POST",
      url: "/api/auth/login",
      headers: {
        origin: "https://evil.example",
        "content-type": "application/json",
      },
      payload: { usr: "rec", pwd: "fixture" },
    });
    expect(res.statusCode).toBe(403);
    expect(res.headers["cache-control"]).toBe("no-store");
  });
});

describe("bounded csrf issuance", () => {
  function count(table: "pre_sessions" | "csrf_tokens"): number {
    const row = db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get() as {
      count: number;
    };
    return row.count;
  }

  it("caps anonymous pre-session issuance and fails closed", async () => {
    // One caller per handshake so the per-caller throttle is not the limit
    // under test; the global cap is.
    for (let i = 0; i < MAX_PENDING_PRE_SESSIONS; i += 1) {
      const res = await app.inject({
        method: "GET",
        url: "/api/auth/csrf",
        remoteAddress: `10.0.0.${i}`,
      });
      expect(res.statusCode).toBe(200);
    }

    const blocked = await app.inject({ method: "GET", url: "/api/auth/csrf" });
    expect(blocked.statusCode).toBe(429);
    expect(blocked.json()).toEqual({ code: "RATE_LIMITED" });
    expect(blocked.headers["cache-control"]).toBe("no-store");
    expect(count("pre_sessions")).toBe(MAX_PENDING_PRE_SESSIONS);
  }, 30_000);

  it("keeps repeated fresh anonymous requests bounded at the cap", async () => {
    for (let i = 0; i < MAX_PENDING_PRE_SESSIONS + 25; i += 1) {
      await app.inject({
        method: "GET",
        url: "/api/auth/csrf",
        remoteAddress: `10.0.1.${i % 250}`,
      });
    }

    expect(count("pre_sessions")).toBe(MAX_PENDING_PRE_SESSIONS);
    expect(count("csrf_tokens")).toBeLessThanOrEqual(
      MAX_PENDING_PRE_SESSIONS * MAX_UNUSED_CSRF_TOKENS,
    );
  }, 30_000);

  it("recovers capacity once an expired handshake is reclaimed", async () => {
    for (let i = 0; i < MAX_PENDING_PRE_SESSIONS; i += 1) {
      expect(sessions.createPreSession()).not.toBeNull();
    }
    const blocked = await app.inject({ method: "GET", url: "/api/auth/csrf" });
    expect(blocked.statusCode).toBe(429);

    db.prepare(
      "UPDATE pre_sessions SET expires_at = 0 WHERE id = (SELECT id FROM pre_sessions LIMIT 1)",
    ).run();
    const recovered = await app.inject({
      method: "GET",
      url: "/api/auth/csrf",
    });

    expect(recovered.statusCode).toBe(200);
    expect(count("pre_sessions")).toBe(MAX_PENDING_PRE_SESSIONS);
  });

  it("rotates unused tokens per owner instead of appending rows", async () => {
    const first = await getCsrf();
    for (let i = 0; i < MAX_UNUSED_CSRF_TOKENS + 3; i += 1) {
      await getCsrf([{ name: CSRF_COOKIE, value: first.csrfCookie }]);
    }

    const ownerTokens = db
      .prepare("SELECT COUNT(*) AS count FROM csrf_tokens WHERE owner_id = ?")
      .get(first.csrfCookie) as { count: number };
    expect(ownerTokens.count).toBeLessThanOrEqual(MAX_UNUSED_CSRF_TOKENS);

    const latest = await getCsrf([
      { name: CSRF_COOKIE, value: first.csrfCookie },
    ]);
    const login = await postLogin(latest);
    expect(login.statusCode).toBe(200);
  });
});

describe("auth row reclamation", () => {
  it("purges expired, consumed and abandoned rows without breaking a live handshake", async () => {
    const abandoned = sessions.createPreSession();
    expect(abandoned).not.toBeNull();
    csrf.issue(abandoned ?? "", "login");
    sessions.destroyPreSession(abandoned ?? "");

    const expiredPre = sessions.createPreSession();
    expect(expiredPre).not.toBeNull();
    csrf.issue(expiredPre ?? "", "login");
    db.prepare("UPDATE pre_sessions SET expires_at = 0 WHERE id = ?").run(
      expiredPre,
    );

    const live = sessions.createPreSession();
    expect(live).not.toBeNull();
    const consumed = csrf.issue(live ?? "", "login");
    expect(csrf.consume(live ?? "", consumed, "login")).toBe(true);

    csrf.issue("ghost-session", "session");

    const res = await app.inject({ method: "GET", url: "/api/auth/csrf" });
    expect(res.statusCode).toBe(200);

    const rows = db
      .prepare("SELECT owner_id, used_at FROM csrf_tokens")
      .all() as Array<{ owner_id: string; used_at: number | null }>;
    expect(
      rows.some(
        (row) => row.owner_id === abandoned || row.owner_id === "ghost-session",
      ),
    ).toBe(false);
    expect(rows.every((row) => row.used_at === null)).toBe(true);

    const expiredTokens = db
      .prepare(
        "SELECT COUNT(*) AS count FROM csrf_tokens WHERE expires_at <= ?",
      )
      .get(Date.now()) as { count: number };
    expect(expiredTokens.count).toBe(0);
    const expiredPreSessions = db
      .prepare(
        "SELECT COUNT(*) AS count FROM pre_sessions WHERE expires_at <= ?",
      )
      .get(Date.now()) as { count: number };
    expect(expiredPreSessions.count).toBe(0);

    const login = await postLogin({
      token: (res.json() as { token: string }).token,
      csrfCookie: cookieValue(res, CSRF_COOKIE),
    });
    expect(login.statusCode).toBe(200);
  });
});

describe("issuance logging hygiene", () => {
  it("never logs or stores plaintext issuance tokens", async () => {
    const records: AuditRecord[] = [];
    const stderrLines: string[] = [];
    const leakDb = new Database(":memory:");
    const leakApp = buildServer({
      config: TEST_CONFIG,
      database: leakDb,
      authenticate: fixtureAuth,
      auditSink: createAuditSink((record) => records.push(record)),
    });
    const stderr = vi.spyOn(process.stderr, "write").mockImplementation(((
      chunk: unknown,
    ) => {
      stderrLines.push(String(chunk));
      return true;
    }) as never);

    try {
      const tokens: string[] = [];
      for (let i = 0; i < 5; i += 1) {
        const res = await leakApp.inject({
          method: "GET",
          url: "/api/auth/csrf",
        });
        tokens.push((res.json() as { token: string }).token);
      }

      expect(records).toEqual([]);
      const output = stderrLines.join("");
      for (const token of tokens) {
        expect(output).not.toContain(token);
      }
      const stored = JSON.stringify(
        leakDb.prepare("SELECT token_hash FROM csrf_tokens").all(),
      );
      for (const token of tokens) {
        expect(stored).not.toContain(token);
      }
    } finally {
      stderr.mockRestore();
      await leakApp.close();
      leakDb.close();
    }
  });
});

describe("caller scoped issuance throttle", () => {
  const abusiveCaller = "198.51.100.7";
  const otherCaller = "198.51.100.8";

  function preSessionCount(): number {
    return (
      db.prepare("SELECT COUNT(*) AS count FROM pre_sessions").get() as {
        count: number;
      }
    ).count;
  }

  it("throttles one abusive caller well before the global cap", async () => {
    for (let i = 0; i < CSRF_ISSUANCE_LIMIT_PER_CLIENT; i += 1) {
      const res = await app.inject({
        method: "GET",
        url: "/api/auth/csrf",
        remoteAddress: abusiveCaller,
      });
      expect(res.statusCode).toBe(200);
    }

    const blocked = await app.inject({
      method: "GET",
      url: "/api/auth/csrf",
      remoteAddress: abusiveCaller,
    });

    expect(blocked.statusCode).toBe(429);
    expect(blocked.json()).toEqual({ code: "RATE_LIMITED" });
    expect(blocked.headers["cache-control"]).toBe("no-store");
    expect(preSessionCount()).toBe(CSRF_ISSUANCE_LIMIT_PER_CLIENT);
    expect(preSessionCount()).toBeLessThan(MAX_PENDING_PRE_SESSIONS);
  });

  it("still issues a handshake to another caller and lets them log in", async () => {
    for (let i = 0; i < CSRF_ISSUANCE_LIMIT_PER_CLIENT; i += 1) {
      await app.inject({
        method: "GET",
        url: "/api/auth/csrf",
        remoteAddress: abusiveCaller,
      });
    }
    expect(
      (
        await app.inject({
          method: "GET",
          url: "/api/auth/csrf",
          remoteAddress: abusiveCaller,
        })
      ).statusCode,
    ).toBe(429);

    const other = await app.inject({
      method: "GET",
      url: "/api/auth/csrf",
      remoteAddress: otherCaller,
    });

    expect(other.statusCode).toBe(200);
    const login = await postLogin({
      token: (other.json() as { token: string }).token,
      csrfCookie: cookieValue(other, CSRF_COOKIE),
    });
    expect(login.statusCode).toBe(200);
  });

  it("keys the throttle on the socket address, not X-Forwarded-For", async () => {
    for (let i = 0; i < CSRF_ISSUANCE_LIMIT_PER_CLIENT; i += 1) {
      await app.inject({
        method: "GET",
        url: "/api/auth/csrf",
        remoteAddress: abusiveCaller,
        headers: { "x-forwarded-for": `203.0.113.${i}` },
      });
    }

    const spoofedHeader = await app.inject({
      method: "GET",
      url: "/api/auth/csrf",
      remoteAddress: abusiveCaller,
      headers: { "x-forwarded-for": "203.0.113.250" },
    });
    expect(spoofedHeader.statusCode).toBe(429);

    // The spoofed address is a different socket caller and is not throttled.
    const spoofedSocket = await app.inject({
      method: "GET",
      url: "/api/auth/csrf",
      remoteAddress: "203.0.113.250",
    });
    expect(spoofedSocket.statusCode).toBe(200);
  });

  it("recovers after the throttle window expires", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-05T00:00:00Z"));
    try {
      for (let i = 0; i < CSRF_ISSUANCE_LIMIT_PER_CLIENT; i += 1) {
        const res = await app.inject({
          method: "GET",
          url: "/api/auth/csrf",
          remoteAddress: abusiveCaller,
        });
        expect(res.statusCode).toBe(200);
      }
      expect(
        (
          await app.inject({
            method: "GET",
            url: "/api/auth/csrf",
            remoteAddress: abusiveCaller,
          })
        ).statusCode,
      ).toBe(429);

      vi.advanceTimersByTime(CSRF_ISSUANCE_WINDOW_MS + 1);

      const recovered = await app.inject({
        method: "GET",
        url: "/api/auth/csrf",
        remoteAddress: abusiveCaller,
      });
      expect(recovered.statusCode).toBe(200);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not log the client address or tokens when throttling", async () => {
    const records: AuditRecord[] = [];
    const stderrLines: string[] = [];
    const throttleDb = new Database(":memory:");
    const throttleApp = buildServer({
      config: TEST_CONFIG,
      database: throttleDb,
      authenticate: fixtureAuth,
      auditSink: createAuditSink((record) => records.push(record)),
    });
    const stderr = vi.spyOn(process.stderr, "write").mockImplementation(((
      chunk: unknown,
    ) => {
      stderrLines.push(String(chunk));
      return true;
    }) as never);

    try {
      const tokens: string[] = [];
      for (let i = 0; i < CSRF_ISSUANCE_LIMIT_PER_CLIENT; i += 1) {
        const res = await throttleApp.inject({
          method: "GET",
          url: "/api/auth/csrf",
          remoteAddress: abusiveCaller,
        });
        tokens.push((res.json() as { token: string }).token);
      }

      const blocked = await throttleApp.inject({
        method: "GET",
        url: "/api/auth/csrf",
        remoteAddress: abusiveCaller,
      });
      expect(blocked.statusCode).toBe(429);

      expect(records).toEqual([]);
      const output = stderrLines.join("");
      expect(output).not.toContain(abusiveCaller);
      for (const token of tokens) {
        expect(output).not.toContain(token);
      }
    } finally {
      stderr.mockRestore();
      await throttleApp.close();
      throttleDb.close();
    }
  });
});
