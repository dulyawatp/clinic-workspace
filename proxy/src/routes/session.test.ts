import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, describe, expect, it } from "vitest";
import type { AppConfig } from "../config.js";
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  SESSION_COOKIE,
} from "../security/constants.js";
import { buildServer } from "../server.js";

const TEST_ORIGIN = "http://localhost:5173";

const TEST_CONFIG: AppConfig = {
  appOrigin: TEST_ORIGIN,
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

let app: FastifyInstance | undefined;
let db: Database.Database | undefined;

function server(): FastifyInstance {
  db = new Database(":memory:");
  // Fixture authenticator only: no live upstream is ever contacted.
  app = buildServer({
    config: TEST_CONFIG,
    database: db,
    auditSink: () => {},
  });
  return app;
}

afterEach(async () => {
  await app?.close();
  db?.close();
  app = undefined;
  db = undefined;
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

async function loginAsTestUser(
  target: FastifyInstance,
): Promise<{ cookie: string; sessionEpoch: string }> {
  const csrfRes = await target.inject({
    method: "GET",
    url: "/api/auth/csrf",
  });
  const csrfToken = (csrfRes.json() as { token: string }).token;

  const loginRes = await target.inject({
    method: "POST",
    url: "/api/auth/login",
    headers: {
      origin: TEST_ORIGIN,
      "content-type": "application/json",
      [CSRF_HEADER]: csrfToken,
      cookie: `${CSRF_COOKIE}=${cookieValue(csrfRes, CSRF_COOKIE)}`,
    },
    payload: { usr: "rec", pwd: "fixture" },
  });
  expect(loginRes.statusCode).toBe(200);

  return {
    cookie: cookiePairs(loginRes.cookies),
    sessionEpoch: loginRes.json().sessionEpoch as string,
  };
}

describe("GET /api/session", () => {
  it("keeps non-secret epoch stable across checks but distinct for same-user logins; epoch is not a credential", async () => {
    const instance = server();
    const first = await loginAsTestUser(instance);
    const second = await loginAsTestUser(instance);
    expect(first.sessionEpoch).toEqual(expect.any(String));
    expect(second.sessionEpoch).not.toBe(first.sessionEpoch);
    for (const login of [first, second]) {
      const checked = await instance.inject({
        method: "GET",
        url: "/api/session",
        headers: { cookie: login.cookie },
      });
      expect(checked.json().sessionEpoch).toBe(login.sessionEpoch);
      expect(login.cookie).not.toContain(login.sessionEpoch);
    }
    const rejected = await instance.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie: `${SESSION_COOKIE}=${first.sessionEpoch}` },
    });
    expect(rejected.statusCode).toBe(401);
  });
  it("returns 401 for an unauthenticated session", async () => {
    const res = await server().inject({ method: "GET", url: "/api/session" });

    expect(res.statusCode).toBe(401);
    expect(res.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(res.headers["cache-control"]).toBe("no-store");
  });

  it("returns the exact SessionDTO shape for an authenticated session", async () => {
    const instance = server();
    const { cookie } = await loginAsTestUser(instance);

    const res = await instance.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie },
    });

    expect(res.statusCode).toBe(200);
    expect(res.headers["cache-control"]).toBe("no-store");
    expect(res.json()).toEqual({
      mode: "fixture",
      sessionEpoch: expect.any(String),
      user: {
        id: expect.any(String),
        displayName: expect.any(String),
        roles: expect.any(Array),
      },
      capabilities: expect.any(Object),
    });
  });

  it("keeps every capability read-only without a verified manifest", async () => {
    const instance = server();
    const { cookie } = await loginAsTestUser(instance);

    const res = await instance.inject({
      method: "GET",
      url: "/api/session",
      headers: { cookie },
    });
    const body = res.json() as {
      capabilities: Record<string, string>;
    };

    expect(Object.values(body.capabilities).length).toBeGreaterThan(0);
    expect(
      Object.values(body.capabilities).every((state) => state === "read_only"),
    ).toBe(true);
  });
});
