import { createHash } from "node:crypto";
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
const PATIENT_1 = `${LAB_ORIGIN}/api/resource/Patient/SYNTH-PATIENT-1`;
const PATIENT_2 = `${LAB_ORIGIN}/api/resource/Patient/SYNTH-PATIENT-2`;
const LIST_URL = `${LAB_ORIGIN}/api/resource/Clinic%20Photo?`;
const PHOTO_URL = `${LAB_ORIGIN}/api/resource/Clinic%20Photo/PHOTO1?`;
const FILE_URL = `${LAB_ORIGIN}/api/resource/File/file-1.png?`;
// Native protected route for private files. It runs `find_file_by_url` +
// `is_downloadable` and streams stored bytes, unlike `frappe.handler.download_file`
// which goes through `File.get_content` and can decode binary into `str`.
const CONTENT_URL = `${LAB_ORIGIN}/private/files/file-1.png`;

const NATIVE_SID = "native-sid-value";
const PHOTO_BYTES = Buffer.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
]);
const PHOTO_SHA = createHash("sha256").update(PHOTO_BYTES).digest("hex");

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

const nurse: Authenticate = async () => ({
  user: { id: "nurse@lab.example", displayName: "Nurse", roles: ["NUR"] },
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

function photoRow(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    name: "PHOTO1",
    patient_reference: "SYNTH-PATIENT-1",
    original_file: "file-1.png",
    original_sha256: PHOTO_SHA,
    original_width: 10,
    original_height: 10,
    creation: "2026-11-11 10:00:00",
    ...overrides,
  };
}

function fileRow(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    file_url: "/private/files/file-1.png",
    file_name: "file-1.png",
    is_private: 1,
    file_size: PHOTO_BYTES.length,
    ...overrides,
  };
}

function defaultHandler(url: string): Response {
  if (url === `${LAB_ORIGIN}/api/method/frappe.auth.get_logged_user`) {
    return jsonResponse({ message: "clinician@lab.example" });
  }
  if (url === PATIENT_1 || url === PATIENT_2) {
    return jsonResponse({
      data: { name: url === PATIENT_1 ? "SYNTH-PATIENT-1" : "SYNTH-PATIENT-2" },
    });
  }
  if (url.startsWith(LIST_URL)) {
    return jsonResponse({ data: [photoRow()] });
  }
  if (url.startsWith(PHOTO_URL)) {
    return jsonResponse({ data: photoRow() });
  }
  if (url.startsWith(FILE_URL)) {
    return jsonResponse({ data: fileRow() });
  }
  if (url === CONTENT_URL) {
    return new Response(PHOTO_BYTES, {
      status: 200,
      headers: { "content-type": "image/png" },
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

async function login(app: FastifyInstance): Promise<string> {
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
    payload: { usr: "clinician@lab.example", pwd: "secret" },
  });
  expect(response.statusCode).toBe(200);
  return (
    response.cookies.find((cookie) => cookie.name === SESSION_COOKIE)?.value ??
    ""
  );
}

function get(
  app: FastifyInstance,
  url: string,
  sessionId: string,
  extraHeaders: Record<string, string> = {},
) {
  return app.inject({
    method: "GET",
    url,
    headers: { cookie: `${SESSION_COOKIE}=${sessionId}`, ...extraHeaders },
  });
}

describe("live opt-in and authorization", () => {
  it("does not expose live photo reads in fixture mode", async () => {
    const db = new Database(":memory:");
    openDbs.push(db);
    const fetchImpl = vi.fn();
    const app = buildServer({
      config: FIXTURE_CONFIG,
      database: db,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    openApps.push(app);

    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-patients/PAT-1/photos",
    });

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({ code: "UNSUPPORTED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses an unauthenticated content read without contacting the lab", async () => {
    const { app, fetchImpl } = buildLive();

    const response = await app.inject({
      method: "GET",
      url: "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
    });

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("requires a live clinician role before listing", async () => {
    const { app, fetchImpl } = buildLive(defaultHandler, receptionist);
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos",
      sessionId,
    );

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("lets a nurse list photos of a patient their native session can read", async () => {
    const { app, fetchImpl } = buildLive(defaultHandler, nurse);
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos",
      sessionId,
    );

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      items: [
        {
          photo_id: "PHOTO1",
          patient_reference: "SYNTH-PATIENT-1",
          sha256: PHOTO_SHA,
          width: 10,
          height: 10,
          created_at: "2026-11-11 10:00:00",
          content_url:
            "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
        },
      ],
    });
    // The native session, not the proxy role, is what the lab authorizes with.
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(PATIENT_1);
  });

  it("lets a nurse read photo bytes with the native session", async () => {
    const { app, fetchImpl } = buildLive(defaultHandler, nurse);
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      sessionId,
    );

    expect(response.statusCode).toBe(200);
    expect(response.rawPayload.equals(PHOTO_BYTES)).toBe(true);
    const download = fetchImpl.mock.calls.at(-1)?.[1] as RequestInit;
    expect(download.headers).toMatchObject({ cookie: `sid=${NATIVE_SID}` });
  });

  it("still denies a nurse whose native session cannot read the patient", async () => {
    const { app, fetchImpl } = buildLive(
      (url) =>
        url === PATIENT_1
          ? jsonResponse({ exc_type: "PermissionError" }, 403)
          : defaultHandler(url),
      nurse,
    );
    const sessionId = await login(app);

    for (const url of [
      "/api/live/synthetic-patients/PAT-1/photos",
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
    ]) {
      const response = await get(app, url, sessionId);
      expect(response.statusCode).toBe(403);
      expect(response.json()).toEqual({ code: "FORBIDDEN" });
    }
    expect(
      fetchImpl.mock.calls.some(
        ([url]) =>
          String(url).startsWith(LIST_URL) ||
          String(url).startsWith(PHOTO_URL) ||
          String(url) === CONTENT_URL,
      ),
    ).toBe(false);
  });
});

describe("live photo listing", () => {
  it("lists only native fields bound to the mapped patient", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos?limit=10&offset=5",
      sessionId,
    );

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      items: [
        {
          photo_id: "PHOTO1",
          patient_reference: "SYNTH-PATIENT-1",
          sha256: PHOTO_SHA,
          width: 10,
          height: 10,
          created_at: "2026-11-11 10:00:00",
          content_url:
            "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
        },
      ],
    });

    const list = fetchImpl.mock.calls.find(([url]) =>
      String(url).startsWith(LIST_URL),
    );
    const target = new URL(String(list?.[0]));
    expect(target.searchParams.get("filters")).toBe(
      JSON.stringify([["patient_reference", "=", "SYNTH-PATIENT-1"]]),
    );
    expect(target.searchParams.get("limit")).toBe("10");
    expect(target.searchParams.get("limit_start")).toBe("5");
    expect((list?.[1] as RequestInit | undefined)?.headers).toMatchObject({
      cookie: `sid=${NATIVE_SID}`,
    });
    expect(response.body).not.toMatch(/category|bodyArea|consent|viewpoint/i);
  });

  it("rejects a cross-patient row instead of describing it", async () => {
    const { app } = buildLive((url) =>
      url.startsWith(LIST_URL)
        ? jsonResponse({
            data: [photoRow({ patient_reference: "SYNTH-PATIENT-2" })],
          })
        : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos",
      sessionId,
    );

    expect(response.statusCode).toBe(502);
    expect(response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  });

  it("rejects an unmapped or traversal-shaped workspace reference", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    for (const reference of ["PAT-UNKNOWN", "..", "PAT%2F1"]) {
      const response = await get(
        app,
        `/api/live/synthetic-patients/${reference}/photos`,
        sessionId,
      );
      expect([400, 404]).toContain(response.statusCode);
      if (response.statusCode === 400) {
        expect(response.json()).toEqual({ code: "VALIDATION" });
      }
    }
    // An unmapped reference never reaches a live call.
    expect(
      fetchImpl.mock.calls.filter(([url]) => String(url).startsWith(LIST_URL)),
    ).toHaveLength(0);
  });

  it("denies a patient the clinician cannot read", async () => {
    const { app, fetchImpl } = buildLive((url) =>
      url === PATIENT_1
        ? jsonResponse({ exc_type: "PermissionError" }, 403)
        : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos",
      sessionId,
    );

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(
      fetchImpl.mock.calls.some(([url]) => String(url).startsWith(LIST_URL)),
    ).toBe(false);
  });

  it("invalidates the session when the native patient read expires", async () => {
    const { app, native } = buildLive((url) =>
      url === PATIENT_1 ? jsonResponse({}, 401) : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos",
      sessionId,
    );

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(native.get(sessionId)).toBeUndefined();
  });
});

describe("live photo bytes", () => {
  it("delivers native private bytes through the protected private-files path", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content?file_url=/private/files/evil.png",
      sessionId,
    );

    expect(response.statusCode).toBe(200);
    expect(response.rawPayload.equals(PHOTO_BYTES)).toBe(true);
    expect(response.headers["content-type"]).toBe("image/png");
    expect(response.headers["content-length"]).toBe(String(PHOTO_BYTES.length));
    expect(response.headers["x-content-type-options"]).toBe("nosniff");
    expect(response.headers["cache-control"]).toBe("no-store");

    const calls = fetchImpl.mock.calls.map(([url]) => String(url));
    expect(calls[0]).toBe(PATIENT_1);
    expect(calls[1]).toContain("/api/resource/Clinic%20Photo/PHOTO1");
    expect(calls[2]).toContain("/api/resource/File/file-1.png");
    // Exact canonical private URL: no query string, no client-supplied path.
    expect(calls[3]).toBe(CONTENT_URL);
    const download = fetchImpl.mock.calls[3]?.[1] as RequestInit;
    expect(download.headers).toMatchObject({
      accept: "image/png,image/jpeg",
      cookie: `sid=${NATIVE_SID}`,
    });
    expect(download.redirect).toBe("manual");
  });

  it("returns a 74-byte PNG byte-for-byte instead of a decoded upstream string", async () => {
    // 2x2 8-bit grayscale PNG, exactly 74 bytes: the size/shape at which
    // `frappe.handler.download_file` -> `File.get_content` can hand back a
    // decoded `str` and corrupt the body into a 415.
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAAAAABX3VL4AAAAEUlEQVR4AQEGAPn/AAAAAAAAAAYAAazUVfoAAAAASUVORK5CYII=",
      "base64",
    );
    expect(png.length).toBe(74);
    expect([0x89, 0x50, 0x4e, 0x47]).toEqual([...png.subarray(0, 4)]);

    const sha = createHash("sha256").update(png).digest("hex");
    const { app, fetchImpl } = buildLive((url) => {
      if (url === CONTENT_URL) {
        return new Response(png, {
          status: 200,
          headers: { "content-type": "image/png" },
        });
      }
      if (url.startsWith(PHOTO_URL)) {
        return jsonResponse({ data: photoRow({ original_sha256: sha }) });
      }
      if (url.startsWith(FILE_URL)) {
        return jsonResponse({ data: fileRow({ file_size: png.length }) });
      }
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      sessionId,
    );

    expect(response.statusCode).toBe(200);
    expect(response.rawPayload.equals(png)).toBe(true);
    expect(response.headers["content-length"]).toBe("74");
    expect(fetchImpl.mock.calls.map(([url]) => String(url))).toContain(
      CONTENT_URL,
    );
  });

  it("denies a photo bound to a different mapped patient", async () => {
    const { app, fetchImpl } = buildLive((url) =>
      url.startsWith(PHOTO_URL)
        ? jsonResponse({
            data: photoRow({ patient_reference: "SYNTH-PATIENT-2" }),
          })
        : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      sessionId,
    );

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(
      fetchImpl.mock.calls.some(([url]) => String(url) === CONTENT_URL),
    ).toBe(false);
  });

  it("denies an unreadable photo or file without leaking existence", async () => {
    const photoDenied = buildLive((url) =>
      url.startsWith(PHOTO_URL) ? jsonResponse({}, 403) : defaultHandler(url),
    );
    const sessionA = await login(photoDenied.app);
    const a = await get(
      photoDenied.app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      sessionA,
    );
    expect(a.statusCode).toBe(403);

    const fileDenied = buildLive((url) =>
      url.startsWith(FILE_URL) ? jsonResponse({}, 404) : defaultHandler(url),
    );
    const sessionB = await login(fileDenied.app);
    const b = await get(
      fileDenied.app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      sessionB,
    );
    expect(b.statusCode).toBe(403);
    expect(b.json()).toEqual({ code: "FORBIDDEN" });
  });

  it("refuses a File row that is public, traversal-shaped or missing size", async () => {
    for (const row of [
      fileRow({ is_private: 0 }),
      fileRow({ file_url: "/private/files/../secret.png" }),
      fileRow({ file_url: "http://evil.example/x.png" }),
      fileRow({ file_size: 0 }),
    ]) {
      const { app, fetchImpl } = buildLive((url) =>
        url.startsWith(FILE_URL)
          ? jsonResponse({ data: row })
          : defaultHandler(url),
      );
      const sessionId = await login(app);
      const response = await get(
        app,
        "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
        sessionId,
      );
      expect(response.statusCode).toBe(403);
      expect(
        fetchImpl.mock.calls.some(([url]) => String(url) === CONTENT_URL),
      ).toBe(false);
    }
  });

  it("rejects a digest mismatch, a non-image body and an oversized body", async () => {
    const wrongDigest = buildLive((url) =>
      url.startsWith(PHOTO_URL)
        ? jsonResponse({ data: photoRow({ original_sha256: "b".repeat(64) }) })
        : defaultHandler(url),
    );
    const digestSession = await login(wrongDigest.app);
    const digest = await get(
      wrongDigest.app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      digestSession,
    );
    expect(digest.statusCode).toBe(502);

    const notImage = buildLive((url) =>
      url === CONTENT_URL
        ? new Response("not an image", { status: 200 })
        : defaultHandler(url),
    );
    const imageSession = await login(notImage.app);
    const image = await get(
      notImage.app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      imageSession,
    );
    expect(image.statusCode).toBe(415);
    expect(image.json()).toEqual({ code: "UNSUPPORTED" });

    const oversized = buildLive((url) =>
      url === CONTENT_URL
        ? new Response(Buffer.alloc(8 * 1024 * 1024 + 1, 0x89), {
            status: 200,
          })
        : defaultHandler(url),
    );
    const sizeSession = await login(oversized.app);
    const size = await get(
      oversized.app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      sizeSession,
    );
    expect(size.statusCode).toBe(413);
    expect(size.json()).toEqual({ code: "FILE_TOO_LARGE" });
  });

  it("invalidates the session when protected delivery returns 401", async () => {
    const { app, native } = buildLive((url) =>
      url === CONTENT_URL ? jsonResponse({}, 401) : defaultHandler(url),
    );
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      sessionId,
    );

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(native.get(sessionId)).toBeUndefined();
  });

  it("treats a 403 with an expired native session as AUTH_EXPIRED", async () => {
    const { app, native } = buildLive((url) => {
      if (url === CONTENT_URL) return jsonResponse({}, 403);
      if (url === `${LAB_ORIGIN}/api/method/frappe.auth.get_logged_user`) {
        return jsonResponse({}, 401);
      }
      return defaultHandler(url);
    });
    const sessionId = await login(app);

    const response = await get(
      app,
      "/api/live/synthetic-patients/PAT-1/photos/PHOTO1/content",
      sessionId,
    );

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(native.get(sessionId)).toBeUndefined();
  });

  it("rejects an unsafe photo id or unconfigured patient before live reads", async () => {
    const { app, fetchImpl } = buildLive();
    const sessionId = await login(app);

    for (const [reference, photoId] of [
      ["PAT-1", ".."],
      ["PAT-1", "PHOTO%2F1"],
      ["PAT-UNKNOWN", "PHOTO1"],
    ] as const) {
      const response = await get(
        app,
        `/api/live/synthetic-patients/${reference}/photos/${photoId}/content`,
        sessionId,
      );
      expect([400, 404]).toContain(response.statusCode);
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
