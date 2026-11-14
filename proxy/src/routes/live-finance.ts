import type { FastifyInstance } from "fastify";
import {
  invalidateLiveAccess,
  type LiveAccessDeps,
  liveUpstream,
  requireLiveAccess,
} from "./live-access.js";
import { probeNativeSession } from "./patient-authority.js";

function cents(value: number): number {
  const scaled = value * 100;
  const rounded = Math.round(scaled);
  if (
    value < 0 ||
    !Number.isSafeInteger(rounded) ||
    Math.abs(scaled - rounded) > 1e-7
  )
    throw new Error("invalid amount");
  return rounded;
}

export function registerLiveFinanceRoutes(
  app: FastifyInstance,
  deps: LiveAccessDeps & { fetchImpl: typeof fetch },
): void {
  const upstream = liveUpstream(deps.config);
  app.get("/api/live/synthetic-finance", async (req, reply) => {
    reply.header("cache-control", "no-store");
    const access = requireLiveAccess(req, reply, deps, upstream, [
      "MGR",
      "CAS",
    ]);
    if (!access || !upstream) return;
    let invoice_count = 0,
      invoiced = 0,
      outstanding = 0;
    for (const name of upstream.syntheticInvoices ?? []) {
      try {
        const response = await deps.fetchImpl(
          `${upstream.origin}/api/resource/Sales%20Invoice/${encodeURIComponent(name)}`,
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
        if (Buffer.byteLength(text, "utf8") > 256 * 1024)
          throw new Error("oversized");
        const row: unknown = JSON.parse(text)?.data;
        if (typeof row !== "object" || row === null) throw new Error("invalid");
        const data = row as Record<string, unknown>;
        if (
          data.name !== name ||
          data.docstatus !== 1 ||
          data.company !== "Demo Clinic" ||
          data.currency !== "THB" ||
          typeof data.remarks !== "string" ||
          !data.remarks.startsWith("demo-seed-v1:invoice:") ||
          typeof data.grand_total !== "number" ||
          !Number.isFinite(data.grand_total) ||
          typeof data.outstanding_amount !== "number" ||
          !Number.isFinite(data.outstanding_amount)
        )
          throw new Error("invalid");
        const totalCents = cents(data.grand_total);
        const outstandingCents = cents(data.outstanding_amount);
        if (outstandingCents > totalCents) throw new Error("invalid balance");
        invoice_count++;
        invoiced += totalCents;
        outstanding += outstandingCents;
        if (
          !Number.isSafeInteger(invoiced) ||
          !Number.isSafeInteger(outstanding)
        )
          throw new Error("invalid total");
      } catch {
        return reply.code(502).send({ code: "UPSTREAM_UNAVAILABLE" });
      }
    }
    return reply.send({
      currency: "THB",
      invoice_count,
      invoiced: invoiced / 100,
      outstanding: outstanding / 100,
    });
  });
}
