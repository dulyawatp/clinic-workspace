import Database from "better-sqlite3";
import { afterEach, describe, expect, it } from "vitest";
import {
  hashCreatePayload,
  PatientCreateStore,
} from "./patient-create-store.js";

const KEY = "request-key-0123456789";
const HASH = hashCreatePayload({
  firstName: "Somchai",
  lastName: "Jaidee",
  sex: "Male",
});

const openDbs: Database.Database[] = [];

afterEach(() => {
  for (const db of openDbs.splice(0)) {
    db.close();
  }
});

function open(now: () => number): {
  db: Database.Database;
  store: PatientCreateStore;
} {
  const db = new Database(":memory:");
  openDbs.push(db);
  return { db, store: new PatientCreateStore(db, now) };
}

describe("PatientCreateStore", () => {
  it("returns the existing record for a duplicate key", () => {
    const { store } = open(Date.now);

    expect(store.begin("u1", KEY, HASH)).toEqual({ kind: "new" });
    expect(store.begin("u1", KEY, HASH)).toEqual({
      kind: "replay",
      state: "pending",
    });
  });

  it("rejects reuse of a key with a different payload", () => {
    const { store } = open(Date.now);
    store.begin("u1", KEY, HASH);

    expect(store.begin("u1", KEY, `${HASH}-other`)).toEqual({
      kind: "conflict",
    });
  });

  it("keeps a resolved key replayable long after it was written", () => {
    let clock = 1_700_000_000_000;
    const { store, db } = open(() => clock);

    store.begin("u1", KEY, HASH);
    store.markCreated("u1", KEY, "PAT-9001");

    // A year later the same key must still suppress a second native create.
    clock += 365 * 24 * 60 * 60 * 1000;

    expect(store.begin("u1", KEY, HASH)).toEqual({
      kind: "replay",
      state: "created",
      patientId: "PAT-9001",
    });
    expect(store.get("u1", KEY)?.state).toBe("created");
    const rows = db
      .prepare("SELECT COUNT(*) AS count FROM patient_creates")
      .get() as {
      count: number;
    };
    expect(rows.count).toBe(1);
  });

  it("keeps an unresolved key pending until something resolves it", () => {
    let clock = 1_700_000_000_000;
    const { store } = open(() => clock);

    store.begin("u1", KEY, HASH);
    clock += 30 * 24 * 60 * 60 * 1000;

    expect(store.begin("u1", KEY, HASH)).toEqual({
      kind: "replay",
      state: "pending",
    });
    expect(store.get("u1", KEY)?.state).toBe("pending");
  });

  it("keeps unknown terminal across reopen", () => {
    const file = new Database(":memory:");
    openDbs.push(file);
    const first = new PatientCreateStore(file, Date.now);
    first.begin("u1", KEY, HASH);
    first.markUnknown("u1", KEY);

    const reopened = new PatientCreateStore(file, Date.now);
    expect(reopened.begin("u1", KEY, HASH)).toEqual({
      kind: "replay",
      state: "unknown",
    });
  });

  it("releases only a pending row", () => {
    const { store } = open(Date.now);
    store.begin("u1", KEY, HASH);
    store.release("u1", KEY);
    expect(store.get("u1", KEY)).toBeUndefined();

    store.begin("u1", KEY, HASH);
    store.markCreated("u1", KEY, "PAT-9001");
    store.release("u1", KEY);
    expect(store.get("u1", KEY)?.state).toBe("created");
  });

  it("scopes keys per native user", () => {
    const { store } = open(Date.now);
    store.begin("u1", KEY, HASH);

    expect(store.begin("u2", KEY, HASH)).toEqual({ kind: "new" });
  });
});
