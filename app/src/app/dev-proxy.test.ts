import {
  createServer as createHttpServer,
  type Server as HttpServer,
  type IncomingHttpHeaders,
} from "node:http";
import { type AddressInfo, createServer as createNetServer } from "node:net";
import { createServer as createViteServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { apiProxy, apiProxyTarget } from "../../vite.proxy";
import { allowTestOrigin } from "../test/network";

interface CapturedRequest {
  url: string | undefined;
  origin: string | undefined;
  cookie: string | undefined;
}

function header(
  headers: IncomingHttpHeaders,
  name: string,
): string | undefined {
  const value = headers[name];
  return Array.isArray(value) ? value[0] : value;
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createNetServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address() as AddressInfo;
      probe.close(() => resolve(address.port));
    });
  });
}

function listen(server: HttpServer): Promise<void> {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
}

function close(server: HttpServer): Promise<void> {
  return new Promise((resolve) => server.close(() => resolve()));
}

const captured: CapturedRequest[] = [];
let target: HttpServer;
let vite: ViteDevServer;
let targetPort: number;
let vitePort: number;
let releaseTargetOrigin: (() => void) | undefined;
let releaseViteOrigin: (() => void) | undefined;

// Stand-in for the local Fastify proxy: it never leaves the loopback interface
// and performs no live upstream call.
beforeAll(async () => {
  target = createHttpServer((req, res) => {
    captured.push({
      url: req.url,
      origin: header(req.headers, "origin"),
      cookie: header(req.headers, "cookie"),
    });

    if (req.url === "/api/session") {
      res.writeHead(401, {
        "content-type": "application/json",
        "cache-control": "no-store",
      });
      res.end(JSON.stringify({ code: "AUTH_EXPIRED" }));
      return;
    }

    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ status: "ok", upstream: "fixture" }));
  });
  await listen(target);
  targetPort = (target.address() as AddressInfo).port;
  // MSW also sees the Vite proxy's outbound hop to this stand-in target, so
  // both exact bound origins are registered; nothing else may bypass.
  releaseTargetOrigin = allowTestOrigin(`http://127.0.0.1:${targetPort}`);

  vitePort = await freePort();
  vite = await createViteServer({
    configFile: false,
    root: process.cwd(),
    logLevel: "silent",
    server: {
      host: "127.0.0.1",
      port: vitePort,
      strictPort: true,
      cors: false,
      proxy: apiProxy(`http://127.0.0.1:${targetPort}`),
    },
  });
  await vite.listen();
  releaseViteOrigin = allowTestOrigin(`http://127.0.0.1:${vitePort}`);
}, 30_000);

afterAll(async () => {
  releaseViteOrigin?.();
  releaseTargetOrigin?.();
  await vite?.close();
  if (target !== undefined) {
    await close(target);
  }
});

describe("Vite dev proxy", () => {
  it("targets the local proxy by default and allows an override", () => {
    expect(apiProxyTarget({} as NodeJS.ProcessEnv)).toBe(
      "http://localhost:3000",
    );
    expect(
      apiProxyTarget({
        CLINIC_PROXY_TARGET: "http://127.0.0.1:4000",
      } as NodeJS.ProcessEnv),
    ).toBe("http://127.0.0.1:4000");
  });

  it("forwards a real same-origin request through the Vite origin", async () => {
    const origin = `http://127.0.0.1:${vitePort}`;
    const response = await fetch(`${origin}/api/health`, {
      headers: { origin, cookie: "session=test-cookie" },
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "ok",
      upstream: "fixture",
    });
    // No CORS relaxation: the response must not become cross-origin readable.
    expect(response.headers.get("access-control-allow-origin")).toBeNull();

    const forwarded = captured.at(-1);
    expect(forwarded?.url).toBe("/api/health");
    // changeOrigin:false keeps the browser Origin, so proxy origin/CSRF checks
    // see the real dev origin.
    expect(forwarded?.origin).toBe(origin);
    expect(forwarded?.cookie).toBe("session=test-cookie");
  });

  it("passes an unauthenticated session 401 and no-store back unchanged", async () => {
    const origin = `http://127.0.0.1:${vitePort}`;
    const response = await fetch(`${origin}/api/session`, {
      headers: { origin, cookie: "session=expired" },
    });

    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ code: "AUTH_EXPIRED" });
    expect(captured.at(-1)?.url).toBe("/api/session");
  });
});
