import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionStore } from "./store.js";

interface OpenStore {
  db: Database.Database;
  store: SessionStore;
}

const openStores: OpenStore[] = [];
const tempDirs: string[] = [];

function openStore(path = ":memory:"): OpenStore {
  const db = new Database(path);
  const store = new SessionStore(db);
  openStores.push({ db, store });
  return { db, store };
}

function tempDatabasePath(): string {
  const dir = mkdtempSync(join(tmpdir(), "clinic-session-"));
  tempDirs.push(dir);
  return join(dir, "clinic.sqlite");
}

afterEach(() => {
  vi.useRealTimers();
  for (const { db } of openStores.splice(0)) {
    db.close();
  }
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("SessionStore", () => {
  it("rotates the session id and invalidates the old one", () => {
    const { store } = openStore();
    const s = store.create({ id: "u1", displayName: "Test", roles: ["REC"] });
    const epoch = store.get(s)?.sessionEpoch;
    const r = store.rotate(s);
    expect(store.get(s)).toBeUndefined();
    expect(store.get(r)?.userId).toBe("u1");
    expect(epoch).toEqual(expect.any(String));
    expect(epoch).not.toBe(s);
    expect(store.get(r)?.sessionEpoch).not.toBe(epoch);
  });

  it("preserves user, display name and roles across rotation", () => {
    const { store } = openStore();
    const s = store.create({
      id: "u1",
      displayName: "Test User",
      roles: ["REC", "CAS"],
    });
    const r = store.rotate(s);
    expect(r).not.toBe(s);
    expect(store.get(r)).toMatchObject({
      userId: "u1",
      displayName: "Test User",
      roles: ["REC", "CAS"],
    });
  });

  it("survives a store reopen with roles intact", () => {
    const file = tempDatabasePath();
    const first = openStore(file);
    const s = first.store.create({
      id: "u1",
      displayName: "Test",
      roles: ["REC"],
    });
    const epoch = first.store.get(s)?.sessionEpoch;
    first.db.close();
    openStores.splice(openStores.indexOf(first), 1);

    const { store } = openStore(file);
    expect(store.get(s)).toMatchObject({
      userId: "u1",
      roles: ["REC"],
      sessionEpoch: epoch,
    });
  });

  it("assigns independent stable epochs to legacy sessions without replacing credentials", () => {
    const db = new Database(":memory:");
    db.exec(
      "CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL, roles_json TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)",
    );
    db.prepare("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)").run(
      "legacy-cookie",
      "u1",
      "Staff",
      '["REC"]',
      Date.now(),
      Date.now() + 60_000,
    );
    const store = new SessionStore(db);
    openStores.push({ db, store });
    const epoch = store.get("legacy-cookie")?.sessionEpoch;
    expect(epoch).toEqual(expect.any(String));
    expect(epoch).not.toBe("legacy-cookie");
    expect(new SessionStore(db).get("legacy-cookie")?.sessionEpoch).toBe(epoch);
  });

  it("expires an idle session after eight hours", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-05T00:00:00Z"));
    const { store } = openStore();
    const s = store.create({ id: "u1", displayName: "Test", roles: ["REC"] });
    vi.setSystemTime(new Date("2026-11-05T08:00:01Z"));
    expect(store.get(s)).toBeUndefined();
  });

  it("destroys every session for one user without touching others", () => {
    const { store } = openStore();
    const first = store.create({
      id: "u1",
      displayName: "One",
      roles: ["REC"],
    });
    const second = store.create({
      id: "u1",
      displayName: "One",
      roles: ["REC"],
    });
    const other = store.create({
      id: "u2",
      displayName: "Two",
      roles: ["DOC"],
    });

    store.destroyAllForUser("u1");

    expect(store.get(first)).toBeUndefined();
    expect(store.get(second)).toBeUndefined();
    expect(store.get(other)?.userId).toBe("u2");
  });
});
