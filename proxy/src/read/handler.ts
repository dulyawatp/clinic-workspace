import type { FastifyInstance, FastifyReply } from "fastify";
import { z } from "zod";
import type { AppConfig } from "../config.js";
import { SESSION_COOKIE } from "../security/constants.js";
import type { SessionStore } from "../session/store.js";
import { READ_RESOURCES } from "./registry.js";
import { type ReadFilters, readFixturePage } from "./upstream.js";

const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 100;

const UNSAFE_FILTER_KEYS: ReadonlySet<string> = new Set([
  "__proto__",
  "constructor",
  "prototype",
]);

export interface ReadRouteDeps {
  config: AppConfig;
  sessions: SessionStore;
}

function parseInteger(raw: unknown): number | undefined {
  if (typeof raw !== "string" || raw.trim() === "") return undefined;
  const value = Number(raw);
  return Number.isFinite(value) ? Math.trunc(value) : undefined;
}

function parseFilters(raw: unknown): ReadFilters | null {
  if (raw === undefined) return {};
  if (typeof raw !== "string") return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return null;
  }

  // Null prototype: a parsed `__proto__` key can never change a prototype.
  const filters: Record<string, string> = Object.create(null);
  for (const [key, value] of Object.entries(parsed)) {
    if (UNSAFE_FILTER_KEYS.has(key) || typeof value !== "string") return null;
    filters[key] = value;
  }
  return filters;
}

function sendCode(reply: FastifyReply, status: number, code: string) {
  return reply.code(status).send({ code });
}

export interface PageWindow {
  limit: number;
  offset: number;
}

/** Default 20, hard cap 100, never below one row; negative/NaN offset is 0. */
export function pageWindow(query: Record<string, unknown>): PageWindow {
  return {
    limit: Math.min(
      Math.max(parseInteger(query.limit) ?? DEFAULT_LIMIT, 1),
      MAX_LIMIT,
    ),
    offset: Math.max(parseInteger(query.offset) ?? 0, 0),
  };
}

/**
 * Allow-listed read endpoint. Order matters and is fixed: session, allow-list,
 * the fixture-only unverified matrix, pagination cap, upstream, Zod parse.
 * Roles come from the server-side session; request headers and query values
 * are never read as a role.
 */
export function registerReadRoutes(
  app: FastifyInstance,
  deps: ReadRouteDeps,
): void {
  const { config, sessions } = deps;

  // Wildcard, not `:resource`: a multi-segment or traversal-shaped path must
  // reach the allow-list and get the same machine-coded 404 rather than
  // Fastify's default route-not-found body (which echoes the path).
  app.get("/api/read/*", async (req, reply) => {
    // Read responses are session-scoped and must never be cached or replayed.
    reply.header("cache-control", "no-store");

    const sessionId = req.cookies[SESSION_COOKIE];
    const session =
      sessionId === undefined ? undefined : sessions.get(sessionId);
    if (session === undefined) {
      return sendCode(reply, 401, "AUTH_EXPIRED");
    }

    // Fixture rows are never served in live mode: no fixture patient record may
    // be mixed with a real live session. A verified live read path does not
    // exist yet, so the surface fails closed.
    if (config.upstreamMode === "live") {
      return sendCode(reply, 404, "UNSUPPORTED");
    }

    const params = req.params as Record<string, unknown>;
    const resourceName = params["*"];
    if (
      typeof resourceName !== "string" ||
      !Object.hasOwn(READ_RESOURCES, resourceName)
    ) {
      return sendCode(reply, 404, "NOT_FOUND");
    }
    const resource = READ_RESOURCES[resourceName];
    if (resource === undefined) {
      return sendCode(reply, 404, "NOT_FOUND");
    }

    // Fixture-only, unverified presentation filter. Never an authorization
    // contract: the real matrix is DECISION_REQUIRED.
    if (
      resource.contract === "fixture" &&
      resource.matrix === "unverified" &&
      resource.roles !== undefined
    ) {
      const allowed = session.roles.some((role) =>
        resource.roles?.includes(role),
      );
      if (!allowed) {
        return sendCode(reply, 403, "FORBIDDEN");
      }
    }

    const query = req.query as Record<string, unknown>;
    const { limit, offset } = pageWindow(query);

    // Search and filters are opt-in per resource. Anything outside the
    // registry's public DTO fields is a client error, never silently ignored:
    // that would let a caller probe hidden fixture fields (e.g. internal_notes).
    const q = (typeof query.q === "string" ? query.q : "").trim();
    if (q !== "" && resource.searchFields.length === 0) {
      return sendCode(reply, 400, "VALIDATION");
    }

    const filters = parseFilters(query.filters);
    if (filters === null) {
      return sendCode(reply, 400, "VALIDATION");
    }
    if (resourceName === "billing-patient-summary" && !filters.id)
      return sendCode(reply, 400, "VALIDATION");
    if (
      resourceName === "frontdesk-appointments" &&
      filters.scope &&
      !["all", "checkedIn", "waiting", "upcoming"].includes(filters.scope)
    )
      return sendCode(reply, 400, "VALIDATION");
    if (
      Object.keys(filters).some((key) => !resource.filterFields.includes(key))
    ) {
      return sendCode(reply, 400, "VALIDATION");
    }

    let page: { rows: unknown[]; total: number };
    try {
      page = readFixturePage(resource.fixtureFile, {
        roles: session.roles,
        q,
        filters,
        searchFields: resource.searchFields,
        limit,
        offset,
      });
    } catch {
      // No exception message, stack or fixture path reaches the client.
      return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
    }

    // Zod strips every field the resource schema does not declare (e.g.
    // `internal_notes`), so the client only ever sees the narrowed DTO.
    const parsed = z.array(resource.schema).safeParse(page.rows);
    if (!parsed.success) {
      return sendCode(reply, 502, "UPSTREAM_UNAVAILABLE");
    }

    return reply.code(200).send({ items: parsed.data, total: page.total });
  });
}
