import clinical from "@clinic/contracts/fixtures/clinical.json" with {
  type: "json",
};
import frontdesk from "@clinic/contracts/fixtures/frontdesk.json" with {
  type: "json",
};
import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { afterEach, expect, it } from "vitest";
import type { RoleCode } from "../capabilities.js";
import type { AppConfig } from "../config.js";
import { CSRF_COOKIE } from "../security/constants.js";
import { buildServer } from "../server.js";
import { pageWindow } from "./handler.js";

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

async function loginAsTestUser(target: FastifyInstance, usr = "rec") {
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

async function readAs(usr: RoleCode, resource: string, query = "") {
  const target = app();
  const { cookie } = await loginAsTestUser(target, usr.toLowerCase());
  return target.inject({
    method: "GET",
    url: `/api/read/${resource}${query}`,
    headers: { cookie },
  });
}

it("serves clinical fixtures only to their fixture roles", async () => {
  for (const resource of [
    "clinical-visits",
    "clinical-sections",
    "clinical-vitals",
    "clinical-diagnoses",
    "clinical-medications",
    "clinical-photos",
    "clinical-labs",
    "clinical-procedures",
    "my-day-counts",
    "clinical-next-patients",
  ]) {
    const result = await readAs("DOC", resource);
    expect(result.statusCode, resource).toBe(200);
    expect(result.json().items.length, resource).toBeGreaterThan(0);
    expect((await readAs("REC", resource)).statusCode, resource).toBe(403);
  }
  expect((await readAs("NUR", "nurse-tasks")).statusCode).toBe(200);
  expect((await readAs("NUR", "clinical-sections")).statusCode).toBe(403);
});

it("keeps same-day visit contexts separate and preserves edge cases", async () => {
  const filter = `?filters=${encodeURIComponent(JSON.stringify({ encounterId: "ENC-2" }))}`;
  const visits = await readAs("DOC", "clinical-visits", filter);
  expect(visits.json().items).toHaveLength(1);
  expect(visits.json().items[0]).toMatchObject({
    encounterId: "ENC-2",
    patientId: "PAT-1",
  });
  const vitals = await readAs("DOC", "clinical-vitals");
  expect(vitals.json().items.map((row: { unit: string }) => row.unit)).toEqual([
    "kg",
    "°F",
  ]);
  expect(
    (await readAs("DOC", "clinical-photos")).json().items[0].consentStatus,
  ).toBe("missing");
  expect((await readAs("DOC", "clinical-labs")).json().items[0]).toMatchObject({
    state: "reviewed",
    reviewer: null,
  });
});

it("fails closed when photo transport metadata disagrees with the manifest", async () => {
  const row = clinical.photos[0] as Record<string, unknown> | undefined;
  if (row === undefined) throw new Error("missing fixture photo");
  const original = { ...row };
  const mismatches: Array<Record<string, unknown>> = [
    { url: "/api/files/fixture-photo-unknown" },
    { url: "/api/files/fixture-photo-2" },
    { sha256: "b".repeat(64) },
    { width: 49 },
    { height: 33 },
    { bytes: 221 },
    { id: "PH-9" },
    { patientId: "PAT-9" },
    { category: "after" },
    { bodyArea: "arm" },
  ];
  try {
    for (const mismatch of mismatches) {
      Object.assign(row, original, mismatch);
      const res = await readAs("DOC", "clinical-photos");
      expect(res.statusCode, JSON.stringify(mismatch)).toBe(502);
      expect(res.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
    }
  } finally {
    Object.assign(row, original);
  }
  expect((await readAs("DOC", "clinical-photos")).statusCode).toBe(200);
});

it("isolates every encounter-owned collection under combined filters", async () => {
  const resources = [
    "clinical-visits",
    "clinical-sections",
    "clinical-vitals",
    "clinical-diagnoses",
    "clinical-medications",
    "clinical-photos",
    "clinical-labs",
    "clinical-procedures",
    "clinical-next-patients",
    "nurse-tasks",
  ];
  const target = app();
  const doc = await loginAsTestUser(target, "doc");
  const nurse = await loginAsTestUser(target, "nur");
  for (const resource of resources) {
    const cookie = resource === "nurse-tasks" ? nurse.cookie : doc.cookie;
    for (const [encounterId, patientId, expected] of [
      ["ENC-1", "PAT-1", true],
      ["ENC-2", "PAT-1", false],
      ["ENC-1", "PAT-UNKNOWN", false],
      ["ENC-UNKNOWN", "PAT-1", false],
    ] as const) {
      const filters = encodeURIComponent(
        JSON.stringify({ encounterId, patientId }),
      );
      const response = await target.inject({
        method: "GET",
        url: `/api/read/${resource}?filters=${filters}`,
        headers: { cookie },
      });
      expect(response.statusCode, resource).toBe(200);
      expect(
        response.json().total > 0,
        `${resource} ${encounterId} ${patientId}`,
      ).toBe(
        expected ||
          (resource === "clinical-visits" &&
            encounterId === "ENC-2" &&
            patientId === "PAT-1"),
      );
      expect(
        response
          .json()
          .items.every(
            (row: { encounterId: string; patientId: string }) =>
              row.encounterId === encounterId && row.patientId === patientId,
          ),
      ).toBe(true);
    }
  }
});

it("serves fixture availability and filters appointment detail by id", async () => {
  const availability = await readAs("REC", "frontdesk-availability");
  expect(availability.statusCode).toBe(200);
  const slots = availability.json().items as Array<{
    state: string;
    fictional: boolean;
  }>;
  expect(slots.some((slot) => slot.state === "leave" && slot.fictional)).toBe(
    true,
  );
  const query = `?filters=${encodeURIComponent(JSON.stringify({ id: "fixture-apt-01" }))}`;
  const detail = await readAs("REC", "frontdesk-appointments", query);
  expect(detail.statusCode).toBe(200);
  expect(detail.json().items).toHaveLength(1);
  expect(detail.json().items[0].id).toBe("fixture-apt-01");
  expect((await readAs("CAS", "frontdesk-availability")).statusCode).toBe(403);
});

it("filters queue by date, status and search without exposing clinical fields", async () => {
  const filters = encodeURIComponent(
    JSON.stringify({ date: "2026-11-05", status: "waiting" }),
  );
  const res = await readAs(
    "REC",
    "frontdesk-queue",
    `?q=Narin&filters=${filters}`,
  );
  expect(res.statusCode).toBe(200);
  expect(res.json().total).toBe(1);
  expect(res.json().items[0].patientName).toBe("Narin Chaiyaporn");
  expect(res.json().items[0]).not.toHaveProperty("notes");
  const summary = await readAs("REC", "frontdesk-queue-kpis");
  expect(summary.json().items[0].waiting).toBe(2);
  expect((await readAs("CAS", "frontdesk-queue-kpis")).statusCode).toBe(403);
});

it("serves checkout references without clinical fields and filters before pagination", async () => {
  const row = frontdesk.checkouts[0] as Record<string, unknown> | undefined;
  if (!row) throw new Error("missing fixture checkout");
  row.doctorNote = "patient has rash";
  try {
    const filters = encodeURIComponent(JSON.stringify({ handoff: "invoiced" }));
    const result = await readAs(
      "REC",
      "frontdesk-checkouts",
      `?filters=${filters}`,
    );
    expect(result.statusCode).toBe(200);
    expect(result.json().items[0].invoiceReference).toBe("INV-FIX-0001");
    expect(JSON.stringify(result.json())).not.toContain("patient has rash");
    expect((await readAs("CAS", "frontdesk-checkouts")).statusCode).toBe(200);
    expect((await readAs("DOC", "frontdesk-checkouts")).statusCode).toBe(403);
  } finally {
    delete row.doctorNote;
  }
});

it("filters availability by date, doctor and source type before pagination", async () => {
  const rows = frontdesk.availability as unknown as Array<
    Record<string, unknown>
  >;
  const originalLength = rows.length;
  try {
    rows.push(
      ...Array.from({ length: 101 }, (_, index) => ({
        id: `test-${String(index).padStart(3, "0")}`,
        date: "2026-12-03",
        time: "09:00",
        practitionerId: "test-doctor",
        practitionerName: "Test",
        state: "booked",
        appointmentType: "test-type",
        fictional: true,
      })),
    );
    const filters = {
      dateFrom: "2026-12-03",
      dateTo: "2026-12-03",
      practitionerId: "test-doctor",
      appointmentType: "test-type",
    };
    const query = `?limit=100&offset=100&filters=${encodeURIComponent(JSON.stringify(filters))}`;
    const result = await readAs("REC", "frontdesk-availability", query);
    expect(result.statusCode).toBe(200);
    expect(result.json().total).toBe(101);
    expect(result.json().items).toHaveLength(1);
    expect(result.json().items[0].id).toBe("test-100");
    const options = await readAs("REC", "frontdesk-availability-options");
    expect(options.statusCode).toBe(200);
    expect(options.json().items[0].practitioners).toContainEqual({
      id: "test-doctor",
      name: "Test",
    });
    expect(options.json().items[0].types).toContain("test-type");
    expect(
      (await readAs("CAS", "frontdesk-availability-options")).statusCode,
    ).toBe(403);
  } finally {
    rows.splice(originalLength);
  }
});

it("requires a session before the allow-list", async () => {
  const target = app();
  const res = await target.inject({
    method: "GET",
    url: "/api/read/patients",
  });
  expect(res.statusCode).toBe(401);
  expect(res.json()).toEqual({ code: "AUTH_EXPIRED" });
});

it("rejects a resource that is not in the allow-list", async () => {
  const res = await readAs("REC", "unknown-resource");
  expect(res.statusCode).toBe(404);
  expect(res.json()).toEqual({ code: "NOT_FOUND" });
});

it("rejects a traversal-shaped path without echoing it", async () => {
  const res = await readAs("REC", "../api/resource/Patient");
  expect([400, 404]).toContain(res.statusCode);
});

it("filters fixture rows by the unverified matrix, not by a client role", async () => {
  const res = await readAs("PHA", "patients");
  expect(res.statusCode).toBe(403);
  expect(res.json()).toEqual({ code: "FORBIDDEN" });
});

it("ignores a client-supplied role header", async () => {
  const target = app();
  const { cookie } = await loginAsTestUser(target, "pha");
  const res = await target.inject({
    method: "GET",
    url: "/api/read/patients",
    headers: { cookie, "x-role": "REC" },
  });
  expect(res.statusCode).toBe(403);
});

it("parses and narrows the DTO at the proxy, dropping unknown fields", async () => {
  const res = await readAs("REC", "patients");
  expect(res.statusCode).toBe(200);
  expect(res.json().items[0]).not.toHaveProperty("internal_notes");
});

it("caps pagination at 100 and defaults to 20", async () => {
  expect(
    (await readAs("REC", "patients", "?limit=500")).json().items.length,
  ).toBeLessThanOrEqual(100);
  expect((await readAs("REC", "patients")).json().items.length).toBe(20);
});

it("pages with a positive offset and reports the unpaged total", async () => {
  const res = await readAs("REC", "patients", "?limit=2&offset=2");
  const body = res.json() as { items: unknown[]; total: number };
  expect(res.statusCode).toBe(200);
  expect(body.items).toHaveLength(2);
  expect(body.total).toBeGreaterThan(2);
});

it("searches patients with a case-insensitive, Thai-preserving query", async () => {
  const res = await readAs(
    "REC",
    "patients",
    `?q=${encodeURIComponent("สมชาย")}`,
  );
  const body = res.json() as { items: Array<{ fullName: string }> };
  expect(res.statusCode).toBe(200);
  expect(body.items.length).toBeGreaterThan(0);
  expect(body.items[0]?.fullName).toContain("สมชาย");
});

it("treats a whitespace-only q as no search", async () => {
  const res = await readAs("REC", "patients", "?q=%20%20");
  expect(res.statusCode).toBe(200);
  expect(res.json().items).toHaveLength(20);
});

it("rejects q on a resource that declares no search fields", async () => {
  const res = await readAs("REC", "appointments", "?q=fixture");
  expect(res.statusCode).toBe(400);
  expect(res.json()).toEqual({ code: "VALIDATION" });
});

it("returns an empty page but the full total past the end", async () => {
  const res = await readAs("REC", "patients", "?offset=999");
  const body = res.json() as { items: unknown[]; total: number };
  expect(res.statusCode).toBe(200);
  expect(body.items).toEqual([]);
  expect(body.total).toBe(24);
});

it("clamps the pagination window at the handler boundary", () => {
  expect(pageWindow({})).toEqual({ limit: 20, offset: 0 });
  expect(pageWindow({ limit: "500" })).toEqual({ limit: 100, offset: 0 });
  expect(pageWindow({ limit: "0" })).toEqual({ limit: 1, offset: 0 });
  expect(pageWindow({ limit: "-5" })).toEqual({ limit: 1, offset: 0 });
  expect(pageWindow({ limit: "not-a-number" })).toEqual({
    limit: 20,
    offset: 0,
  });
  expect(pageWindow({ offset: "-3" })).toEqual({ limit: 20, offset: 0 });
});

it("applies an allow-listed public filter field", async () => {
  const filters = encodeURIComponent(JSON.stringify({ hn: "FX-0001" }));
  const res = await readAs("REC", "patients", `?filters=${filters}`);
  const body = res.json() as {
    items: Array<{ hn: string }>;
    total: number;
  };
  expect(res.statusCode).toBe(200);
  expect(body.total).toBe(1);
  expect(body.items[0]?.hn).toBe("FX-0001");
});

it("rejects a filter on a hidden fixture field", async () => {
  const filters = encodeURIComponent(
    JSON.stringify({ internal_notes: "Fixture-only note" }),
  );
  const res = await readAs("REC", "patients", `?filters=${filters}`);
  expect(res.statusCode).toBe(400);
  expect(res.json()).toEqual({ code: "VALIDATION" });
});

it("rejects unknown and unsafe filter keys", async () => {
  const unknown = encodeURIComponent(JSON.stringify({ nope: "x" }));
  const unknownRes = await readAs("REC", "patients", `?filters=${unknown}`);
  expect(unknownRes.statusCode).toBe(400);

  // Built from a raw JSON string: an object literal would set the prototype
  // instead of serialising an own `__proto__` key.
  const unsafe = encodeURIComponent('{"__proto__":"x"}');
  const unsafeRes = await readAs("REC", "patients", `?filters=${unsafe}`);
  expect(unsafeRes.statusCode).toBe(400);
  expect(unsafeRes.json()).toEqual({ code: "VALIDATION" });
});

it("serves the identity-only billing summary to the recorded billing roles", async () => {
  const filters = encodeURIComponent(JSON.stringify({ id: "fixture-dir-01" }));
  const asCashier = await readAs(
    "CAS",
    "billing-patient-summary",
    `?filters=${filters}`,
  );
  expect(asCashier.statusCode).toBe(200);
  expect(asCashier.json().total).toBe(1);
  expect(asCashier.json().items[0]).toEqual({
    id: "fixture-dir-01",
    fullName: "สมชาย ใจดี",
    fictional: true,
  });

  // The matrix records MGR as the second billing role so the manager's
  // payments tab is reachable; the DTO stays identity-only.
  const asManager = await readAs(
    "MGR",
    "billing-patient-summary",
    `?filters=${filters}`,
  );
  expect(asManager.statusCode).toBe(200);
  expect(asManager.json().items[0]).toEqual(asCashier.json().items[0]);

  const denied = await readAs(
    "REC",
    "billing-patient-summary",
    `?filters=${filters}`,
  );
  expect(denied.statusCode).toBe(403);
  expect(denied.json()).toEqual({ code: "FORBIDDEN" });
});

it("requires an id filter for the billing summary", async () => {
  const missing = await readAs("CAS", "billing-patient-summary");
  expect(missing.statusCode).toBe(400);
  expect(missing.json()).toEqual({ code: "VALIDATION" });
});

it("rejects an appointment scope outside the reception drilldowns", async () => {
  const unknown = encodeURIComponent(
    JSON.stringify({ date: "2026-11-05", scope: "everyone" }),
  );
  const rejected = await readAs(
    "REC",
    "frontdesk-appointments",
    `?filters=${unknown}`,
  );
  expect(rejected.statusCode).toBe(400);
  expect(rejected.json()).toEqual({ code: "VALIDATION" });

  const allowed = encodeURIComponent(
    JSON.stringify({ date: "2026-11-05", scope: "waiting" }),
  );
  const accepted = await readAs(
    "REC",
    "frontdesk-appointments",
    `?filters=${allowed}`,
  );
  expect(accepted.statusCode).toBe(200);
  const items = accepted.json().items as Array<{ status: string }>;
  expect(items.length).toBeGreaterThan(0);
  expect(items.every((row) => row.status === "waiting")).toBe(true);
});
