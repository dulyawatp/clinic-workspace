import type { Database } from "better-sqlite3";
import {
  MAX_UNUSED_CSRF_TOKENS,
  PRE_SESSION_TTL_MS,
  SESSION_TTL_MS,
} from "./constants.js";
import { type CsrfPurpose, hashToken, issueToken } from "./csrf.js";

const LOGIN_ATTEMPT_REJECTED = new Error("LOGIN_ATTEMPT_REJECTED");

const TOKEN_TTL_MS: Record<CsrfPurpose, number> = {
  login: PRE_SESSION_TTL_MS,
  session: SESSION_TTL_MS,
};

const SCHEMA = `
CREATE TABLE IF NOT EXISTS csrf_tokens (
  token_hash TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  purpose TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER
);
`;

/**
 * Browser-facing CSRF tokens. The plaintext is returned once at issue time and
 * never persisted; only the SHA-256 hash is stored.
 */
export class CsrfStore {
  constructor(
    private readonly db: Database,
    private readonly now: () => number = Date.now,
  ) {
    this.db.exec(SCHEMA);
  }

  issue(ownerId: string, purpose: CsrfPurpose): string {
    const token = issueToken();
    this.db
      .prepare(
        "INSERT INTO csrf_tokens (token_hash, owner_id, purpose, expires_at, used_at) VALUES (?, ?, ?, ?, NULL)",
      )
      .run(
        hashToken(token),
        ownerId,
        purpose,
        this.now() + TOKEN_TTL_MS[purpose],
      );
    this.pruneOwnerTokens(ownerId);
    return token;
  }

  /** Keeps the newest unused tokens per owner so repeat GETs cannot append rows. */
  private pruneOwnerTokens(ownerId: string): void {
    this.db
      .prepare(
        `DELETE FROM csrf_tokens
         WHERE owner_id = ? AND used_at IS NULL
           AND rowid NOT IN (
             SELECT rowid FROM csrf_tokens
             WHERE owner_id = ? AND used_at IS NULL
             ORDER BY rowid DESC LIMIT ?
           )`,
      )
      .run(ownerId, ownerId, MAX_UNUSED_CSRF_TOKENS);
  }

  /**
   * Reclaims expired and consumed tokens, plus tokens whose owner row no longer
   * exists (abandoned pre-sessions and destroyed sessions).
   */
  purgeExpired(): void {
    const now = this.now();
    this.db
      .prepare(
        "DELETE FROM csrf_tokens WHERE expires_at <= ? OR used_at IS NOT NULL",
      )
      .run(now);

    this.db
      .prepare(
        "DELETE FROM csrf_tokens WHERE purpose = 'login' AND owner_id NOT IN (SELECT id FROM pre_sessions)",
      )
      .run();

    this.db
      .prepare(
        "DELETE FROM csrf_tokens WHERE purpose = 'session' AND owner_id NOT IN (SELECT id FROM sessions)",
      )
      .run();
  }

  verify(ownerId: string, token: string, purpose: CsrfPurpose): boolean {
    const row = this.db
      .prepare(
        `SELECT 1 AS ok FROM csrf_tokens
         WHERE token_hash = ? AND owner_id = ? AND purpose = ?
           AND used_at IS NULL AND expires_at > ?`,
      )
      .get(hashToken(token), ownerId, purpose, this.now());
    return row !== undefined;
  }

  /** Single-use: the token is marked used only if it was still valid. */
  consume(ownerId: string, token: string, purpose: CsrfPurpose): boolean {
    const result = this.db
      .prepare(
        `UPDATE csrf_tokens SET used_at = ?
         WHERE token_hash = ? AND owner_id = ? AND purpose = ?
           AND used_at IS NULL AND expires_at > ?`,
      )
      .run(this.now(), hashToken(token), ownerId, purpose, this.now());
    return result.changes === 1;
  }

  /**
   * Atomically claims an unexpired pre-session, consumes the presented login
   * token and revokes every sibling token for that owner.
   *
   * All three statements run inside one synchronous SQLite transaction. Any
   * failed validation throws, rolling the whole attempt back, so an invalid or
   * cross-owner token can never destroy a legitimate handshake.
   */
  claimLoginAttempt(preSessionId: string, token: string): boolean {
    const claimPreSession = this.db.prepare(
      "DELETE FROM pre_sessions WHERE id = ? AND expires_at > ?",
    );
    const consumeToken = this.db.prepare(
      `UPDATE csrf_tokens SET used_at = ?
       WHERE token_hash = ? AND owner_id = ? AND purpose = 'login'
         AND used_at IS NULL AND expires_at > ?`,
    );
    const revokeSiblings = this.db.prepare(
      "DELETE FROM csrf_tokens WHERE owner_id = ?",
    );

    const attempt = this.db.transaction((): true => {
      const now = this.now();
      if (claimPreSession.run(preSessionId, now).changes !== 1) {
        throw LOGIN_ATTEMPT_REJECTED;
      }
      if (
        consumeToken.run(now, hashToken(token), preSessionId, now).changes !== 1
      ) {
        throw LOGIN_ATTEMPT_REJECTED;
      }
      revokeSiblings.run(preSessionId);
      return true;
    });

    try {
      attempt();
      return true;
    } catch (error) {
      if (error === LOGIN_ATTEMPT_REJECTED) {
        return false;
      }
      throw error;
    }
  }

  revokeAll(ownerId: string): void {
    this.db.prepare("DELETE FROM csrf_tokens WHERE owner_id = ?").run(ownerId);
  }
}
