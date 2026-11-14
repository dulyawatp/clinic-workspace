import { randomBytes, randomUUID } from "node:crypto";
import type { Database } from "better-sqlite3";
import type { RoleCode } from "../capabilities.js";
import {
  MAX_PENDING_PRE_SESSIONS,
  PRE_SESSION_TTL_MS,
  SESSION_TTL_MS,
} from "../security/constants.js";

export type SessionId = string;

export interface SessionUser {
  id: string;
  displayName: string;
  roles: RoleCode[];
}

export interface SessionRecord {
  /** Public correlation epoch, independently random; never an auth credential. */
  sessionEpoch: string;
  userId: string;
  displayName: string;
  roles: RoleCode[];
  createdAt: number;
  expiresAt: number;
}

interface SessionRow {
  session_epoch: string;
  id: string;
  user_id: string;
  display_name: string;
  roles_json: string;
  created_at: number;
  expires_at: number;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS pre_sessions (
  id TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  session_epoch TEXT NOT NULL,
  user_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  roles_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
`;

function newId(): string {
  return randomBytes(32).toString("base64url");
}

function parseRoles(json: string): RoleCode[] {
  const parsed: unknown = JSON.parse(json);
  return Array.isArray(parsed) ? (parsed as RoleCode[]) : [];
}

/**
 * Server-side sessions. Roles are captured from the login response so later
 * handlers read them from the session, never from the request.
 */
export class SessionStore {
  constructor(
    private readonly db: Database,
    private readonly now: () => number = Date.now,
  ) {
    this.db.exec(SCHEMA);
    // Additive migration for existing local session databases. Epochs survive
    // store reopen; ordinary session checks never manufacture replacements.
    const columns = this.db.prepare("PRAGMA table_info(sessions)").all() as {
      name: string;
    }[];
    if (!columns.some((column) => column.name === "session_epoch")) {
      this.db.exec("ALTER TABLE sessions ADD COLUMN session_epoch TEXT");
    }
    const missing = this.db
      .prepare("SELECT id FROM sessions WHERE session_epoch IS NULL")
      .all() as { id: string }[];
    const assign = this.db.prepare(
      "UPDATE sessions SET session_epoch = ? WHERE id = ? AND session_epoch IS NULL",
    );
    for (const row of missing) assign.run(randomUUID(), row.id);
  }

  create(user: SessionUser): SessionId {
    const id = newId();
    const createdAt = this.now();
    this.db
      .prepare(
        "INSERT INTO sessions (id, session_epoch, user_id, display_name, roles_json, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      )
      .run(
        id,
        randomUUID(),
        user.id,
        user.displayName,
        JSON.stringify(user.roles),
        createdAt,
        createdAt + SESSION_TTL_MS,
      );
    return id;
  }

  get(id: string): SessionRecord | undefined {
    const row = this.db
      .prepare(
        "SELECT id, session_epoch, user_id, display_name, roles_json, created_at, expires_at FROM sessions WHERE id = ?",
      )
      .get(id) as SessionRow | undefined;

    if (row === undefined) {
      return undefined;
    }

    if (row.expires_at <= this.now()) {
      this.destroy(id);
      return undefined;
    }

    return {
      sessionEpoch: row.session_epoch,
      userId: row.user_id,
      displayName: row.display_name,
      roles: parseRoles(row.roles_json),
      createdAt: row.created_at,
      expiresAt: row.expires_at,
    };
  }

  rotate(oldId: string): SessionId {
    const existing = this.get(oldId);
    if (existing === undefined) {
      throw new Error("SESSION_NOT_FOUND");
    }

    this.destroy(oldId);
    return this.create({
      id: existing.userId,
      displayName: existing.displayName,
      roles: existing.roles,
    });
  }

  destroy(id: string): void {
    this.db.prepare("DELETE FROM sessions WHERE id = ?").run(id);
  }

  /** Revokes every session belonging to a user (password change, forced logout). */
  destroyAllForUser(userId: string): void {
    this.db.prepare("DELETE FROM sessions WHERE user_id = ?").run(userId);
  }

  /**
   * Creates an unauthenticated pre-session used to bind a login CSRF token.
   *
   * Expired rows are reclaimed first, then live rows are counted against a
   * global cap. At the cap this returns `null` and the caller fails closed with
   * `RATE_LIMITED`; no new persisted row is written. Purge/count/insert run
   * synchronously, so concurrent requests cannot overshoot the cap.
   */
  createPreSession(limit: number = MAX_PENDING_PRE_SESSIONS): string | null {
    const createdAt = this.now();
    this.purgeExpiredPreSessions();

    const row = this.db
      .prepare("SELECT COUNT(*) AS count FROM pre_sessions")
      .get() as { count: number };
    if (row.count >= limit) {
      return null;
    }

    const id = newId();
    this.db
      .prepare(
        "INSERT INTO pre_sessions (id, created_at, expires_at) VALUES (?, ?, ?)",
      )
      .run(id, createdAt, createdAt + PRE_SESSION_TTL_MS);
    return id;
  }

  /** Reclaims abandoned/expired anonymous handshake rows. */
  purgeExpiredPreSessions(): void {
    this.db
      .prepare("DELETE FROM pre_sessions WHERE expires_at <= ?")
      .run(this.now());
  }

  hasPreSession(id: string): boolean {
    const row = this.db
      .prepare("SELECT expires_at FROM pre_sessions WHERE id = ?")
      .get(id) as { expires_at: number } | undefined;

    if (row === undefined) {
      return false;
    }

    if (row.expires_at <= this.now()) {
      this.destroyPreSession(id);
      return false;
    }

    return true;
  }

  destroyPreSession(id: string): void {
    this.db.prepare("DELETE FROM pre_sessions WHERE id = ?").run(id);
  }
}
