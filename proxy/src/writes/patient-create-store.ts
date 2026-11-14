import { createHash } from "node:crypto";
import type { Database } from "better-sqlite3";

/**
 * `pending` is written before the upstream call, so a process restart mid-flight
 * reads as "we cannot prove the outcome" and never re-attempts. `created` and
 * `unknown` are terminal for a request key.
 */
export type PatientCreateState = "pending" | "created" | "unknown";

export interface PatientCreateRecord {
  userId: string;
  requestKey: string;
  payloadHash: string;
  state: PatientCreateState;
  patientId?: string;
  createdAt: number;
  updatedAt: number;
}

interface PatientCreateRow {
  user_id: string;
  request_key: string;
  payload_hash: string;
  state: string;
  patient_id: string | null;
  created_at: number;
  updated_at: number;
}

export type BeginOutcome =
  /** This attempt owns the upstream create. */
  | { kind: "new" }
  /** Same user, same key, same payload: never call upstream again. */
  | { kind: "replay"; state: PatientCreateState; patientId?: string }
  /** Same key, different payload: refuse instead of guessing. */
  | { kind: "conflict" };

const SCHEMA = `
CREATE TABLE IF NOT EXISTS patient_creates (
  user_id TEXT NOT NULL,
  request_key TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  state TEXT NOT NULL,
  patient_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, request_key)
);
`;

function toRecord(row: PatientCreateRow): PatientCreateRecord {
  return {
    userId: row.user_id,
    requestKey: row.request_key,
    payloadHash: row.payload_hash,
    state: row.state as PatientCreateState,
    ...(row.patient_id === null ? {} : { patientId: row.patient_id }),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

/** Stable, non-reversible fingerprint of the creation payload. */
export function hashCreatePayload(payload: {
  firstName: string;
  lastName?: string;
  sex: string;
}): string {
  const canonical = JSON.stringify([
    payload.firstName,
    payload.lastName ?? "",
    payload.sex,
  ]);
  return `sha256:${createHash("sha256").update(canonical).digest("hex")}`;
}

/**
 * Durable outcome log for live patient registration, keyed by
 * `(native user, request key)`. It stores no field the clinician typed: only the
 * request key, a SHA-256 fingerprint of the payload and the native `Patient`
 * docname. That docname is native-owned and may itself be a human name, so this
 * table is still sensitive local state, not a de-identified record.
 *
 * Rows never expire in this lab slice: a key that once resolved must stay
 * resolvable, otherwise a late replay would create a duplicate patient.
 */
export class PatientCreateStore {
  constructor(
    private readonly db: Database,
    private readonly now: () => number = Date.now,
  ) {
    this.db.exec(SCHEMA);
  }

  begin(userId: string, requestKey: string, payloadHash: string): BeginOutcome {
    const now = this.now();

    // Write-ahead: the row exists before the upstream call, so a crash cannot
    // leave an unrecorded, retryable key behind.
    const inserted = this.db
      .prepare(
        `INSERT INTO patient_creates (user_id, request_key, payload_hash, state, patient_id, created_at, updated_at)
         VALUES (?, ?, ?, 'pending', NULL, ?, ?)
         ON CONFLICT (user_id, request_key) DO NOTHING`,
      )
      .run(userId, requestKey, payloadHash, now, now);
    if (inserted.changes === 1) {
      return { kind: "new" };
    }

    const existing = this.get(userId, requestKey);
    if (existing === undefined) {
      // Cannot happen without manual deletion; fail closed rather than create.
      return { kind: "conflict" };
    }
    if (existing.payloadHash !== payloadHash) {
      return { kind: "conflict" };
    }
    return {
      kind: "replay",
      state: existing.state,
      ...(existing.patientId === undefined
        ? {}
        : { patientId: existing.patientId }),
    };
  }

  markCreated(userId: string, requestKey: string, patientId: string): void {
    this.db
      .prepare(
        "UPDATE patient_creates SET state = 'created', patient_id = ?, updated_at = ? WHERE user_id = ? AND request_key = ?",
      )
      .run(patientId, this.now(), userId, requestKey);
  }

  markUnknown(userId: string, requestKey: string): void {
    this.db
      .prepare(
        "UPDATE patient_creates SET state = 'unknown', updated_at = ? WHERE user_id = ? AND request_key = ?",
      )
      .run(this.now(), userId, requestKey);
  }

  /**
   * Drops a `pending` row after a definite non-create (the native site refused
   * or nothing was sent). The clinician may retry the same key; an uncertain
   * outcome is kept as `unknown` instead and is never retried automatically.
   */
  release(userId: string, requestKey: string): void {
    this.db
      .prepare(
        "DELETE FROM patient_creates WHERE user_id = ? AND request_key = ? AND state = 'pending'",
      )
      .run(userId, requestKey);
  }

  get(userId: string, requestKey: string): PatientCreateRecord | undefined {
    const row = this.db
      .prepare(
        "SELECT user_id, request_key, payload_hash, state, patient_id, created_at, updated_at FROM patient_creates WHERE user_id = ? AND request_key = ?",
      )
      .get(userId, requestKey) as PatientCreateRow | undefined;
    return row === undefined ? undefined : toRecord(row);
  }
}
