import type { FastifyInstance, FastifyReply } from "fastify";
import type { LiveUpstream } from "../config.js";
import {
  type EncounterDraftStore,
  patientIntentFingerprint,
} from "../writes/encounter-draft-store.js";
import {
  type LiveAccessDeps,
  liveUpstream,
  requireLiveAccess,
} from "./live-access.js";

const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 100;

export interface LiveEncounterDraftsDeps extends LiveAccessDeps {
  encounterDrafts: EncounterDraftStore;
}

function sendCode(reply: FastifyReply, status: number, code: string) {
  return reply.code(status).send({ code });
}

function parseInteger(raw: unknown, fallback: number): number {
  if (typeof raw !== "string" || raw.trim() === "") return fallback;
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 0) return fallback;
  return value;
}

/**
 * Resolves the opaque patient fingerprint to a workspace reference using only
 * the server-owned synthetic allow-list. A fingerprint that is not configured
 * resolves to nothing, so an old or foreign row is never described to a client.
 */
function referencesByFingerprint(upstream: LiveUpstream): Map<string, string> {
  const references = new Map<string, string>();
  for (const [reference, nativePatientId] of upstream.syntheticPatients) {
    const fingerprint = patientIntentFingerprint(nativePatientId);
    // Duplicate native names are a configuration concern; the first reference wins.
    if (!references.has(fingerprint)) {
      references.set(fingerprint, reference);
    }
  }
  return references;
}

/**
 * Cross-session recovery hints for the caller's *own* synthetic encounter
 * drafts. This is a local list from `EncounterDraftStore`, not proof that a
 * native draft still exists or is still readable: opening one always goes
 * through `GET /api/live/encounter-review`, which re-authorizes the docname and
 * its patient with the caller's own native session against the configured
 * allow-list.
 *
 * Requires live mode, the explicit `CLINIC_LIVE_ENCOUNTER_WRITES` opt-in, the
 * `DOC` role and a native credential. The user id comes from the session, never
 * from the client; only `created` rows with a configured patient fingerprint are
 * shown, and the payload carries no native patient, encounter or practitioner
 * identifier and no clinical text. Reading performs no write and no upstream
 * call, and no permission is changed.
 *
 * Response: `{ drafts: [{ request_key, patient_reference, created_at }],
 * truncated }`, newest first, where `created_at` is an ISO 8601 timestamp and
 * `truncated` states that further local rows exist beyond `limit`.
 */
export function registerLiveEncounterDraftRoutes(
  app: FastifyInstance,
  deps: LiveEncounterDraftsDeps,
): void {
  const { config, encounterDrafts } = deps;
  const upstream = liveUpstream(config);
  const references =
    upstream === undefined
      ? new Map<string, string>()
      : referencesByFingerprint(upstream);

  app.get("/api/live/encounter-drafts", async (req, reply) => {
    reply.header("cache-control", "no-store");

    if (upstream === undefined) {
      return sendCode(reply, 404, "UNSUPPORTED");
    }
    if (config.liveEncounterWrites !== true) {
      return sendCode(reply, 403, "WRITE_DISABLED");
    }
    const access = requireLiveAccess(req, reply, deps, upstream);
    if (access === null) return;

    const query = req.query as Record<string, unknown>;
    const limit = Math.max(
      1,
      Math.min(parseInteger(query.limit, DEFAULT_LIMIT), MAX_LIMIT),
    );

    const page = encounterDrafts.listCreatedHints(access.userId, limit);

    const drafts: Array<{
      request_key: string;
      patient_reference: string;
      created_at: string;
    }> = [];
    for (const hint of page.drafts) {
      const patientReference = references.get(hint.patientFingerprint);
      if (patientReference === undefined) continue;
      drafts.push({
        request_key: hint.requestKey,
        patient_reference: patientReference,
        created_at: new Date(hint.createdAt).toISOString(),
      });
    }

    return reply.code(200).send({ drafts, truncated: page.truncated });
  });
}
