import { createHash } from "node:crypto";
import type { Database } from "better-sqlite3";

/**
 * `pending` is written before the native PUT, so a crash or restart mid-flight
 * reads as "we cannot prove the outcome" and never re-attempts. `unknown` is
 * terminal for this operation: only a proven no-write or a confirmed save
 * removes the row.
 */
export type ReviewWriteState = "pending" | "unknown";

export interface EncounterReviewWriteRecord {
  userId: string;
  encounterId: string;
  operationKey: string;
  state: ReviewWriteState;
  createdAt: number;
  updatedAt: number;
}

interface ReviewWriteRow {
  user_id: string;
  encounter_id: string;
  operation_key: string;
  state: string;
  created_at: number;
  updated_at: number;
}

export type BeginReviewOutcome =
  /** This attempt owns the native write. */
  | { kind: "new" }
  /** The same operation already reached an uncertain outcome: answer unknown. */
  | { kind: "replay"; state: Extract<ReviewWriteState, "unknown"> }
  /** Another unresolved write owns this encounter for this user: no write. */
  | { kind: "blocked"; state: ReviewWriteState };

/**
 * One unresolved review write per `(user, encounter)`, independent of tabs,
 * sessions and process restarts. The primary key is the hard guarantee.
 */
const SCHEMA = `
CREATE TABLE IF NOT EXISTS encounter_review_writes (
  user_id TEXT NOT NULL,
  encounter_id TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, encounter_id)
);
`;

function toRecord(row: ReviewWriteRow): EncounterReviewWriteRecord {
  return {
    userId: row.user_id,
    encounterId: row.encounter_id,
    operationKey: row.operation_key,
    state: row.state === "unknown" ? "unknown" : "pending",
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

/**
 * Opaque operation identity for one review intent: the caller's own draft plus
 * the base version it edits. Deliberately derived from server-owned values and
 * the base timestamp only, so no review text is ever stored or hashed here.
 */
export function reviewOperationKey(
  encounterId: string,
  baseModified: string,
): string {
  return createHash("sha256")
    .update(JSON.stringify(["encounter-review", encounterId, baseModified]))
    .digest("hex");
}

/**
 * Durable write-intent lock for live review-details edits. It stores no note
 * text, no native patient and no free text at all: only the caller's user id,
 * the encounter docname, an opaque operation key and the outcome state.
 *
 * Rows are never expired: an uncertain outcome must keep blocking identical and
 * later writes until it can be resolved outside this feature. A crash between
 * `begin` and the native PUT therefore leaves a `pending` row that fails closed.
 */
export class EncounterReviewStore {
  constructor(
    private readonly db: Database,
    private readonly now: () => number = Date.now,
  ) {
    db.exec(SCHEMA);
  }

  /**
   * Atomically claims the write-ahead row. A second tab, session or process for
   * the same user and encounter is refused instead of racing the native PUT.
   */
  begin(
    userId: string,
    encounterId: string,
    operationKey: string,
  ): BeginReviewOutcome {
    const now = this.now();
    const inserted = this.db
      .prepare(
        `INSERT INTO encounter_review_writes (user_id, encounter_id, operation_key, state, created_at, updated_at)
         VALUES (?, ?, ?, 'pending', ?, ?)
         ON CONFLICT (user_id, encounter_id) DO NOTHING`,
      )
      .run(userId, encounterId, operationKey, now, now);
    if (inserted.changes === 1) {
      return { kind: "new" };
    }

    const existing = this.get(userId, encounterId);
    if (existing === undefined) {
      // The row vanished between the insert attempt and the read: treat the
      // encounter as still owned rather than starting a concurrent write.
      return { kind: "blocked", state: "pending" };
    }
    if (
      existing.operationKey === operationKey &&
      existing.state === "unknown"
    ) {
      return { kind: "replay", state: "unknown" };
    }
    return { kind: "blocked", state: existing.state };
  }

  markUnknown(userId: string, encounterId: string): void {
    this.db
      .prepare(
        "UPDATE encounter_review_writes SET state = 'unknown', updated_at = ? WHERE user_id = ? AND encounter_id = ?",
      )
      .run(this.now(), userId, encounterId);
  }

  /** Only a proven no-write or a confirmed save may clear the intent. */
  release(userId: string, encounterId: string): void {
    this.db
      .prepare(
        "DELETE FROM encounter_review_writes WHERE user_id = ? AND encounter_id = ?",
      )
      .run(userId, encounterId);
  }

  get(
    userId: string,
    encounterId: string,
  ): EncounterReviewWriteRecord | undefined {
    const row = this.db
      .prepare(
        "SELECT user_id, encounter_id, operation_key, state, created_at, updated_at FROM encounter_review_writes WHERE user_id = ? AND encounter_id = ?",
      )
      .get(userId, encounterId) as ReviewWriteRow | undefined;
    return row === undefined ? undefined : toRecord(row);
  }
}
