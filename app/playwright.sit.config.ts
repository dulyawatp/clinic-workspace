import { defineConfig, devices } from "@playwright/test";

// Native SIT only: no server, fixtures, persisted browser state, or media.
export default defineConfig({
  testDir: "./e2e-sit",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  outputDir: "./test-results/sit",
  preserveOutput: "never",
  reporter: [["line"]],
  use: {
    baseURL: "http://127.0.0.1:18081",
    trace: "off",
    screenshot: "off",
    video: "off",
    acceptDownloads: false,
    serviceWorkers: "block",
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
  },
  projects: [{ name: "sit-chromium", use: { ...devices["Desktop Chrome"] } }],
});
