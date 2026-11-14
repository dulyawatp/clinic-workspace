import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CsrfStore } from "./csrf-store.js";

describe("CsrfStore", () => {
  let db: Database.Database;
  let csrf: CsrfStore;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-05T00:00:00Z"));
    db = new Database(":memory:");
    csrf = new CsrfStore(db);
  });

  afterEach(() => {
    db.close();
    vi.useRealTimers();
  });

  it("stores only a hash, never the plaintext token", () => {
    const token = csrf.issue("pre-1", "login");
    const rows = db.prepare("SELECT token_hash FROM csrf_tokens").all();
    expect(JSON.stringify(rows)).not.toContain(token);
  });

  it("consumes a login token exactly once", () => {
    const token = csrf.issue("pre-1", "login");
    expect(csrf.consume("pre-1", token, "login")).toBe(true);
    expect(csrf.consume("pre-1", token, "login")).toBe(false);
  });

  it("expires a login token after thirty minutes", () => {
    const token = csrf.issue("pre-1", "login");
    vi.advanceTimersByTime(30 * 60 * 1000 + 1);
    expect(csrf.verify("pre-1", token, "login")).toBe(false);
  });

  it("rejects a token presented with a different owner", () => {
    const token = csrf.issue("pre-1", "login");
    expect(csrf.verify("pre-2", token, "login")).toBe(false);
  });
});
