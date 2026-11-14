import type { FastifyInstance, FastifyReply } from "fastify";
import type { AppConfig } from "../config.js";
import { CSRF_COOKIE, SESSION_COOKIE } from "../security/constants.js";
import type { CsrfStore } from "../security/csrf-store.js";
import type { NativeCredentialStore } from "../session/native-store.js";
import type { SessionStore } from "../session/store.js";
import { readAuthorizedPatient } from "./patient-authority.js";

const CLINICIAN_ROLE = "DOC";

export interface LiveMetadataDeps {
  config: AppConfig;
  sessions: SessionStore;
  csrf: CsrfStore;
  native: NativeCredentialStore;
  fetchImpl: typeof fetch;
}

interface SyntheticPatientMetadata {
  workspace_patient_id: string;
  native_patient_id: string;
  display_name: string;
}

function sendCode(reply: FastifyReply, status: number, code: string) {
  return reply.code(status).send({ code });
}

/**
 * Returns only the explicitly configured lab workspace mapping, verified for
 * this clinician against the live site. No fixture record, no arbitrary client
 * input and no unverified patient metadata is ever included.
 */
export function registerLiveMetadataRoutes(
  app: FastifyInstance,
  deps: LiveMetadataDeps,
): void {
  const { config, sessions, csrf, native, fetchImpl } = deps;
  const upstream = config.upstreamMode === "live" ? config.live : undefined;

  app.get("/api/live/synthetic-patients", async (req, reply) => {
    reply.header("cache-control", "no-store");

    if (upstream === undefined) {
      return sendCode(reply, 404, "UNSUPPORTED");
    }

    const sessionId = req.cookies[SESSION_COOKIE];
    const session =
      sessionId === undefined ? undefined : sessions.get(sessionId);
    if (sessionId === undefined || session === undefined) {
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }
    if (
      !session.roles.includes(CLINICIAN_ROLE) &&
      !session.roles.includes("REC") &&
      !session.roles.includes("NUR")
    ) {
      return sendCode(reply, 403, "FORBIDDEN");
    }

    const credentials = native.get(sessionId);
    if (credentials === undefined) {
      sessions.destroy(sessionId);
      csrf.revokeAll(sessionId);
      reply.clearCookie(SESSION_COOKIE, { path: "/" });
      reply.clearCookie(CSRF_COOKIE, { path: "/" });
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }

    const items: SyntheticPatientMetadata[] = [];
    for (const [workspaceId, nativePatientId] of upstream.syntheticPatients) {
      const authority = await readAuthorizedPatient(
        upstream,
        fetchImpl,
        credentials,
        nativePatientId,
      );

      if (authority.state === "expired") {
        native.clear(sessionId);
        sessions.destroy(sessionId);
        csrf.revokeAll(sessionId);
        reply.clearCookie(SESSION_COOKIE, { path: "/" });
        reply.clearCookie(CSRF_COOKIE, { path: "/" });
        return sendCode(reply, 401, "AUTH_EXPIRED");
      }
      if (authority.state === "unavailable") {
        return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
      }
      if (authority.state === "authorized") {
        items.push({
          workspace_patient_id: workspaceId,
          native_patient_id: authority.nativePatientId,
          display_name: authority.displayName,
        });
      }
      // `denied` entries are omitted; they are never described to the client.
    }

    return reply.code(200).send({ items });
  });
}
