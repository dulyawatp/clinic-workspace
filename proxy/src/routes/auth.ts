import accounts from "@clinic/contracts/fixtures/accounts.json" with {
  type: "json",
};
import type { CookieSerializeOptions } from "@fastify/cookie";
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import {
  buildCapabilityMap,
  type CapabilityMap,
  isRoleCode,
  type RoleCode,
} from "../capabilities.js";
import type { AppConfig } from "../config.js";
import type { AuditCode, AuditEvent, AuditSink } from "../security/audit.js";
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  SESSION_COOKIE,
} from "../security/constants.js";
import { hashIp } from "../security/csrf.js";
import type { CsrfStore } from "../security/csrf-store.js";
import { IssuanceThrottle } from "../security/issuance-throttle.js";
import { assertTrustedOrigin } from "../security/origin.js";
import {
  isNativeCredentials,
  type NativeCredentialStore,
  type NativeCredentials,
} from "../session/native-store.js";
import type { SessionStore, SessionUser } from "../session/store.js";

export interface CsrfResponse {
  token: string;
}

export interface LoginRequest {
  usr: string;
  pwd: string;
}

export interface SessionDTO {
  /** Which upstream answered this login; the client never infers it. */
  mode: "live" | "fixture";
  /** Non-secret session correlation only; cannot authenticate requests. */
  sessionEpoch: string;
  user: {
    id: string;
    displayName: string;
    roles: RoleCode[];
  };
  capabilities: CapabilityMap;
}

/**
 * One successful login. `native` is the originating clinician's Frappe session
 * and is stored server-side only; it is never serialised to the client.
 */
export interface AuthenticatedUser {
  user: SessionUser;
  native?: NativeCredentials;
}

/**
 * Upstream authentication interface. The fixture implementation is the default;
 * the live implementation exists only for the explicitly enabled lab.
 */
export type Authenticate = (
  credentials: LoginRequest,
) => Promise<AuthenticatedUser | null>;

export interface AuthDeps {
  config: AppConfig;
  csrf: CsrfStore;
  sessions: SessionStore;
  native: NativeCredentialStore;
  authenticate: Authenticate;
  auditSink: AuditSink;
}

// Fictional fixture accounts, shared with the app's test harness. These are
// demo credentials, not secrets; no other account source exists.
function toSessionUser(account: (typeof accounts)[number]): SessionUser | null {
  const roles = account.user.roles.filter(isRoleCode);
  // Fail closed if a fixture ever carries a role outside the closed union.
  if (roles.length !== account.user.roles.length) return null;
  return {
    id: account.user.id,
    displayName: account.user.displayName,
    roles,
  };
}

export function fixtureAuthenticate(): Authenticate {
  return async ({ usr, pwd }) => {
    const match = accounts.find(
      (account) => account.usr === usr && account.pwd === pwd,
    );
    if (match === undefined) return null;
    const user = toSessionUser(match);
    return user === null ? null : { user };
  };
}

export function headerValue(
  value: string | string[] | undefined,
): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function hasJsonContentType(req: FastifyRequest): boolean {
  const header = headerValue(req.headers["content-type"]);
  return header?.split(";")[0]?.trim().toLowerCase() === "application/json";
}

function parseLoginRequest(body: unknown): LoginRequest | null {
  if (typeof body !== "object" || body === null) {
    return null;
  }

  const { usr, pwd } = body as Record<string, unknown>;
  if (
    typeof usr !== "string" ||
    typeof pwd !== "string" ||
    usr.length === 0 ||
    pwd.length === 0
  ) {
    return null;
  }

  return { usr, pwd };
}

function auditRejection(
  sink: AuditSink,
  req: FastifyRequest,
  event: AuditEvent,
  status: number,
  code: AuditCode,
): void {
  sink({
    event,
    ipHash: hashIp(req.ip),
    route: req.routeOptions.url ?? "unknown",
    status,
    code,
  });
}

function reject(
  reply: FastifyReply,
  status: number,
  code: string,
): FastifyReply {
  return reply.code(status).send({ code });
}

export function registerAuthRoutes(app: FastifyInstance, deps: AuthDeps): void {
  const { config, csrf, sessions, native, authenticate, auditSink } = deps;

  const cookieOptions: CookieSerializeOptions = {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: config.appOrigin.startsWith("https://"),
  };

  // Auth responses carry CSRF tokens and session state: never cache them,
  // including anonymous/authenticated CSRF, login/logout and their rejections.
  app.addHook("onSend", async (req, reply, payload) => {
    if (req.routeOptions.url?.startsWith("/api/auth")) {
      reply.header("cache-control", "no-store");
    }
    return payload;
  });

  // Caller-scoped throttle for *new* anonymous handshakes. The key is the
  // server-observed socket address (hashed), never a forwarded header.
  const issuanceThrottle = new IssuanceThrottle();

  app.get("/api/auth/csrf", async (req, reply) => {
    // Reclaim expired/consumed/abandoned rows before issuing anything new.
    sessions.purgeExpiredPreSessions();
    csrf.purgeExpired();

    const sessionId = req.cookies[SESSION_COOKIE];
    const session =
      sessionId === undefined ? undefined : sessions.get(sessionId);

    if (sessionId !== undefined && session !== undefined) {
      const token = csrf.issue(sessionId, "session");
      reply.setCookie(CSRF_COOKIE, sessionId, cookieOptions);
      return reply.code(200).send({ token } satisfies CsrfResponse);
    }

    const existing = req.cookies[CSRF_COOKIE];
    let preSessionId: string | null =
      existing !== undefined && sessions.hasPreSession(existing)
        ? existing
        : null;

    if (preSessionId === null) {
      const clientKey = hashIp(req.socket.remoteAddress ?? "unknown");
      // Caller-scoped throttle first: one caller cannot exhaust the global cap.
      if (!issuanceThrottle.tryAcquire(clientKey)) {
        return reject(reply, 429, "RATE_LIMITED");
      }

      // Global cap stays the final safety net.
      preSessionId = sessions.createPreSession();
      if (preSessionId === null) {
        return reject(reply, 429, "RATE_LIMITED");
      }
    }

    const token = csrf.issue(preSessionId, "login");
    reply.setCookie(CSRF_COOKIE, preSessionId, cookieOptions);
    return reply.code(200).send({ token } satisfies CsrfResponse);
  });

  // Origin and content type run before the body parser so a cross-origin or
  // form-encoded request is rejected without its payload being read.
  const loginGuard = async (req: FastifyRequest, reply: FastifyReply) => {
    if (!assertTrustedOrigin(req, config.appOrigin)) {
      auditRejection(
        auditSink,
        req,
        "auth.login.rejected_origin",
        403,
        "FORBIDDEN",
      );
      await reject(reply, 403, "FORBIDDEN");
      return;
    }

    if (!hasJsonContentType(req)) {
      auditRejection(
        auditSink,
        req,
        "auth.login.rejected_content_type",
        415,
        "UNSUPPORTED",
      );
      await reject(reply, 415, "UNSUPPORTED");
    }
  };

  const logoutGuard = async (req: FastifyRequest, reply: FastifyReply) => {
    if (!assertTrustedOrigin(req, config.appOrigin)) {
      auditRejection(
        auditSink,
        req,
        "auth.logout.rejected_origin",
        403,
        "FORBIDDEN",
      );
      await reject(reply, 403, "FORBIDDEN");
      return;
    }

    if (!hasJsonContentType(req)) {
      auditRejection(
        auditSink,
        req,
        "auth.logout.rejected_content_type",
        415,
        "UNSUPPORTED",
      );
      await reject(reply, 415, "UNSUPPORTED");
    }
  };

  app.post("/api/auth/login", { onRequest: loginGuard }, async (req, reply) => {
    const credentials = parseLoginRequest(req.body);
    if (credentials === null) {
      auditRejection(
        auditSink,
        req,
        "auth.login.rejected_body",
        400,
        "VALIDATION",
      );
      return reject(reply, 400, "VALIDATION");
    }

    const preSessionId = req.cookies[CSRF_COOKIE];
    const token = headerValue(req.headers[CSRF_HEADER]);
    if (preSessionId === undefined || token === undefined) {
      auditRejection(
        auditSink,
        req,
        "auth.login.rejected_csrf",
        403,
        "FORBIDDEN",
      );
      return reject(reply, 403, "FORBIDDEN");
    }

    // One synchronous transaction claims the unexpired pre-session, consumes
    // the presented token and revokes sibling tokens. A failed validation rolls
    // all three back, so a bad token cannot destroy a legitimate handshake. The
    // upstream call happens only after the transaction commits.
    if (!csrf.claimLoginAttempt(preSessionId, token)) {
      auditRejection(
        auditSink,
        req,
        "auth.login.rejected_csrf",
        403,
        "FORBIDDEN",
      );
      return reject(reply, 403, "FORBIDDEN");
    }

    let authenticated: AuthenticatedUser | null;
    try {
      authenticated = await authenticate(credentials);
    } catch {
      // The exception message is never returned, logged or audited.
      auditRejection(
        auditSink,
        req,
        "auth.login.upstream_error",
        502,
        "UPSTREAM_UNAVAILABLE",
      );
      return reject(reply, 502, "UPSTREAM_UNAVAILABLE");
    }

    if (authenticated === null) {
      auditRejection(
        auditSink,
        req,
        "auth.login.rejected_credentials",
        403,
        "FORBIDDEN",
      );
      return reject(reply, 403, "FORBIDDEN");
    }

    // Live sessions without a usable native credential are unusable for writes
    // and must not be created as if they were authenticated.
    if (
      config.upstreamMode === "live" &&
      !isNativeCredentials(authenticated.native)
    ) {
      auditRejection(
        auditSink,
        req,
        "auth.login.upstream_error",
        502,
        "UPSTREAM_UNAVAILABLE",
      );
      return reject(reply, 502, "UPSTREAM_UNAVAILABLE");
    }

    const user = authenticated.user;
    const sessionId = sessions.create(user);
    const createdSession = sessions.get(sessionId);
    if (!createdSession) throw new Error("SESSION_NOT_FOUND");
    if (isNativeCredentials(authenticated.native)) {
      try {
        native.set(sessionId, authenticated.native, createdSession.expiresAt);
      } catch {
        sessions.destroy(sessionId);
        auditRejection(
          auditSink,
          req,
          "auth.login.upstream_error",
          502,
          "UPSTREAM_UNAVAILABLE",
        );
        return reject(reply, 502, "UPSTREAM_UNAVAILABLE");
      }
    }
    reply.setCookie(SESSION_COOKIE, sessionId, cookieOptions);
    reply.clearCookie(CSRF_COOKIE, { path: "/" });

    const livePhotoUpload =
      config.upstreamMode === "live" &&
      config.live !== undefined &&
      config.livePhotoWrites === true &&
      user.roles.includes("DOC");
    const livePatientCreate =
      config.upstreamMode === "live" &&
      config.live !== undefined &&
      config.livePatientWrites === true &&
      user.roles.includes("REC");
    const liveEncounterDraft =
      config.upstreamMode === "live" &&
      config.live !== undefined &&
      config.liveEncounterWrites === true &&
      user.roles.includes("DOC");
    const body: SessionDTO = {
      mode: config.upstreamMode,
      sessionEpoch: createdSession.sessionEpoch,
      user: { id: user.id, displayName: user.displayName, roles: user.roles },
      capabilities: buildCapabilityMap(config.activeWrites, {
        livePhotoUpload,
        livePatientCreate,
        liveEncounterDraft,
      }),
    };
    return reply.code(200).send(body);
  });

  app.post(
    "/api/auth/logout",
    { onRequest: logoutGuard },
    async (req, reply) => {
      const sessionId = req.cookies[SESSION_COOKIE];
      // A missing or already-expired session is a no-op: logout stays idempotent
      // and only clears cookies. CSRF is enforced for a live session.
      const session =
        sessionId === undefined ? undefined : sessions.get(sessionId);
      if (sessionId !== undefined && session !== undefined) {
        const token = headerValue(req.headers[CSRF_HEADER]);
        if (token === undefined || !csrf.consume(sessionId, token, "session")) {
          auditRejection(
            auditSink,
            req,
            "auth.logout.rejected_csrf",
            403,
            "FORBIDDEN",
          );
          return reject(reply, 403, "FORBIDDEN");
        }

        sessions.destroy(sessionId);
        native.clear(sessionId);
        csrf.revokeAll(sessionId);
      }

      reply.clearCookie(SESSION_COOKIE, { path: "/" });
      reply.clearCookie(CSRF_COOKIE, { path: "/" });
      return reply.code(204).send();
    },
  );
}
