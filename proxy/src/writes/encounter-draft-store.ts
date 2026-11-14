import { createHash } from "node:crypto";
import type { Database } from "better-sqlite3";

/**
 * `pending` is written before the upstream call, so a process restart mid-flight
 * reads as "we cannot prove the outcome" and never re-attempts. `created` and
 * `unknown` are terminal for a request key.
 */
export type EncounterDraftState = "pending" | "created" | "unknown";

export interface EncounterDraftRecord {
  userId: string;
  requestKey: string;
  payloadHash: string;
  state: EncounterDraftState;
  encounterId?: string;
  createdAt: number;
  updatedAt: number;
}

interface EncounterDraftRow {
  user_id: string;
  request_key: string;
  payload_hash: string;
  state: string;
  encounter_id: string | null;
  created_at: number;
  updated_at: number;
}

/** A local, non-authoritative hint that a draft was created for this user. */
export interface EncounterDraftHint {
  requestKey: string;
  /** Opaque patient fingerprint; only the route may resolve it. */
  patientFingerprint: string;
  createdAt: number;
}

export type BeginDraftOutcome =
  /** This attempt owns the upstream create. */
  | { kind: "new" }
  /** Same user, same key, same resolved payload: never call upstream again. */
  | {
      kind: "replay";
      state: EncounterDraftState;
      encounterId?: string;
    }
  /** Same key, different resolved payload: refuse instead of guessing. */
  | { kind: "conflict" }
  /**
   * A different request key already has an unresolved (`pending`/`unknown`)
   * intent for this user's patient. Nothing is sent; the earlier key owns the
   * outcome.
   */
  | { kind: "locked"; state: "pending" | "unknown" };

const SCHEMA = `
CREATE TABLE IF NOT EXISTS encounter_drafts (
  user_id TEXT NOT NULL,
  request_key TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  patient_fingerprint TEXT,
  state TEXT NOT NULL,
  encounter_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, request_key)
);
`;

/**
 * One unresolved intent per user and patient. `created` rows are outside the
 * index, so a successful draft never blocks a later legitimate one; `pending`
 * and `unknown` rows do, until a definite failure deletes the row.
 */
const INTENT_INDEX = `
CREATE UNIQUE INDEX IF NOT EXISTS encounter_drafts_patient_intent
  ON encounter_drafts (user_id, patient_fingerprint)
  WHERE patient_fingerprint IS NOT NULL AND state IN ('pending', 'unknown');
`;

function ensureSchema(db: Database): void {
  db.exec(SCHEMA);
  // Migration safety: an encounter_drafts table created before the intent lock
  // has no fingerprint column. Existing rows keep NULL and stay replayable by
  // request key only.
  const columns = db
    .prepare("PRAGMA table_info(encounter_drafts)")
    .all() as Array<{ name: string }>;
  if (!columns.some((column) => column.name === "patient_fingerprint")) {
    db.exec("ALTER TABLE encounter_drafts ADD COLUMN patient_fingerprint TEXT");
  }
  db.exec(INTENT_INDEX);
}

function toRecord(row: EncounterDraftRow): EncounterDraftRecord {
  return {
    userId: row.user_id,
    requestKey: row.request_key,
    payloadHash: row.payload_hash,
    state: row.state as EncounterDraftState,
    ...(row.encounter_id === null ? {} : { encounterId: row.encounter_id }),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

/**
 * Stable, non-reversible fingerprint of the resolved draft payload. Only
 * server-owned values enter it, so a replayed key with a different patient
 * resolves to a conflict instead of a second native draft.
 */
export function hashEncounterDraftPayload(payload: {
  patient: string;
  practitioner: string;
  appointmentType: string;
}): string {
  const canonical = JSON.stringify([
    payload.patient,
    payload.practitioner,
    payload.appointmentType,
  ]);
  return `sha256:${createHash("sha256").update(canonical).digest("hex")}`;
}

function isConstraintViolation(error: unknown): boolean {
  const code =
    typeof error === "object" && error !== null
      ? (error as { code?: unknown }).code
      : undefined;
  return typeof code === "string" && code.startsWith("SQLITE_CONSTRAINT");
}

/**
 * Opaque per-patient intent fingerprint. Never a native patient name: the lock
 * key must not itself disclose a clinical identifier.
 */
export function patientIntentFingerprint(nativePatientId: string): string {
  return createHash("sha256")
    .update(JSON.stringify(["encounter-patient", nativePatientId]))
    .digest("hex");
}

/**
 * Durable outcome log for live synthetic encounter drafts, keyed by
 * `(native user, request key)`. It stores no clinical field and no native
 * patient or practitioner name: only the request key, a SHA-256 fingerprint of
 * the resolved payload, an opaque patient fingerprint for the intent lock and
 * the created `Patient Encounter` docname.
 * Rows never expire: a key that once resolved must stay resolvable, otherwise a
 * late replay would create a duplicate encounter.
 */
export class EncounterDraftStore {
  constructor(
    private readonly db: Database,
    private readonly now: () => number = Date.now,
  ) {
    ensureSchema(db);
  }

  /**
   * Atomically claims the write-ahead row and the per-patient intent. The unique
   * partial index is the hard guarantee, so two processes or two tabs cannot
   * both start a create for the same user and patient.
   */
  begin(
    userId: string,
    requestKey: string,
    payloadHash: string,
    patientFingerprint: string,
  ): BeginDraftOutcome {
    const now = this.now();

    let inserted: { changes: number };
    try {
      inserted = this.db
        .prepare(
          `INSERT INTO encounter_drafts (user_id, request_key, payload_hash, patient_fingerprint, state, encounter_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'pending', NULL, ?, ?)
           ON CONFLICT (user_id, request_key) DO NOTHING`,
        )
        .run(userId, requestKey, payloadHash, patientFingerprint, now, now);
    } catch (error) {
      if (!isConstraintViolation(error)) throw error;
      // A different unresolved key owns this patient; resolve the outcome for
      // the presented key first so a replay is never blocked by the lock.
      return this.resolveExisting(
        userId,
        requestKey,
        payloadHash,
        patientFingerprint,
      );
    }
    if (inserted.changes === 1) {
      return { kind: "new" };
    }

    return this.resolveExisting(
      userId,
      requestKey,
      payloadHash,
      patientFingerprint,
    );
  }

  private resolveExisting(
    userId: string,
    requestKey: string,
    payloadHash: string,
    patientFingerprint: string,
  ): BeginDraftOutcome {
    const existing = this.get(userId, requestKey);
    if (existing !== undefined) {
      if (existing.payloadHash !== payloadHash) {
        return { kind: "conflict" };
      }
      return {
        kind: "replay",
        state: existing.state,
        ...(existing.encounterId === undefined
          ? {}
          : { encounterId: existing.encounterId }),
      };
    }

    // No row for this key: the constraint came from another unresolved key for
    // the same patient. Nothing is created and that key keeps the outcome.
    const blocker = this.blockingIntent(userId, patientFingerprint);
    return blocker === undefined
      ? { kind: "conflict" }
      : { kind: "locked", state: blocker };
  }

  /** The unresolved key that currently owns this user's patient intent. */
  private blockingIntent(
    userId: string,
    patientFingerprint: string,
  ): "pending" | "unknown" | undefined {
    const row = this.db
      .prepare(
        "SELECT state FROM encounter_drafts WHERE user_id = ? AND patient_fingerprint = ? AND state IN ('pending', 'unknown') ORDER BY created_at ASC LIMIT 1",
      )
      .get(userId, patientFingerprint) as { state: string } | undefined;
    if (row === undefined) return undefined;
    return row.state === "unknown" ? "unknown" : "pending";
  }

  markCreated(userId: string, requestKey: string, encounterId: string): void {
    this.db
      .prepare(
        "UPDATE encounter_drafts SET state = 'created', encounter_id = ?, updated_at = ? WHERE user_id = ? AND request_key = ?",
      )
      .run(encounterId, this.now(), userId, requestKey);
  }

  markUnknown(userId: string, requestKey: string): void {
    this.db
      .prepare(
        "UPDATE encounter_drafts SET state = 'unknown', updated_at = ? WHERE user_id = ? AND request_key = ?",
      )
      .run(this.now(), userId, requestKey);
  }

  /**
   * Drops a `pending` row after a definite non-create. An uncertain outcome is
   * kept as `unknown` instead and is never retried automatically.
   */
  release(userId: string, requestKey: string): void {
    this.db
      .prepare(
        "DELETE FROM encounter_drafts WHERE user_id = ? AND request_key = ? AND state = 'pending'",
      )
      .run(userId, requestKey);
  }

  get(userId: string, requestKey: string): EncounterDraftRecord | undefined {
    const row = this.db
      .prepare(
        "SELECT user_id, request_key, payload_hash, state, encounter_id, created_at, updated_at FROM encounter_drafts WHERE user_id = ? AND request_key = ?",
      )
      .get(userId, requestKey) as EncounterDraftRow | undefined;
    return row === undefined ? undefined : toRecord(row);
  }

  /**
   * Cross-session recovery hints for one user: only rows that are *provably*
   * created and carry a patient fingerprint. `pending`/`unknown` rows are never
   * promoted, and a legacy row written before the fingerprint column existed
   * stays invisible because its patient cannot be proven.
   *
   * Ordering is newest first with the request key as a stable tie-break. The
   * caller receives at most `limit` rows and `truncated` when more eligible rows
   * exist; this is a bounded hint list, never a claim that every draft is shown.
   */
  listCreatedHints(
    userId: string,
    limit: number,
  ): { drafts: EncounterDraftHint[]; truncated: boolean } {
    const rows = this.db
      .prepare(
        `SELECT request_key, patient_fingerprint, created_at
           FROM encounter_drafts
          WHERE user_id = ? AND state = 'created' AND patient_fingerprint IS NOT NULL
          ORDER BY created_at DESC, request_key DESC
          LIMIT ?`,
      )
      .all(userId, limit + 1) as Array<{
      request_key: string;
      patient_fingerprint: string;
      created_at: number;
    }>;

    return {
      drafts: rows.slice(0, limit).map((row) => ({
        requestKey: row.request_key,
        patientFingerprint: row.patient_fingerprint,
        createdAt: row.created_at,
      })),
      truncated: rows.length > limit,
    };
  }
}
