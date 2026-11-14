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
import type { Authenticate } from "./auth.js";

const TEST_ORIGIN = "http://localhost:5173";
const LAB_ORIGIN = "https://lab.example";

const NATIVE_SID = "native-sid-value";
const NATIVE_CSRF = "native-csrf-value";
const DOC_USER = "clinician@lab.example";
const OTHER_DOC = "other@lab.example";
const NATIVE_PATIENT = "SYNTH-PATIENT-1";
const OTHER_NATIVE_PATIENT = "SYNTH-PATIENT-2";
const UNLISTED_NATIVE_PATIENT = "SYNTH-PATIENT-9";
const PRACTITIONER = "PRACT-0001";
const APPOINTMENT_TYPE = "Synthetic Follow Up";
const ENCOUNTER_ID = "ENC-9001";
const URL_PATH = "/api/live/encounter-drafts";
const START = 1_760_000_000_000;

const LIVE_UPSTREAM: LiveUpstream = {
  origin: LAB_ORIGIN,
  timeoutMs: 1_000,
  syntheticPatients: new Map([
    ["PAT-1", NATIVE_PATIENT],
    ["PAT-2", OTHER_NATIVE_PATIENT],
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

const recAuthenticate: Authenticate = async () => ({
  user: {
    id: "reception@lab.example",
    displayName: "Front Desk",
    roles: ["REC"],
  },
  native: { sid: NATIVE_SID, csrf: NATIVE_CSRF },
});

interface DraftHint {
  request_key: string;
  patient_reference: string;
  created_at: string;
}

interface DraftList {
  drafts: DraftHint[];
  truncated: boolean;
}

interface Harness {
  app: FastifyInstance;
  db: Database.Database;
  fetchImpl: ReturnType<typeof vi.fn>;
  native: NativeCredentialStore;
  store: EncounterDraftStore;
  /** Freezes the store clock so created_at ordering is exact. */
  at: (ms: number) => void;
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

function buildLive(
  config: AppConfig = LIVE_CONFIG,
  authenticate: Authenticate = docAuthenticate,
): Harness {
  const db = new Database(":memory:");
  openDbs.push(db);
  let clock = START;
  const store = new EncounterDraftStore(db, () => clock);
  const native = new NativeCredentialStore();
  const fetchImpl = vi.fn(async () => {
    throw new Error("the local draft list never calls the upstream");
  });
  const app = buildServer({
    config,
    database: db,
    authenticate,
    native,
    encounterDrafts: store,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  openApps.push(app);
  return {
    app,
    db,
    fetchImpl,
    native,
    store,
    at: (ms: number) => {
      clock = ms;
    },
  };
}

function buildFixture(): Harness {
  const db = new Database(":memory:");
  openDbs.push(db);
  const store = new EncounterDraftStore(db);
  const native = new NativeCredentialStore();
  const fetchImpl = vi.fn();
  const app = buildServer({
    config: FIXTURE_CONFIG,
    database: db,
    native,
    encounterDrafts: store,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  openApps.push(app);
  return { app, db, fetchImpl, native, store, at: () => {} };
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

async function getDrafts(app: FastifyInstance, sessionId?: string, query = "") {
  return app.inject({
    method: "GET",
    url: `${URL_PATH}${query}`,
    headers:
      sessionId === undefined
        ? {}
        : { cookie: `${SESSION_COOKIE}=${sessionId}` },
  });
}

/** Seeds one `created` draft through the store; the store is the only id source. */
function seedCreatedDraft(
  harness: Harness,
  requestKey: string,
  options: {
    userId?: string;
    nativePatient?: string;
    encounterId?: string;
    createdAt?: number;
  } = {},
): void {
  harness.at(options.createdAt ?? START);
  const begun = harness.store.begin(
    options.userId ?? DOC_USER,
    requestKey,
    hashEncounterDraftPayload({
      patient: options.nativePatient ?? NATIVE_PATIENT,
      practitioner: PRACTITIONER,
      appointmentType: APPOINTMENT_TYPE,
    }),
    patientIntentFingerprint(options.nativePatient ?? NATIVE_PATIENT),
  );
  expect(begun.kind).toBe("new");
  harness.store.markCreated(
    options.userId ?? DOC_USER,
    requestKey,
    options.encounterId ?? ENCOUNTER_ID,
  );
}

/** Inserts a row directly, for legacy/malformed fingerprints the store never writes. */
function seedRawRow(
  harness: Harness,
  requestKey: string,
  fingerprint: string | null,
): void {
  harness.db
    .prepare(
      `INSERT INTO encounter_drafts
         (user_id, request_key, payload_hash, patient_fingerprint, state,
          encounter_id, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'created', ?, ?, ?)`,
    )
    .run(
      DOC_USER,
      requestKey,
      `sha256:${"b".repeat(64)}`,
      fingerprint,
      "ENC-LEGACY",
      START,
      START,
    );
}

/** A row written before the fingerprint column existed: legacy, never promoted. */
function seedLegacyNullFingerprint(harness: Harness, requestKey: string): void {
  seedRawRow(harness, requestKey, null);
}

function draftCount(db: Database.Database): number {
  const row = db
    .prepare("SELECT COUNT(*) AS count FROM encounter_drafts")
    .get() as { count: number };
  return row.count;
}

describe("feature boundary", () => {
  it("is not exposed in fixture mode and never calls the upstream", async () => {
    const harness = buildFixture();

    const response = await getDrafts(harness.app);

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({ code: "UNSUPPORTED" });
    expect(harness.fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses without the encounter opt-in", async () => {
    const harness = buildLive(NO_WRITE_CONFIG);
    const sessionId = await login(harness.app);

    const response = await getDrafts(harness.app, sessionId);

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "WRITE_DISABLED" });
    expect(harness.fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses an unauthenticated request", async () => {
    const harness = buildLive();

    const response = await getDrafts(harness.app);

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(harness.fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a session without the clinician role", async () => {
    const harness = buildLive(LIVE_CONFIG, recAuthenticate);
    const sessionId = await login(harness.app, {
      usr: "reception@lab.example",
      pwd: "secret",
    });

    const response = await getDrafts(harness.app, sessionId);

    expect(response.statusCode).toBe(403);
    expect(response.json()).toEqual({ code: "FORBIDDEN" });
    expect(harness.fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a session whose native credential is gone", async () => {
    const harness = buildLive();
    const sessionId = await login(harness.app);
    harness.native.clear(sessionId);

    const response = await getDrafts(harness.app, sessionId);

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(harness.fetchImpl).not.toHaveBeenCalled();
  });
});

describe("local draft hints", () => {
  it("lists only the caller's created drafts", async () => {
    const harness = buildLive();
    seedCreatedDraft(harness, "own-request-key-0001");
    seedCreatedDraft(harness, "other-request-key-0001", {
      userId: OTHER_DOC,
      nativePatient: OTHER_NATIVE_PATIENT,
    });
    const sessionId = await login(harness.app);

    const response = await getDrafts(harness.app, sessionId);

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      drafts: [
        {
          request_key: "own-request-key-0001",
          patient_reference: "PAT-1",
          created_at: new Date(START).toISOString(),
        },
      ],
      truncated: false,
    });
    expect(harness.fetchImpl).not.toHaveBeenCalled();
  });

  it("orders newest first with a stable request-key tie-break", async () => {
    const harness = buildLive();
    seedCreatedDraft(harness, "key-000000000000000a", { createdAt: START });
    seedCreatedDraft(harness, "key-000000000000000b", { createdAt: START });
    seedCreatedDraft(harness, "key-000000000000000c", {
      createdAt: START + 1_000,
    });
    const sessionId = await login(harness.app);

    const body = (await getDrafts(harness.app, sessionId)).json() as DraftList;

    expect(body.drafts.map((draft) => draft.request_key)).toEqual([
      "key-000000000000000c",
      "key-000000000000000b",
      "key-000000000000000a",
    ]);
  });

  it("never promotes pending, unknown or legacy null-fingerprint rows", async () => {
    const harness = buildLive();
    seedCreatedDraft(harness, "created-request-key-01");
    // One unresolved intent per patient, so each row uses its own patient.
    harness.store.begin(
      DOC_USER,
      "pending-request-key-01",
      hashEncounterDraftPayload({
        patient: OTHER_NATIVE_PATIENT,
        practitioner: PRACTITIONER,
        appointmentType: APPOINTMENT_TYPE,
      }),
      patientIntentFingerprint(OTHER_NATIVE_PATIENT),
    );
    harness.store.begin(
      DOC_USER,
      "unknown-request-key-01",
      hashEncounterDraftPayload({
        patient: UNLISTED_NATIVE_PATIENT,
        practitioner: PRACTITIONER,
        appointmentType: APPOINTMENT_TYPE,
      }),
      patientIntentFingerprint(UNLISTED_NATIVE_PATIENT),
    );
    harness.store.markUnknown(DOC_USER, "unknown-request-key-01");
    seedLegacyNullFingerprint(harness, "legacy-request-key-01");
    const sessionId = await login(harness.app);

    const body = (await getDrafts(harness.app, sessionId)).json() as DraftList;

    expect(body.drafts.map((draft) => draft.request_key)).toEqual([
      "created-request-key-01",
    ]);
  });

  it("omits fingerprints that are unlisted or malformed", async () => {
    const harness = buildLive();
    seedCreatedDraft(harness, "unlisted-request-key-01", {
      nativePatient: UNLISTED_NATIVE_PATIENT,
    });
    seedRawRow(harness, "malformed-request-key-1", "not-a-fingerprint");
    seedCreatedDraft(harness, "listed-request-key-001");
    const sessionId = await login(harness.app);

    const body = (await getDrafts(harness.app, sessionId)).json() as DraftList;

    expect(body.drafts.map((draft) => draft.request_key)).toEqual([
      "listed-request-key-001",
    ]);
  });

  it("returns no hints when the synthetic allow-list is empty", async () => {
    const harness = buildLive({
      ...LIVE_CONFIG,
      live: { ...LIVE_UPSTREAM, syntheticPatients: new Map() },
    });
    seedCreatedDraft(harness, "created-request-key-01");
    const sessionId = await login(harness.app);

    const response = await getDrafts(harness.app, sessionId);

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ drafts: [], truncated: false });
  });

  it("returns only the three hint fields and no secret or native id", async () => {
    const harness = buildLive();
    seedCreatedDraft(harness, "own-request-key-0001");
    const sessionId = await login(harness.app);

    const response = await getDrafts(harness.app, sessionId);
    const body = response.json() as DraftList;
    const raw = response.body;
    const payloadHash = hashEncounterDraftPayload({
      patient: NATIVE_PATIENT,
      practitioner: PRACTITIONER,
      appointmentType: APPOINTMENT_TYPE,
    });

    expect(Object.keys(body).sort()).toEqual(["drafts", "truncated"]);
    expect(Object.keys(body.drafts[0] ?? {}).sort()).toEqual([
      "created_at",
      "patient_reference",
      "request_key",
    ]);
    for (const secret of [
      ENCOUNTER_ID,
      NATIVE_PATIENT,
      DOC_USER,
      NATIVE_SID,
      NATIVE_CSRF,
      payloadHash,
      payloadHash.replace("sha256:", ""),
      patientIntentFingerprint(NATIVE_PATIENT),
      "encounter_id",
      "user_id",
    ]) {
      expect(raw).not.toContain(secret);
    }
  });

  it("bounds the list and reports truncation instead of claiming completeness", async () => {
    const harness = buildLive();
    for (let index = 0; index < 21; index += 1) {
      seedCreatedDraft(
        harness,
        `bulk-request-key-${String(index).padStart(4, "0")}`,
        { createdAt: START + index },
      );
    }
    const sessionId = await login(harness.app);

    const defaultPage = (
      await getDrafts(harness.app, sessionId)
    ).json() as DraftList;
    expect(defaultPage.drafts).toHaveLength(20);
    expect(defaultPage.truncated).toBe(true);

    const explicitPage = (
      await getDrafts(harness.app, sessionId, "?limit=100")
    ).json() as DraftList;
    expect(explicitPage.drafts).toHaveLength(21);
    expect(explicitPage.truncated).toBe(false);

    const narrowPage = (
      await getDrafts(harness.app, sessionId, "?limit=2")
    ).json() as DraftList;
    expect(narrowPage.drafts).toHaveLength(2);
    expect(narrowPage.drafts.map((draft) => draft.request_key)).toEqual([
      "bulk-request-key-0020",
      "bulk-request-key-0019",
    ]);
    expect(narrowPage.truncated).toBe(true);
  });

  it("does not write anything or change permissions", async () => {
    const harness = buildLive();
    seedCreatedDraft(harness, "own-request-key-0001");
    const sessionId = await login(harness.app);
    const before = draftCount(harness.db);

    await getDrafts(harness.app, sessionId);

    expect(draftCount(harness.db)).toBe(before);
    expect(harness.fetchImpl).not.toHaveBeenCalled();
  });
});
