import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";
import { TEST_APP_ORIGIN } from "./src/test/test-origin.js";
import { nativeBuildOptions } from "./vite.native.js";
import { apiProxy, apiProxyTarget } from "./vite.proxy.js";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // Only `--mode native` changes the asset base and output folder (Frappe-hosted
  // build); the default build keeps Vite's root base and `dist/`.
  ...nativeBuildOptions(mode),
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    // Same-origin only: the app talks to `/api` on its own dev origin. Vite's
    // default permissive dev CORS is unnecessary here and is switched off.
    cors: false,
    proxy: apiProxy(apiProxyTarget()),
  },
  test: {
    environment: "jsdom",
    // Playwright-only specs under e2e/ must never be collected by Vitest; they
    // import Playwright test APIs and run through `pnpm --filter @clinic/app e2e`.
    exclude: [
      ...configDefaults.exclude,
      "e2e/**",
      "e2e-live/**",
      "e2e-marley/**",
    ],
    // Relative fetch input (what the app sends) resolves here before MSW sees
    // it; the default jsdom origin would be an unrelated localhost port.
    environmentOptions: { jsdom: { url: TEST_APP_ORIGIN } },
    setupFiles: ["./src/test/setup.ts"],
  },
}));
