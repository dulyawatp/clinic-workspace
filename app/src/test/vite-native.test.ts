import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  NATIVE_ASSET_BASE,
  NATIVE_MODE,
  NATIVE_OUT_DIR,
  nativeBuildOptions,
  resolveNativeOutDir,
} from "../../vite.native.js";

// Vitest runs with the package root as cwd (`pnpm --filter @clinic/app test`).
// The real `vite.config.ts` is not imported here: as a config module its
// `import.meta.url` is not a file URL outside a real Vite run, so the wired
// result is proven instead by the native build check (see the app README) and
// by the source assertion below.
const APP_ROOT = process.cwd();
const CONFIG_SOURCE = readFileSync(join(APP_ROOT, "vite.config.ts"), "utf8");

describe("native build fragment", () => {
  it("is the only source of the native base", () => {
    expect(NATIVE_ASSET_BASE).toBe("/assets/clinic_ui/ui/");
    expect(nativeBuildOptions(NATIVE_MODE)).toEqual({
      base: "/assets/clinic_ui/ui/",
      define: { "import.meta.env.VITE_CLINIC_NATIVE": '"1"' },
      build: {
        outDir: NATIVE_OUT_DIR,
        emptyOutDir: true,
        manifest: true,
      },
    });
  });

  it("turns the native transport on as a build constant", () => {
    // The transport flag is inlined by the native build; it must never depend on
    // a shell variable, so the fragment defines it and the proxy build does not.
    expect(nativeBuildOptions(NATIVE_MODE)?.define).toEqual({
      "import.meta.env.VITE_CLINIC_NATIVE": '"1"',
    });
    expect(nativeBuildOptions("production")?.define).toBeUndefined();
  });

  it("returns nothing outside native mode", () => {
    expect(nativeBuildOptions("production")).toBeUndefined();
    expect(nativeBuildOptions("development")).toBeUndefined();
    expect(nativeBuildOptions("")).toBeUndefined();
  });

  it("writes into the clinic_ui app public folder", () => {
    expect(resolveNativeOutDir(APP_ROOT)).toBe(
      join(
        dirname(APP_ROOT),
        "frappe-apps",
        "clinic_ui",
        "clinic_ui",
        "public",
        "ui",
      ),
    );
  });
});

describe("vite config wiring", () => {
  it("applies the fragment by mode and never overrides base itself", () => {
    expect(CONFIG_SOURCE).toContain("...nativeBuildOptions(mode)");
    expect(CONFIG_SOURCE).toContain("defineConfig(({ mode })");
    // `base` lives only in the native fragment, so the default build keeps
    // Vite's "/" base and the existing `dist/` output folder.
    expect(CONFIG_SOURCE).not.toContain("base:");
    expect(CONFIG_SOURCE).not.toContain("outDir:");
  });

  it("keeps the dev proxy and test setup untouched", () => {
    expect(CONFIG_SOURCE).toContain("proxy: apiProxy(apiProxyTarget())");
    expect(CONFIG_SOURCE).toContain('setupFiles: ["./src/test/setup.ts"]');
  });
});
