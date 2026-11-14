import type { FastifyInstance, FastifyReply } from "fastify";
import type { AppConfig } from "../config.js";
import { SESSION_COOKIE } from "../security/constants.js";
import type { SessionStore } from "../session/store.js";
import { photoAsset, photoAssetBytes } from "./photo-assets.js";
import { fixtureResourceRoles } from "./registry.js";

/**
 * The bytes surface reuses the `clinical-photos` fixture matrix: the same
 * `matrix: "unverified"` role filter gates the DTO and the file. It is not an
 * authorization contract and proves nothing about a real backend.
 */
const FILE_RESOURCE = "clinical-photos";

export interface FileRouteDeps {
  config: AppConfig;
  sessions: SessionStore;
}

function sendCode(reply: FastifyReply, status: number, code: string) {
  return reply.code(status).send({ code });
}

/**
 * Fixture-only private file endpoint. Order is fixed: session, fixture role
 * matrix, exact allow-list id. There is no filesystem read, no query/path
 * forwarding and no upstream fallback: an id that is not in the manifest is a
 * 404, never a request to another source.
 */
export function registerFileRoutes(
  app: FastifyInstance,
  deps: FileRouteDeps,
): void {
  const { config, sessions } = deps;

  // Wildcard, not `:fileId`: a multi-segment or traversal-shaped id reaches the
  // allow-list and gets the same machine-coded 404 instead of a route-not-found
  // body that echoes the requested value.
  app.get("/api/files/*", async (req, reply) => {
    // Private clinical bytes are session-scoped and must never be cached.
    reply.header("cache-control", "no-store");

    const sessionId = req.cookies[SESSION_COOKIE];
    const session =
      sessionId === undefined ? undefined : sessions.get(sessionId);
    if (session === undefined) {
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }

    // In live lab mode no private bytes are served until a verified live read
    // path exists. The fixture manifest is not a live authorization contract.
    if (config.upstreamMode === "live") {
      return sendCode(reply, 404, "UNSUPPORTED");
    }

    const roles = fixtureResourceRoles(FILE_RESOURCE);
    if (!session.roles.some((role) => roles.includes(role))) {
      return sendCode(reply, 403, "FORBIDDEN");
    }

    const fileId = (req.params as Record<string, unknown>)["*"];
    const asset = typeof fileId === "string" ? photoAsset(fileId) : undefined;
    const bytes =
      typeof fileId === "string" ? photoAssetBytes(fileId) : undefined;
    if (asset === undefined || bytes === undefined) {
      return sendCode(reply, 404, "NOT_FOUND");
    }

    reply.header("content-type", asset.contentType);
    reply.header("x-content-type-options", "nosniff");
    reply.header("content-length", String(asset.bytes));
    return reply.code(200).send(bytes);
  });
}
