import type { FastifyInstance } from "fastify";
import {
  invalidateLiveAccess,
  type LiveAccessDeps,
  liveUpstream,
  requireLiveAccess,
} from "./live-access.js";
import { probeNativeSession } from "./patient-authority.js";

export function registerLiveStockRoutes(
  app: FastifyInstance,
  deps: LiveAccessDeps & { fetchImpl: typeof fetch },
): void {
  const upstream = liveUpstream(deps.config);
  app.get("/api/live/synthetic-stock", async (req, reply) => {
    reply.header("cache-control", "no-store");
    const access = requireLiveAccess(req, reply, deps, upstream, "PHA");
    if (!access || !upstream) return;
    const items = [];
    for (const code of upstream.syntheticItems ?? []) {
      try {
        const response = await deps.fetchImpl(
          `${upstream.origin}/api/resource/Item/${encodeURIComponent(code)}`,
          {
            method: "GET",
            headers: {
              accept: "application/json",
              cookie: `sid=${access.native.sid}`,
            },
            redirect: "manual",
            signal: AbortSignal.timeout(upstream.timeoutMs),
          },
        );
        if (
          response.status === 401 ||
          ((response.status === 403 || response.status === 404) &&
            (await probeNativeSession(
              upstream,
              deps.fetchImpl,
              access.native,
            )) === "expired")
        ) {
          invalidateLiveAccess(reply, deps, access.sessionId);
          return reply.code(401).send({ code: "AUTH_EXPIRED" });
        }
        if (response.status === 403 || response.status === 404)
          return reply.code(403).send({ code: "FORBIDDEN" });
        if (response.status !== 200)
          return reply.code(502).send({ code: "UPSTREAM_UNAVAILABLE" });
        const text = await response.text();
        if (Buffer.byteLength(text, "utf8") > 128 * 1024)
          throw new Error("oversized");
        const row: unknown = JSON.parse(text)?.data;
        if (typeof row !== "object" || row === null) throw new Error("invalid");
        const item = row as Record<string, unknown>;
        if (
          item.name !== code ||
          item.item_code !== code ||
          typeof item.item_name !== "string" ||
          !item.item_name.trim() ||
          typeof item.stock_uom !== "string" ||
          !item.stock_uom.trim() ||
          (item.is_stock_item !== 0 && item.is_stock_item !== 1)
        )
          throw new Error("invalid");
        items.push({
          item_code: code,
          item_name: item.item_name,
          stock_uom: item.stock_uom,
          is_stock_item: item.is_stock_item === 1,
        });
      } catch {
        return reply.code(502).send({ code: "UPSTREAM_UNAVAILABLE" });
      }
    }
    return reply.code(200).send({ items });
  });
}
