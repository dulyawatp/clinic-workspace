import type { FastifyReply, FastifyRequest } from "fastify";
import type { RoleCode } from "../capabilities.js";
import type { AppConfig, LiveUpstream } from "../config.js";
import { CSRF_COOKIE, SESSION_COOKIE } from "../security/constants.js";
import type { CsrfStore } from "../security/csrf-store.js";
import type {
  NativeCredentialStore,
  NativeCredentials,
} from "../session/native-store.js";
import type { SessionStore } from "../session/store.js";

export const CLINICIAN_ROLE = "DOC";

export interface LiveAccessDeps {
  config: AppConfig;
  sessions: SessionStore;
  csrf: CsrfStore;
  native: NativeCredentialStore;
}

export function liveUpstream(config: AppConfig): LiveUpstream | undefined {
  return config.upstreamMode === "live" ? config.live : undefined;
}

export interface LiveAccess {
  sessionId: string;
  /** Server-resolved session identity; never a client value. */
  userId: string;
  native: NativeCredentials;
}

/** Sends the machine-coded refusal and returns `null` on any failed check. */
export function requireLiveAccess(
  req: FastifyRequest,
  reply: FastifyReply,
  deps: LiveAccessDeps,
  upstream: LiveUpstream | undefined,
  requiredRole: RoleCode | RoleCode[] = CLINICIAN_ROLE,
): LiveAccess | null {
  if (upstream === undefined) {
    reply.code(404).send({ code: "UNSUPPORTED" });
    return null;
  }

  const sessionId = req.cookies[SESSION_COOKIE];
  const session =
    sessionId === undefined ? undefined : deps.sessions.get(sessionId);
  if (sessionId === undefined || session === undefined) {
    reply.code(401).send({ code: "AUTH_EXPIRED" });
    return null;
  }
  const allowedRoles = Array.isArray(requiredRole)
    ? requiredRole
    : [requiredRole];
  if (!session.roles.some((role) => allowedRoles.includes(role))) {
    reply.code(403).send({ code: "FORBIDDEN" });
    return null;
  }

  const native = deps.native.get(sessionId);
  if (native === undefined) {
    invalidateLiveAccess(reply, deps, sessionId);
    reply.code(401).send({ code: "AUTH_EXPIRED" });
    return null;
  }

  return { sessionId, userId: session.userId, native };
}

export function invalidateLiveAccess(
  reply: FastifyReply,
  deps: LiveAccessDeps,
  sessionId: string,
): void {
  deps.native.clear(sessionId);
  deps.sessions.destroy(sessionId);
  deps.csrf.revokeAll(sessionId);
  reply.clearCookie(SESSION_COOKIE, { path: "/" });
  reply.clearCookie(CSRF_COOKIE, { path: "/" });
}
