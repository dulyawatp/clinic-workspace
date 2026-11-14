import type { FastifyInstance } from "fastify";
import { buildCapabilityMap } from "../capabilities.js";
import type { AppConfig } from "../config.js";
import { SESSION_COOKIE } from "../security/constants.js";
import type { NativeCredentialStore } from "../session/native-store.js";
import type { SessionStore } from "../session/store.js";
import type { SessionDTO } from "./auth.js";

export interface SessionRouteDeps {
  config: AppConfig;
  sessions: SessionStore;
  native: NativeCredentialStore;
}

/**
 * Reads the session from the server-side store, never from the request body
 * or headers. Capabilities are the default-deny map until a verified manifest
 * exists; the client never derives capabilities from roles.
 */
export function registerSessionRoutes(
  app: FastifyInstance,
  deps: SessionRouteDeps,
): void {
  const { config, sessions, native } = deps;

  app.get("/api/session", async (req, reply) => {
    // Session state is per-cookie and must never be cached or replayed.
    reply.header("Cache-Control", "no-store");

    const sessionId = req.cookies[SESSION_COOKIE];
    const session =
      sessionId === undefined ? undefined : sessions.get(sessionId);

    if (session === undefined) {
      return reply.code(401).send({ code: "AUTH_EXPIRED" });
    }

    const body: SessionDTO = {
      mode: config.upstreamMode,
      sessionEpoch: session.sessionEpoch,
      user: {
        id: session.userId,
        displayName: session.displayName,
        roles: session.roles,
      },
      capabilities: buildCapabilityMap(config.activeWrites, {
        livePhotoUpload:
          config.upstreamMode === "live" &&
          config.live !== undefined &&
          config.livePhotoWrites === true &&
          session.roles.includes("DOC") &&
          native.get(sessionId ?? "") !== undefined,
        livePatientCreate:
          config.upstreamMode === "live" &&
          config.live !== undefined &&
          config.livePatientWrites === true &&
          session.roles.includes("REC") &&
          native.get(sessionId ?? "") !== undefined,
        liveEncounterDraft:
          config.upstreamMode === "live" &&
          config.live !== undefined &&
          config.liveEncounterWrites === true &&
          session.roles.includes("DOC") &&
          native.get(sessionId ?? "") !== undefined,
      }),
    };
    return reply.code(200).send(body);
  });
}
