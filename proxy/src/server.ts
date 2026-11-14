import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { pathToFileURL } from "node:url";
import cookie from "@fastify/cookie";
import Database from "better-sqlite3";
import Fastify, { type FastifyInstance, LogController } from "fastify";
import { type AppConfig, isSafeLiveOrigin, loadConfig } from "./config.js";
import { type Authenticate, fixtureAuthenticate } from "./routes/auth.js";
import { registerRoutes } from "./routes/index.js";
import { type AuditSink, createStderrAuditSink } from "./security/audit.js";
import { CsrfStore } from "./security/csrf-store.js";
import { isValidAppOrigin } from "./security/origin.js";
import { createLiveAuthenticate } from "./session/live-auth.js";
import { NativeCredentialStore } from "./session/native-store.js";
import { SessionStore } from "./session/store.js";
import { EncounterDraftStore } from "./writes/encounter-draft-store.js";
import { EncounterReviewStore } from "./writes/encounter-review-store.js";
import { PatientCreateStore } from "./writes/patient-create-store.js";

export interface BuildServerOptions {
  config?: AppConfig;
  database?: Database.Database;
  authenticate?: Authenticate;
  auditSink?: AuditSink;
  fetchImpl?: typeof fetch;
  native?: NativeCredentialStore;
  patientCreates?: PatientCreateStore;
  encounterDrafts?: EncounterDraftStore;
  encounterReviews?: EncounterReviewStore;
}

function openDatabase(path: string): Database.Database {
  if (path !== ":memory:") {
    mkdirSync(dirname(path), { recursive: true });
  }

  return new Database(path);
}

function clientStatusFromError(error: unknown): number {
  if (typeof error === "object" && error !== null && "statusCode" in error) {
    const statusCode = (error as { statusCode?: unknown }).statusCode;
    if (
      typeof statusCode === "number" &&
      statusCode >= 400 &&
      statusCode < 500
    ) {
      return statusCode;
    }
  }

  return 500;
}

function machineCodeForStatus(status: number): string {
  switch (status) {
    case 400:
    case 422:
      return "VALIDATION";
    case 401:
      return "AUTH_EXPIRED";
    case 403:
      return "FORBIDDEN";
    case 409:
      return "CONFLICT_STALE";
    case 413:
      return "FILE_TOO_LARGE";
    case 415:
      return "UNSUPPORTED";
    case 429:
      return "RATE_LIMITED";
    default:
      return "UNKNOWN";
  }
}

export function buildServer(options: BuildServerOptions = {}): FastifyInstance {
  const config = options.config ?? loadConfig();

  // Invalid configuration is a startup error: no request is ever served with
  // an opaque (`file:`, `data:`, "null") or non-HTTP(S) allow-list entry.
  if (!isValidAppOrigin(config.appOrigin)) {
    throw new Error(
      "INVALID_APP_ORIGIN: APP_ORIGIN must be a concrete http(s) origin",
    );
  }

  // Live mode is only reachable through an explicit, complete lab opt-in.
  // Refuse before touching storage, regardless of an injected authenticator.
  if (config.upstreamMode === "live") {
    if (config.live === undefined) {
      throw new Error(
        "LIVE_LAB_NOT_ENABLED: live mode requires the explicit lab configuration",
      );
    }
    if (!isSafeLiveOrigin(config.live.origin)) {
      throw new Error(
        "INSECURE_FRAPPE_ORIGIN: live origin must be https or loopback http",
      );
    }
  }

  const database = options.database ?? openDatabase(config.databasePath);
  const app = Fastify({
    logger: false,
    logController: new LogController({ disableRequestLogging: true }),
  });

  // Machine code only: no exception message, stack or cause is ever serialised.
  app.setErrorHandler((error, _req, reply) => {
    const status = clientStatusFromError(error);
    return reply.code(status).send({ code: machineCodeForStatus(status) });
  });

  // Narrow exception only: logout is a bodyless state change, so an empty JSON
  // body is treated as `{}`. Non-empty bodies are delegated to Fastify's
  // hardened parser with `'error'` prototype/constructor actions, so nested
  // `__proto__`/`constructor.prototype` payloads are rejected with a safe 400
  // and malformed JSON never escalates to a 500.
  const parseJsonBody = app.getDefaultJsonParser("error", "error");
  app.addContentTypeParser(
    "application/json",
    { parseAs: "string" },
    (req, body, done) => {
      const raw = typeof body === "string" ? body : body.toString();
      if (raw.trim() === "") {
        done(null, {});
        return;
      }

      parseJsonBody(req, raw, done);
    },
  );

  // Stores create their tables here, before any route can use them.
  const csrf = new CsrfStore(database);
  const sessions = new SessionStore(database);
  const native = options.native ?? new NativeCredentialStore();
  const patientCreates =
    options.patientCreates ?? new PatientCreateStore(database);
  const encounterDrafts =
    options.encounterDrafts ?? new EncounterDraftStore(database);
  const encounterReviews =
    options.encounterReviews ?? new EncounterReviewStore(database);
  const fetchImpl = options.fetchImpl ?? fetch;
  const authenticate =
    options.authenticate ??
    (config.upstreamMode === "live" && config.live !== undefined
      ? createLiveAuthenticate(config.live, fetchImpl)
      : fixtureAuthenticate());
  const auditSink = options.auditSink ?? createStderrAuditSink();

  app.register(cookie);
  registerRoutes(app, {
    config,
    csrf,
    sessions,
    native,
    authenticate,
    auditSink,
    fetchImpl,
    patientCreates,
    encounterDrafts,
    encounterReviews,
  });

  return app;
}

const DEFAULT_PORT = 3000;
const DEFAULT_HOST = "127.0.0.1";

function listenPort(raw: string | undefined): number {
  const port = Number(raw ?? DEFAULT_PORT);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("INVALID_PORT");
  }
  return port;
}

function isDirectExecution(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && import.meta.url === pathToFileURL(entry).href;
}

// Only a directly executed `server.ts`/`server.js` listens; importing
// `buildServer` in tests never opens a socket. Synchronous startup failures
// (live mode, invalid APP_ORIGIN, bad PORT) are reported without a stack or
// configuration values.
if (isDirectExecution()) {
  try {
    buildServer()
      .listen({
        port: listenPort(process.env.PORT),
        host: process.env.HOST ?? DEFAULT_HOST,
      })
      .catch((error: unknown) => {
        console.error(
          "proxy failed to start:",
          error instanceof Error ? error.message : "unknown error",
        );
        process.exitCode = 1;
      });
  } catch (error) {
    console.error(
      "proxy failed to start:",
      error instanceof Error ? error.message : "unknown error",
    );
    process.exitCode = 1;
  }
}
