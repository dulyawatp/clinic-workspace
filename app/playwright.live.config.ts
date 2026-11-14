import { defineConfig, devices } from "@playwright/test";

/**
 * Browse-only acceptance tests against the deployed `clinic-photo-accept.local`
 * stack reached through an existing loopback tunnel. This config owns no server:
 * no webServer, no fixtures, no global setup, no seed data. The base URL is the
 * exact deployed origin and is intentionally not configurable from the
 * environment, so a run can never drift onto the fixture dev stack by accident.
 *
 * Artefacts are disabled on purpose: traces, screenshots and video would capture
 * authenticated pages. The reporter is line-only (no HTML bundle), retries are
 * zero, and tests run serially because they share the remote synthetic clinician
 * account.
 */
export const LIVE_BASE_URL = "http://127.0.0.1:8083";

export default defineConfig({
  testDir: "./e2e-live",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  outputDir: "./test-results/live",
  preserveOutput: "never",
  reporter: [["line"]],
  use: {
    baseURL: LIVE_BASE_URL,
    trace: "off",
    screenshot: "off",
    video: "off",
    acceptDownloads: false,
    serviceWorkers: "block",
    ignoreHTTPSErrors: false,
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
  },
  projects: [{ name: "live-chromium", use: { ...devices["Desktop Chrome"] } }],
});
