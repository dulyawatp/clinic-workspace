import { defineConfig, devices } from "@playwright/test";

/**
 * Default browser validation uses Vite and the local fixture proxy. With
 * CLINIC_E2E_NO_BACKEND=1, only Vite starts and Playwright routes every API
 * request through the shared MSW fixture handlers; no proxy socket is used.
 */
const APP_PORT = process.env.CLINIC_E2E_APP_PORT ?? "4173";
const PROXY_PORT = process.env.CLINIC_E2E_PROXY_PORT ?? "3000";
const APP_ORIGIN = `http://localhost:${APP_PORT}`;
const PROXY_ORIGIN = `http://localhost:${PROXY_PORT}`;
const noBackend = process.env.CLINIC_E2E_NO_BACKEND === "1";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: APP_ORIGIN,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    ...(!noBackend
      ? [
          {
            command: "pnpm --filter @clinic/proxy dev",
            url: `${PROXY_ORIGIN}/api/health`,
            reuseExistingServer: !process.env.CI,
            env: {
              PORT: PROXY_PORT,
              UPSTREAM_MODE: "fixture",
              DATABASE_PATH: ":memory:",
              APP_ORIGIN,
            },
          },
        ]
      : []),
    {
      command: `pnpm --filter @clinic/app dev --host localhost --port ${APP_PORT} --strictPort`,
      url: `${APP_ORIGIN}/clinic/login`,
      reuseExistingServer: !process.env.CI,
      env: { CLINIC_PROXY_TARGET: PROXY_ORIGIN },
    },
  ],
});
