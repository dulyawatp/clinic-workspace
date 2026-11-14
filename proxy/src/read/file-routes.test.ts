import { createHash } from "node:crypto";
import assets from "@clinic/contracts/fixtures/photo-assets.json" with {
  type: "json",
};
import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, expect, it } from "vitest";
import type { AppConfig } from "../config.js";
import { CSRF_COOKIE } from "../security/constants.js";
import { buildServer } from "../server.js";
import { pngIntrinsicSize } from "./photo-assets.js";

const TEST_CONFIG: AppConfig = {
  appOrigin: "http://localhost:5173",
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

const servers: FastifyInstance[] = [];
const databases: Database.Database[] = [];

function cookiePairs(cookies: Array<{ name: string; value: string }>): string {
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

function csrfCookie(res: {
  cookies: Array<{ name: string; value: string }>;
}): string {
  const value =
    res.cookies.find((cookie) => cookie.name === CSRF_COOKIE)?.value ?? "";
  return `${CSRF_COOKIE}=${value}`;
}

function app(): FastifyInstance {
  const db = new Database(":memory:");
  databases.push(db);
  const server = buildServer({
    config: TEST_CONFIG,
    database: db,
    auditSink: () => {},
  });
  servers.push(server);
  return server;
}

afterEach(async () => {
  await Promise.all(servers.map(async (server) => server.close()));
  servers.length = 0;
  for (const db of databases) db.close();
  databases.length = 0;
});

async function loginAs(target: FastifyInstance, usr: string) {
  const csrfRes = await target.inject({ method: "GET", url: "/api/auth/csrf" });
  const token = (csrfRes.json() as { token: string }).token;
  const login = await target.inject({
    method: "POST",
    url: "/api/auth/login",
    headers: {
      origin: TEST_CONFIG.appOrigin,
      "content-type": "application/json",
      "x-clinic-csrf": token,
      cookie: csrfCookie(csrfRes),
    },
    payload: { usr, pwd: "fixture" },
  });
  return { cookie: cookiePairs(login.cookies) };
}

async function getFile(
  target: FastifyInstance,
  cookie: string | undefined,
  path: string,
) {
  return target.inject({
    method: "GET",
    url: path,
    ...(cookie === undefined ? {} : { headers: { cookie } }),
  });
}

const expectedBytes = (fileId: string) => {
  const asset = assets.assets.find((entry) => entry.fileId === fileId);
  if (asset === undefined) throw new Error(`missing fixture asset ${fileId}`);
  return { asset, bytes: Buffer.from(asset.dataBase64, "base64") };
};

it("streams the allowlisted before/after pair with private headers only", async () => {
  const target = app();
  for (const usr of ["doc", "nur"] as const) {
    const { cookie } = await loginAs(target, usr);
    for (const fileId of ["fixture-photo-1", "fixture-photo-2"]) {
      const { asset, bytes } = expectedBytes(fileId);
      const res = await getFile(target, cookie, `/api/files/${fileId}`);

      expect(res.statusCode, `${usr} ${fileId}`).toBe(200);
      expect(res.headers["content-type"]).toBe("image/png");
      expect(res.headers["x-content-type-options"]).toBe("nosniff");
      expect(res.headers["cache-control"]).toBe("no-store");
      expect(String(res.headers["content-length"])).toBe(String(asset.bytes));
      expect(res.rawPayload.equals(bytes)).toBe(true);
      expect(createHash("sha256").update(res.rawPayload).digest("hex")).toBe(
        asset.sha256,
      );
      expect(pngIntrinsicSize(res.rawPayload)).toEqual({
        width: asset.width,
        height: asset.height,
      });
    }
  }
});

it("requires a session before serving any fixture bytes", async () => {
  const res = await getFile(app(), undefined, "/api/files/fixture-photo-1");
  expect(res.statusCode).toBe(401);
  expect(res.json()).toEqual({ code: "AUTH_EXPIRED" });
  expect(res.headers["content-type"]).toContain("application/json");
  expect(res.headers["cache-control"]).toBe("no-store");
});

it("denies roles outside the clinical photo fixture matrix", async () => {
  const target = app();
  for (const usr of ["rec", "pha", "cas", "mgr", "adm"] as const) {
    const { cookie } = await loginAs(target, usr);
    const res = await getFile(target, cookie, "/api/files/fixture-photo-1");
    expect(res.statusCode, usr).toBe(403);
    expect(res.json()).toEqual({ code: "FORBIDDEN" });
    expect(res.headers["content-type"]).toContain("application/json");
    expect(res.headers["cache-control"]).toBe("no-store");
  }
});

it("denies unknown ids without a path, fixture or upstream fallback", async () => {
  const target = app();
  const { cookie } = await loginAs(target, "doc");
  for (const fileId of [
    "missing-photo",
    "fixture-photo-3",
    "fixture-photo-1.png",
    "fixture-photo-1/extra",
    "a/b/c",
    "..%2F..%2Fpackage.json",
    "..%2F..%2F..%2Fetc%2Fpasswd",
    "https:%2F%2Fexample.com%2Fphoto.png",
    "file:%2F%2F%2Fetc%2Fpasswd",
  ]) {
    const res = await getFile(target, cookie, `/api/files/${fileId}`);
    expect(res.statusCode, fileId).toBe(404);
    expect(res.json()).toEqual({ code: "NOT_FOUND" });
    expect(res.headers["cache-control"]).toBe("no-store");
    expect(Buffer.from(res.rawPayload).includes("clinic-platform")).toBe(false);
    expect(
      res.rawPayload.subarray(0, 8).equals(Buffer.from([0x89, 0x50])),
    ).toBe(false);
  }
});

it("ignores query values and never turns the file route into a reader", async () => {
  const target = app();
  const { cookie } = await loginAs(target, "doc");
  const { bytes } = expectedBytes("fixture-photo-1");
  const res = await getFile(
    target,
    cookie,
    "/api/files/fixture-photo-1?path=..%2F..%2Fpackage.json&url=https:%2F%2Fexample.com",
  );
  expect(res.statusCode).toBe(200);
  expect(res.rawPayload.equals(bytes)).toBe(true);
});
