import { resolve } from "node:path";

/**
 * Native (Frappe-hosted) build settings. Kept beside `vite.proxy.ts` so the
 * default proxy-mode build keeps Vite's defaults: nothing here is applied unless
 * the build runs with `--mode native`.
 */

/** Mode name that switches the build to the Frappe-hosted layout. */
export const NATIVE_MODE = "native";

/** Public URL prefix of the native build. Frappe serves `<app>/public` at `/assets/<app>`. */
export const NATIVE_ASSET_BASE = "/assets/clinic_ui/ui/";

/**
 * Output folder, relative to the Vite root (the `app` package). It is this
 * app's own `public/ui`, which Frappe exposes at `/assets/clinic_ui/ui/`.
 * The folder is generated and gitignored; deployment must build before serving
 * (see the app README).
 */
export const NATIVE_OUT_DIR = "../frappe-apps/clinic_ui/clinic_ui/public/ui";

export type NativeBuildOptions = {
  base: string;
  define: { "import.meta.env.VITE_CLINIC_NATIVE": string };
  build: {
    outDir: string;
    emptyOutDir: true;
    manifest: true;
  };
};

/** Absolute output folder for a given Vite root; used by tests and by tooling. */
export function resolveNativeOutDir(root: string): string {
  return resolve(root, NATIVE_OUT_DIR);
}

/**
 * Vite config fragment for `--mode native`, or `undefined` for every other mode
 * so the default build is unchanged.
 *
 * Two build constants are required, and both are set here rather than through a
 * shell variable or `.env.native`, so the native build cannot be produced with
 * the proxy transport by accident:
 *
 * - `import.meta.env.VITE_CLINIC_NATIVE` = `"1"` enables
 *   `nativeTransportEnabled()` in `src/transport/native/flag.ts`. A user `define`
 *   takes precedence over the env values Vite loads, so `VITE_CLINIC_NATIVE=0` in
 *   the environment cannot flip it back.
 * - `manifest: true` emits `.vite/manifest.json`, the only source the Frappe
 *   shell controller reads to name the content-hashed entry files.
 */
export function nativeBuildOptions(
  mode: string,
): NativeBuildOptions | undefined {
  if (mode !== NATIVE_MODE) return undefined;
  return {
    base: NATIVE_ASSET_BASE,
    define: { "import.meta.env.VITE_CLINIC_NATIVE": JSON.stringify("1") },
    build: {
      outDir: NATIVE_OUT_DIR,
      emptyOutDir: true,
      manifest: true,
    },
  };
}
