import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, describe, expect, it } from "vitest";
import type { AppConfig } from "../config.js";
import { buildServer } from "../server.js";

const TEST_CONFIG: AppConfig = {
  appOrigin: "http://localhost:5173",
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

let app: FastifyInstance | undefined;
let db: Database.Database | undefined;

function server(): FastifyInstance {
  db = new Database(":memory:");
  app = buildServer({
    config: TEST_CONFIG,
    database: db,
    authenticate: async () => null,
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

describe("GET /api/health", () => {
  it("reports fixture upstream without leaking configuration", async () => {
    const res = await server().inject({ method: "GET", url: "/api/health" });

    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ status: "ok", upstream: "fixture" });
    expect(res.body).not.toContain(TEST_CONFIG.databasePath);
    expect(res.body).not.toContain(TEST_CONFIG.appOrigin);
    expect(res.body).not.toContain("live");
  });
});
