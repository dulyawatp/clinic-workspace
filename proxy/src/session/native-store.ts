/**
 * The originating clinician's native Frappe session. Both values are secrets:
 * they live in process memory only and are never serialised to a client.
 */
export interface NativeCredentials {
  sid: string;
  csrf: string;
}

// Printable ASCII only: a stored value can never inject a cookie pair or header.
const NATIVE_VALUE = /^[\x21-\x7e]{1,1024}$/;

export function isNativeCredentials(
  value: unknown,
): value is NativeCredentials {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const { sid, csrf } = value as Record<string, unknown>;
  return (
    typeof sid === "string" &&
    typeof csrf === "string" &&
    NATIVE_VALUE.test(sid) &&
    NATIVE_VALUE.test(csrf) &&
    !/[;,]/.test(sid) &&
    !/[;,]/.test(csrf)
  );
}

export interface StoredNativeCredentials extends NativeCredentials {
  expiresAt: number;
}

/** Bounded so a hostile login churn cannot grow process memory without limit. */
const MAX_NATIVE_SESSIONS = 10_000;

/**
 * Process-memory-only store for the originating clinician's native Frappe
 * session. Nothing here is ever written to SQLite, serialised to a client or
 * logged. A restart drops every credential (fail closed); expiry is checked on
 * every read against the session's own expiry.
 */
export class NativeCredentialStore {
  private readonly entries = new Map<string, StoredNativeCredentials>();

  constructor(private readonly now: () => number = Date.now) {}

  set(
    sessionId: string,
    credentials: NativeCredentials,
    expiresAt: number,
  ): void {
    if (!isNativeCredentials(credentials) || !Number.isFinite(expiresAt)) {
      throw new Error("INVALID_NATIVE_CREDENTIALS");
    }

    this.pruneExpired();
    if (this.entries.size >= MAX_NATIVE_SESSIONS) {
      // Fail closed instead of evicting another clinician's live credential.
      throw new Error("NATIVE_CREDENTIAL_CAPACITY");
    }

    this.entries.set(sessionId, {
      sid: credentials.sid,
      csrf: credentials.csrf,
      expiresAt,
    });
  }

  get(sessionId: string): NativeCredentials | undefined {
    const entry = this.entries.get(sessionId);
    if (entry === undefined) {
      return undefined;
    }
    if (entry.expiresAt <= this.now()) {
      this.entries.delete(sessionId);
      return undefined;
    }
    if (!isNativeCredentials(entry)) {
      this.entries.delete(sessionId);
      return undefined;
    }
    return { sid: entry.sid, csrf: entry.csrf };
  }

  clear(sessionId: string): void {
    this.entries.delete(sessionId);
  }

  clearAll(): void {
    this.entries.clear();
  }

  private pruneExpired(): void {
    const now = this.now();
    for (const [sessionId, entry] of this.entries) {
      if (entry.expiresAt <= now) {
        this.entries.delete(sessionId);
      }
    }
  }
}
