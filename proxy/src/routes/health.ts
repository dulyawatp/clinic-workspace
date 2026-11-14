import type { FastifyInstance } from "fastify";
import type { AppConfig } from "../config.js";

export interface HealthResponse {
  status: "ok";
  upstream: "fixture" | "unreachable";
}

export interface HealthRouteDeps {
  config: AppConfig;
}

/**
 * Liveness plus a coarse upstream label. Only the mode-derived label is
 * exposed: no upstream URL, host, credential or configuration value leaks.
 */
export function registerHealthRoutes(
  app: FastifyInstance,
  deps: HealthRouteDeps,
): void {
  const { config } = deps;

  app.get("/api/health", async (_req, reply) => {
    const body: HealthResponse = {
      status: "ok",
      upstream: config.upstreamMode === "fixture" ? "fixture" : "unreachable",
    };
    return reply.code(200).send(body);
  });
}
