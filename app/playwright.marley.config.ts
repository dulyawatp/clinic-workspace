import { chmodSync, mkdirSync, statSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";
import {
  loadRuntime,
  proxyEnv,
  RESULTS_DIR_ENV,
} from "./e2e-marley/marley-env";

/**
 * Marley real-UI role matrix.
 *
 * Owns exactly one freshly launched local stack: the Vite dev server on
 * 127.0.0.1:5183 and the Fastify proxy on 127.0.0.1:3101, both bound to
 * loopback, with the proxy in `UPSTREAM_MODE=live` against the existing SSH
 * tunnel at 127.0.0.1:18080. `reuseExistingServer` is false, so a stale local
 * stack can never answer for this run.
 *
 * Artefacts that would capture authenticated screens are off (trace,
 * screenshot, video), failure output is discarded, retries are zero and the
 * line/JSON reporters are the only outputs. The JSON report and the proxy's
 * session database are written outside the repository, under
 * `MARLEY_RESULTS_DIR`.
 */

const APP_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_DIR = resolve(APP_DIR, "..");

export const APP_PORT = 5183;
export const PROXY_PORT = 3101;
export const MARLEY_BASE_URL = `http://127.0.0.1:${APP_PORT}`;

const runtime = loadRuntime();

function resultsDir(): string {
  const dir = process.env[RESULTS_DIR_ENV];
  if (dir === undefined || dir.trim() === "")
    throw new Error(
      `${RESULTS_DIR_ENV} must name an absolute directory outside the repository`,
    );
  if (!isAbsolute(dir)) throw new Error(`${RESULTS_DIR_ENV} must be absolute`);
  const resolved = resolve(dir);
  const inside = relative(REPO_DIR, resolved);
  if (inside !== "" && !inside.startsWith(".."))
    throw new Error(`${RESULTS_DIR_ENV} must be outside the repository`);
  mkdirSync(resolved, { recursive: true, mode: 0o700 });
  // Tighten a pre-existing directory too: results may hold synthetic identifiers.
  chmodSync(resolved, 0o700);
  const stats = statSync(resolved);
  if (!stats.isDirectory())
    throw new Error(`${RESULTS_DIR_ENV} must be a directory`);
  return resolved;
}

const output = resultsDir();

export default defineConfig({
  testDir: "./e2e-marley",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 25_000 },
  outputDir: resolve(output, "artifacts"),
  preserveOutput: "never",
  reporter: [
    ["line"],
    ["json", { outputFile: resolve(output, "report.json") }],
  ],
  use: {
    baseURL: MARLEY_BASE_URL,
    trace: "off",
    screenshot: "off",
    video: "off",
    acceptDownloads: false,
    serviceWorkers: "block",
    ignoreHTTPSErrors: false,
    actionTimeout: 25_000,
    navigationTimeout: 60_000,
  },
  projects: [
    { name: "marley-chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: [
    {
      command: "pnpm exec tsx src/server.ts",
      cwd: resolve(REPO_DIR, "proxy"),
      url: `http://127.0.0.1:${PROXY_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "ignore",
      env: proxyEnv(
        runtime,
        MARLEY_BASE_URL,
        PROXY_PORT,
        resolve(output, "proxy-sessions.sqlite"),
      ),
    },
    {
      command: `pnpm exec vite --host 127.0.0.1 --port ${APP_PORT} --strictPort`,
      cwd: APP_DIR,
      url: `${MARLEY_BASE_URL}/clinic/login`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "ignore",
      env: { CLINIC_PROXY_TARGET: `http://127.0.0.1:${PROXY_PORT}` },
    },
  ],
});
